"""Experiment API schemas (API_PLAN §2.11, SPEC_AMENDMENTS A14, docs/EXPERIMENTS.md)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from omnigrowth.core.enums import ExperimentStatus, ExperimentVariable, Platform

FLOOR_UNITS_PER_VARIANT = 30
# Fixed by the human (A14): intervals and decisions at 0.95. Not configurable per experiment.
CONFIDENCE_LEVEL = 0.95


class MinimumEffect(BaseModel):
    """The smallest difference worth detecting, fixed before the experiment starts."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ABSOLUTE", "RELATIVE"]
    value: float = Field(gt=0, le=1_000_000)


class ExperimentDesignIn(BaseModel):
    """Pre-registered design. Frozen at start (E2) together with the sample plan."""

    model_config = ConfigDict(extra="forbid")

    assignment: Literal["RANDOMIZED", "OBSERVATIONAL"] = "RANDOMIZED"
    platform: Platform
    platform_account_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    # The metric is read from ONE snapshot per post at this age (+/- tolerance).
    observation_hours: int = Field(default=72, ge=1, le=24 * 30)
    age_tolerance_hours: int = Field(default=12, ge=1, le=72)
    direction: Literal["HIGHER_IS_BETTER", "LOWER_IS_BETTER"] = "HIGHER_IS_BETTER"
    minimum_effect: MinimumEffect
    power: float = Field(default=0.8, ge=0.5, le=0.99)
    min_units_per_variant: int = Field(default=FLOOR_UNITS_PER_VARIANT, le=100_000)
    allocation: Literal["BLOCKED_BY_ACCOUNT_EQUAL"] = "BLOCKED_BY_ACCOUNT_EQUAL"
    stopping_rule: Literal["FIXED_ENDPOINT"] = "FIXED_ENDPOINT"
    # A human attests that variants differ only in the declared variable (E2 guard).
    single_variable_attested: bool = False

    @field_validator("min_units_per_variant")
    @classmethod
    def _floor(cls, value: int) -> int:
        if value < FLOOR_UNITS_PER_VARIANT:
            raise ValueError(
                f"the floor is {FLOOR_UNITS_PER_VARIANT} independent units per variant"
            )
        return value

    @field_validator("platform_account_ids")
    @classmethod
    def _unique(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("platform_account_ids must be unique")
        return value

    @model_validator(mode="after")
    def _equivalent_ages(self) -> ExperimentDesignIn:
        # Posts are compared at equivalent ages: the window around the observation age may
        # span at most a quarter of that age on each side (e.g. 72 h +/- 18 h).
        if self.age_tolerance_hours * 4 > self.observation_hours:
            raise ValueError("age_tolerance_hours must be at most observation_hours / 4")
        return self


class VariantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(pattern=r"^[A-Z][A-Z0-9_]{0,15}$")
    description: str = Field(min_length=1, max_length=2000)


class ExperimentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brand_id: uuid.UUID
    campaign_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    hypothesis: str = Field(min_length=10, max_length=4000)
    variable: ExperimentVariable
    # A derived-metric key (active definition for the brand) or a raw snapshot count.
    metric: str = Field(min_length=1, max_length=64)
    end_at: datetime
    design: ExperimentDesignIn
    # The first variant is the control.
    variants: list[VariantIn] = Field(min_length=2, max_length=5)

    @field_validator("end_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("end_at must include a timezone offset")
        return value

    @model_validator(mode="after")
    def _labels_unique(self) -> ExperimentCreate:
        labels = [v.label for v in self.variants]
        if len(set(labels)) != len(labels):
            raise ValueError("variant labels must be unique")
        return self


class UnitAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_asset_id: uuid.UUID
    platform_account_id: uuid.UUID
    # Only for OBSERVATIONAL experiments; randomized units are allocated by the server.
    variant_label: str | None = None


class UnitsCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    units: list[UnitAssignment] = Field(min_length=1, max_length=500)


class UnitRead(BaseModel):
    id: uuid.UUID
    content_asset_id: uuid.UUID
    platform_account_id: uuid.UUID
    variant_id: uuid.UUID
    variant_label: str
    assigned_by: str
    block_key: str
    block_seq: int


class VariantRead(BaseModel):
    id: uuid.UUID
    label: str
    description: str
    is_control: bool
    units: int
    sample_size: int | None
    observed_value: float | None


class ExperimentRead(BaseModel):
    id: uuid.UUID
    brand_id: uuid.UUID
    campaign_id: uuid.UUID | None
    name: str
    hypothesis: str
    variable: ExperimentVariable
    metric: dict[str, Any]
    status: ExperimentStatus
    assignment: str
    design: dict[str, Any]
    confidence_level: float
    min_units_per_variant: int
    start_at: datetime | None
    end_at: datetime | None
    variants: list[VariantRead]
    winner_variant_id: uuid.UUID | None
    verdict: str | None


class ExperimentResultRead(BaseModel):
    experiment_id: uuid.UUID
    status: ExperimentStatus
    verdict: str | None
    method: dict[str, Any]
    confidence_level: float
    winner_variant_id: uuid.UUID | None
    inconclusive_reason: str | None
    real: dict[str, Any] | None
    simulated: dict[str, Any] | None
    evidence_snapshot_ids: list[uuid.UUID]
    interpretation: str
