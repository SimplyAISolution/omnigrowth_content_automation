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
from omnigrowth.providers.router import ModelRouter
from omnigrowth.providers.structured import FALLBACK_CODES

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Embeddings:
    provider: str
    model: str
    vectors: list[list[float]]

    @property
    def dim(self) -> int:
        return len(self.vectors[0]) if self.vectors else 0


async def embed_texts(
    router: ModelRouter,
    texts: list[str],
    *,
    privacy: PrivacyClass = PrivacyClass.PRIVATE,
    profile: RoutingProfile = RoutingProfile.LOCAL_ONLY,
) -> Embeddings:
    if not texts:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "nothing to embed")
    last_error: OmniGrowthError | None = None
    for model in router.candidates(ModelRole.EMBEDDING_LOCAL, privacy, profile):
        provider = router.provider(model)
        if not isinstance(provider, EmbeddingProvider):
            continue
        try:
            async with router.semaphore(model):
                vectors = await provider.embed(model.model, texts)
        except OmniGrowthError as exc:
            if exc.code not in FALLBACK_CODES:
                raise
            log.warning(
                "embedding model failed, trying next candidate",
                extra={"provider": model.provider_name, "code": str(exc.code)},
            )
            last_error = exc
            continue
        return Embeddings(provider=model.provider_name, model=model.model, vectors=vectors)
    raise last_error or OmniGrowthError(
        ErrorCode.MODEL_UNAVAILABLE, "no embedding-capable provider configured"
    )
