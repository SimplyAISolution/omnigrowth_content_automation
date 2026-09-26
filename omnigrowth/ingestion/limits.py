"""Resource limits for file ingestion (spec §25 upload size limits, API_PLAN §6.2).

Every limit breach raises ``OmniGrowthError(VALIDATION_FAILURE, details={"limit": <name>})``.
"""

from __future__ import annotations

from dataclasses import dataclass

MiB = 1024 * 1024


@dataclass(frozen=True, slots=True)
class IngestionLimits:
    """Caps applied before and during parsing. Field names are the ``limit`` detail value."""

    max_bytes: int = 25 * MiB
    max_text_chars: int = 2_000_000
    max_pdf_pages: int = 500
    # DOCX zip-bomb guard (checked on the ZIP central directory BEFORE python-docx opens it).
    max_zip_total_uncompressed: int = 100 * MiB
    max_zip_member_uncompressed: int = 50 * MiB
    max_zip_compression_ratio: int = 100
    max_zip_members: int = 2000
    # Members smaller than this are exempt from the ratio check: tiny XML parts are often
    # highly compressible and cannot amplify meaningfully.
    zip_ratio_check_min_bytes: int = 1 * MiB
    # Wall-clock budget for one parse (checked between ZIP chunks and PDF pages). The RQ job
    # timeout of the ingest queue is the outer, hard limit.
    max_parse_seconds: float = 120.0


DEFAULT_LIMITS = IngestionLimits()
