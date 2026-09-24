"""Health checks for the API (PHASES.md Phase 2 D, API_PLAN §2.1).

Each check maps only its *expected* failure types to ``DOWN``; anything else propagates
(AGENTS.md §3: no blind excepts). Strings emitted in ``detail`` pass through ``mask_text``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import httpx
import redis.asyncio as redis_asyncio
import yaml
from redis.exceptions import RedisError

from omnigrowth.core.enums import HealthStatus
from omnigrowth.core.errors import ConfigurationError, ErrorCode, OmniGrowthError
from omnigrowth.core.masking import mask_text
from omnigrowth.core.settings import Settings
from omnigrowth.providers.openai_compatible import OpenAICompatibleProvider
from omnigrowth.schemas.health import CheckResult, HealthChecks, HealthReport
from omnigrowth.storage.db import ping_database

logger = logging.getLogger(__name__)

# Infrastructure probes must answer fast; a hung dependency must not hang /health.
DATABASE_TIMEOUT_S = 2.0
REDIS_TIMEOUT_S = 2.0

_DB_DOWN_CODES = frozenset({ErrorCode.DATABASE_FAILURE, ErrorCode.CONFIGURATION_ERROR})
NOT_CONFIGURED = "not configured"


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 2)


def _down(start: float, detail: str) -> CheckResult:
    return CheckResult(
        status=HealthStatus.DOWN, latency_ms=_elapsed_ms(start), detail=mask_text(detail)
    )


# ------------------------------------------------------------------ database
async def check_database() -> CheckResult:
    start = time.perf_counter()
    try:
        async with asyncio.timeout(DATABASE_TIMEOUT_S):
            await ping_database()
    except OmniGrowthError as exc:
        if exc.code not in _DB_DOWN_CODES:
            raise
        logger.warning("health: database down", extra={"error_code": str(exc.code)})
        return _down(start, str(exc.details.get("error", exc.code)))
    except OSError as exc:  # includes TimeoutError
        logger.warning("health: database down", extra={"error": type(exc).__name__})
        return _down(start, type(exc).__name__)
    return CheckResult(status=HealthStatus.OK, latency_ms=_elapsed_ms(start))


# ------------------------------------------------------------------ redis
async def check_redis(settings: Settings) -> CheckResult:
    start = time.perf_counter()
    try:
        client = redis_asyncio.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=REDIS_TIMEOUT_S,
            socket_timeout=REDIS_TIMEOUT_S,
        )
    except ValueError:  # malformed REDIS_URL: report DOWN, never a 500
        logger.warning("health: REDIS_URL invalid")
        return _down(start, "invalid REDIS_URL")
    try:
        async with asyncio.timeout(REDIS_TIMEOUT_S):
            await client.ping()
    except (RedisError, OSError) as exc:  # OSError includes TimeoutError
        logger.warning("health: redis down", extra={"error": type(exc).__name__})
        return _down(start, type(exc).__name__)
    finally:
        await client.aclose()
    return CheckResult(status=HealthStatus.OK, latency_ms=_elapsed_ms(start))


# ------------------------------------------------------------------ local LLM
def _load_models_config(config_dir: Path) -> dict[str, Any] | None:
    path = config_dir / "models.yaml"
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigurationError("models.yaml is not valid YAML", path=str(path)) from exc
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ConfigurationError("models.yaml must be a mapping", path=str(path))
    return data


def resolve_local_llm_url(settings: Settings) -> str | None:
    """Env ``OMNIGROWTH_LOCAL_LLM_URL`` > preset > ``None`` (A1: never guess).

    The preset key is ``base_url_docker`` when ``OMNIGROWTH_RUNTIME=docker`` (containers reach
    the host LLM via host.docker.internal), else ``base_url_host``.
    """
    if settings.local_llm_url:
        return settings.local_llm_url.rstrip("/")
    config = _load_models_config(settings.config_dir)
    if config is None:
        return None
    presets = config.get("presets")
    if not isinstance(presets, dict):
        return None
    preset = presets.get(settings.local_llm_preset)
    if not isinstance(preset, dict):
        return None
    base = preset.get("base_url_docker" if settings.runtime == "docker" else "base_url_host")
    if not isinstance(base, str) or not base.strip():
        return None
    return base.strip().rstrip("/")


async def check_local_llm(
    settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
) -> CheckResult:
    start = time.perf_counter()
    try:
        base = resolve_local_llm_url(settings)
    except ConfigurationError as exc:
        logger.warning("health: local LLM config invalid", extra={"error": exc.message})
        return _down(start, exc.message)
    if base is None:
        return CheckResult(status=HealthStatus.DOWN, latency_ms=None, detail=NOT_CONFIGURED)
    # LLM HTTP lives only in omnigrowth.providers (ARCHITECTURE §3 rule 4).
    provider = OpenAICompatibleProvider(
        "local_llm", base, timeout_s=settings.llm_health_timeout_s, transport=transport
    )
    try:
        models = await provider.list_models()
    except OmniGrowthError as exc:
        if exc.code not in (ErrorCode.MODEL_UNAVAILABLE, ErrorCode.MODEL_TIMEOUT):
            raise
        logger.warning("health: local LLM unreachable", extra={"error_code": str(exc.code)})
        detail = str(exc.details.get("error") or exc.message.split(": ", 1)[-1])
        return _down(start, detail)
    finally:
        await provider.aclose()
    return CheckResult(
        status=HealthStatus.OK, latency_ms=_elapsed_ms(start), detail=f"{len(models)} models"
    )


# ------------------------------------------------------------------ aggregate
def overall_status(checks: HealthChecks) -> HealthStatus:
    if HealthStatus.DOWN in (checks.database.status, checks.redis.status):
        return HealthStatus.DOWN
    if checks.local_llm.status is not HealthStatus.OK:
        return HealthStatus.DEGRADED
    return HealthStatus.OK


async def get_health(settings: Settings) -> HealthReport:
    database, redis_result, local_llm = await asyncio.gather(
        check_database(), check_redis(settings), check_local_llm(settings)
    )
    checks = HealthChecks(database=database, redis=redis_result, local_llm=local_llm)
    report = HealthReport(status=overall_status(checks), checks=checks)
    if report.status is not HealthStatus.OK:
        logger.warning("health: %s", report.status, extra={"checks": checks.model_dump()})
    return report
