"""Authenticated, bounded standard-library HTTP boundary for the adapter."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import unquote_to_bytes

from .domain import (
    AdapterError,
    AdapterErrorCode,
    error_envelope,
    success_envelope,
)


MAX_BODY_BYTES = 64 * 1024
MAX_NONCE_LENGTH = 256
MAX_IDENTIFIER_LENGTH = 128
DEFAULT_MAX_SKEW_SECONDS = 300
_NONCE_RE = re.compile(r"\A[0-9A-Fa-f]{32,256}\Z")
_IDENTIFIER_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")
_SIGNATURE_RE = re.compile(r"\A[0-9A-Fa-f]{64}\Z")
_PAYLOAD_RE = re.compile(r"\A[0-9A-Fa-f]{48}\Z")
_OPERATION_KEYS = frozenset(
    {"request_id", "operation_type", "device_id", "payload_hex", "payload_version"}
)
_SAFE_RESULT_KEYS = frozenset({
    "route", "state", "status", "request_id", "device_id", "operation_type",
    "payload_version", "capabilities", "firmware_version", "hardware_version",
    "module_version", "antenna_count", "region", "supports_epc", "supports_tid",
    "supports_user_read", "supports_user_write", "masked_epc", "masked_tid",
    "identity_hash", "device_code", "safe_error", "verification_ok", "retryable",
})
_SAFE_MESSAGES = {
    AdapterErrorCode.AUTHENTICATION_ERROR: "authentication failed",
    AdapterErrorCode.CONFIGURATION_ERROR: "resource is not configured",
    AdapterErrorCode.PROTOCOL_ERROR: "invalid request",
    AdapterErrorCode.CAPACITY_EXCEEDED: "request body is too large",
    AdapterErrorCode.DEVICE_ERROR: "internal device error",
    AdapterErrorCode.CONNECTION_ERROR: "device connection failed",
    AdapterErrorCode.TIMEOUT: "device operation timed out",
    AdapterErrorCode.NO_TAG: "no tag was found",
    AdapterErrorCode.MULTIPLE_TAGS: "multiple tags were found",
    AdapterErrorCode.TARGET_CHANGED: "tag target changed",
    AdapterErrorCode.UNSUPPORTED_MEMORY: "memory operation is unsupported",
    AdapterErrorCode.WRITE_UNCERTAIN: "write outcome is uncertain",
    AdapterErrorCode.VERIFICATION_FAILED: "write verification failed",
}


class AdapterService(Protocol):
    def test_connection(self, device_id: str) -> dict: ...
    def get_device(self, device_id: str) -> dict: ...
    def submit_operation(self, request: dict) -> dict: ...
    def get_operation(self, request_id: str) -> dict: ...


def canonical_request(
    method: str,
    request_target: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bytes:
    digest = hashlib.sha256(body).hexdigest()
    return "\n".join(
        (method.upper(), request_target, timestamp, nonce, digest)
    ).encode("utf-8")


def sign_request(
    secret: bytes,
    method: str,
    request_target: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    return hmac.new(
        secret,
        canonical_request(method, request_target, timestamp, nonce, body),
        hashlib.sha256,
    ).hexdigest()


def verify_signature(
    secret: bytes,
    signature: str,
    method: str,
    request_target: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bool:
    expected = sign_request(secret, method, request_target, timestamp, nonce, body)
    return hmac.compare_digest(expected, signature)


def _authentication_error() -> AdapterError:
    return AdapterError(
        AdapterErrorCode.AUTHENTICATION_ERROR,
        _SAFE_MESSAGES[AdapterErrorCode.AUTHENTICATION_ERROR],
    )


class ReplayGuard:
    """Persistent global-per-secret nonce replay protection."""

    def __init__(self, sqlite_path: str | Path, ttl_seconds: int = 300) -> None:
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        self.ttl_seconds = ttl_seconds
        self._connection = sqlite3.connect(
            str(sqlite_path), timeout=5, isolation_level=None, check_same_thread=False
        )
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS replay_nonces ("
            "nonce TEXT PRIMARY KEY, accepted_at INTEGER NOT NULL)"
        )
        self._lock = threading.Lock()

    def accept(self, nonce: str, *, now: int, expires_at: int | None = None) -> None:
        expiry = now + self.ttl_seconds if expires_at is None else expires_at
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    "DELETE FROM replay_nonces WHERE accepted_at < ?", (now,)
                )
                self._connection.execute(
                    "INSERT INTO replay_nonces (nonce, accepted_at) VALUES (?, ?)",
                    (nonce, expiry),
                )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                raise _authentication_error() from error
            except BaseException:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            if connection is not None:
                self._connection = None
                connection.close()


def _get_unique_header(headers: Mapping[str, str], name: str) -> str:
    getter = getattr(headers, "get_all", None)
    if getter is not None:
        values = getter(name, [])
        if len(values) != 1:
            raise _authentication_error()
        return values[0]
    value = headers.get(name)
    if value is None:
        raise _authentication_error()
    return value


def authenticate_request(
    secret: bytes,
    headers: Mapping[str, str],
    method: str,
    request_target: str,
    body: bytes,
    replay_guard: ReplayGuard,
    *,
    now: int | float | None = None,
    max_skew_seconds: int = DEFAULT_MAX_SKEW_SECONDS,
) -> None:
    try:
        timestamp = _get_unique_header(headers, "X-RFID-Timestamp")
        nonce = _get_unique_header(headers, "X-RFID-Nonce")
        signature = _get_unique_header(headers, "X-RFID-Signature")
        if timestamp == "0" or not timestamp.isascii() or not timestamp.isdecimal():
            raise _authentication_error()
        if len(timestamp) > 1 and timestamp.startswith("0"):
            raise _authentication_error()
        timestamp_value = int(timestamp)
        current = int(time.time() if now is None else now)
        if abs(current - timestamp_value) > max_skew_seconds:
            raise _authentication_error()
        if not _NONCE_RE.fullmatch(nonce):
            raise _authentication_error()
        if not _SIGNATURE_RE.fullmatch(signature):
            raise _authentication_error()
        if not verify_signature(
            secret, signature, method, request_target, timestamp, nonce, body
        ):
            raise _authentication_error()
        replay_guard.accept(
            nonce, now=current, expires_at=timestamp_value + max_skew_seconds
        )
    except AdapterError:
        raise
    except (TypeError, ValueError, UnicodeError) as error:
        raise _authentication_error() from error


def _decode_identifier(raw: str) -> str:
    try:
        decoded = unquote_to_bytes(raw).decode("utf-8", "strict")
    except (UnicodeError, ValueError) as error:
        raise AdapterError(
            AdapterErrorCode.PROTOCOL_ERROR,
            _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR],
        ) from error
    if len(decoded) > MAX_IDENTIFIER_LENGTH or not _IDENTIFIER_RE.fullmatch(decoded):
        raise AdapterError(
            AdapterErrorCode.PROTOCOL_ERROR,
            _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR],
        )
    return decoded


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _validate_operation(value: object, devices: frozenset[str]) -> dict:
    if not isinstance(value, dict) or set(value) != _OPERATION_KEYS:
        raise AdapterError(
            AdapterErrorCode.PROTOCOL_ERROR,
            _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR],
        )
    request_id = _decode_identifier(value["request_id"])
    device_id = _decode_identifier(value["device_id"])
    operation_type = value["operation_type"]
    payload_hex = value["payload_hex"]
    payload_version = value["payload_version"]
    if operation_type != "write_and_verify":
        raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR])
    if type(payload_version) is not int or payload_version <= 0:
        raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR])
    if not isinstance(payload_hex, str) or not _PAYLOAD_RE.fullmatch(payload_hex):
        raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR])
    try:
        decoded_payload = bytes.fromhex(payload_hex)
    except ValueError as error:
        raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR]) from error
    if len(decoded_payload) != 24:
        raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR])
    if device_id not in devices:
        raise AdapterError(
            AdapterErrorCode.CONFIGURATION_ERROR,
            _SAFE_MESSAGES[AdapterErrorCode.CONFIGURATION_ERROR],
        )
    return {
        "request_id": request_id,
        "operation_type": operation_type,
        "device_id": device_id,
        "payload_hex": payload_hex.upper(),
        "payload_version": payload_version,
    }


def _safe_result_value(value: object) -> object:
    if value is None or type(value) in {str, int, bool}:
        return value
    if isinstance(value, list):
        return [_safe_result_value(item) for item in value]
    if isinstance(value, dict) and set(value).issubset(_SAFE_RESULT_KEYS):
        return {key: _safe_result_value(item) for key, item in value.items()}
    raise AdapterError(
        AdapterErrorCode.DEVICE_ERROR,
        _SAFE_MESSAGES[AdapterErrorCode.DEVICE_ERROR],
    )


def _safe_result(result: object) -> dict:
    safe = _safe_result_value(result)
    if not isinstance(safe, dict):
        raise AdapterError(
            AdapterErrorCode.DEVICE_ERROR,
            _SAFE_MESSAGES[AdapterErrorCode.DEVICE_ERROR],
        )
    return safe


def make_handler(
    service: AdapterService,
    secret: bytes,
    replay_guard: ReplayGuard,
    devices: frozenset[str],
    clock: Callable[[], int | float],
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "xq-rfid-adapter"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
            del message, explain
            self._send_adapter_error(
                code,
                AdapterError(AdapterErrorCode.PROTOCOL_ERROR, _SAFE_MESSAGES[AdapterErrorCode.PROTOCOL_ERROR]),
            )

        def do_GET(self) -> None:
            self._handle("GET")

        def do_POST(self) -> None:
            self._handle("POST")

        def do_DELETE(self) -> None:
            self._handle(self.command)

        do_PUT = do_DELETE
        do_PATCH = do_DELETE
        do_OPTIONS = do_DELETE
        do_HEAD = do_DELETE
        do_TRACE = do_DELETE
        do_CONNECT = do_DELETE

        def _read_body(self, method: str) -> bytes:
            transfer_encodings = self.headers.get_all("Transfer-Encoding", [])
            if transfer_encodings:
                raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR)
            if method != "POST":
                lengths = self.headers.get_all("Content-Length", [])
                if not lengths:
                    return b""
                if len(lengths) != 1 or lengths[0] != "0":
                    raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR)
                return b""
            lengths = self.headers.get_all("Content-Length", [])
            if not lengths:
                raise _HttpError(411, AdapterErrorCode.PROTOCOL_ERROR)
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR)
            length = int(lengths[0])
            if length > MAX_BODY_BYTES:
                raise _HttpError(413, AdapterErrorCode.CAPACITY_EXCEEDED)
            content_types = self.headers.get_all("Content-Type", [])
            if len(content_types) != 1 or content_types[0].lower() != "application/json":
                raise _HttpError(415, AdapterErrorCode.PROTOCOL_ERROR)
            body = self.rfile.read(length)
            if len(body) != length:
                raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR)
            return body

        def _json_object(self, body: bytes) -> dict:
            try:
                value = json.loads(
                    body.decode("utf-8", "strict"),
                    object_pairs_hook=_reject_duplicate_keys,
                )
            except (UnicodeError, ValueError, json.JSONDecodeError) as error:
                raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR) from error
            if not isinstance(value, dict):
                raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR)
            return value

        def _raw_request_target(self) -> str:
            try:
                request_line = self.raw_requestline.decode("iso-8859-1")
                parts = request_line.rstrip("\r\n").split(" ")
                if len(parts) != 3 or parts[0] != self.command:
                    raise ValueError("invalid request line")
                target = parts[1]
                if not target.startswith("/") or any(
                    ord(character) < 0x21 or ord(character) > 0x7E
                    for character in target
                ):
                    raise ValueError("invalid request target")
                return target
            except (UnicodeError, ValueError) as error:
                raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR) from error

        def _handle(self, method: str) -> None:
            try:
                request_target = self._raw_request_target()
                body = self._read_body(method)
                authenticate_request(
                    secret, self.headers, method, request_target, body, replay_guard,
                    now=clock(),
                )
                result, request_id = self._route(method, self.path, body)
                self._send_json(
                    200, success_envelope(_safe_result(result), request_id=request_id)
                )
            except _HttpError as error:
                self._send_adapter_error(error.status, error.adapter_error)
            except AdapterError as error:
                status = 401 if error.code is AdapterErrorCode.AUTHENTICATION_ERROR else 400
                self._send_adapter_error(status, error)
            except Exception:
                self._send_adapter_error(
                    500,
                    AdapterError(AdapterErrorCode.DEVICE_ERROR, _SAFE_MESSAGES[AdapterErrorCode.DEVICE_ERROR]),
                )

        def _route(
            self, method: str, normalized_target: str, body: bytes
        ) -> tuple[dict, str | None]:
            if method not in {"GET", "POST"}:
                raise _HttpError(405, AdapterErrorCode.PROTOCOL_ERROR)
            path = normalized_target.split("?", 1)[0]
            if path.startswith("/v1/devices/"):
                suffix = path[len("/v1/devices/"):]
                if suffix.endswith("/test-connection"):
                    if method != "POST":
                        raise _HttpError(405, AdapterErrorCode.PROTOCOL_ERROR)
                    raw_id = suffix[:-len("/test-connection")]
                    device_id = _decode_identifier(raw_id)
                    if self._json_object(body):
                        raise _HttpError(400, AdapterErrorCode.PROTOCOL_ERROR)
                    self._require_device(device_id)
                    return service.test_connection(device_id), None
                if method != "GET":
                    raise _HttpError(405, AdapterErrorCode.PROTOCOL_ERROR)
                device_id = _decode_identifier(suffix)
                self._require_device(device_id)
                return service.get_device(device_id), None
            if path == "/v1/operations":
                if method != "POST":
                    raise _HttpError(405, AdapterErrorCode.PROTOCOL_ERROR)
                request = _validate_operation(self._json_object(body), devices)
                return service.submit_operation(request), request["request_id"]
            if path.startswith("/v1/operations/"):
                if method != "GET":
                    raise _HttpError(405, AdapterErrorCode.PROTOCOL_ERROR)
                request_id = _decode_identifier(path[len("/v1/operations/"):])
                return service.get_operation(request_id), request_id
            raise _HttpError(404, AdapterErrorCode.PROTOCOL_ERROR)

        def _require_device(self, device_id: str) -> None:
            if device_id not in devices:
                raise _HttpError(404, AdapterErrorCode.CONFIGURATION_ERROR)

        def _send_adapter_error(self, status: int, error: AdapterError) -> None:
            message = _SAFE_MESSAGES.get(
                error.code, _SAFE_MESSAGES[AdapterErrorCode.DEVICE_ERROR]
            )
            code = error.code if error.code in _SAFE_MESSAGES else AdapterErrorCode.DEVICE_ERROR
            safe = AdapterError(code, message, retryable=error.retryable)
            self._send_json(status, error_envelope(safe))

        def _send_json(self, status: int, envelope: dict) -> None:
            payload = json.dumps(envelope, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

    return Handler


class _HttpError(Exception):
    def __init__(self, status: int, code: AdapterErrorCode) -> None:
        self.status = status
        self.adapter_error = AdapterError(code, _SAFE_MESSAGES[code])
        super().__init__(status)


class AdapterHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, replay_guard: ReplayGuard, **kwargs) -> None:
        self.replay_guard = replay_guard
        super().__init__(*args, **kwargs)

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            guard = self.replay_guard
            if guard is not None:
                self.replay_guard = None
                guard.close()


def create_server(
    address: tuple[str, int],
    service: AdapterService,
    secret: bytes,
    sqlite_path: str | Path,
    devices: frozenset[str],
    *,
    clock: Callable[[], int | float] = time.time,
) -> AdapterHttpServer:
    replay_guard = ReplayGuard(sqlite_path)
    handler = make_handler(service, secret, replay_guard, devices, clock)
    try:
        return AdapterHttpServer(address, handler, replay_guard=replay_guard)
    except BaseException:
        replay_guard.close()
        raise
