"""Measure parse times of large but legitimate inputs at the ingestion limits."""

import io
import random
import sys
import time

sys.path.insert(0, ".")
import docx

from omnigrowth.ingestion import parse_docx, parse_pdf
from omnigrowth.ingestion.limits import IngestionLimits
from omnigrowth.ingestion.parsers.docx_parser import (
    check_zip_limits,
    verify_zip_stream,
)
from tests.unit.test_ingestion_parsers import make_pdf

rng = random.Random(7)
WORDS = [
    "local",
    "agents",
    "keep",
    "data",
    "on",
    "the",
    "machine",
    "and",
    "avoid",
    "per",
    "token",
    "fees",
    "for",
    "small",
    "teams",
]


def para(n: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n))


big = IngestionLimits(max_text_chars=50_000_000, max_parse_seconds=10_000)

for pages in (100, 500):
    pdf = make_pdf([para(450) for _ in range(pages)])
    t = time.perf_counter()
    doc = parse_pdf(pdf, limits=big)
    print(
        f"PDF {pages} pages, {len(pdf) / 1e6:.1f} MB, {len(doc.text) / 1e6:.2f} M chars: {time.perf_counter() - t:.2f} s"
    )

for paragraphs in (2_000, 20_000):
    d = docx.Document()
    for _ in range(paragraphs):
        d.add_paragraph(para(60))
    buf = io.BytesIO()
    d.save(buf)
    data = buf.getvalue()
    t = time.perf_counter()
    check_zip_limits(data, big)
    verify_zip_stream(data, big)
    t_stream = time.perf_counter() - t
    t = time.perf_counter()
    doc = parse_docx(data, limits=big)
    print(
        f"DOCX {paragraphs} paras, {len(data) / 1e6:.1f} MB zipped, {len(doc.text) / 1e6:.2f} M chars: "
        f"stream check {t_stream:.2f} s, full parse {time.perf_counter() - t:.2f} s"
    )
