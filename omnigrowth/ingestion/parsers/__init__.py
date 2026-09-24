"""Parser dispatch: bytes -> ``NormalizedDocument`` (spec §27)."""

from __future__ import annotations

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import (
    check_size,
    ingestion_error,
    mismatch_error,
    validation_error,
)
from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import NormalizedDocument
from omnigrowth.ingestion.parsers.docx_parser import check_zip_limits, parse_docx
from omnigrowth.ingestion.parsers.html_parser import parse_html
from omnigrowth.ingestion.parsers.pdf_parser import parse_pdf
from omnigrowth.ingestion.parsers.subtitle_parser import parse_subtitles
from omnigrowth.ingestion.parsers.text_parser import parse_markdown, parse_text
from omnigrowth.ingestion.sniff import sniff

__all__ = [
    "check_zip_limits",
    "parse",
    "parse_docx",
    "parse_html",
    "parse_markdown",
    "parse_pdf",
    "parse_subtitles",
    "parse_text",
]

TEXT_FAMILY = frozenset(
    {
        SourceType.TEXT,
        SourceType.MARKDOWN,
        SourceType.TRANSCRIPT,
        SourceType.SUBTITLE,
        SourceType.NOTE,
        SourceType.URL,
    }
)
_MEDIA = frozenset({SourceType.AUDIO, SourceType.VIDEO})


def _reconcile(sniffed: SourceType, requested: SourceType | None) -> SourceType:
    if requested is None or requested is sniffed:
        return sniffed
    if requested is SourceType.YOUTUBE_URL:
        raise validation_error("YOUTUBE_URL sources are not files", source_type=requested)
    if sniffed in TEXT_FAMILY and requested in TEXT_FAMILY:
        return requested
    if sniffed in _MEDIA and requested in _MEDIA:
        return requested
    raise mismatch_error(detected=str(sniffed), declared=str(requested))


def parse(
    data: bytes,
    *,
    filename: str | None = None,
    declared_content_type: str | None = None,
    source_type: SourceType | None = None,
    limits: IngestionLimits = DEFAULT_LIMITS,
) -> NormalizedDocument:
    """Validate, sniff and parse ``data``.

    The bytes are always sniffed; a caller-supplied ``source_type`` may only refine within
    the text family (e.g. TEXT -> TRANSCRIPT/NOTE) or within audio/video, never contradict
    the magic bytes. ``source_type=URL`` parses the bytes as HTML.
    """
    check_size(data, limits)
    sniffed, _mime = sniff(data, filename, declared_content_type)
    effective = _reconcile(sniffed, source_type)
    match effective:
        case SourceType.PDF:
            return parse_pdf(data, limits=limits)
        case SourceType.DOCX:
            return parse_docx(data, limits=limits)
        case SourceType.MARKDOWN:
            return parse_markdown(data, limits=limits)
        case SourceType.SUBTITLE | SourceType.TRANSCRIPT:
            return parse_subtitles(data, source_type=effective, limits=limits)
        case SourceType.URL:
            return parse_html(data, limits=limits)
        case SourceType.TEXT | SourceType.NOTE:
            return parse_text(data, source_type=effective, limits=limits)
        case SourceType.AUDIO | SourceType.VIDEO:
            raise ingestion_error(
                f"{effective} processing is not implemented yet (transcription is future work)",
                "NOT_IMPLEMENTED",
                source_type=str(effective),
            )
        case _:
            raise validation_error("unsupported file type", source_type=str(effective))
