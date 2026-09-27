"""Routed embeddings (EMBEDDING_LOCAL role; §10 duplicate/fatigue detection, A4).

Same routing rules as chat: PRIVATE text never leaves the machine, candidates are tried in
order and transport failures fall through to the next one. Model names come only from
``config/models.yaml``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from omnigrowth.core.enums import ModelRole, PrivacyClass, RoutingProfile
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.base import EmbeddingProvider
from omnigrowth.providers.router import ModelRouter, ResolvedModel
from omnigrowth.providers.structured import FALLBACK_CODES

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Embeddings:
    provider: str
    model: str
    vectors: list[list[float]]
    # Indexes of inputs that were clipped to the model's context budget (clip=True only).
    clipped: tuple[int, ...] = ()

    @property
    def dim(self) -> int:
        return len(self.vectors[0]) if self.vectors else 0


async def embed_texts(
    router: ModelRouter,
    texts: list[str],
    *,
    privacy: PrivacyClass = PrivacyClass.PRIVATE,
    profile: RoutingProfile = RoutingProfile.LOCAL_ONLY,
    clip: bool = False,
) -> Embeddings:
    """Embed each text. The context budget applies per text. With ``clip=True`` a text
    longer than the budget is cut to its first ``context_tokens * chars_per_token``
    characters here, explicitly and reported in ``clipped``, instead of being refused (or
    silently truncated by the server)."""
    if not texts:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "nothing to embed")
    last_error: OmniGrowthError | None = None
    for model in router.candidates(ModelRole.EMBEDDING_LOCAL, privacy, profile):
        provider = router.provider(model)
        if not isinstance(provider, EmbeddingProvider):
            continue
        inputs: list[str] = list(texts)
        clipped: list[int] = []
        try:
            for i, text in enumerate(inputs):
                inputs[i], was_clipped = await _fit(router, model, text, clip=clip)
                if was_clipped:
                    clipped.append(i)
        except OmniGrowthError as exc:  # too large for this model: a later one may accept it
            if exc.code is not ErrorCode.VALIDATION_FAILURE:
                raise
            last_error = exc
            continue
        try:
            async with router.slot(model):
                vectors = await provider.embed(model.model, inputs)
        except OmniGrowthError as exc:
            if exc.code not in FALLBACK_CODES:
                raise
            log.warning(
                "embedding model failed, trying next candidate",
                extra={"provider": model.provider_name, "code": str(exc.code)},
            )
            last_error = exc
            continue
        return Embeddings(
            provider=model.provider_name,
            model=model.model,
            vectors=vectors,
            clipped=tuple(clipped),
        )
    raise last_error or OmniGrowthError(
        ErrorCode.MODEL_UNAVAILABLE, "no embedding-capable provider configured"
    )


MAX_CLIP_ROUNDS = 6


async def _fit(
    router: ModelRouter, model: ResolvedModel, text: str, *, clip: bool
) -> tuple[str, bool]:
    """Return ``text`` if its exact token count fits the context, else (with ``clip``) the
    longest prefix found that fits: start from the approximate character budget, then
    shrink by the measured tokens-per-character ratio until an EXACT count fits."""
    try:
        await router.check_text_budget(model, text)
        return text, False
    except OmniGrowthError as exc:
        if not clip or exc.code is not ErrorCode.VALIDATION_FAILURE:
            raise
        budget = int(exc.details["max"])
        tokens = int(exc.details["prompt_tokens"])
    candidate = text[: min(len(text), router.approximate_char_budget(model))]
    for _ in range(MAX_CLIP_ROUNDS):
        try:
            await router.check_text_budget(model, candidate)
            return candidate, True
        except OmniGrowthError as exc:
            if exc.code is not ErrorCode.VALIDATION_FAILURE:
                raise
            tokens = int(exc.details["prompt_tokens"])
            candidate = candidate[: max(1, int(len(candidate) * budget / tokens * 0.95))]
    raise OmniGrowthError(
        ErrorCode.VALIDATION_FAILURE,
        "could not clip the text to the embedding context",
        details={"limit": "context_tokens", "max": budget},
    )
