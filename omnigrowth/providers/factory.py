"""Build providers + router from settings and ``config/models.yaml``."""

from __future__ import annotations

import logging

import httpx

from omnigrowth.core.enums import ModelLocality
from omnigrowth.core.settings import Settings
from omnigrowth.providers.base import ChatProvider
from omnigrowth.providers.config import ModelsConfig, ProviderConfig, load_models_config
from omnigrowth.providers.openai_compatible import OpenAICompatibleProvider
from omnigrowth.providers.router import ModelRouter
from omnigrowth.providers.slots import RedisSlotLimiter

log = logging.getLogger(__name__)


def resolve_base_url(
    name: str, provider: ProviderConfig, config: ModelsConfig, settings: Settings
) -> str | None:
    """A1: env OMNIGROWTH_LOCAL_LLM_URL (for the configured local preset) > preset > None.

    Never guesses: a provider without a resolvable URL is simply not built.
    """
    if provider.base_url:
        return provider.base_url.rstrip("/")
    if provider.preset is None:
        return None
    if settings.local_llm_url and provider.preset == settings.local_llm_preset:
        return settings.local_llm_url.rstrip("/")
    preset = config.presets[provider.preset]
    url = preset.base_url_docker if settings.runtime == "docker" else preset.base_url_host
    return url.rstrip("/")


def build_providers(
    config: ModelsConfig,
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, ChatProvider]:
    providers: dict[str, ChatProvider] = {}
    for name, provider in config.providers.items():
        api_key: str | None = None
        if provider.uses_cloud_key:
            if settings.cloud_api_key is None:
                log.info("provider disabled: no OMNIGROWTH_CLOUD_API_KEY", extra={"provider": name})
                continue
            api_key = settings.cloud_api_key.get_secret_value()
        if provider.locality is ModelLocality.REMOTE and not provider.uses_cloud_key:
            log.warning("remote provider without cloud key ignored", extra={"provider": name})
            continue
        url = resolve_base_url(name, provider, config, settings)
        if url is None:
            log.info("provider disabled: no base URL", extra={"provider": name})
            continue
        providers[name] = OpenAICompatibleProvider(
            name,
            url,
            api_key=api_key,
            timeout_s=provider.timeout_s,
            transport=transport,
            # Only a cloud provider may honour system proxy settings.
            trust_env=provider.locality is ModelLocality.REMOTE,
        )
    return providers


def build_router(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> ModelRouter:
    config = load_models_config(settings.config_dir)
    # Every production router shares the GPU slots through Redis (A2 across processes).
    slots = RedisSlotLimiter.from_url(settings.redis_url.get_secret_value())
    return ModelRouter(config, build_providers(config, settings, transport=transport), slots=slots)
