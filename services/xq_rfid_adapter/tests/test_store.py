import json
import math
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from xq_rfid_adapter.domain import AdapterErrorCode
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
        AdapterErrorCode.CONNECTION_ERROR.value: "device connection failed",
        AdapterErrorCode.WRITE_UNCERTAIN.value: "write outcome is uncertain",
        AdapterErrorCode.VERIFICATION_FAILED.value: "write verification failed",
    }
    return {
        "code": code,
        "message": messages[code],
        "device_code": None,
        "retryable": code == AdapterErrorCode.CONNECTION_ERROR.value,
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

    def test_version_zero_nonempty_database_is_rejected_without_mutation(self):
        self.store.close()
        os.remove(self.path)
        connection = sqlite3.connect(self.path)
        connection.execute("CREATE TABLE attacker_data(value TEXT)")
        connection.commit()
        before = list(connection.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ))
        connection.close()
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(list(connection.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
            )), before)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
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

    def test_schema_signature_and_active_device_index_are_canonical(self):
        connection = sqlite3.connect(self.path)
        try:
            meta = dict(connection.execute("SELECT key, value FROM adapter_meta"))
            self.assertRegex(meta["schema_signature"], r"\A[a-f0-9]{64}\Z")
            index_rows = {
                row[1]: row for row in connection.execute("PRAGMA index_list(operations)")
            }
            active = index_rows["operations_one_active_device_idx"]
            self.assertEqual(active[2], 1)
            self.assertEqual(active[4], 1)
            self.assertEqual(
                [row[2] for row in connection.execute(
                    "PRAGMA index_info(operations_one_active_device_idx)"
                )],
                ["device_id"],
            )
            recovery = index_rows["operations_recovery_idx"]
            self.assertEqual(recovery[2], 0)
            self.assertEqual(recovery[4], 1)
            self.assertEqual(
                [row[2] for row in connection.execute(
                    "PRAGMA index_info(operations_recovery_idx)"
                )],
                ["lease_until", "created_at", "id"],
            )
            recovery_sql = connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND name='operations_recovery_idx'"
            ).fetchone()[0]
            self.assertIn(
                "WHERE state IN ('claimed','inventorying','writing','verifying')",
                recovery_sql,
            )
        finally:
            connection.close()

    def test_signature_or_schema_sql_tampering_is_rejected_without_mutation(self):
        self.store.close()
        cases = {
            "signature": (
                "UPDATE adapter_meta SET value='0' WHERE key='schema_signature'",
                None,
            ),
            "partial_predicate": (
                "DROP INDEX operations_one_active_device_idx",
                "CREATE UNIQUE INDEX operations_one_active_device_idx ON operations(device_id) "
                "WHERE state IN ('claimed','inventorying','writing')",
            ),
            "index_order": (
                "DROP INDEX operations_claim_idx",
                "CREATE INDEX operations_claim_idx ON operations(state,device_id,created_at,id)",
            ),
            "old_recovery_index": (
                "DROP INDEX operations_recovery_idx",
                "CREATE INDEX operations_recovery_idx "
                "ON operations(state,lease_until,created_at,id)",
            ),
        }
        pristine = self.path + ".pristine"
        shutil.copyfile(self.path, pristine)
        for name, statements in cases.items():
            with self.subTest(name=name):
                shutil.copyfile(pristine, self.path)
                connection = sqlite3.connect(self.path)
                before_version = connection.execute("PRAGMA user_version").fetchone()[0]
                for statement in statements:
                    if statement is not None:
                        connection.execute(statement)
                connection.commit()
                before_sql = list(connection.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ))
                connection.close()
                with self.assertRaises(StoreError) as caught:
                    OperationStore(self.path)
                self.assertEqual(caught.exception.code, "store_schema")
                connection = sqlite3.connect(self.path)
                try:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], before_version)
                    self.assertEqual(list(connection.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                    )), before_sql)
                finally:
                    connection.close()

    def test_table_constraints_are_validated_exactly(self):
        self.store.close()
        connection = sqlite3.connect(self.path)
        operations_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='operations'"
        ).fetchone()[0]
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='operations'",
            (operations_sql.replace("attempts >= 0", "attempts >= -1"),),
        )
        connection.execute("PRAGMA writable_schema=OFF")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")

    def test_schema_rejects_semantic_literal_changes_and_extra_user_objects(self):
        self.store.close()
        pristine = self.path + ".schema-pristine"
        shutil.copyfile(self.path, pristine)
        for name, tamper in (
            (
                "literal_case",
                lambda connection: connection.execute(
                    "UPDATE sqlite_master SET sql=replace(sql, "
                    "\"'write_and_verify'\", \"'WRITE_AND_VERIFY'\") "
                    "WHERE type='table' AND name='operations'"
                ),
            ),
            (
                "trigger",
                lambda connection: connection.execute(
                    "CREATE TRIGGER deny_operations BEFORE INSERT ON operations "
                    "BEGIN SELECT RAISE(ABORT, 'denied'); END"
                ),
            ),
        ):
            with self.subTest(name=name):
                shutil.copyfile(pristine, self.path)
                connection = sqlite3.connect(self.path)
                if name == "literal_case":
                    connection.execute("PRAGMA writable_schema=ON")
                tamper(connection)
                if name == "literal_case":
                    connection.execute("PRAGMA writable_schema=OFF")
                connection.commit()
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

    def test_tampered_row_scalar_and_state_invariants_fail_closed(self):
        self.store.create_or_get(sample_request())
        tampering = {
            "invented_state": "state='invented'",
            "negative_attempts": "attempts=-1",
            "queued_attempts": "attempts=1",
            "negative_time": "created_at=-1",
            "reversed_time": "updated_at=created_at-1",
            "queued_claim_timestamp": "claimed_at=created_at",
            "bad_hash": "payload_hash='not-a-hash'",
            "valid_but_wrong_fingerprint": "request_fingerprint='0' || substr(request_fingerprint,2)",
            "bad_payload": "payload=zeroblob(23)",
        }
        pristine = self.path + ".row-pristine"
        shutil.copyfile(self.path, pristine)
        for name, assignment in tampering.items():
            with self.subTest(name=name):
                shutil.copyfile(pristine, self.path)
                connection = sqlite3.connect(self.path)
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(f"UPDATE operations SET {assignment} WHERE request_id='r1'")
                connection.commit()
                connection.close()
                with self.assertRaises(StoreError) as caught:
                    self.store.get("r1")
                self.assertEqual(caught.exception.code, "store_unavailable")

    def test_tampered_terminal_history_and_lease_rows_fail_closed(self):
        self.store.create_or_get(sample_request(), now=1)
        self.store.claim_next("reader-1", "worker-a", 10, now=2)
        self.store.transition("r1", "worker-a", "claimed", "cancelled", now=3)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE operations SET claimed_at=NULL,attempts=0 WHERE request_id='r1'"
        )
        connection.execute(
            "UPDATE device_leases SET owner_id='bad owner',lease_until=-1 "
            "WHERE device_id='reader-1'"
        )
        connection.commit()
        connection.close()
        for call in (lambda: self.store.get("r1"), lambda: self.store.get_lease("reader-1")):
            with self.subTest(call=call):
                with self.assertRaises(StoreError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "store_unavailable")

    def test_uncreatable_path_has_fixed_safe_error(self):
        secret_path = os.path.join(self.tempdir.name, "missing", "private-name.sqlite3")
        with self.assertRaises(StoreError) as caught:
            OperationStore(secret_path)
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertNotIn(secret_path, str(caught.exception))
        self.assertNotIn("private-name", repr(caught.exception))

    def test_persistent_store_rejects_memory_database(self):
        for path in (":memory:", Path(":memory:")):
            with self.subTest(path=path):
                with self.assertRaises(StoreError) as caught:
                    OperationStore(path)
                self.assertEqual(caught.exception.code, "invalid_argument")

    def test_initialize_requires_wal_pragma_result(self):
        real_connect = sqlite3.connect

        class NonWalConnection(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "journal_mode" in sql.lower() and "wal" in sql.lower():
                    return super().execute("PRAGMA journal_mode")
                return super().execute(sql, parameters)

        self.store.close()
        connection = real_connect(self.path)
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.close()
        with mock.patch(
            "xq_rfid_adapter.store.sqlite3.connect",
            side_effect=lambda *args, **kwargs: real_connect(
                *args, factory=NonWalConnection, **kwargs
            ),
        ):
            with self.assertRaises(StoreError) as caught:
                OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_unavailable")
        connection = real_connect(self.path)
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        finally:
            connection.close()

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
        inventorying = self.store.transition("r1", "worker-a", "claimed", "inventorying", now=12)
        self.assertEqual(inventorying["updated_at"], 12)
        writing = self.store.transition("r1", "worker-a", "inventorying", "writing", now=13)
        verifying = self.store.transition("r1", "worker-a", "writing", "verifying", now=14)
        succeeded = self.store.transition(
            "r1", "worker-a", "verifying", "succeeded",
            result=safe_result(), now=15
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
            self.store.transition(
                "queued-direct", "worker-a", "queued", "claimed", now=13
            )
        self.assertEqual(caught.exception.code, "illegal_transition")
        self.assertEqual(self.store.get("queued-direct")["state"], "queued")

    def test_direct_success_from_writing_is_allowed(self):
        self.store.transition("r1", "worker-a", "claimed", "inventorying", now=12)
        self.store.transition("r1", "worker-a", "inventorying", "writing", now=13)
        result = self.store.transition(
            "r1", "worker-a", "writing", "succeeded",
            result=safe_result(), now=14
        )
        self.assertEqual(result["state"], "succeeded")

    def test_failure_and_cancel_edges_are_allowed(self):
        for expected in ("claimed", "inventorying"):
            with self.subTest(expected=expected):
                request_id = "r-" + expected
                device_id = "reader-" + expected
                self.store.create_or_get(
                    sample_request(request_id, device_id=device_id), now=20
                )
                self.store.claim_next(device_id, "worker-a", 30, now=21)
                if expected == "inventorying":
                    self.store.transition(request_id, "worker-a", "claimed", "inventorying", now=22)
                failed = self.store.transition(
                    request_id, "worker-a", expected, "failed_retryable",
                    error=safe_error(), now=23
                )
                self.assertEqual(failed["state"], "failed_retryable")
        request_id = "r-cancel"
        self.store.create_or_get(
            sample_request(request_id, device_id="reader-cancel"), now=30
        )
        self.store.claim_next("reader-cancel", "worker-a", 30, now=31)
        cancelled = self.store.transition(request_id, "worker-a", "claimed", "cancelled", now=32)
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
                        other.transition("r1", "worker-a", expected, new, now=20)
                    self.assertIn(caught.exception.code, {"stale_state", "illegal_transition"})
                    self.assertEqual(self.store.get("r1"), before)
            self.store.transition("r1", "worker-a", "claimed", "cancelled", now=21)
            terminal = self.store.get("r1")
            with self.assertRaises(StoreError):
                other.transition("r1", "worker-a", "cancelled", "queued", now=22)
            self.assertEqual(self.store.get("r1"), terminal)
        finally:
            other.close()

    def test_not_found_is_distinct_from_stale(self):
        with self.assertRaises(StoreError) as caught:
            self.store.transition(
                "missing", "worker-a", "claimed", "inventorying", now=12
            )
        self.assertEqual(caught.exception.code, "not_found")
        with self.assertRaises(StoreError) as caught:
            self.store.transition("r1", "worker-a", "inventorying", "writing", now=12)
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
                    self.store.transition("r1", "worker-a", "claimed", "inventorying", result=result, now=12)
        bad_errors = [
            "exception text",
            {"code": "anything", "message": "traceback secret", "device_code": None, "retryable": True},
            {**safe_error(), "extra": "secret"},
            {**safe_error(), "retryable": False},
        ]
        for error in bad_errors:
            with self.subTest(error=error):
                with self.assertRaises(StoreError):
                    self.store.transition("r1", "worker-a", "claimed", "failed_retryable", error=error, now=12)
        self.assertEqual(self.store.get("r1")["state"], "claimed")

    def test_transition_requires_matching_active_device_lease(self):
        connection = sqlite3.connect(self.path)
        connection.execute("DELETE FROM device_leases WHERE device_id='reader-1'")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.transition("r1", "worker-a", "claimed", "inventorying", now=12)
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertEqual(self.store.get("r1")["state"], "claimed")

    def test_transition_requires_current_unexpired_owner(self):
        before = self.store.get("r1")
        with self.assertRaises(StoreError) as caught:
            self.store.transition(
                "r1", "worker-b", "claimed", "inventorying", now=12
            )
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(self.store.get("r1"), before)

        boundary = self.store.transition(
            "r1", "worker-a", "claimed", "inventorying", now=41
        )
        self.assertEqual(boundary["state"], "inventorying")

        self.store.create_or_get(
            sample_request("expired", device_id="reader-expired"), now=10
        )
        self.store.claim_next("reader-expired", "worker-a", 30, now=11)
        before = self.store.get("expired")
        with self.assertRaises(StoreError) as caught:
            self.store.transition(
                "expired", "worker-a", "claimed", "inventorying", now=42
            )
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(self.store.get("expired"), before)

    def test_transition_rejects_invalid_owner_without_mutation(self):
        before = self.store.get("r1")
        for owner in (None, True, "", "bad owner", "x" * 129):
            with self.subTest(owner=owner):
                with self.assertRaises(StoreError) as caught:
                    self.store.transition(
                        "r1", owner, "claimed", "inventorying", now=12
                    )
                self.assertEqual(caught.exception.code, "invalid_argument")
                self.assertEqual(self.store.get("r1"), before)

    def test_two_stores_racing_transition_have_one_winner(self):
        stores = [OperationStore(self.path), OperationStore(self.path)]
        barrier = threading.Barrier(2)

        def transition(store):
            barrier.wait()
            try:
                return store.transition(
                    "r1", "worker-a", "claimed", "inventorying", now=12
                )["state"]
            except StoreError as error:
                return error.code

        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(transition, stores))
            self.assertEqual(sorted(outcomes), ["inventorying", "stale_state"])
        finally:
            for store in stores:
                store.close()

    def test_transition_timestamp_cannot_regress(self):
        self.store.transition("r1", "worker-a", "claimed", "inventorying", now=15)
        with self.assertRaises(StoreError) as caught:
            self.store.transition("r1", "worker-a", "inventorying", "writing", now=12)
        self.assertEqual(caught.exception.code, "invalid_argument")
        self.assertEqual(self.store.get("r1")["updated_at"], 15)

    def test_repeated_completion_does_not_change_timestamp_or_result(self):
        self.store.transition("r1", "worker-a", "claimed", "inventorying", now=12)
        self.store.transition("r1", "worker-a", "inventorying", "writing", now=13)
        completed = self.store.transition(
            "r1", "worker-a", "writing", "succeeded",
            result=safe_result(), now=14
        )
        with self.assertRaises(StoreError):
            self.store.transition(
                "r1", "worker-a", "writing", "succeeded",
                result=safe_result(), now=99
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

    def test_same_owner_idle_acquire_never_shortens_valid_lease(self):
        self.store.acquire_lease("reader-1", "worker-a", 30, now=10)
        acquired = self.store.acquire_lease("reader-1", "worker-a", 5, now=11)
        self.assertEqual(acquired["lease_until"], 40)
        self.assertEqual(self.store.get_lease("reader-1"), acquired)
        boundary = self.store.acquire_lease(
            "reader-1", "worker-a", 1, now=40
        )
        self.assertEqual(boundary["lease_until"], 41)
        self.assertEqual(self.store.get_lease("reader-1"), boundary)

    def test_same_owner_idle_renew_never_shortens_valid_lease(self):
        self.store.acquire_lease("reader-1", "worker-a", 30, now=10)
        renewed = self.store.renew_lease("reader-1", "worker-a", 5, now=11)
        self.assertEqual(renewed["lease_until"], 40)
        self.assertEqual(self.store.get_lease("reader-1"), renewed)

    def test_same_owner_active_acquire_never_shortens_valid_lease(self):
        self.store.create_or_get(sample_request(), now=1)
        claimed = self.store.claim_next("reader-1", "worker-a", 30, now=10)
        acquired = self.store.acquire_lease("reader-1", "worker-a", 5, now=11)
        operation = self.store.get("r1")
        self.assertEqual(acquired["lease_until"], 40)
        self.assertEqual(self.store.get_lease("reader-1"), acquired)
        self.assertEqual(operation["lease_until"], 40)
        self.assertEqual(operation["updated_at"], 11)
        self.assertEqual(claimed["updated_at"], 10)

    def test_same_owner_active_renew_never_shortens_valid_lease(self):
        self.store.create_or_get(sample_request(), now=1)
        claimed = self.store.claim_next("reader-1", "worker-a", 30, now=10)
        renewed = self.store.renew_lease("reader-1", "worker-a", 5, now=11)
        operation = self.store.get("r1")
        self.assertEqual(renewed["lease_until"], 40)
        self.assertEqual(self.store.get_lease("reader-1"), renewed)
        self.assertEqual(operation["lease_until"], 40)
        self.assertEqual(operation["updated_at"], 11)
        self.assertEqual(claimed["updated_at"], 10)

    def test_second_claim_same_owner_leaves_queue_and_lease_unchanged(self):
        self.store.create_or_get(sample_request("r1"), now=1)
        self.store.create_or_get(sample_request("r2"), now=2)
        first = self.store.claim_next("reader-1", "worker-a", 10, now=10)
        lease_before = self.store.get_lease("reader-1")
        self.assertEqual(first["request_id"], "r1")
        with self.assertRaises(StoreError) as caught:
            self.store.claim_next("reader-1", "worker-a", 30, now=11)
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(self.store.get("r2")["state"], "queued")
        self.assertEqual(self.store.get_lease("reader-1"), lease_before)
        self.assertEqual(self.store.get("r1")["lease_until"], lease_before["lease_until"])

    def test_second_claim_other_owner_leaves_queue_and_lease_unchanged(self):
        self.store.create_or_get(sample_request("r1"), now=1)
        self.store.create_or_get(sample_request("r2"), now=2)
        self.store.claim_next("reader-1", "worker-a", 10, now=10)
        lease_before = self.store.get_lease("reader-1")
        with self.assertRaises(StoreError) as caught:
            self.store.claim_next("reader-1", "worker-b", 30, now=11)
        self.assertEqual(caught.exception.code, "lease_conflict")
        self.assertEqual(self.store.get("r2")["state"], "queued")
        self.assertEqual(self.store.get_lease("reader-1"), lease_before)

    def test_partial_unique_index_prevents_two_active_operations_per_device(self):
        self.store.create_or_get(sample_request("r1"), now=1)
        self.store.create_or_get(sample_request("r2"), now=2)
        self.store.claim_next("reader-1", "worker-a", 10, now=10)
        connection = sqlite3.connect(self.path)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE operations SET state='claimed',claim_owner='worker-a',"
                    "lease_until=20,claimed_at=11,attempts=1 WHERE request_id='r2'"
                )
        finally:
            connection.close()
        self.assertEqual(self.store.get("r2")["state"], "queued")

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
            self.store.transition(
                "old", "worker-a", "claimed", "inventorying", now=13
            )

    def test_lease_mutations_reject_missing_malformed_or_expired_claim_leases(self):
        scenarios = {
            "missing": "DELETE FROM device_leases WHERE device_id='reader-1'",
            "malformed": "UPDATE device_leases SET lease_until='x' WHERE device_id='reader-1'",
        }
        pristine = self.path + ".lease-pristine"
        self.store.create_or_get(sample_request("old"), now=1)
        self.store.claim_next("reader-1", "worker-a", 1, now=10)
        shutil.copyfile(self.path, pristine)
        for name, tamper in scenarios.items():
            with self.subTest(name=name):
                shutil.copyfile(pristine, self.path)
                connection = sqlite3.connect(self.path)
                connection.execute("PRAGMA ignore_check_constraints=ON")
                connection.execute(tamper)
                connection.commit()
                connection.close()
                with self.assertRaises(StoreError) as caught:
                    self.store.acquire_lease("reader-1", "worker-b", 5, now=12)
                self.assertEqual(caught.exception.code, "store_unavailable")
        shutil.copyfile(pristine, self.path)
        recovered = self.store.acquire_lease("reader-1", "worker-a", 5, now=12)
        self.assertEqual(recovered["owner_id"], "worker-a")
        self.assertEqual(self.store.get("old")["state"], "failed_retryable")

    def test_lease_renewal_rejects_time_before_active_history(self):
        self.store.create_or_get(sample_request("old"), now=1)
        self.store.claim_next("reader-1", "worker-a", 30, now=10)
        self.store.transition(
            "old", "worker-a", "claimed", "inventorying", now=20
        )
        for call in (
            lambda: self.store.renew_lease("reader-1", "worker-a", 5, now=15),
            lambda: self.store.acquire_lease("reader-1", "worker-a", 5, now=15),
        ):
            with self.subTest(call=call):
                with self.assertRaises(StoreError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "invalid_argument")
        self.assertEqual(self.store.get("old")["updated_at"], 20)

    def test_default_clock_is_sampled_after_claim_lock_acquisition(self):
        self.store.create_or_get(sample_request("old"))
        other = OperationStore(self.path, busy_timeout_ms=3000)
        lock = sqlite3.connect(self.path, isolation_level=None)
        lock.execute("BEGIN IMMEDIATE")
        started = threading.Event()
        outcome = {}

        def claim():
            started.set()
            outcome["value"] = other.claim_next("reader-1", "worker-a", 1)

        thread = threading.Thread(target=claim)
        thread.start()
        started.wait()
        time.sleep(1.1)
        lock.rollback()
        lock.close()
        thread.join()
        try:
            claimed = outcome["value"]
            self.assertGreaterEqual(claimed["lease_until"], int(time.time()))
        finally:
            other.close()

    def test_takeover_rejects_active_owner_mismatched_with_expired_lease(self):
        self.store.create_or_get(sample_request("old"), now=1)
        self.store.claim_next("reader-1", "worker-a", 1, now=10)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE operations SET claim_owner='worker-x' WHERE request_id='old'"
        )
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.acquire_lease("reader-1", "worker-b", 5, now=12)
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertEqual(self.store.get_lease("reader-1")["owner_id"], "worker-a")

    def test_missing_active_index_and_duplicate_active_rows_fail_closed_quickly(self):
        self.store.create_or_get(sample_request("old"), now=1)
        self.store.create_or_get(sample_request("queued"), now=2)
        self.store.claim_next("reader-1", "worker-a", 1, now=10)
        connection = sqlite3.connect(self.path)
        connection.execute("DROP INDEX operations_one_active_device_idx")
        connection.execute(
            "UPDATE operations SET state='claimed',claim_owner='worker-a',lease_until=11,"
            "claimed_at=10,attempts=1 WHERE request_id='queued'"
        )
        connection.commit()
        connection.close()
        for call in (
            lambda: self.store.acquire_lease("reader-1", "worker-a", 5, now=12),
            lambda: self.store.claim_next("reader-1", "worker-b", 5, now=12),
        ):
            with self.subTest(call=call):
                with self.assertRaises(StoreError) as caught:
                    call()
                self.assertEqual(caught.exception.code, "store_schema")
        with self.assertRaises(StoreError) as caught:
            OperationStore(self.path)
        self.assertEqual(caught.exception.code, "store_schema")

    def test_active_zero_attempts_fail_closed(self):
        self.store.create_or_get(sample_request("old"), now=1)
        self.store.claim_next("reader-1", "worker-a", 5, now=10)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE operations SET attempts=0 WHERE request_id='old'")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.get("old")
        self.assertEqual(caught.exception.code, "store_unavailable")

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

    def test_release_rejects_mismatched_active_owner_without_deleting_lease(self):
        self.store.create_or_get(sample_request(), now=1)
        self.store.claim_next("reader-1", "worker-a", 5, now=10)
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE operations SET claim_owner='worker-x' WHERE request_id='r1'")
        connection.commit()
        connection.close()
        with self.assertRaises(StoreError) as caught:
            self.store.release_lease("reader-1", "worker-a")
        self.assertEqual(caught.exception.code, "store_unavailable")
        self.assertEqual(self.store.get_lease("reader-1")["owner_id"], "worker-a")

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
        device_id = f"reader-{request_id}"
        self.store.create_or_get(
            sample_request(request_id, device_id=device_id), now=claimed_at - 2
        )
        self.store.claim_next(device_id, "worker-a", 1, now=claimed_at)
        if state != "claimed":
            self.store.transition(request_id, "worker-a", "claimed", "inventorying", now=claimed_at)
        if state in {"writing", "verifying"}:
            self.store.transition(request_id, "worker-a", "inventorying", "writing", now=claimed_at)
        if state == "verifying":
            self.store.transition(request_id, "worker-a", "writing", "verifying", now=claimed_at)

    def test_recovery_codes_depend_on_write_boundary(self):
        for index, state in enumerate(("claimed", "inventorying", "writing", "verifying")):
            with self.subTest(state=state):
                request_id = f"r-{index}"
                self.make_state(request_id, state, 10 + index * 10)
        recovered = self.store.recover_expired_claims(now=100)
        self.assertEqual([item["request_id"] for item in recovered], ["r-0", "r-1", "r-2", "r-3"])
        for item in recovered:
            pre_write = item["request_id"] in {"r-0", "r-1"}
            expected = (
                AdapterErrorCode.CONNECTION_ERROR.value
                if pre_write else AdapterErrorCode.WRITE_UNCERTAIN.value
            )
            self.assertEqual(item["state"], "failed_retryable")
            self.assertEqual(item["error"]["code"], expected)
            self.assertIs(item["error"]["retryable"], pre_write)
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
        self.assertIsNotNone(self.store.get_lease("reader-b"))
        remaining = self.store.get("b")
        self.assertEqual(remaining["claim_owner"], self.store.get_lease("reader-b")["owner_id"])
        second = self.store.recover_expired_claims(now=12, batch_limit=2)
        self.assertEqual([item["request_id"] for item in second], ["b"])
        self.assertIsNone(self.store.get_lease("reader-b"))
        with self.assertRaises(StoreError):
            self.store.recover_expired_claims(now=12, batch_limit=0)

    def test_orphan_lease_cleanup_is_bounded_by_batch_limit(self):
        connection = sqlite3.connect(self.path)
        connection.executemany(
            "INSERT INTO device_leases(device_id,owner_id,lease_until) VALUES (?,?,?)",
            [(f"orphan-{index}", "worker-a", 1) for index in range(5)],
        )
        connection.commit()
        connection.close()
        self.assertEqual(self.store.recover_expired_claims(now=2, batch_limit=1), [])
        connection = sqlite3.connect(self.path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM device_leases WHERE device_id LIKE 'orphan-%'"
                ).fetchone()[0],
                4,
            )
        finally:
            connection.close()

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
            error = stores[0].get("restart")["error"]
            self.assertEqual(
                error["code"], AdapterErrorCode.WRITE_UNCERTAIN.value
            )
            self.assertIs(error["retryable"], False)
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
            self.assertNotIn("TEMP B-TREE", repr(recovery).upper())
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
