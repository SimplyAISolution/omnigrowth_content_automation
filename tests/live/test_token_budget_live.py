"""Live: the context budget's token counts equal the server's own (item 3, Phase 9 pre-gate).

For each input, the exact count used for enforcement (LM Studio SDK: chat template +
tokenizer) is compared with ``usage.prompt_tokens`` that the server reports when it actually
processes the same request (``max_tokens=1``, so the GPU work is one token). Inputs cover
multilingual text, emoji (incl. ZWJ sequences and flags) and long URLs. The test also shows
where the old characters/3 estimate UNDER-counts, which is why it no longer enforces.

Opt-in: ``uv run pytest tests/live/test_token_budget_live.py -m live_llm`` with LM Studio
serving the configured chat model.
"""

from __future__ import annotations

import json
import math
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from omnigrowth.providers.tokens import LMStudioTokenCounter

pytestmark = pytest.mark.live_llm

BASE = "http://127.0.0.1:1234/v1"
MODEL = "qwen/qwen3.5-9b"
AUDIT_DIR = Path(__file__).resolve().parents[2] / "docs" / "audit" / "live"

URL = (
    "https://example.com/very/long/path/segment-"
    + "abcdef0123456789" * 12
    + "?utm_source=newsletter&utm_medium=email&utm_campaign=q4-launch&ref=%E4%BD%A0%E5%A5%BD"
)
CASES: dict[str, list[dict[str, str]]] = {
    "english": [
        {"role": "user", "content": "Local AI agents keep proprietary data on the machine."}
    ],
    "chinese": [
        {
            "role": "user",
            "content": "本地人工智能代理将专有数据保存在机器上，而不是发送到云端。" * 4,  # noqa: RUF001
        }
    ],
    "arabic": [
        {
            "role": "user",
            "content": "تحتفظ وكلاء الذكاء الاصطناعي المحليون بالبيانات على الجهاز. " * 4,
        }
    ],
    "emoji": [{"role": "user", "content": "🚀🔥 Launch day! 👩‍💻👨‍👩‍👧‍👦 🇺🇸🇯🇵🇩🇪 ✨💡🤖 " * 6}],
    "long_url": [{"role": "user", "content": f"Read more: {URL} {URL}"}],
    "mixed_system_user": [
        {
            "role": "system",
            "content": "You write LinkedIn posts. Answer in JSON only. Über-précis; 日本語OK.",
        },
        {"role": "user", "content": f"Topic: agents 🤖 — see {URL}"},
    ],
}


def _server_prompt_tokens(messages: list[dict[str, str]]) -> int:
    body = json.dumps(
        {"model": MODEL, "messages": messages, "max_tokens": 1, "temperature": 0}
    ).encode()
    req = urllib.request.Request(  # noqa: S310 - fixed local http URL
        f"{BASE}/chat/completions", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - local server
        return int(json.load(resp)["usage"]["prompt_tokens"])


async def test_exact_counts_match_the_server_and_beat_the_character_estimate() -> None:
    counter = LMStudioTokenCounter(BASE)
    rows: list[dict[str, Any]] = []
    try:
        for name, messages in CASES.items():
            ours = await counter.count_chat(MODEL, messages, None)
            server = _server_prompt_tokens(messages)
            chars = sum(len(m["content"]) for m in messages)
            rows.append(
                {
                    "case": name,
                    "characters": chars,
                    "exact_prompt_tokens": ours.prompt_tokens,
                    "server_prompt_tokens": server,
                    "approx_chars_div_3": math.ceil(chars / 3),
                    "approx_undercounts": math.ceil(chars / 3) < server,
                    "loaded_context": ours.loaded_context,
                }
            )
    finally:
        await counter.aclose()
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (AUDIT_DIR / f"token_budget_{stamp}.json").write_text(
            json.dumps({"model": MODEL, "cases": rows}, indent=2) + "\n", encoding="utf-8"
        )
    for row in rows:
        # The enforced count IS the server's count (template overhead included).
        assert row["exact_prompt_tokens"] == row["server_prompt_tokens"], row
    # The character estimate is not safe for every script: at least one case under-counts.
    assert any(row["approx_undercounts"] for row in rows), rows
