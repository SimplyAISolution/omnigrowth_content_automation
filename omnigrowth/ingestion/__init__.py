"""OmniGrowth ingestion package (spec §25, §27).

Pure functions: bytes in, ``NormalizedDocument`` out. No DB, no network, nothing executed.
"""

from __future__ import annotations

from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import (
    NormalizedDocument,
    Section,
    normalize_text,
)
from omnigrowth.ingestion.parsers import (
    parse,
    parse_docx,
    parse_html,
    parse_markdown,
    parse_pdf,
    parse_subtitles,
    parse_text,
)
from omnigrowth.ingestion.sniff import sniff_type

__all__ = [
    "DEFAULT_LIMITS",
    "IngestionLimits",
    "NormalizedDocument",
    "Section",
    "normalize_text",
    "parse",
    "parse_docx",
    "parse_html",
    "parse_markdown",
    "parse_pdf",
    "parse_subtitles",
    "parse_text",
    "sniff_type",
]
