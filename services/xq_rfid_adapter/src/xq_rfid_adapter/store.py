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

from .domain import AdapterErrorCode


SCHEMA_VERSION = 1
_DEFAULT_BUSY_TIMEOUT_MS = 1000
_MAX_BUSY_TIMEOUT_MS = 60_000
_MAX_LEASE_SECONDS = 86_400
_MAX_TIMESTAMP = 253_402_300_799
_DEFAULT_RECOVERY_BATCH = 100
_MAX_RECOVERY_BATCH = 1000
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
    AdapterErrorCode.CONNECTION_ERROR: ("device connection failed", True),
    AdapterErrorCode.TIMEOUT: ("device operation timed out", True),
    AdapterErrorCode.PROTOCOL_ERROR: ("invalid request", False),
    AdapterErrorCode.DEVICE_ERROR: ("internal device error", False),
    AdapterErrorCode.NO_TAG: ("no tag was found", False),
    AdapterErrorCode.MULTIPLE_TAGS: ("multiple tags were found", False),
    AdapterErrorCode.TARGET_CHANGED: ("tag target changed", False),
    AdapterErrorCode.UNSUPPORTED_MEMORY: ("memory operation is unsupported", False),
    AdapterErrorCode.CAPACITY_EXCEEDED: ("request body is too large", False),
    AdapterErrorCode.WRITE_UNCERTAIN: ("write outcome is uncertain", False),
    AdapterErrorCode.VERIFICATION_FAILED: ("write verification failed", False),
}
_ACTIVE_STATE_SQL = "'claimed','inventorying','writing','verifying'"
_SCHEMA_STATEMENTS = (
    """CREATE TABLE operations (
        id INTEGER PRIMARY KEY,
        request_id TEXT NOT NULL UNIQUE
            CHECK(length(request_id) BETWEEN 1 AND 128
                AND request_id NOT GLOB '*[^A-Za-z0-9._-]*'),
        device_id TEXT NOT NULL
            CHECK(length(device_id) BETWEEN 1 AND 128
                AND device_id NOT GLOB '*[^A-Za-z0-9._-]*'),
        operation_type TEXT NOT NULL CHECK(operation_type = 'write_and_verify'),
        payload BLOB NOT NULL CHECK(typeof(payload) = 'blob' AND length(payload) = 24),
        payload_version INTEGER NOT NULL
            CHECK(typeof(payload_version) = 'integer' AND payload_version BETWEEN 1 AND 255),
        payload_hash TEXT NOT NULL
            CHECK(length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'),
        request_fingerprint TEXT NOT NULL
            CHECK(length(request_fingerprint) = 64
                AND request_fingerprint NOT GLOB '*[^0-9a-f]*'),
        state TEXT NOT NULL CHECK(state IN (
            'queued','claimed','inventorying','writing','verifying','succeeded',
            'failed_retryable','failed_manual','cancelled'
        )),
        claim_owner TEXT CHECK(claim_owner IS NULL OR (
            length(claim_owner) BETWEEN 1 AND 128
            AND claim_owner NOT GLOB '*[^A-Za-z0-9._-]*'
        )),
        lease_until INTEGER CHECK(lease_until IS NULL OR (
            typeof(lease_until) = 'integer' AND lease_until BETWEEN 0 AND 253402300799
        )),
        created_at INTEGER NOT NULL CHECK(
            typeof(created_at) = 'integer' AND created_at BETWEEN 0 AND 253402300799
        ),
        updated_at INTEGER NOT NULL CHECK(
            typeof(updated_at) = 'integer' AND updated_at BETWEEN created_at AND 253402300799
        ),
        claimed_at INTEGER CHECK(claimed_at IS NULL OR (
            typeof(claimed_at) = 'integer'
            AND claimed_at BETWEEN created_at AND 253402300799
        )),
        completed_at INTEGER CHECK(completed_at IS NULL OR (
            typeof(completed_at) = 'integer'
            AND completed_at BETWEEN created_at AND 253402300799
        )),
        attempts INTEGER NOT NULL DEFAULT 0
            CHECK(typeof(attempts) = 'integer' AND attempts >= 0),
        result_json TEXT,
        error_json TEXT,
        CHECK(
            (state IN ('claimed','inventorying','writing','verifying')
                AND claim_owner IS NOT NULL AND lease_until IS NOT NULL
                AND claimed_at IS NOT NULL AND completed_at IS NULL)
            OR
            (state NOT IN ('claimed','inventorying','writing','verifying')
                AND claim_owner IS NULL AND lease_until IS NULL)
        ),
        CHECK(
            (state IN ('succeeded','failed_retryable','failed_manual','cancelled')
                AND completed_at IS NOT NULL)
            OR
            (state NOT IN ('succeeded','failed_retryable','failed_manual','cancelled')
                AND completed_at IS NULL)
        )
    )""",
    """CREATE TABLE device_leases (
        device_id TEXT PRIMARY KEY
            CHECK(length(device_id) BETWEEN 1 AND 128
                AND device_id NOT GLOB '*[^A-Za-z0-9._-]*'),
        owner_id TEXT NOT NULL
            CHECK(length(owner_id) BETWEEN 1 AND 128
                AND owner_id NOT GLOB '*[^A-Za-z0-9._-]*'),
        lease_until INTEGER NOT NULL CHECK(
            typeof(lease_until) = 'integer' AND lease_until BETWEEN 0 AND 253402300799
        )
    )""",
    """CREATE TABLE adapter_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE INDEX operations_claim_idx
        ON operations(device_id, state, created_at, id)""",
    """CREATE INDEX operations_recovery_idx
        ON operations(lease_until, created_at, id)
        WHERE state IN ('claimed','inventorying','writing','verifying')""",
    """CREATE UNIQUE INDEX operations_one_active_device_idx
        ON operations(device_id)
        WHERE state IN ('claimed','inventorying','writing','verifying')""",
    """CREATE INDEX device_leases_expiry_idx
        ON device_leases(lease_until, device_id)""",
)


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.split())


_SCHEMA_SIGNATURE = hashlib.sha256(
    "\n".join(_normalize_schema_sql(statement) for statement in _SCHEMA_STATEMENTS).encode(
        "utf-8"
    )
).hexdigest()
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
        if (
            isinstance(path, bool)
            or not isinstance(path, (str, Path))
            or not str(path)
            or str(path) == ":memory:"
        ):
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
        owner_id: object,
        expected_state: object,
        new_state: object,
        result: object = None,
        error: object = None,
        now: int | float | None = None,
    ) -> dict:
        request_id = _identifier(request_id, "invalid_argument")
        owner_id = _identifier(owner_id, "invalid_argument")
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
        authority_sql = """
            AND claim_owner = ? AND lease_until >= ?
            AND EXISTS (
                SELECT 1 FROM device_leases
                WHERE device_leases.device_id = operations.device_id
                  AND device_leases.owner_id = operations.claim_owner
                  AND device_leases.owner_id = ?
                  AND device_leases.lease_until = operations.lease_until
                  AND device_leases.lease_until >= ?
            )
        """
        try:
            with self._transaction() as connection:
                self._validate_active_device_invariant(connection)
                current = connection.execute(
                    "SELECT state, updated_at, device_id, claim_owner, lease_until "
                    "FROM operations WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                if current is None:
                    raise StoreError("not_found")
                if current["state"] != expected_state:
                    raise StoreError("stale_state")
                if timestamp < current["updated_at"]:
                    raise StoreError("invalid_argument")
                lease = connection.execute(
                    "SELECT device_id, owner_id, lease_until FROM device_leases "
                    "WHERE device_id = ?",
                    (current["device_id"],),
                ).fetchone()
                if lease is None:
                    raise StoreError("store_unavailable")
                public_lease = _public_lease(lease)
                if (
                    current["claim_owner"] != public_lease["owner_id"]
                    or current["lease_until"] != public_lease["lease_until"]
                ):
                    raise StoreError("store_unavailable")
                if current["claim_owner"] != owner_id or current["lease_until"] < timestamp:
                    raise StoreError("lease_conflict")
                authority = (owner_id, timestamp, owner_id, timestamp)
                if terminal:
                    cursor = connection.execute(
                        """
                        UPDATE operations
                        SET state = ?, updated_at = ?, completed_at = ?,
                            result_json = ?, error_json = ?, claim_owner = NULL,
                            lease_until = NULL
                        WHERE request_id = ? AND state = ?
                        """ + authority_sql,
                        (
                            new_state,
                            timestamp,
                            timestamp,
                            _json_dump(result_value),
                            _json_dump(error_value),
                            request_id,
                            expected_state,
                            *authority,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE operations SET state = ?, updated_at = ?
                        WHERE request_id = ? AND state = ?
                        """ + authority_sql,
                        (
                            new_state,
                            timestamp,
                            request_id,
                            expected_state,
                            *authority,
                        ),
                    )
                if cursor.rowcount != 1:
                    raise StoreError("store_unavailable")
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
        try:
            with self._transaction() as connection:
                timestamp, lease_until = _lease_times(now, lease_seconds)
                self._validate_active_device_invariant(connection)
                current = connection.execute(
                    "SELECT device_id, owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                lease = None if current is None else _public_lease(current)
                active = self._active_operation(connection, device_id)
                if active is not None:
                    if (
                        lease is None
                        or active["claim_owner"] != lease["owner_id"]
                        or active["lease_until"] != lease["lease_until"]
                    ):
                        raise StoreError("store_unavailable")
                    if timestamp < active["updated_at"]:
                        raise StoreError("invalid_argument")
                takeover = (
                    lease is not None
                    and lease["owner_id"] != owner_id
                    and lease["lease_until"] < timestamp
                )
                expired_same_owner = (
                    lease is not None
                    and lease["owner_id"] == owner_id
                    and lease["lease_until"] < timestamp
                )
                if takeover or expired_same_owner:
                    recovered = self._recover_device_operations(
                        connection, device_id, lease["owner_id"], timestamp
                    )
                    remaining = connection.execute(
                        f"SELECT 1 FROM operations WHERE device_id = ? "
                        f"AND state IN ({_ACTIVE_STATE_SQL}) LIMIT 1",
                        (device_id,),
                    ).fetchone()
                    if recovered not in {0, 1} or remaining is not None:
                        raise StoreError("store_unavailable")
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
        try:
            with self._transaction() as connection:
                timestamp, lease_until = _lease_times(now, lease_seconds)
                self._validate_active_device_invariant(connection)
                current = connection.execute(
                    "SELECT device_id, owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                if current is None:
                    raise StoreError("lease_conflict")
                lease = _public_lease(current)
                active = self._active_operation(connection, device_id)
                if active is not None:
                    if (
                        active["claim_owner"] != lease["owner_id"]
                        or active["lease_until"] != lease["lease_until"]
                    ):
                        raise StoreError("store_unavailable")
                    if timestamp < active["updated_at"]:
                        raise StoreError("invalid_argument")
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
                self._validate_active_device_invariant(connection)
                current = connection.execute(
                    "SELECT device_id, owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                lease = None if current is None else _public_lease(current)
                active = self._active_operation(connection, device_id)
                if active is not None:
                    if (
                        lease is None
                        or active["claim_owner"] != lease["owner_id"]
                        or active["lease_until"] != lease["lease_until"]
                    ):
                        raise StoreError("store_unavailable")
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
                return None if row is None else _public_lease(row)
        except StoreError:
            raise
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
        try:
            with self._transaction() as connection:
                timestamp, lease_until = _lease_times(now, lease_seconds)
                self._validate_active_device_invariant(connection)
                current = connection.execute(
                    "SELECT device_id, owner_id, lease_until FROM device_leases WHERE device_id = ?",
                    (device_id,),
                ).fetchone()
                lease = None if current is None else _public_lease(current)
                active = self._active_operation(connection, device_id)
                if active is not None:
                    if (
                        lease is None
                        or active["claim_owner"] != lease["owner_id"]
                        or active["lease_until"] != lease["lease_until"]
                    ):
                        raise StoreError("store_unavailable")
                    if timestamp < active["updated_at"]:
                        raise StoreError("invalid_argument")
                expired = lease is not None and lease["lease_until"] < timestamp
                if expired and active is not None:
                    recovered = self._recover_device_operations(
                        connection, device_id, lease["owner_id"], timestamp
                    )
                    if recovered != 1:
                        raise StoreError("store_unavailable")
                    active = None
                if active is not None:
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
                        AdapterErrorCode.WRITE_UNCERTAIN
                        if candidate["state"] in {"writing", "verifying"}
                        else AdapterErrorCode.CONNECTION_ERROR
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
                    WHERE device_id IN (
                        SELECT device_leases.device_id FROM device_leases
                        WHERE lease_until < ? AND NOT EXISTS (
                            SELECT 1 FROM operations
                            WHERE operations.device_id = device_leases.device_id
                              AND operations.claim_owner = device_leases.owner_id
                              AND operations.state IN (
                                  'claimed','inventorying','writing','verifying'
                              )
                        )
                        ORDER BY lease_until, device_id LIMIT ?
                    )
                    """,
                    (timestamp, batch_limit),
                )
            return recovered
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None

    @staticmethod
    def _validate_active_device_invariant(connection: sqlite3.Connection) -> None:
        index = connection.execute(
            "SELECT type, sql FROM sqlite_master "
            "WHERE name = 'operations_one_active_device_idx'"
        ).fetchone()
        if (
            index is None
            or index["type"] != "index"
            or _normalize_schema_sql(index["sql"] or "")
            != _normalize_schema_sql(_SCHEMA_STATEMENTS[5])
        ):
            raise StoreError("store_schema")
        duplicate = connection.execute(
            f"SELECT 1 FROM operations WHERE state IN ({_ACTIVE_STATE_SQL}) "
            "GROUP BY device_id HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate is not None:
            raise StoreError("store_schema")

    @staticmethod
    def _active_operation(
        connection: sqlite3.Connection, device_id: str
    ) -> sqlite3.Row | None:
        row = connection.execute(
            f"SELECT claim_owner, lease_until, claimed_at, updated_at, attempts "
            f"FROM operations WHERE device_id = ? AND state IN ({_ACTIVE_STATE_SQL}) "
            "LIMIT 1",
            (device_id,),
        ).fetchone()
        if row is None:
            return None
        if (
            not isinstance(row["claim_owner"], str)
            or _IDENTIFIER_RE.fullmatch(row["claim_owner"]) is None
            or type(row["lease_until"]) is not int
            or not 0 <= row["lease_until"] <= _MAX_TIMESTAMP
            or type(row["claimed_at"]) is not int
            or not 0 <= row["claimed_at"] <= _MAX_TIMESTAMP
            or type(row["updated_at"]) is not int
            or not row["claimed_at"] <= row["updated_at"] <= _MAX_TIMESTAMP
            or type(row["attempts"]) is not int
            or row["attempts"] < 1
        ):
            raise StoreError("store_unavailable")
        return row

    @staticmethod
    def _recover_device_operations(
        connection: sqlite3.Connection,
        device_id: str,
        owner_id: str,
        now: int,
    ) -> int:
        cursor = connection.execute(
            """
            UPDATE operations
            SET state='failed_retryable', updated_at=?, completed_at=?,
                error_json=CASE
                    WHEN state IN ('writing','verifying') THEN ? ELSE ? END,
                result_json=NULL, claim_owner=NULL, lease_until=NULL
            WHERE device_id=? AND claim_owner=?
              AND state IN ('claimed','inventorying','writing','verifying')
              AND lease_until < ?
            """,
            (
                now,
                now,
                _json_dump(_fixed_error(AdapterErrorCode.WRITE_UNCERTAIN)),
                _json_dump(_fixed_error(AdapterErrorCode.CONNECTION_ERROR)),
                device_id,
                owner_id,
                now,
            ),
        )
        if cursor.rowcount > 1:
            raise StoreError("store_schema")
        return cursor.rowcount

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
                databases = connection.execute("PRAGMA database_list").fetchall()
                main = [row for row in databases if row[1] == "main"]
                if len(main) != 1 or not isinstance(main[0][2], str) or not main[0][2]:
                    raise StoreError("store_unavailable")
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > SCHEMA_VERSION or version not in {0, SCHEMA_VERSION}:
                    raise StoreError("store_schema")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                    if version == 0:
                        existing = connection.execute(
                            "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
                        ).fetchone()
                        if existing is not None:
                            raise StoreError("store_schema")
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
                journal = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if journal is None or len(journal) < 1 or journal[0] != "wal":
                    raise StoreError("store_unavailable")
        except StoreError:
            raise
        except sqlite3.Error as error:
            raise _translate_sqlite_error(error) from None
        except (OSError, ValueError):
            raise StoreError("store_unavailable") from None

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        expected_objects = {
            "operations": ("table", _SCHEMA_STATEMENTS[0]),
            "device_leases": ("table", _SCHEMA_STATEMENTS[1]),
            "adapter_meta": ("table", _SCHEMA_STATEMENTS[2]),
            "operations_claim_idx": ("index", _SCHEMA_STATEMENTS[3]),
            "operations_recovery_idx": ("index", _SCHEMA_STATEMENTS[4]),
            "operations_one_active_device_idx": ("index", _SCHEMA_STATEMENTS[5]),
            "device_leases_expiry_idx": ("index", _SCHEMA_STATEMENTS[6]),
        }
        placeholders = ",".join("?" for _ in expected_objects)
        present = {
            row["name"]: (row["type"], row["sql"])
            for row in connection.execute(
                f"SELECT name, type, sql FROM sqlite_master WHERE name IN ({placeholders})",
                tuple(expected_objects),
            )
        }
        if set(present) != set(expected_objects):
            raise StoreError("store_schema")
        unexpected = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND name NOT IN "
            f"({placeholders}) LIMIT 1",
            tuple(expected_objects),
        ).fetchone()
        if unexpected is not None:
            raise StoreError("store_schema")
        for name, (object_type, sql) in expected_objects.items():
            actual_type, actual_sql = present[name]
            if actual_type != object_type or actual_sql is None:
                raise StoreError("store_schema")
            if _normalize_schema_sql(actual_sql) != _normalize_schema_sql(sql):
                raise StoreError("store_schema")
        meta = connection.execute(
            "SELECT value FROM adapter_meta WHERE key = 'schema_signature'"
        ).fetchall()
        if len(meta) != 1 or meta[0]["value"] != _SCHEMA_SIGNATURE:
            raise StoreError("store_schema")
        expected_columns = {
            "operations": [
                ("id", "INTEGER", 0, None, 1),
                ("request_id", "TEXT", 1, None, 0),
                ("device_id", "TEXT", 1, None, 0),
                ("operation_type", "TEXT", 1, None, 0),
                ("payload", "BLOB", 1, None, 0),
                ("payload_version", "INTEGER", 1, None, 0),
                ("payload_hash", "TEXT", 1, None, 0),
                ("request_fingerprint", "TEXT", 1, None, 0),
                ("state", "TEXT", 1, None, 0),
                ("claim_owner", "TEXT", 0, None, 0),
                ("lease_until", "INTEGER", 0, None, 0),
                ("created_at", "INTEGER", 1, None, 0),
                ("updated_at", "INTEGER", 1, None, 0),
                ("claimed_at", "INTEGER", 0, None, 0),
                ("completed_at", "INTEGER", 0, None, 0),
                ("attempts", "INTEGER", 1, "0", 0),
                ("result_json", "TEXT", 0, None, 0),
                ("error_json", "TEXT", 0, None, 0),
            ],
            "device_leases": [
                ("device_id", "TEXT", 0, None, 1),
                ("owner_id", "TEXT", 1, None, 0),
                ("lease_until", "INTEGER", 1, None, 0),
            ],
            "adapter_meta": [
                ("key", "TEXT", 0, None, 1),
                ("value", "TEXT", 1, None, 0),
            ],
        }
        for table, expected in expected_columns.items():
            actual = [tuple(row)[1:6] for row in connection.execute(f"PRAGMA table_info({table})")]
            if actual != expected:
                raise StoreError("store_schema")
        expected_indexes = {
            "operations_claim_idx": (False, False, ["device_id", "state", "created_at", "id"]),
            "operations_recovery_idx": (
                False,
                True,
                ["lease_until", "created_at", "id"],
            ),
            "operations_one_active_device_idx": (True, True, ["device_id"]),
            "device_leases_expiry_idx": (False, False, ["lease_until", "device_id"]),
        }
        table_indexes = {
            "operations": {
                "operations_claim_idx",
                "operations_recovery_idx",
                "operations_one_active_device_idx",
            },
            "device_leases": {"device_leases_expiry_idx"},
        }
        for table, names in table_indexes.items():
            listed = {row[1]: row for row in connection.execute(f"PRAGMA index_list({table})")}
            for name in names:
                unique, partial, columns = expected_indexes[name]
                row = listed.get(name)
                if row is None or bool(row[2]) is not unique or bool(row[4]) is not partial:
                    raise StoreError("store_schema")
                actual = [entry[2] for entry in connection.execute(f"PRAGMA index_info({name})")]
                if actual != columns:
                    raise StoreError("store_schema")
        if connection.execute(
            f"SELECT COUNT(*) FROM operations WHERE state IN ({_ACTIVE_STATE_SQL}) "
            "GROUP BY device_id HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone() is not None:
            raise StoreError("store_schema")

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO adapter_meta(key, value) VALUES ('schema_signature', ?)",
            (_SCHEMA_SIGNATURE,),
        )

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


def _fixed_error(
    code: AdapterErrorCode, device_code: str | None = None
) -> dict:
    message, retryable = _SAFE_ERRORS[code]
    return {
        "code": code.value,
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
    try:
        code = AdapterErrorCode(value["code"])
    except (TypeError, ValueError):
        raise StoreError("invalid_error") from None
    if code not in _SAFE_ERRORS:
        raise StoreError("invalid_error")
    message, expected_retryable = _SAFE_ERRORS[code]
    if value["message"] != message or type(value["retryable"]) is not bool:
        raise StoreError("invalid_error")
    if value["retryable"] is not expected_retryable:
        raise StoreError("invalid_error")
    write_uncertain = code is AdapterErrorCode.WRITE_UNCERTAIN
    if state == "failed_retryable":
        if not expected_retryable and not write_uncertain:
            raise StoreError("invalid_error")
    elif expected_retryable or write_uncertain:
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
        if state not in _STATES:
            raise StoreError("store_unavailable")
        for identifier_column in ("request_id", "device_id"):
            if _IDENTIFIER_RE.fullmatch(row[identifier_column]) is None:
                raise StoreError("store_unavailable")
        if row["operation_type"] != "write_and_verify":
            raise StoreError("store_unavailable")
        if type(row["payload_version"]) is not int or not 1 <= row["payload_version"] <= 255:
            raise StoreError("store_unavailable")
        if not isinstance(row["payload"], bytes) or len(row["payload"]) != 24:
            raise StoreError("store_unavailable")
        if _HASH_RE.fullmatch(row["payload_hash"]) is None:
            raise StoreError("store_unavailable")
        if hashlib.sha256(row["payload"]).hexdigest() != row["payload_hash"]:
            raise StoreError("store_unavailable")
        if _HASH_RE.fullmatch(row["request_fingerprint"]) is None:
            raise StoreError("store_unavailable")
        expected_fingerprint = _fingerprint(
            {
                "request_id": row["request_id"],
                "device_id": row["device_id"],
                "operation_type": row["operation_type"],
                "payload_hash": row["payload_hash"],
                "payload_version": row["payload_version"],
            }
        )
        if row["request_fingerprint"] != expected_fingerprint:
            raise StoreError("store_unavailable")
        if type(row["attempts"]) is not int or row["attempts"] < 0:
            raise StoreError("store_unavailable")
        for column in ("created_at", "updated_at", "claimed_at", "completed_at", "lease_until"):
            value = row[column]
            if value is not None and (type(value) is not int or not 0 <= value <= _MAX_TIMESTAMP):
                raise StoreError("store_unavailable")
        if row["updated_at"] < row["created_at"]:
            raise StoreError("store_unavailable")
        for column in ("claimed_at", "completed_at"):
            if row[column] is not None and row[column] < row["created_at"]:
                raise StoreError("store_unavailable")
        if row["claimed_at"] is not None and row["updated_at"] < row["claimed_at"]:
            raise StoreError("store_unavailable")
        if row["completed_at"] is not None and (
            row["updated_at"] < row["completed_at"]
            or (row["claimed_at"] is not None and row["completed_at"] < row["claimed_at"])
        ):
            raise StoreError("store_unavailable")
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
            if state == "queued":
                if row["claimed_at"] is not None or row["attempts"] != 0:
                    raise StoreError("store_unavailable")
            elif row["claimed_at"] is None or row["attempts"] < 1:
                raise StoreError("store_unavailable")
        elif (
            row["claim_owner"] is None
            or _IDENTIFIER_RE.fullmatch(row["claim_owner"]) is None
            or row["lease_until"] is None
            or row["claimed_at"] is None
            or row["attempts"] < 1
        ):
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


def _public_lease(row: sqlite3.Row) -> dict:
    try:
        device_id = _identifier(row["device_id"], "store_unavailable")
        owner_id = _identifier(row["owner_id"], "store_unavailable")
        lease_until = row["lease_until"]
        if type(lease_until) is not int or not 0 <= lease_until <= _MAX_TIMESTAMP:
            raise StoreError("store_unavailable")
        return _lease_dict(device_id, owner_id, lease_until)
    except (KeyError, TypeError, ValueError, StoreError):
        raise StoreError("store_unavailable") from None


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
