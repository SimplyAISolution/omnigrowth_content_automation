"""Model router (spec §23, §51, §52; ADR-0002; A2).

Turns (role, privacy class, routing profile) into an ordered list of concrete models:
- LOCAL_ONLY    -> local candidates only (default; the only profile used by tests)
- BALANCED      -> local candidates first, then remote
- QUALITY_FIRST -> remote candidates first, then local
PRIVATE tasks never receive a REMOTE model regardless of profile. Each provider has a
concurrency semaphore (A2: local default 1 — a second request waits, it does not fail),
and in workers a cross-process Redis slot per concurrency group (providers/slots.py).
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from omnigrowth.core.enums import ModelLocality, ModelRole, PrivacyClass, RoutingProfile
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.base import ChatProvider
from omnigrowth.providers.config import ModelCandidate, ModelsConfig
from omnigrowth.providers.idle import IdleProbe
from omnigrowth.providers.slots import SlotLimiter
from omnigrowth.providers.tokens import ApproximateTokenCounter, TokenCount, TokenCounter

# The total deadline of one call: the provider timeout plus this margin. httpx timeouts are
# per phase (connect/read/write), so without a total deadline a trickling response could hold
# the GPU indefinitely.
CALL_DEADLINE_MARGIN_S = 5.0


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    role: ModelRole
    provider_name: str
    model: str
    locality: ModelLocality
    input_cost_per_mtok: Decimal
    output_cost_per_mtok: Decimal
    context_tokens: int | None = None

    def estimate_cost(self, input_tokens: int | None, output_tokens: int | None) -> Decimal | None:
        if self.locality is ModelLocality.LOCAL:
            return Decimal(0)
        if input_tokens is None or output_tokens is None:
            return None
        if not self.input_cost_per_mtok and not self.output_cost_per_mtok:
            return None  # remote model without configured prices: cost unknown, never $0
        per_million = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * self.input_cost_per_mtok
            + Decimal(output_tokens) * self.output_cost_per_mtok
        ) / per_million


class ModelRouter:
    def __init__(
        self,
        config: ModelsConfig,
        providers: dict[str, ChatProvider],
        *,
        slots: SlotLimiter | None = None,
        counters: dict[str, TokenCounter] | None = None,
        idle_probes: dict[str, IdleProbe] | None = None,
    ) -> None:
        self.config = config
        self.providers = providers
        # Token counters per provider. Missing ones fall back to the APPROXIMATE estimate,
        # but only for providers whose config says ``budget_counting: approximate``.
        self.counters = dict(counters or {})
        self.idle_probes = dict(idle_probes or {})
        # Cross-process slots (Redis) in workers; None in unit tests (in-process only).
        self.slots = slots
        # Semaphores are created lazily per running event loop: RQ jobs each run their own
        # loop (asyncio.run), and a Semaphore is bound to the loop that first contends it.
        self._semaphores: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, dict[str, asyncio.Semaphore]
        ] = weakref.WeakKeyDictionary()

    def _resolve(self, role: ModelRole, candidate: ModelCandidate) -> ResolvedModel:
        return ResolvedModel(
            role=role,
            provider_name=candidate.provider,
            model=candidate.model,
            locality=self.config.locality_of(candidate),
            input_cost_per_mtok=candidate.input_cost_per_mtok,
            output_cost_per_mtok=candidate.output_cost_per_mtok,
            context_tokens=candidate.context_tokens,
        )

    def candidates(
        self, role: ModelRole, privacy: PrivacyClass, profile: RoutingProfile
    ) -> list[ResolvedModel]:
        configured = [self._resolve(role, c) for c in self.config.roles.get(role, [])]
        available = [m for m in configured if m.provider_name in self.providers]
        local = [m for m in available if m.locality is ModelLocality.LOCAL]
        remote = [m for m in available if m.locality is ModelLocality.REMOTE]

        if privacy is PrivacyClass.PRIVATE:
            remote = []
        if profile is RoutingProfile.LOCAL_ONLY:
            ordered = local
        elif profile is RoutingProfile.BALANCED:
            ordered = local + remote
        else:
            ordered = remote + local

        if not ordered:
            only_remote_left = privacy is PrivacyClass.PRIVATE and any(
                m.locality is ModelLocality.REMOTE for m in available
            )
            if only_remote_left:
                raise OmniGrowthError(
                    ErrorCode.PRIVACY_VIOLATION,
                    f"no LOCAL model configured for PRIVATE {role} task",
                    details={"role": role, "profile": profile},
                )
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"no model available for role {role} under {profile}",
                details={"role": role, "profile": profile, "privacy": privacy},
            )
        return ordered

    def provider(self, model: ResolvedModel) -> ChatProvider:
        return self.providers[model.provider_name]

    def _concurrency_key(self, provider_name: str) -> tuple[str, int]:
        provider = self.config.providers[provider_name]
        if provider.concurrency_group is None:
            return f"provider:{provider_name}", provider.max_concurrency
        members = [
            p
            for p in self.config.providers.values()
            if p.concurrency_group == provider.concurrency_group
        ]
        return f"group:{provider.concurrency_group}", min(p.max_concurrency for p in members)

    def semaphore(self, model: ResolvedModel) -> asyncio.Semaphore:
        per_loop = self._semaphores.setdefault(asyncio.get_running_loop(), {})
        key, limit = self._concurrency_key(model.provider_name)
        if key not in per_loop:
            per_loop[key] = asyncio.Semaphore(limit)
        return per_loop[key]

    @asynccontextmanager
    async def slot(self, model: ResolvedModel) -> AsyncIterator[None]:
        """In-process semaphore, then (when configured) the cross-process lease, then a total
        deadline on the call itself."""
        provider = self.config.providers[model.provider_name]
        async with self.semaphore(model):
            if self.slots is None:
                await self._verify_idle(model)  # a configured fail-closed check always applies
                async with _deadline(provider.timeout_s, model):
                    yield
                return
            key, limit = self._concurrency_key(model.provider_name)
            async with self.slots.slot(
                key, limit, lease_s=provider.slot_lease_s, max_wait_s=provider.slot_wait_s
            ):
                # Holding the lease is not enough: the server must also report itself idle
                # (a cancelled predecessor finishes prompt processing first; other clients
                # of the server are outside the lease). Unverifiable -> blocked.
                await self._verify_idle(model)
                async with _deadline(provider.timeout_s, model):
                    yield

    async def _verify_idle(self, model: ResolvedModel) -> None:
        provider = self.config.providers[model.provider_name]
        if provider.idle_check == "none":
            return
        probe = self.idle_probes.get(model.provider_name)
        if probe is None:
            raise OmniGrowthError(
                ErrorCode.CONFIGURATION_ERROR,
                "provider requires a server idle check but none was built",
                details={"provider": model.provider_name},
            )
        await probe.wait_idle(timeout_s=provider.slot_wait_s)

    def _counter(self, model: ResolvedModel) -> TokenCounter:
        provider = self.config.providers[model.provider_name]
        counter = self.counters.get(model.provider_name)
        if counter is not None:
            return counter
        if provider.budget_counting == "lmstudio":
            raise OmniGrowthError(
                ErrorCode.CONFIGURATION_ERROR,
                "provider requires the LM Studio token counter but none was built",
                details={"provider": model.provider_name},
            )
        counter = ApproximateTokenCounter(provider.chars_per_token)
        self.counters[model.provider_name] = counter
        return counter

    def _check(
        self, model: ResolvedModel, count: TokenCount, max_output_tokens: int | None
    ) -> TokenCount:
        provider = self.config.providers[model.provider_name]
        configured = model.context_tokens or provider.context_tokens
        # The server's LOADED context wins when it is smaller than the configured one.
        context = min(configured, count.loaded_context or configured)
        if max_output_tokens is None and model.role is not ModelRole.EMBEDDING_LOCAL:
            max_output_tokens = provider.default_output_tokens
        needed = count.total + (max_output_tokens or 0)
        if needed > context:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                "model input does not fit the context budget",
                details={
                    "limit": "context_tokens",
                    "max": context,
                    "configured_context": configured,
                    "loaded_context": count.loaded_context,
                    "prompt_tokens": count.prompt_tokens,
                    "schema_tokens": count.schema_tokens,
                    "max_output_tokens": max_output_tokens or 0,
                    "counted_by": count.counted_by,
                    "exact": count.exact,
                },
            )
        return count

    async def check_chat_budget(
        self,
        model: ResolvedModel,
        messages: Sequence[dict[str, Any]],
        response_format: Any | None,
        max_output_tokens: int | None,
    ) -> TokenCount:
        """Refuse a request unless template-rendered prompt tokens + schema tokens + output
        allowance fit the context (exact with the server tokenizer where configured)."""
        count = await self._counter(model).count_chat(model.model, messages, response_format)
        return self._check(model, count, max_output_tokens)

    async def check_text_budget(self, model: ResolvedModel, text: str) -> TokenCount:
        count = await self._counter(model).count_text(
            model.model, text, embedding=model.role is ModelRole.EMBEDDING_LOCAL
        )
        return self._check(model, count, None)

    def approximate_char_budget(self, model: ResolvedModel) -> int:
        """APPROXIMATE character budget for clipping (verified afterwards by an exact count)."""
        provider = self.config.providers[model.provider_name]
        return int((model.context_tokens or provider.context_tokens) * provider.chars_per_token)

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
        if self.slots is not None:
            await self.slots.aclose()
        for counter in self.counters.values():
            await counter.aclose()


@asynccontextmanager
async def _deadline(timeout_s: float, model: ResolvedModel) -> AsyncIterator[None]:
    try:
        async with asyncio.timeout(timeout_s + CALL_DEADLINE_MARGIN_S):
            yield
    except TimeoutError as exc:
        raise OmniGrowthError(
            ErrorCode.MODEL_TIMEOUT,
            f"{model.provider_name}: call exceeded its total deadline",
            details={"provider": model.provider_name, "deadline_s": timeout_s},
            retryable=True,
        ) from exc
