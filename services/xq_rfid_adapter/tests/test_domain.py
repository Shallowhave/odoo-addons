import inspect
import math
import unittest
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from typing import get_type_hints

from xq_rfid_adapter.domain import (
    AdapterError,
    AdapterErrorCode,
    DeviceCapabilities,
    MemoryBank,
    TagObservation,
    WriteMemoryBank,
    TagTarget,
    error_envelope,
    success_envelope,
)
from xq_rfid_adapter.drivers.base import Driver


class TestAdapterError(unittest.TestCase):
    def test_error_codes_are_exact(self):
        self.assertEqual(
            [code.value for code in AdapterErrorCode],
            [
                "configuration_error",
                "authentication_error",
                "connection_error",
                "timeout",
                "protocol_error",
                "device_error",
                "no_tag",
                "multiple_tags",
                "target_changed",
                "unsupported_memory",
                "capacity_exceeded",
                "write_uncertain",
                "verification_failed",
            ],
        )

    def test_error_envelope_keeps_safe_device_code(self):
        error = AdapterError(AdapterErrorCode.NO_TAG, "no tag", device_code="0x12")
        self.assertEqual(
            error.to_dict(),
            {
                "code": "no_tag",
                "message": "no tag",
                "device_code": "0x12",
                "retryable": False,
            },
        )

    def test_only_connection_and_timeout_are_retryable_by_default(self):
        for code in AdapterErrorCode:
            with self.subTest(code=code):
                expected = code in {
                    AdapterErrorCode.CONNECTION_ERROR,
                    AdapterErrorCode.TIMEOUT,
                }
                self.assertIs(AdapterError(code, "safe message").retryable, expected)

    def test_retryability_can_be_explicitly_overridden(self):
        error = AdapterError(
            AdapterErrorCode.DEVICE_ERROR,
            "busy",
            retryable=True,
        )
        self.assertTrue(error.retryable)

    def test_error_rejects_untyped_or_empty_public_fields(self):
        with self.assertRaises(TypeError):
            AdapterError("no_tag", "no tag")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            AdapterError(AdapterErrorCode.NO_TAG, "")
        with self.assertRaises(ValueError):
            AdapterError(AdapterErrorCode.NO_TAG, "no tag", device_code="")
        with self.assertRaises(TypeError):
            AdapterError(AdapterErrorCode.NO_TAG, "no tag", retryable=1)  # type: ignore[arg-type]

    def test_error_public_state_is_read_only_after_construction(self):
        error = AdapterError(AdapterErrorCode.NO_TAG, "no tag", device_code="0x12")
        expected = error.to_dict()

        for name, value in {
            "code": AdapterErrorCode.DEVICE_ERROR,
            "message": "raw frame: secret",
            "device_code": "raw-secret",
            "retryable": True,
            "args": ("raw frame: secret",),
            "raw_frame": b"secret",
            "traceback": "secret",
        }.items():
            with self.subTest(name=name), self.assertRaises((AttributeError, TypeError)):
                setattr(error, name, value)

        self.assertEqual(error.to_dict(), expected)

    def test_error_instance_dictionary_cannot_mutate_or_attach_state(self):
        error = AdapterError(AdapterErrorCode.NO_TAG, "no tag")

        self.assertIsInstance(error.__dict__, MappingProxyType)
        with self.assertRaises(TypeError):
            error.__dict__["raw_frame"] = b"secret"
        with self.assertRaises((AttributeError, TypeError)):
            error.__dict__ = {"raw_frame": b"secret"}

        self.assertEqual(
            error.to_dict(),
            {
                "code": "no_tag",
                "message": "no tag",
                "device_code": None,
                "retryable": False,
            },
        )

    def test_error_remains_raiseable_and_catchable(self):
        error = AdapterError(AdapterErrorCode.TIMEOUT, "timed out")

        with self.assertRaises(AdapterError) as caught:
            raise error

        self.assertIs(caught.exception, error)
        self.assertEqual(str(error), "timed out")


class TestTagValues(unittest.TestCase):
    def test_target_normalizes_hex_case(self):
        target = TagTarget(epc="a1b2", tid="00ff")
        self.assertEqual(target.epc, "A1B2")
        self.assertEqual(target.tid, "00FF")

    def test_target_requires_strict_even_nonempty_hex(self):
        invalid_epcs = ["", "A", "GG", "0x12", "AA BB", " AA", "AA "]
        for epc in invalid_epcs:
            with self.subTest(epc=epc), self.assertRaises(ValueError):
                TagTarget(epc=epc)

        for tid in ["", "1", "zz", "0x12", "AA-BB"]:
            with self.subTest(tid=tid), self.assertRaises(ValueError):
                TagTarget(epc="AABB", tid=tid)

    def test_target_rejects_non_string_identity(self):
        with self.assertRaises(TypeError):
            TagTarget(epc=b"AABB")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            TagTarget(epc="AABB", tid=b"0011")  # type: ignore[arg-type]

    def test_target_is_immutable_and_hashable(self):
        target = TagTarget("AABB", "0011")
        self.assertEqual({target}, {TagTarget("aabb", "0011")})
        with self.assertRaises(FrozenInstanceError):
            target.epc = "CCDD"  # type: ignore[misc]

    def test_observation_preserves_valid_optional_metadata(self):
        observation = TagObservation(TagTarget("AABB"), antenna=2, rssi=-47.5)
        self.assertEqual(observation.antenna, 2)
        self.assertEqual(observation.rssi, -47.5)
        self.assertEqual(hash(observation), hash(TagObservation(TagTarget("aabb"), 2, -47.5)))

    def test_observation_rejects_invalid_target_or_metadata(self):
        with self.assertRaises(TypeError):
            TagObservation("AABB")  # type: ignore[arg-type]
        for antenna in [-1, 1.5, True]:
            with self.subTest(antenna=antenna), self.assertRaises((TypeError, ValueError)):
                TagObservation(TagTarget("AABB"), antenna=antenna)  # type: ignore[arg-type]
        for rssi in [math.inf, math.nan, "-40", True]:
            with self.subTest(rssi=rssi), self.assertRaises((TypeError, ValueError)):
                TagObservation(TagTarget("AABB"), rssi=rssi)  # type: ignore[arg-type]


class TestMemoryAndCapabilities(unittest.TestCase):
    def test_memory_banks_are_exact_and_exclude_reserved(self):
        self.assertEqual(
            [bank.value for bank in MemoryBank],
            ["epc", "tid", "user"],
        )
        with self.assertRaises(ValueError):
            MemoryBank("reserved")

    def test_write_memory_bank_allows_only_user_bank(self):
        self.assertEqual([bank.value for bank in WriteMemoryBank], ["user"])
        self.assertIs(WriteMemoryBank("user"), WriteMemoryBank.USER)
        for bank in ["epc", "tid", "reserved"]:
            with self.subTest(bank=bank), self.assertRaises(ValueError):
                WriteMemoryBank(bank)

    def test_capabilities_are_explicit_immutable_booleans(self):
        capabilities = DeviceCapabilities(
            epc_read=True,
            epc_write=False,
            tid_read=True,
            tid_write=False,
            user_read=True,
            user_write=True,
        )
        self.assertEqual(
            hash(capabilities),
            hash(DeviceCapabilities(True, False, True, False, True, True)),
        )
        with self.assertRaises(FrozenInstanceError):
            capabilities.user_write = False  # type: ignore[misc]

    def test_capabilities_reject_truthy_non_booleans(self):
        with self.assertRaises(TypeError):
            DeviceCapabilities(True, False, True, False, True, 1)  # type: ignore[arg-type]


class TestResultEnvelope(unittest.TestCase):
    def test_success_envelope_has_fixed_shape(self):
        result = {"connected": True}
        self.assertEqual(
            success_envelope(result, request_id="req-1"),
            {
                "ok": True,
                "request_id": "req-1",
                "result": {"connected": True},
                "error": None,
            },
        )

    def test_error_envelope_has_fixed_shape(self):
        error = AdapterError(AdapterErrorCode.TIMEOUT, "device timed out")
        self.assertEqual(
            error_envelope(error),
            {
                "ok": False,
                "request_id": None,
                "result": None,
                "error": {
                    "code": "timeout",
                    "message": "device timed out",
                    "device_code": None,
                    "retryable": True,
                },
            },
        )

    def test_envelopes_reject_invalid_boundary_values(self):
        with self.assertRaises(TypeError):
            success_envelope([])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            success_envelope({}, request_id=1)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            success_envelope({}, request_id="")
        with self.assertRaises(TypeError):
            error_envelope(ValueError("unsafe"))  # type: ignore[arg-type]


class TestDriverProtocol(unittest.TestCase):
    def test_method_signatures_and_type_hints_are_exact(self):
        expected = {
            "test_connection": (
                ["self"],
                {},
                dict,
            ),
            "get_device_info": (
                ["self"],
                {},
                dict,
            ),
            "inventory": (
                ["self", "duration_ms", "include_tid"],
                {"duration_ms": int, "include_tid": bool},
                list[TagObservation],
            ),
            "read_memory": (
                ["self", "target", "bank", "word_offset", "word_count"],
                {
                    "target": TagTarget,
                    "bank": MemoryBank,
                    "word_offset": int,
                    "word_count": int,
                },
                bytes,
            ),
            "write_memory": (
                ["self", "target", "bank", "word_offset", "payload"],
                {
                    "target": TagTarget,
                    "bank": WriteMemoryBank,
                    "word_offset": int,
                    "payload": bytes,
                },
                dict,
            ),
            "close": (
                ["self"],
                {},
                type(None),
            ),
        }

        for method_name, (parameter_names, parameter_hints, return_hint) in expected.items():
            with self.subTest(method=method_name):
                method = getattr(Driver, method_name)
                signature = inspect.signature(method)
                hints = get_type_hints(method)
                self.assertEqual(list(signature.parameters), parameter_names)
                self.assertEqual(
                    {name: hints[name] for name in parameter_names if name != "self"},
                    parameter_hints,
                )
                self.assertEqual(hints["return"], return_hint)

    def test_fake_driver_structurally_conforms_at_runtime(self):
        class FakeDriver:
            def test_connection(self) -> dict:
                return {"connected": True}

            def get_device_info(self) -> dict:
                return {"model": "fake"}

            def inventory(self, duration_ms: int, include_tid: bool) -> list[TagObservation]:
                return []

            def read_memory(
                self,
                target: TagTarget,
                bank: MemoryBank,
                word_offset: int,
                word_count: int,
            ) -> bytes:
                return b""

            def write_memory(
                self,
                target: TagTarget,
                bank: WriteMemoryBank,
                word_offset: int,
                payload: bytes,
            ) -> dict:
                return {"written": True}

            def close(self) -> None:
                return None

        self.assertIsInstance(FakeDriver(), Driver)


if __name__ == "__main__":
    unittest.main()
