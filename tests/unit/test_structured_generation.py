"""``generate_structured``: validation, repair loop, fallback, modes, privacy, concurrency (§35)."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest
from pydantic import BaseModel

from omnigrowth.core import aio
from omnigrowth.core.enums import (
    ModelLocality,
    ModelRole,
    ModelRunStatus,
    PrivacyClass,
    RoutingProfile,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.base import (
    ChatRequest,
    ChatResult,
    StructuredMode,
    UnsupportedResponseFormatError,
)
from omnigrowth.providers.config import ModelsConfig
from omnigrowth.providers.fake import FakeProvider
from omnigrowth.providers.router import ModelRouter, ResolvedModel
from omnigrowth.providers.structured import (
    CapabilityCache,
    InMemoryRecorder,
    RunContext,
    extract_json_text,
    generate_structured,
    probe_structured_mode,
)


class Answer(BaseModel):
    topic: str
    keywords: list[str]


GOOD = '{"topic": "local ai", "keywords": ["a", "b", "c"]}'


@dataclass(frozen=True)
class StubPrompt:
    prompt_ref: str = "system/structured_probe@v1"
    prompt_id: str = "system/structured_probe"
    version: int = 1
    input_hash: str = "0" * 64
    role: ModelRole = ModelRole.CHEAP_LOCAL
    privacy_class: PrivacyClass = PrivacyClass.PUBLIC
    temperature: float = 0.1
    max_tokens: int | None = None
    messages: list[dict[str, str]] = field(
        default_factory=lambda: [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Topic: local ai"},
        ]
    )


def ok(text: str = GOOD) -> ChatResult:
    return ChatResult(text=text, input_tokens=20, output_tokens=10, latency_ms=5)


def err(code: ErrorCode) -> OmniGrowthError:
    return OmniGrowthError(code, f"injected {code}", retryable=True)


def config(two_local: bool = True, with_remote: bool = False) -> ModelsConfig:
    candidates = [{"provider": "primary", "model": "local-a"}]
    if two_local:
        candidates.append({"provider": "secondary", "model": "local-b"})
    if with_remote:
        candidates.append(
            {
                "provider": "cloud",
                "model": "remote-x",
                "input_cost_per_mtok": "3",
                "output_cost_per_mtok": "15",
            }
        )
    return ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {
                "primary": {"locality": "LOCAL", "preset": "p"},
                "secondary": {"locality": "LOCAL", "preset": "p"},
                "cloud": {"locality": "REMOTE", "base_url": "https://c/v1", "uses_cloud_key": True},
            },
            "roles": {"CHEAP_LOCAL": candidates},
        }
    )


def context(
    privacy: PrivacyClass = PrivacyClass.PUBLIC,
    profile: RoutingProfile = RoutingProfile.LOCAL_ONLY,
) -> RunContext:
    return RunContext(
        task="unit.test",
        role=ModelRole.CHEAP_LOCAL,
        privacy=privacy,
        correlation_id="corr-1",
        profile=profile,
    )


async def run(
    providers: dict[str, FakeProvider],
    *,
    cfg: ModelsConfig | None = None,
    ctx: RunContext | None = None,
    capabilities: CapabilityCache | None = None,
    recorder: InMemoryRecorder | None = None,
) -> tuple[Answer, InMemoryRecorder]:
    rec = recorder or InMemoryRecorder()
    router = ModelRouter(cfg or config(), dict(providers))
    result = await generate_structured(
        Answer,
        StubPrompt(),
        router=router,
        context=ctx or context(),
        recorder=rec,
        capabilities=capabilities or CapabilityCache(),
    )
    return result.value, rec


# ------------------------------------------------------------------ happy path
async def test_valid_output_first_try_records_one_run() -> None:
    primary = FakeProvider("primary", script=[ok()])
    value, rec = await run({"primary": primary, "secondary": FakeProvider("secondary", script=[])})
    assert value.keywords == ["a", "b", "c"]
    assert len(rec.records) == 1
    record = rec.records[0]
    assert record.status is ModelRunStatus.SUCCEEDED
    assert record.attempt_no == 0 and record.raw_response is None
    assert record.estimated_cost_usd == 0  # local
    assert record.structured_output_mode is StructuredMode.JSON_SCHEMA
    request = primary.requests[0]
    assert request.response_format is not None
    assert request.response_format["type"] == "json_schema"
    assert request.meta["input_hash"] == "0" * 64


@pytest.mark.parametrize(
    "text",
    [
        "<think>let me reason {not json}</think>\n" + GOOD,
        "```json\n" + GOOD + "\n```",
        "Sure! Here it is: " + GOOD + " Hope that helps.",
    ],
)
async def test_reasoning_blocks_and_fences_are_tolerated(text: str) -> None:
    value, _ = await run({"primary": FakeProvider("primary", script=[ok(text)])})
    assert value.topic == "local ai"


def test_unterminated_think_prefix_is_stripped() -> None:
    assert extract_json_text('reasoning...</think>{"a": 1}') == '{"a": 1}'


# ----------------------------------------------------- repair loop (gate: malformed JSON x3)
async def test_malformed_json_three_times_raises_llm_output_invalid_with_raw() -> None:
    bad = ["not json at all", '{"topic": 1', '{"topic": "x"}']  # 3 invalid outputs
    primary = FakeProvider("primary", script=[ok(b) for b in bad])
    secondary = FakeProvider("secondary", script=[ok()])
    rec = InMemoryRecorder()
    with pytest.raises(OmniGrowthError) as exc:
        await run({"primary": primary, "secondary": secondary}, recorder=rec)
    assert exc.value.code is ErrorCode.LLM_OUTPUT_INVALID
    assert [r.attempt_no for r in rec.records] == [0, 1, 2]
    assert all(r.status is ModelRunStatus.FAILED for r in rec.records)
    assert all(r.error_code is ErrorCode.LLM_OUTPUT_INVALID for r in rec.records)
    assert [r.raw_response for r in rec.records] == bad  # raw payload persisted (AGENTS.md §4)
    assert rec.records[0].repair_of_model_run_id is None
    assert rec.records[1].repair_of_model_run_id is not None
    assert secondary.requests == []  # invalid output is not a fallback trigger
    # Repair prompts carry the validation errors back to the model.
    repair_request = primary.requests[1]
    assert repair_request.messages[-2]["role"] == "assistant"
    assert "invalid" in repair_request.messages[-1]["content"]


async def test_repair_succeeds_on_second_attempt() -> None:
    primary = FakeProvider("primary", script=[ok("{oops"), ok()])
    value, rec = await run({"primary": primary})
    assert value.topic == "local ai"
    assert [r.status for r in rec.records] == [ModelRunStatus.FAILED, ModelRunStatus.SUCCEEDED]


# ------------------------------------------------------------------ failure modes + fallback
@pytest.mark.parametrize(
    "code",
    [ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_TIMEOUT, ErrorCode.PROVIDER_RATE_LIMIT],
)
async def test_transport_failure_falls_back_to_next_model(code: ErrorCode) -> None:
    primary = FakeProvider("primary", script=[err(code)])
    secondary = FakeProvider("secondary", script=[ok()])
    value, rec = await run({"primary": primary, "secondary": secondary})
    assert value.topic == "local ai"
    assert rec.records[0].status is ModelRunStatus.FAILED
    assert rec.records[0].error_code is code
    assert rec.records[1].model.provider_name == "secondary"


@pytest.mark.parametrize(
    "code",
    [ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_TIMEOUT, ErrorCode.PROVIDER_RATE_LIMIT],
)
async def test_all_models_failing_raises_categorized_error(code: ErrorCode) -> None:
    providers = {
        "primary": FakeProvider("primary", script=[err(code)]),
        "secondary": FakeProvider("secondary", script=[err(code)]),
    }
    rec = InMemoryRecorder()
    with pytest.raises(OmniGrowthError) as exc:
        await run(providers, recorder=rec)
    assert exc.value.code is code
    assert exc.value.details["attempted"] == ["primary:local-a", "secondary:local-b"]
    assert len(rec.records) == 2


async def test_non_transport_errors_do_not_fall_back() -> None:
    primary = FakeProvider("primary", script=[err(ErrorCode.CONFIGURATION_ERROR)])
    secondary = FakeProvider("secondary", script=[ok()])
    with pytest.raises(OmniGrowthError) as exc:
        await run({"primary": primary, "secondary": secondary})
    assert exc.value.code is ErrorCode.CONFIGURATION_ERROR
    assert secondary.requests == []


# ------------------------------------------------------------------ structured-mode downgrade
async def test_unsupported_modes_downgrade_to_prompt_only_and_are_cached() -> None:
    primary = FakeProvider(
        "primary",
        script=[
            UnsupportedResponseFormatError("json_schema", "nope"),
            UnsupportedResponseFormatError("json_object", "nope"),
            ok(),
            ok(),
        ],
    )
    caps = CapabilityCache()
    value, rec = await run({"primary": primary}, capabilities=caps)
    assert value.topic == "local ai"
    final = primary.requests[2]
    assert final.response_format is None
    assert "JSON Schema" in final.messages[0]["content"]  # schema moved into the prompt
    assert [r.structured_output_mode for r in rec.records] == [
        StructuredMode.JSON_SCHEMA,  # rejected, recorded
        StructuredMode.JSON_OBJECT,  # rejected, recorded
        StructuredMode.PROMPT_ONLY,  # succeeded
    ]
    # Second call starts directly in the cached mode.
    await run({"primary": primary}, capabilities=caps)
    assert primary.requests[3].response_format is None


# ------------------------------------------------------------------ privacy, budget, concurrency
async def test_private_task_never_calls_remote_even_quality_first() -> None:
    cloud = FakeProvider("cloud", script=[ok()])
    primary = FakeProvider("primary", script=[ok()])
    await run(
        {"primary": primary, "cloud": cloud},
        cfg=config(two_local=False, with_remote=True),
        ctx=context(PrivacyClass.PRIVATE, RoutingProfile.QUALITY_FIRST),
    )
    assert cloud.requests == []
    assert len(primary.requests) == 1


async def test_budget_guard_runs_before_remote_calls_only() -> None:
    checked: list[ResolvedModel] = []

    class Guard:
        async def check(self, model: ResolvedModel, context: RunContext) -> None:
            checked.append(model)
            raise OmniGrowthError(ErrorCode.BUDGET_EXCEEDED, "over budget")

    cloud = FakeProvider("cloud", script=[ok()])
    router = ModelRouter(config(two_local=False, with_remote=True), {"cloud": cloud})
    with pytest.raises(OmniGrowthError) as exc:
        await generate_structured(
            Answer,
            StubPrompt(),
            router=router,
            context=context(PrivacyClass.PUBLIC, RoutingProfile.QUALITY_FIRST),
            recorder=InMemoryRecorder(),
            budget=Guard(),
        )
    assert exc.value.code is ErrorCode.BUDGET_EXCEEDED
    assert [m.locality for m in checked] == [ModelLocality.REMOTE]
    assert cloud.requests == []


async def test_local_concurrency_is_one_request_at_a_time() -> None:
    in_flight = 0
    peak = 0

    class SlowProvider(FakeProvider):
        async def chat(self, request: ChatRequest) -> ChatResult:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return ok()

    slow = SlowProvider("primary", script=[])
    router = ModelRouter(config(two_local=False), {"primary": slow})
    await asyncio.gather(
        *[
            generate_structured(
                Answer,
                StubPrompt(),
                router=router,
                context=context(),
                recorder=InMemoryRecorder(),
                capabilities=CapabilityCache(),
            )
            for _ in range(4)
        ]
    )
    assert peak == 1  # A2: queued, not failed


async def test_remote_cost_is_estimated_from_config() -> None:
    cloud = FakeProvider("cloud", script=[ok()])
    router = ModelRouter(config(two_local=False, with_remote=True), {"cloud": cloud})
    rec = InMemoryRecorder()
    await generate_structured(
        Answer,
        StubPrompt(),
        router=router,
        context=context(PrivacyClass.PUBLIC, RoutingProfile.QUALITY_FIRST),
        recorder=rec,
        capabilities=CapabilityCache(),
    )
    # 20 input tokens * $3/M + 10 output tokens * $15/M
    assert str(rec.records[0].estimated_cost_usd) == "0.00021"


# ------------------------------------------------------------------ Phase 3 review fixes
async def test_prompt_privacy_overrides_weaker_context() -> None:
    """A PRIVATE prompt run with a PUBLIC context must still never reach a REMOTE model."""
    cloud = FakeProvider("cloud", script=[ok()])
    primary = FakeProvider("primary", script=[ok()])
    rec = InMemoryRecorder()
    router = ModelRouter(
        config(two_local=False, with_remote=True), {"primary": primary, "cloud": cloud}
    )
    await generate_structured(
        Answer,
        StubPrompt(privacy_class=PrivacyClass.PRIVATE),
        router=router,
        context=context(PrivacyClass.PUBLIC, RoutingProfile.QUALITY_FIRST),
        recorder=rec,
        capabilities=CapabilityCache(),
    )
    assert cloud.requests == []
    assert rec.records[0].context.privacy is PrivacyClass.PRIVATE


async def test_role_mismatch_is_rejected() -> None:
    router = ModelRouter(config(), {"primary": FakeProvider("primary", script=[ok()])})
    with pytest.raises(OmniGrowthError) as exc:
        await generate_structured(
            Answer,
            StubPrompt(role=ModelRole.FRONTIER_CREATIVE),
            router=router,
            context=context(),
            recorder=InMemoryRecorder(),
        )
    assert exc.value.code is ErrorCode.VALIDATION_FAILURE


async def test_prompt_temperature_and_max_tokens_are_defaults() -> None:
    primary = FakeProvider("primary", script=[ok()])
    router = ModelRouter(config(), {"primary": primary})
    await generate_structured(
        Answer,
        StubPrompt(temperature=0.0, max_tokens=321),
        router=router,
        context=context(),
        recorder=InMemoryRecorder(),
        capabilities=CapabilityCache(),
    )
    assert primary.requests[0].temperature == 0.0
    assert primary.requests[0].max_tokens == 321


async def test_invalid_raw_output_is_masked_before_persisting() -> None:
    leaky = "not json, but api_key=sk-abcdefghijklmnopqrstuvwxyz leaked"
    primary = FakeProvider("primary", script=[ok(leaky)] * 3)
    rec = InMemoryRecorder()
    with pytest.raises(OmniGrowthError):
        await run({"primary": primary}, recorder=rec)
    assert all("sk-abcdefghijklmnop" not in (r.raw_response or "") for r in rec.records)


async def test_format_rejection_is_recorded_as_a_run() -> None:
    primary = FakeProvider(
        "primary", script=[UnsupportedResponseFormatError("json_schema", "no"), ok()]
    )
    _value, rec = await run({"primary": primary})
    assert [r.status for r in rec.records] == [ModelRunStatus.FAILED, ModelRunStatus.SUCCEEDED]
    assert rec.records[0].error_code is ErrorCode.VALIDATION_FAILURE


async def test_budget_exceeded_on_remote_falls_back_to_local() -> None:
    class Guard:
        async def check(self, model: ResolvedModel, context: RunContext) -> None:
            raise OmniGrowthError(ErrorCode.BUDGET_EXCEEDED, "over budget")

    cloud = FakeProvider("cloud", script=[ok()])
    primary = FakeProvider("primary", script=[ok()])
    router = ModelRouter(
        config(two_local=False, with_remote=True), {"primary": primary, "cloud": cloud}
    )
    result = await generate_structured(
        Answer,
        StubPrompt(),
        router=router,
        context=context(PrivacyClass.PUBLIC, RoutingProfile.QUALITY_FIRST),
        recorder=InMemoryRecorder(),
        budget=Guard(),
        capabilities=CapabilityCache(),
    )
    assert result.model.provider_name == "primary"
    assert cloud.requests == []


def test_remote_model_without_prices_has_unknown_cost() -> None:
    model = ResolvedModel(
        role=ModelRole.CHEAP_LOCAL,
        provider_name="ollama",
        model="x:cloud",
        locality=ModelLocality.REMOTE,
        input_cost_per_mtok=__import__("decimal").Decimal(0),
        output_cost_per_mtok=__import__("decimal").Decimal(0),
    )
    assert model.estimate_cost(100, 100) is None


def test_router_is_reusable_across_event_loops() -> None:
    """RQ jobs each run their own loop; per-loop semaphores must not break reuse."""
    primary = FakeProvider("primary", script=[ok(), ok()])
    router = ModelRouter(config(two_local=False), {"primary": primary})

    async def once() -> str:
        result = await generate_structured(
            Answer,
            StubPrompt(),
            router=router,
            context=context(),
            recorder=InMemoryRecorder(),
            capabilities=CapabilityCache(),
        )
        return result.value.topic

    assert aio.run(once) == "local ai"
    assert aio.run(once) == "local ai"


async def test_probe_finds_best_working_mode_and_caches_it() -> None:
    primary = FakeProvider(
        "primary",
        script=[UnsupportedResponseFormatError("json_schema", "no"), ok()],
    )
    router = ModelRouter(config(two_local=False), {"primary": primary})
    model = router.candidates(
        ModelRole.CHEAP_LOCAL, PrivacyClass.PUBLIC, RoutingProfile.LOCAL_ONLY
    )[0]
    caps = CapabilityCache()
    mode = await probe_structured_mode(
        Answer, StubPrompt(), router=router, model=model, capabilities=caps
    )
    assert mode is StructuredMode.JSON_OBJECT
    assert caps.get(model) is StructuredMode.JSON_OBJECT


async def test_probe_returns_none_when_no_mode_validates() -> None:
    primary = FakeProvider("primary", script=[ok("nope"), ok("nope"), ok("nope")])
    router = ModelRouter(config(two_local=False), {"primary": primary})
    model = router.candidates(
        ModelRole.CHEAP_LOCAL, PrivacyClass.PUBLIC, RoutingProfile.LOCAL_ONLY
    )[0]
    assert (
        await probe_structured_mode(
            Answer, StubPrompt(), router=router, model=model, capabilities=CapabilityCache()
        )
        is None
    )


async def test_providers_in_one_concurrency_group_share_a_slot() -> None:
    """A2: LM Studio + Ollama on one GPU -> one local call at a time across both."""
    in_flight = 0
    peak = 0

    class Slow(FakeProvider):
        async def chat(self, request: ChatRequest) -> ChatResult:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return ok()

    cfg = ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {
                "a": {"locality": "LOCAL", "preset": "p", "concurrency_group": "gpu"},
                "b": {"locality": "LOCAL", "preset": "p", "concurrency_group": "gpu"},
            },
            "roles": {
                "CHEAP_LOCAL": [{"provider": "a", "model": "m1"}],
                "REASONING_LOCAL": [{"provider": "b", "model": "m2"}],
            },
        }
    )
    router = ModelRouter(cfg, {"a": Slow("a", script=[]), "b": Slow("b", script=[])})

    async def call(role: ModelRole) -> None:
        await generate_structured(
            Answer,
            StubPrompt(role=role),
            router=router,
            context=RunContext(
                task="t", role=role, privacy=PrivacyClass.PUBLIC, correlation_id="c"
            ),
            recorder=InMemoryRecorder(),
            capabilities=CapabilityCache(),
        )

    await asyncio.gather(*(call(r) for r in [ModelRole.CHEAP_LOCAL, ModelRole.REASONING_LOCAL] * 2))
    assert peak == 1


# ------------------------------------------------------------------ inference guards (post-MVP)
async def _run_prompt(chars: int, max_tokens: int | None) -> FakeProvider:
    primary = FakeProvider("primary", script=[ok()])
    prompt = StubPrompt(messages=[{"role": "user", "content": "x" * chars}], max_tokens=max_tokens)
    router = ModelRouter(config(two_local=False), {"primary": primary})
    await generate_structured(
        Answer,
        prompt,
        router=router,
        context=context(),
        recorder=InMemoryRecorder(),
        capabilities=CapabilityCache(),
    )
    return primary


async def test_oversized_prompt_is_refused_before_any_model_call() -> None:
    # Default budget: 32768 tokens at a pessimistic 3 chars/token.
    with pytest.raises(OmniGrowthError) as info:
        await _run_prompt(100_000, None)  # ceil(100000/3) = 33334 > 32768
    assert info.value.code is ErrorCode.VALIDATION_FAILURE
    assert info.value.details["limit"] == "context_tokens"
    # This synthetic provider has no tokenizer: the count is the APPROXIMATE estimate and is
    # labelled so (33,334 for the text + the response_format schema, counted separately).
    assert info.value.details["counted_by"] == "approximate"
    assert info.value.details["exact"] is False
    assert info.value.details["prompt_tokens"] == 33_334
    assert info.value.details["schema_tokens"] > 0
    assert info.value.details["max_output_tokens"] == 4_096  # max_tokens unset -> default


async def test_context_budget_counts_the_output_allowance() -> None:
    # 60000 chars -> 20000 tokens; + 8192 output = 28192 fits; + 16000 = 36000 does not.
    assert len((await _run_prompt(60_000, 8_192)).requests) == 1
    with pytest.raises(OmniGrowthError) as info:
        await _run_prompt(60_000, 16_000)
    assert info.value.details["max_output_tokens"] == 16_000
    assert info.value.details["max"] == 32_768


async def test_a_call_past_its_total_deadline_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnigrowth.providers import router as router_module

    monkeypatch.setattr(router_module, "CALL_DEADLINE_MARGIN_S", 0.0)

    class Trickle(FakeProvider):
        async def chat(self, request: ChatRequest) -> ChatResult:
            await asyncio.sleep(5)  # e.g. a server trickling bytes under the read timeout
            return ok()  # pragma: no cover

    cfg = ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {"primary": {"locality": "LOCAL", "preset": "p", "timeout_s": 0.05}},
            "roles": {"CHEAP_LOCAL": [{"provider": "primary", "model": "m"}]},
        }
    )
    router = ModelRouter(cfg, {"primary": Trickle("primary", script=[])})
    with pytest.raises(OmniGrowthError) as info:
        await generate_structured(
            Answer,
            StubPrompt(),
            router=router,
            context=context(),
            recorder=InMemoryRecorder(),
            capabilities=CapabilityCache(),
        )
    assert info.value.code is ErrorCode.MODEL_TIMEOUT


class RecordingSlots:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, float, float]] = []
        self.held = 0
        self.closed = False

    @asynccontextmanager
    async def slot(
        self, key: str, limit: int, *, lease_s: float, max_wait_s: float
    ) -> AsyncIterator[str]:
        self.calls.append((key, limit, lease_s, max_wait_s))
        self.held += 1
        try:
            yield key
        finally:
            self.held -= 1

    async def aclose(self) -> None:
        self.closed = True


async def test_every_model_call_holds_a_cross_process_slot() -> None:
    slots = RecordingSlots()
    held_during_call: list[int] = []

    class Probe(FakeProvider):
        async def chat(self, request: ChatRequest) -> ChatResult:
            held_during_call.append(slots.held)
            return ok()

    cfg = ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {
                "primary": {
                    "locality": "LOCAL",
                    "preset": "p",
                    "concurrency_group": "gpu",
                    "timeout_s": 50,
                    "slot_wait_s": 7,
                    "slot_lease_s": 12,
                }
            },
            "roles": {"CHEAP_LOCAL": [{"provider": "primary", "model": "m"}]},
        }
    )
    router = ModelRouter(cfg, {"primary": Probe("primary", script=[])}, slots=slots)
    await generate_structured(
        Answer,
        StubPrompt(),
        router=router,
        context=context(),
        recorder=InMemoryRecorder(),
        capabilities=CapabilityCache(),
    )
    assert held_during_call == [1]
    assert slots.calls == [("group:gpu", 1, 12.0, 7.0)]
    await router.aclose()
    assert slots.closed


async def test_embedding_budget_is_per_text_and_clipping_is_explicit() -> None:
    from omnigrowth.providers.embeddings import embed_texts

    class Embedder(FakeProvider):
        def __init__(self) -> None:
            super().__init__("primary", script=[])
            self.inputs: list[list[str]] = []

        async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
            self.inputs.append(texts)
            return [[1.0, 0.0] for _ in texts]

    cfg = ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {"primary": {"locality": "LOCAL", "preset": "p"}},
            "roles": {
                "EMBEDDING_LOCAL": [{"provider": "primary", "model": "e", "context_tokens": 2048}]
            },
        }
    )
    embedder = Embedder()
    router = ModelRouter(cfg, {"primary": embedder})
    short, long_text = "a" * 6_000, "b" * 7_000  # budget: 2048 tokens * 3 chars = 6144 chars
    # Two short texts fit individually although their sum exceeds the budget.
    await embed_texts(router, [short, short])
    with pytest.raises(OmniGrowthError) as info:
        await embed_texts(router, [short, long_text])
    assert info.value.details["limit"] == "context_tokens"
    clipped = await embed_texts(router, [short, long_text], clip=True)
    assert clipped.clipped == (1,)
    assert [len(t) for t in embedder.inputs[-1]] == [6_000, 6_144]


# ------------------------------------------------------------------ exact token counting
class ExactCounter:
    """Stands in for the LM Studio SDK counter: fixed counts and a loaded context."""

    def __init__(self, prompt: int, schema: int, loaded: int | None) -> None:
        from omnigrowth.providers.tokens import TokenCount

        self.count = TokenCount(prompt, schema, True, "lmstudio-sdk", loaded)
        self.seen: list[object] = []

    async def count_chat(self, model: str, messages: object, response_format: object) -> object:
        self.seen.append(messages)
        return self.count

    async def count_text(self, model: str, text: str, *, embedding: bool) -> object:
        return self.count

    async def aclose(self) -> None:
        return None


def _exact_config(context: int = 32_768) -> ModelsConfig:
    return ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {
                "primary": {
                    "locality": "LOCAL",
                    "preset": "p",
                    "context_tokens": context,
                    "budget_counting": "lmstudio",
                }
            },
            "roles": {"CHEAP_LOCAL": [{"provider": "primary", "model": "m"}]},
        }
    )


async def _run_exact(counter: ExactCounter, max_tokens: int | None) -> FakeProvider:
    primary = FakeProvider("primary", script=[ok()])
    router = ModelRouter(_exact_config(), {"primary": primary}, counters={"primary": counter})  # type: ignore[dict-item]
    await generate_structured(
        Answer,
        StubPrompt(max_tokens=max_tokens),
        router=router,
        context=context(),
        recorder=InMemoryRecorder(),
        capabilities=CapabilityCache(),
    )
    return primary


async def test_exact_counts_decide_and_the_loaded_context_wins() -> None:
    # 20,000 prompt + 500 schema + 8,000 output = 28,500 fits 32,768 ...
    assert len((await _run_exact(ExactCounter(20_000, 500, 32_768), 8_000)).requests) == 1
    # ... but not a server that LOADED the model with only 16,384 tokens.
    with pytest.raises(OmniGrowthError) as info:
        await _run_exact(ExactCounter(20_000, 500, 16_384), 8_000)
    details = info.value.details
    assert details["max"] == 16_384 and details["configured_context"] == 32_768
    assert details["loaded_context"] == 16_384
    assert details["counted_by"] == "lmstudio-sdk" and details["exact"] is True
    assert (details["prompt_tokens"], details["schema_tokens"]) == (20_000, 500)


async def test_a_provider_configured_for_exact_counting_never_falls_back_to_estimates() -> None:
    primary = FakeProvider("primary", script=[ok()])
    router = ModelRouter(_exact_config(), {"primary": primary})  # no counter built
    with pytest.raises(OmniGrowthError) as info:
        await generate_structured(
            Answer,
            StubPrompt(),
            router=router,
            context=context(),
            recorder=InMemoryRecorder(),
            capabilities=CapabilityCache(),
        )
    assert info.value.code is ErrorCode.CONFIGURATION_ERROR
    assert primary.requests == []


async def test_the_counter_sees_the_request_as_built() -> None:
    counter = ExactCounter(100, 10, 32_768)
    await _run_exact(counter, 1_000)
    messages = counter.seen[0]
    assert isinstance(messages, list) and messages  # the rendered request's messages


async def test_embedding_clip_converges_on_exact_counts() -> None:
    """Regression (review #11): the clip loop shrinks by the MEASURED token ratio until an
    exact count fits (here 1 token per 2 characters, far denser than the 3-char estimate)."""
    from omnigrowth.providers.embeddings import embed_texts
    from omnigrowth.providers.tokens import TokenCount

    class DenseCounter:
        async def count_chat(self, model: str, messages: object, response_format: object) -> object:
            raise AssertionError("not used")  # pragma: no cover

        async def count_text(self, model: str, text: str, *, embedding: bool) -> TokenCount:
            return TokenCount(math.ceil(len(text) / 2), 0, True, "lmstudio-sdk", 2_048)

        async def aclose(self) -> None:
            return None

    class Embedder(FakeProvider):
        def __init__(self) -> None:
            super().__init__("primary", script=[])
            self.inputs: list[list[str]] = []

        async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
            self.inputs.append(texts)
            return [[1.0] for _ in texts]

    cfg = ModelsConfig.model_validate(
        {
            "presets": {"p": {"base_url_host": "http://h/v1", "base_url_docker": "http://d/v1"}},
            "providers": {
                "primary": {"locality": "LOCAL", "preset": "p", "budget_counting": "lmstudio"}
            },
            "roles": {
                "EMBEDDING_LOCAL": [{"provider": "primary", "model": "e", "context_tokens": 2048}]
            },
        }
    )
    embedder = Embedder()
    router = ModelRouter(cfg, {"primary": embedder}, counters={"primary": DenseCounter()})  # type: ignore[dict-item]
    result = await embed_texts(router, ["x" * 20_000], clip=True)
    assert result.clipped == (0,)
    (text,) = embedder.inputs[-1]
    assert math.ceil(len(text) / 2) <= 2_048  # fits by the exact count
    assert len(text) > 3_000  # but not clipped far below the budget


async def test_token_counter_never_loads_a_model() -> None:
    """Regression (review B1): an unloaded model is refused, never loaded by counting."""
    from omnigrowth.providers.tokens import LMStudioTokenCounter

    class Session:
        def __init__(self) -> None:
            self.loaded_calls = 0

        async def list_loaded(self) -> list[object]:
            self.loaded_calls += 1
            return []

        async def model(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("model() may load onto the GPU and must never be called")

    class Client:
        llm = Session()
        embedding = Session()

    counter = LMStudioTokenCounter("http://127.0.0.1:1234/v1")
    counter._client = Client()
    with pytest.raises(OmniGrowthError) as info:
        await counter.count_chat("qwen/qwen3.5-9b", [{"role": "user", "content": "hi"}], None)
    assert info.value.details["reason"] == "MODEL_NOT_LOADED"
    with pytest.raises(OmniGrowthError):
        await counter.count_text("nomic", "hi", embedding=True)
    assert Client.llm.loaded_calls == 1 and Client.embedding.loaded_calls == 1
