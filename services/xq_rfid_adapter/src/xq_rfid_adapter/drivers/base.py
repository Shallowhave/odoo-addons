"""Hardware-neutral contract implemented by RFID device drivers."""

from typing import Protocol, runtime_checkable

from xq_rfid_adapter.domain import (
    MemoryBank,
    TagObservation,
    TagTarget,
    WriteMemoryBank,
)


@runtime_checkable
class Driver(Protocol):
    """Structural interface for an RFID device driver."""

    def test_connection(self) -> dict: ...

    def get_device_info(self) -> dict: ...

    def inventory(
        self,
        duration_ms: int,
        include_tid: bool,
    ) -> list[TagObservation]: ...

    def read_memory(
        self,
        target: TagTarget,
        bank: MemoryBank,
        word_offset: int,
        word_count: int,
    ) -> bytes: ...

    def write_memory(
        self,
        target: TagTarget,
        bank: WriteMemoryBank,
        word_offset: int,
        payload: bytes,
    ) -> dict: ...

    def close(self) -> None: ...
