"""``config/publishing.yaml`` — scheduler lease/batch and retry/backoff settings (§14)."""

from __future__ import annotations

import random
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnigrowth.core.errors import ErrorCode, OmniGrowthError


class SchedulerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick_seconds: float = Field(gt=0)
    batch_size: int = Field(ge=1, le=500)
    lease_seconds: int = Field(ge=5)
    request_timeout_seconds: int = Field(ge=1)


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(ge=1, le=20)
    backoff_base_seconds: int = Field(ge=1)
    backoff_max_seconds: int = Field(ge=1)
    uncertain_hold_seconds: int = Field(ge=1)
    schedule_grace_seconds: int = Field(ge=0)


class PublishingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scheduler: SchedulerConfig
    publishing: RetryConfig

    @model_validator(mode="after")
    def _lease_covers_request(self) -> PublishingConfig:
        if self.scheduler.lease_seconds <= self.scheduler.request_timeout_seconds + 5:
            raise ValueError("lease_seconds must exceed request_timeout_seconds + 5 (§3.4)")
        return self


def load_publishing_config(config_dir: Path) -> PublishingConfig:
    path = config_dir / "publishing.yaml"
    try:
        return PublishingConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise OmniGrowthError(
            ErrorCode.CONFIGURATION_ERROR,
            "invalid config/publishing.yaml",
            details={"path": str(path), "error": type(exc).__name__},
        ) from exc


@lru_cache(maxsize=4)
def cached_publishing_config(config_dir: Path) -> PublishingConfig:
    return load_publishing_config(config_dir)


def backoff_seconds(
    attempt: int,
    config: RetryConfig,
    *,
    retry_after_s: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Exponential backoff with full jitter, honouring ``Retry-After`` (§3.5)."""
    ceiling = min(config.backoff_max_seconds, config.backoff_base_seconds * 2 ** (attempt - 1))
    delay = (rng or random.SystemRandom()).uniform(0, ceiling)
    if retry_after_s is not None:
        delay = max(delay, retry_after_s)
    return float(min(delay, config.backoff_max_seconds))
