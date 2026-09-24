"""Shared helpers: categorized error constructors, size checks and text decoding."""

from __future__ import annotations

import codecs
import unicodedata

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.ingestion.limits import IngestionLimits

MAX_BAD_CHAR_RATIO = 0.05
_ALLOWED_CONTROLS = frozenset("\n\r\t\f\v")

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),  # must precede UTF-16 LE (shares the FF FE prefix)
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def validation_error(message: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.VALIDATION_FAILURE, message, details=dict(details))


def ingestion_error(message: str, reason: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.INGESTION_FAILURE, message, details={"reason": reason, **details}
    )


def limit_error(name: str, limit: int, actual: int | None = None) -> OmniGrowthError:
    details: dict[str, object] = {"limit": name, "max": limit}
    if actual is not None:
        details["actual"] = actual
    return OmniGrowthError(
        ErrorCode.VALIDATION_FAILURE, f"ingestion limit exceeded: {name}", details=details
    )


def mismatch_error(**details: object) -> OmniGrowthError:
    return validation_error("content does not match declared type", **details)


def check_size(data: bytes, limits: IngestionLimits) -> None:
    if not data:
        raise validation_error("empty", reason="EMPTY")
    if len(data) > limits.max_bytes:
        raise limit_error("max_bytes", limits.max_bytes, len(data))


def detect_bom(data: bytes) -> str | None:
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            return encoding
    return None


def bad_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = sum(
        1
        for ch in text
        if ch in ("\ufffd", "\x00")
        or (ch not in _ALLOWED_CONTROLS and unicodedata.category(ch) == "Cc")
    )
    return bad / len(text)


def _decode_raw(data: bytes, *, final: bool) -> tuple[str, str]:
    """Return (text, encoding). ``final=False`` tolerates a truncated trailing sequence."""
    bom_encoding = detect_bom(data)
    if bom_encoding is not None:
        decoder = codecs.getincrementaldecoder(bom_encoding)(errors="replace")
        return decoder.decode(data, final=final), bom_encoding
    try:
        utf8 = codecs.getincrementaldecoder("utf-8")(errors="strict")
        return utf8.decode(data, final=final), "utf-8"
    except UnicodeDecodeError:
        # cp1252 leaves 5 bytes undefined; they become U+FFFD and count as "bad" characters.
        return data.decode("cp1252", errors="replace"), "cp1252"


def decode_text(data: bytes) -> tuple[str, str]:
    """BOM-aware UTF-8 / UTF-16 / UTF-32, then UTF-8, then cp1252.

    Raises INGESTION_FAILURE "not a text file" when >5% of characters are replacement,
    NUL or control characters.
    """
    text, encoding = _decode_raw(data, final=True)
    if bad_char_ratio(text) > MAX_BAD_CHAR_RATIO:
        raise ingestion_error("not a text file", "NOT_TEXT", encoding=encoding)
    return text, encoding


def decode_prefix(data: bytes) -> str | None:
    """Decode a (possibly truncated) prefix for sniffing; None when it does not look textual."""
    text, _ = _decode_raw(data, final=False)
    if bad_char_ratio(text) > MAX_BAD_CHAR_RATIO:
        return None
    return text
