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
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal

from omnigrowth.core.enums import ModelLocality, ModelRole, PrivacyClass, RoutingProfile
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.base import ChatProvider
from omnigrowth.providers.config import ModelCandidate, ModelsConfig
from omnigrowth.providers.slots import SlotLimiter

# A slot outlives the provider timeout by this margin, then expires if its holder crashed.
SLOT_TTL_MARGIN_S = 30.0


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    role: ModelRole
    provider_name: str
    model: str
    locality: ModelLocality
    input_cost_per_mtok: Decimal
    output_cost_per_mtok: Decimal

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
    ) -> None:
        self.config = config
        self.providers = providers
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
        """In-process semaphore, then (when configured) the cross-process slot."""
        async with self.semaphore(model):
            if self.slots is None:
                yield
                return
            key, limit = self._concurrency_key(model.provider_name)
            provider = self.config.providers[model.provider_name]
            async with self.slots.slot(
                key,
                limit,
                ttl_s=provider.timeout_s + SLOT_TTL_MARGIN_S,
                max_wait_s=provider.slot_wait_s,
            ):
                yield

    def check_input_size(self, model: ResolvedModel, texts: Iterable[str]) -> None:
        limit = self.config.providers[model.provider_name].max_prompt_chars
        size = sum(len(t) for t in texts)
        if size > limit:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                "model input too large",
                details={"limit": "max_prompt_chars", "max": limit, "actual": size},
            )

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
        if self.slots is not None:
            await self.slots.aclose()
