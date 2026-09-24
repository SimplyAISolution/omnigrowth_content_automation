"""``generate_structured`` — the only way agents get structured data from a model (§35, A1).

Pipeline per call:
1. Router picks ordered candidates for (role, privacy, profile). PRIVATE never goes REMOTE.
2. For each candidate (graceful fallback, §3): request structured output in the best mode the
   model supports (json_schema -> json_object -> prompt-only; downgraded and cached when the
   endpoint rejects a mode).
3. Parse (strip <think> blocks / code fences) and validate with Pydantic. Invalid -> up to
   ``max_repairs`` (2) repair attempts on the same model, then ``LLM_OUTPUT_INVALID`` with the
   raw payload persisted.
4. Every provider call — success, failure, repair — is recorded as one ``model_runs`` row.
Transport-level failures (unavailable / timeout / rate limit) fall through to the next
candidate; if every candidate fails, the last categorized error is raised.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from omnigrowth.core.enums import (
    ModelLocality,
    ModelRole,
    ModelRunStatus,
    PrivacyClass,
    RoutingProfile,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.logging import bind_context
from omnigrowth.core.masking import mask_text
from omnigrowth.providers.base import (
    ChatRequest,
    ChatResult,
    StructuredMode,
    UnsupportedResponseFormatError,
)
from omnigrowth.providers.router import ModelRouter, ResolvedModel

log = logging.getLogger(__name__)

MAX_REPAIRS = 2
FALLBACK_CODES = frozenset(
    {
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.MODEL_TIMEOUT,
        ErrorCode.PROVIDER_RATE_LIMIT,
        # Over budget on a remote model: try the next (e.g. local) candidate (spec 51).
        ErrorCode.BUDGET_EXCEEDED,
    }
)
_PRIVACY_RANK = {PrivacyClass.PUBLIC: 0, PrivacyClass.INTERNAL: 1, PrivacyClass.PRIVATE: 2}
_MODE_ORDER = (StructuredMode.JSON_SCHEMA, StructuredMode.JSON_OBJECT, StructuredMode.PROMPT_ONLY)


# ------------------------------------------------------------------ inputs / outputs
class PromptInput(Protocol):
    """What ``generate_structured`` needs from a rendered prompt (prompts.RenderedPrompt)."""

    @property
    def prompt_ref(self) -> str: ...
    @property
    def prompt_id(self) -> str: ...
    @property
    def version(self) -> int: ...
    @property
    def input_hash(self) -> str: ...
    @property
    def messages(self) -> Sequence[dict[str, str]]: ...
    @property
    def role(self) -> ModelRole: ...
    @property
    def privacy_class(self) -> PrivacyClass: ...
    @property
    def temperature(self) -> float: ...
    @property
    def max_tokens(self) -> int | None: ...


@dataclass(frozen=True, slots=True)
class RunContext:
    task: str
    role: ModelRole
    privacy: PrivacyClass
    correlation_id: str
    profile: RoutingProfile = RoutingProfile.LOCAL_ONLY
    brand_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    prompt_version_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class ModelRunRecord:
    """One ``model_runs`` row (DATA_MODEL §6.3)."""

    context: RunContext
    model: ResolvedModel
    attempt_no: int
    input_hash: str
    status: ModelRunStatus
    structured_output_mode: StructuredMode | None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None
    latency_ms: int | None = None
    error_code: ErrorCode | None = None
    raw_response: str | None = None
    repair_of_model_run_id: uuid.UUID | None = None


class RunRecorder(Protocol):
    async def record(self, record: ModelRunRecord) -> uuid.UUID: ...


class BudgetGuard(Protocol):
    """Raises BUDGET_EXCEEDED before a remote call would exceed the campaign budget (§51)."""

    async def check(self, model: ResolvedModel, context: RunContext) -> None: ...


@dataclass
class InMemoryRecorder:
    records: list[ModelRunRecord] = field(default_factory=list)

    async def record(self, record: ModelRunRecord) -> uuid.UUID:
        self.records.append(record)
        return uuid.uuid4()


@dataclass(frozen=True, slots=True)
class StructuredResult[T: BaseModel]:
    value: T
    model: ResolvedModel
    mode: StructuredMode
    model_run_id: uuid.UUID
    attempts: int
    input_tokens: int | None
    output_tokens: int | None


# ------------------------------------------------------------------ capability cache
class CapabilityCache:
    """Best structured-output mode per (provider, model); downgraded on rejection."""

    def __init__(self) -> None:
        self._modes: dict[tuple[str, str], StructuredMode] = {}

    def get(self, model: ResolvedModel) -> StructuredMode:
        return self._modes.get((model.provider_name, model.model), StructuredMode.JSON_SCHEMA)

    def set(self, model: ResolvedModel, mode: StructuredMode) -> None:
        self._modes[(model.provider_name, model.model)] = mode

    def downgrade(self, model: ResolvedModel) -> StructuredMode | None:
        current = self.get(model)
        index = _MODE_ORDER.index(current)
        if index + 1 >= len(_MODE_ORDER):
            return None
        self.set(model, _MODE_ORDER[index + 1])
        return _MODE_ORDER[index + 1]


DEFAULT_CAPABILITIES = CapabilityCache()


# ------------------------------------------------------------------ parsing
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def extract_json_text(text: str) -> str:
    """Strip reasoning blocks and markdown fences; fall back to the outermost {...}."""
    cleaned = _THINK.sub("", text)
    if "</think>" in cleaned.lower():  # unterminated opening tag earlier in the stream
        cleaned = cleaned[cleaned.lower().rindex("</think>") + len("</think>") :]
    cleaned = cleaned.strip()
    fence = _FENCE.match(cleaned)
    if fence:
        cleaned = fence.group(1).strip()
    if cleaned.startswith("{") or cleaned.startswith("["):
        return cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    return cleaned[start : end + 1] if 0 <= start < end else cleaned


def parse_structured[T: BaseModel](text: str, schema: type[T]) -> tuple[T | None, list[str]]:
    candidate = extract_json_text(text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, [f"not valid JSON: {exc.msg} at position {exc.pos}"]
    try:
        return schema.model_validate(data), []
    except ValidationError as exc:
        return None, [
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        ]


# ------------------------------------------------------------------ request building
def _schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Respond with ONLY a JSON object (no prose, no markdown fences) that validates "
        "against this JSON Schema:\n" + json.dumps(schema.model_json_schema(), sort_keys=True)
    )


def _build_request(
    model: ResolvedModel,
    messages: list[dict[str, str]],
    schema: type[BaseModel],
    mode: StructuredMode,
    prompt: PromptInput,
    attempt_no: int,
    temperature: float,
    max_tokens: int | None,
) -> ChatRequest:
    response_format: dict[str, Any] | None
    msgs = [dict(m) for m in messages]
    if mode is StructuredMode.JSON_SCHEMA:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": schema.model_json_schema(),
                "strict": False,
            },
        }
    else:
        response_format = {"type": "json_object"} if mode is StructuredMode.JSON_OBJECT else None
        if msgs and msgs[0].get("role") == "system":
            msgs[0]["content"] = msgs[0]["content"] + "\n\n" + _schema_instruction(schema)
        else:
            msgs.insert(0, {"role": "system", "content": _schema_instruction(schema)})
    return ChatRequest(
        model=model.model,
        messages=msgs,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        meta={
            "prompt_id": prompt.prompt_id,
            "prompt_version": prompt.version,
            "input_hash": prompt.input_hash,
            "attempt_no": attempt_no,
        },
    )


def _repair_messages(
    messages: list[dict[str, str]], bad_output: str, errors: list[str]
) -> list[dict[str, str]]:
    problems = "\n".join(f"- {e}" for e in errors[:10])
    return [
        *messages,
        {"role": "assistant", "content": bad_output[:4000]},
        {
            "role": "user",
            "content": (
                "Your previous response was invalid:\n"
                f"{problems}\n"
                "Reply again with ONLY the corrected JSON object."
            ),
        },
    ]


# ------------------------------------------------------------------ main entrypoint
@dataclass
class _Attempt:
    router: ModelRouter
    model: ResolvedModel
    context: RunContext
    recorder: RunRecorder
    prompt: PromptInput
    capabilities: CapabilityCache
    budget: BudgetGuard | None = None

    async def call(
        self,
        messages: list[dict[str, str]],
        schema: type[BaseModel],
        attempt_no: int,
        temperature: float,
        max_tokens: int | None,
        repair_of: uuid.UUID | None,
    ) -> tuple[ChatResult, StructuredMode]:
        provider = self.router.provider(self.model)
        while True:
            if self.model.locality is ModelLocality.REMOTE and self.budget is not None:
                await self.budget.check(self.model, self.context)  # before every remote call
            mode = self.capabilities.get(self.model)
            request = _build_request(
                self.model, messages, schema, mode, self.prompt, attempt_no, temperature, max_tokens
            )
            try:
                async with self.router.semaphore(self.model):
                    return await provider.chat(request), mode
            except UnsupportedResponseFormatError:
                # The provider was called: record it (every call gets a model_runs row).
                await self.recorder.record(
                    ModelRunRecord(
                        context=self.context,
                        model=self.model,
                        attempt_no=attempt_no,
                        input_hash=self.prompt.input_hash,
                        status=ModelRunStatus.FAILED,
                        structured_output_mode=mode,
                        error_code=ErrorCode.VALIDATION_FAILURE,
                        repair_of_model_run_id=repair_of,
                    )
                )
                downgraded = self.capabilities.downgrade(self.model)
                log.info(
                    "structured mode downgraded",
                    extra={"from_mode": mode, "to_mode": downgraded},
                )
                if downgraded is None:
                    raise
            except OmniGrowthError as exc:
                await self.recorder.record(
                    ModelRunRecord(
                        context=self.context,
                        model=self.model,
                        attempt_no=attempt_no,
                        input_hash=self.prompt.input_hash,
                        status=ModelRunStatus.FAILED,
                        structured_output_mode=mode,
                        error_code=exc.code,
                        raw_response=(
                            str(exc.details.get("raw"))
                            if exc.code is ErrorCode.LLM_OUTPUT_INVALID
                            else None
                        ),
                        repair_of_model_run_id=repair_of,
                    )
                )
                raise


async def generate_structured[T: BaseModel](
    schema: type[T],
    prompt: PromptInput,
    *,
    router: ModelRouter,
    context: RunContext,
    recorder: RunRecorder,
    budget: BudgetGuard | None = None,
    capabilities: CapabilityCache = DEFAULT_CAPABILITIES,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_repairs: int = MAX_REPAIRS,
) -> StructuredResult[T]:
    """``temperature`` / ``max_tokens`` default to the prompt front-matter values."""
    if not 0 <= max_repairs <= MAX_REPAIRS:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "max_repairs must be 0..2")
    if prompt.role is not context.role:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"prompt {prompt.prompt_ref} declares role {prompt.role}, caller asked {context.role}",
        )
    # The stricter privacy class (prompt vs caller) wins (ADR-0002 #4, spec 52).
    if _PRIVACY_RANK[prompt.privacy_class] > _PRIVACY_RANK[context.privacy]:
        context = dataclasses.replace(context, privacy=prompt.privacy_class)
    temperature = prompt.temperature if temperature is None else temperature
    max_tokens = prompt.max_tokens if max_tokens is None else max_tokens
    candidates = router.candidates(context.role, context.privacy, context.profile)
    last_error: OmniGrowthError | None = None
    for model in candidates:
        with bind_context(model=model.model, agent=context.task):
            try:
                return await _run_on_model(
                    schema,
                    _Attempt(router, model, context, recorder, prompt, capabilities, budget),
                    temperature,
                    max_tokens,
                    max_repairs,
                )
            except OmniGrowthError as exc:
                if exc.code not in FALLBACK_CODES:
                    raise
                log.warning(
                    "model failed, trying next candidate",
                    extra={"code": str(exc.code), "provider": model.provider_name},
                )
                last_error = exc
    if last_error is None:  # candidates() never returns an empty list
        raise OmniGrowthError(ErrorCode.MODEL_UNAVAILABLE, "no model candidates")
    last_error.details.setdefault("attempted", [f"{m.provider_name}:{m.model}" for m in candidates])
    raise last_error


async def _run_on_model[T: BaseModel](
    schema: type[T],
    attempt: _Attempt,
    temperature: float,
    max_tokens: int | None,
    max_repairs: int,
) -> StructuredResult[T]:
    messages = [dict(m) for m in attempt.prompt.messages]
    previous_run: uuid.UUID | None = None
    for attempt_no in range(max_repairs + 1):
        result, mode = await attempt.call(
            messages, schema, attempt_no, temperature, max_tokens, previous_run
        )
        value, errors = parse_structured(result.text, schema)
        record = ModelRunRecord(
            context=attempt.context,
            model=attempt.model,
            attempt_no=attempt_no,
            input_hash=attempt.prompt.input_hash,
            status=ModelRunStatus.SUCCEEDED if value is not None else ModelRunStatus.FAILED,
            structured_output_mode=mode,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            estimated_cost_usd=attempt.model.estimate_cost(
                result.input_tokens, result.output_tokens
            ),
            latency_ms=result.latency_ms,
            error_code=None if value is not None else ErrorCode.LLM_OUTPUT_INVALID,
            # Model output can echo secrets from the input (spec 53): mask before persisting.
            raw_response=None if value is not None else mask_text(result.text),
            repair_of_model_run_id=previous_run,
        )
        run_id = await attempt.recorder.record(record)
        log.info(
            "model run",
            extra={
                "provider": attempt.model.provider_name,
                "attempt_no": attempt_no,
                "status": str(record.status),
                "mode": str(mode),
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
        if value is not None:
            return StructuredResult(
                value=value,
                model=attempt.model,
                mode=mode,
                model_run_id=run_id,
                attempts=attempt_no + 1,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        previous_run = run_id
        if attempt_no == max_repairs:
            raise OmniGrowthError(
                ErrorCode.LLM_OUTPUT_INVALID,
                f"model output failed {schema.__name__} validation after "
                f"{max_repairs} repair attempt(s)",
                details={
                    "model_run_id": str(run_id),
                    "errors": errors[:10],
                    "raw_preview": mask_text(result.text[:500]),
                },
            )
        messages = _repair_messages(messages, result.text, errors)
    raise AssertionError("unreachable")  # loop always returns or raises


# ------------------------------------------------------------------ capability probe (A1)
async def probe_structured_mode[T: BaseModel](
    schema: type[T],
    prompt: PromptInput,
    *,
    router: ModelRouter,
    model: ResolvedModel,
    capabilities: CapabilityCache,
) -> StructuredMode | None:
    """Find the best structured-output mode ``model`` supports and cache it (A1).

    Tries json_schema -> json_object -> prompt-only; a mode "works" when the endpoint accepts
    it AND the reply validates against ``schema``. Returns None when no mode works.
    Transport errors propagate (the model is unreachable, not merely incapable).
    """
    provider = router.provider(model)
    for mode in _MODE_ORDER:
        request = _build_request(
            model,
            [dict(m) for m in prompt.messages],
            schema,
            mode,
            prompt,
            0,
            prompt.temperature,
            prompt.max_tokens,
        )
        try:
            async with router.semaphore(model):
                result = await provider.chat(request)
        except UnsupportedResponseFormatError:
            continue
        value, _errors = parse_structured(result.text, schema)
        if value is not None:
            capabilities.set(model, mode)
            return mode
    return None
