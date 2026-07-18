"""Safe RFID write-and-verify service state machine."""

from __future__ import annotations

import binascii
import hashlib
import re
import threading
import time
from collections.abc import Mapping

from .domain import (
    AdapterError,
    AdapterErrorCode,
    DeviceCapabilities,
    MemoryBank,
    TagObservation,
    TagTarget,
    WriteMemoryBank,
)
from .drivers.base import Driver
from .store import OperationStore, StoreError

_FULL_INVENTORY_MS = 500
_RECONFIRM_INVENTORY_MS = 100
_WORD_OFFSET = 0
_WORD_COUNT = 12
_PAYLOAD_LENGTH = 24
_DEFAULT_BOUNDARY_RETRY_SECONDS = 60
_MAX_BOUNDARY_RETRY_SECONDS = 86_400
_BOUNDARY_CODES = frozenset({
    AdapterErrorCode.AUTHENTICATION_ERROR,
    AdapterErrorCode.CONFIGURATION_ERROR,
})
_UNPROVABLE_WRITE_CODES = frozenset({
    AdapterErrorCode.TIMEOUT,
    AdapterErrorCode.CONNECTION_ERROR,
    AdapterErrorCode.WRITE_UNCERTAIN,
    *_BOUNDARY_CODES,
})
_PAYLOAD_RE = re.compile(r"\A[0-9A-Fa-f]{48}\Z")
_DEVICE_CODE_RE = re.compile(r"\A[A-Za-z0-9._:-]{1,64}\Z")
_SAFE_MESSAGES = {
    AdapterErrorCode.CONFIGURATION_ERROR: "resource is not configured",
    AdapterErrorCode.AUTHENTICATION_ERROR: "authentication failed",
    AdapterErrorCode.CONNECTION_ERROR: "device connection failed",
    AdapterErrorCode.TIMEOUT: "device operation timed out",
    AdapterErrorCode.PROTOCOL_ERROR: "invalid request",
    AdapterErrorCode.DEVICE_ERROR: "internal device error",
    AdapterErrorCode.NO_TAG: "no tag was found",
    AdapterErrorCode.MULTIPLE_TAGS: "multiple tags were found",
    AdapterErrorCode.TARGET_CHANGED: "tag target changed",
    AdapterErrorCode.UNSUPPORTED_MEMORY: "memory operation is unsupported",
    AdapterErrorCode.CAPACITY_EXCEEDED: "request body is too large",
    AdapterErrorCode.WRITE_UNCERTAIN: "write outcome is uncertain",
    AdapterErrorCode.VERIFICATION_FAILED: "write verification failed",
}


def _error(code: AdapterErrorCode, *, device_code: str | None = None) -> AdapterError:
    if device_code is not None and _DEVICE_CODE_RE.fullmatch(device_code) is None:
        device_code = None
    return AdapterError(
        code,
        _SAFE_MESSAGES[code],
        device_code=device_code,
        retryable=False if code is AdapterErrorCode.WRITE_UNCERTAIN else None,
    )


def _safe_error(error: AdapterError) -> AdapterError:
    code = error.code if error.code in _SAFE_MESSAGES else AdapterErrorCode.DEVICE_ERROR
    if code in {
        AdapterErrorCode.CONFIGURATION_ERROR,
        AdapterErrorCode.AUTHENTICATION_ERROR,
    }:
        code = AdapterErrorCode.DEVICE_ERROR
    return _error(code, device_code=error.device_code)


class _UnexpectedDriverError(Exception):
    """Internal marker that deliberately discards unsafe Driver details."""


def validate_payload(request: object) -> bytes:
    try:
        if not isinstance(request, dict):
            raise ValueError
        payload_hex = request["payload_hex"]
        version = request["payload_version"]
        if (
            not isinstance(payload_hex, str)
            or _PAYLOAD_RE.fullmatch(payload_hex) is None
            or type(version) is not int
        ):
            raise ValueError
        value = bytes.fromhex(payload_hex)
        if (
            len(value) != _PAYLOAD_LENGTH
            or value[:2] != b"XQ"
            or value[2] != 1
            or version != value[2]
            or int.from_bytes(value[20:24], "big") != binascii.crc32(value[:20])
        ):
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError):
        raise _error(AdapterErrorCode.PROTOCOL_ERROR) from None


def identity_hash(target: TagTarget) -> str:
    epc = bytes.fromhex(target.epc)
    tid = b"" if target.tid is None else bytes.fromhex(target.tid)
    return hashlib.sha256(
        b"XQ-RFID-TARGET-v1\0"
        + len(epc).to_bytes(2, "big") + epc
        + len(tid).to_bytes(2, "big") + tid
    ).hexdigest()


def _identity_descriptor(value: str | None) -> dict | None:
    if value is None:
        return None
    visible = min(4, len(value) // 4)
    return {"nibble_length": len(value), "suffix": value[-visible:]}


class RfidService:
    """Synchronous facade; a device queue may call its explicit helpers."""

    def __init__(
        self,
        store: OperationStore,
        drivers: Mapping[str, Driver],
        *,
        owner_id: str,
        capabilities: Mapping[str, DeviceCapabilities],
        lease_seconds: int = 30,
        boundary_retry_seconds: int = _DEFAULT_BOUNDARY_RETRY_SECONDS,
        wake_device=None,
        cancellation_event: threading.Event | None = None,
    ) -> None:
        if not isinstance(store, OperationStore) or not isinstance(owner_id, str):
            raise TypeError("invalid service configuration")
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if (
            type(boundary_retry_seconds) is not int
            or not 1 <= boundary_retry_seconds <= _MAX_BOUNDARY_RETRY_SECONDS
        ):
            raise ValueError("boundary_retry_seconds is invalid")
        copied_drivers = dict(drivers)
        copied_capabilities = dict(capabilities)
        if (
            set(copied_capabilities) != set(copied_drivers)
            or any(
                not isinstance(value, DeviceCapabilities)
                for value in copied_capabilities.values()
            )
        ):
            raise TypeError("invalid service configuration")
        self._store = store
        self._drivers = copied_drivers
        self._capabilities = copied_capabilities
        self._owner_id = owner_id
        self._lease_seconds = lease_seconds
        self._boundary_retry_seconds = boundary_retry_seconds
        if cancellation_event is not None and not isinstance(
            cancellation_event, threading.Event
        ):
            raise TypeError("invalid service configuration")
        self._wake_device = wake_device
        self._cancellation_event = cancellation_event or threading.Event()
        self._lock = threading.Lock()
        self._driver_start_condition = threading.Condition()
        self._driver_starts = 0
        self._heartbeat_stops: set[threading.Event] = set()
        self._closed = False

    @property
    def lease_seconds(self) -> int:
        return self._lease_seconds

    def _driver(self, device_id: str) -> Driver:
        try:
            return self._drivers[device_id]
        except KeyError:
            raise _error(AdapterErrorCode.CONFIGURATION_ERROR) from None

    def _capability(self, device_id: str) -> DeviceCapabilities:
        try:
            return self._capabilities[device_id]
        except KeyError:
            raise _error(AdapterErrorCode.CONFIGURATION_ERROR) from None

    def _required_write_capability(self, device_id: str) -> DeviceCapabilities:
        capability = self._capability(device_id)
        if not (
            capability.epc_read
            and capability.user_read
            and capability.user_write
        ):
            raise _error(AdapterErrorCode.UNSUPPORTED_MEMORY)
        return capability

    def test_connection(self, device_id: str) -> dict:
        driver = self._driver(device_id)
        return self._call(device_id, driver.test_connection)

    def get_device(self, device_id: str) -> dict:
        driver = self._driver(device_id)
        return self._call(device_id, driver.get_device_info)

    def submit_operation(self, request: dict) -> dict:
        validate_payload(request)
        operation = self._mutate_store(
            self._store.create_or_get,
            request,
            cancellation_event=self._cancellation_event,
        )
        if self._wake_device is not None:
            self._wake_device(operation["device_id"])
        return self._submitted(operation)

    def submit_write_and_verify(self, request: dict) -> dict:
        return self.submit_operation(request)

    @staticmethod
    def _submitted(operation: dict) -> dict:
        return {
            "state": operation["state"],
            "request_id": operation["request_id"],
            "operation_type": operation["operation_type"],
            "payload_version": operation["payload_version"],
        }

    def get_operation(self, request_id: str) -> dict:
        operation = self._store.get(request_id)
        if operation is None:
            raise StoreError("not_found")
        result = operation["result"] or {}
        return {
            **self._submitted(operation),
            "epc_identity": result.get("epc_identity"),
            "tid_identity": result.get("tid_identity"),
            "identity_hash": (
                None if result.get("identity_hash") is None
                else "sha256:" + result["identity_hash"]
            ),
            "verification_ok": bool(result.get("verification_ok", False)),
            "retryable": bool(operation["error"] and operation["error"]["retryable"]),
            "error": operation["error"],
        }

    def _require_active_locked(self) -> None:
        if self._closed or self._cancellation_event.is_set():
            raise StoreError("lease_conflict")

    def _mutate_store(self, operation, *args, **kwargs):
        """Serialize owner mutations with close at the process boundary."""
        with self._lock:
            self._require_active_locked()
            return operation(*args, **kwargs)

    def acquire_device_lease(self, device_id: str) -> None:
        self._mutate_store(
            self._store.acquire_lease,
            device_id,
            self._owner_id,
            self._lease_seconds,
            cancellation_event=self._cancellation_event,
        )

    def _renew(self, device_id: str) -> None:
        self._mutate_store(
            self._store.renew_lease,
            device_id,
            self._owner_id,
            self._lease_seconds,
            cancellation_event=self._cancellation_event,
        )

    def call_driver(self, device_id: str, method, *args):
        return self._call(device_id, method, *args)

    def _call(self, device_id: str, method, *args):
        with self._store.device_guard(device_id, self._cancellation_event):
            stopped = threading.Event()
            lease_error = []
            interval = max(0.05, self._lease_seconds / 3)
            with self._lock:
                self._require_active_locked()
                self._store.renew_lease(
                    device_id,
                    self._owner_id,
                    self._lease_seconds,
                    cancellation_event=self._cancellation_event,
                )
                self._heartbeat_stops.add(stopped)

            def heartbeat() -> None:
                while not stopped.wait(interval):
                    try:
                        self._renew(device_id)
                    except StoreError as error:
                        lease_error.append(error)
                        return

            thread = threading.Thread(
                target=heartbeat,
                name=f"xq-rfid-lease-{device_id}",
                daemon=True,
            )
            call_error = None
            result = None
            heartbeat_started = False
            driver_invoked = False
            try:
                thread.start()
                heartbeat_started = True
                with self._driver_start_condition:
                    if self._cancellation_event.is_set():
                        raise StoreError("lease_conflict")
                    self._driver_starts += 1
                try:
                    driver_invoked = True
                    result = method(*args)
                finally:
                    with self._driver_start_condition:
                        self._driver_starts -= 1
                        self._driver_start_condition.notify_all()
            except BaseException as error:
                call_error = error
            finally:
                stopped.set()
                if heartbeat_started:
                    thread.join()
                with self._lock:
                    self._heartbeat_stops.discard(stopped)
            try:
                self._renew(device_id)
            except StoreError as error:
                if call_error is None:
                    raise
                if not self._cancellation_event.is_set():
                    raise error from None
            if lease_error:
                raise lease_error[0]
            if call_error is not None:
                if isinstance(call_error, AdapterError):
                    raise call_error
                if driver_invoked:
                    raise _UnexpectedDriverError from None
                raise call_error
            return result

    @staticmethod
    def _select_target(observations: list[TagObservation], include_tid: bool) -> TagTarget:
        if not observations:
            raise _error(AdapterErrorCode.NO_TAG)
        unique: dict[tuple[str, str | None], TagTarget] = {}
        epc_tids: dict[str, set[str | None]] = {}
        for observation in observations:
            if not isinstance(observation, TagObservation):
                raise _error(AdapterErrorCode.PROTOCOL_ERROR)
            target = observation.target
            key = (target.epc, target.tid if include_tid else None)
            unique[key] = target if include_tid else TagTarget(target.epc)
            epc_tids.setdefault(target.epc, set()).add(target.tid)
        if include_tid and any(len(values) > 1 for values in epc_tids.values()):
            raise _error(AdapterErrorCode.MULTIPLE_TAGS)
        if len(unique) != 1:
            raise _error(AdapterErrorCode.MULTIPLE_TAGS)
        target = next(iter(unique.values()))
        if include_tid and target.tid is None:
            raise _error(AdapterErrorCode.PROTOCOL_ERROR)
        return target

    def _inventory(self, device_id: str, driver: Driver, duration: int, include_tid: bool) -> TagTarget:
        observations = self._call(
            device_id, driver.inventory, duration, include_tid
        )
        return self._select_target(observations, include_tid)

    def _read(self, device_id: str, driver: Driver, target: TagTarget) -> bytes:
        value = self._call(
            device_id, driver.read_memory,
            target, MemoryBank.USER, _WORD_OFFSET, _WORD_COUNT,
        )
        if not isinstance(value, bytes) or len(value) != _PAYLOAD_LENGTH:
            raise _error(AdapterErrorCode.CAPACITY_EXCEEDED)
        return bytes(value)

    def _write(
        self,
        device_id: str,
        driver: Driver,
        target: TagTarget,
        payload: bytes,
    ) -> None:
        result = self._call(
            device_id,
            driver.write_memory,
            target,
            WriteMemoryBank.USER,
            _WORD_OFFSET,
            payload,
        )
        if (
            isinstance(result, dict)
            and set(result) == {"written"}
            and type(result["written"]) is bool
        ):
            if result["written"]:
                return
            raise _error(AdapterErrorCode.DEVICE_ERROR)
        raise _error(AdapterErrorCode.WRITE_UNCERTAIN)

    def _reconfirm_persisted_target(
        self,
        work: dict,
        driver: Driver,
        intended_hash: str,
    ) -> TagTarget:
        confirmed = self._inventory(
            work["device_id"],
            driver,
            _RECONFIRM_INVENTORY_MS,
            self._capability(work["device_id"]).tid_read,
        )
        if identity_hash(confirmed) != intended_hash:
            raise _error(AdapterErrorCode.TARGET_CHANGED)
        return confirmed

    def _defer_boundary(
        self, work: dict, expected_state: str, error: AdapterError
    ) -> None:
        self._mutate_store(
            self._store.defer_boundary,
            work["request_id"],
            self._owner_id,
            expected_state,
            self._boundary_retry_seconds,
            cancellation_event=self._cancellation_event,
        )
        raise _error(error.code, device_code=error.device_code) from None

    def _fail(self, work: dict, expected_state: str, error: AdapterError, *, uncertain=False) -> dict:
        safe = _error(AdapterErrorCode.WRITE_UNCERTAIN) if uncertain else _safe_error(error)
        retryable = safe.code in {AdapterErrorCode.CONNECTION_ERROR, AdapterErrorCode.TIMEOUT}
        if uncertain:
            retryable = True
        new_state = "failed_retryable" if retryable else "failed_manual"
        return self._mutate_store(
            self._store.transition,
            work["request_id"], self._owner_id, expected_state, new_state,
            error=safe.to_dict(),
            cancellation_event=self._cancellation_event,
        )

    def process_operation(self, device_id: str) -> dict | None:
        work = self._mutate_store(
            self._store.claim_next_work,
            device_id,
            self._owner_id,
            self._lease_seconds,
            cancellation_event=self._cancellation_event,
        )
        if work is None:
            return None
        work = {**work, "state": "inventorying"}
        self._mutate_store(
            self._store.transition,
            work["request_id"], self._owner_id, "claimed", "inventorying",
            cancellation_event=self._cancellation_event,
        )
        try:
            driver = self._driver(device_id)
            capability = self._required_write_capability(work["device_id"])
            include_tid = capability.tid_read
            target = self._inventory(device_id, driver, _FULL_INVENTORY_MS, include_tid)
            before = self._read(device_id, driver, target)
            confirmed = self._inventory(
                device_id, driver, _RECONFIRM_INVENTORY_MS, include_tid
            )
            if identity_hash(confirmed) != identity_hash(target):
                raise _error(AdapterErrorCode.TARGET_CHANGED)
            self._mutate_store(
                self._store.prepare_write,
                work["request_id"], self._owner_id, identity_hash(target),
                hashlib.sha256(before).hexdigest(),
                cancellation_event=self._cancellation_event,
            )
        except AdapterError as error:
            if error.code in _BOUNDARY_CODES:
                self._defer_boundary(work, "inventorying", error)
            return self._fail(work, "inventorying", error)
        except _UnexpectedDriverError:
            return self._fail(
                work, "inventorying", _error(AdapterErrorCode.DEVICE_ERROR)
            )
        return self._write_and_verify(work, driver, target, before, initial=True)

    def _write_and_verify(
        self, work: dict, driver: Driver, target: TagTarget, before: bytes, *, initial: bool
    ) -> dict:
        device_id = work["device_id"]
        payload = work["payload"]
        ambiguous = False
        try:
            self._write(device_id, driver, target, payload)
        except AdapterError as error:
            if error.code in _UNPROVABLE_WRITE_CODES:
                ambiguous = True
            else:
                return self._fail(work, "writing", error)
        except _UnexpectedDriverError:
            ambiguous = True
        self._mutate_store(
            self._store.transition,
            work["request_id"], self._owner_id, "writing", "verifying",
            cancellation_event=self._cancellation_event,
        )
        internal = None
        try:
            if ambiguous:
                internal = self._store.get_work_item(
                    work["request_id"], self._owner_id
                )
                target = self._reconfirm_persisted_target(
                    work, driver, internal["target_identity_hash"]
                )
            current = self._read(device_id, driver, target)
        except AdapterError as error:
            uncertain = error.code in {
                AdapterErrorCode.CONNECTION_ERROR,
                AdapterErrorCode.TIMEOUT,
                *_BOUNDARY_CODES,
            }
            return self._fail(work, "verifying", error, uncertain=uncertain)
        except _UnexpectedDriverError:
            return self._fail(
                work,
                "verifying",
                _error(AdapterErrorCode.WRITE_UNCERTAIN),
                uncertain=True,
            )
        if current == payload:
            return self._succeed(work, target)
        if ambiguous and current == before:
            if (
                internal["rewrite_count"] == 0
                and hashlib.sha256(current).hexdigest() == internal["pre_write_hash"]
            ):
                try:
                    target = self._reconfirm_persisted_target(
                        work, driver, internal["target_identity_hash"]
                    )
                except AdapterError as error:
                    return self._fail(
                        work,
                        "verifying",
                        error,
                        uncertain=error.code in _UNPROVABLE_WRITE_CODES,
                    )
                except _UnexpectedDriverError:
                    return self._fail(
                        work,
                        "verifying",
                        _error(AdapterErrorCode.WRITE_UNCERTAIN),
                        uncertain=True,
                    )
                self._mutate_store(
                    self._store.begin_controlled_rewrite,
                    work["request_id"], self._owner_id,
                    cancellation_event=self._cancellation_event,
                )
                try:
                    self._write(device_id, driver, target, payload)
                except AdapterError as error:
                    if error.code not in _UNPROVABLE_WRITE_CODES:
                        return self._fail(work, "writing", error)
                except _UnexpectedDriverError:
                    pass
                self._mutate_store(
                    self._store.transition,
                    work["request_id"], self._owner_id, "writing", "verifying",
                    cancellation_event=self._cancellation_event,
                )
                try:
                    second = self._read(device_id, driver, target)
                except AdapterError as error:
                    return self._fail(
                        work,
                        "verifying",
                        error,
                        uncertain=error.code in _UNPROVABLE_WRITE_CODES,
                    )
                except _UnexpectedDriverError:
                    return self._fail(
                        work,
                        "verifying",
                        _error(AdapterErrorCode.WRITE_UNCERTAIN),
                        uncertain=True,
                    )
                if second == payload:
                    return self._succeed(work, target)
        return self._fail(
            work, "verifying", _error(AdapterErrorCode.VERIFICATION_FAILED)
        )

    def _succeed(self, work: dict, target: TagTarget) -> dict:
        result = self._mutate_store(
            self._store.transition,
            work["request_id"], self._owner_id, "verifying", "succeeded",
            result={
                "identity_hash": identity_hash(target),
                "verification_ok": True,
                "epc_identity": _identity_descriptor(target.epc),
                "tid_identity": _identity_descriptor(target.tid),
            },
            cancellation_event=self._cancellation_event,
        )
        return result

    def recover_uncertain(self, request_id: str) -> dict:
        work = self._mutate_store(
            self._store.resume_uncertain,
            request_id,
            self._owner_id,
            self._lease_seconds,
            cancellation_event=self._cancellation_event,
        )
        driver = self._driver(work["device_id"])
        try:
            include_tid = self._required_write_capability(work["device_id"]).tid_read
        except AdapterError as error:
            return self._fail(work, "verifying", error, uncertain=True)
        try:
            target = self._inventory(
                work["device_id"], driver, _FULL_INVENTORY_MS, include_tid
            )
            if identity_hash(target) != work["target_identity_hash"]:
                raise _error(AdapterErrorCode.TARGET_CHANGED)
            current = self._read(work["device_id"], driver, target)
        except AdapterError as error:
            return self._fail(
                work,
                "verifying",
                error,
                uncertain=error.code in _UNPROVABLE_WRITE_CODES,
            )
        except _UnexpectedDriverError:
            return self._fail(
                work,
                "verifying",
                _error(AdapterErrorCode.WRITE_UNCERTAIN),
                uncertain=True,
            )
        if current == work["payload"]:
            return self._succeed(work, target)
        if (
            hashlib.sha256(current).hexdigest() == work["pre_write_hash"]
            and work["rewrite_count"] == 0
        ):
            try:
                confirmed = self._inventory(
                    work["device_id"],
                    driver,
                    _RECONFIRM_INVENTORY_MS,
                    include_tid,
                )
                if identity_hash(confirmed) != work["target_identity_hash"]:
                    raise _error(AdapterErrorCode.TARGET_CHANGED)
            except AdapterError as error:
                return self._fail(
                    work,
                    "verifying",
                    error,
                    uncertain=error.code in _UNPROVABLE_WRITE_CODES,
                )
            except _UnexpectedDriverError:
                return self._fail(
                    work,
                    "verifying",
                    _error(AdapterErrorCode.WRITE_UNCERTAIN),
                    uncertain=True,
                )
            self._mutate_store(
                self._store.begin_controlled_rewrite,
                request_id, self._owner_id,
                cancellation_event=self._cancellation_event,
            )
            return self._write_and_verify(work, driver, target, current, initial=False)
        return self._fail(
            work, "verifying", _error(AdapterErrorCode.VERIFICATION_FAILED)
        )

    def close(self, timeout: float | None = None) -> None:
        """Stop active lease heartbeats; the device queue owns driver shutdown."""
        if (
            timeout is not None
            and (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or timeout < 0
            )
        ):
            raise ValueError("timeout is invalid")
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        with self._driver_start_condition:
            self._cancellation_event.set()
            while self._driver_starts:
                if deadline is None:
                    self._driver_start_condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._driver_start_condition.wait(remaining)
        if deadline is None:
            acquired = self._lock.acquire()
        else:
            acquired = self._lock.acquire(
                timeout=max(0.0, deadline - time.monotonic())
            )
        if not acquired:
            return
        try:
            self._closed = True
            heartbeat_stops = tuple(self._heartbeat_stops)
        finally:
            self._lock.release()
        for stopped in heartbeat_stops:
            stopped.set()
