"""Readable-text extraction from fetched HTML (URL ingestion). Stdlib ``html.parser`` only;
nothing is executed, no resources are fetched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import check_size, decode_text, ingestion_error
from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import MetadataValue, NormalizedDocument, build_document
from omnigrowth.ingestion.sniff import MIME_HTML

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "nav",
        "footer",
        "noscript",
        "svg",
        "template",
        "iframe",
        "object",
        "embed",
        "canvas",
        "head",
        "math",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_MAIN_TAGS = frozenset({"main", "article"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "table",
        "section",
        "article",
        "main",
        "header",
        "aside",
        "blockquote",
        "pre",
        "hr",
        "dd",
        "dt",
        "dl",
        "figure",
        "figcaption",
        "td",
        "th",
    }
)
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)


@dataclass
class _Chunk:
    heading: str | None
    in_main: bool
    parts: list[str] = field(default_factory=list)


class _ReadableTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._title_parts: list[str] | None = None
        self._skip_depth = 0
        self._main_depth = 0
        self._heading_parts: list[str] | None = None
        self.chunks: list[_Chunk] = [_Chunk(None, False)]

    def _current(self) -> _Chunk:
        chunk = self.chunks[-1]
        if chunk.in_main != (self._main_depth > 0):
            chunk = _Chunk(chunk.heading, self._main_depth > 0)
            self.chunks.append(chunk)
        return chunk

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and self.title is None and self._title_parts is None:
            self._title_parts = []
            return
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in _MAIN_TAGS:
            self._main_depth += 1
        if tag in _HEADING_TAGS:
            self._heading_parts = []
        elif tag in _BLOCK_TAGS:
            self._current().parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self._skip_depth and tag in _BLOCK_TAGS:
            self._current().parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._title_parts is not None:
            self.title = " ".join("".join(self._title_parts).split()) or None
            self._title_parts = None
            return
        if tag in _SKIP_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth or tag in _VOID_TAGS:
            return
        if tag in _HEADING_TAGS and self._heading_parts is not None:
            heading = " ".join("".join(self._heading_parts).split())
            self._heading_parts = None
            if heading:
                self.chunks.append(_Chunk(heading, self._main_depth > 0))
            return
        if tag in _MAIN_TAGS and self._main_depth:
            self._main_depth -= 1
        if tag in _BLOCK_TAGS:
            self._current().parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title_parts is not None:
            self._title_parts.append(data)
            return
        if self._skip_depth:
            return
        if self._heading_parts is not None:
            self._heading_parts.append(data)
            return
        self._current().parts.append(data)


def _chunk_text(chunk: _Chunk) -> str:
    lines = ("".join(chunk.parts)).split("\n")
    return "\n".join(" ".join(line.split()) for line in lines)


def parse_html(
    data: bytes,
    base_url: str | None = None,
    *,
    limits: IngestionLimits = DEFAULT_LIMITS,
) -> NormalizedDocument:
    """Title + main readable text; headings become sections. ``source_type`` is ``URL``.

    If the page has ``<main>``/``<article>`` content, only that content is kept.
    """
    check_size(data, limits)
    markup, encoding = decode_text(data)
    parser = _ReadableTextParser()
    parser.feed(markup)
    parser.close()
    chunks = parser.chunks
    if any(c.in_main and (c.heading or "".join(c.parts).strip()) for c in chunks):
        chunks = [c for c in chunks if c.in_main]
    sections = [(c.heading, _chunk_text(c)) for c in chunks]
    text = "\n\n".join((f"{heading}\n{body}" if heading else body) for heading, body in sections)
    if not text.strip():
        raise ingestion_error("page contains no readable text", "NO_TEXT")
    metadata: dict[str, MetadataValue] = {"encoding": encoding, "base_url": base_url}
    return build_document(
        data=data,
        source_type=SourceType.URL,
        text=text,
        limits=limits,
        detected_mime=MIME_HTML,
        title=parser.title,
        sections=sections,
        metadata=metadata,
    )
