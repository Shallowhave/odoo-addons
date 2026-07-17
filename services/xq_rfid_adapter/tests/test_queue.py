import binascii
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from unittest import mock

from xq_rfid_adapter.domain import AdapterError, AdapterErrorCode, DeviceCapabilities, TagObservation, TagTarget
from xq_rfid_adapter.drivers.fake import FakeDriver
from xq_rfid_adapter.queue import DeviceQueue, QueueError, _DeviceWorker
from xq_rfid_adapter.service import RfidService
from xq_rfid_adapter.store import OperationStore, StoreError


def payload(token=b"0123456789ABCDEF"):
    prefix = b"XQ" + bytes((1, 0)) + token
    return prefix + binascii.crc32(prefix).to_bytes(4, "big")


def request(request_id, *, device_id="reader-1", token=b"0123456789ABCDEF"):
    value = payload(token)
    return {
        "request_id": request_id,
        "device_id": device_id,
        "operation_type": "write_and_verify",
        "payload_hex": value.hex(),
        "payload_version": 1,
    }


def capabilities():
    return DeviceCapabilities(True, False, True, False, True, True)


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    raise AssertionError("condition was not reached")


class QueueCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "operations.sqlite3")
        self.store = OperationStore(self.path)
        self.queues = []

    def tearDown(self):
        for queue in self.queues:
            queue.close()
        self.store.close()
        self.tempdir.cleanup()

    def make_queue(self, drivers, **options):
        owner_id = options.pop("owner_id", "adapter-test-owner")
        device_capabilities = options.pop(
            "capabilities", {device_id: capabilities() for device_id in drivers}
        )
        queue = DeviceQueue(
            self.store,
            drivers,
            capabilities=device_capabilities,
            owner_id=owner_id,
            poll_interval=options.pop("poll_interval", 0.05),
            shutdown_timeout=options.pop("shutdown_timeout", 0.2),
            **options,
        )
        self.queues.append(queue)
        return queue

    def test_same_device_is_serial_and_claims_in_creation_order(self):
        target = TagTarget("AABBCCDD", "00112233")
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]] * 4,
            user_memory={target: b"\x00" * 24},
        )
        queue = self.make_queue({"reader-1": driver})
        queue.submit_operation(request("r1", token=b"0000000000000001"))
        queue.submit_operation(request("r2", token=b"0000000000000002"))
        wait_for(lambda: self.store.get("r2")["state"] == "succeeded")
        writes = [record for record in driver.call_records if record["operation"] == "write_memory"]
        self.assertEqual(len(writes), 2)
        self.assertEqual(len({record["thread_id"] for record in driver.call_records}), 1)
        self.assertLess(writes[0]["started_at"], writes[1]["started_at"])

    def test_different_devices_can_overlap(self):
        releases = []
        drivers = {}
        for index in range(2):
            target = TagTarget(f"AABBCC0{index}", f"0011220{index}")
            entered, release = threading.Event(), threading.Event()
            releases.append(release)
            drivers[f"reader-{index}"] = FakeDriver(
                capabilities=capabilities(),
                inventory_snapshots=[[TagObservation(target)]] * 2,
                user_memory={target: b"\x00" * 24},
                block_events={"inventory": (entered, release)},
            )
            drivers[f"reader-{index}"]._test_entered = entered
        queue = self.make_queue(drivers)
        try:
            queue.submit_operation(request("r0", device_id="reader-0"))
            queue.submit_operation(request("r1", device_id="reader-1"))
            self.assertTrue(drivers["reader-0"]._test_entered.wait(1))
            self.assertTrue(drivers["reader-1"]._test_entered.wait(1))
            ids = {
                drivers["reader-0"].call_records[0]["thread_id"],
                drivers["reader-1"].call_records[0]["thread_id"],
            }
            self.assertEqual(len(ids), 2)
        finally:
            for release in releases:
                release.set()

    def test_two_stores_and_owners_cannot_drive_one_device_concurrently(self):
        target = TagTarget("AABBCCDD", "00112233")
        entered_a, release_a = threading.Event(), threading.Event()
        entered_b, release_b = threading.Event(), threading.Event()
        driver_a = FakeDriver(
            capabilities=capabilities(), inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24}, block_events={"inventory": (entered_a, release_a)},
        )
        driver_b = FakeDriver(
            capabilities=capabilities(), inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24}, block_events={"inventory": (entered_b, release_b)},
        )
        other_store = OperationStore(self.path)
        queue_a = self.make_queue({"reader-1": driver_a}, owner_id="adapter-owner-a")
        try:
            queue_a.submit_operation(request("shared"))
            self.assertTrue(entered_a.wait(2))
            queue_b = DeviceQueue(
                other_store,
                {"reader-1": driver_b},
                capabilities={"reader-1": capabilities()},
                owner_id="adapter-owner-b",
                poll_interval=0.02,
                shutdown_timeout=0.1,
            )
            self.queues.append(queue_b)
            queue_b.submit_operation(request("shared"))
            time.sleep(0.1)
            self.assertFalse(entered_b.is_set())
        finally:
            release_a.set()
            release_b.set()
            other_store.close()

    def test_diagnostics_run_on_worker_serialize_behind_write_and_are_bounded(self):
        target = TagTarget("AABBCCDD", "00112233")
        write_entered, write_release = threading.Event(), threading.Event()
        diagnostic_entered, diagnostic_release = threading.Event(), threading.Event()
        driver = FakeDriver(
            capabilities=capabilities(), inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24},
            block_events={
                "inventory": (write_entered, write_release),
                "get_device_info": (diagnostic_entered, diagnostic_release),
            },
        )
        queue = self.make_queue(
            {"reader-1": driver}, max_diagnostics=1, diagnostic_timeout=2.0
        )
        queue.submit_operation(request("write"))
        self.assertTrue(write_entered.wait(1))
        result = []
        caller = threading.Thread(target=lambda: result.append(queue.get_device("reader-1")))
        caller.start()
        time.sleep(0.05)
        self.assertFalse(diagnostic_entered.is_set())
        write_release.set()
        self.assertTrue(diagnostic_entered.wait(1))
        with self.assertRaises(QueueError) as caught:
            queue.test_connection("reader-1")
        self.assertEqual(caught.exception.code, "diagnostic_overload")
        diagnostic_release.set()
        caller.join(1)
        self.assertEqual(result[0]["status"], "connected")
        worker_id = next(record["thread_id"] for record in driver.call_records if record["operation"] == "inventory")
        diagnostic_id = next(record["thread_id"] for record in driver.call_records if record["operation"] == "get_device_info")
        self.assertEqual(worker_id, diagnostic_id)

    def test_diagnostic_cannot_enqueue_after_worker_stop(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue({"reader-1": driver}, diagnostic_timeout=0.05)
        worker = queue._workers["reader-1"]
        acquired = threading.Event()
        release = threading.Event()
        outcome = []
        original_slots = worker.diagnostic_slots
        original_acquire = original_slots.acquire

        class PausedSlots:
            def acquire(self, *args, **kwargs):
                result = original_acquire(*args, **kwargs)
                acquired.set()
                release.wait(1)
                return result

            def release(self):
                original_slots.release()

        worker.diagnostic_slots = PausedSlots()
        caller = threading.Thread(
            target=lambda: self._capture_diagnostic(queue, outcome)
        )
        caller.start()
        self.assertTrue(acquired.wait(1))
        queue.close()
        release.set()
        caller.join(1)

        self.assertFalse(caller.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], QueueError)
        self.assertEqual(outcome[0].code, "queue_closed")
        self.assertEqual(worker.diagnostics.qsize(), 0)

    @staticmethod
    def _capture_diagnostic(queue, outcome):
        try:
            result = queue.get_device("reader-1")
        except BaseException as error:
            result = error
        outcome.append(result)

    def test_blocked_diagnostic_keeps_lease_until_hardware_call_returns(self):
        entered, release = threading.Event(), threading.Event()
        driver = FakeDriver(
            capabilities=capabilities(),
            block_events={"get_device_info": (entered, release)},
        )
        queue = self.make_queue(
            {"reader-1": driver}, lease_seconds=1, diagnostic_timeout=3.0
        )
        outcome = []
        caller = threading.Thread(
            target=lambda: outcome.append(queue.get_device("reader-1"))
        )
        caller.start()
        self.assertTrue(entered.wait(1))
        time.sleep(2.2)

        other_store = OperationStore(self.path)
        try:
            with self.assertRaises(StoreError) as caught:
                other_store.acquire_lease("reader-1", "other-owner", 30)
            self.assertEqual(caught.exception.code, "lease_conflict")
        finally:
            other_store.close()
            release.set()
        caller.join(1)
        self.assertFalse(caller.is_alive())
        self.assertEqual(outcome[0]["status"], "connected")

    def test_dequeued_diagnostic_cannot_acquire_lease_after_close(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=5.0, shutdown_timeout=0.05,
            diagnostic_timeout=1.0,
        )
        wait_for(lambda: self.store.get_lease("reader-1") is None)
        worker = queue._workers["reader-1"]
        dequeued = threading.Event()
        release = threading.Event()
        outcome = []
        lease_calls = []
        original_get = worker.diagnostics.get_nowait
        original_acquire = self.store.acquire_lease

        def paused_get():
            item = original_get()
            dequeued.set()
            release.wait(1)
            return item

        def recording_acquire(*args, **kwargs):
            lease_calls.append((args, kwargs))
            return original_acquire(*args, **kwargs)

        worker.diagnostics.get_nowait = paused_get
        self.store.acquire_lease = recording_acquire
        caller = threading.Thread(
            target=lambda: self._capture_diagnostic(queue, outcome)
        )
        caller.start()
        self.assertTrue(dequeued.wait(1))

        queue.close()
        release.set()
        caller.join(1)

        self.assertFalse(caller.is_alive())
        self.assertEqual(lease_calls, [])
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], QueueError)
        self.assertEqual(outcome[0].code, "queue_closed")
        self.assertIsNone(self.store.get_lease("reader-1"))
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )

    def test_inflight_diagnostic_lease_acquire_rolls_back_after_close(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=5.0, shutdown_timeout=0.05,
            diagnostic_timeout=1.0,
        )
        wait_for(lambda: self.store.get_lease("reader-1") is None)
        entered = threading.Event()
        release = threading.Event()
        outcome = []
        original_acquire = self.store.acquire_lease
        original_transaction = self.store._transaction
        acquire_active = threading.Event()

        @contextmanager
        def paused_transaction(*args, **kwargs):
            with original_transaction(*args, **kwargs) as connection:
                yield connection
                if acquire_active.is_set():
                    entered.set()
                    release.wait(1)

        def marked_acquire(*args, **kwargs):
            acquire_active.set()
            try:
                return original_acquire(*args, **kwargs)
            finally:
                acquire_active.clear()

        self.store._transaction = paused_transaction
        self.store.acquire_lease = marked_acquire
        caller = threading.Thread(
            target=lambda: self._capture_diagnostic(queue, outcome)
        )
        caller.start()
        self.assertTrue(entered.wait(1))

        queue.close()
        release.set()
        caller.join(1)

        self.assertFalse(caller.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")
        self.assertIsNone(self.store.get_lease("reader-1"))
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )

    def test_close_after_diagnostic_lease_commit_releases_idle_lease(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=5.0, shutdown_timeout=0.05,
            diagnostic_timeout=1.0,
        )
        wait_for(lambda: self.store.get_lease("reader-1") is None)
        committed = threading.Event()
        release = threading.Event()
        outcome = []
        original_acquire = queue._service.acquire_device_lease

        def paused_acquire(device_id):
            original_acquire(device_id)
            committed.set()
            release.wait(1)

        queue._service.acquire_device_lease = paused_acquire
        caller = threading.Thread(
            target=lambda: self._capture_diagnostic(queue, outcome)
        )
        caller.start()
        self.assertTrue(committed.wait(1))

        queue.close()
        release.set()
        caller.join(1)

        self.assertFalse(caller.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")
        self.assertIsNone(self.store.get_lease("reader-1"))
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )

    def test_diagnostic_hardware_call_is_surrounded_by_lease_renewal(self):
        events = []

        class RecordingDriver(FakeDriver):
            def get_device_info(self):
                events.append("driver")
                return super().get_device_info()

        driver = RecordingDriver(capabilities=capabilities())
        original_renew = self.store.renew_lease

        def recording_renew(*args, **kwargs):
            events.append("renew")
            return original_renew(*args, **kwargs)

        self.store.renew_lease = recording_renew
        queue = self.make_queue({"reader-1": driver})

        self.assertEqual(queue.get_device("reader-1")["status"], "connected")
        self.assertEqual(events, ["renew", "driver", "renew"])

    def test_wake_signals_coalesce_and_poll_work_is_bounded(self):
        target = TagTarget("AABBCCDD", "00112233")
        entered, release = threading.Event(), threading.Event()
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]] * 6,
            user_memory={target: b"\x00" * 24},
            block_events={"inventory": (entered, release)},
        )
        queue = self.make_queue({"reader-1": driver}, poll_limit=1, poll_interval=5.0)
        for index in range(3):
            self.store.create_or_get(request(f"r{index}", token=f"000000000000000{index}".encode()))
        queue.wake("reader-1")
        self.assertTrue(entered.wait(1))
        for _ in range(100):
            queue.wake("reader-1")
        release.set()
        wait_for(lambda: self.store.get("r1")["state"] == "succeeded")
        time.sleep(0.1)
        states = [self.store.get(f"r{index}")["state"] for index in range(3)]
        self.assertEqual(states.count("succeeded"), 2)
        self.assertEqual(states.count("queued"), 1)

    def test_lease_loss_stops_before_next_hardware_call(self):
        target = TagTarget("AABBCCDD", "00112233")
        entered, release = threading.Event(), threading.Event()
        driver = FakeDriver(
            capabilities=capabilities(), inventory_snapshots=[[TagObservation(target)]],
            user_memory={target: b"\x00" * 24}, block_events={"inventory": (entered, release)},
        )
        queue = self.make_queue({"reader-1": driver}, lease_seconds=1)
        queue.submit_operation(request("lease-loss"))
        self.assertTrue(entered.wait(1))
        other_store = OperationStore(self.path)
        try:
            other_store.acquire_lease("reader-1", "adapter-new-owner", 30, now=int(time.time()) + 10)
        finally:
            other_store.close()
        release.set()
        time.sleep(0.1)
        self.assertEqual(driver.call_counts.get("inventory"), 1)
        self.assertEqual(driver.call_counts.get("read_memory", 0), 0)
        self.assertEqual(driver.call_counts.get("write_memory", 0), 0)

    def test_boundary_error_keeps_worker_alive_without_tight_retry(self):
        target = TagTarget("AABBCCDD", "00112233")
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[
                [TagObservation(target)], [TagObservation(target)]
            ],
            user_memory={target: b"\x00" * 24},
            scripted_errors={
                "inventory": [
                    AdapterError(AdapterErrorCode.AUTHENTICATION_ERROR, "secret=password-123"),
                    None,
                ]
            },
        )
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=0.02, boundary_retry_seconds=2
        )
        queue.submit_operation(request("boundary"))
        wait_for(lambda: self.store.get("boundary")["state"] == "queued" and
                 self.store.get("boundary")["attempts"] == 1)
        first_calls = driver.call_counts.get("inventory", 0)
        time.sleep(0.2)
        self.assertEqual(driver.call_counts.get("inventory", 0), first_calls)
        self.assertTrue(queue.worker_threads[0].is_alive())
        wait_for(lambda: self.store.get("boundary")["state"] == "succeeded", timeout=2.0)
        self.assertEqual(driver.call_counts.get("write_memory", 0), 1)

    def test_recovery_store_error_keeps_worker_alive_and_blocks_later_write(self):
        target = TagTarget("AABBCCDD", "00112233")
        initial_driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24},
            scripted_errors={
                "read_memory": [
                    None,
                    AdapterError(AdapterErrorCode.TIMEOUT, "private"),
                ]
            },
            write_modes=["no_apply_then_timeout"],
        )
        initial = RfidService(
            self.store,
            {"reader-1": initial_driver},
            owner_id="setup-owner",
            capabilities={"reader-1": capabilities()},
        )
        initial.submit_operation(request("uncertain"))
        self.assertEqual(
            initial.process_operation("reader-1")["error"]["code"],
            "write_uncertain",
        )
        self.store.release_lease("reader-1", "setup-owner")
        initial.close()
        self.store.create_or_get(
            request("later", token=b"0000000000000002")
        )

        original_uncertain = self.store.uncertain_request_ids
        self.store.uncertain_request_ids = mock_uncertain = lambda *args, **kwargs: (
            (_ for _ in ()).throw(StoreError("store_busy"))
        )
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24},
        )
        queue = self.make_queue({"reader-1": driver}, poll_interval=0.02)
        try:
            time.sleep(0.1)
            self.assertTrue(queue.worker_threads[0].is_alive())
            self.assertEqual(driver.call_counts.get("write_memory", 0), 0)
            self.assertEqual(self.store.get("later")["state"], "queued")
        finally:
            self.store.uncertain_request_ids = original_uncertain
            del mock_uncertain

    def test_oldest_unresolved_recovery_blocks_later_recovery(self):
        class RecoveryService:
            lease_seconds = 30

            def __init__(self):
                self.calls = []

            def recover_uncertain(self, request_id):
                self.calls.append(request_id)
                return {
                    "state": "failed_retryable",
                    "error": {"code": "write_uncertain"},
                }

            def acquire_device_lease(self, device_id):
                return None


        worker = _DeviceWorker(
            "reader-1",
            FakeDriver(),
            RecoveryService(),
            self.store,
            "worker-a",
            poll_interval=1.0,
            poll_limit=1,
            recovery_batch=2,
            max_diagnostics=1,
        )
        self.store.uncertain_request_ids = lambda *args, **kwargs: [
            "oldest", "later"
        ]
        self.store.acquire_lease = lambda *args, **kwargs: {}
        self.store.release_lease = lambda *args, **kwargs: True

        self.assertFalse(worker._recover_one_tick())
        self.assertEqual(worker.service.calls, ["oldest"])
        self.assertFalse(worker._recover_one_tick())
        self.assertEqual(worker.service.calls, ["oldest"])

    def test_recovery_close_after_lease_commit_releases_idle_lease(self):
        class PausedRecoveryService:
            lease_seconds = 30

            def __init__(self):
                self.committed = threading.Event()
                self.release = threading.Event()

            def acquire_device_lease(self, device_id):
                self.store.acquire_lease(device_id, "worker-a", 30)
                self.committed.set()
                self.release.wait(1)

            def recover_uncertain(self, request_id):
                raise StoreError("lease_conflict")

        service = PausedRecoveryService()
        service.store = self.store
        worker = _DeviceWorker(
            "reader-1",
            FakeDriver(),
            service,
            self.store,
            "worker-a",
            poll_interval=1.0,
            poll_limit=1,
            recovery_batch=1,
            max_diagnostics=1,
        )
        self.store.uncertain_request_ids = lambda *args, **kwargs: ["uncertain"]
        outcome = []
        runner = threading.Thread(
            target=lambda: outcome.append(worker._recover_one_tick())
        )
        runner.start()
        self.assertTrue(service.committed.wait(1))
        worker.stop()
        service.release.set()
        runner.join(1)

        self.assertFalse(runner.is_alive())
        self.assertEqual(outcome, [False])
        self.assertIsNone(self.store.get_lease("reader-1"))

    def test_restart_uncertain_recovery_is_verify_first(self):
        target = TagTarget("AABBCCDD", "00112233")
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24},
            scripted_errors={
                "read_memory": [
                    None,
                    AdapterError(AdapterErrorCode.TIMEOUT, "private"),
                ]
            },
            write_modes=["no_apply_then_timeout"],
        )
        service = RfidService(
            self.store,
            {"reader-1": driver},
            owner_id="setup-owner",
            capabilities={"reader-1": capabilities()},
        )
        service.submit_operation(request("uncertain"))
        self.assertEqual(
            service.process_operation("reader-1")["error"]["code"],
            "write_uncertain",
        )
        self.store.release_lease("reader-1", "setup-owner")
        service.close()
        resumed = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]] * 2,
            user_memory={target: b"\x00" * 24},
            write_modes=["apply_and_return"],
        )
        self.make_queue(
            {"reader-1": resumed}, recovery_batch=1, poll_interval=5.0,
        )
        wait_for(lambda: self.store.get("uncertain")["state"] == "succeeded")
        operations = [record["operation"] for record in resumed.call_records]
        self.assertEqual(operations[:2], ["inventory", "read_memory"])

    def test_partial_worker_start_failure_rolls_back_started_workers(self):
        drivers = {
            "reader-1": FakeDriver(capabilities=capabilities()),
            "reader-2": FakeDriver(capabilities=capabilities()),
        }
        original_start = threading.Thread.start
        starts = 0

        def fail_second_worker(thread):
            nonlocal starts
            if thread.name.startswith("xq-rfid-"):
                starts += 1
                if starts == 2:
                    raise RuntimeError("worker start failed")
            return original_start(thread)

        with mock.patch.object(threading.Thread, "start", fail_second_worker):
            with self.assertRaisesRegex(RuntimeError, "worker start failed"):
                DeviceQueue(
                    self.store,
                    drivers,
                    capabilities={
                        device_id: capabilities() for device_id in drivers
                    },
                    owner_id="startup-owner",
                )

        wait_for(lambda: drivers["reader-1"].call_counts.get("close", 0) == 1)
        self.assertEqual(drivers["reader-2"].call_counts.get("close", 0), 1)
        self.assertFalse(any(
            thread.name == "xq-rfid-reader-1" and thread.is_alive()
            for thread in threading.enumerate()
        ))

    def test_partial_start_rollback_closes_every_unstarted_driver(self):
        drivers = {
            f"reader-{index}": FakeDriver(capabilities=capabilities())
            for index in range(3)
        }
        original_start = threading.Thread.start
        starts = 0

        def fail_second_worker(thread):
            nonlocal starts
            if thread.name.startswith("xq-rfid-"):
                starts += 1
                if starts == 2:
                    raise RuntimeError("worker start failed")
            return original_start(thread)

        with mock.patch.object(threading.Thread, "start", fail_second_worker):
            with self.assertRaisesRegex(RuntimeError, "worker start failed"):
                DeviceQueue(
                    self.store,
                    drivers,
                    capabilities={
                        device_id: capabilities() for device_id in drivers
                    },
                    owner_id="startup-owner",
                    shutdown_timeout=0,
                )

        wait_for(lambda: all(
            driver.call_counts.get("close", 0) == 1
            for driver in drivers.values()
        ))

    def test_partial_start_rollback_uses_shutdown_deadline(self):
        close_entered = threading.Event()
        release_close = threading.Event()

        class BlockingCloseDriver(FakeDriver):
            def close(self):
                close_entered.set()
                release_close.wait(1)
                super().close()

        drivers = {
            "reader-1": BlockingCloseDriver(capabilities=capabilities()),
            "reader-2": FakeDriver(capabilities=capabilities()),
        }
        original_start = threading.Thread.start
        starts = 0
        outcome = []

        def fail_second_worker(thread):
            nonlocal starts
            if thread.name.startswith("xq-rfid-"):
                starts += 1
                if starts == 2:
                    raise RuntimeError("worker start failed")
            return original_start(thread)

        def construct():
            try:
                DeviceQueue(
                    self.store,
                    drivers,
                    capabilities={
                        device_id: capabilities() for device_id in drivers
                    },
                    owner_id="startup-owner",
                    shutdown_timeout=0.05,
                )
            except BaseException as error:
                outcome.append(error)

        with mock.patch.object(threading.Thread, "start", fail_second_worker):
            constructor = threading.Thread(target=construct)
            constructor.start()
            self.assertTrue(close_entered.wait(1))
            constructor.join(0.2)

        self.assertFalse(constructor.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], RuntimeError)
        self.assertEqual(str(outcome[0]), "worker start failed")
        release_close.set()
        wait_for(lambda: drivers["reader-1"].call_counts.get("close", 0) == 1)

    def test_bounds_worker_count_and_default_owner_has_128_random_bits(self):
        drivers = {f"reader-{index}": FakeDriver() for index in range(65)}
        with self.assertRaises(QueueError) as caught:
            DeviceQueue(self.store, drivers, capabilities={device_id: capabilities() for device_id in drivers})
        self.assertEqual(caught.exception.code, "too_many_devices")
        queue = self.make_queue({"reader-1": FakeDriver()}, owner_id=None)
        self.assertRegex(queue.owner_id, r"\Aadapter-[0-9a-f]{32,}\Z")
        self.assertTrue(all(thread.daemon for thread in queue.worker_threads))

    def test_close_cancellation_during_validation_prevents_late_commit(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=5.0, shutdown_timeout=0.05
        )
        entered = threading.Event()
        release = threading.Event()
        outcome = []

        class PausedRequest(dict):
            def __getitem__(self, key):
                if key == "device_id":
                    entered.set()
                    release.wait(1)
                return super().__getitem__(key)

        value = PausedRequest(request("validation-race"))
        submitter = threading.Thread(
            target=lambda: self._capture_queue_submit(queue, value, outcome)
        )
        submitter.start()
        self.assertTrue(entered.wait(1))

        queue.close()
        self.assertIsNone(self.store.get("validation-race"))
        release.set()
        submitter.join(1)

        self.assertFalse(submitter.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")
        self.assertIsNone(self.store.get("validation-race"))
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )

    def test_timed_out_close_permanently_rejects_uncommitted_submission(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=5.0, shutdown_timeout=0.05
        )
        entered = threading.Event()
        release = threading.Event()
        original_submit = queue._service.submit_operation
        outcome = []

        def blocked_submit(value):
            entered.set()
            release.wait(1)
            return original_submit(value)

        queue._service.submit_operation = blocked_submit
        submitter = threading.Thread(
            target=lambda: self._capture_queue_submit(
                queue, request("blocked"), outcome
            )
        )
        submitter.start()
        self.assertTrue(entered.wait(1))

        started = time.monotonic()
        queue.close()
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.15)
        self.assertTrue(queue._closing_event.is_set())
        self.assertTrue(queue._cancellation_event.is_set())
        with self.assertRaises(QueueError) as caught:
            queue.submit_operation(request("after-close"))
        self.assertEqual(caught.exception.code, "queue_closed")
        release.set()
        submitter.join(1)
        self.assertFalse(submitter.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")
        self.assertIsNone(self.store.get("blocked"))
        self.assertIsNone(self.store.get("after-close"))
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )
        wait_for(lambda: not queue.worker_threads[0].is_alive())

    @staticmethod
    def _capture_queue_submit(queue, value, outcome=None):
        try:
            result = queue.submit_operation(value)
        except BaseException as error:
            result = error
        if outcome is not None:
            outcome.append(result)

    def test_close_preserves_an_already_committed_submission(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue({"reader-1": driver}, poll_interval=5.0)
        accepted = threading.Event()
        release = threading.Event()
        original_submit = queue._service.submit_operation
        outcome = []

        def paused_submit(value):
            result = original_submit(value)
            accepted.set()
            release.wait(1)
            return result

        queue._service.submit_operation = paused_submit
        submitter = threading.Thread(
            target=lambda: outcome.append(queue.submit_operation(request("accepted")))
        )
        submitter.start()
        self.assertTrue(accepted.wait(1))

        started = time.monotonic()
        queue.close()

        self.assertLess(time.monotonic() - started, 0.3)
        release.set()
        submitter.join(1)
        self.assertFalse(submitter.is_alive())
        self.assertEqual(outcome[0]["request_id"], "accepted")
        self.assertEqual(self.store.get("accepted")["request_id"], "accepted")
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )

    def test_claim_commit_paused_across_close_is_rolled_back(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue(
            {"reader-1": driver}, poll_interval=5.0, shutdown_timeout=0.05
        )
        queue._workers["reader-1"].stop()
        queue.worker_threads[0].join(1)
        self.store.create_or_get(request("claim-race"))
        commit_reached = threading.Event()
        release_commit = threading.Event()
        original_transaction = self.store._transaction
        outcome = []

        @contextmanager
        def paused_transaction(*args, **kwargs):
            with original_transaction(*args, **kwargs) as connection:
                yield connection
                if kwargs.get("cancellation_event") is queue._cancellation_event:
                    commit_reached.set()
                    release_commit.wait(1)

        self.store._transaction = paused_transaction
        worker = threading.Thread(
            target=lambda: self._capture_process_operation(
                queue._service, outcome
            )
        )
        worker.start()
        self.assertTrue(commit_reached.wait(1))

        queue.close()
        release_commit.set()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")
        self.assertEqual(self.store.get("claim-race")["state"], "queued")
        self.assertIsNone(self.store.get_lease("reader-1"))
        self.assertEqual(
            [record["operation"] for record in driver.call_records],
            ["close"],
        )

    @staticmethod
    def _capture_process_operation(service, outcome):
        try:
            result = service.process_operation("reader-1")
        except BaseException as error:
            result = error
        outcome.append(result)

    def test_close_stops_blocked_call_heartbeat_so_expired_lease_can_be_taken_over(self):
        target = TagTarget("AABBCCDD", "00112233")
        entered, release = threading.Event(), threading.Event()
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(target)]],
            user_memory={target: b"\x00" * 24},
            block_events={"inventory": (entered, release)},
        )
        queue = self.make_queue(
            {"reader-1": driver}, lease_seconds=1, shutdown_timeout=0.05
        )
        queue.submit_operation(request("blocked-heartbeat"))
        self.assertTrue(entered.wait(1))
        wait_for(lambda: any(
            thread.name == "xq-rfid-lease-reader-1" and thread.is_alive()
            for thread in threading.enumerate()
        ))

        started = time.monotonic()
        queue.close()

        self.assertLess(time.monotonic() - started, 0.15)
        wait_for(lambda: not any(
            thread.name == "xq-rfid-lease-reader-1" and thread.is_alive()
            for thread in threading.enumerate()
        ))
        self.assertTrue(queue.worker_threads[0].is_alive())
        self.assertEqual(driver.call_counts.get("close", 0), 0)

        other_store = OperationStore(self.path)
        try:
            lease = other_store.acquire_lease(
                "reader-1", "takeover-owner", 30, now=int(time.time()) + 10
            )
            self.assertEqual(lease["owner_id"], "takeover-owner")
        finally:
            try:
                other_store.release_lease("reader-1", "takeover-owner")
            except StoreError:
                pass
            release.set()
            other_store.close()
        wait_for(lambda: driver.call_counts.get("close", 0) == 1)

    def test_concurrent_close_callers_share_one_shutdown(self):
        driver = FakeDriver(capabilities=capabilities())
        queue = self.make_queue({"reader-1": driver}, shutdown_timeout=0.5)
        entered = threading.Event()
        release = threading.Event()
        original_close = queue._service.close
        calls = []

        def paused_close(timeout=None):
            calls.append(timeout)
            entered.set()
            release.wait(1)
            return original_close(timeout=timeout)

        queue._service.close = paused_close
        closers = [threading.Thread(target=queue.close) for _ in range(2)]
        closers[0].start()
        self.assertTrue(entered.wait(1))
        closers[1].start()
        time.sleep(0.05)
        self.assertTrue(closers[1].is_alive())
        release.set()
        for closer in closers:
            closer.join(1)
            self.assertFalse(closer.is_alive())
        self.assertEqual(len(calls), 1)

    def test_close_uses_shared_deadline_is_idempotent_and_never_closes_active_driver(self):
        releases = []
        drivers = {}
        for index in range(2):
            target = TagTarget(f"AABBCC0{index}", f"0011220{index}")
            entered, release = threading.Event(), threading.Event()
            releases.append(release)
            driver = FakeDriver(
                capabilities=capabilities(), inventory_snapshots=[[TagObservation(target)]],
                user_memory={target: b"\x00" * 24}, block_events={"inventory": (entered, release)},
            )
            driver._test_entered = entered
            drivers[f"reader-{index}"] = driver
        queue = self.make_queue(drivers, shutdown_timeout=0.05)
        queue.submit_operation(request("r0", device_id="reader-0"))
        queue.submit_operation(request("r1", device_id="reader-1"))
        self.assertTrue(all(driver._test_entered.wait(1) for driver in drivers.values()))
        started = time.monotonic()
        queue.close()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.15)
        self.assertTrue(all(thread.daemon and thread.is_alive() for thread in queue.worker_threads))
        self.assertTrue(all(driver.call_counts.get("close", 0) == 0 for driver in drivers.values()))
        queue.close()
        self.assertTrue(all(driver.call_counts.get("close", 0) == 0 for driver in drivers.values()))
        for release in releases:
            release.set()
        wait_for(lambda: all(driver.call_counts.get("close", 0) == 1 for driver in drivers.values()))
        queue.close()
        self.assertTrue(all(driver.call_counts.get("close", 0) == 1 for driver in drivers.values()))


if __name__ == "__main__":
    unittest.main()
