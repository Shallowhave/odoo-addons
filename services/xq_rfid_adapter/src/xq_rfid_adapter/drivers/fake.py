"""Deterministic, thread-safe fake RFID driver for adapter tests."""

from __future__ import annotations

import copy
import threading
import time
from collections import Counter, deque
from collections.abc import Iterable, Mapping

from xq_rfid_adapter.domain import (
    AdapterError,
    AdapterErrorCode,
    DeviceCapabilities,
    MemoryBank,
    TagObservation,
    TagTarget,
    WriteMemoryBank,
)


class FakeDriver:
    """Small hardware-neutral fake with scripted outcomes and memory."""

    def __init__(
        self,
        *,
        capabilities: DeviceCapabilities | None = None,
        inventory_snapshots: Iterable[Iterable[TagObservation]] = (),
        user_memory: Mapping[TagTarget, bytes] | None = None,
        capacities: Mapping[TagTarget, int] | None = None,
        scripted_errors: Mapping[str, Iterable[AdapterError | None]] | None = None,
        write_modes: Iterable[str] = (),
        block_events: Mapping[str, tuple[threading.Event, threading.Event]] | None = None,
        device_info: dict | None = None,
    ) -> None:
        self.capabilities = capabilities or DeviceCapabilities(
            True, False, True, False, True, True
        )
        if not isinstance(self.capabilities, DeviceCapabilities):
            raise TypeError("capabilities must be DeviceCapabilities")
        self.inventory_snapshots = deque(
            [tuple(copy.deepcopy(list(snapshot))) for snapshot in inventory_snapshots]
        )
        self.user_memory = {
            target: bytes(value) for target, value in (user_memory or {}).items()
        }
        self.capacities = {
            target: int(value) for target, value in (capacities or {}).items()
        }
        self.scripted_errors = {
            operation: deque(copy.deepcopy(list(values)))
            for operation, values in (scripted_errors or {}).items()
        }
        self.write_modes = list(write_modes)
        self.block_events = dict(block_events or {})
        self.device_info = copy.deepcopy(device_info) if device_info is not None else {
            "status": "connected",
            "capabilities": {
                "supports_epc": self.capabilities.epc_read,
                "supports_tid": self.capabilities.tid_read,
                "supports_user_read": self.capabilities.user_read,
                "supports_user_write": self.capabilities.user_write,
            },
            "antenna_count": 1,
            "firmware_version": {"major": 1, "minor": 0, "patch": 0},
            "hardware_version": {"major": 1, "minor": 0, "patch": 0},
            "module_version": {"major": 1, "minor": 0, "patch": 0},
            "region": "US",
        }
        self._lock = threading.RLock()
        self._records: list[dict] = []
        self._counts: Counter[str] = Counter()
        self._last_inventory: tuple[TagObservation, ...] = ()
        self._closed = False

    @property
    def call_records(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._records)

    @property
    def call_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def _before(self, operation: str, **fields: object) -> None:
        with self._lock:
            if self._closed and operation != "close":
                raise AdapterError(AdapterErrorCode.CONNECTION_ERROR, "device connection failed")
            record = {
                "operation": operation,
                "target": copy.deepcopy(fields.pop("target", None)),
                "started_at": time.monotonic(),
                "thread_id": threading.get_ident(),
                **copy.deepcopy(fields),
            }
            self._records.append(record)
            self._counts[operation] += 1
        events = self.block_events.get(operation)
        if events is not None:
            entered, release = events
            entered.set()
            release.wait()
        with self._lock:
            outcomes = self.scripted_errors.get(operation)
            if outcomes:
                if not isinstance(outcomes, deque):
                    outcomes = deque(copy.deepcopy(list(outcomes)))
                    self.scripted_errors[operation] = outcomes
                outcome = outcomes.popleft()
                if outcome is not None:
                    raise copy.copy(outcome)

    def test_connection(self) -> dict:
        self._before("test_connection")
        return {"status": "connected"}

    def get_device_info(self) -> dict:
        self._before("get_device_info")
        return copy.deepcopy(self.device_info)

    def inventory(self, duration_ms: int, include_tid: bool) -> list[TagObservation]:
        if type(duration_ms) is not int or duration_ms <= 0 or type(include_tid) is not bool:
            raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, "invalid request")
        self._before("inventory", duration_ms=duration_ms, include_tid=include_tid)
        with self._lock:
            if self.inventory_snapshots:
                snapshot = self.inventory_snapshots.popleft()
                self._last_inventory = snapshot
            else:
                snapshot = self._last_inventory
        observations = []
        for observation in snapshot:
            target = observation.target
            if not include_tid and target.tid is not None:
                target = TagTarget(target.epc)
            observations.append(TagObservation(target, observation.antenna, observation.rssi))
        return observations

    def read_memory(
        self,
        target: TagTarget,
        bank: MemoryBank,
        word_offset: int,
        word_count: int,
    ) -> bytes:
        if not isinstance(target, TagTarget) or bank is not MemoryBank.USER:
            raise AdapterError(AdapterErrorCode.UNSUPPORTED_MEMORY, "memory operation is unsupported")
        if type(word_offset) is not int or type(word_count) is not int or word_offset < 0 or word_count <= 0:
            raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, "invalid request")
        self._before(
            "read_memory", target=target, bank=bank.value,
            word_offset=word_offset, word_count=word_count,
        )
        start = word_offset * 2
        end = start + word_count * 2
        with self._lock:
            capacity = self.capacities.get(target, len(self.user_memory.get(target, b"")))
            if end > capacity:
                raise AdapterError(AdapterErrorCode.CAPACITY_EXCEEDED, "request body is too large")
            value = self.user_memory.get(target, b"")
            return bytes(value[start:end])

    def write_memory(
        self,
        target: TagTarget,
        bank: WriteMemoryBank,
        word_offset: int,
        payload: bytes,
    ) -> dict:
        if not isinstance(target, TagTarget) or bank is not WriteMemoryBank.USER:
            raise AdapterError(AdapterErrorCode.UNSUPPORTED_MEMORY, "memory operation is unsupported")
        if type(word_offset) is not int or word_offset < 0 or not isinstance(payload, bytes) or not payload or len(payload) % 2:
            raise AdapterError(AdapterErrorCode.PROTOCOL_ERROR, "invalid request")
        payload = bytes(payload)
        self._before(
            "write_memory", target=target, bank=bank.value,
            word_offset=word_offset, byte_count=len(payload),
        )
        start = word_offset * 2
        end = start + len(payload)
        with self._lock:
            capacity = self.capacities.get(target, len(self.user_memory.get(target, b"")))
            if end > capacity:
                raise AdapterError(AdapterErrorCode.CAPACITY_EXCEEDED, "request body is too large")
            mode = self.write_modes.pop(0) if self.write_modes else "apply_and_return"
            current = bytearray(self.user_memory.get(target, b"\x00" * capacity))
            if len(current) < capacity:
                current.extend(b"\x00" * (capacity - len(current)))
            if mode in {"apply_and_return", "apply_then_timeout"}:
                current[start:end] = payload
                self.user_memory[target] = bytes(current)
            elif mode == "partial_apply_then_timeout":
                midpoint = start + max(1, len(payload) // 2)
                current[start:midpoint] = payload[: midpoint - start]
                self.user_memory[target] = bytes(current)
            elif mode != "no_apply_then_timeout":
                raise AdapterError(AdapterErrorCode.DEVICE_ERROR, "internal device error")
        if mode.endswith("timeout"):
            raise AdapterError(AdapterErrorCode.TIMEOUT, "device operation timed out")
        return {"written": True}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._before("close")
            self._closed = True
