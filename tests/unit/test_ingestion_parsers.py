"""Ingestion parsers (Phase 4): sniffing, parsing, normalisation, limits, hostile inputs.

All fixtures are generated in-test (no committed binaries, no network).
"""

from __future__ import annotations

import hashlib
import io
import random
import zipfile
from collections.abc import Callable

import docx
import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from omnigrowth.core.enums import SourceType
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.ingestion import (
    DEFAULT_LIMITS,
    IngestionLimits,
    normalize_text,
    parse,
    parse_docx,
    parse_html,
    parse_pdf,
    parse_subtitles,
    parse_text,
    sniff_type,
)
from omnigrowth.ingestion.limits import MiB

# Unicode characters built with chr() to keep this file pure ASCII.
LDQUO, RDQUO, E_ACUTE, COMBINING_ACUTE, O_UML = (
    chr(0x201C),
    chr(0x201D),
    chr(0xE9),
    chr(0x301),
    chr(0xF6),
)


# ----------------------------------------------------------------------------- builders
def rng(seed: int) -> random.Random:
    """Deterministic PRNG for fuzz-style byte generation (not security relevant)."""
    return random.Random(seed)  # noqa: S311


def make_pdf(pages: list[str], *, title: str | None = None) -> bytes:
    """Minimal valid PDF with one Helvetica text object per page (empty string = no text)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, text in enumerate(pages):
        content = (f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET" if text else "0 0 m 9 9 l S").encode()
        objs.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>"
            ).encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    if title:
        objs.append(f"<< /Title ({title}) >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, obj in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    info = f" /Info {len(objs)} 0 R" if title else ""
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R{info} >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def encrypt_pdf(data: bytes, user_password: str) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(user_password=user_password, owner_password="owner", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_docx(*, title: str | None = "Core Title") -> bytes:
    document = docx.Document()
    if title:
        document.core_properties.title = title
    document.add_paragraph("Preamble paragraph.")
    document.add_heading("Introduction", level=1)
    document.add_paragraph("First body paragraph.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Cell A1"
    table.cell(0, 1).text = "Cell B1"
    table.cell(1, 0).text = "Cell A2"
    table.cell(1, 1).text = "Cell B2"
    document.add_heading("Details", level=2)
    document.add_paragraph("Second body paragraph.")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def rewrite_zip(
    data: bytes,
    *,
    replace: dict[str, bytes] | None = None,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    replace = replace or {}
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            dst.writestr(info.filename, replace.get(info.filename, src.read(info.filename)))
        for name, payload in (extra or {}).items():
            dst.writestr(name, payload)
    return out.getvalue()


def plain_zip(members: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return out.getvalue()


def expect_error(code: ErrorCode, func: object, *args: object, **kwargs: object) -> OmniGrowthError:
    assert callable(func)
    with pytest.raises(OmniGrowthError) as info:
        func(*args, **kwargs)
    assert info.value.code is code, (info.value.code, info.value.message, info.value.details)
    return info.value


SRT = b"""1
00:00:01,000 --> 00:00:02,500
<i>Hello</i> there,

2
00:00:03,000 --> 00:00:04,000
{\\an8}general Kenobi.

3
00:00:05,000 --> 00:00:06,000
Second line
continues here.
"""

VTT = b"""WEBVTT - demo file

NOTE This is a comment
that spans two lines and must be ignored

STYLE
::cue { color: yellow }

intro
00:00:01.000 --> 00:00:02.000 align:start position:10%
<v Bob>Welcome</v> to the <c.yellow>show</c>.

00:00:02.500 --> 00:00:04.000
Today we talk <00:00:03.000>about &amp; around parsers.
"""

HTML = b"""<!doctype html>
<html><head><title>  My   Page </title>
<style>body { color: red }</style>
<script>alert("owned")</script></head>
<body>
<nav>Home | About</nav>
<main>
<h1>Main Heading</h1>
<p>First paragraph with <b>bold</b> text &amp; entity.</p>
<noscript>enable js</noscript>
<svg><title>icon</title><text>svg text</text></svg>
<h2>Sub Heading</h2>
<p>Second paragraph.</p>
</main>
<footer>Copyright footer</footer>
</body></html>
"""


# ----------------------------------------------------------------------------- happy paths
def test_plain_text_happy_path() -> None:
    data = b"Hello   world.\nSecond line here.\n"
    doc = parse(data, filename="notes.txt", declared_content_type="text/plain; charset=utf-8")
    assert doc.source_type is SourceType.TEXT
    assert doc.text == "Hello   world.\nSecond line here."
    assert doc.word_count == 5
    assert doc.metadata["word_count"] == 5
    assert doc.metadata["detected_mime"] == "text/plain"
    assert doc.checksum == hashlib.sha256(data).hexdigest()
    assert doc.transcript is None


def test_note_and_plain_transcript_refine_text_family() -> None:
    note = parse(b"a quick note", source_type=SourceType.NOTE)
    assert note.source_type is SourceType.NOTE
    transcript = parse(b"Speaker one: hi.\nSpeaker two: hello.", source_type=SourceType.TRANSCRIPT)
    assert transcript.source_type is SourceType.TRANSCRIPT
    assert transcript.transcript == transcript.text


def test_markdown_front_matter_sections_and_title() -> None:
    data = (
        b"---\ntitle: FM Title\nauthor: Jane\ntags: [a, b]\nversion: 3\nword_count: 999\n---\n"
        b"Intro before headings.\n\n# The Title\n\nBody one.\n\n## Part Two ##\n\n"
        b"```\n# not a heading\n```\nMore.\n"
    )
    doc = parse(data, filename="post.md")
    assert doc.source_type is SourceType.MARKDOWN
    assert doc.title == "The Title"
    assert doc.metadata["author"] == "Jane"
    assert doc.metadata["version"] == 3
    assert "tags" not in doc.metadata  # non-scalar ignored
    assert doc.metadata["word_count"] == doc.word_count  # reserved key not overridden
    assert "author: Jane" not in doc.text
    assert doc.text.startswith("Intro before headings.")
    assert "# The Title" in doc.text  # raw markdown kept
    headings = [s.heading for s in doc.sections]
    assert headings == [None, "The Title", "Part Two"]
    assert "# not a heading" in doc.sections[2].text


def test_markdown_invalid_front_matter_is_ignored() -> None:
    doc = parse(b"---\n: [unclosed\n---\n# Hi\n\ntext\n", filename="x.md")
    assert doc.title == "Hi"
    assert set(doc.metadata) == {"encoding", "word_count", "detected_mime"}


def test_markdown_front_matter_title_fallback_and_signal_sniffing() -> None:
    data = b"## Setup\n\n- install **uv**\n- see [docs](https://example.com)\n"
    assert sniff_type(data, None, None) is SourceType.MARKDOWN
    doc = parse(b"---\ntitle: Only FM\n---\nno headings\n", filename="a.markdown")
    assert doc.title == "Only FM"


def test_pdf_happy_path() -> None:
    data = make_pdf(["Hello page one", "Second page text"], title="My Doc")
    doc = parse(data, filename="doc.pdf", declared_content_type="application/pdf")
    assert doc.source_type is SourceType.PDF
    assert doc.title == "My Doc"
    assert doc.metadata["page_count"] == 2
    assert [s.heading for s in doc.sections] == ["Page 1", "Page 2"]
    assert "Hello page one" in doc.text and "Second page text" in doc.text
    assert doc.checksum == hashlib.sha256(data).hexdigest()


def test_docx_happy_path() -> None:
    doc = parse(make_docx(), filename="report.docx")
    assert doc.source_type is SourceType.DOCX
    assert doc.title == "Core Title"
    assert [s.heading for s in doc.sections] == [None, "Introduction", "Details"]
    assert "Cell A1 | Cell B1" in doc.sections[1].text
    assert "Cell B2" in doc.text
    assert doc.sections[2].text == "Second body paragraph."


def test_docx_zip_content_type_is_accepted() -> None:
    assert sniff_type(make_docx(), "r.docx", "application/zip") is SourceType.DOCX


def test_srt_transcript_ignores_numbers_timestamps_and_tags() -> None:
    doc = parse(SRT, filename="talk.srt")
    assert doc.source_type is SourceType.SUBTITLE
    assert doc.transcript == "Hello there, general Kenobi. Second line continues here."
    assert "-->" not in doc.text and "00:00" not in doc.text
    assert doc.metadata["cue_count"] == 3
    assert doc.metadata["subtitle_format"] == "srt"
    assert doc.metadata["duration_seconds"] == 6


def test_vtt_transcript_ignores_note_style_and_cue_tags() -> None:
    doc = parse(VTT, filename="talk.vtt", declared_content_type="text/vtt")
    assert doc.source_type is SourceType.TRANSCRIPT
    assert doc.transcript == "Welcome to the show. Today we talk about & around parsers."
    assert "comment" not in doc.text and "yellow" not in doc.text
    assert "intro" not in doc.text and "WEBVTT" not in doc.text
    assert doc.metadata["cue_count"] == 2


def test_rolling_caption_duplicates_are_collapsed() -> None:
    data = b"WEBVTT\n\n00:00.000 --> 00:01.000\nsame\n\n00:01.000 --> 00:02.000\nsame\n"
    assert parse_subtitles(data).transcript == "same"


def test_html_extraction_drops_script_style_nav_footer() -> None:
    doc = parse_html(HTML, base_url="https://example.com/post")
    assert doc.source_type is SourceType.URL
    assert doc.title == "My Page"
    for dropped in ("alert", "color: red", "Home | About", "Copyright", "enable js", "svg text"):
        assert dropped not in doc.text
    assert "First paragraph with bold text & entity." in doc.text
    assert [s.heading for s in doc.sections] == ["Main Heading", "Sub Heading"]
    assert doc.metadata["base_url"] == "https://example.com/post"
    assert doc.metadata["detected_mime"] == "text/html"


def test_html_without_main_keeps_body_text_and_parse_maps_to_url() -> None:
    data = b"<html><body><h1>T</h1><p>body text</p><script>x()</script></body></html>"
    doc = parse(data, source_type=SourceType.URL)
    assert doc.source_type is SourceType.URL
    assert "body text" in doc.text and "x()" not in doc.text


def test_html_with_no_text_fails() -> None:
    expect_error(ErrorCode.INGESTION_FAILURE, parse_html, b"<script>only()</script>")


# ----------------------------------------------------------------------------- sniffing
MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + bytes(64)
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + bytes(64)
M4A = b"\x00\x00\x00\x18ftypM4A \x00\x00\x02\x00" + bytes(64)
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(64)
OGG = b"OggS\x00\x02" + bytes(64)
WEBM = b"\x1a\x45\xdf\xa3" + bytes(64)
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + bytes(64)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(64)
RANDOM_ZIP = plain_zip({"hello.txt": b"hi", "data.bin": b"\x00\x01"})
RANDOM_BINARY = rng(7).randbytes(4096)


def byte_id(value: object) -> str | None:
    """Short pytest ids for byte payloads (default ids for everything else)."""
    if isinstance(value, bytes):
        return f"{value[:4].hex()}-{len(value)}B"
    return None


@pytest.mark.parametrize(
    ("data", "filename", "content_type", "expected"),
    [
        (make_pdf(["x"]), "a.pdf", "application/pdf", SourceType.PDF),
        (make_pdf(["x"]), None, None, SourceType.PDF),
        (make_pdf(["x"]), "a.bin", "application/octet-stream", SourceType.PDF),
        (make_docx(), "a.docx", None, SourceType.DOCX),
        (b"plain words", "a.txt", "text/plain", SourceType.TEXT),
        (b"plain words", None, None, SourceType.TEXT),
        (b"# Title\n\nwords", "a.md", "text/markdown", SourceType.MARKDOWN),
        (SRT, None, None, SourceType.SUBTITLE),
        (SRT, "a.srt", "application/x-subrip", SourceType.SUBTITLE),
        (VTT, None, None, SourceType.TRANSCRIPT),
        (MP3, "a.mp3", "audio/mpeg", SourceType.AUDIO),
        (MP4, "a.mp4", "video/mp4", SourceType.VIDEO),
        (M4A, "a.m4a", None, SourceType.AUDIO),
        (MP4, "a.m4a", "audio/mp4", SourceType.VIDEO),  # container ambiguity is tolerated
        (WAV, None, None, SourceType.AUDIO),
        (OGG, None, None, SourceType.AUDIO),
        (WEBM, "clip.webm", None, SourceType.VIDEO),
    ],
    ids=byte_id,
)
def test_sniff_matrix(
    data: bytes, filename: str | None, content_type: str | None, expected: SourceType
) -> None:
    assert sniff_type(data, filename, content_type) is expected


@pytest.mark.parametrize(
    ("data", "filename", "content_type"),
    [
        (b"just some text", "report.pdf", None),
        (b"just some text", None, "application/pdf"),
        (make_pdf(["x"]), "a.docx", None),
        (make_pdf(["x"]), "a.txt", None),
        (make_pdf(["x"]), None, "image/png"),
        (RANDOM_ZIP, "fake.docx", None),
        (
            RANDOM_ZIP,
            None,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        (EXE, "invoice.pdf", None),
        (EXE, "notes.txt", "text/plain"),
        (JPEG, "photo.txt", None),
        (MP3, "song.pdf", None),
        (make_docx(), "a.pdf", None),
        (RANDOM_BINARY, "a.pdf", None),
    ],
    ids=byte_id,
)
def test_sniff_mismatch_is_validation_failure(
    data: bytes, filename: str | None, content_type: str | None
) -> None:
    err = expect_error(ErrorCode.VALIDATION_FAILURE, sniff_type, data, filename, content_type)
    assert err.message == "content does not match declared type"


@pytest.mark.parametrize(
    ("data", "filename"),
    [(EXE, None), (EXE, "setup.exe"), (JPEG, None), (RANDOM_ZIP, "a.zip"), (RANDOM_BINARY, None)],
    ids=byte_id,
)
def test_sniff_unsupported_binary(data: bytes, filename: str | None) -> None:
    err = expect_error(ErrorCode.VALIDATION_FAILURE, sniff_type, data, filename, None)
    assert err.message == "unsupported file type"


@pytest.mark.parametrize("data", [MP3, MP4, WAV, WEBM], ids=byte_id)
def test_audio_video_parse_not_implemented(data: bytes) -> None:
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data)
    assert err.details["reason"] == "NOT_IMPLEMENTED"


def test_declared_source_type_cannot_contradict_bytes() -> None:
    err = expect_error(ErrorCode.VALIDATION_FAILURE, parse, b"text", source_type=SourceType.PDF)
    assert err.message == "content does not match declared type"
    expect_error(ErrorCode.VALIDATION_FAILURE, parse, make_pdf(["x"]), source_type=SourceType.TEXT)
    expect_error(ErrorCode.VALIDATION_FAILURE, parse, b"x", source_type=SourceType.YOUTUBE_URL)


# ----------------------------------------------------------------------------- corrupted / hostile
def test_empty_file_rejected() -> None:
    err = expect_error(ErrorCode.VALIDATION_FAILURE, parse, b"", filename="a.txt")
    assert err.message == "empty"
    expect_error(ErrorCode.VALIDATION_FAILURE, sniff_type, b"", None, None)
    expect_error(ErrorCode.VALIDATION_FAILURE, parse_pdf, b"")


def test_truncated_pdf() -> None:
    data = make_pdf(["Hello page one", "Two"])
    for cut in (len(data) // 2, 40, 12):
        err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data[:cut], filename="a.pdf")
        assert err.details["reason"] == "CORRUPT"


def test_pdf_with_garbage_after_header() -> None:
    data = b"%PDF-1.7\n" + rng(0).randbytes(5000)
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data)
    assert err.details["reason"] == "CORRUPT"


def test_encrypted_pdf_rejected() -> None:
    data = encrypt_pdf(make_pdf(["Secret text"]), "hunter2")
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data, filename="s.pdf")
    assert err.details["reason"] == "ENCRYPTED"


def test_encrypted_pdf_with_empty_user_password_is_decrypted() -> None:
    doc = parse(encrypt_pdf(make_pdf(["Open text"]), ""), filename="o.pdf")
    assert "Open text" in doc.text


def test_pdf_without_text_layer() -> None:
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, make_pdf(["", ""]))
    assert err.details["reason"] == "NO_TEXT_LAYER"
    assert "OCR" in err.message


def test_parse_pdf_direct_on_non_pdf() -> None:
    expect_error(ErrorCode.INGESTION_FAILURE, parse_pdf, b"hello world")


def test_mutated_pdfs_never_raise_raw_exceptions() -> None:
    base = make_pdf(["Alpha beta gamma", "Delta"], title="T")
    gen = rng(1234)
    for _ in range(60):
        mutated = bytearray(base)
        for _ in range(gen.randint(1, 12)):
            mutated[gen.randrange(9, len(mutated))] = gen.randrange(256)
        try:
            parse_pdf(bytes(mutated))
        except OmniGrowthError as exc:
            assert exc.code in (ErrorCode.INGESTION_FAILURE, ErrorCode.VALIDATION_FAILURE)


def test_docx_with_corrupt_xml() -> None:
    data = rewrite_zip(make_docx(), replace={"word/document.xml": b"<w:document><w:body><w:p>"})
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data, filename="bad.docx")
    assert err.details["reason"] == "CORRUPT"


def test_docx_missing_relationship_target() -> None:
    data = rewrite_zip(make_docx(), replace={"word/_rels/document.xml.rels": b"not xml at all"})
    expect_error(ErrorCode.INGESTION_FAILURE, parse, data, filename="bad.docx")


def test_truncated_docx() -> None:
    data = make_docx()[:300]
    # Sniffing cannot see the central directory -> not a DOCX -> mismatch with .docx name.
    expect_error(ErrorCode.VALIDATION_FAILURE, parse, data, filename="t.docx")
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse_docx, data)
    assert err.details["reason"] == "CORRUPT"


def test_mutated_docx_never_raises_raw_exceptions() -> None:
    base = make_docx()
    gen = rng(99)
    for _ in range(40):
        mutated = bytearray(base)
        for _ in range(gen.randint(1, 8)):
            mutated[gen.randrange(4, len(mutated))] = gen.randrange(256)
        try:
            parse_docx(bytes(mutated))
        except OmniGrowthError as exc:
            assert exc.code in (ErrorCode.INGESTION_FAILURE, ErrorCode.VALIDATION_FAILURE)


def test_docx_zip_bomb_compression_ratio() -> None:
    bomb = rewrite_zip(make_docx(), extra={"word/media/bomb.bin": bytes(20 * MiB)})
    assert len(bomb) < MiB  # tiny on disk
    err = expect_error(ErrorCode.VALIDATION_FAILURE, parse, bomb, filename="bomb.docx")
    assert err.details["limit"] == "max_zip_compression_ratio"


def test_docx_too_many_members() -> None:
    extra = {f"extra/item{i}.xml": b"<a/>" for i in range(DEFAULT_LIMITS.max_zip_members)}
    data = rewrite_zip(make_docx(), extra=extra)
    err = expect_error(ErrorCode.VALIDATION_FAILURE, parse, data, filename="many.docx")
    assert err.details["limit"] == "max_zip_members"


def test_docx_uncompressed_size_limits() -> None:
    data = rewrite_zip(make_docx(), extra={"word/media/big.bin": rng(3).randbytes(4096)})
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        parse_docx,
        data,
        limits=IngestionLimits(max_zip_member_uncompressed=2048),
    )
    assert err.details["limit"] == "max_zip_member_uncompressed"
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        parse_docx,
        data,
        limits=IngestionLimits(max_zip_total_uncompressed=10_000),
    )
    assert err.details["limit"] == "max_zip_total_uncompressed"


def test_text_file_with_nul_bytes() -> None:
    data = b"hello\x00\x00\x00world\x00\x00" * 50
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data, filename="a.txt")
    assert err.message == "not a text file"
    expect_error(ErrorCode.INGESTION_FAILURE, parse_text, data)
    # Unlabelled binary garbage is rejected at sniffing.
    expect_error(ErrorCode.VALIDATION_FAILURE, parse, data)


def test_srt_with_malformed_timestamps_skips_bad_cues() -> None:
    data = (
        b"1\n00:00:01,000 --> 00:00:02,000\nGood cue.\n\n"
        b"2\n00:00:xx,000 --> 00:00:03,000\nBad cue one.\n\n"
        b"3\n00:00:05,000 -> 00:00:06,000\nBad cue two.\n\n"
        b"4\n00:00:09,000 --> 00:00:07,000\nBackwards cue.\n\n"
        b"5\n00:00:10,000 --> 00:00:11,000\nAnother good cue.\n"
    )
    doc = parse(data, filename="a.srt")
    assert doc.transcript == "Good cue. Another good cue."
    assert doc.metadata["cue_count"] == 2


def test_srt_with_zero_valid_cues_fails() -> None:
    data = b"1\n00:00:xx,000 --> 00:00:03,000\nBad.\n\n2\nno timing here\n"
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, data, filename="a.srt")
    assert err.details["reason"] == "NO_CUES"


def test_vtt_with_no_cues_fails() -> None:
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse, b"WEBVTT\n\nNOTE nothing here\n")
    assert err.details["reason"] == "NO_CUES"


# ----------------------------------------------------------------------------- limits
def test_oversize_bytes_custom_limit() -> None:
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE, parse, b"0123456789A", limits=IngestionLimits(max_bytes=10)
    )
    assert err.details["limit"] == "max_bytes"


def test_oversize_bytes_default_limit_is_25_mib() -> None:
    assert DEFAULT_LIMITS.max_bytes == 25 * MiB
    err = expect_error(ErrorCode.VALIDATION_FAILURE, parse, b"a" * (25 * MiB + 1))
    assert err.details["limit"] == "max_bytes"


def test_max_text_chars() -> None:
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        parse,
        b"word " * 100,
        limits=IngestionLimits(max_text_chars=50),
    )
    assert err.details["limit"] == "max_text_chars"
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        parse,
        make_pdf(["a long line of pdf text"]),
        limits=IngestionLimits(max_text_chars=5),
    )
    assert err.details["limit"] == "max_text_chars"


def test_max_pdf_pages() -> None:
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        parse,
        make_pdf(["one", "two", "three"]),
        limits=IngestionLimits(max_pdf_pages=2),
    )
    assert err.details["limit"] == "max_pdf_pages"


# ----------------------------------------------------------------------------- decoding
def test_utf8_bom() -> None:
    doc = parse(b"\xef\xbb\xbfHello BOM", filename="a.txt")
    assert doc.text == "Hello BOM"
    assert doc.metadata["encoding"] == "utf-8-sig"


def test_utf16_with_bom() -> None:
    original = f"H{E_ACUTE}llo w{O_UML}rld\r\nline two"
    for encoding in ("utf-16", "utf-16-be"):
        data = original.encode(encoding)
        if encoding == "utf-16-be":
            data = b"\xfe\xff" + data
        doc = parse(data, filename="a.txt")
        assert doc.text == f"H{E_ACUTE}llo w{O_UML}rld\nline two"


def test_cp1252_smart_quotes_fallback() -> None:
    doc = parse(b"\x93smart quotes\x94 caf\xe9", filename="a.txt")
    assert doc.text == f"{LDQUO}smart quotes{RDQUO} caf{E_ACUTE}"
    assert doc.metadata["encoding"] == "cp1252"


# ----------------------------------------------------------------------------- normalisation
def test_normalize_text_rules() -> None:
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"
    assert normalize_text("caf" + "e" + COMBINING_ACUTE) == "caf" + E_ACUTE
    assert normalize_text("a\x07b\x1b[0mc\td\x00") == "ab[0mc\td"
    assert normalize_text("a\n\n\n\n\n\n\nb") == "a\n\n\nb"
    assert normalize_text("a\n\n\nb") == "a\n\n\nb"
    assert normalize_text("  \n  padded   \n\n  ") == "padded"


def test_parse_normalises_and_checksums_original_bytes() -> None:
    data = b"Line one  \r\n\r\n\r\n\r\n\r\n\r\nLine\x07 two\r\n"
    doc = parse(data, filename="a.txt")
    assert doc.text == "Line one\n\n\nLine two"
    assert doc.checksum == hashlib.sha256(data).hexdigest()
    assert doc.checksum != hashlib.sha256(doc.text.encode()).hexdigest()


def test_document_is_frozen() -> None:
    doc = parse(b"hello", filename="a.txt")
    with pytest.raises(ValidationError):
        doc.title = "changed"  # type: ignore[misc]


# ------------------------------------------------------------------ upload hardening (post-MVP)
def _patch_declared_size(data: bytes, name: str, size: int) -> bytes:
    """Rewrite the uncompressed size of ``name`` in both its local and central headers."""
    import struct

    out = bytearray(data)
    encoded = name.encode()
    for signature, size_offset, name_offset in ((b"PK\x03\x04", 22, 30), (b"PK\x01\x02", 24, 46)):
        start = 0
        while (pos := out.find(signature, start)) != -1:
            if out[pos + name_offset : pos + name_offset + len(encoded)] == encoded:
                struct.pack_into("<I", out, pos + size_offset, size)
            start = pos + 4
    return bytes(out)


def test_docx_member_lying_about_its_size_is_rejected() -> None:
    data = rewrite_zip(make_docx(), extra={"word/media/liar.bin": bytes(3 * MiB)})
    lying = _patch_declared_size(data, "word/media/liar.bin", 1000)
    with zipfile.ZipFile(io.BytesIO(lying)) as archive:
        assert archive.getinfo("word/media/liar.bin").file_size == 1000  # header check passes
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse_docx, lying)
    assert err.details["reason"] == "CORRUPT"


def test_docx_streamed_bytes_are_capped_even_below_the_ratio_threshold() -> None:
    # Each member is under the 1 MiB ratio-exemption line, so only real byte counting stops it.
    extra = {f"word/media/p{i}.bin": bytes(900 * 1024) for i in range(4)}
    data = rewrite_zip(make_docx(), extra=extra)
    limits = IngestionLimits(max_zip_total_uncompressed=10 * MiB)
    parse_docx(data, limits=limits)  # 3.5 MiB: fine
    from omnigrowth.ingestion.parsers.docx_parser import verify_zip_stream

    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        verify_zip_stream,
        data,
        IngestionLimits(max_zip_total_uncompressed=2 * MiB),
    )
    assert err.details["limit"] == "max_zip_total_uncompressed"


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 '
        b'"&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]><w:document>&lol2;</w:document>',
        b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b"<w:document>&xxe;</w:document>",
    ],
    ids=["billion-laughs", "external-entity"],
)
def test_docx_with_dtd_or_entities_is_rejected_before_xml_parsing(payload: bytes) -> None:
    data = rewrite_zip(make_docx(), replace={"word/document.xml": payload})
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse_docx, data)
    assert err.details["reason"] == "UNSAFE_XML"
    assert err.details["part"] == "word/document.xml"


def test_dtd_marker_split_across_chunks_is_found() -> None:
    from omnigrowth.ingestion.parsers import docx_parser

    filler = b"<!-- " + b"x" * (docx_parser._CHUNK - 8) + b" -->"
    payload = b'<?xml version="1.0"?>' + filler + b"<!DOCTYPE d []><w:document/>"
    data = rewrite_zip(make_docx(), replace={"word/document.xml": payload})
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse_docx, data)
    assert err.details["reason"] == "UNSAFE_XML"


def _fake_clock(step: float) -> Callable[[], float]:
    import itertools

    counter = itertools.count()
    return lambda: next(counter) * step


def test_parse_time_budget_is_enforced() -> None:
    from omnigrowth.ingestion.parsers.docx_parser import verify_zip_stream

    limits = IngestionLimits(max_parse_seconds=5)
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE, verify_zip_stream, make_docx(), limits, clock=_fake_clock(10)
    )
    assert err.details["limit"] == "max_parse_seconds"
    err = expect_error(
        ErrorCode.VALIDATION_FAILURE,
        parse_pdf,
        make_pdf(["Page one text."]),
        limits=limits,
        clock=_fake_clock(10),
    )
    assert err.details["limit"] == "max_parse_seconds"


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be", "utf-32-le"])
def test_docx_dtd_in_utf16_or_utf32_part_is_rejected(encoding: str) -> None:
    xml = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "b">]><w:document>&a;</w:document>'
    data = rewrite_zip(make_docx(), replace={"word/document.xml": xml.encode(encoding)})
    err = expect_error(ErrorCode.INGESTION_FAILURE, parse_docx, data)
    assert err.details["reason"] == "UNSAFE_XML"
