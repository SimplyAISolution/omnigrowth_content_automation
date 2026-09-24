"""DOCX extraction via python-docx, behind a ZIP-bomb guard on the central directory."""

from __future__ import annotations

import io
import zipfile
import zlib

import docx
from docx.document import Document as DocxDocument
from docx.exceptions import PythonDocxError
from docx.opc.exceptions import OpcError
from docx.table import Table
from docx.text.paragraph import Paragraph

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import check_size, ingestion_error, limit_error
from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import NormalizedDocument, build_document
from omnigrowth.ingestion.sniff import MIME_DOCX

_ZIP_ERRORS: tuple[type[BaseException], ...] = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    EOFError,
    ValueError,
    NotImplementedError,  # unsupported compression method
    RuntimeError,  # encrypted member
    zlib.error,
)
# python-docx raises its own errors, KeyError for missing parts/relationships, ValueError for
# a wrong main content type, and lxml.etree.XMLSyntaxError (a SyntaxError subclass) for
# malformed XML. AttributeError/TypeError/IndexError appear for structurally odd XML.
_DOCX_ERRORS: tuple[type[BaseException], ...] = (
    *_ZIP_ERRORS,
    PythonDocxError,
    OpcError,
    KeyError,
    SyntaxError,
    AttributeError,
    TypeError,
    IndexError,
)


def check_zip_limits(data: bytes, limits: IngestionLimits) -> None:
    """Reject ZIP bombs using the central directory only (nothing is decompressed)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
    except _ZIP_ERRORS as exc:
        raise ingestion_error(
            "DOCX archive is corrupted", "CORRUPT", error=type(exc).__name__
        ) from exc
    if len(infos) > limits.max_zip_members:
        raise limit_error("max_zip_members", limits.max_zip_members, len(infos))
    total = 0
    for info in infos:
        size = info.file_size
        if size > limits.max_zip_member_uncompressed:
            raise limit_error(
                "max_zip_member_uncompressed", limits.max_zip_member_uncompressed, size
            )
        if size >= limits.zip_ratio_check_min_bytes:
            ratio = size // max(info.compress_size, 1)
            if ratio > limits.max_zip_compression_ratio:
                raise limit_error(
                    "max_zip_compression_ratio", limits.max_zip_compression_ratio, ratio
                )
        total += size
        if total > limits.max_zip_total_uncompressed:
            raise limit_error(
                "max_zip_total_uncompressed", limits.max_zip_total_uncompressed, total
            )


def _is_heading(paragraph: Paragraph) -> bool:
    style = paragraph.style
    name = (style.name or "") if style is not None else ""
    return name.startswith("Heading") or name == "Title"


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            key = id(cell._tc)  # merged cells repeat the same underlying <w:tc>
            if key in seen:
                continue
            seen.add(key)
            text = cell.text.strip()
            if text:
                cells.append(text)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _extract(document: DocxDocument) -> tuple[str | None, list[tuple[str | None, str]]]:
    sections: list[tuple[str | None, str]] = []
    heading: str | None = None
    buf: list[str] = []
    title_style_text: str | None = None
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            if _is_heading(block) and block.text.strip():
                style_name = block.style.name if block.style is not None else None
                if title_style_text is None and style_name == "Title":
                    title_style_text = block.text
                sections.append((heading, "\n".join(buf)))
                heading, buf = block.text, []
            else:
                buf.append(block.text)
        else:
            buf.append(_table_text(block))
    sections.append((heading, "\n".join(buf)))
    core_title = document.core_properties.title or None
    return core_title or title_style_text, sections


def parse_docx(data: bytes, *, limits: IngestionLimits = DEFAULT_LIMITS) -> NormalizedDocument:
    """Paragraphs + table cells; Heading/Title styles start sections; core-properties title."""
    check_size(data, limits)
    check_zip_limits(data, limits)
    try:
        document = docx.Document(io.BytesIO(data))
        title, sections = _extract(document)
    except _DOCX_ERRORS as exc:
        raise ingestion_error(
            "DOCX is corrupted or unreadable", "CORRUPT", error=type(exc).__name__
        ) from exc
    text = "\n\n".join((f"{heading}\n{body}" if heading else body) for heading, body in sections)
    if not text.strip():
        raise ingestion_error("DOCX contains no text", "NO_TEXT")
    return build_document(
        data=data,
        source_type=SourceType.DOCX,
        text=text,
        limits=limits,
        detected_mime=MIME_DOCX,
        title=title,
        sections=sections,
    )
