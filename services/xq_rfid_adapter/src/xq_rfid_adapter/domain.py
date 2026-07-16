"""Strict, serializable values shared by adapter drivers and callers."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from enum import Enum
from typing import TypedDict


class AdapterErrorCode(str, Enum):
    """Stable public error codes exposed by the adapter."""

    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_ERROR = "authentication_error"
    CONNECTION_ERROR = "connection_error"
    TIMEOUT = "timeout"
    PROTOCOL_ERROR = "protocol_error"
    DEVICE_ERROR = "device_error"
    NO_TAG = "no_tag"
    MULTIPLE_TAGS = "multiple_tags"
    TARGET_CHANGED = "target_changed"
    UNSUPPORTED_MEMORY = "unsupported_memory"
    CAPACITY_EXCEEDED = "capacity_exceeded"
    WRITE_UNCERTAIN = "write_uncertain"
    VERIFICATION_FAILED = "verification_failed"


_RETRYABLE_BY_DEFAULT = frozenset(
    {AdapterErrorCode.CONNECTION_ERROR, AdapterErrorCode.TIMEOUT}
)


class ErrorPayload(TypedDict):
    code: str
    message: str
    device_code: str | None
    retryable: bool


class ResultEnvelope(TypedDict):
    ok: bool
    request_id: str | None
    result: dict | None
    error: ErrorPayload | None


class AdapterError(Exception):
    """An adapter failure with fixed, validated public serialization."""

    __slots__ = ("_state",)
    _SERIALIZED_FIELDS = frozenset({"code", "message", "device_code", "retryable"})

    def __init__(
        self,
        code: AdapterErrorCode,
        message: str,
        *,
        device_code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        if not isinstance(code, AdapterErrorCode):
            raise TypeError("code must be an AdapterErrorCode")
        _validate_nonempty_string(message, "message")
        if device_code is not None:
            _validate_nonempty_string(device_code, "device_code")
        if retryable is not None and type(retryable) is not bool:
            raise TypeError("retryable must be a bool or None")

        resolved_retryable = (
            code in _RETRYABLE_BY_DEFAULT if retryable is None else retryable
        )
        super().__init__(message)
        object.__setattr__(
            self,
            "_state",
            (code, message, device_code, resolved_retryable),
        )

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._SERIALIZED_FIELDS:
            raise AttributeError(f"{name} is read-only")
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if name in self._SERIALIZED_FIELDS:
            raise AttributeError(f"{name} is read-only")
        super().__delattr__(name)

    @property
    def code(self) -> AdapterErrorCode:
        return self._state[0]

    @property
    def message(self) -> str:
        return self._state[1]

    @property
    def device_code(self) -> str | None:
        return self._state[2]

    @property
    def retryable(self) -> bool:
        return self._state[3]

    def __reduce__(self):
        """Reconstruct only the validated state for pickle and copy operations."""

        return (
            _restore_adapter_error,
            (self.code, self.message, self.device_code, self.retryable),
        )

    def to_dict(self) -> ErrorPayload:
        """Serialize only the fixed safe adapter error fields."""

        return {
            "code": self.code.value,
            "message": self.message,
            "device_code": self.device_code,
            "retryable": self.retryable,
        }


def _restore_adapter_error(
    code: AdapterErrorCode,
    message: str,
    device_code: str | None,
    retryable: bool,
) -> AdapterError:
    return AdapterError(
        code,
        message,
        device_code=device_code,
        retryable=retryable,
    )


class MemoryBank(str, Enum):
    """Memory banks available through the first-phase read boundary."""

    EPC = "epc"
    TID = "tid"
    USER = "user"


class WriteMemoryBank(str, Enum):
    """Memory banks accepted by the first-phase write boundary."""

    USER = "user"


def _normalize_hex(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value or len(value) % 2:
        raise ValueError(f"{field_name} must be non-empty even-length hex")
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        raise ValueError(f"{field_name} must contain only hexadecimal characters")
    return value.upper()


def _validate_nonempty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value:
        raise ValueError(f"{field_name} must not be empty")


def _validate_request_id(request_id: str | None) -> None:
    if request_id is not None:
        _validate_nonempty_string(request_id, "request_id")


@dataclass(frozen=True, slots=True)
class TagTarget:
    """Unambiguous identity of one RFID tag."""

    epc: str
    tid: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "epc", _normalize_hex(self.epc, "epc"))
        if self.tid is not None:
            object.__setattr__(self, "tid", _normalize_hex(self.tid, "tid"))


@dataclass(frozen=True, slots=True)
class TagObservation:
    """A tag identity observed during inventory with optional radio metadata."""

    target: TagTarget
    antenna: int | None = None
    rssi: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.target, TagTarget):
            raise TypeError("target must be a TagTarget")
        if self.antenna is not None:
            if type(self.antenna) is not int:
                raise TypeError("antenna must be an int or None")
            if self.antenna < 0:
                raise ValueError("antenna must not be negative")
        if self.rssi is not None:
            if isinstance(self.rssi, bool) or not isinstance(self.rssi, (int, float)):
                raise TypeError("rssi must be a real number or None")
            if not math.isfinite(self.rssi):
                raise ValueError("rssi must be finite")
            object.__setattr__(self, "rssi", float(self.rssi))


@dataclass(frozen=True, slots=True)
class DeviceCapabilities:
    """Typed memory operations supported by a device and its driver."""

    epc_read: bool
    epc_write: bool
    tid_read: bool
    tid_write: bool
    user_read: bool
    user_write: bool

    def __post_init__(self) -> None:
        for field in fields(self):
            if type(getattr(self, field.name)) is not bool:
                raise TypeError(f"{field.name} must be a bool")


def success_envelope(
    result: dict,
    *,
    request_id: str | None = None,
) -> ResultEnvelope:
    """Build the fixed successful result envelope."""

    if not isinstance(result, dict):
        raise TypeError("result must be a dict")
    _validate_request_id(request_id)
    return {
        "ok": True,
        "request_id": request_id,
        "result": dict(result),
        "error": None,
    }


def error_envelope(
    error: AdapterError,
    *,
    request_id: str | None = None,
) -> ResultEnvelope:
    """Build the fixed failure envelope from a safe adapter error."""

    if not isinstance(error, AdapterError):
        raise TypeError("error must be an AdapterError")
    _validate_request_id(request_id)
    return {
        "ok": False,
        "request_id": request_id,
        "result": None,
        "error": error.to_dict(),
    }
