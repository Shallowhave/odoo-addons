"""Bounded per-device workers for serialized RFID driver access."""

from __future__ import annotations

import queue
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Mapping

from .domain import AdapterError, AdapterErrorCode, DeviceCapabilities
from .drivers.base import Driver
from .service import RfidService
from .store import OperationStore, StoreError

_MAX_WORKERS = 64
_MAX_DIAGNOSTICS = 8
_MAX_POLL_LIMIT = 100
_MAX_RECOVERY_BATCH = 100
_OWNER_RE = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")
_SAFE_MESSAGES = {
    "invalid_configuration": "device queue configuration is invalid",
    "too_many_devices": "too many RFID devices are configured",
    "unknown_device": "RFID device is not configured",
    "diagnostic_overload": "device diagnostic queue is full",
    "diagnostic_timeout": "device diagnostic timed out",
    "queue_closed": "device queue is closed",
    "diagnostic_failed": "device diagnostic failed",
}


class QueueError(Exception):
    """Fixed-code queue error that does not expose worker exception details."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _SAFE_MESSAGES:
            code = "diagnostic_failed"
        self.code = code
        super().__init__(_SAFE_MESSAGES[code])


@dataclass
class _Diagnostic:
    method_name: str
    done: threading.Event = field(default_factory=threading.Event)
    result: object = None
    error: BaseException | None = None


class _DeviceWorker:
    def __init__(
        self,
        device_id: str,
        driver: Driver,
        service: RfidService,
        store: OperationStore,
        owner_id: str,
        *,
        poll_interval: float,
        poll_limit: int,
        recovery_batch: int,
        max_diagnostics: int,
    ) -> None:
        self.device_id = device_id
        self.driver = driver
        self.service = service
        self.store = store
        self.owner_id = owner_id
        self.poll_interval = poll_interval
        self.poll_limit = poll_limit
        self.recovery_batch = recovery_batch
        self.recovery_retry_seconds = max(1.0, poll_interval * 4)
        self._next_recovery_at = 0.0
        self.wake_event = threading.Event()
        self.stop_event = threading.Event()
        self.diagnostics: queue.Queue[_Diagnostic] = queue.Queue(max_diagnostics)
        self.diagnostic_slots = threading.BoundedSemaphore(max_diagnostics)
        self.diagnostic_lock = threading.Lock()
        self.thread = threading.Thread(
            target=self._run,
            name=f"xq-rfid-{device_id}",
            daemon=True,
        )

    def start(self) -> None:
        self.wake_event.set()
        self.thread.start()

    def wake(self) -> None:
        self.wake_event.set()

    def stop(self) -> None:
        with self.diagnostic_lock:
            self.stop_event.set()
        self.wake_event.set()

    def diagnose(self, method_name: str, timeout: float) -> object:
        if self.stop_event.is_set():
            raise QueueError("queue_closed")
        if not self.diagnostic_slots.acquire(blocking=False):
            raise QueueError("diagnostic_overload")
        item = _Diagnostic(method_name)
        try:
            with self.diagnostic_lock:
                if self.stop_event.is_set():
                    raise QueueError("queue_closed")
                self.diagnostics.put_nowait(item)
        except queue.Full:
            self.diagnostic_slots.release()
            raise QueueError("diagnostic_overload") from None
        except BaseException:
            self.diagnostic_slots.release()
            raise
        self.wake()
        if not item.done.wait(timeout):
            raise QueueError("diagnostic_timeout")
        if item.error is not None:
            if isinstance(item.error, (QueueError, StoreError)):
                raise item.error
            raise QueueError("diagnostic_failed")
        return item.result

    def _recover_one_tick(self) -> bool:
        now = time.monotonic()
        if now < self._next_recovery_at:
            return False
        try:
            request_ids = self.store.uncertain_request_ids(
                self.device_id, batch_limit=self.recovery_batch
            )
        except StoreError:
            return False
        for request_id in request_ids:
            if self.stop_event.is_set():
                return False
            try:
                self.store.acquire_lease(
                    self.device_id, self.owner_id, self.service.lease_seconds
                )
            except StoreError:
                return False
            try:
                operation = self.service.recover_uncertain(request_id)
            except StoreError:
                return False
            except BaseException:
                return False
            self._release_idle_lease()
            if (
                operation.get("state") == "failed_retryable"
                and (operation.get("error") or {}).get("code")
                == AdapterErrorCode.WRITE_UNCERTAIN.value
            ):
                self._next_recovery_at = (
                    time.monotonic() + self.recovery_retry_seconds
                )
                return False
        self._next_recovery_at = 0.0
        return True

    def _process_operations(self) -> None:
        for _ in range(self.poll_limit):
            if self.stop_event.is_set():
                return
            try:
                operation = self.service.process_operation(self.device_id)
            except AdapterError as error:
                if error.code in {
                    AdapterErrorCode.AUTHENTICATION_ERROR,
                    AdapterErrorCode.CONFIGURATION_ERROR,
                }:
                    return
                return
            except StoreError:
                return
            except BaseException:
                return
            if operation is None:
                self._release_idle_lease()
                return
            self._release_idle_lease()

    def _process_diagnostics(self) -> None:
        while not self.stop_event.is_set():
            try:
                item = self.diagnostics.get_nowait()
            except queue.Empty:
                return
            try:
                self.store.acquire_lease(
                    self.device_id, self.owner_id, self.service.lease_seconds
                )
                method: Callable[[], object] = getattr(self.driver, item.method_name)
                item.result = self.service.call_driver(
                    self.device_id, method
                )
            except BaseException as error:
                item.error = error
            finally:
                self._release_idle_lease()
                item.done.set()
                self.diagnostic_slots.release()
                self.diagnostics.task_done()

    def _release_idle_lease(self) -> None:
        try:
            self.store.release_lease(self.device_id, self.owner_id)
        except StoreError:
            pass

    def _cancel_diagnostics(self) -> None:
        while True:
            try:
                item = self.diagnostics.get_nowait()
            except queue.Empty:
                return
            item.error = QueueError("queue_closed")
            item.done.set()
            self.diagnostic_slots.release()
            self.diagnostics.task_done()

    def _run(self) -> None:
        try:
            while not self.stop_event.is_set():
                self.wake_event.wait(self.poll_interval)
                self.wake_event.clear()
                if self.stop_event.is_set():
                    break
                recovery_safe = self._recover_one_tick()
                if recovery_safe:
                    self._process_operations()
                self._process_diagnostics()
        finally:
            self._cancel_diagnostics()
            try:
                self.driver.close()
            except BaseException:
                pass


class DeviceQueue:
    """One daemon worker and one coalescing operation wake token per device."""

    def __init__(
        self,
        store: OperationStore,
        drivers: Mapping[str, Driver],
        *,
        capabilities: Mapping[str, DeviceCapabilities],
        owner_id: str | None = None,
        lease_seconds: int = 30,
        boundary_retry_seconds: int = 60,
        max_diagnostics: int = _MAX_DIAGNOSTICS,
        recovery_batch: int = 8,
        poll_limit: int = 8,
        poll_interval: float = 0.25,
        diagnostic_timeout: float = 5.0,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if not isinstance(store, OperationStore) or not isinstance(drivers, Mapping):
            raise QueueError("invalid_configuration")
        copied_drivers = dict(drivers)
        copied_capabilities = dict(capabilities)
        if len(copied_drivers) > _MAX_WORKERS:
            raise QueueError("too_many_devices")
        if (
            not copied_drivers
            or set(copied_capabilities) != set(copied_drivers)
            or any(
                not isinstance(device_id, str) or _OWNER_RE.fullmatch(device_id) is None
                for device_id in copied_drivers
            )
            or any(
                not isinstance(value, DeviceCapabilities)
                for value in copied_capabilities.values()
            )
        ):
            raise QueueError("invalid_configuration")
        if owner_id is None:
            owner_id = "adapter-" + secrets.token_hex(16)
        if not isinstance(owner_id, str) or _OWNER_RE.fullmatch(owner_id) is None:
            raise QueueError("invalid_configuration")
        if (
            type(lease_seconds) is not int or lease_seconds <= 0
            or type(boundary_retry_seconds) is not int
            or not 1 <= boundary_retry_seconds <= 86_400
            or type(max_diagnostics) is not int or not 1 <= max_diagnostics <= _MAX_DIAGNOSTICS
            or type(recovery_batch) is not int or not 1 <= recovery_batch <= _MAX_RECOVERY_BATCH
            or type(poll_limit) is not int or not 1 <= poll_limit <= _MAX_POLL_LIMIT
            or isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) or poll_interval <= 0
            or isinstance(diagnostic_timeout, bool) or not isinstance(diagnostic_timeout, (int, float)) or diagnostic_timeout <= 0
            or isinstance(shutdown_timeout, bool) or not isinstance(shutdown_timeout, (int, float)) or shutdown_timeout < 0
        ):
            raise QueueError("invalid_configuration")
        self._store = store
        self._drivers = MappingProxyType(copied_drivers)
        self._owner_id = owner_id
        self._diagnostic_timeout = float(diagnostic_timeout)
        self._shutdown_timeout = float(shutdown_timeout)
        self._lock = threading.RLock()
        self._close_leader_lock = threading.Lock()
        self._closing_event = threading.Event()
        self._closed = False
        self._close_complete = threading.Event()
        self._cancellation_event = threading.Event()
        self._service = RfidService(
            store,
            copied_drivers,
            owner_id=owner_id,
            capabilities=copied_capabilities,
            lease_seconds=lease_seconds,
            boundary_retry_seconds=boundary_retry_seconds,
            wake_device=self.wake,
            cancellation_event=self._cancellation_event,
        )
        self._workers = {
            device_id: _DeviceWorker(
                device_id,
                driver,
                self._service,
                store,
                owner_id,
                poll_interval=float(poll_interval),
                poll_limit=poll_limit,
                recovery_batch=recovery_batch,
                max_diagnostics=max_diagnostics,
            )
            for device_id, driver in copied_drivers.items()
        }
        try:
            store.recover_expired_claims(batch_limit=recovery_batch)
        except StoreError:
            pass
        for worker in self._workers.values():
            worker.start()

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def worker_threads(self) -> tuple[threading.Thread, ...]:
        return tuple(worker.thread for worker in self._workers.values())

    def _worker(self, device_id: str) -> _DeviceWorker:
        try:
            return self._workers[device_id]
        except (KeyError, TypeError):
            raise QueueError("unknown_device") from None

    def wake(self, device_id: str) -> None:
        with self._lock:
            if self._closing_event.is_set() or self._closed:
                return
            worker = self._worker(device_id)
        worker.wake()

    def submit_operation(self, request: dict) -> dict:
        with self._lock:
            if self._closing_event.is_set() or self._closed:
                raise QueueError("queue_closed")
            return self._service.submit_operation(request)

    def submit_write_and_verify(self, request: dict) -> dict:
        return self.submit_operation(request)

    def get_operation(self, request_id: str) -> dict:
        return self._service.get_operation(request_id)

    def test_connection(self, device_id: str) -> dict:
        return self._worker(device_id).diagnose(
            "test_connection", self._diagnostic_timeout
        )

    def get_device(self, device_id: str) -> dict:
        return self._worker(device_id).diagnose(
            "get_device_info", self._diagnostic_timeout
        )

    def close(self) -> None:
        deadline = time.monotonic() + self._shutdown_timeout
        with self._close_leader_lock:
            leader = not self._closing_event.is_set()
            if leader:
                self._closing_event.set()
                self._cancellation_event.set()
                workers = tuple(self._workers.values())
                for worker in workers:
                    worker.stop()
        if not leader:
            self._close_complete.wait(
                max(0.0, deadline - time.monotonic())
            )
            return
        try:
            self._service.close(
                timeout=max(0.0, deadline - time.monotonic())
            )
            for worker in workers:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                worker.thread.join(remaining)
        finally:
            self._closed = True
            self._close_complete.set()
