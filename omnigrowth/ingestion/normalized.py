"""Common internal representation for all ingested content (spec §27 "normalize all content").

``NormalizedDocument`` is what every parser returns. ``build_document`` is the single place
where text normalisation, checksum, word count and the ``max_text_chars`` limit are applied.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import limit_error
from omnigrowth.ingestion.limits import IngestionLimits

MetadataValue = str | int | None

_KEEP_CONTROL = frozenset("\n\t")
_BLANK_RUN_RE = re.compile(r"\n{4,}")  # 3+ blank lines -> exactly 2 blank lines
_WORD_RE = re.compile(r"\S+")


class Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    heading: str | None = None
    text: str


class NormalizedDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_type: SourceType
    title: str | None = None
    text: str
    sections: list[Section] = Field(default_factory=list)
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)
    checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    word_count: int = Field(ge=0)
    transcript: str | None = None


def normalize_text(text: str) -> str:
    """UTF-8-safe canonical text: LF newlines, NFC, no control chars except ``\\n``/``\\t``,
    no trailing whitespace per line, runs of more than 2 blank lines collapsed to 2, trimmed.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2028", "\n").replace("\u2029", "\n\n")
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        ch
        for ch in text
        if ch in _KEEP_CONTROL or (unicodedata.category(ch) != "Cc" and ch != "\ufeff")
    )
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = _BLANK_RUN_RE.sub("\n\n\n", text)
    return text.strip()


def normalize_inline(text: str | None) -> str | None:
    """Normalise a single-line value (title, heading): whitespace collapsed; empty -> None."""
    if text is None:
        return None
    collapsed = " ".join(normalize_text(text).split())
    return collapsed or None


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_document(
    *,
    data: bytes,
    source_type: SourceType,
    text: str,
    limits: IngestionLimits,
    detected_mime: str,
    title: str | None = None,
    sections: list[tuple[str | None, str]] | None = None,
    metadata: dict[str, MetadataValue] | None = None,
    transcript: str | None = None,
) -> NormalizedDocument:
    """Normalise extracted content and assemble the document. ``data`` = ORIGINAL bytes."""
    if len(text) > limits.max_text_chars:
        raise limit_error("max_text_chars", limits.max_text_chars, len(text))
    norm_text = normalize_text(text)
    norm_sections: list[Section] = []
    for heading, body in sections or []:
        norm_heading = normalize_inline(heading)
        norm_body = normalize_text(body)
        if norm_heading is None and not norm_body:
            continue
        norm_sections.append(Section(heading=norm_heading, text=norm_body))
    words = count_words(norm_text)
    meta: dict[str, MetadataValue] = dict(metadata or {})
    meta["word_count"] = words
    meta["detected_mime"] = detected_mime
    return NormalizedDocument(
        source_type=source_type,
        title=normalize_inline(title),
        text=norm_text,
        sections=norm_sections,
        metadata=meta,
        checksum=sha256_hex(data),
        word_count=words,
        transcript=normalize_text(transcript) if transcript is not None else None,
    )
