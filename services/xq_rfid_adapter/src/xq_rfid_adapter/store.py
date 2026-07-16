"""Persistent idempotent RFID operations, device leases, and crash recovery."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 1
_DEFAULT_BUSY_TIMEOUT_MS = 1000
_MAX_BUSY_TIMEOUT_MS = 60_000
_MAX_LEASE_SECONDS = 86_400
_MAX_TIMESTAMP = 253_402_300_799
_DEFAULT_RECOVERY_BATCH = 100
_MAX_RECOVERY_BATCH = 1000
_MAX_ACTIVE_PER_DEVICE = 1000
_IDENTIFIER_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")
_HASH_RE = re.compile(r"\A[a-f0-9]{64}\Z")
_DEVICE_CODE_RE = re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")
_REQUEST_KEYS = frozenset(
    {"request_id", "device_id", "operation_type", "payload_hex", "payload_version"}
)
_STATES = frozenset(
    {
        "queued",
        "claimed",
        "inventorying",
        "writing",
        "verifying",
        "succeeded",
        "failed_retryable",
        "failed_manual",
        "cancelled",
    }
)
_TERMINAL_STATES = frozenset(
    {"succeeded", "failed_retryable", "failed_manual", "cancelled"}
)
_ALLOWED_TRANSITIONS = {
    "claimed": frozenset(
        {"inventorying", "failed_retryable", "failed_manual", "cancelled"}
    ),
    "inventorying": frozenset(
        {"writing", "failed_retryable", "failed_manual", "cancelled"}
    ),
    "writing": frozenset({"verifying", "succeeded", "failed_manual"}),
    "verifying": frozenset({"succeeded", "failed_manual"}),
}
_RESULT_KEYS = frozenset({"identity_hash", "verification_ok"})
_ERROR_KEYS = frozenset({"code", "message", "device_code", "retryable"})
_SAFE_ERRORS = {
    "connection_error": ("device connection failed", True),
    "timeout": ("device operation timed out", True),
    "protocol_error": ("invalid request", False),
    "device_error": ("internal device error", False),
    "no_tag": ("no tag was found", False),
    "multiple_tags": ("multiple tags were found", False),
    "target_changed": ("tag target changed", False),
    "unsupported_memory": ("memory operation is unsupported", False),
    "capacity_exceeded": ("request body is too large", False),
    "write_uncertain": ("write outcome is uncertain", True),
    "verification_failed": ("write verification failed", False),
}
_SAFE_STORE_MESSAGES = {
    "store_unavailable": "operation store is unavailable",
    "store_busy": "operation store is busy",
    "store_schema": "operation store schema is incompatible",
    "invalid_request": "invalid operation request",
    "request_conflict": "request identifier conflicts with an existing operation",
    "not_found": "operation was not found",
    "stale_state": "operation state changed",
    "illegal_transition": "operation transition is not allowed",
    "invalid_result": "invalid operation result",
    "invalid_error": "invalid operation error",
    "invalid_argument": "invalid store argument",
    "lease_conflict": "device lease is unavailable",
}


class StoreError(Exception):
    """Fixed-code store error that never exposes SQLite or path details."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _SAFE_STORE_MESSAGES:
            code = "store_unavailable"
        self.code = code
        super().__init__(_SAFE_STORE_MESSAGES[code])


class OperationStore:
    """SQLite store using one configured connection per public method."""

    def __init__(self, path: str | Path, *, busy_timeout_ms: int = _DEFAULT_BUSY_TIMEOUT_MS) -> None:
        if isinstance(path, bool) or not isinstance(path, (str, Path)) or not str(path):
            raise StoreError("invalid_argument")
        if (
            type(busy_timeout_ms) is not int
            or not 1 <= busy_timeout_ms <= _MAX_BUSY_TIMEOUT_MS
        ):
            raise StoreError("invalid_argument")
        self._path = str(path)
        self._busy_timeout_ms = busy_timeout_ms
        self._initialize()

    @property
    def schema_version(self) -> int:
        return SCHEMA_VERSION

    def close(self) -> None:
        """Compatibility no-op: method-scoped connections are already closed."""

    def create_or_get(self, request: object, now: int | float | None = None) -> dict:
        request_id = _request_identifier(request)
        timestamp = _timestamp(now)
        try:
            with self._transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM operations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                try:
                    validated = _validate_request(request)
                except StoreError:
                    if existing is not None:
                        raise StoreError("request_conflict") from None
                    raise
                fingerprint = _fingerprint(validated)
                if existing is not None:
                    if existing["request_fingerprint"] != fingerprint:
                        raise StoreError("request_conflict")
                    return _public_operation(existing)
                connection.execute(
                    """
                    INSERT INTO operations (
                        request_id, device_id, operation_type, payload, payload_version,
                        payload_hash, request_fingerprint, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        validated["request_id"],
                        validated["device_id"],
                        validated["operation_type"],
                        validated["payload"],
                        validated["payload_version"],
                        validated["payload_hash"],
                        fingerprint,
                        timestamp,
                        timestamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM operations WHERE request_id = ?",
                    (validated["request_id"],),
                ).fetchone()
                return _public_operation(row)
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def get(self, request_id: object) -> dict | None:
        value = _identifier(request_id, "invalid_argument")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT * FROM operations WHERE request_id = ?", (value,)
                ).fetchone()
                return None if row is None else _public_operation(row)
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def count(self) -> int:
        try:
            with self._connection() as connection:
                return int(connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0])
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def transition(
        self,
        request_id: object,
        expected_state: object,
        new_state: object,
        result: object = None,
        error: object = None,
        now: int | float | None = None,
    ) -> dict:
        request_id = _identifier(request_id, "invalid_argument")
        if expected_state not in _STATES or new_state not in _STATES:
            raise StoreError("illegal_transition")
        if expected_state == "queued" or new_state == "claimed":
            raise StoreError("illegal_transition")
        if new_state not in _ALLOWED_TRANSITIONS.get(expected_state, frozenset()):
            raise StoreError("illegal_transition")
        timestamp = _timestamp(now)
        result_value = _validate_result(result, new_state)
        error_value = _validate_error(error, new_state)
        terminal = new_state in _TERMINAL_STATES
        try:
            with self._transaction() as connection:
                current = connection.execute(
                    "SELECT state FROM operations WHERE request_id = ?", (request_id,)
                ).fetchone()
                if current is None:
                    raise StoreError("not_found")
                if current["state"] != expected_state:
                    raise StoreError("stale_state")
                if terminal:
                    cursor = connection.execute(
                        """
                        UPDATE operations
                        SET state = ?, updated_at = ?, completed_at = ?,
                            result_json = ?, error_json = ?, claim_owner = NULL,
                            lease_until = NULL
                        WHERE request_id = ? AND state = ?
                        """,
                        (
                            new_state,
                            timestamp,
                            timestamp,
                            _json_dump(result_value),
                            _json_dump(error_value),
                            request_id,
                            expected_state,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE operations SET state = ?, updated_at = ?
                        WHERE request_id = ? AND state = ?
                        """,
                        (new_state, timestamp, request_id, expected_state),
                    )
                if cursor.rowcount != 1:
                    raise StoreError("stale_state")
                row = connection.execute(
                    "SELECT * FROM operations WHERE request_id = ?", (request_id,)
                ).fetchone()
                return _public_operation(row)
        except StoreError:
            raise
        except sqlite3.Error as sql_error:
            raise _translate_sqlite_error(sql_error) from None

    def acquire_lease(
        self,
        device_id: object,
        owner_id: object,
        lease_seconds: object,
        now: int | float | None = None,
    ) -> dict:
        device_id = _identifier(device_id, "invalid_argument")
        owner_id = _identifier(owner_id, "invalid_argument")
        timestamp, lease_until = _lease_times(now, lease_seconds)
        try:
            with self._transaction() as connection:
                current = connection.execute(
                    "SELECT owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                takeover = (
                    current is not None
                    and current["owner_id"] != owner_id
                    and current["lease_until"] < timestamp
                )
                if takeover:
                    self._recover_device_operations(
                        connection, device_id, current["owner_id"], timestamp
                    )
                if not self._upsert_lease(
                    connection, device_id, owner_id, timestamp, lease_until
                ):
                    raise StoreError("lease_conflict")
                self._renew_active_operations(
                    connection, device_id, owner_id, lease_until, timestamp
                )
                return _lease_dict(device_id, owner_id, lease_until)
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def renew_lease(
        self,
        device_id: object,
        owner_id: object,
        lease_seconds: object,
        now: int | float | None = None,
    ) -> dict:
        device_id = _identifier(device_id, "invalid_argument")
        owner_id = _identifier(owner_id, "invalid_argument")
        timestamp, lease_until = _lease_times(now, lease_seconds)
        try:
            with self._transaction() as connection:
                cursor = connection.execute(
                    """
                    UPDATE device_leases SET lease_until = ?
                    WHERE device_id = ? AND owner_id = ? AND lease_until >= ?
                    """,
                    (lease_until, device_id, owner_id, timestamp),
                )
                if cursor.rowcount != 1:
                    raise StoreError("lease_conflict")
                self._renew_active_operations(
                    connection, device_id, owner_id, lease_until, timestamp
                )
                return _lease_dict(device_id, owner_id, lease_until)
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def release_lease(self, device_id: object, owner_id: object) -> bool:
        device_id = _identifier(device_id, "invalid_argument")
        owner_id = _identifier(owner_id, "invalid_argument")
        try:
            with self._transaction() as connection:
                active = connection.execute(
                    """
                    SELECT 1 FROM operations
                    WHERE device_id = ? AND claim_owner = ?
                      AND state IN ('claimed','inventorying','writing','verifying')
                    LIMIT 1
                    """,
                    (device_id, owner_id),
                ).fetchone()
                if active is not None:
                    raise StoreError("lease_conflict")
                cursor = connection.execute(
                    "DELETE FROM device_leases WHERE device_id = ? AND owner_id = ?",
                    (device_id, owner_id),
                )
                return cursor.rowcount == 1
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def get_lease(self, device_id: object) -> dict | None:
        device_id = _identifier(device_id, "invalid_argument")
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT device_id, owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                return None if row is None else dict(row)
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def claim_next(
        self,
        device_id: object,
        owner_id: object,
        lease_seconds: object,
        now: int | float | None = None,
    ) -> dict | None:
        device_id = _identifier(device_id, "invalid_argument")
        owner_id = _identifier(owner_id, "invalid_argument")
        timestamp, lease_until = _lease_times(now, lease_seconds)
        try:
            with self._transaction() as connection:
                current = connection.execute(
                    "SELECT owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                if (
                    current is not None
                    and current["owner_id"] != owner_id
                    and current["lease_until"] < timestamp
                ):
                    self._recover_device_operations(
                        connection, device_id, current["owner_id"], timestamp
                    )
                active_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM operations
                    WHERE device_id = ?
                      AND state IN ('claimed','inventorying','writing','verifying')
                    """,
                    (device_id,),
                ).fetchone()[0]
                if active_count >= _MAX_ACTIVE_PER_DEVICE:
                    raise StoreError("lease_conflict")
                candidate = connection.execute(
                    """
                    SELECT id FROM operations
                    WHERE device_id = ? AND state = 'queued'
                    ORDER BY created_at, id LIMIT 1
                    """,
                    (device_id,),
                ).fetchone()
                if candidate is None:
                    return None
                if not self._upsert_lease(
                    connection, device_id, owner_id, timestamp, lease_until
                ):
                    raise StoreError("lease_conflict")
                if active_count:
                    self._renew_active_operations(
                        connection, device_id, owner_id, lease_until, timestamp
                    )
                cursor = connection.execute(
                    """
                    UPDATE operations
                    SET state = 'claimed', claim_owner = ?, lease_until = ?,
                        claimed_at = ?, updated_at = ?, attempts = attempts + 1
                    WHERE id = ? AND state = 'queued'
                    """,
                    (owner_id, lease_until, timestamp, timestamp, candidate["id"]),
                )
                if cursor.rowcount != 1:
                    raise StoreError("stale_state")
                row = connection.execute(
                    "SELECT * FROM operations WHERE id = ?", (candidate["id"],)
                ).fetchone()
                return _public_operation(row)
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    def recover_expired_claims(
        self, now: int | float | None = None, *, batch_limit: int = _DEFAULT_RECOVERY_BATCH
    ) -> list[dict]:
        timestamp = _timestamp(now)
        if type(batch_limit) is not int or not 1 <= batch_limit <= _MAX_RECOVERY_BATCH:
            raise StoreError("invalid_argument")
        recovered: list[dict] = []
        try:
            with self._transaction() as connection:
                candidates = connection.execute(
                    """
                    SELECT id, state FROM operations
                    WHERE state IN ('claimed','inventorying','writing','verifying')
                      AND lease_until < ?
                    ORDER BY lease_until, created_at, id LIMIT ?
                    """,
                    (timestamp, batch_limit),
                ).fetchall()
                for candidate in candidates:
                    error_code = (
                        "write_uncertain"
                        if candidate["state"] in {"writing", "verifying"}
                        else "connection_error"
                    )
                    cursor = connection.execute(
                        """
                        UPDATE operations
                        SET state = 'failed_retryable', updated_at = ?, completed_at = ?,
                            error_json = ?, result_json = NULL,
                            claim_owner = NULL, lease_until = NULL
                        WHERE id = ? AND state = ? AND lease_until < ?
                        """,
                        (
                            timestamp,
                            timestamp,
                            _json_dump(_fixed_error(error_code)),
                            candidate["id"],
                            candidate["state"],
                            timestamp,
                        ),
                    )
                    if cursor.rowcount == 1:
                        row = connection.execute(
                            "SELECT * FROM operations WHERE id = ?", (candidate["id"],)
                        ).fetchone()
                        recovered.append(_public_operation(row))
                connection.execute(
                    """
                    DELETE FROM device_leases
                    WHERE lease_until < ? AND NOT EXISTS (
                        SELECT 1 FROM operations
                        WHERE operations.device_id = device_leases.device_id
                          AND operations.claim_owner = device_leases.owner_id
                          AND operations.state IN (
                              'claimed','inventorying','writing','verifying'
                          )
                    )
                    """,
                    (timestamp,),
                )
            return recovered
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    @staticmethod
    def _recover_device_operations(
        connection: sqlite3.Connection,
        device_id: str,
        owner_id: str,
        now: int,
    ) -> None:
        rows = connection.execute(
            """
            SELECT id, state FROM operations
            WHERE device_id = ? AND claim_owner = ?
              AND state IN ('claimed','inventorying','writing','verifying')
              AND lease_until < ?
            ORDER BY created_at, id
            """,
            (device_id, owner_id, now),
        ).fetchall()
        for row in rows:
            code = (
                "write_uncertain"
                if row["state"] in {"writing", "verifying"}
                else "connection_error"
            )
            connection.execute(
                """
                UPDATE operations
                SET state='failed_retryable', updated_at=?, completed_at=?,
                    error_json=?, result_json=NULL, claim_owner=NULL,
                    lease_until=NULL
                WHERE id=? AND state=? AND claim_owner=? AND lease_until < ?
                """,
                (
                    now,
                    now,
                    _json_dump(_fixed_error(code)),
                    row["id"],
                    row["state"],
                    owner_id,
                    now,
                ),
            )

    @staticmethod
    def _renew_active_operations(
        connection: sqlite3.Connection,
        device_id: str,
        owner_id: str,
        lease_until: int,
        now: int,
    ) -> None:
        connection.execute(
            """
            UPDATE operations SET lease_until = ?, updated_at = ?
            WHERE device_id = ? AND claim_owner = ?
              AND state IN ('claimed','inventorying','writing','verifying')
            """,
            (lease_until, now, device_id, owner_id),
        )

    def _upsert_lease(
        self,
        connection: sqlite3.Connection,
        device_id: str,
        owner_id: str,
        now: int,
        lease_until: int,
    ) -> bool:
        cursor = connection.execute(
            """
            INSERT INTO device_leases(device_id, owner_id, lease_until)
            VALUES (?, ?, ?)
            ON CONFLICT(device_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                lease_until = excluded.lease_until
            WHERE device_leases.owner_id = excluded.owner_id
               OR device_leases.lease_until < ?
            """,
            (device_id, owner_id, lease_until, now),
        )
        return cursor.rowcount == 1

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION or version not in {0, SCHEMA_VERSION}:
                    raise StoreError("store_schema")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if version == 0:
                        self._create_schema(connection)
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                    elif version == SCHEMA_VERSION:
                        self._validate_schema(connection)
                    else:
                        raise StoreError("store_schema")
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
                connection.execute("PRAGMA journal_mode = WAL")
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None
        except (OSError, ValueError):
            raise StoreError("store_unavailable") from None

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        expected_types = {
            "operations": "table",
            "device_leases": "table",
            "operations_claim_idx": "index",
            "operations_recovery_idx": "index",
            "device_leases_expiry_idx": "index",
        }
        present = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name, type FROM sqlite_master WHERE name IN (?,?,?,?,?)",
                tuple(sorted(expected_types)),
            )
        }
        if present != expected_types:
            raise StoreError("store_schema")
        expected_operation_columns = {
            "id", "request_id", "device_id", "operation_type", "payload",
            "payload_version", "payload_hash", "request_fingerprint", "state",
            "claim_owner", "lease_until", "created_at", "updated_at", "claimed_at",
            "completed_at", "attempts", "result_json", "error_json",
        }
        expected_lease_columns = {"device_id", "owner_id", "lease_until"}
        operation_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(operations)")
        }
        lease_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(device_leases)")
        }
        if operation_columns != expected_operation_columns or lease_columns != expected_lease_columns:
            raise StoreError("store_schema")
        operations_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='operations'"
        ).fetchone()[0]
        lease_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='device_leases'"
        ).fetchone()[0]
        required_fragments = (
            "request_id TEXT NOT NULL UNIQUE",
            "device_id TEXT NOT NULL",
            "operation_type TEXT NOT NULL CHECK(operation_type = 'write_and_verify')",
            "payload BLOB NOT NULL CHECK(length(payload) = 24)",
            "payload_version INTEGER NOT NULL CHECK(payload_version BETWEEN 1 AND 255)",
            "payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64)",
            "request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64)",
            "state TEXT NOT NULL CHECK(state IN (",
            "created_at INTEGER NOT NULL",
            "updated_at INTEGER NOT NULL",
            "attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0)",
            "CHECK((claim_owner IS NULL) = (lease_until IS NULL))",
        )
        if any(fragment not in operations_sql for fragment in required_fragments):
            raise StoreError("store_schema")
        required_lease_fragments = (
            "device_id TEXT PRIMARY KEY",
            "owner_id TEXT NOT NULL",
            "lease_until INTEGER NOT NULL",
        )
        if any(fragment not in lease_sql for fragment in required_lease_fragments):
            raise StoreError("store_schema")
        expected_indexes = {
            "operations_claim_idx": ["device_id", "state", "created_at", "id"],
            "operations_recovery_idx": ["state", "lease_until", "created_at", "id"],
            "device_leases_expiry_idx": ["lease_until", "device_id"],
        }
        for name, columns in expected_indexes.items():
            actual = [row[2] for row in connection.execute(f"PRAGMA index_info({name})")]
            if actual != columns:
                raise StoreError("store_schema")

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        statements = (
            """CREATE TABLE operations (
                id INTEGER PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                device_id TEXT NOT NULL,
                operation_type TEXT NOT NULL CHECK(operation_type = 'write_and_verify'),
                payload BLOB NOT NULL CHECK(length(payload) = 24),
                payload_version INTEGER NOT NULL CHECK(payload_version BETWEEN 1 AND 255),
                payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
                request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint) = 64),
                state TEXT NOT NULL CHECK(state IN (
                    'queued','claimed','inventorying','writing','verifying','succeeded',
                    'failed_retryable','failed_manual','cancelled'
                )),
                claim_owner TEXT,
                lease_until INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                claimed_at INTEGER,
                completed_at INTEGER,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
                result_json TEXT,
                error_json TEXT,
                CHECK((claim_owner IS NULL) = (lease_until IS NULL))
            )""",
            """CREATE TABLE device_leases (
                device_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                lease_until INTEGER NOT NULL
            )""",
            """CREATE INDEX operations_claim_idx
                ON operations(device_id, state, created_at, id)""",
            """CREATE INDEX operations_recovery_idx
                ON operations(state, lease_until, created_at, id)""",
            """CREATE INDEX device_leases_expiry_idx
                ON device_leases(lease_until, device_id)""",
        )
        for statement in statements:
            connection.execute(statement)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._path,
                timeout=self._busy_timeout_ms / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
        finally:
            if connection is not None:
                connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise


def _request_identifier(value: object) -> str:
    if not isinstance(value, dict) or "request_id" not in value:
        raise StoreError("invalid_request")
    return _identifier(value["request_id"], "invalid_request")


def _validate_request(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != _REQUEST_KEYS:
        raise StoreError("invalid_request")
    request_id = _identifier(value["request_id"], "invalid_request")
    device_id = _identifier(value["device_id"], "invalid_request")
    if value["operation_type"] != "write_and_verify":
        raise StoreError("invalid_request")
    version = value["payload_version"]
    if type(version) is not int or not 1 <= version <= 255:
        raise StoreError("invalid_request")
    payload_hex = value["payload_hex"]
    if not isinstance(payload_hex, str) or len(payload_hex) != 48:
        raise StoreError("invalid_request")
    try:
        payload = bytes.fromhex(payload_hex)
    except ValueError:
        raise StoreError("invalid_request") from None
    if len(payload) != 24:
        raise StoreError("invalid_request")
    return {
        "request_id": request_id,
        "device_id": device_id,
        "operation_type": "write_and_verify",
        "payload": payload,
        "payload_hash": hashlib.sha256(payload).hexdigest(),
        "payload_version": version,
    }


def _fingerprint(request: dict) -> str:
    canonical = json.dumps(
        {
            "request_id": request["request_id"],
            "device_id": request["device_id"],
            "operation_type": request["operation_type"],
            "payload_hash": request["payload_hash"],
            "payload_version": request["payload_version"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _identifier(value: object, error_code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise StoreError(error_code)
    return value


def _timestamp(value: int | float | None) -> int:
    if value is None:
        value = time.time()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StoreError("invalid_argument")
    if not math.isfinite(value) or value < 0 or value > _MAX_TIMESTAMP:
        raise StoreError("invalid_argument")
    return int(value)


def _lease_times(now: int | float | None, lease_seconds: object) -> tuple[int, int]:
    timestamp = _timestamp(now)
    if (
        isinstance(lease_seconds, bool)
        or not isinstance(lease_seconds, (int, float))
        or not math.isfinite(lease_seconds)
        or lease_seconds <= 0
        or lease_seconds > _MAX_LEASE_SECONDS
        or int(lease_seconds) != lease_seconds
    ):
        raise StoreError("invalid_argument")
    lease_until = timestamp + int(lease_seconds)
    if lease_until > _MAX_TIMESTAMP:
        raise StoreError("invalid_argument")
    return timestamp, lease_until


def _validate_result(value: object, state: str) -> dict | None:
    if state != "succeeded":
        if value is not None:
            raise StoreError("invalid_result")
        return None
    if not isinstance(value, dict) or set(value) != _RESULT_KEYS:
        raise StoreError("invalid_result")
    identity_hash = value["identity_hash"]
    if identity_hash is not None and (
        not isinstance(identity_hash, str) or _HASH_RE.fullmatch(identity_hash) is None
    ):
        raise StoreError("invalid_result")
    if type(value["verification_ok"]) is not bool or not value["verification_ok"]:
        raise StoreError("invalid_result")
    return {"identity_hash": identity_hash, "verification_ok": True}


def _fixed_error(code: str, device_code: str | None = None) -> dict:
    message, retryable = _SAFE_ERRORS[code]
    return {
        "code": code,
        "message": message,
        "device_code": device_code,
        "retryable": retryable,
    }


def _validate_error(value: object, state: str) -> dict | None:
    if state not in {"failed_retryable", "failed_manual"}:
        if value is not None:
            raise StoreError("invalid_error")
        return None
    if not isinstance(value, dict) or set(value) != _ERROR_KEYS:
        raise StoreError("invalid_error")
    code = value["code"]
    if code not in _SAFE_ERRORS:
        raise StoreError("invalid_error")
    message, expected_retryable = _SAFE_ERRORS[code]
    if value["message"] != message or type(value["retryable"]) is not bool:
        raise StoreError("invalid_error")
    if value["retryable"] is not expected_retryable:
        raise StoreError("invalid_error")
    if (state == "failed_retryable") is not expected_retryable:
        raise StoreError("invalid_error")
    device_code = value["device_code"]
    if device_code is not None and (
        not isinstance(device_code, str)
        or _DEVICE_CODE_RE.fullmatch(device_code) is None
    ):
        raise StoreError("invalid_error")
    return _fixed_error(code, device_code)


def _public_operation(row: sqlite3.Row) -> dict:
    try:
        result = _json_load(row["result_json"])
        error = _json_load(row["error_json"])
        state = row["state"]
        if state == "succeeded":
            result = _validate_result(result, state)
            if error is not None or row["completed_at"] is None:
                raise StoreError("store_unavailable")
        elif state in {"failed_retryable", "failed_manual"}:
            error = _validate_error(error, state)
            if result is not None or row["completed_at"] is None:
                raise StoreError("store_unavailable")
        elif state == "cancelled":
            if result is not None or error is not None or row["completed_at"] is None:
                raise StoreError("store_unavailable")
        elif result is not None or error is not None or row["completed_at"] is not None:
            raise StoreError("store_unavailable")
        if state in {"queued", "succeeded", "failed_retryable", "failed_manual", "cancelled"}:
            if row["claim_owner"] is not None or row["lease_until"] is not None:
                raise StoreError("store_unavailable")
        elif row["claim_owner"] is None or row["lease_until"] is None:
            raise StoreError("store_unavailable")
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError, StoreError):
        raise StoreError("store_unavailable") from None
    return {
        "id": row["id"],
        "request_id": row["request_id"],
        "device_id": row["device_id"],
        "operation_type": row["operation_type"],
        "payload_version": row["payload_version"],
        "state": row["state"],
        "claim_owner": row["claim_owner"],
        "lease_until": row["lease_until"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "claimed_at": row["claimed_at"],
        "completed_at": row["completed_at"],
        "attempts": row["attempts"],
        "result": result,
        "error": error,
    }


def _lease_dict(device_id: str, owner_id: str, lease_until: int) -> dict:
    return {"device_id": device_id, "owner_id": owner_id, "lease_until": lease_until}


def _json_dump(value: object) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _json_load(value: str | None) -> dict | None:
    return None if value is None else json.loads(value)


def _translate_sqlite_error(error: sqlite3.Error) -> StoreError:
    if isinstance(error, sqlite3.OperationalError) and "locked" in str(error).lower():
        return StoreError("store_busy")
    return StoreError("store_unavailable")
