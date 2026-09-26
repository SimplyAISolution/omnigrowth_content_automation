"""PDF text extraction via pypdf (text layer only; OCR is future work, spec §27)."""

from __future__ import annotations

import io
import struct
import time
import zlib
from collections.abc import Callable

from pypdf import PasswordType, PdfReader
from pypdf.errors import DependencyError, PyPdfError

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import check_size, ingestion_error, limit_error
from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import NormalizedDocument, build_document
from omnigrowth.ingestion.sniff import MIME_PDF

# pypdf documents PyPdfError (PdfReadError, PdfStreamError, ParseError, LimitReachedError,
# FileNotDecryptedError, ...) and DependencyError, but on hostile input its object parser and
# filters are also known to leak these builtin/stdlib types. Each is listed explicitly; this
# is NOT a blanket ``except Exception``.
_PDF_ERRORS: tuple[type[BaseException], ...] = (
    PyPdfError,
    DependencyError,
    ValueError,  # includes UnicodeDecodeError
    KeyError,
    IndexError,
    TypeError,
    AttributeError,
    AssertionError,
    RecursionError,
    NotImplementedError,
    OSError,
    EOFError,
    struct.error,
    zlib.error,
)


def _encrypted_error() -> Exception:
    return ingestion_error("PDF is encrypted and cannot be opened without a password", "ENCRYPTED")


def _open_reader(data: bytes) -> PdfReader:
    reader = PdfReader(io.BytesIO(data), strict=False)
    if reader.is_encrypted:
        try:
            result = reader.decrypt("")
        except (DependencyError, NotImplementedError, PyPdfError) as exc:
            raise _encrypted_error() from exc
        if result == PasswordType.NOT_DECRYPTED:
            raise _encrypted_error()
    return reader


def _info_title(reader: PdfReader) -> str | None:
    info = reader.metadata
    if info is None:
        return None
    title = info.title
    return str(title) if title else None


def parse_pdf(
    data: bytes,
    *,
    limits: IngestionLimits = DEFAULT_LIMITS,
    clock: Callable[[], float] = time.monotonic,
) -> NormalizedDocument:
    """Per-page text; sections are ``Page N``; metadata ``page_count``; title from doc info.

    Decompression is bounded by pypdf's own per-stream output limits (75 MB defaults); pages,
    extracted characters and wall-clock time are bounded here.
    """
    check_size(data, limits)
    deadline = clock() + limits.max_parse_seconds
    if not data.startswith(b"%PDF-"):
        raise ingestion_error("not a PDF file", "CORRUPT")
    pages: list[tuple[str | None, str]] = []
    total_chars = 0
    try:
        reader = _open_reader(data)
        page_count = len(reader.pages)
        if page_count == 0:
            raise ingestion_error("PDF has no pages", "CORRUPT")
        if page_count > limits.max_pdf_pages:
            raise limit_error("max_pdf_pages", limits.max_pdf_pages, page_count)
        for number, page in enumerate(reader.pages, start=1):
            if clock() >= deadline:
                raise limit_error("max_parse_seconds", int(limits.max_parse_seconds))
            page_text = page.extract_text() or ""
            total_chars += len(page_text)
            if total_chars > limits.max_text_chars:
                raise limit_error("max_text_chars", limits.max_text_chars, total_chars)
            pages.append((f"Page {number}", page_text))
        title = _info_title(reader)
    except _PDF_ERRORS as exc:
        raise ingestion_error(
            "PDF is corrupted or unreadable", "CORRUPT", error=type(exc).__name__
        ) from exc
    if not any(text.strip() for _, text in pages):
        raise ingestion_error(
            "PDF has no extractable text layer (scanned/image-only PDF); "
            "OCR is not implemented yet",
            "NO_TEXT_LAYER",
            page_count=page_count,
        )
    return build_document(
        data=data,
        source_type=SourceType.PDF,
        text="\n\n".join(text for _, text in pages),
        limits=limits,
        detected_mime=MIME_PDF,
        title=title,
        sections=pages,
        metadata={"page_count": page_count},
    )
