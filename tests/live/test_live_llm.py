"""Live smoke test against the local LLM endpoint (PHASES.md Phase 3 gate).

Opt-in only: ``uv run poe test-live`` (marker ``live_llm``). Uses the real config/models.yaml,
the real prompt registry and OpenAICompatibleProvider; lower-quality output is acceptable
but it MUST pass schema validation (A8). The result is written to
``docs/audit/phase3_live_llm.json`` so the gate report can cite it.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from omnigrowth.core.enums import ModelRole, PrivacyClass
from omnigrowth.core.settings import Settings
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.factory import build_router
from omnigrowth.providers.probe import ProbeOutput
from omnigrowth.providers.structured import (
    CapabilityCache,
    InMemoryRecorder,
    RunContext,
    generate_structured,
)

pytestmark = pytest.mark.live_llm

REPO = Path(__file__).resolve().parents[2]
REPORT = REPO / "docs" / "audit" / "phase3_live_llm.json"


async def test_structured_probe_against_local_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    # The root conftest points everything at unroutable test URLs; the live test needs the
    # real LLM endpoint only (no DB / Redis).
    for key in ("OMNIGROWTH_LOCAL_LLM_URL", "OMNIGROWTH_RUNTIME"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        OMNIGROWTH_CONFIG_DIR=str(REPO / "config"),
        OMNIGROWTH_LOCAL_LLM_URL=os.environ.get("LIVE_LLM_URL") or None,
    )
    router = build_router(settings)
    registry = PromptRegistry.load()
    prompt = registry.get("system/structured_probe")
    rendered = prompt.render({"topic": "Local AI agents versus cloud-hosted chatbots"})
    recorder = InMemoryRecorder()
    try:
        result = await generate_structured(
            ProbeOutput,
            rendered,
            router=router,
            context=RunContext(
                task="phase3.live_smoke",
                role=ModelRole.CHEAP_LOCAL,
                privacy=PrivacyClass.PUBLIC,
                correlation_id="phase3-live",
            ),
            recorder=recorder,
            capabilities=CapabilityCache(),
            temperature=prompt.front_matter.temperature,
            max_tokens=None,
        )
    finally:
        await router.aclose()

    assert isinstance(result.value, ProbeOutput)
    REPORT.write_text(
        json.dumps(
            {
                "ran_at": datetime.now(UTC).isoformat(),
                "prompt": rendered.prompt_ref,
                "provider": result.model.provider_name,
                "model": result.model.model,
                "structured_mode": str(result.mode),
                "attempts": result.attempts,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "runs": [
                    {
                        "attempt_no": r.attempt_no,
                        "status": str(r.status),
                        "error_code": str(r.error_code) if r.error_code else None,
                        "latency_ms": r.latency_ms,
                        "mode": str(r.structured_output_mode),
                    }
                    for r in recorder.records
                ],
                "output": result.value.model_dump(),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
