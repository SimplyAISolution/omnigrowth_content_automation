"""Integration: migrations, state-machine writes, audit rows and DB triggers (Phase 2)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import ActorType, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import CAMPAIGN, Actor, apply_transition, create_with_state
from omnigrowth.models import Base
from tests.integration.conftest import _plain, run_alembic

pytestmark = pytest.mark.integration

OWNER = Actor(ActorType.HUMAN, "user-owner", UserRole.OWNER)
CAMPAIGNS = Base.metadata.tables["campaigns"]
AUDIT = Base.metadata.tables["audit_events"]
BRANDS = Base.metadata.tables["brands"]


async def _brand(session: AsyncSession) -> uuid.UUID:
    brand_id = uuid7()
    await session.execute(
        BRANDS.insert().values(
            id=brand_id, slug=f"b-{brand_id.hex[:8]}", name="Simply AI", display_name="Simply AI"
        )
    )
    return brand_id


async def _campaign(session: AsyncSession, brand_id: uuid.UUID) -> uuid.UUID:
    campaign_id = uuid7()
    await create_with_state(
        session,
        machine=CAMPAIGN,
        table=CAMPAIGNS,
        audit_table=AUDIT,
        row={
            "id": campaign_id,
            "brand_id": brand_id,
            "name": "Local AI agents",
            "objective": "AUTHORITY",
            "topic": "Local AI agents will become an important alternative.",
            "primary_platform": "LINKEDIN",
            "routing_profile": "LOCAL_ONLY",
            "actor_type": "HUMAN",
            "actor_id": OWNER.id,
        },
        trigger="create",
        actor=OWNER,
        correlation_id="it-corr",
        brand_id=brand_id,
    )
    return campaign_id


def test_single_alembic_head(fresh_database: str) -> None:
    result = run_alembic(fresh_database, "heads")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("(head)") == 1


def test_models_match_migrations(fresh_database: str) -> None:
    result = run_alembic(fresh_database, "check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_extensions_installed(fresh_database: str) -> None:
    with psycopg.connect(_plain(fresh_database)) as conn:
        rows = conn.execute("SELECT extname FROM pg_extension").fetchall()
    assert {"vector", "citext"} <= {r[0] for r in rows}


async def test_create_and_transition_write_audit_rows(session: AsyncSession) -> None:
    brand_id = await _brand(session)
    campaign_id = await _campaign(session, brand_id)

    await apply_transition(
        session,
        machine=CAMPAIGN,
        table=CAMPAIGNS,
        audit_table=AUDIT,
        entity_id=campaign_id,
        from_state="DRAFT",
        trigger="start_planning",
        actor=OWNER,
        correlation_id="it-corr",
        brand_id=brand_id,
    )
    status = (
        await session.execute(select(CAMPAIGNS.c.status).where(CAMPAIGNS.c.id == campaign_id))
    ).scalar_one()
    assert str(status) in {"PLANNING", "CampaignStatus.PLANNING"}

    audit = (
        await session.execute(
            select(AUDIT.c.action, AUDIT.c.metadata)
            .where(AUDIT.c.entity_id == campaign_id)
            .order_by(AUDIT.c.occurred_at, AUDIT.c.id)
        )
    ).all()
    assert [a.action for a in audit] == ["status.transition", "status.transition"]
    assert audit[0].metadata["to"] == "DRAFT"
    assert audit[1].metadata == {
        "transition": "C2",
        "from": "DRAFT",
        "to": "PLANNING",
        "trigger": "start_planning",
        "reason": None,
    }


async def test_stale_from_state_is_conflict(session: AsyncSession) -> None:
    brand_id = await _brand(session)
    campaign_id = await _campaign(session, brand_id)
    kwargs = {
        "machine": CAMPAIGN,
        "table": CAMPAIGNS,
        "audit_table": AUDIT,
        "entity_id": campaign_id,
        "trigger": "start_planning",
        "actor": OWNER,
        "correlation_id": "it",
        "brand_id": brand_id,
    }
    await apply_transition(session, from_state="DRAFT", **kwargs)  # type: ignore[arg-type]
    with pytest.raises(OmniGrowthError) as exc:
        # A second caller still believes the row is DRAFT: compare-and-set must lose.
        await apply_transition(session, from_state="DRAFT", **kwargs)  # type: ignore[arg-type]
    assert exc.value.code is ErrorCode.CONFLICT


async def test_audit_events_are_append_only(session: AsyncSession) -> None:
    brand_id = await _brand(session)
    await _campaign(session, brand_id)
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(update(AUDIT).values(action="tampered"))


async def test_enum_rejects_unknown_status(session: AsyncSession) -> None:
    brand_id = await _brand(session)
    campaign_id = await _campaign(session, brand_id)
    with pytest.raises(DBAPIError):
        await session.execute(
            text("UPDATE campaigns SET status = 'NOT_A_STATE' WHERE id = :id"),
            {"id": campaign_id},
        )


def test_downgrade_to_base_and_upgrade_again(fresh_database: str) -> None:
    down = run_alembic(fresh_database, "downgrade", "base")
    assert down.returncode == 0, down.stderr[-2000:]
    up = run_alembic(fresh_database, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
