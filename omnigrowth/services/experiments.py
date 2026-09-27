"""Experiments (spec §18, STATE_MACHINES §4, SPEC_AMENDMENTS A14, docs/EXPERIMENTS.md).

Pre-registered, fixed-endpoint experiments whose unit of analysis is one published post:

* create (E1): design + variants (first = control) are fixed up front;
* units: RANDOMIZED experiments allocate each registered post to a variant server-side
  (equal allocation, blocked by platform account, seeded and auditable); OBSERVATIONAL
  ones take the human's labels and can never declare a winner;
* start (E2): guards, then the sample plan (power 80 % against the pre-registered minimum
  effect, baseline variability from REAL history, floor 30 per variant) is frozen;
* E3/E6 on the scheduler tick; evaluate (E4/E5) only once ``end_at`` has passed and only
  once (terminal) — no peeking, no stopping at the first significant look;
* the metric is read from ONE snapshot per post at a fixed age; simulated snapshots
  (MOCK/FIXTURE) are analysed separately and can never select a winner.
"""

from __future__ import annotations

import logging
import random
import secrets
import statistics
import uuid
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Table, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.analytics.experiment_stats import bonferroni, compare, required_sample
from omnigrowth.analytics.formula import COST_METRICS, SNAPSHOT_METRICS, Formula, evaluate
from omnigrowth.core.enums import (
    ActorType,
    CampaignStatus,
    ContentAssetStatus,
    ExperimentStatus,
    JobKind,
    MetricSource,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import get_context
from omnigrowth.core.state_machines import EXPERIMENT, Actor, apply_transition, create_with_state
from omnigrowth.models import (
    AnalyticsSnapshot,
    AuditEvent,
    Brand,
    Campaign,
    ContentAsset,
    Experiment,
    ExperimentUnit,
    ExperimentVariant,
    PlatformAccount,
    Publication,
)
from omnigrowth.schemas.experiments import (
    CONFIDENCE_LEVEL,
    ExperimentCreate,
    ExperimentRead,
    ExperimentResultRead,
    UnitRead,
    UnitsCreate,
    VariantRead,
)
from omnigrowth.services import metric_definitions
from omnigrowth.services.jobs import JobQueue, cancel_queued, create_job, dispatch

log = logging.getLogger(__name__)

_EXPERIMENTS = cast(Table, Experiment.__table__)
_AUDIT = cast(Table, AuditEvent.__table__)
SYSTEM_EXPERIMENTS = Actor(ActorType.SYSTEM, "experiments")
SCHEDULER_EXPERIMENTS = Actor(ActorType.SCHEDULER, "scheduler:experiments")
MIN_BASELINE_POSTS = 10
BASELINE_LOOKBACK_DAYS = 180
REAL_SOURCES = (MetricSource.API, MetricSource.MANUAL)
INTERPRETATION = (
    "Each confidence interval is a frequentist interval at the stated level: across repeated "
    "experiments, intervals built this way cover the true difference at that rate. It is NOT "
    "the probability that a variant is better. A winner is declared only for a randomized "
    "design, on real (non-simulated) data, with every variant at or above the frozen sample "
    "requirement, and a Bonferroni-adjusted p-value below 1 - confidence level (plus a "
    "pairwise win over any other significant treatment)."
)


def _correlation() -> str:
    return str(get_context().get("correlation_id") or uuid7())


def _conflict(message: str, guard: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.INVALID_STATE_TRANSITION,
        message,
        details={"failed_guard": guard, **details},
    )


def _invalid(message: str, **details: object) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.VALIDATION_FAILURE, message, details=dict(details))


# ------------------------------------------------------------------ metric
@dataclass(frozen=True, slots=True)
class MetricSpec:
    key: str
    formula: Formula
    definition_id: uuid.UUID | None
    revision: int | None

    def citation(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "definition_id": str(self.definition_id) if self.definition_id else None,
            "revision": self.revision,
            "formula": self.formula.source,
        }


async def _resolve_metric(
    session: AsyncSession, brand_id: uuid.UUID, experiment: Experiment | None, key: str
) -> MetricSpec:
    from omnigrowth.analytics.formula import parse_formula

    if experiment is not None and experiment.metric_definition_id is not None:
        found = await metric_definitions.get_definition(session, experiment.metric_definition_id)
        return MetricSpec(found.key, found.formula, found.id, found.version_no)
    if experiment is not None and experiment.raw_metric is not None:
        return MetricSpec(experiment.raw_metric, parse_formula(experiment.raw_metric), None, None)
    definitions = await metric_definitions.resolve(session, brand_id)
    if key in definitions:
        d = definitions[key]
        spec = MetricSpec(d.key, d.formula, d.id, d.version_no)
    elif key in SNAPSHOT_METRICS:
        spec = MetricSpec(key, parse_formula(key), None, None)
    else:
        raise _invalid(
            f"unknown metric {key!r}",
            field="metric",
            allowed=sorted(definitions) + sorted(SNAPSHOT_METRICS),
        )
    if spec.formula.metrics & set(COST_METRICS):
        raise _invalid(
            "experiment metrics must be audience outcomes; cost/time inputs are not measured "
            "per unit at a fixed age",
            field="metric",
        )
    return spec


# ------------------------------------------------------------------ create (E1)
async def create_experiment(
    session: AsyncSession, actor: Actor, body: ExperimentCreate
) -> ExperimentRead:
    if await session.get(Brand, body.brand_id) is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "brand not found")
    campaign = await session.get(Campaign, body.campaign_id)
    if campaign is None or campaign.brand_id != body.brand_id:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "campaign not found")
    if campaign.status not in (CampaignStatus.ACTIVE, CampaignStatus.PAUSED):
        raise _conflict(
            "experiments need an ACTIVE or PAUSED campaign",
            "campaign_active_or_paused",
            status=campaign.status.value,
        )
    accounts = (
        (
            await session.execute(
                select(PlatformAccount).where(
                    PlatformAccount.id.in_(body.design.platform_account_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    if len(accounts) != len(body.design.platform_account_ids) or any(
        a.brand_id != body.brand_id or a.platform is not body.design.platform for a in accounts
    ):
        raise _invalid(
            "every platform account must belong to the brand and to the design's platform",
            field="design.platform_account_ids",
        )
    metric = await _resolve_metric(session, body.brand_id, None, body.metric)
    experiment_id = uuid7()
    design = body.design.model_dump(mode="json")
    design["unit"] = (
        "one published post (content asset) on one of the design's platform accounts; its "
        "metric is read from a single snapshot at observation_hours +/- age_tolerance_hours"
    )
    # Seed for the blocked randomization: stored so every allocation can be re-derived.
    design["randomization_seed"] = secrets.randbits(63)
    design["metric"] = metric.citation()
    design["variant_order"] = [v.label for v in body.variants]  # first = control
    await create_with_state(
        session,
        machine=EXPERIMENT,
        table=_EXPERIMENTS,
        audit_table=_AUDIT,
        row={
            "id": experiment_id,
            "brand_id": body.brand_id,
            "campaign_id": body.campaign_id,
            "name": body.name,
            "hypothesis": body.hypothesis,
            "variable": body.variable,
            "metric_definition_id": metric.definition_id,
            "raw_metric": None if metric.definition_id else metric.key,
            "min_sample_size": body.design.min_units_per_variant,
            "confidence_threshold": Decimal(str(CONFIDENCE_LEVEL)),
            "end_at": body.end_at,
            "design": design,
            "assignment": body.design.assignment,
        },
        trigger="create",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=body.brand_id,
    )
    for variant in body.variants:
        session.add(
            ExperimentVariant(
                id=uuid7(),
                experiment_id=experiment_id,
                label=variant.label,
                description=variant.description,
            )
        )
    await session.commit()
    return await get_experiment(session, experiment_id)


# ------------------------------------------------------------------ reads
async def _require(
    session: AsyncSession, experiment_id: uuid.UUID, *, lock: bool = False
) -> Experiment:
    # populate_existing: transitions are Core UPDATEs, so a cached ORM object would be stale.
    stmt = (
        select(Experiment)
        .where(Experiment.id == experiment_id)
        .execution_options(populate_existing=True)
    )
    if lock:
        stmt = stmt.with_for_update()
    experiment = await session.scalar(stmt)
    if experiment is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "experiment not found")
    return experiment


async def _variants(session: AsyncSession, experiment_id: uuid.UUID) -> list[ExperimentVariant]:
    """Variants in their pre-registered order; the first is the control.

    The order is frozen in ``design.variant_order`` at creation: rows inserted in one
    transaction share ``created_at``, and UUIDv7 ids from the same millisecond are not
    ordered, so neither column can define "first".
    """
    experiment = await _require(session, experiment_id)
    order = {label: i for i, label in enumerate(experiment.design.get("variant_order", []))}
    rows = await session.execute(
        select(ExperimentVariant).where(ExperimentVariant.experiment_id == experiment_id)
    )
    return sorted(rows.scalars(), key=lambda v: (order.get(v.label, len(order)), v.label))


async def _unit_counts(session: AsyncSession, experiment_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(ExperimentUnit.variant_id, func.count())
        .where(ExperimentUnit.experiment_id == experiment_id)
        .group_by(ExperimentUnit.variant_id)
    )
    return {variant_id: int(n) for variant_id, n in rows.all()}


async def get_experiment(session: AsyncSession, experiment_id: uuid.UUID) -> ExperimentRead:
    experiment = await _require(session, experiment_id)
    variants = await _variants(session, experiment_id)
    counts = await _unit_counts(session, experiment_id)
    result = experiment.result or {}
    return ExperimentRead(
        id=experiment.id,
        brand_id=experiment.brand_id,
        campaign_id=experiment.campaign_id,
        name=experiment.name,
        hypothesis=experiment.hypothesis,
        variable=experiment.variable,
        metric=dict(experiment.design.get("metric") or {}),
        status=experiment.status,
        assignment=experiment.assignment,
        design=dict(experiment.design),
        confidence_level=float(experiment.confidence_threshold),
        min_units_per_variant=experiment.min_sample_size,
        start_at=experiment.start_at,
        end_at=experiment.end_at,
        variants=[
            VariantRead(
                id=v.id,
                label=v.label,
                description=v.description,
                is_control=i == 0,
                units=int(counts.get(v.id, 0)),
                sample_size=v.sample_size,
                observed_value=float(v.observed_value) if v.observed_value is not None else None,
            )
            for i, v in enumerate(variants)
        ],
        winner_variant_id=experiment.winner_variant_id,
        verdict=result.get("verdict"),
    )


async def list_experiments(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None,
    campaign_id: uuid.UUID | None,
    status: ExperimentStatus | None,
) -> list[ExperimentRead]:
    stmt = select(Experiment.id).order_by(Experiment.created_at.desc()).limit(200)
    if brand_id is not None:
        stmt = stmt.where(Experiment.brand_id == brand_id)
    if campaign_id is not None:
        stmt = stmt.where(Experiment.campaign_id == campaign_id)
    if status is not None:
        stmt = stmt.where(Experiment.status == status)
    ids = list((await session.execute(stmt)).scalars())
    return [await get_experiment(session, i) for i in ids]


# ------------------------------------------------------------------ units
def _run_sizes(seed: int, block_key: str, variants: int) -> Iterator[int]:
    """Randomised block sizes (V or 2V) so the last assignments of a block are not
    predictable from the earlier ones in that account."""
    run = 0
    while True:
        rng = random.Random(f"{seed}:{block_key}:size:{run}")  # noqa: S311 - allocation
        yield variants * rng.choice((1, 2))
        run += 1


def allocate(
    seed: int, block_key: str, block_seq: int, variant_ids: Sequence[uuid.UUID]
) -> uuid.UUID:
    """Blocked equal allocation within an account: consecutive runs of V or 2V units (size
    drawn per run) contain every variant equally often, in an order shuffled by a PRNG
    seeded with (seed, account, run). Deterministic and re-derivable for audits."""
    size = len(variant_ids)
    start = 0
    for run, run_size in enumerate(_run_sizes(seed, block_key, size)):
        if block_seq < start + run_size:
            order = list(variant_ids) * (run_size // size)
            random.Random(f"{seed}:{block_key}:{run}").shuffle(order)  # noqa: S311 - allocation
            return order[block_seq - start]
        start += run_size
    raise AssertionError("unreachable")  # pragma: no cover


ACTIVE_STATES = (ExperimentStatus.DRAFT, ExperimentStatus.RUNNING, ExperimentStatus.COLLECTING)


async def register_units(
    session: AsyncSession, actor: Actor, experiment_id: uuid.UUID, body: UnitsCreate
) -> list[UnitRead]:
    experiment = await _require(session, experiment_id, lock=True)
    if experiment.status not in (ExperimentStatus.DRAFT, ExperimentStatus.RUNNING):
        raise _conflict(
            "units can only be added while DRAFT or RUNNING",
            "accepting_units",
            status=experiment.status.value,
        )
    requested = [u.content_asset_id for u in body.units]
    if len(set(requested)) != len(requested):
        raise _invalid("a post can be listed only once", field="units")
    busy = set(
        (
            await session.execute(
                select(ExperimentUnit.content_asset_id)
                .join(Experiment, Experiment.id == ExperimentUnit.experiment_id)
                .where(
                    ExperimentUnit.content_asset_id.in_(requested),
                    Experiment.status.in_(ACTIVE_STATES),
                )
            )
        ).scalars()
    )
    if busy:
        raise _invalid(
            "a post can be a unit of only one active experiment (its outcome would be "
            "shared by two treatments)",
            field="units",
            content_asset_ids=sorted(str(b) for b in busy),
        )
    design = experiment.design
    variants = await _variants(session, experiment_id)
    by_label = {v.label: v for v in variants}
    allowed_accounts = {uuid.UUID(a) for a in design["platform_account_ids"]}
    randomized = experiment.assignment == "RANDOMIZED"
    created: list[UnitRead] = []
    for unit in body.units:
        asset = await session.get(ContentAsset, unit.content_asset_id)
        if asset is None or asset.brand_id != experiment.brand_id:
            raise OmniGrowthError(ErrorCode.NOT_FOUND, "content asset not found")
        if asset.platform.value != design["platform"]:
            raise _invalid(
                "the unit's platform differs from the design's platform",
                field="content_asset_id",
                content_asset_id=str(asset.id),
            )
        if unit.platform_account_id not in allowed_accounts:
            raise _invalid(
                "the unit's account is not one of the design's accounts",
                field="platform_account_id",
            )
        if asset.status in (ContentAssetStatus.REJECTED, ContentAssetStatus.ARCHIVED):
            raise _invalid(
                "rejected or archived content cannot be a unit", field="content_asset_id"
            )
        if randomized:
            if unit.variant_label is not None:
                raise _invalid(
                    "randomized experiments allocate variants; do not send variant_label",
                    field="variant_label",
                )
            if asset.status is ContentAssetStatus.PUBLISHED:
                raise _invalid(
                    "a randomized unit must be assigned before it is published",
                    field="content_asset_id",
                )
        elif unit.variant_label not in by_label:
            raise _invalid(
                "observational units need the variant_label of an existing variant",
                field="variant_label",
            )
        block = str(unit.platform_account_id)
        seq = int(
            await session.scalar(
                select(func.count()).where(
                    ExperimentUnit.experiment_id == experiment_id,
                    ExperimentUnit.block_key == block,
                )
            )
            or 0
        )
        if randomized:
            variant_id = allocate(
                int(design["randomization_seed"]), block, seq, [v.id for v in variants]
            )
        else:
            variant_id = by_label[cast(str, unit.variant_label)].id
        row = ExperimentUnit(
            id=uuid7(),
            experiment_id=experiment_id,
            variant_id=variant_id,
            content_asset_id=asset.id,
            platform_account_id=unit.platform_account_id,
            block_key=block,
            block_seq=seq,
            assigned_by="RANDOMIZATION" if randomized else "MANUAL",
        )
        session.add(row)
        await session.flush()
        label = next(v.label for v in variants if v.id == variant_id)
        created.append(
            UnitRead(
                id=row.id,
                content_asset_id=asset.id,
                platform_account_id=unit.platform_account_id,
                variant_id=variant_id,
                variant_label=label,
                assigned_by=row.assigned_by,
                block_key=block,
                block_seq=seq,
            )
        )
    await session.commit()
    return created


# ------------------------------------------------------------------ start (E2)
async def _baseline(
    session: AsyncSession, experiment: Experiment, metric: MetricSpec
) -> tuple[float | None, float | None, int]:
    """SD and mean of the metric per post at the design's age, from REAL evidence on the
    brand's posts on the same accounts published BEFORE the experiment was created, never
    including the experiment's own units."""
    design = experiment.design
    own = set(
        (
            await session.execute(
                select(ExperimentUnit.content_asset_id).where(
                    ExperimentUnit.experiment_id == experiment.id
                )
            )
        ).scalars()
    )
    values = await _unit_values_for_publications(
        session,
        brand_id=experiment.brand_id,
        account_ids=[uuid.UUID(a) for a in design["platform_account_ids"]],
        since=(experiment.created_at - timedelta(days=BASELINE_LOOKBACK_DAYS)),
        until=experiment.created_at,
        exclude_assets=own,
        observation=timedelta(hours=design["observation_hours"]),
        tolerance=timedelta(hours=design["age_tolerance_hours"]),
        metric=metric,
    )
    if len(values) < MIN_BASELINE_POSTS:
        return None, None, len(values)
    return statistics.stdev(values), statistics.fmean(values), len(values)


async def start_experiment(
    session: AsyncSession, actor: Actor, experiment_id: uuid.UUID, *, now: datetime
) -> ExperimentRead:
    experiment = await _require(session, experiment_id, lock=True)
    if experiment.status is not ExperimentStatus.DRAFT:
        raise _conflict(
            "only a DRAFT experiment can start", "is_draft", status=experiment.status.value
        )
    campaign = await session.get(Campaign, experiment.campaign_id)
    if campaign is None or campaign.status is not CampaignStatus.ACTIVE:
        raise _conflict("the campaign must be ACTIVE", "campaign_active")
    design = dict(experiment.design)
    if not design.get("single_variable_attested"):
        raise _conflict(
            "a human must attest that variants differ only in the declared variable",
            "single_variable_attested",
        )
    window = timedelta(hours=design["observation_hours"] + design["age_tolerance_hours"])
    if experiment.end_at is None or experiment.end_at < now + window:
        raise _conflict(
            "end_at must leave observation_hours + age_tolerance_hours for posts to reach "
            "their measurement age",
            "end_after_observation_window",
        )
    variants = await _variants(session, experiment_id)
    per_variant = await _unit_counts(session, experiment_id)
    if len(variants) < 2 or any(per_variant.get(v.id, 0) < 1 for v in variants):
        raise _conflict("every variant needs at least one unit", "variants_have_units")
    metric = await _resolve_metric(session, experiment.brand_id, experiment, "")
    sigma, baseline_mean, baseline_n = await _baseline(session, experiment, metric)
    effect = design["minimum_effect"]
    delta: float | None = float(effect["value"])
    if effect["kind"] == "RELATIVE":
        delta = None if baseline_mean in (None, 0) else abs(baseline_mean) * float(effect["value"])
    alpha = 1.0 - float(experiment.confidence_threshold)
    plan = required_sample(
        sigma=sigma,
        delta=delta,
        alpha=alpha,
        power=float(design["power"]),
        comparisons=len(variants) - 1,
        floor=int(design["min_units_per_variant"]),
    )
    design["sample_plan"] = {
        "required_units_per_variant": plan.required_per_variant,
        "power_based": plan.power_based,
        "floor": plan.floor,
        "basis": plan.basis,
        "alpha": alpha,
        "alpha_per_comparison": plan.alpha_per_comparison,
        "power": float(design["power"]),
        "baseline_sd": sigma,
        "baseline_mean": baseline_mean,
        "baseline_posts": baseline_n,
        "baseline_sources": [s.value for s in REAL_SOURCES],
        "minimum_effect_absolute": delta,
        "frozen_at": now.isoformat(),
    }
    await apply_transition(
        session,
        machine=EXPERIMENT,
        table=_EXPERIMENTS,
        audit_table=_AUDIT,
        entity_id=experiment_id,
        from_state=ExperimentStatus.DRAFT,
        trigger="start",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=experiment.brand_id,
        values={
            "start_at": now,
            "design": design,
            "min_sample_size": plan.required_per_variant,
        },
    )
    await session.commit()
    return await get_experiment(session, experiment_id)


async def cancel_experiment(
    session: AsyncSession, actor: Actor, experiment_id: uuid.UUID, reason: str
) -> ExperimentRead:
    experiment = await _require(session, experiment_id, lock=True)
    await apply_transition(
        session,
        machine=EXPERIMENT,
        table=_EXPERIMENTS,
        audit_table=_AUDIT,
        entity_id=experiment_id,
        from_state=experiment.status,
        trigger="cancel",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=experiment.brand_id,
        reason=reason,
    )
    await session.commit()
    return await get_experiment(session, experiment_id)


# ------------------------------------------------------------------ unit values at age
@dataclass(frozen=True, slots=True)
class UnitValue:
    variant_id: uuid.UUID
    block: str
    value: float | None
    snapshot_id: uuid.UUID | None
    source: MetricSource | None
    # OK | NOT_PUBLISHED | ACCOUNT_MISMATCH | NOT_YET_AT_AGE | NO_SNAPSHOT_AT_AGE |
    # METRIC_NULL | MOCK_PUBLICATION (real analysis only)
    status: str


def is_real_evidence(publication: Publication, snapshot: AnalyticsSnapshot) -> bool:
    """REAL = numbers from the platform (API) or read off it by a human (MANUAL) for a post
    that actually went out (publish mode LIVE or MANUAL export). A MOCK publication never
    reached an audience, so any number attached to it is simulated, whatever its source."""
    return snapshot.source in REAL_SOURCES and publication.publish_mode is not PublishMode.MOCK


def _closest(
    snapshots: Sequence[AnalyticsSnapshot], target: datetime, tolerance: timedelta
) -> AnalyticsSnapshot | None:
    """The one snapshot per unit: captured closest to the target age, inside the tolerance.
    Later or earlier pulls of the same post are never extra samples."""
    inside = [s for s in snapshots if abs(s.captured_at - target) <= tolerance]
    return min(inside, key=lambda s: (abs(s.captured_at - target), s.id)) if inside else None


async def _snapshots_by_publication(
    session: AsyncSession, publication_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[AnalyticsSnapshot]]:
    found: dict[uuid.UUID, list[AnalyticsSnapshot]] = defaultdict(list)
    if not publication_ids:
        return found
    rows = await session.execute(
        select(AnalyticsSnapshot).where(AnalyticsSnapshot.publication_id.in_(publication_ids))
    )
    for snapshot in rows.scalars():
        found[snapshot.publication_id].append(snapshot)
    return found


def _value(metric: MetricSpec, snapshot: AnalyticsSnapshot) -> float | None:
    inputs = {k: getattr(snapshot, k) for k in SNAPSHOT_METRICS}
    return evaluate(metric.formula, inputs).value


async def _unit_values(
    session: AsyncSession,
    experiment: Experiment,
    metric: MetricSpec,
    *,
    real: bool,
    now: datetime,
) -> list[UnitValue]:
    """One value per unit from REAL or from SIMULATED evidence only (each analysis takes its
    own closest-to-age snapshot, so the two can never mix)."""
    design = experiment.design
    observation = timedelta(hours=design["observation_hours"])
    tolerance = timedelta(hours=design["age_tolerance_hours"])
    units = list(
        (
            await session.execute(
                select(ExperimentUnit).where(ExperimentUnit.experiment_id == experiment.id)
            )
        ).scalars()
    )
    publications: dict[uuid.UUID, Publication] = {}
    if units:
        rows = await session.execute(
            select(Publication)
            .where(
                Publication.content_asset_id.in_([u.content_asset_id for u in units]),
                Publication.status.in_([PublicationStatus.PUBLISHED, PublicationStatus.DELETED]),
                Publication.published_at.is_not(None),
            )
            .order_by(Publication.published_at)
        )
        for found_pub in rows.scalars():
            # The first publication of the asset is the unit's exposure.
            publications.setdefault(found_pub.content_asset_id, found_pub)
    snaps = await _snapshots_by_publication(session, [p.id for p in publications.values()])
    values: list[UnitValue] = []

    def excluded(unit: ExperimentUnit, status: str) -> UnitValue:
        return UnitValue(unit.variant_id, unit.block_key, None, None, None, status)

    for unit in units:
        pub = publications.get(unit.content_asset_id)
        if pub is None or pub.published_at is None:
            values.append(excluded(unit, "NOT_PUBLISHED"))
            continue
        if pub.platform_account_id != unit.platform_account_id:
            values.append(excluded(unit, "ACCOUNT_MISMATCH"))
            continue
        if real and pub.publish_mode is PublishMode.MOCK:
            values.append(excluded(unit, "MOCK_PUBLICATION"))
            continue
        target = pub.published_at + observation
        if target + tolerance > now:
            values.append(excluded(unit, "NOT_YET_AT_AGE"))
            continue
        eligible = [s for s in snaps.get(pub.id, []) if is_real_evidence(pub, s) is real]
        snapshot = _closest(eligible, target, tolerance)
        if snapshot is None:
            values.append(excluded(unit, "NO_SNAPSHOT_AT_AGE"))
            continue
        value = _value(metric, snapshot)
        values.append(
            UnitValue(
                unit.variant_id,
                unit.block_key,
                value,
                snapshot.id,
                snapshot.source,
                "OK" if value is not None else "METRIC_NULL",
            )
        )
    return values


async def _unit_values_for_publications(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    account_ids: Sequence[uuid.UUID],
    since: datetime,
    until: datetime,
    exclude_assets: set[uuid.UUID],
    observation: timedelta,
    tolerance: timedelta,
    metric: MetricSpec,
) -> list[float]:
    """Per-post values at the design's age for historical posts (the baseline), REAL only."""
    pubs = list(
        (
            await session.execute(
                select(Publication).where(
                    Publication.brand_id == brand_id,
                    Publication.platform_account_id.in_(list(account_ids)),
                    Publication.published_at.is_not(None),
                    Publication.published_at >= since,
                    Publication.published_at < until,
                    Publication.publish_mode != PublishMode.MOCK,
                )
            )
        ).scalars()
    )
    pubs = [p for p in pubs if p.content_asset_id not in exclude_assets]
    snaps = await _snapshots_by_publication(session, [p.id for p in pubs])
    values: list[float] = []
    for pub in pubs:
        if pub.published_at is None:
            continue
        eligible = [s for s in snaps.get(pub.id, []) if is_real_evidence(pub, s)]
        snapshot = _closest(eligible, pub.published_at + observation, tolerance)
        if snapshot is None:
            continue
        value = _value(metric, snapshot)
        if value is not None:
            values.append(value)
    return values


# ------------------------------------------------------------------ analysis
def _analyse(
    variants: Sequence[ExperimentVariant],
    values: Sequence[UnitValue],
    *,
    confidence: float,
    required: int,
    direction: str,
) -> dict[str, Any]:
    """Per-variant summaries and treatment-vs-control comparisons on ONE set of units.

    Only accounts (blocks) in which EVERY variant has at least 2 measured units are
    analysed; the others are reported as ``BLOCK_INCOMPLETE``. So the n checked against the
    requirement, the n of the tests and the evidence ids are always the same units.
    """
    control = variants[0]
    by_block: dict[str, dict[uuid.UUID, list[UnitValue]]] = defaultdict(lambda: defaultdict(list))
    for unit in values:
        if unit.status == "OK" and unit.value is not None:
            by_block[unit.block][unit.variant_id].append(unit)
    eligible = {
        block
        for block, arms in by_block.items()
        if all(len(arms.get(v.id, [])) >= 2 for v in variants)
    }
    blocks: dict[uuid.UUID, dict[str, list[float]]] = {v.id: {} for v in variants}
    evidence: dict[uuid.UUID, list[str]] = {v.id: [] for v in variants}
    incomplete: dict[uuid.UUID, int] = defaultdict(int)
    for block, arms in by_block.items():
        for variant_id, units in arms.items():
            if block in eligible:
                blocks[variant_id][block] = [cast(float, u.value) for u in units]
                evidence[variant_id].extend(str(u.snapshot_id) for u in units)
            else:
                incomplete[variant_id] += len(units)
    per_variant: list[dict[str, Any]] = []
    for i, v in enumerate(variants):
        flat = [x for block in blocks[v.id].values() for x in block]
        statuses: dict[str, int] = defaultdict(int)
        for unit in values:
            if unit.variant_id == v.id and unit.status != "OK":
                statuses[unit.status] += 1
        if incomplete.get(v.id):
            statuses["BLOCK_INCOMPLETE"] += incomplete[v.id]
        per_variant.append(
            {
                "variant_id": str(v.id),
                "label": v.label,
                "is_control": i == 0,
                "n": len(flat),
                "mean": statistics.fmean(flat) if flat else None,
                "sd": statistics.stdev(flat) if len(flat) > 1 else None,
                "excluded": dict(statuses),
                "meets_requirement": len(flat) >= required,
                "snapshot_ids": sorted(evidence[v.id]),
            }
        )
    alpha = 1.0 - confidence
    comparisons = _comparisons(
        control, list(variants[1:]), blocks, alpha=alpha, direction=direction
    )
    return {
        "per_variant": per_variant,
        "comparisons": comparisons,
        "accounts_analysed": sorted(eligible),
    }


def _comparisons(
    reference: ExperimentVariant,
    others: Sequence[ExperimentVariant],
    blocks: dict[uuid.UUID, dict[str, list[float]]],
    *,
    alpha: float,
    direction: str,
) -> list[dict[str, Any]]:
    """Each ``other`` minus ``reference``, Bonferroni-adjusted over the family, with
    Bonferroni-simultaneous intervals (significant <=> interval excludes 0)."""
    m = max(len(others), 1)
    level = 1.0 - alpha / m
    raw = [(v, compare(blocks[reference.id], blocks[v.id], ci_level=level)) for v in others]
    usable = [(v, c) for v, c in raw if c is not None]
    adjusted = bonferroni([c.p_value for _, c in usable])
    out: list[dict[str, Any]] = []
    for (variant, c), p_adj in zip(usable, adjusted, strict=True):
        favourable = c.difference > 0 if direction == "HIGHER_IS_BETTER" else c.difference < 0
        out.append(
            {
                "variant_id": str(variant.id),
                "label": variant.label,
                "versus": reference.label,
                "difference": c.difference,
                "relative_lift": c.relative_lift,
                "hedges_g": c.hedges_g,
                "std_error": c.std_error,
                "df": c.df,
                "ci_low": c.ci_low,
                "ci_high": c.ci_high,
                "ci_level": c.ci_level,
                "p_value": c.p_value,
                "p_value_adjusted": p_adj,
                "adjustment": f"Bonferroni x{m}" if m > 1 else "none",
                "significant": p_adj < alpha,
                "favourable": favourable,
                "blocks_used": c.blocks_used,
                "degenerate": c.degenerate,
            }
        )
    out.extend(
        {
            "variant_id": str(variant.id),
            "label": variant.label,
            "versus": reference.label,
            "not_estimable": "no account has at least 2 measured units in both arms",
        }
        for variant, c in raw
        if c is None
    )
    return out


def _decide(
    variants: Sequence[ExperimentVariant],
    real: dict[str, Any],
    *,
    randomized: bool,
    required: int,
    alpha: float,
    direction: str,
    blocks: dict[uuid.UUID, dict[str, list[float]]],
) -> tuple[str, uuid.UUID | None, str | None, list[dict[str, Any]]]:
    """(verdict, winner, failed_guard, pairwise). Winners only for randomized real data.

    One treatment significantly better than control -> it wins. Several -> the best must
    also be significantly better than each other such treatment (pairwise Welch,
    Bonferroni over those pairs); otherwise "beat control, not distinguished".
    """
    if all(v["n"] == 0 for v in real["per_variant"]):
        return "NO_REAL_DATA", None, "no_real_data", []
    if any(v["n"] < required for v in real["per_variant"]):
        return "INSUFFICIENT_EVIDENCE", None, "insufficient_sample", []
    if not randomized:
        return "OBSERVATIONAL_ASSOCIATION", None, "observational_design", []
    comparisons = [c for c in real["comparisons"] if "not_estimable" not in c]
    if len(comparisons) != len(variants) - 1:
        return "INSUFFICIENT_EVIDENCE", None, "metric_unavailable", []
    better = [c for c in comparisons if c["significant"] and c["favourable"]]
    if len(better) == 1:
        return "WINNER", uuid.UUID(better[0]["variant_id"]), None, []
    if better:
        sign = 1.0 if direction == "HIGHER_IS_BETTER" else -1.0
        means = {v["variant_id"]: v["mean"] for v in real["per_variant"]}
        best_id = max(better, key=lambda c: sign * means[c["variant_id"]])["variant_id"]
        by_id = {str(v.id): v for v in variants}
        rivals = [by_id[c["variant_id"]] for c in better if c["variant_id"] != best_id]
        pairwise = _comparisons(by_id[best_id], rivals, blocks, alpha=alpha, direction=direction)
        # Here "favourable" for rival - best means the rival is better; the best must beat
        # every rival: rival - best significantly unfavourable.
        if all(
            "not_estimable" not in c and c["significant"] and not c["favourable"] for c in pairwise
        ):
            return "WINNER", uuid.UUID(best_id), None, pairwise
        return "TREATMENTS_BEAT_CONTROL_UNRANKED", None, "treatments_not_distinguished", pairwise
    if all(c["significant"] and not c["favourable"] for c in comparisons):
        return "WINNER", variants[0].id, None, []  # control beats every treatment
    return "NO_DETECTABLE_DIFFERENCE", None, "below_confidence", []


async def evaluate_experiment(
    session: AsyncSession, actor: Actor, experiment_id: uuid.UUID, *, now: datetime
) -> dict[str, Any]:
    """E4/E5 once, after the fixed endpoint. Before ``end_at`` nothing happens (no peeking)."""
    experiment = await _require(session, experiment_id, lock=True)
    if experiment.status is not ExperimentStatus.COLLECTING:
        raise _conflict(
            "only a COLLECTING experiment can be evaluated",
            "is_collecting",
            status=experiment.status.value,
        )
    if experiment.end_at is None or now < experiment.end_at:
        return {"experiment_id": str(experiment_id), "no_transition": "WINDOW_OPEN"}
    design = experiment.design
    metric = await _resolve_metric(session, experiment.brand_id, experiment, "")
    variants = await _variants(session, experiment_id)
    real_values = await _unit_values(session, experiment, metric, real=True, now=now)
    simulated_values = [
        u
        for u in await _unit_values(session, experiment, metric, real=False, now=now)
        if u.status == "OK"
    ]
    confidence = float(experiment.confidence_threshold)
    required = experiment.min_sample_size
    real = _analyse(
        variants,
        real_values,
        confidence=confidence,
        required=required,
        direction=design["direction"],
    )
    simulated = None
    if simulated_values:
        simulated = _analyse(
            variants,
            simulated_values,
            confidence=confidence,
            required=required,
            direction=design["direction"],
        )
        simulated["note"] = (
            "SIMULATED evidence (MOCK/FIXTURE snapshots): shown for pipeline checks only. It "
            "never selects a winner and must not change production strategy."
        )
    randomized = experiment.assignment == "RANDOMIZED"
    real_blocks = _blocks_of(variants, real_values, set(real["accounts_analysed"]))
    verdict, winner, failed_guard, pairwise = _decide(
        variants,
        real,
        randomized=randomized,
        required=required,
        alpha=1.0 - confidence,
        direction=design["direction"],
        blocks=real_blocks,
    )
    if pairwise:
        real["pairwise_between_winners"] = pairwise
    evidence = sorted({sid for v in real["per_variant"] for sid in v["snapshot_ids"]})
    result = {
        "verdict": verdict,
        "failed_guard": failed_guard,
        "evaluated_at": now.isoformat(),
        "confidence_level": confidence,
        "required_units_per_variant": required,
        "sample_plan": design.get("sample_plan"),
        "method": _method(design, confidence, len(variants) - 1),
        "real": real,
        "simulated": simulated,
        "design": "RANDOMIZED" if randomized else "OBSERVATIONAL",
        "interpretation": INTERPRETATION,
        "evidence_snapshot_ids": evidence,
    }
    for summary, variant in zip(real["per_variant"], variants, strict=True):
        variant.sample_size = summary["n"]
        variant.observed_value = (
            Decimal(str(summary["mean"])) if summary["mean"] is not None else None
        )
    concluded = verdict == "WINNER"
    await apply_transition(
        session,
        machine=EXPERIMENT,
        table=_EXPERIMENTS,
        audit_table=_AUDIT,
        entity_id=experiment_id,
        from_state=ExperimentStatus.COLLECTING,
        trigger="evaluate_concluded" if concluded else "evaluate_inconclusive",
        actor=actor,
        correlation_id=_correlation(),
        brand_id=experiment.brand_id,
        reason=failed_guard,
        values={
            "result": result,
            "confidence": Decimal(str(round(confidence, 3))),
            "winner_variant_id": winner,
            "concluded_at": now,
        },
    )
    await session.commit()
    return {"experiment_id": str(experiment_id), "verdict": verdict, "failed_guard": failed_guard}


def _blocks_of(
    variants: Sequence[ExperimentVariant], values: Sequence[UnitValue], eligible: set[str]
) -> dict[uuid.UUID, dict[str, list[float]]]:
    blocks: dict[uuid.UUID, dict[str, list[float]]] = {v.id: defaultdict(list) for v in variants}
    for unit in values:
        if unit.status == "OK" and unit.value is not None and unit.block in eligible:
            blocks[unit.variant_id][unit.block].append(unit.value)
    return {k: dict(v) for k, v in blocks.items()}


def _method(design: dict[str, Any], confidence: float, comparisons: int) -> dict[str, Any]:
    return {
        "unit": design.get("unit"),
        "value_per_unit": (
            f"primary metric from one snapshot at {design['observation_hours']} h "
            f"+/- {design['age_tolerance_hours']} h after publication"
        ),
        "test": "two-sided Welch t-test per treatment vs control, blocked by platform account",
        "multiplicity": (
            f"Bonferroni over {comparisons} treatment-vs-control comparisons (decisions and "
            "intervals agree); a winner among several significant treatments must also beat "
            "each of them pairwise (Bonferroni over those pairs)"
            if comparisons > 1
            else "none (one comparison)"
        ),
        "analysed_accounts": "accounts where every variant has at least 2 measured posts",
        "intervals": (
            f"{confidence:.3f} two-sided"
            + (f", Bonferroni-simultaneous over {comparisons}" if comparisons > 1 else "")
        ),
        "stopping_rule": design.get("stopping_rule"),
        "allocation": design.get("allocation")
        if design.get("assignment") == "RANDOMIZED"
        else "none (observational)",
        "direction": design.get("direction"),
        "simulated_evidence": "analysed separately; never used for the verdict",
    }


async def get_results(session: AsyncSession, experiment_id: uuid.UUID) -> ExperimentResultRead:
    experiment = await _require(session, experiment_id)
    result = experiment.result or {}
    return ExperimentResultRead(
        experiment_id=experiment.id,
        status=experiment.status,
        verdict=result.get("verdict"),
        method=result.get("method")
        or _method(experiment.design, float(experiment.confidence_threshold), 1),
        confidence_level=float(experiment.confidence_threshold),
        winner_variant_id=experiment.winner_variant_id,
        inconclusive_reason=result.get("failed_guard"),
        real=result.get("real"),
        simulated=result.get("simulated"),
        evidence_snapshot_ids=[uuid.UUID(s) for s in result.get("evidence_snapshot_ids", [])],
        interpretation=INTERPRETATION,
    )


# ------------------------------------------------------------------ evaluation job + tick
async def request_evaluation(
    session: AsyncSession, queue: JobQueue, actor: Actor, experiment_id: uuid.UUID
) -> uuid.UUID:
    experiment = await _require(session, experiment_id)
    if experiment.status is not ExperimentStatus.COLLECTING:
        raise _conflict(
            "only a COLLECTING experiment can be evaluated",
            "is_collecting",
            status=experiment.status.value,
        )
    job_id = await create_job(
        session,
        kind=JobKind.EXPERIMENT_EVALUATION,
        actor=actor,
        brand_id=experiment.brand_id,
        subject_type="experiments",
        subject_id=experiment_id,
    )
    await session.commit()
    try:
        dispatch(queue, JobKind.EXPERIMENT_EVALUATION, job_id)
    except OmniGrowthError:
        await cancel_queued(session, job_id, reason="queue unavailable")
        await session.commit()
        raise
    return job_id


async def sync_experiments(session: AsyncSession, *, now: datetime) -> dict[str, int]:
    """Scheduler tick: E3 (every variant has a published unit), E6 (window closed before
    that), and the single fixed-endpoint evaluation of COLLECTING experiments."""
    counts = {"collecting": 0, "window_closed": 0, "evaluated": 0}
    running = list(
        (
            await session.execute(
                select(Experiment.id, Experiment.status).where(
                    Experiment.status.in_([ExperimentStatus.RUNNING, ExperimentStatus.COLLECTING])
                )
            )
        ).all()
    )
    for experiment_id, status in running:
        try:
            await _sync_one(session, experiment_id, status, now=now, counts=counts)
        except (OmniGrowthError, SQLAlchemyError) as exc:
            # One failing experiment must not stall the others on every tick.
            await session.rollback()
            log.error(
                "experiment sync failed",
                extra={"experiment_id": str(experiment_id), "error": type(exc).__name__},
            )
    return counts


async def _sync_one(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    status: ExperimentStatus,
    *,
    now: datetime,
    counts: dict[str, int],
) -> None:
    experiment = await _require(session, experiment_id, lock=True)
    if experiment.status is not status:
        await session.rollback()
        return
    if status is ExperimentStatus.COLLECTING:
        end_at = experiment.end_at  # read before rollback expires the ORM object
        await session.rollback()  # evaluate_experiment re-locks and re-checks the state
        if end_at is not None and now >= end_at:
            await evaluate_experiment(session, SCHEDULER_EXPERIMENTS, experiment_id, now=now)
            counts["evaluated"] += 1
        return
    published = await _variants_with_published_units(session, experiment_id)
    variants = await _variants(session, experiment_id)
    if variants and all(v.id in published for v in variants):
        await apply_transition(
            session,
            machine=EXPERIMENT,
            table=_EXPERIMENTS,
            audit_table=_AUDIT,
            entity_id=experiment_id,
            from_state=ExperimentStatus.RUNNING,
            trigger="all_variants_published",
            actor=SYSTEM_EXPERIMENTS,
            correlation_id=_correlation(),
            brand_id=experiment.brand_id,
        )
        counts["collecting"] += 1
    elif experiment.end_at is not None and now >= experiment.end_at:
        await apply_transition(
            session,
            machine=EXPERIMENT,
            table=_EXPERIMENTS,
            audit_table=_AUDIT,
            entity_id=experiment_id,
            from_state=ExperimentStatus.RUNNING,
            trigger="window_closed",
            actor=SCHEDULER_EXPERIMENTS,
            correlation_id=_correlation(),
            brand_id=experiment.brand_id,
            reason="variants_not_published",
            values={
                "result": {
                    "verdict": "INSUFFICIENT_EVIDENCE",
                    "failed_guard": "variants_not_published",
                }
            },
        )
        counts["window_closed"] += 1
    await session.commit()


async def _variants_with_published_units(
    session: AsyncSession, experiment_id: uuid.UUID
) -> set[uuid.UUID]:
    rows = await session.execute(
        select(ExperimentUnit.variant_id)
        .join(Publication, Publication.content_asset_id == ExperimentUnit.content_asset_id)
        .where(
            ExperimentUnit.experiment_id == experiment_id,
            Publication.status == PublicationStatus.PUBLISHED,
        )
        .distinct()
    )
    return set(rows.scalars())
