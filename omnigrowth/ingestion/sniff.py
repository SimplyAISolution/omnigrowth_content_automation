"""Content-type sniffing (spec §25 content-type validation; API_PLAN §6.2).

Magic bytes decide first. The filename extension and the declared content type are only
*checked* against what the bytes are; they never override them. The filename is treated as
an opaque string and never used as a path.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import PurePosixPath

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import decode_prefix, mismatch_error, validation_error

SNIFF_PREFIX_BYTES = 64 * 1024

MIME_PDF = "application/pdf"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MIME_TEXT = "text/plain"
MIME_MARKDOWN = "text/markdown"
MIME_SRT = "application/x-subrip"
MIME_VTT = "text/vtt"
MIME_HTML = "text/html"

# Content categories used to compare bytes against extension / declared content type.
PDF, DOCX, TEXT, AUDIO, VIDEO = "pdf", "docx", "text", "audio", "video"
ZIP, EXEC, IMAGE, ARCHIVE = "zip", "exec", "image", "archive"
_SUPPORTED_CATEGORIES = frozenset({PDF, DOCX, TEXT, AUDIO, VIDEO})
_MEDIA = frozenset({AUDIO, VIDEO})

_EXT_CATEGORY: dict[str, str] = {
    ".pdf": PDF,
    ".docx": DOCX,
    ".txt": TEXT,
    ".text": TEXT,
    ".md": TEXT,
    ".markdown": TEXT,
    ".srt": TEXT,
    ".vtt": TEXT,
    ".mp3": AUDIO,
    ".wav": AUDIO,
    ".m4a": AUDIO,
    ".aac": AUDIO,
    ".ogg": AUDIO,
    ".oga": AUDIO,
    ".opus": AUDIO,
    ".flac": AUDIO,
    ".weba": AUDIO,
    ".mp4": VIDEO,
    ".m4v": VIDEO,
    ".mov": VIDEO,
    ".webm": VIDEO,
    ".mkv": VIDEO,
    ".ogv": VIDEO,
    ".avi": VIDEO,
    ".zip": ZIP,
    ".exe": EXEC,
    ".dll": EXEC,
    ".msi": EXEC,
    ".so": EXEC,
    ".elf": EXEC,
    ".jpg": IMAGE,
    ".jpeg": IMAGE,
    ".png": IMAGE,
    ".gif": IMAGE,
    ".webp": IMAGE,
    ".gz": ARCHIVE,
    ".7z": ARCHIVE,
    ".rar": ARCHIVE,
    ".doc": ARCHIVE,  # legacy OLE Word: unsupported binary
}

_CT_CATEGORY: dict[str, str] = {
    MIME_PDF: PDF,
    "application/x-pdf": PDF,
    MIME_DOCX: DOCX,
    MIME_SRT: TEXT,
    "application/json": TEXT,
    "application/zip": ZIP,
    "application/x-zip-compressed": ZIP,
    "application/x-msdownload": EXEC,
    "application/x-dosexec": EXEC,
    "application/x-executable": EXEC,
    "application/msword": ARCHIVE,
    "application/gzip": ARCHIVE,
}
_CT_PREFIX_CATEGORY: tuple[tuple[str, str], ...] = (
    ("text/", TEXT),
    ("audio/", AUDIO),
    ("video/", VIDEO),
    ("image/", IMAGE),
)
_GENERIC_CT = frozenset({"", "application/octet-stream", "binary/octet-stream"})

_SRT_RE = re.compile(
    r"^\s*\d+[ \t]*\n[ \t]*\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}[ \t]*-->[ \t]*\d{1,2}:\d{2}:\d{2}",
    re.MULTILINE,
)
_MD_HEADING_RE = re.compile(r"^#{1,6}[ \t]+\S", re.MULTILINE)
_MD_FENCE_RE = re.compile(r"^(```|~~~)", re.MULTILINE)
_MD_LIST_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+\.)[ \t]+\S", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^)\s]+\)")
_MD_EMPH_RE = re.compile(r"(\*\*|__)[^\s*_][^\n]*?\1")
_MD_QUOTE_RE = re.compile(r"^>[ \t]?\S", re.MULTILINE)


def extension_of(filename: str | None) -> str:
    if not filename:
        return ""
    return PurePosixPath(filename.replace("\\", "/")).suffix.lower()


def normalize_content_type(declared: str | None) -> str:
    if not declared:
        return ""
    return declared.split(";", 1)[0].strip().lower()


def _category_of_extension(ext: str) -> str | None:
    return _EXT_CATEGORY.get(ext)


def _category_of_content_type(ct: str) -> str | None:
    if ct in _GENERIC_CT:
        return None
    if ct in _CT_CATEGORY:
        return _CT_CATEGORY[ct]
    for prefix, category in _CT_PREFIX_CATEGORY:
        if ct.startswith(prefix):
            return category
    return "other"


def _compatible(detected: str, declared: str | None) -> bool:
    if declared is None or declared == detected:
        return True
    if detected in _MEDIA and declared in _MEDIA:
        return True  # e.g. an .mp4 container carrying audio only
    return detected == DOCX and declared == ZIP


def _media_magic(data: bytes) -> tuple[str, str] | None:
    """(category, mime) for common audio/video containers."""
    if data.startswith(b"ID3"):
        return AUDIO, "audio/mpeg"
    if len(data) >= 2 and data[0] == 0xFF and data[1] in (0xFB, 0xF3, 0xF2, 0xFA, 0xE3):
        return AUDIO, "audio/mpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return AUDIO, "audio/wav"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return VIDEO, "video/x-msvideo"
    if data.startswith(b"fLaC"):
        return AUDIO, "audio/flac"
    if data.startswith(b"OggS"):
        return AUDIO, "audio/ogg"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"M4A ", b"M4B ", b"M4P "):
            return AUDIO, "audio/mp4"
        if brand == b"qt  ":
            return VIDEO, "video/quicktime"
        return VIDEO, "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return VIDEO, "video/webm"
    return None


def _unsupported_binary_magic(data: bytes) -> str | None:
    if data.startswith(b"MZ") or data.startswith(b"\x7fELF"):
        return EXEC
    if data[:4] in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe"):
        return EXEC  # Mach-O
    if data.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")):
        return IMAGE
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return IMAGE
    if data.startswith((b"\x1f\x8b", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")):
        return ARCHIVE
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return ARCHIVE  # OLE2 (legacy .doc/.xls)
    return None


def _is_docx_zip(data: bytes) -> bool:
    """True when the ZIP central directory lists the two mandatory DOCX parts."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError):
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def looks_like_markdown(text: str) -> bool:
    kinds = sum(
        1
        for pattern in (_MD_LIST_RE, _MD_LINK_RE, _MD_EMPH_RE, _MD_QUOTE_RE)
        if pattern.search(text)
    )
    structural = bool(_MD_HEADING_RE.search(text)) or bool(_MD_FENCE_RE.search(text))
    return structural and kinds >= 1


def looks_like_vtt(text: str) -> bool:
    first = text.lstrip("\ufeff").split("\n", 1)[0].rstrip("\r")
    return first == "WEBVTT" or first.startswith(("WEBVTT ", "WEBVTT\t"))


def looks_like_srt(text: str) -> bool:
    return _SRT_RE.search(text.replace("\r\n", "\n")) is not None


def _sniff_text_subtype(text: str, ext: str, ct: str) -> tuple[SourceType, str]:
    if ext == ".vtt" or ct == MIME_VTT or looks_like_vtt(text):
        return SourceType.TRANSCRIPT, MIME_VTT
    if ext == ".srt" or ct == MIME_SRT or looks_like_srt(text):
        return SourceType.SUBTITLE, MIME_SRT
    if ext in (".md", ".markdown") or ct in (MIME_MARKDOWN, "text/x-markdown"):
        return SourceType.MARKDOWN, MIME_MARKDOWN
    if looks_like_markdown(text):
        return SourceType.MARKDOWN, MIME_MARKDOWN
    return SourceType.TEXT, MIME_TEXT


def sniff(
    data: bytes, filename: str | None, declared_content_type: str | None
) -> tuple[SourceType, str]:
    """Return ``(source_type, detected_mime)``; see ``sniff_type``."""
    if not data:
        raise validation_error("empty", reason="EMPTY")
    ext = extension_of(filename)
    ct = normalize_content_type(declared_content_type)
    declared = [c for c in (_category_of_extension(ext), _category_of_content_type(ct)) if c]

    def _check(detected: str) -> None:
        for category in declared:
            if not _compatible(detected, category):
                raise mismatch_error(detected=detected, declared=category)

    head = data[:SNIFF_PREFIX_BYTES]
    if data.startswith(b"%PDF-"):
        _check(PDF)
        return SourceType.PDF, MIME_PDF
    if data.startswith(b"PK\x03\x04"):
        if _is_docx_zip(data):
            _check(DOCX)
            return SourceType.DOCX, MIME_DOCX
        return _reject_binary(ZIP, declared)
    media = _media_magic(head)
    if media is not None:
        category, mime = media
        _check(category)
        return (SourceType.AUDIO if category == AUDIO else SourceType.VIDEO), mime
    binary = _unsupported_binary_magic(head)
    if binary is not None:
        return _reject_binary(binary, declared)

    text = decode_prefix(head)
    if text is None:
        if TEXT in declared:
            # Declared as text: let the text decoder produce INGESTION_FAILURE "not a text file".
            _check(TEXT)
            return SourceType.TEXT, MIME_TEXT
        if any(c in _SUPPORTED_CATEGORIES for c in declared):
            raise mismatch_error(detected="binary", declared=declared[0])
        raise validation_error("unsupported file type", detected="binary")
    _check(TEXT)
    return _sniff_text_subtype(text, ext, ct)


def _reject_binary(detected: str, declared: list[str]) -> tuple[SourceType, str]:
    for category in declared:
        if category in _SUPPORTED_CATEGORIES and not _compatible(detected, category):
            raise mismatch_error(detected=detected, declared=category)
    raise validation_error("unsupported file type", detected=detected)


def sniff_type(data: bytes, filename: str | None, declared_content_type: str | None) -> SourceType:
    """Decide the ``SourceType`` of uploaded bytes by magic bytes, then text heuristics.

    Raises VALIDATION_FAILURE "content does not match declared type" when the extension or
    declared content type contradicts the bytes, "unsupported file type" for other binary
    data, and "empty" for zero bytes.
    """
    return sniff(data, filename, declared_content_type)[0]
