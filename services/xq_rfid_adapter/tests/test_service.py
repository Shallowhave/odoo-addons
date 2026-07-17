import binascii
import hashlib
import os
import tempfile
import threading
import time
import unittest

from xq_rfid_adapter.domain import (
    AdapterError,
    AdapterErrorCode,
    DeviceCapabilities,
    TagObservation,
    TagTarget,
)
from xq_rfid_adapter.drivers.base import Driver
from xq_rfid_adapter.drivers.fake import FakeDriver
from xq_rfid_adapter.service import RfidService, identity_hash, validate_payload
from unittest import mock

from xq_rfid_adapter.store import OperationStore, StoreError


def payload(token=b"0123456789ABCDEF"):
    prefix = b"XQ" + bytes((1, 0)) + token
    return prefix + binascii.crc32(prefix).to_bytes(4, "big")


def request(request_id="r1", *, data=None, version=1, device_id="reader-1"):
    data = payload() if data is None else data
    return {
        "request_id": request_id,
        "device_id": device_id,
        "operation_type": "write_and_verify",
        "payload_hex": data.hex(),
        "payload_version": version,
    }


def capabilities(*, epc=True, tid=True, user_read=True, user_write=True):
    return DeviceCapabilities(epc, False, tid, False, user_read, user_write)


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = OperationStore(os.path.join(self.tempdir.name, "operations.sqlite3"))
        self.target = TagTarget("AABBCCDD", "00112233")
        self.driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[
                [TagObservation(self.target)],
                [TagObservation(self.target)],
            ],
            user_memory={self.target: b"\x00" * 24},
        )
        self.service = RfidService(
            self.store,
            {"reader-1": self.driver},
            owner_id="worker-a",
            capabilities={"reader-1": capabilities()},
        )

    def tearDown(self):
        self.service.close()
        self.store.close()
        self.tempdir.cleanup()

    def test_fake_driver_satisfies_protocol_and_copies_call_records(self):
        self.assertIsInstance(self.driver, Driver)
        result = self.driver.inventory(500, True)
        result.clear()
        records = self.driver.call_records
        self.assertEqual(records[0]["operation"], "inventory")
        records[0]["operation"] = "tampered"
        self.assertEqual(self.driver.call_records[0]["operation"], "inventory")

    def test_payload_validation_golden_and_rejections_create_no_rows(self):
        self.assertEqual(validate_payload(request()), payload())
        invalid = []
        bad_magic = bytearray(payload()); bad_magic[:2] = b"NO"; invalid.append(request(data=bytes(bad_magic)))
        bad_version = bytearray(payload()); bad_version[2] = 2; invalid.append(request(data=bytes(bad_version), version=2))
        bad_crc = bytearray(payload()); bad_crc[-1] ^= 1; invalid.append(request(data=bytes(bad_crc)))
        invalid.append(request(version=2))
        invalid.append({**request(), "payload_hex": "GG" * 24})
        invalid.append({**request(), "payload_hex": b"00" * 24})
        for index, value in enumerate(invalid):
            value["request_id"] = f"bad-{index}"
            with self.subTest(index=index), self.assertRaises(AdapterError) as caught:
                self.service.submit_operation(value)
            self.assertEqual(caught.exception.code, AdapterErrorCode.PROTOCOL_ERROR)
        self.assertEqual(self.store.count(), 0)

    def test_successful_write_exact_sequence_and_safe_public_identity(self):
        submitted = self.service.submit_operation(request())
        self.assertEqual(submitted["state"], "queued")
        result = self.service.process_operation("reader-1")
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(
            [record["operation"] for record in self.driver.call_records],
            ["inventory", "read_memory", "inventory", "write_memory", "read_memory"],
        )
        for record in self.driver.call_records:
            if record["operation"] in {"read_memory", "write_memory"}:
                self.assertEqual(record["word_offset"], 0)
            if record["operation"] == "read_memory":
                self.assertEqual(record["word_count"], 12)
        public = self.service.get_operation("r1")
        self.assertEqual(public["identity_hash"], "sha256:" + identity_hash(self.target))
        self.assertTrue(public["verification_ok"])
        self.assertEqual(public["epc_identity"], {"nibble_length": 8, "suffix": "DD"})
        self.assertEqual(public["tid_identity"], {"nibble_length": 8, "suffix": "33"})
        rendered = repr(public)
        self.assertNotIn(self.target.epc, rendered)
        self.assertNotIn(self.target.tid, rendered)
        self.assertNotIn(request()["payload_hex"], rendered)

    def test_duplicate_submission_writes_once(self):
        self.service.submit_operation(request())
        self.service.submit_operation(request())
        self.service.process_operation("reader-1")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 1)
        self.assertEqual(self.store.count(), 1)

    def test_missing_required_capability_fails_before_any_driver_call(self):
        for missing in ("epc", "user_read", "user_write"):
            with self.subTest(missing=missing):
                store = OperationStore(
                    os.path.join(self.tempdir.name, f"missing-{missing}.sqlite3")
                )
                driver = FakeDriver(capabilities=capabilities())
                service = RfidService(
                    store,
                    {"reader-1": driver},
                    owner_id="worker-a",
                    capabilities={
                        "reader-1": capabilities(**{missing: False})
                    },
                )
                try:
                    service.submit_operation(request(f"missing-{missing}"))

                    result = service.process_operation("reader-1")

                    self.assertEqual(result["state"], "failed_manual")
                    self.assertEqual(
                        result["error"]["code"], "unsupported_memory"
                    )
                    self.assertEqual(driver.call_records, [])
                finally:
                    service.close()
                    store.close()

    def test_duplicate_observations_collapse_and_conflicting_tid_fails(self):
        duplicate = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[
                [TagObservation(self.target), TagObservation(self.target)],
                [TagObservation(self.target)],
            ],
            user_memory={self.target: b"\x00" * 24},
        )
        service = RfidService(
            self.store,
            {"reader-1": duplicate},
            owner_id="worker-a",
            capabilities={"reader-1": capabilities()},
        )
        service.submit_operation(request())
        self.assertEqual(service.process_operation("reader-1")["state"], "succeeded")
        service.close()

        store2 = OperationStore(os.path.join(self.tempdir.name, "other.sqlite3"))
        conflicting = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(TagTarget("AABB", "0011")), TagObservation(TagTarget("AABB", "0022"))]],
        )
        service2 = RfidService(store2, {"reader-1": conflicting}, owner_id="worker-a", capabilities={"reader-1": capabilities()})
        try:
            service2.submit_operation(request("r2"))
            result = service2.process_operation("reader-1")
            self.assertEqual(result["error"]["code"], "multiple_tags")
            self.assertEqual(conflicting.call_counts.get("write_memory", 0), 0)
        finally:
            service2.close(); store2.close()

    def test_same_epc_with_mixed_missing_and_present_tid_is_multiple_tags(self):
        driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[
                TagObservation(TagTarget("AABB")),
                TagObservation(TagTarget("AABB", "0011")),
            ]],
        )
        service = RfidService(
            self.store,
            {"reader-1": driver},
            owner_id="worker-a",
            capabilities={"reader-1": capabilities()},
        )
        service.submit_operation(request())

        result = service.process_operation("reader-1")

        self.assertEqual(result["error"]["code"], "multiple_tags")
        self.assertEqual(driver.call_counts.get("write_memory", 0), 0)
        service.close()

    def test_zero_multiple_missing_tid_and_target_change_fail_before_write(self):
        cases = [
            ([], "no_tag"),
            ([TagObservation(self.target), TagObservation(TagTarget("EEFF", "4455"))], "multiple_tags"),
            ([TagObservation(TagTarget("AABB"))], "protocol_error"),
        ]
        for index, (snapshot, code) in enumerate(cases):
            path = os.path.join(self.tempdir.name, f"case-{index}.sqlite3")
            store = OperationStore(path)
            driver = FakeDriver(capabilities=capabilities(), inventory_snapshots=[snapshot])
            service = RfidService(store, {"reader-1": driver}, owner_id="worker-a", capabilities={"reader-1": capabilities()})
            try:
                service.submit_operation(request(f"case-{index}"))
                result = service.process_operation("reader-1")
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(driver.call_counts.get("write_memory", 0), 0)
            finally:
                service.close(); store.close()

        changed = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(self.target)], [TagObservation(TagTarget("AABBCCEE", "00112244"))]],
            user_memory={self.target: b"\x00" * 24},
        )
        store = OperationStore(os.path.join(self.tempdir.name, "changed.sqlite3"))
        service = RfidService(store, {"reader-1": changed}, owner_id="worker-a", capabilities={"reader-1": capabilities()})
        try:
            service.submit_operation(request("changed"))
            result = service.process_operation("reader-1")
            self.assertEqual(result["error"]["code"], "target_changed")
            self.assertEqual(changed.call_counts.get("write_memory", 0), 0)
        finally:
            service.close(); store.close()

    def test_short_read_capacity_and_unsupported_are_manual_safe_failures(self):
        for index, error in enumerate((
            AdapterError(AdapterErrorCode.CAPACITY_EXCEEDED, "unsafe capacity details"),
            AdapterError(AdapterErrorCode.UNSUPPORTED_MEMORY, "unsafe bank details"),
        )):
            store = OperationStore(os.path.join(self.tempdir.name, f"error-{index}.sqlite3"))
            driver = FakeDriver(
                capabilities=capabilities(),
                inventory_snapshots=[[TagObservation(self.target)]],
                scripted_errors={"read_memory": [error]},
            )
            service = RfidService(store, {"reader-1": driver}, owner_id="worker-a", capabilities={"reader-1": capabilities()})
            try:
                service.submit_operation(request(f"error-{index}"))
                result = service.process_operation("reader-1")
                self.assertEqual(result["error"]["code"], error.code.value)
                self.assertNotIn("unsafe", repr(result))
            finally:
                service.close(); store.close()

        short = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(self.target)]],
            user_memory={self.target: b"\x00" * 22},
            capacities={self.target: 24},
        )
        store = OperationStore(os.path.join(self.tempdir.name, "short.sqlite3"))
        service = RfidService(store, {"reader-1": short}, owner_id="worker-a", capabilities={"reader-1": capabilities()})
        try:
            service.submit_operation(request("short"))
            result = service.process_operation("reader-1")
            self.assertEqual(result["error"]["code"], "capacity_exceeded")
        finally:
            service.close(); store.close()

    def test_known_post_write_verification_errors_remain_exact_manual_failures(self):
        codes = (
            AdapterErrorCode.TARGET_CHANGED,
            AdapterErrorCode.PROTOCOL_ERROR,
            AdapterErrorCode.DEVICE_ERROR,
            AdapterErrorCode.CAPACITY_EXCEEDED,
            AdapterErrorCode.UNSUPPORTED_MEMORY,
        )
        for index, code in enumerate(codes):
            store = OperationStore(
                os.path.join(self.tempdir.name, f"verify-known-{index}.sqlite3")
            )
            driver = FakeDriver(
                capabilities=capabilities(),
                inventory_snapshots=[
                    [TagObservation(self.target)], [TagObservation(self.target)]
                ],
                user_memory={self.target: b"\x00" * 24},
                scripted_errors={
                    "read_memory": [None, AdapterError(code, "unsafe details")]
                },
            )
            service = RfidService(
                store,
                {"reader-1": driver},
                owner_id="worker-a",
                capabilities={"reader-1": capabilities()},
            )
            try:
                service.submit_operation(request(f"verify-known-{index}"))
                result = service.process_operation("reader-1")
                self.assertEqual(result["state"], "failed_manual")
                self.assertEqual(result["error"]["code"], code.value)
                self.assertNotIn("unsafe", repr(result))
                self.assertNotIn(
                    f"verify-known-{index}",
                    store.uncertain_request_ids("reader-1"),
                )
            finally:
                service.close()
                store.close()

    def test_apply_then_timeout_reconfirms_target_before_accepting_readback(self):
        changed = TagTarget("AABBCCEE", "00112244")
        self.driver.write_modes = ["apply_then_timeout"]
        self.driver.inventory_snapshots.append([TagObservation(changed)])
        self.service.submit_operation(request())

        result = self.service.process_operation("reader-1")

        self.assertEqual(result["state"], "failed_manual")
        self.assertEqual(result["error"]["code"], "target_changed")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 1)
        self.assertEqual(self.driver.call_counts.get("read_memory", 0), 1)

    def test_apply_then_timeout_succeeds_with_one_write(self):
        self.driver.write_modes = ["apply_then_timeout"]
        self.driver.inventory_snapshots.append([TagObservation(self.target)])
        self.service.submit_operation(request())
        result = self.service.process_operation("reader-1")
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 1)

    def test_exact_negative_write_response_fails_manual_without_rewrite(self):
        class NegativeResponseDriver:
            def __init__(self, delegate):
                self.delegate = delegate
                self.write_calls = 0

            def test_connection(self):
                return self.delegate.test_connection()

            def get_device_info(self):
                return self.delegate.get_device_info()

            def inventory(self, duration_ms, include_tid):
                return self.delegate.inventory(duration_ms, include_tid)

            def read_memory(self, target, bank, word_offset, word_count):
                return self.delegate.read_memory(
                    target, bank, word_offset, word_count
                )

            def write_memory(self, target, bank, word_offset, data):
                self.write_calls += 1
                return {"written": False}

            def close(self):
                self.delegate.close()

        driver = NegativeResponseDriver(self.driver)
        service = RfidService(
            self.store,
            {"reader-1": driver},
            owner_id="worker-a",
            capabilities={"reader-1": capabilities()},
        )
        service.submit_operation(request())

        result = service.process_operation("reader-1")

        self.assertEqual(result["state"], "failed_manual")
        self.assertEqual(result["error"]["code"], "device_error")
        self.assertEqual(driver.write_calls, 1)
        self.assertEqual(self.driver.call_counts.get("read_memory", 0), 1)

    def test_malformed_write_responses_verify_possible_apply(self):
        class MalformedResponseDriver:
            def __init__(self, delegate, response):
                self.delegate = delegate
                self.response = response

            def test_connection(self):
                return self.delegate.test_connection()

            def get_device_info(self):
                return self.delegate.get_device_info()

            def inventory(self, duration_ms, include_tid):
                return self.delegate.inventory(duration_ms, include_tid)

            def read_memory(self, target, bank, word_offset, word_count):
                return self.delegate.read_memory(
                    target, bank, word_offset, word_count
                )

            def write_memory(self, target, bank, word_offset, data):
                self.delegate.write_memory(target, bank, word_offset, data)
                return self.response

            def close(self):
                self.delegate.close()

        for index, response in enumerate(({}, None, True)):
            with self.subTest(response=response):
                store = OperationStore(
                    os.path.join(self.tempdir.name, f"write-response-{index}.sqlite3")
                )
                delegate = FakeDriver(
                    capabilities=capabilities(),
                    inventory_snapshots=[
                        [TagObservation(self.target)], [TagObservation(self.target)],
                        [TagObservation(self.target)],
                    ],
                    user_memory={self.target: b"\x00" * 24},
                )
                driver = MalformedResponseDriver(delegate, response)
                service = RfidService(
                    store,
                    {"reader-1": driver},
                    owner_id="worker-a",
                    capabilities={"reader-1": capabilities()},
                )
                try:
                    service.submit_operation(request(f"write-response-{index}"))
                    result = service.process_operation("reader-1")
                    self.assertEqual(result["state"], "succeeded")
                    self.assertIsNone(result["error"])
                    self.assertEqual(delegate.call_counts.get("read_memory", 0), 2)
                    self.assertEqual(delegate.call_counts.get("write_memory", 0), 1)
                finally:
                    service.close()
                    store.close()

    def test_no_apply_timeout_allows_exactly_one_controlled_rewrite(self):
        self.driver.write_modes = ["no_apply_then_timeout", "apply_and_return"]
        self.driver.inventory_snapshots.extend([[TagObservation(self.target)]])
        self.service.submit_operation(request())
        result = self.service.process_operation("reader-1")
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 2)
        self.assertEqual(self.store.get("r1")["state"], "succeeded")

    def test_partial_timeout_fails_manual_without_rewrite(self):
        self.driver.write_modes = ["partial_apply_then_timeout"]
        self.service.submit_operation(request())
        result = self.service.process_operation("reader-1")
        self.assertEqual(result["error"]["code"], "verification_failed")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 1)

    def test_second_controlled_rewrite_read_timeout_remains_uncertain(self):
        self.driver.write_modes = ["no_apply_then_timeout", "apply_and_return"]
        self.driver.inventory_snapshots.append([TagObservation(self.target)])
        self.driver.scripted_errors = {
            "read_memory": [
                None,
                None,
                AdapterError(AdapterErrorCode.TIMEOUT, "unsafe timeout"),
            ]
        }
        self.service.submit_operation(request())

        result = self.service.process_operation("reader-1")

        self.assertEqual(result["state"], "failed_retryable")
        self.assertEqual(result["error"]["code"], "write_uncertain")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 2)
        self.assertIn("r1", self.store.uncertain_request_ids("reader-1"))

    def test_unreadable_verification_is_write_uncertain_and_restart_is_verify_first(self):
        self.driver.write_modes = ["no_apply_then_timeout"]
        self.driver.scripted_errors = {
            "read_memory": [None, AdapterError(AdapterErrorCode.TIMEOUT, "unsafe timeout")]
        }
        self.service.submit_operation(request())
        result = self.service.process_operation("reader-1")
        self.assertEqual(result["error"]["code"], "write_uncertain")
        self.assertFalse(result["error"]["retryable"])
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 1)

        resumed_driver = FakeDriver(
            capabilities=capabilities(),
            inventory_snapshots=[[TagObservation(self.target)], [TagObservation(self.target)]],
            user_memory={self.target: b"\x00" * 24},
        )
        resumed = RfidService(self.store, {"reader-1": resumed_driver}, owner_id="worker-a", capabilities={"reader-1": capabilities()})
        try:
            final = resumed.recover_uncertain("r1")
            self.assertEqual(final["state"], "succeeded")
            self.assertEqual(resumed_driver.call_records[0]["operation"], "inventory")
            self.assertEqual(resumed_driver.call_records[1]["operation"], "read_memory")
            self.assertEqual(resumed_driver.call_counts.get("write_memory", 0), 1)
        finally:
            resumed.close()

    def test_uncertain_recovery_missing_capability_remains_resumable_without_driver_call(self):
        for missing in ("epc", "user_read", "user_write"):
            with self.subTest(missing=missing):
                request_id = f"recover-missing-{missing}"
                store = OperationStore(
                    os.path.join(self.tempdir.name, f"recover-missing-{missing}.sqlite3")
                )
                initial_driver = FakeDriver(
                    capabilities=capabilities(),
                    inventory_snapshots=[
                        [TagObservation(self.target)], [TagObservation(self.target)]
                    ],
                    user_memory={self.target: b"\x00" * 24},
                    scripted_errors={
                        "read_memory": [
                            None,
                            AdapterError(AdapterErrorCode.TIMEOUT, "unsafe timeout"),
                        ]
                    },
                    write_modes=["no_apply_then_timeout"],
                )
                initial = RfidService(
                    store,
                    {"reader-1": initial_driver},
                    owner_id="worker-a",
                    capabilities={"reader-1": capabilities()},
                )
                try:
                    initial.submit_operation(request(request_id))
                    first = initial.process_operation("reader-1")
                    self.assertEqual(first["state"], "failed_retryable")
                    self.assertEqual(first["error"]["code"], "write_uncertain")
                finally:
                    initial.close()

                recovery_driver = FakeDriver(
                    capabilities=capabilities(),
                    inventory_snapshots=[
                        [TagObservation(self.target)], [TagObservation(self.target)]
                    ],
                    user_memory={self.target: b"\x00" * 24},
                )
                recovery = RfidService(
                    store,
                    {"reader-1": recovery_driver},
                    owner_id="worker-a",
                    capabilities={
                        "reader-1": capabilities(**{missing: False})
                    },
                )
                try:
                    result = recovery.recover_uncertain(request_id)

                    self.assertEqual(result["state"], "failed_retryable")
                    self.assertEqual(result["error"]["code"], "write_uncertain")
                    self.assertEqual(recovery_driver.call_records, [])
                    self.assertIn(
                        request_id, store.uncertain_request_ids("reader-1")
                    )
                finally:
                    recovery.close()
                    store.close()

    def test_rewrite_reconfirmation_unprovable_errors_remain_discoverably_uncertain(self):
        for index, code in enumerate((
            AdapterErrorCode.TIMEOUT,
            AdapterErrorCode.CONNECTION_ERROR,
            AdapterErrorCode.AUTHENTICATION_ERROR,
            AdapterErrorCode.CONFIGURATION_ERROR,
        )):
            request_id = f"reconfirm-uncertain-{index}"
            store = OperationStore(
                os.path.join(self.tempdir.name, f"reconfirm-uncertain-{index}.sqlite3")
            )
            driver = FakeDriver(
                capabilities=capabilities(),
                inventory_snapshots=[
                    [TagObservation(self.target)], [TagObservation(self.target)]
                ],
                user_memory={self.target: b"\x00" * 24},
                scripted_errors={
                    "inventory": [None, None, AdapterError(code, "unsafe details")]
                },
                write_modes=["no_apply_then_timeout"],
            )
            service = RfidService(
                store,
                {"reader-1": driver},
                owner_id="worker-a",
                capabilities={"reader-1": capabilities()},
            )
            try:
                service.submit_operation(request(request_id))
                result = service.process_operation("reader-1")
                self.assertEqual(result["state"], "failed_retryable")
                self.assertEqual(result["error"]["code"], "write_uncertain")
                self.assertIn(request_id, store.uncertain_request_ids("reader-1"))
            finally:
                service.close()
                store.close()

    def test_rewrite_reconfirmation_known_error_remains_exact_manual_failure(self):
        self.driver.write_modes = ["no_apply_then_timeout"]
        self.driver.inventory_snapshots.extend([[TagObservation(self.target)]])
        self.driver.scripted_errors = {
            "inventory": [None, None, AdapterError(
                AdapterErrorCode.TARGET_CHANGED, "unsafe details"
            )]
        }
        self.service.submit_operation(request())

        result = self.service.process_operation("reader-1")

        self.assertEqual(result["state"], "failed_manual")
        self.assertEqual(result["error"]["code"], "target_changed")
        self.assertNotIn("r1", self.store.uncertain_request_ids("reader-1"))

    def test_controlled_rewrite_reinventories_and_rejects_changed_target(self):
        changed = TagTarget("AABBCCEE", "00112244")
        self.driver.write_modes = ["no_apply_then_timeout"]
        self.driver.inventory_snapshots.extend([[TagObservation(changed)]])
        self.service.submit_operation(request())

        result = self.service.process_operation("reader-1")

        self.assertEqual(result["state"], "failed_manual")
        self.assertEqual(result["error"]["code"], "target_changed")
        self.assertEqual(self.driver.call_counts.get("write_memory", 0), 1)
        self.assertEqual(self.driver.call_counts.get("inventory", 0), 3)

    def test_structural_driver_uses_injected_capabilities_only(self):
        class StructuralDriver:
            def __init__(self, delegate):
                self._delegate = delegate

            def test_connection(self):
                return self._delegate.test_connection()

            def get_device_info(self):
                return self._delegate.get_device_info()

            def inventory(self, duration_ms, include_tid):
                return self._delegate.inventory(duration_ms, include_tid)

            def read_memory(self, target, bank, word_offset, word_count):
                return self._delegate.read_memory(target, bank, word_offset, word_count)

            def write_memory(self, target, bank, word_offset, payload):
                return self._delegate.write_memory(target, bank, word_offset, payload)

            def close(self):
                return self._delegate.close()

        driver = StructuralDriver(self.driver)
        service = RfidService(
            self.store,
            {"reader-1": driver},
            owner_id="worker-a",
            capabilities={"reader-1": capabilities()},
        )
        service.submit_operation(request())
        self.assertEqual(service.process_operation("reader-1")["state"], "succeeded")

    def test_pre_write_auth_and_configuration_errors_defer_without_persisting_error(self):
        for index, code in enumerate((
            AdapterErrorCode.AUTHENTICATION_ERROR,
            AdapterErrorCode.CONFIGURATION_ERROR,
        )):
            store = OperationStore(os.path.join(self.tempdir.name, f"boundary-{index}.sqlite3"))
            driver = FakeDriver(
                capabilities=capabilities(),
                scripted_errors={"inventory": [AdapterError(code, "secret=password-123")]},
            )
            service = RfidService(
                store,
                {"reader-1": driver},
                owner_id="worker-a",
                capabilities={"reader-1": capabilities()},
                boundary_retry_seconds=60,
            )
            try:
                service.submit_operation(request(f"boundary-{index}"))
                with self.assertRaises(AdapterError) as caught:
                    service.process_operation("reader-1")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.message, (
                    "authentication failed" if code is AdapterErrorCode.AUTHENTICATION_ERROR
                    else "resource is not configured"
                ))
                self.assertNotIn("secret", repr(caught.exception.to_dict()))
                operation = store.get(f"boundary-{index}")
                self.assertEqual(operation["state"], "queued")
                self.assertEqual(operation["attempts"], 1)
                self.assertIsNone(operation["error"])
                self.assertIsNone(operation["result"])
                self.assertIsNone(operation["claim_owner"])
                self.assertIsNone(operation["lease_until"])
                self.assertIsNone(operation["claimed_at"])
                self.assertIsNone(store.get_lease("reader-1"))
            finally:
                service.close()
                store.close()

    def test_post_write_auth_and_configuration_errors_verify_before_finishing(self):
        for index, (operation, code) in enumerate((
            ("write_memory", AdapterErrorCode.AUTHENTICATION_ERROR),
            ("read_memory", AdapterErrorCode.CONFIGURATION_ERROR),
        )):
            store = OperationStore(os.path.join(self.tempdir.name, f"post-boundary-{index}.sqlite3"))
            scripted = (
                {"write_memory": [AdapterError(code, "secret=password-123")]}
                if operation == "write_memory"
                else {
                    "read_memory": [
                        None,
                        AdapterError(code, "secret=password-123"),
                    ]
                }
            )
            driver = FakeDriver(
                capabilities=capabilities(),
                inventory_snapshots=[
                    [TagObservation(self.target)], [TagObservation(self.target)]
                ],
                user_memory={self.target: b"\x00" * 24},
                scripted_errors=scripted,
            )
            service = RfidService(
                store,
                {"reader-1": driver},
                owner_id="worker-a",
                capabilities={"reader-1": capabilities()},
                boundary_retry_seconds=60,
            )
            try:
                service.submit_operation(request(f"post-boundary-{index}"))
                result = service.process_operation("reader-1")
                if operation == "write_memory":
                    self.assertEqual(result["state"], "succeeded")
                    self.assertEqual(driver.call_counts.get("write_memory", 0), 2)
                else:
                    self.assertEqual(result["state"], "failed_retryable")
                    self.assertEqual(result["error"]["code"], "write_uncertain")
                    self.assertFalse(result["error"]["retryable"])
                    self.assertEqual(driver.call_counts.get("write_memory", 0), 1)
                self.assertNotIn("secret", repr(result))
            finally:
                service.close()
                store.close()

    def test_uncertain_recovery_target_mismatch_fails_manual(self):
        changed = TagTarget("AABBCCEE", "00112244")
        self.driver.write_modes = ["no_apply_then_timeout"]
        self.driver.scripted_errors = {
            "read_memory": [
                None,
                AdapterError(AdapterErrorCode.TIMEOUT, "unsafe timeout"),
            ]
        }
        self.service.submit_operation(request())
        first = self.service.process_operation("reader-1")
        self.assertEqual(first["error"]["code"], "write_uncertain")
        self.driver.inventory_snapshots.append([TagObservation(changed)])

        result = self.service.recover_uncertain("r1")

        self.assertEqual(result["state"], "failed_manual")
        self.assertEqual(result["error"]["code"], "target_changed")
        self.assertNotIn("r1", self.store.uncertain_request_ids("reader-1"))

    def test_uncertain_recovery_auth_and_configuration_errors_remain_resumable(self):
        for index, code in enumerate((
            AdapterErrorCode.AUTHENTICATION_ERROR,
            AdapterErrorCode.CONFIGURATION_ERROR,
        )):
            request_id = f"recover-boundary-{index}"
            store = OperationStore(
                os.path.join(self.tempdir.name, f"recover-boundary-{index}.sqlite3")
            )
            driver = FakeDriver(
                capabilities=capabilities(),
                inventory_snapshots=[
                    [TagObservation(self.target)], [TagObservation(self.target)]
                ],
                user_memory={self.target: b"\x00" * 24},
                scripted_errors={
                    "inventory": [None, None, AdapterError(code, "secret=value")],
                    "read_memory": [
                        None,
                        AdapterError(AdapterErrorCode.TIMEOUT, "unsafe timeout"),
                    ],
                },
                write_modes=["no_apply_then_timeout"],
            )
            service = RfidService(
                store,
                {"reader-1": driver},
                owner_id="worker-a",
                capabilities={"reader-1": capabilities()},
            )
            try:
                service.submit_operation(request(request_id))
                first = service.process_operation("reader-1")
                self.assertEqual(first["error"]["code"], "write_uncertain")

                result = service.recover_uncertain(request_id)

                self.assertEqual(result["state"], "failed_retryable")
                self.assertEqual(result["error"]["code"], "write_uncertain")
                self.assertNotIn("secret", repr(result))
                self.assertIn(request_id, store.uncertain_request_ids("reader-1"))
            finally:
                service.close()
                store.close()

    def test_call_driver_after_close_never_renews_or_calls_driver(self):
        self.store.acquire_lease("reader-1", "worker-a", 30)
        renew_calls = []
        original_renew = self.store.renew_lease

        def record_renew(*args, **kwargs):
            renew_calls.append((args, kwargs))
            return original_renew(*args, **kwargs)

        self.store.renew_lease = record_renew
        driver_calls = []
        self.service.close()

        with self.assertRaises(StoreError) as caught:
            self.service.call_driver(
                "reader-1", lambda: driver_calls.append("called")
            )

        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(renew_calls, [])
        self.assertEqual(driver_calls, [])

    def test_close_cannot_return_before_entered_driver_call_starts(self):
        self.store.acquire_lease("reader-1", "worker-a", 30)
        start_entered = threading.Event()
        release_start = threading.Event()
        hardware_started = threading.Event()
        outcome = []
        original_start = threading.Thread.start

        def paused_start(thread):
            if thread.name == "xq-rfid-lease-reader-1":
                start_entered.set()
                release_start.wait(1)
            return original_start(thread)

        with mock.patch.object(threading.Thread, "start", paused_start):
            caller = threading.Thread(
                target=lambda: self._capture_call_driver(
                    outcome, hardware_started.set
                )
            )
            caller.start()
            self.assertTrue(start_entered.wait(1))
            closer = threading.Thread(target=self.service.close)
            closer.start()
            closer.join(1)
            self.assertFalse(closer.is_alive())
            self.assertFalse(hardware_started.is_set())
            release_start.set()
            caller.join(1)
            closer.join(1)

        self.assertFalse(caller.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertFalse(hardware_started.is_set())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")

    def test_inflight_driver_return_after_close_cannot_renew_again(self):
        self.store.acquire_lease("reader-1", "worker-a", 30)
        entered = threading.Event()
        release = threading.Event()
        outcome = []
        renew_calls = []
        original_renew = self.store.renew_lease

        def record_renew(*args, **kwargs):
            renew_calls.append((args, kwargs))
            return original_renew(*args, **kwargs)

        def blocked_driver_call():
            entered.set()
            release.wait(1)
            return "unsafe-success"

        self.store.renew_lease = record_renew
        caller = threading.Thread(
            target=lambda: self._capture_call_driver(
                outcome, blocked_driver_call
            )
        )
        caller.start()
        self.assertTrue(entered.wait(1))
        renew_count_at_close = len(renew_calls)

        self.service.close()
        release.set()
        caller.join(1)

        self.assertFalse(caller.is_alive())
        self.assertEqual(len(renew_calls), renew_count_at_close)
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")

    def _capture_call_driver(self, outcome, method):
        try:
            outcome.append(self.service.call_driver("reader-1", method))
        except BaseException as error:
            outcome.append(error)

    def test_final_success_transition_paused_across_close_is_rejected(self):
        reached_success = threading.Event()
        release_success = threading.Event()
        outcome = []
        original_succeed = self.service._succeed

        def paused_succeed(work, target):
            reached_success.set()
            release_success.wait(1)
            return original_succeed(work, target)

        self.service._succeed = paused_succeed
        self.service.submit_operation(request("close-before-success"))
        worker = threading.Thread(
            target=lambda: self._capture_process_operation(outcome)
        )
        worker.start()
        self.assertTrue(reached_success.wait(1))

        self.service.close()
        release_success.set()
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], StoreError)
        self.assertEqual(outcome[0].code, "lease_conflict")
        self.assertEqual(
            self.store.get("close-before-success")["state"], "verifying"
        )

    def _capture_process_operation(self, outcome):
        try:
            outcome.append(self.service.process_operation("reader-1"))
        except BaseException as error:
            outcome.append(error)

    def test_service_close_is_idempotent_and_does_not_close_driver(self):
        self.service.close()
        self.service.close()
        self.assertEqual(self.driver.call_counts.get("close", 0), 0)

    def test_identity_hash_matches_locked_binary_format(self):
        epc = bytes.fromhex(self.target.epc)
        tid = bytes.fromhex(self.target.tid)
        expected = hashlib.sha256(
            b"XQ-RFID-TARGET-v1\0"
            + len(epc).to_bytes(2, "big") + epc
            + len(tid).to_bytes(2, "big") + tid
        ).hexdigest()
        self.assertEqual(identity_hash(self.target), expected)


if __name__ == "__main__":
    unittest.main()
