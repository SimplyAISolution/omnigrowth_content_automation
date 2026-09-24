"""Integration: generate_structured persists model_runs rows that satisfy the DB constraints."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ModelRole, PrivacyClass
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import ModelRun
from omnigrowth.prompts import PromptRegistry
from omnigrowth.providers.base import ChatResult
from omnigrowth.providers.config import ModelsConfig
from omnigrowth.providers.fake import FakeProvider
from omnigrowth.providers.probe import ProbeOutput
from omnigrowth.providers.router import ModelRouter
from omnigrowth.providers.structured import CapabilityCache, RunContext, generate_structured
from omnigrowth.storage.model_runs import DbRunRecorder
from omnigrowth.storage.prompt_versions import sync_prompt_versions

pytestmark = pytest.mark.integration

CONFIG = ModelsConfig.model_validate(
    {
        "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
        "providers": {"local": {"locality": "LOCAL", "preset": "p"}},
        "roles": {"CHEAP_LOCAL": [{"provider": "local", "model": "m"}]},
    }
)
GOOD = '{"topic": "local ai", "keywords": ["agents", "privacy", "cost"]}'


def result(text: str) -> ChatResult:
    return ChatResult(text=text, input_tokens=30, output_tokens=12, latency_ms=4)


async def _generate(session: AsyncSession, script: list[ChatResult]) -> None:
    registry = PromptRegistry.load()
    ids = await sync_prompt_versions(session, registry)
    prompt = registry.get("system/structured_probe")
    rendered = prompt.render({"topic": "local ai"})
    router = ModelRouter(CONFIG, {"local": FakeProvider("local", script=script)})
    await generate_structured(
        ProbeOutput,
        rendered,
        router=router,
        context=RunContext(
            task="itest.probe",
            role=ModelRole.CHEAP_LOCAL,
            privacy=PrivacyClass.PUBLIC,
            correlation_id="itest",
            prompt_version_id=ids[prompt.ref],
        ),
        recorder=DbRunRecorder(session),
        capabilities=CapabilityCache(),
    )


class _Row(BaseModel):
    attempt_no: int
    status: str
    error_code: str | None
    raw_response: str | None


async def _rows(session: AsyncSession) -> list[_Row]:
    rows = (
        await session.execute(
            select(ModelRun).where(ModelRun.correlation_id == "itest").order_by(ModelRun.attempt_no)
        )
    ).scalars()
    return [
        _Row(
            attempt_no=r.attempt_no,
            status=str(r.status),
            error_code=str(r.error_code) if r.error_code else None,
            raw_response=r.raw_response,
        )
        for r in rows
    ]


async def test_successful_run_is_persisted(session: AsyncSession) -> None:
    await _generate(session, [result(GOOD)])
    rows = await _rows(session)
    assert len(rows) == 1
    assert rows[0].status.endswith("SUCCEEDED") and rows[0].error_code is None


async def test_invalid_output_persists_every_attempt_with_raw(session: AsyncSession) -> None:
    with pytest.raises(OmniGrowthError) as exc:
        await _generate(session, [result("nope"), result("{}"), result('{"topic": ""}')])
    assert exc.value.code is ErrorCode.LLM_OUTPUT_INVALID
    rows = await _rows(session)
    assert [r.attempt_no for r in rows] == [0, 1, 2]
    assert all(r.error_code and r.error_code.endswith("LLM_OUTPUT_INVALID") for r in rows)
    assert [r.raw_response for r in rows] == ["nope", "{}", '{"topic": ""}']


async def test_provider_offline_is_persisted_as_failed_run(session: AsyncSession) -> None:
    offline = OmniGrowthError(ErrorCode.MODEL_UNAVAILABLE, "offline", retryable=True)
    with pytest.raises(OmniGrowthError) as exc:
        registry = PromptRegistry.load()
        rendered = registry.get("system/structured_probe").render({"topic": "x"})
        await generate_structured(
            ProbeOutput,
            rendered,
            router=ModelRouter(CONFIG, {"local": FakeProvider("local", script=[offline])}),
            context=RunContext(
                task="itest.probe",
                role=ModelRole.CHEAP_LOCAL,
                privacy=PrivacyClass.PUBLIC,
                correlation_id="itest",
            ),
            recorder=DbRunRecorder(session),
            capabilities=CapabilityCache(),
        )
    assert exc.value.code is ErrorCode.MODEL_UNAVAILABLE
    rows = await _rows(session)
    assert len(rows) == 1 and rows[0].error_code
    assert rows[0].error_code.endswith("MODEL_UNAVAILABLE")
