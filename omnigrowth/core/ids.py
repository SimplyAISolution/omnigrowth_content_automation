"""Identifiers: UUIDv7 (time-ordered, GLOSSARY §1) and correlation ids."""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7: 48-bit unix ms timestamp, version 7, variant 10, 74 random bits."""
    unix_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")
    value = (unix_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= ((rand >> 62) & 0xFFF) << 64  # rand_a: 12 bits
    value |= 0b10 << 62
    value |= rand & ((1 << 62) - 1)  # rand_b: 62 bits
    return uuid.UUID(int=value)


def new_correlation_id() -> str:
    return uuid7().hex
