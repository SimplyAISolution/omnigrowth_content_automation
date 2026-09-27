"""Does cancelling a client request stop generation inside LM Studio? (Phase 9 item 2)

Starts a long non-streaming generation (the production request mode), cancels the client
task after ``CANCEL_AFTER_S``, and samples three independent signals every 0.25 s:
LM Studio's own model status (``lms ps --json``: status/queued), GPU utilisation
(``nvidia-smi``) and the server log (``lms log stream``). A control run without
cancellation shows how long the same request keeps the model busy when left alone.

Usage: ``uv run python scripts/probe_cancellation.py`` with only the configured model
loaded. Writes ``docs/audit/live/cancellation_<UTC>.json``.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

LMS = str(Path.home() / ".lmstudio" / "bin" / "lms")
BASE = "http://127.0.0.1:1234/v1"
MODEL = "qwen/qwen3.5-9b"
CANCEL_AFTER_S = 4.0
OBSERVE_S = 20.0
PROMPT = "Write the numbers from 1 to 3000 separated by commas, then explain each digit."


def _status() -> dict[str, Any]:
    out = subprocess.run([LMS, "ps", "--json"], capture_output=True, text=True, timeout=10)  # noqa: S603
    for model in json.loads(out.stdout or "[]"):
        if model.get("identifier") == MODEL:
            return {"status": model.get("status"), "queued": model.get("queued")}
    return {"status": "not-loaded"}


def _gpu() -> int | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
        )
        return int(out.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


async def _sampler(t0: float, samples: list[dict[str, Any]], stop: asyncio.Event) -> None:
    while not stop.is_set():
        status, gpu = await asyncio.gather(asyncio.to_thread(_status), asyncio.to_thread(_gpu))
        samples.append({"t": round(time.monotonic() - t0, 2), **status, "gpu_util": gpu})
        await asyncio.sleep(0.25)


async def _generate(client: httpx.AsyncClient, max_tokens: int) -> dict[str, Any]:
    resp = await client.post(
        f"{BASE}/chat/completions",
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": PROMPT}],
            "max_tokens": max_tokens,
            "temperature": 0,
        },
    )
    return dict(resp.json().get("usage") or {})


async def run(cancel: bool, max_tokens: int) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    stop = asyncio.Event()
    t0 = time.monotonic()
    sampler = asyncio.create_task(_sampler(t0, samples, stop))
    result: dict[str, Any] = {"cancel": cancel, "max_tokens": max_tokens}
    async with httpx.AsyncClient(timeout=600) as client:
        task = asyncio.create_task(_generate(client, max_tokens))
        if cancel:
            await asyncio.sleep(CANCEL_AFTER_S)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                result["cancelled_at_s"] = round(time.monotonic() - t0, 2)
            await asyncio.sleep(OBSERVE_S)
        else:
            result["usage"] = await task
            result["finished_at_s"] = round(time.monotonic() - t0, 2)
            await asyncio.sleep(3)
    stop.set()
    await sampler
    result["samples"] = samples
    busy = [s["t"] for s in samples if s.get("status") not in ("idle", "not-loaded")]
    result["last_busy_status_s"] = max(busy) if busy else None
    if cancel:
        after = [s for s in samples if s["t"] >= result["cancelled_at_s"]]
        idle = [s["t"] for s in after if s.get("status") == "idle"]
        result["first_idle_after_cancel_s"] = min(idle) if idle else None
    return result


async def main() -> None:
    log_path = Path("docs/audit/live") / f"cancellation_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        streamer = subprocess.Popen(  # noqa: S603, ASYNC220 - log streamer runs alongside
            [LMS, "log", "stream", "--source", "server"], stdout=log, stderr=subprocess.STDOUT
        )
        try:
            await asyncio.sleep(1.0)
            control = await run(cancel=False, max_tokens=400)
            cancelled = await run(cancel=True, max_tokens=3000)
        finally:
            streamer.terminate()
    report = {
        "ran_at": datetime.now(UTC).isoformat(),
        "model": MODEL,
        "request_mode": "non-streaming /v1/chat/completions (production mode)",
        "control_uncancelled": control,
        "cancelled": cancelled,
        "server_log": str(log_path),
    }
    out = log_path.with_suffix(".json")
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in cancelled.items() if k != "samples"}, indent=2))
    print("control:", {k: v for k, v in control.items() if k != "samples"})
    print("report:", out)


if __name__ == "__main__":
    asyncio.run(main())
