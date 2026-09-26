"""Derived-metric definitions (spec §17, API_PLAN §2.10): configurable formulas and weights.

* Global defaults (``brand_id IS NULL``) are seeded lazily from ``analytics/metrics.py``;
  seeding is idempotent (ON CONFLICT DO NOTHING on the version / active-key constraints).
* Rows are immutable. An edit (PATCH) deactivates the current row and inserts revision
  ``version_no + 1`` in the same scope; reports cite the definition id and revision used.
* A brand definition with the same key as a global one overrides it for that brand.
* Formulas are parsed by ``analytics/formula.py`` (restricted grammar, never ``eval``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import ColumnElement, Select, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.analytics.formula import Formula, FormulaError, from_json, parse_formula, to_json
from omnigrowth.analytics.metrics import DEFINITIONS
from omnigrowth.core.enums import ActorType
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import Brand, DerivedMetricDefinition
from omnigrowth.schemas.analytics import (
    DerivedMetricDefinitionCreate,
    DerivedMetricDefinitionRead,
    DerivedMetricDefinitionUpdate,
)

SEED_ACTOR_ID = "seed:defaults"


@dataclass(frozen=True, slots=True)
class ResolvedDefinition:
    id: uuid.UUID
    key: str
    name: str
    version_no: int
    brand_id: uuid.UUID | None
    formula: Formula

    def citation(self) -> dict[str, object]:
        return {"definition_id": str(self.id), "key": self.key, "revision": self.version_no}


def _invalid(exc: FormulaError) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.VALIDATION_FAILURE,
        f"invalid formula: {exc}",
        details={"reason": "INVALID_FORMULA", "field": "formula"},
    )


def _read(row: DerivedMetricDefinition) -> DerivedMetricDefinitionRead:
    return DerivedMetricDefinitionRead(
        id=row.id,
        brand_id=row.brand_id,
        key=row.key,
        name=row.name,
        description=row.description,
        formula=str(row.formula["expression"]),
        weights=dict(row.formula.get("weights") or {}),
        metrics=sorted(from_json(row.formula).metrics),
        revision=row.version_no,
        is_active=row.is_active,
        is_default=row.brand_id is None,
        created_by=f"{row.actor_type.value}:{row.actor_id}",
        created_at=row.created_at,
    )


async def ensure_defaults(session: AsyncSession) -> None:
    """Seed missing global defaults once, in their own committed transaction step, so a
    report never cites an id that a rolled-back read request would discard. After the first
    seed this is a single SELECT (no writes on read paths)."""
    present = set(
        (
            await session.execute(
                select(DerivedMetricDefinition.key).where(
                    DerivedMetricDefinition.brand_id.is_(None),
                    DerivedMetricDefinition.version_no == 1,
                )
            )
        ).scalars()
    )
    missing = [d for d in DEFINITIONS if d.key not in present]
    if not missing:
        return
    for definition in missing:
        await session.execute(
            pg_insert(DerivedMetricDefinition)
            .values(
                id=uuid7(),
                brand_id=None,
                key=definition.key,
                version_no=1,
                name=definition.name,
                description=definition.description,
                formula=to_json(definition.formula),
                is_active=True,
                actor_type=ActorType.SYSTEM,
                actor_id=SEED_ACTOR_ID,
            )
            .on_conflict_do_nothing()
        )
    await session.commit()


def _active_rows(brand_id: uuid.UUID | None) -> Select[tuple[DerivedMetricDefinition]]:
    scope: ColumnElement[bool] = DerivedMetricDefinition.brand_id.is_(None)
    if brand_id is not None:
        scope = or_(scope, DerivedMetricDefinition.brand_id == brand_id)
    return (
        select(DerivedMetricDefinition)
        .where(DerivedMetricDefinition.is_active, scope)
        .order_by(DerivedMetricDefinition.key, DerivedMetricDefinition.brand_id.nulls_first())
    )


async def resolve(
    session: AsyncSession, brand_id: uuid.UUID | None
) -> dict[str, ResolvedDefinition]:
    """Active definitions for a brand, keyed by ``key`` (brand rows override global ones)."""
    await ensure_defaults(session)
    resolved: dict[str, ResolvedDefinition] = {}
    for row in (await session.execute(_active_rows(brand_id))).scalars():
        resolved[row.key] = ResolvedDefinition(
            row.id, row.key, row.name, row.version_no, row.brand_id, from_json(row.formula)
        )
    return resolved


async def get_definition(session: AsyncSession, definition_id: uuid.UUID) -> ResolvedDefinition:
    await ensure_defaults(session)
    row = await session.get(DerivedMetricDefinition, definition_id)
    if row is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "derived metric definition not found")
    return ResolvedDefinition(
        row.id, row.key, row.name, row.version_no, row.brand_id, from_json(row.formula)
    )


async def list_definitions(
    session: AsyncSession, brand_id: uuid.UUID | None, *, include_history: bool = False
) -> list[DerivedMetricDefinitionRead]:
    await ensure_defaults(session)
    if include_history:
        scope: ColumnElement[bool] = DerivedMetricDefinition.brand_id.is_(None)
        if brand_id is not None:
            scope = or_(scope, DerivedMetricDefinition.brand_id == brand_id)
        stmt = (
            select(DerivedMetricDefinition)
            .where(scope)
            .order_by(DerivedMetricDefinition.key, DerivedMetricDefinition.version_no)
        )
    else:
        stmt = _active_rows(brand_id)
    return [_read(r) for r in (await session.execute(stmt)).scalars()]


async def create_definition(
    session: AsyncSession, actor: Actor, body: DerivedMetricDefinitionCreate
) -> DerivedMetricDefinitionRead:
    if await session.get(Brand, body.brand_id) is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "brand not found")
    try:
        formula = parse_formula(body.formula, body.weights)
    except FormulaError as exc:
        raise _invalid(exc) from exc
    await ensure_defaults(session)
    existing = await session.scalar(
        select(DerivedMetricDefinition.id).where(
            DerivedMetricDefinition.brand_id == body.brand_id,
            DerivedMetricDefinition.key == body.key,
            DerivedMetricDefinition.is_active,
        )
    )
    if existing is not None:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "an active definition with this key exists; PATCH it to create a revision",
            details={"definition_id": str(existing)},
        )
    last = await session.scalar(
        select(func.max(DerivedMetricDefinition.version_no)).where(
            DerivedMetricDefinition.brand_id == body.brand_id,
            DerivedMetricDefinition.key == body.key,
        )
    )
    row = DerivedMetricDefinition(
        id=uuid7(),
        brand_id=body.brand_id,
        key=body.key,
        version_no=(last or 0) + 1,
        name=body.name,
        description=body.description,
        formula=to_json(formula),
        is_active=True,
        actor_type=actor.type,
        actor_id=actor.id,
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:  # a concurrent create won the unique (brand, key) index
        await session.rollback()
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "an active definition with this key was just created; PATCH it instead",
            details={"key": body.key},
        ) from exc
    await session.refresh(row)
    return _read(row)


async def revise_definition(
    session: AsyncSession,
    actor: Actor,
    definition_id: uuid.UUID,
    body: DerivedMetricDefinitionUpdate,
) -> DerivedMetricDefinitionRead:
    """New immutable revision; the old one stays for reports that cited it."""
    await ensure_defaults(session)
    current = await session.scalar(
        select(DerivedMetricDefinition)
        .where(DerivedMetricDefinition.id == definition_id)
        .with_for_update()
    )
    if current is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "derived metric definition not found")
    if not current.is_active:
        raise OmniGrowthError(
            ErrorCode.CONFLICT,
            "only the active revision can be revised",
            details={"definition_id": str(definition_id)},
        )
    expression = body.formula if body.formula is not None else str(current.formula["expression"])
    weights = body.weights if body.weights is not None else current.formula.get("weights") or {}
    try:
        formula = parse_formula(expression, weights)
    except FormulaError as exc:
        raise _invalid(exc) from exc
    await session.execute(
        update(DerivedMetricDefinition)
        .where(DerivedMetricDefinition.id == current.id)
        .values(is_active=False)
    )
    await session.flush()
    row = DerivedMetricDefinition(
        id=uuid7(),
        brand_id=current.brand_id,
        key=current.key,
        version_no=current.version_no + 1,
        name=body.name if body.name is not None else current.name,
        description=body.description if body.description is not None else current.description,
        formula=to_json(formula),
        is_active=True,
        actor_type=actor.type,
        actor_id=actor.id,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _read(row)
