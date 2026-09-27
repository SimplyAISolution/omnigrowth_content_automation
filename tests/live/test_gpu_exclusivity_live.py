"""Live: lease loss and cancellation against the REAL LM Studio server (Phase 9 item 2).

Caller A starts a long generation through the production router (real Redis lease, real
idle check, real provider). Mid-generation its lease is deleted (Redis data loss). Caller B
is waiting for the same GPU. Evidence recorded from the server itself: ``lms ps --json``
status timeline and the server log. The test asserts that A was cancelled (SLOT_LOST), the
server logged the client disconnect and returned to idle, and B's generation started only
after that (server-reported idle observed between A's cancel and B's start).

Opt-in: ``uv run pytest tests/live/test_gpu_exclusivity_live.py -m live_llm`` (needs Redis
and LM Studio with the configured model). Takes ~45 s (one 30 s settle after the loss).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis

from omnigrowth.core.enums import ModelRole, PrivacyClass, RoutingProfile
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.settings import get_settings
from omnigrowth.providers.base import ChatRequest
from omnigrowth.providers.factory import build_router
from omnigrowth.providers.idle import default_lms_path
from omnigrowth.providers.slots import RedisSlotLimiter

pytestmark = pytest.mark.live_llm

AUDIT_DIR = Path(__file__).resolve().parents[2] / "docs" / "audit" / "live"


def _dotenv(key: str) -> str | None:
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == key:
            return value.split("#", 1)[0].strip() or None
    return None


# Captured at import time, before the root autouse fixture swaps in unroutable URLs.
_REDIS_URL = os.environ.get("REDIS_URL")


def _status() -> str:
    lms = default_lms_path()
    assert lms is not None
    out = subprocess.run([lms, "ps", "--json"], capture_output=True, text=True, timeout=10)  # noqa: S603
    states = {m["identifier"]: m["status"] for m in json.loads(out.stdout or "[]")}
    return "busy" if any(s != "idle" for s in states.values()) else "idle"


async def test_lease_loss_stops_generation_before_the_next_call_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in ("OMNIGROWTH_LOCAL_LLM_URL", "OMNIGROWTH_RUNTIME"):
        monkeypatch.delenv(key, raising=False)
    redis_url = _REDIS_URL or _dotenv("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL not configured")
    monkeypatch.setenv("REDIS_URL", redis_url)  # the root fixture set an unroutable one
    get_settings.cache_clear()
    settings = get_settings()
    router_a, router_b = build_router(settings), build_router(settings)  # two "workers"
    model = router_a.candidates(
        ModelRole.REASONING_LOCAL, PrivacyClass.PUBLIC, RoutingProfile.LOCAL_ONLY
    )[0]
    key, _ = router_a._concurrency_key(model.provider_name)
    slot, marker = RedisSlotLimiter.names(key, 0)
    raw = Redis.from_url(settings.redis_url.get_secret_value())
    await raw.set(marker, "1")  # start from a clean state (no settle for A)

    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = AUDIT_DIR / f"gpu_exclusivity_{stamp}.log"
    lms = default_lms_path()
    assert lms is not None
    t0 = time.monotonic()
    timeline: list[dict[str, Any]] = []
    events: dict[str, float] = {}
    stop = asyncio.Event()

    async def sample() -> None:
        # Each sample spans [t_from, t_to]: `lms ps` takes ~0.3 s, so the status belongs to
        # that interval, not to the moment the probe started.
        while not stop.is_set():
            t_from = time.monotonic() - t0
            server = await asyncio.to_thread(_status)
            t_to = time.monotonic() - t0
            timeline.append({"t_from": round(t_from, 2), "t_to": round(t_to, 2), "server": server})
            await asyncio.sleep(0.25)

    def request(tag: str, max_tokens: int) -> ChatRequest:
        return ChatRequest(
            model=model.model,
            messages=[{"role": "user", "content": f"[{tag}] Count from 1 to 5000 in words."}],
            max_tokens=max_tokens,
            temperature=0,
        )

    async def caller_a() -> None:
        try:
            async with router_a.slot(model):
                events["a_start"] = time.monotonic() - t0
                await router_a.provider(model).chat(request("A", 3000))
            events["a_finished_uncancelled"] = time.monotonic() - t0
        except OmniGrowthError as exc:
            events["a_end"] = time.monotonic() - t0
            events["a_reason"] = exc.details.get("reason")  # type: ignore[assignment]

    async def caller_b() -> None:
        await asyncio.sleep(3)
        await raw.delete(slot, marker)  # data loss while A generates
        events["lease_deleted"] = time.monotonic() - t0
        async with router_b.slot(model):
            events["b_start"] = time.monotonic() - t0
            await router_b.provider(model).chat(request("B", 20))
        events["b_end"] = time.monotonic() - t0

    with log_path.open("w", encoding="utf-8") as log:
        streamer = subprocess.Popen(  # noqa: S603, ASYNC220 - log streamer runs alongside
            [lms, "log", "stream", "--source", "server"], stdout=log, stderr=subprocess.STDOUT
        )
        sampler = asyncio.create_task(sample())
        try:
            await asyncio.gather(caller_a(), caller_b())
        finally:
            stop.set()
            await sampler
            streamer.terminate()
            await router_a.aclose()
            await router_b.aclose()
            await raw.aclose()
    server_log = log_path.read_text(encoding="utf-8", errors="replace")
    a_end, b_start = events.get("a_end", 1e9), events.get("b_start", -1.0)
    # Samples entirely inside the gap between A's cancel and B's start.
    gap = [x for x in timeline if x["t_from"] >= a_end and x["t_to"] <= b_start]
    idle_between = [x["t_to"] for x in gap if x["server"] == "idle"]
    busy_in_gap = [x for x in gap if x["server"] == "busy"]
    report = {
        "ran_at": stamp,
        "label": "real local inference (LM Studio) + real Redis lease",
        "events_s": events,
        "server_idle_observed_between_a_cancel_and_b_start": idle_between[:5],
        "busy_samples_between_a_cancel_and_b_start": busy_in_gap,
        "server_log_has_disconnect_stop": "Client disconnected. Stopping generation" in server_log,
        "timeline": timeline,
        "server_log": log_path.name,
    }
    (AUDIT_DIR / f"gpu_exclusivity_{stamp}.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8"
    )
    assert events.get("a_reason") == "SLOT_LOST", events  # A lost its lease and was cancelled
    assert report["server_log_has_disconnect_stop"], "server did not log stopping generation"
    assert idle_between, "no server-reported idle between A's cancel and B's start"
    assert not busy_in_gap, busy_in_gap  # nothing generated between A's stop and B's start
    assert events["b_start"] > events["a_end"]
