"""Plain text, notes, plain transcripts and Markdown."""

from __future__ import annotations

import logging
import re

import yaml

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import check_size, decode_text
from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import MetadataValue, NormalizedDocument, build_document
from omnigrowth.ingestion.sniff import MIME_MARKDOWN, MIME_TEXT

logger = logging.getLogger(__name__)

MAX_FRONT_MATTER_CHARS = 64 * 1024
_RESERVED_META = frozenset({"word_count", "detected_mime", "encoding", "page_count"})
_ATX_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(```|~~~)")
_FRONT_MATTER_END = frozenset({"---", "..."})


def parse_text(
    data: bytes,
    *,
    source_type: SourceType = SourceType.TEXT,
    limits: IngestionLimits = DEFAULT_LIMITS,
    title: str | None = None,
) -> NormalizedDocument:
    """Plain text (also NOTE and plain TRANSCRIPT; the latter also sets ``transcript``)."""
    check_size(data, limits)
    text, encoding = decode_text(data)
    return build_document(
        data=data,
        source_type=source_type,
        text=text,
        limits=limits,
        detected_mime=MIME_TEXT,
        title=title,
        sections=[(None, text)],
        metadata={"encoding": encoding},
        transcript=text if source_type is SourceType.TRANSCRIPT else None,
    )


def _split_front_matter(text: str) -> tuple[dict[str, MetadataValue], str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    for idx in range(1, len(lines)):
        if lines[idx].strip() in _FRONT_MATTER_END:
            raw = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            return _front_matter_metadata(raw), body
    return {}, text  # unterminated: not front matter


def _front_matter_metadata(raw: str) -> dict[str, MetadataValue]:
    if len(raw) > MAX_FRONT_MATTER_CHARS:
        logger.debug("markdown front matter ignored: too large", extra={"chars": len(raw)})
        return {}
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        logger.debug("markdown front matter ignored: invalid YAML", extra={"error": str(exc)})
        return {}
    if not isinstance(loaded, dict):
        return {}
    meta: dict[str, MetadataValue] = {}
    for key, value in loaded.items():
        if not isinstance(key, str) or key in _RESERVED_META:
            continue
        if isinstance(value, str) or (isinstance(value, int) and not isinstance(value, bool)):
            meta[key] = value
    return meta


def _markdown_sections(body: str) -> tuple[str | None, list[tuple[str | None, str]]]:
    """Split on ATX headings outside fenced code blocks. Returns (first H1, sections)."""
    sections: list[tuple[str | None, str]] = []
    heading: str | None = None
    buf: list[str] = []
    first_h1: str | None = None
    in_fence: str | None = None
    for line in body.split("\n"):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            in_fence = None if in_fence == marker else (in_fence or marker)
        match = None if in_fence or fence else _ATX_RE.match(line)
        if match:
            sections.append((heading, "\n".join(buf)))
            heading = (match.group(2) or "").strip()
            buf = []
            if first_h1 is None and len(match.group(1)) == 1 and heading:
                first_h1 = heading
            continue
        buf.append(line)
    sections.append((heading, "\n".join(buf)))
    return first_h1, sections


def parse_markdown(data: bytes, *, limits: IngestionLimits = DEFAULT_LIMITS) -> NormalizedDocument:
    """Markdown kept raw (normalised); sections per ATX heading; title = first H1."""
    check_size(data, limits)
    text, encoding = decode_text(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    front, body = _split_front_matter(text)
    first_h1, sections = _markdown_sections(body)
    fm_title = front.get("title")
    title = first_h1 or (fm_title if isinstance(fm_title, str) else None)
    metadata: dict[str, MetadataValue] = {**front, "encoding": encoding}
    return build_document(
        data=data,
        source_type=SourceType.MARKDOWN,
        text=body,
        limits=limits,
        detected_mime=MIME_MARKDOWN,
        title=title,
        sections=sections,
        metadata=metadata,
    )
