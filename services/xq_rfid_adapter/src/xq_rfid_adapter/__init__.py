"""Domain contract for the XQ RFID adapter service."""

from .domain import (
    AdapterError,
    AdapterErrorCode,
    DeviceCapabilities,
    MemoryBank,
    TagObservation,
    TagTarget,
    WriteMemoryBank,
    error_envelope,
    success_envelope,
)

__all__ = [
    "AdapterError",
    "AdapterErrorCode",
    "DeviceCapabilities",
    "MemoryBank",
    "TagObservation",
    "TagTarget",
    "WriteMemoryBank",
    "error_envelope",
    "success_envelope",
]
