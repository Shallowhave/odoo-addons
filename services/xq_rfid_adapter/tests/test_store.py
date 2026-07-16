import json
import math
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from xq_rfid_adapter.store import OperationStore, SCHEMA_VERSION, StoreError


PAYLOAD_A = "01" * 24
PAYLOAD_B = "02" * 24


def sample_request(request_id="r1", **changes):
    value = {
        "request_id": request_id,
        "device_id": "reader-1",
        "operation_type": "write_and_verify",
        "payload_hex": PAYLOAD_A,
        "payload_version": 1,
    }
    value.update(changes)
    return value


def safe_error(code="connection_error"):
    messages = {
        "connection_error": "device connection failed",
        "write_uncertain": "write outcome is uncertain",
        "verification_failed": "write verification failed",
    }
    return {
        "code": code,
        "message": messages[code],
        "device_code": None,
        "retryable": code in {"connection_error", "write_uncertain"},
    }


def safe_result():
    return {
        "identity_hash": "a" * 64,
        "verification_ok": True,
    }


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tempdir.name, "operations.sqlite3")
        self.store = OperationStore(self.path)

    def tearDown(self):
        self.store.close()
        self.tempdir.cleanup()


class TestSchemaAndSafety(StoreCase):
    def test_fresh_schema_has_version_pragmas_constraints_and_indexes(self):
        self.assertEqual(self.store.schema_version, SCHEMA_VERSION)
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            operation_indexes = {
                row[1] for row in connection.execute("PRAGMA index_list(operations)")
            }
            lease_indexes = {
                row[1] for row in connection.execute("PRAGMA index_list(device_leases)")
            }
            self.assertIn("operations_claim_idx", operation_indexes)
            self.assertIn("operations_recovery_idx", operation_indexes)
            self.assertIn("device_leases_expiry_idx", lease_indexes)
            sql = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'operations'"
            ).fetchone()[0]
            self.assertIn("UNIQUE", sql.upper())
            self.assertIn("CHECK", sql.upper())
        finally:
            connection.close()
        reopened = OperationStore(self.path)
        reopened.close()

    def test_newer_schema_is_rejected_without_mutation(self):
        self.store.close()
        connection = sqlite3.connect(self.path)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        connection.close()
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION + 1,
            )
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            connection.close()

    def test_newer_delete_mode_database_is_rejected_without_journal_mutation(self):
        self.store.close()
        os.remove(self.path)
        connection = sqlite3.connect(self.path)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        connection.close()
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

    def test_schema_creation_rolls_back_on_mid_migration_failure(self):
        self.store.close()
        os.remove(self.path)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE device_leases(existing INTEGER)")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError):
            OperationStore(self.path)
        connection = sqlite3.connect(self.path)
        try:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertNotIn("operations", names)
            self.assertIn("device_leases", names)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

    def test_current_version_without_required_schema_is_rejected(self):
        self.store.close()
        os.remove(self.path)
        connection = sqlite3.connect(self.path)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.close()
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")

    def test_malformed_current_version_schema_is_rejected(self):
        self.store.close()
        os.remove(self.path)
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            CREATE TABLE operations(request_id TEXT, device_id TEXT, operation_type TEXT,
                payload BLOB, payload_version INTEGER, request_fingerprint TEXT, state TEXT,
                claim_owner TEXT, lease_until INTEGER);
            CREATE TABLE device_leases(existing INTEGER);
            CREATE TABLE operations_claim_idx(fake INTEGER);
            CREATE TABLE operations_recovery_idx(fake INTEGER);
            CREATE TABLE device_leases_expiry_idx(fake INTEGER);
            PRAGMA user_version = 1;
            """
        )
        connection.close()
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")

    def test_tampered_serialized_data_fails_with_safe_store_error(self):
        self.store.create_or_get(sample_request())
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE operations SET result_json=? WHERE request_id='r1'",
            ('{"raw_frame":"secret"}',),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.get("r1")
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertNotIn("secret", str(caught.exception))
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE operations SET result_json=NULL,error_json='[]' WHERE request_id='r1'"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.get("r1")
        self.assertEqual(caught.exception.code, "store_unavailable")
        connection = sqlite3.connect(self.path)
        connection.execute(
            "UPDATE operations SET result_json=?,error_json=?,state='queued' WHERE request_id='r1'",
            (json.dumps(safe_result()), json.dumps(safe_error())),
        )
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.get("r1")
        self.assertEqual(caught.exception.code, "store_unavailable")

    def test_uncreatable_path_has_fixed_safe_error(self):
        secret_path = os.path.join(self.tempdir.name, "missing", "private-name.sqlite3")
        with self.assertRaises(StoreError) as caught:
            OperationStore(secret_path)
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertNotIn(secret_path, str(caught.exception))
        self.assertNotIn("private-name", repr(caught.exception))

    def test_safe_output_omits_payload_hash_secret_and_raw_identity(self):
        operation = self.store.create_or_get(sample_request())
        forbidden = "secret password raw_frame epc tid fingerprint traceback payload_hex payload_hash"
        rendered = repr(operation).lower()
        for word in forbidden.split():
            self.assertNotIn(word, rendered)
        fetched = self.store.get("r1")
        self.assertEqual(fetched, operation)
        self.assertNotIn(PAYLOAD_A, repr(fetched))

    def test_request_schema_and_values_are_strict(self):
        invalid = [
            {},
            {**sample_request(), "secret": "no"},
            sample_request(request_id="bad/id"),
            sample_request(device_id=""),
            sample_request(operation_type="read"),
            sample_request(payload_hex="00" * 23),
            sample_request(payload_hex="GG" * 24),
            sample_request(payload_version=True),
            sample_request(payload_version=0),
            sample_request(payload_version=256),
        ]
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(StoreError) as caught:
                    self.store.create_or_get(request)
                self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(self.store.count(), 0)

    def test_time_identifiers_and_limits_reject_bool_nonfinite_and_out_of_range(self):
        self.store.create_or_get(sample_request())
        for now in (True, math.nan, math.inf, -1, 2**63):
            with self.subTest(now=now):
                with self.assertRaises(StoreError):
                    self.store.claim_next("reader-1", "worker-a", 1, now=now)
        for duration in (True, 0, -1, math.nan, math.inf, 86401):
            with self.subTest(duration=duration):
                with self.assertRaises(StoreError):
                    self.store.claim_next("reader-1", "worker-a", duration, now=10)
        with self.assertRaises(StoreError):
            self.store.claim_next("reader-1", "bad owner", 1, now=10)


class TestIdempotency(StoreCase):
    def test_same_request_is_idempotent_and_reopen_preserves_it(self):
        first = self.store.create_or_get(sample_request("r1"), now=10)
        second = self.store.create_or_get(sample_request("r1"), now=99)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["created_at"], 10)
        self.assertEqual(second["created_at"], 10)
        self.assertEqual(self.store.count(), 1)
        reopened = OperationStore(self.path)
        try:
            self.assertEqual(reopened.get("r1"), first)
        finally:
            reopened.close()

    def test_reused_id_with_any_immutable_difference_conflicts(self):
        self.store.create_or_get(sample_request())
        conflicts = [
            sample_request(device_id="reader-2"),
            sample_request(operation_type="other"),
            sample_request(payload_hex=PAYLOAD_B),
            sample_request(payload_version=2),
        ]
        for request in conflicts:
            with self.subTest(request=request):
                with self.assertRaises(StoreError) as caught:
                    self.store.create_or_get(request)
                self.assertEqual(caught.exception.code, "request_conflict")
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.get("r1")["device_id"], "reader-1")

    def test_concurrent_identical_create_has_one_row(self):
        stores = [OperationStore(self.path) for _ in range(8)]
        barrier = threading.Barrier(len(stores))
        try:
            def create(store):
                barrier.wait()
                return store.create_or_get(sample_request(), now=20)["id"]
            with ThreadPoolExecutor(max_workers=len(stores)) as executor:
                ids = list(executor.map(create, stores))
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(self.store.count(), 1)
        finally:
            for store in stores:
                store.close()

    def test_concurrent_conflict_never_overwrites_winner(self):
        stores = [OperationStore(self.path), OperationStore(self.path)]
        barrier = threading.Barrier(2)
        requests = [sample_request(payload_hex=PAYLOAD_A), sample_request(payload_hex=PAYLOAD_B)]
        def create(pair):
            store, request = pair
            barrier.wait()
            try:
                return ("ok", store.create_or_get(request, now=20))
            except StoreError as error:
                return (error.code, None)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(create, zip(stores, requests)))
            self.assertEqual(sorted(value[0] for value in outcomes), ["ok", "request_conflict"])
            self.assertEqual(self.store.count(), 1)
        finally:
            for store in stores:
                store.close()


class TestTransitions(StoreCase):
    def setUp(self):
        super().setUp()
        self.store.create_or_get(sample_request(), now=10)
        self.store.claim_next("reader-1", "worker-a", 30, now=11)

    def test_all_allowed_paths_and_timestamps(self):
        inventorying = self.store.transition("r1", "claimed", "inventorying", now=12)
        self.assertEqual(inventorying["updated_at"], 12)
        writing = self.store.transition("r1", "inventorying", "writing", now=13)
        verifying = self.store.transition("r1", "writing", "verifying", now=14)
        succeeded = self.store.transition(
            "r1", "verifying", "succeeded", result=safe_result(), now=15
        )
        self.assertEqual(writing["state"], "writing")
        self.assertEqual(verifying["state"], "verifying")
        self.assertEqual(succeeded["state"], "succeeded")
        self.assertEqual(succeeded["completed_at"], 15)
        self.assertEqual(succeeded["result"], safe_result())
        self.assertIsNone(succeeded["claim_owner"])
        self.assertIsNone(succeeded["lease_until"])
        self.assertEqual(succeeded["attempts"], 1)

    def test_generic_transition_cannot_claim_without_atomic_lease(self):
        queued = self.store.create_or_get(sample_request("queued-direct"), now=12)
        self.assertEqual(queued["state"], "queued")
        with self.assertRaises(StoreError) as caught:
            self.store.transition("queued-direct", "queued", "claimed", now=13)
        self.assertEqual(caught.exception.code, "illegal_transition")
        self.assertEqual(self.store.get("queued-direct")["state"], "queued")

    def test_direct_success_from_writing_is_allowed(self):
        self.store.transition("r1", "claimed", "inventorying", now=12)
        self.store.transition("r1", "inventorying", "writing", now=13)
        result = self.store.transition(
            "r1", "writing", "succeeded", result=safe_result(), now=14
        )
        self.assertEqual(result["state"], "succeeded")

    def test_failure_and_cancel_edges_are_allowed(self):
        for expected in ("claimed", "inventorying"):
            with self.subTest(expected=expected):
                request_id = "r-" + expected
                self.store.create_or_get(sample_request(request_id), now=20)
                self.store.claim_next("reader-1", "worker-a", 30, now=21)
                if expected == "inventorying":
                    self.store.transition(request_id, "claimed", "inventorying", now=22)
                failed = self.store.transition(
                    request_id, expected, "failed_retryable", error=safe_error(), now=23
                )
                self.assertEqual(failed["state"], "failed_retryable")
        request_id = "r-cancel"
        self.store.create_or_get(sample_request(request_id), now=30)
        self.store.claim_next("reader-1", "worker-a", 30, now=31)
        cancelled = self.store.transition(request_id, "claimed", "cancelled", now=32)
        self.assertEqual(cancelled["state"], "cancelled")

    def test_illegal_stale_backward_skipped_and_terminal_transitions_do_not_mutate(self):
        other = OperationStore(self.path)
        try:
            before = self.store.get("r1")
            attempts = [
                ("claimed", "writing"),
                ("queued", "claimed"),
                ("claimed", "queued"),
                ("unknown", "failed_manual"),
            ]
            for expected, new in attempts:
                with self.subTest(expected=expected, new=new):
                    with self.assertRaises(StoreError) as caught:
                        other.transition("r1", expected, new, now=20)
                    self.assertIn(caught.exception.code, {"stale_state", "illegal_transition"})
                    self.assertEqual(self.store.get("r1"), before)
            self.store.transition("r1", "claimed", "cancelled", now=21)
            terminal = self.store.get("r1")
            with self.assertRaises(StoreError):
                other.transition("r1", "cancelled", "queued", now=22)
            self.assertEqual(self.store.get("r1"), terminal)
        finally:
            other.close()

    def test_not_found_is_distinct_from_stale(self):
        with self.assertRaises(StoreError) as caught:
            self.store.transition("missing", "claimed", "inventorying", now=12)
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(StoreError) as caught:
            self.store.transition("r1", "inventorying", "writing", now=12)
        self.assertEqual(caught.exception.code, "stale_state")

    def test_result_and_error_schemas_are_state_specific_and_safe(self):
        bad_results = [
            {},
            {**safe_result(), "epc": "secret"},
            {"identity_hash": "not-a-hash", "verification_ok": True},
            {"identity_hash": "a" * 64, "verification_ok": 1},
        ]
        for result in bad_results:
            with self.subTest(result=result):
                with self.assertRaises(StoreError):
                    self.store.transition("r1", "claimed", "inventorying", result=result, now=12)
        bad_errors = [
            "exception text",
            {"code": "anything", "message": "traceback secret", "device_code": None, "retryable": True},
            {**safe_error(), "extra": "secret"},
            {**safe_error(), "retryable": False},
        ]
        for error in bad_errors:
            with self.subTest(error=error):
                with self.assertRaises(StoreError):
                    self.store.transition("r1", "claimed", "failed_retryable", error=error, now=12)
        self.assertEqual(self.store.get("r1")["state"], "claimed")

    def test_repeated_completion_does_not_change_timestamp_or_result(self):
        self.store.transition("r1", "claimed", "inventorying", now=12)
        self.store.transition("r1", "inventorying", "writing", now=13)
        completed = self.store.transition(
            "r1", "writing", "succeeded", result=safe_result(), now=14
        )
        with self.assertRaises(StoreError):
            self.store.transition(
                "r1", "writing", "succeeded", result=safe_result(), now=99
            )
        self.assertEqual(self.store.get("r1"), completed)


class TestClaimsAndLeases(StoreCase):
    def test_deterministic_claim_and_device_isolation(self):
        self.store.create_or_get(sample_request("z-last"), now=11)
        self.store.create_or_get(sample_request("a-first"), now=10)
        self.store.create_or_get(sample_request("other", device_id="reader-2"), now=9)
        claimed = self.store.claim_next("reader-1", "worker-a", 5, now=20)
        self.assertEqual(claimed["request_id"], "a-first")
        self.assertEqual(claimed["attempts"], 1)
        self.assertEqual(claimed["claimed_at"], 20)
        self.assertEqual(claimed["lease_until"], 25)
        self.assertEqual(self.store.get("other")["state"], "queued")
        self.assertEqual(
            self.store.get_lease("reader-1"),
            {"device_id": "reader-1", "owner_id": "worker-a", "lease_until": 25},
        )

    def test_lease_renew_block_takeover_and_release_use_inclusive_expiry(self):
        acquired = self.store.acquire_lease("reader-1", "worker-a", 5, now=10)
        self.assertEqual(acquired["lease_until"], 15)
        renewed = self.store.renew_lease("reader-1", "worker-a", 8, now=11)
        self.assertEqual(renewed["lease_until"], 19)
        for now in (18, 19):
            with self.subTest(now=now):
                with self.assertRaises(StoreError) as caught:
                    self.store.acquire_lease("reader-1", "worker-b", 2, now=now)
                self.assertEqual(caught.exception.code, "lease_conflict")
        takeover = self.store.acquire_lease("reader-1", "worker-b", 2, now=20)
        self.assertEqual(takeover["owner_id"], "worker-b")
        self.assertFalse(self.store.release_lease("reader-1", "worker-a"))
        self.assertTrue(self.store.release_lease("reader-1", "worker-b"))
        self.assertIsNone(self.store.get_lease("reader-1"))
        self.assertFalse(self.store.release_lease("reader-1", "worker-b"))

    def test_renew_requires_existing_matching_unexpired_owner(self):
        with self.assertRaises(StoreError) as caught:
            self.store.renew_lease("reader-1", "worker-a", 2, now=10)
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.store.acquire_lease("reader-1", "worker-a", 2, now=10)
        with self.assertRaises(StoreError):
            self.store.renew_lease("reader-1", "worker-b", 2, now=11)
        with self.assertRaises(StoreError):
            self.store.renew_lease("reader-1", "worker-a", 2, now=13)

    def test_same_owner_acquire_renewal_updates_active_operation(self):
        self.store.create_or_get(sample_request(), now=1)
        self.store.claim_next("reader-1", "worker-a", 1, now=10)
        renewed = self.store.acquire_lease("reader-1", "worker-a", 10, now=11)
        self.assertEqual(self.store.get("r1")["lease_until"], renewed["lease_until"])
        self.assertEqual(self.store.recover_expired_claims(now=12), [])

    def test_repeated_claim_renews_all_active_operations(self):
        self.store.create_or_get(sample_request("r1"), now=1)
        self.store.create_or_get(sample_request("r2"), now=2)
        first = self.store.claim_next("reader-1", "worker-a", 1, now=10)
        second = self.store.claim_next("reader-1", "worker-a", 10, now=11)
        self.assertEqual(first["request_id"], "r1")
        self.assertEqual(second["request_id"], "r2")
        self.assertEqual(self.store.get("r1")["lease_until"], 21)
        self.assertEqual(self.store.get("r2")["lease_until"], 21)
        self.assertEqual(self.store.recover_expired_claims(now=12), [])

    def test_expired_takeover_recovers_previous_active_operation_atomically(self):
        self.store.create_or_get(sample_request("old"), now=1)
        self.store.claim_next("reader-1", "worker-a", 1, now=10)
        takeover = self.store.acquire_lease("reader-1", "worker-b", 5, now=12)
        old = self.store.get("old")
        self.assertEqual(takeover["owner_id"], "worker-b")
        self.assertEqual(old["state"], "failed_retryable")
        self.assertEqual(old["error"]["code"], "connection_error")
        self.assertIsNone(old["claim_owner"])
        with self.assertRaises(StoreError):
            self.store.transition("old", "claimed", "inventorying", now=13)

    def test_renew_updates_active_operation_lease_atomically(self):
        self.store.create_or_get(sample_request(), now=1)
        self.store.claim_next("reader-1", "worker-a", 1, now=10)
        renewed = self.store.renew_lease("reader-1", "worker-a", 10, now=11)
        operation = self.store.get("r1")
        self.assertEqual(operation["lease_until"], renewed["lease_until"])
        self.assertEqual(self.store.recover_expired_claims(now=12), [])
        self.assertEqual(self.store.get("r1")["state"], "claimed")

    def test_release_is_blocked_while_owner_has_active_operation(self):
        self.store.create_or_get(sample_request(), now=1)
        self.store.claim_next("reader-1", "worker-a", 5, now=10)
        with self.assertRaises(StoreError) as caught:
            self.store.release_lease("reader-1", "worker-a")
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(self.store.get_lease("reader-1")["owner_id"], "worker-a")
        with self.assertRaises(StoreError):
            self.store.acquire_lease("reader-1", "worker-b", 5, now=11)

    def test_claim_requires_or_establishes_matching_persisted_lease(self):
        self.store.create_or_get(sample_request(), now=1)
        self.store.acquire_lease("reader-1", "worker-a", 5, now=10)
        with self.assertRaises(StoreError) as caught:
            self.store.claim_next("reader-1", "worker-b", 5, now=11)
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(self.store.get("r1")["state"], "queued")
        claimed = self.store.claim_next("reader-1", "worker-a", 5, now=11)
        self.assertEqual(claimed["claim_owner"], "worker-a")
        self.assertEqual(self.store.get_lease("reader-1")["owner_id"], "worker-a")

    def test_no_queued_work_does_not_leave_new_lease(self):
        self.assertIsNone(self.store.claim_next("reader-1", "worker-a", 5, now=10))
        self.assertIsNone(self.store.get_lease("reader-1"))

    def test_two_stores_racing_claim_get_at_most_one_operation(self):
        self.store.create_or_get(sample_request(), now=1)
        stores = [OperationStore(self.path), OperationStore(self.path)]
        barrier = threading.Barrier(2)
        def claim(pair):
            store, owner = pair
            barrier.wait()
            try:
                result = store.claim_next("reader-1", owner, 5, now=10)
                return None if result is None else result["request_id"]
            except StoreError as error:
                return error.code
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(claim, zip(stores, ["worker-a", "worker-b"])))
            self.assertEqual(outcomes.count("r1"), 1)
            self.assertEqual(self.store.get("r1")["state"], "claimed")
            operation = self.store.get("r1")
            lease = self.store.get_lease("reader-1")
            self.assertEqual(operation["claim_owner"], lease["owner_id"])
            self.assertEqual(operation["lease_until"], lease["lease_until"])
        finally:
            for store in stores:
                store.close()


class TestRecovery(StoreCase):
    def make_state(self, request_id, state, claimed_at):
        self.store.create_or_get(sample_request(request_id), now=claimed_at - 2)
        self.store.claim_next("reader-1", "worker-a", 1, now=claimed_at)
        if state != "claimed":
            self.store.transition(request_id, "claimed", "inventorying", now=claimed_at)
        if state in {"writing", "verifying"}:
            self.store.transition(request_id, "inventorying", "writing", now=claimed_at)
        if state == "verifying":
            self.store.transition(request_id, "writing", "verifying", now=claimed_at)

    def test_recovery_codes_depend_on_write_boundary(self):
        for index, state in enumerate(("claimed", "inventorying", "writing", "verifying")):
            with self.subTest(state=state):
                request_id = f"r-{index}"
                self.make_state(request_id, state, 10 + index * 10)
        recovered = self.store.recover_expired_claims(now=100)
        self.assertEqual([item["request_id"] for item in recovered], ["r-0", "r-1", "r-2", "r-3"])
        for item in recovered:
            expected = "connection_error" if item["request_id"] in {"r-0", "r-1"} else "write_uncertain"
            self.assertEqual(item["state"], "failed_retryable")
            self.assertEqual(item["error"]["code"], expected)
            self.assertIsNone(item["claim_owner"])
            self.assertIsNone(item["lease_until"])

    def test_inclusive_boundary_active_and_terminal_are_untouched(self):
        self.make_state("boundary", "claimed", 10)
        self.assertEqual(self.store.recover_expired_claims(now=11), [])
        self.assertEqual(self.store.get("boundary")["state"], "claimed")
        recovered = self.store.recover_expired_claims(now=12)
        self.assertEqual([item["request_id"] for item in recovered], ["boundary"])
        self.assertEqual(self.store.recover_expired_claims(now=13), [])

    def test_recovery_is_bounded_and_deterministically_ordered(self):
        for request_id in ("c", "a", "b"):
            self.make_state(request_id, "claimed", 10)
        first = self.store.recover_expired_claims(now=12, batch_limit=2)
        self.assertEqual([item["request_id"] for item in first], ["c", "a"])
        self.assertIsNotNone(self.store.get_lease("reader-1"))
        remaining = self.store.get("b")
        self.assertEqual(remaining["claim_owner"], self.store.get_lease("reader-1")["owner_id"])
        second = self.store.recover_expired_claims(now=12, batch_limit=2)
        self.assertEqual([item["request_id"] for item in second], ["b"])
        self.assertIsNone(self.store.get_lease("reader-1"))
        with self.assertRaises(StoreError):
            self.store.recover_expired_claims(now=12, batch_limit=0)

    def test_restart_and_concurrent_recovery_have_no_duplicates(self):
        self.make_state("restart", "verifying", 100)
        self.store.close()
        stores = [OperationStore(self.path), OperationStore(self.path)]
        barrier = threading.Barrier(2)
        def recover(store):
            barrier.wait()
            return store.recover_expired_claims(now=102)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(recover, stores))
            recovered = [item for batch in outcomes for item in batch]
            self.assertEqual([item["request_id"] for item in recovered], ["restart"])
            self.assertEqual(stores[0].get("restart")["error"]["code"], "write_uncertain")
        finally:
            for store in stores:
                store.close()


class TestResourcesAndContention(StoreCase):
    def test_close_is_idempotent_and_methods_use_independent_connections(self):
        for index in range(100):
            request_id = f"r-{index}"
            self.store.create_or_get(sample_request(request_id), now=index)
            self.assertIsNotNone(self.store.get(request_id))
        self.store.close()
        self.store.close()
        self.assertEqual(self.store.count(), 100)

    def test_busy_contention_is_bounded_and_safe(self):
        other = OperationStore(self.path, busy_timeout_ms=25)
        lock = sqlite3.connect(self.path, isolation_level=None)
        lock.execute("PRAGMA busy_timeout = 25")
        lock.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(StoreError) as caught:
                other.create_or_get(sample_request(), now=10)
            self.assertEqual(caught.exception.code, "store_busy")
            self.assertNotIn("locked", str(caught.exception).lower())
            self.assertNotIn(self.path, str(caught.exception))
        finally:
            lock.rollback()
            lock.close()
            other.close()

    def test_query_plans_use_claim_and_recovery_indexes(self):
        connection = sqlite3.connect(self.path)
        try:
            claim = connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM operations "
                "WHERE device_id=? AND state='queued' ORDER BY created_at,id LIMIT 1",
                ("reader-1",),
            ).fetchall()
            recovery = connection.execute(
                "EXPLAIN QUERY PLAN SELECT id FROM operations "
                "WHERE state IN ('claimed','inventorying','writing','verifying') "
                "AND lease_until < ? ORDER BY lease_until,created_at,id LIMIT ?",
                (10, 100),
            ).fetchall()
            self.assertIn("operations_claim_idx", repr(claim))
            self.assertIn("operations_recovery_idx", repr(recovery))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
