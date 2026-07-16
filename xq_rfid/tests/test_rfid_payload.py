# -*- coding: utf-8 -*-

import importlib.util
from pathlib import Path
import unittest
from uuid import UUID


MODULE_PATH = Path(__file__).resolve().parents[1] / "models" / "rfid_payload.py"
SPEC = importlib.util.spec_from_file_location("xq_rfid_rfid_payload", MODULE_PATH)
rfid_payload = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rfid_payload)

decode_payload = rfid_payload.decode_payload
encode_payload = rfid_payload.encode_payload


class TestRfidPayload(unittest.TestCase):
    TOKEN = UUID("00112233-4455-6677-8899-aabbccddeeff")
    GOLDEN_PAYLOAD = bytes.fromhex(
        "5851010300112233445566778899aabbccddeeff1aecc2dd"
    )

    def test_encode_is_exactly_24_bytes_and_round_trips(self):
        payload = encode_payload(self.TOKEN, flags=3)

        self.assertEqual(len(payload), 24)
        self.assertEqual(payload[:4], b"XQ\x01\x03")
        self.assertEqual(
            decode_payload(payload),
            {"version": 1, "flags": 3, "token": self.TOKEN},
        )

    def test_encode_matches_golden_payload(self):
        self.assertEqual(encode_payload(self.TOKEN, flags=3), self.GOLDEN_PAYLOAD)

    def test_uuid_string_input_is_accepted(self):
        self.assertEqual(
            encode_payload(str(self.TOKEN)),
            encode_payload(self.TOKEN),
        )

    def test_flag_boundaries_are_accepted(self):
        self.assertEqual(decode_payload(encode_payload(self.TOKEN, 0))["flags"], 0)
        self.assertEqual(decode_payload(encode_payload(self.TOKEN, 255))["flags"], 255)

    def test_flags_outside_one_byte_are_rejected(self):
        for flags in (-1, 256):
            with self.subTest(flags=flags):
                with self.assertRaisesRegex(ValueError, "flags must fit one byte"):
                    encode_payload(self.TOKEN, flags)

    def test_non_integer_flags_are_rejected(self):
        for flags in (True, 1.0, "1", None):
            with self.subTest(flags=flags):
                with self.assertRaisesRegex(TypeError, "flags must be an integer"):
                    encode_payload(self.TOKEN, flags)

    def test_invalid_token_type_is_rejected(self):
        for token in (None, 1, b"00112233-4455-6677-8899-aabbccddeeff"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(TypeError, "token must be a UUID or string"):
                    encode_payload(token)

    def test_wrong_payload_type_is_rejected(self):
        for payload in (bytearray(24), memoryview(bytes(24)), "x" * 24, None):
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(TypeError, "payload must be bytes"):
                    decode_payload(payload)

    def test_wrong_payload_length_is_rejected(self):
        for payload in (b"", bytes(23), bytes(25), bytes(1024)):
            with self.subTest(length=len(payload)):
                with self.assertRaisesRegex(ValueError, "exactly 24 bytes"):
                    decode_payload(payload)

    def test_wrong_magic_is_rejected(self):
        payload = bytearray(self.GOLDEN_PAYLOAD)
        payload[:2] = b"NO"

        with self.assertRaisesRegex(ValueError, "unsupported RFID payload"):
            decode_payload(bytes(payload))

    def test_wrong_version_is_rejected(self):
        payload = bytearray(self.GOLDEN_PAYLOAD)
        payload[2] = 2

        with self.assertRaisesRegex(ValueError, "unsupported RFID payload"):
            decode_payload(bytes(payload))

    def test_corrupt_crc_is_rejected(self):
        payload = bytearray(encode_payload(UUID(int=1)))
        payload[10] ^= 1

        with self.assertRaisesRegex(ValueError, "CRC32"):
            decode_payload(bytes(payload))


if __name__ == "__main__":
    unittest.main()
