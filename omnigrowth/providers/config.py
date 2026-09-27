"""Typed view of ``config/models.yaml`` (ADR-0002, A1, A2). No model names live in Python."""

from __future__ import annotations

import fnmatch
from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from omnigrowth.core.enums import ModelLocality, ModelRole
from omnigrowth.core.errors import ConfigurationError

CLOUD_KEY_ENV = "OMNIGROWTH_CLOUD_API_KEY"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PresetConfig(_Strict):
    base_url_host: str
    base_url_docker: str


class ProviderConfig(_Strict):
    """One OpenAI-compatible endpoint. Local providers point at a preset; the cloud
    provider has an explicit base_url and is enabled only when OMNIGROWTH_CLOUD_API_KEY
    is set (generic SDK env vars are never read)."""

    kind: Literal["openai_compatible"] = "openai_compatible"
    locality: ModelLocality
    preset: str | None = None
    base_url: str | None = None
    uses_cloud_key: bool = False
    max_concurrency: int = Field(default=1, ge=1, le=32)
    # Providers that share hardware (e.g. LM Studio + Ollama on one GPU) share one semaphore;
    # the group limit is the smallest max_concurrency among its members (A2).
    concurrency_group: str | None = None
    timeout_s: float = Field(default=120.0, gt=0)
    # Bounded wait for a cross-process inference slot (providers/slots.py) before the call
    # fails with MODEL_UNAVAILABLE instead of queueing forever.
    slot_wait_s: float = Field(default=300.0, gt=0)
    # Lease of that slot; renewed every lease/3 while the call runs (providers/slots.py).
    slot_lease_s: float = Field(default=30.0, ge=0.05)
    # Context budget (docs/audit/safeguards_post_mvp.md §4): a request is refused unless
    # ceil(input_chars / chars_per_token) + max_tokens <= context_tokens. 3 characters per
    # token is deliberately pessimistic (English BPE averages ~4). ``context_tokens`` must not
    # exceed what the server actually loads the model with; a candidate may override it.
    context_tokens: int = Field(default=32_768, ge=512)
    # How tokens are counted for the budget: "lmstudio" = the server's own chat template and
    # tokenizer via the LM Studio SDK (exact; also reads the LOADED context length);
    # "approximate" = characters / chars_per_token (labelled approximate everywhere).
    budget_counting: Literal["lmstudio", "approximate"] = "approximate"
    # Before every call, verify the server itself reports all loaded models idle with an
    # empty queue ("lmstudio_cli": `lms ps --json`); "none" = no server-side verification
    # (unit tests, remote APIs). A failed or impossible verification BLOCKS the call.
    idle_check: Literal["lmstudio_cli", "none"] = "none"
    chars_per_token: float = Field(default=3.0, gt=0.5, le=8.0)
    # Output allowance assumed when a request sets no max_tokens (servers then generate up
    # to their own limit, so 0 would under-count).
    default_output_tokens: int = Field(default=4_096, ge=0)

    @model_validator(mode="after")
    def _one_url_source(self) -> ProviderConfig:
        if self.locality is ModelLocality.LOCAL and self.preset is None and self.base_url is None:
            raise ValueError("local provider needs a preset or base_url")
        if self.uses_cloud_key and self.locality is not ModelLocality.REMOTE:
            raise ValueError("uses_cloud_key is only valid for REMOTE providers")
        return self


class ModelCandidate(_Strict):
    provider: str
    model: str
    # USD per million tokens; local models cost 0.
    input_cost_per_mtok: Decimal = Decimal(0)
    output_cost_per_mtok: Decimal = Decimal(0)
    # Served context length for this model when it differs from the provider default.
    context_tokens: int | None = Field(default=None, ge=512)


class ModelsConfig(_Strict):
    presets: dict[str, PresetConfig]
    remote_patterns: list[str] = Field(default_factory=list)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    roles: dict[ModelRole, list[ModelCandidate]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _references_resolve(self) -> ModelsConfig:
        for name, provider in self.providers.items():
            if provider.preset is not None and provider.preset not in self.presets:
                raise ValueError(f"provider {name!r}: unknown preset {provider.preset!r}")
        for role, candidates in self.roles.items():
            for candidate in candidates:
                if candidate.provider not in self.providers:
                    raise ValueError(f"role {role}: unknown provider {candidate.provider!r}")
        return self

    def locality_of(self, candidate: ModelCandidate) -> ModelLocality:
        """REMOTE if the provider is remote OR the model name matches a remote pattern
        (PHASE0 R3: Ollama serves `*-cloud` models behind a local endpoint)."""
        if self.providers[candidate.provider].locality is ModelLocality.REMOTE:
            return ModelLocality.REMOTE
        name = candidate.model.lower()
        if any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in self.remote_patterns):
            return ModelLocality.REMOTE
        return ModelLocality.LOCAL


def load_models_config(config_dir: Path) -> ModelsConfig:
    path = config_dir / "models.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError("models.yaml not found", path=str(path)) from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError("models.yaml is not valid YAML", path=str(path)) from exc
    try:
        return ModelsConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise ConfigurationError(
            "models.yaml failed validation",
            path=str(path),
            errors=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()],
        ) from exc
