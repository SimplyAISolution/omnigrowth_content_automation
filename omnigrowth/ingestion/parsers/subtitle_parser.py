"""SRT (SourceType.SUBTITLE) and WebVTT (SourceType.TRANSCRIPT) cue extraction.

Best effort: cues with malformed timestamps are skipped; zero usable cues is an
INGESTION_FAILURE. Markup (``<i>``, ``<c.x>``, ``<v Speaker>``, inline timestamps,
``{\\an8}``) is stripped; NOTE / STYLE / REGION blocks are ignored.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from omnigrowth.core.enums import SourceType
from omnigrowth.ingestion._common import check_size, decode_text, ingestion_error
from omnigrowth.ingestion.limits import DEFAULT_LIMITS, IngestionLimits
from omnigrowth.ingestion.normalized import NormalizedDocument, build_document
from omnigrowth.ingestion.parsers.text_parser import parse_text
from omnigrowth.ingestion.sniff import MIME_SRT, MIME_VTT, looks_like_srt, looks_like_vtt

_SRT_TS = r"(\d{1,2}):([0-5]\d):([0-5]\d)[,.](\d{1,3})"
_SRT_TIMING_RE = re.compile(rf"^\s*{_SRT_TS}\s*-->\s*{_SRT_TS}(?:\s.*)?$")
_VTT_TS = r"(?:(\d{1,}):)?([0-5]\d):([0-5]\d)\.(\d{3})"
_VTT_TIMING_RE = re.compile(rf"^\s*{_VTT_TS}[ \t]+-->[ \t]+{_VTT_TS}(?:[ \t].*)?$")
_TAG_RE = re.compile(r"<[^>\n]*>")
_ASS_RE = re.compile(r"\{\\[^}\n]*\}")
_BLOCK_SPLIT_RE = re.compile(r"\n[ \t]*\n+")
_VTT_SKIP_BLOCKS = ("NOTE", "STYLE", "REGION")


@dataclass(frozen=True, slots=True)
class _Cue:
    start: float
    end: float
    text: str


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000


def _clean_cue_text(lines: list[str]) -> str:
    joined = " ".join(line.strip() for line in lines if line.strip())
    joined = _ASS_RE.sub("", _TAG_RE.sub("", joined))
    return " ".join(html.unescape(joined).split())


def _parse_srt(text: str) -> list[_Cue]:
    cues: list[_Cue] = []
    for block in _BLOCK_SPLIT_RE.split(text.strip()):
        lines = block.split("\n")
        timing_idx = next((i for i, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing_idx is None:
            continue
        match = _SRT_TIMING_RE.match(lines[timing_idx])
        if match is None:
            continue  # malformed timestamp: skip this cue
        g = match.groups()
        start, end = _seconds(*g[0:4]), _seconds(*g[4:8])
        if end < start:
            continue
        cue_text = _clean_cue_text(lines[timing_idx + 1 :])
        if cue_text:
            cues.append(_Cue(start, end, cue_text))
    return cues


def _parse_vtt(text: str) -> list[_Cue]:
    cues: list[_Cue] = []
    blocks = _BLOCK_SPLIT_RE.split(text.lstrip("\ufeff").strip())
    for block in blocks[1:]:  # blocks[0] is the WEBVTT header
        lines = block.split("\n")
        if lines[0].strip().startswith(_VTT_SKIP_BLOCKS) and "-->" not in lines[0]:
            continue
        timing_idx = next((i for i, line in enumerate(lines[:2]) if "-->" in line), None)
        if timing_idx is None:
            continue
        match = _VTT_TIMING_RE.match(lines[timing_idx])
        if match is None:
            continue
        g = match.groups()
        start, end = _seconds(*g[0:4]), _seconds(*g[4:8])
        if end < start:
            continue
        cue_text = _clean_cue_text(lines[timing_idx + 1 :])
        if cue_text:
            cues.append(_Cue(start, end, cue_text))
    return cues


def _dedupe(cues: list[_Cue]) -> list[str]:
    """Drop consecutive duplicate lines (rolling auto-captions repeat them)."""
    out: list[str] = []
    for cue in cues:
        if not out or out[-1] != cue.text:
            out.append(cue.text)
    return out


def parse_subtitles(
    data: bytes,
    *,
    source_type: SourceType | None = None,
    limits: IngestionLimits = DEFAULT_LIMITS,
) -> NormalizedDocument:
    """Parse SRT or WebVTT (auto-detected). With ``source_type=TRANSCRIPT`` and content that
    is neither, the text is treated as a plain transcript.
    """
    check_size(data, limits)
    raw, encoding = decode_text(data)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if looks_like_vtt(text):
        fmt, mime, cues = "vtt", MIME_VTT, _parse_vtt(text)
        resolved = source_type or SourceType.TRANSCRIPT
    elif looks_like_srt(text) or source_type is not SourceType.TRANSCRIPT:
        fmt, mime, cues = "srt", MIME_SRT, _parse_srt(text)
        resolved = source_type or SourceType.SUBTITLE
    else:
        return parse_text(data, source_type=SourceType.TRANSCRIPT, limits=limits)
    if not cues:
        raise ingestion_error("no valid subtitle cues found", "NO_CUES", format=fmt)
    lines = _dedupe(cues)
    body = "\n".join(lines)
    return build_document(
        data=data,
        source_type=resolved,
        text=body,
        limits=limits,
        detected_mime=mime,
        sections=[(None, body)],
        metadata={
            "encoding": encoding,
            "subtitle_format": fmt,
            "cue_count": len(cues),
            "duration_seconds": int(max(c.end for c in cues)),
        },
        transcript=" ".join(lines),
    )
