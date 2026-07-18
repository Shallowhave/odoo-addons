# -*- coding: utf-8 -*-

import struct
from uuid import UUID
import zlib


MAGIC = b"XQ"
PAYLOAD_VERSION = 1
PAYLOAD_SIZE = 24
_BODY_SIZE = PAYLOAD_SIZE - 4


def encode_payload(token, flags=0):
    """Encode a UUID token and one-byte flags into the RFID payload format."""
    if isinstance(token, UUID):
        parsed_token = token
    elif isinstance(token, str):
        parsed_token = UUID(token)
    else:
        raise TypeError("token must be a UUID or string")

    if isinstance(flags, bool) or not isinstance(flags, int):
        raise TypeError("flags must be an integer")
    if not 0 <= flags <= 0xFF:
        raise ValueError("flags must fit one byte")

    body = MAGIC + bytes((PAYLOAD_VERSION, flags)) + parsed_token.bytes
    checksum = zlib.crc32(body) & 0xFFFFFFFF
    return body + struct.pack(">I", checksum)


def decode_payload(payload):
    """Validate and decode an RFID payload into its version, flags, and token."""
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if len(payload) != PAYLOAD_SIZE:
        raise ValueError("payload must be exactly 24 bytes")

    body, raw_checksum = payload[:_BODY_SIZE], payload[_BODY_SIZE:]
    if body[:2] != MAGIC or body[2] != PAYLOAD_VERSION:
        raise ValueError("unsupported RFID payload")

    expected_checksum = zlib.crc32(body) & 0xFFFFFFFF
    actual_checksum = struct.unpack(">I", raw_checksum)[0]
    if actual_checksum != expected_checksum:
        raise ValueError("invalid RFID payload CRC32")

    return {
        "version": body[2],
        "flags": body[3],
        "token": UUID(bytes=body[4:_BODY_SIZE]),
    }
