"""Application settings (pydantic-settings). Env + `.env`; no secrets in code or YAML.

Rules enforced here:
- DATABASE_URL / REDIS_URL are required and explicit (ADR-0008: never fall back to a
  library default that could hit another project's database on :5432).
- PUBLISH_MODE=LIVE is refused when ENV=test (A6 hard block, part a).
- Cloud models use only OMNIGROWTH_CLOUD_API_KEY, never generic SDK env vars (ADR-0002).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from omnigrowth.core.enums import Environment, PublishMode, RoutingProfile
from omnigrowth.core.errors import ConfigurationError


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Environment = Field(default=Environment.DEV, validation_alias="ENV")
    publish_mode: PublishMode = Field(default=PublishMode.MOCK, validation_alias="PUBLISH_MODE")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_json: bool = Field(default=True, validation_alias="LOG_JSON")

    database_url: SecretStr = Field(validation_alias="DATABASE_URL")
    redis_url: SecretStr = Field(validation_alias="REDIS_URL")
    db_pool_size: int = Field(default=10, ge=1, le=100, validation_alias="DB_POOL_SIZE")

    runtime: Literal["host", "docker"] = Field(
        default="host", validation_alias="OMNIGROWTH_RUNTIME"
    )
    local_llm_url: str | None = Field(default=None, validation_alias="OMNIGROWTH_LOCAL_LLM_URL")
    local_llm_preset: str = Field(
        default="lmstudio", validation_alias="OMNIGROWTH_LOCAL_LLM_PRESET"
    )
    cloud_api_key: SecretStr | None = Field(
        default=None, validation_alias="OMNIGROWTH_CLOUD_API_KEY"
    )
    credential_kek: SecretStr | None = Field(
        default=None, validation_alias="OMNIGROWTH_CREDENTIAL_KEK"
    )
    routing_profile: RoutingProfile = Field(
        default=RoutingProfile.LOCAL_ONLY, validation_alias="OMNIGROWTH_ROUTING_PROFILE"
    )
    config_dir: Path = Field(
        default=Path("config"),
        validation_alias=AliasChoices("OMNIGROWTH_CONFIG_DIR", "CONFIG_DIR"),
    )
    upload_dir: Path = Field(default=Path("data/uploads"), validation_alias="OMNIGROWTH_UPLOAD_DIR")
    # Rate limits (API_PLAN section 6.4). Format: "N/unit;N/unit" (unit: second|minute|hour|day).
    rate_limit_backend: Literal["redis", "memory"] = Field(
        default="redis", validation_alias="OMNIGROWTH_RATE_LIMIT_BACKEND"
    )
    rate_limit_login: str = Field(
        default="5/minute;20/hour", validation_alias="OMNIGROWTH_RATE_LIMIT_LOGIN"
    )
    rate_limit_ingest: str = Field(
        default="30/minute;300/hour", validation_alias="OMNIGROWTH_RATE_LIMIT_INGEST"
    )
    # Bounded inference backlog: a human request that would put more than this many open
    # (QUEUED or RUNNING) jobs on one model-bound queue is refused with 429 RATE_LIMITED.
    max_open_model_jobs: int = Field(
        default=20, ge=1, validation_alias="OMNIGROWTH_MAX_OPEN_MODEL_JOBS"
    )
    llm_health_timeout_s: float = Field(default=2.0, gt=0, validation_alias="LLM_HEALTH_TIMEOUT_S")
    # API_PLAN §1.6: job-creating and publishing POSTs require an Idempotency-Key.
    require_idempotency_key: bool = Field(
        default=True, validation_alias="OMNIGROWTH_REQUIRE_IDEMPOTENCY_KEY"
    )
    # ---- Phase 12 integrations (n8n). Webhooks are disabled until a key is configured.
    # Server key every webhook signing secret is derived from (>= 32 characters); never logged.
    webhook_key: SecretStr | None = Field(default=None, validation_alias="OMNIGROWTH_WEBHOOK_KEY")
    # Hosts (or host:port) that outbound webhooks may reach even though they are private or
    # local, for a self-hosted n8n. Everything else must be a public HTTPS address.
    webhook_target_allowlist: str = Field(
        default="", validation_alias="OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST"
    )
    webhook_max_attempts: int = Field(
        default=8, ge=1, le=20, validation_alias="OMNIGROWTH_WEBHOOK_MAX_ATTEMPTS"
    )
    webhook_timeout_s: float = Field(
        default=10.0, gt=0, le=60, validation_alias="OMNIGROWTH_WEBHOOK_TIMEOUT_S"
    )
    webhook_secret_grace_hours: int = Field(
        default=24, ge=0, le=168, validation_alias="OMNIGROWTH_WEBHOOK_SECRET_GRACE_HOURS"
    )
    # Self-hosted / sandbox platform hosts reachable over HTTP or private addresses
    # (host:port, comma-separated). Public platform APIs must be HTTPS on 443 (Phase 13).
    platform_host_allowlist: str = Field(
        default="", validation_alias="OMNIGROWTH_PLATFORM_HOST_ALLOWLIST"
    )
    # Base URL of the web app, for links in notifications (no secrets, no data in the URL).
    app_url: str = Field(default="http://127.0.0.1:5173", validation_alias="OMNIGROWTH_APP_URL")

    @field_validator("publish_mode", mode="before")
    @classmethod
    def _upper_publish_mode(cls, value: object) -> object:
        # GLOSSARY §4a: env parsing is case-insensitive (`live` -> PublishMode.LIVE).
        return value.upper() if isinstance(value, str) else value

    @field_validator("env", mode="before")
    @classmethod
    def _lower_env(cls, value: object) -> object:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _live_publishing_guard(self) -> Settings:
        if self.env is Environment.TEST and self.publish_mode is PublishMode.LIVE:
            raise ConfigurationError(
                "PUBLISH_MODE=LIVE is forbidden when ENV=test (A6)",
                guard="ENV_TEST",
            )
        return self

    @property
    def is_test(self) -> bool:
        return self.env is Environment.TEST


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Tests call ``get_settings.cache_clear()`` after patching env."""
    return Settings()  # values come from the environment (pydantic-settings)
