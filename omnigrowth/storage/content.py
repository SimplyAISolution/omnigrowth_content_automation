"""Content persistence: assets, immutable versions, QA results, embeddings, approval actions.

Nothing here commits; the caller owns the transaction. Asset status changes go through the
CONTENT_ASSET machine only (AGENTS.md §4).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ApprovalActionType,
    ContentAssetStatus,
    ContentVersionOrigin,
    EmbeddingField,
    EmbeddingOwnerType,
)
from omnigrowth.core.ids import uuid7
from omnigrowth.core.state_machines import (
    CONTENT_ASSET,
    Actor,
    apply_transition,
    create_with_state,
)
from omnigrowth.models import (
    ApprovalAction,
    AuditEvent,
    ContentAsset,
    ContentVersion,
    Embedding,
    QaResult,
)

_ASSETS = cast(Table, ContentAsset.__table__)
_NOT_LIVE = (
    ContentAssetStatus.QA_FAILED,
    ContentAssetStatus.REJECTED,
    ContentAssetStatus.ARCHIVED,
)
_AUDIT = cast(Table, AuditEvent.__table__)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ assets
async def create_asset(
    session: AsyncSession,
    *,
    row: dict[str, Any],
    actor: Actor,
    correlation_id: str,
) -> uuid.UUID:
    """CA1 ``create`` -> DRAFT. A new asset is its own lineage root (no parent)."""
    asset_id = row.get("id") or uuid7()
    await create_with_state(
        session,
        machine=CONTENT_ASSET,
        table=_ASSETS,
        audit_table=_AUDIT,
        row={**row, "id": asset_id, "root_asset_id": asset_id, "lineage_depth": 0},
        trigger="create",
        actor=actor,
        correlation_id=correlation_id,
        brand_id=row["brand_id"],
    )
    return asset_id


async def transition_asset(
    session: AsyncSession,
    asset: ContentAsset,
    *,
    trigger: str,
    actor: Actor,
    correlation_id: str,
    from_state: str | None = None,
    values: Mapping[str, Any] | None = None,
    fence: Mapping[str, Any] | None = None,
    reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> str:
    transition = await apply_transition(
        session,
        machine=CONTENT_ASSET,
        table=_ASSETS,
        audit_table=_AUDIT,
        entity_id=asset.id,
        from_state=from_state or asset.status,
        trigger=trigger,
        actor=actor,
        correlation_id=correlation_id,
        brand_id=asset.brand_id,
        values=values,
        fence=fence,
        reason=reason,
        extra_metadata=extra,
    )
    return transition.target


async def get_asset(
    session: AsyncSession, asset_id: uuid.UUID, *, lock: bool = False
) -> ContentAsset | None:
    stmt = select(ContentAsset).where(ContentAsset.id == asset_id)
    if lock:
        stmt = stmt.with_for_update()
    asset = (await session.execute(stmt)).scalar_one_or_none()
    if asset is not None:
        await session.refresh(asset)
    return asset


async def list_assets(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
    statuses: Sequence[ContentAssetStatus] = (),
    generation_request_id: uuid.UUID | None = None,
    limit: int = 200,
) -> list[ContentAsset]:
    stmt = select(ContentAsset).where(ContentAsset.archived_at.is_(None))
    if brand_id is not None:
        stmt = stmt.where(ContentAsset.brand_id == brand_id)
    if campaign_id is not None:
        stmt = stmt.where(ContentAsset.campaign_id == campaign_id)
    if statuses:
        stmt = stmt.where(ContentAsset.status.in_(statuses))
    if generation_request_id is not None:
        stmt = stmt.where(ContentAsset.generation_request_id == generation_request_id)
    rows = (await session.execute(stmt.order_by(ContentAsset.created_at).limit(limit))).scalars()
    return list(rows)


async def assets_by_plan_item(
    session: AsyncSession, campaign_id: uuid.UUID
) -> dict[uuid.UUID, ContentAsset]:
    rows = (
        await session.execute(
            select(ContentAsset).where(
                ContentAsset.campaign_id == campaign_id, ContentAsset.plan_item_id.is_not(None)
            )
        )
    ).scalars()
    return {r.plan_item_id: r for r in rows if r.plan_item_id is not None}


async def claim_for_generation(
    session: AsyncSession, asset_ids: Sequence[uuid.UUID], request_id: uuid.UUID
) -> None:
    """Point DRAFT assets at a new generation request (the old request's CA2 fence then
    discards any late result). Not a status change."""
    if asset_ids:
        await session.execute(
            update(ContentAsset)
            .where(
                ContentAsset.id.in_(asset_ids),
                ContentAsset.status == ContentAssetStatus.DRAFT,
            )
            .values(generation_request_id=request_id, updated_at=func.now())
        )


# ------------------------------------------------------------------ versions
@dataclass(frozen=True, slots=True)
class NewVersion:
    origin: ContentVersionOrigin
    hook: str | None
    body: str
    cta: str | None
    platform_payload: dict[str, Any]
    media_requirements: list[Any]
    author: Actor
    title: str | None = None
    hook_type: str | None = None
    cta_type: str | None = None
    based_on_version_id: uuid.UUID | None = None
    prompt_version_id: uuid.UUID | None = None
    model_run_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    brand_profile_id: uuid.UUID | None = None


async def add_version(session: AsyncSession, asset_id: uuid.UUID, new: NewVersion) -> uuid.UUID:
    """Insert the next immutable version (§36). ``version_no`` is per asset, gap-free."""
    number = (
        await session.execute(
            select(func.coalesce(func.max(ContentVersion.version_no), 0)).where(
                ContentVersion.content_asset_id == asset_id
            )
        )
    ).scalar_one()
    row = ContentVersion(
        id=uuid7(),
        content_asset_id=asset_id,
        version_no=number + 1,
        origin=new.origin,
        based_on_version_id=new.based_on_version_id,
        title=new.title,
        hook=new.hook,
        body=new.body,
        cta=new.cta,
        media_requirements=new.media_requirements,
        platform_payload=new.platform_payload,
        body_hash=sha256(new.body),
        word_count=len(new.body.split()),
        char_count=len(new.body),
        hook_type=new.hook_type,
        cta_type=new.cta_type,
        prompt_version_id=new.prompt_version_id,
        model_run_id=new.model_run_id,
        agent_run_id=new.agent_run_id,
        brand_profile_id=new.brand_profile_id,
        author_actor_type=new.author.type,
        author_actor_id=new.author.id,
    )
    session.add(row)
    await session.flush([row])
    return row.id


async def get_version(
    session: AsyncSession, asset_id: uuid.UUID, version_id: uuid.UUID
) -> ContentVersion | None:
    return (
        await session.execute(
            select(ContentVersion).where(
                ContentVersion.id == version_id, ContentVersion.content_asset_id == asset_id
            )
        )
    ).scalar_one_or_none()


async def list_versions(session: AsyncSession, asset_id: uuid.UUID) -> list[ContentVersion]:
    return list(
        (
            await session.execute(
                select(ContentVersion)
                .where(ContentVersion.content_asset_id == asset_id)
                .order_by(ContentVersion.version_no)
            )
        ).scalars()
    )


# ------------------------------------------------------------------ QA
async def save_qa_results(
    session: AsyncSession, version_id: uuid.UUID, rows: Sequence[dict[str, Any]]
) -> None:
    """One row per (version, stage); a re-run replaces that stage's result."""
    for row in rows:
        stmt = pg_insert(QaResult).values(
            id=uuid7(),
            content_version_id=version_id,
            stage=row["stage"],
            outcome=row["outcome"],
            score=row["score"],
            findings=row["findings"],
            checker=row["checker"],
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["content_version_id", "stage"],
                set_={
                    "outcome": stmt.excluded.outcome,
                    "score": stmt.excluded.score,
                    "findings": stmt.excluded.findings,
                    "checker": stmt.excluded.checker,
                    "created_at": func.now(),
                },
            )
        )


async def qa_results(session: AsyncSession, version_id: uuid.UUID) -> list[QaResult]:
    return list(
        (
            await session.execute(
                select(QaResult)
                .where(QaResult.content_version_id == version_id)
                .order_by(QaResult.stage)
            )
        ).scalars()
    )


# ------------------------------------------------------------------ embeddings (§10, A4)
async def save_embedding(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    version_id: uuid.UUID,
    field: EmbeddingField,
    model: str,
    vector: list[float],
    text: str,
) -> None:
    stmt = pg_insert(Embedding).values(
        id=uuid7(),
        brand_id=brand_id,
        owner_type=EmbeddingOwnerType.CONTENT_VERSION,
        owner_id=version_id,
        field=field,
        chunk_index=0,
        embedding_model=model,
        dim=len(vector),
        embedding=vector,
        content_hash=sha256(text),
    )
    await session.execute(
        stmt.on_conflict_do_nothing(constraint="uq_embeddings_owner_field_chunk_model")
    )


@dataclass(frozen=True, slots=True)
class Neighbour:
    content_asset_id: uuid.UUID
    content_version_id: uuid.UUID
    similarity: float


async def nearest_versions(
    session: AsyncSession,
    *,
    brand_id: uuid.UUID,
    model: str,
    vector: list[float],
    field: EmbeddingField,
    exclude_asset_id: uuid.UUID,
    limit: int,
) -> list[Neighbour]:
    """Cosine neighbours: the *current* version of the brand's other live assets.

    QA-failed, rejected and archived content is ignored — it was never going to be seen, so
    it cannot cause audience fatigue.
    """
    distance = Embedding.embedding.cosine_distance(vector)
    rows = (
        await session.execute(
            select(ContentAsset.id, ContentAsset.current_version_id, distance.label("d"))
            .join(ContentAsset, ContentAsset.current_version_id == Embedding.owner_id)
            .where(
                Embedding.brand_id == brand_id,
                Embedding.owner_type == EmbeddingOwnerType.CONTENT_VERSION,
                Embedding.field == field,
                Embedding.embedding_model == model,
                Embedding.dim == len(vector),
                ContentAsset.brand_id == brand_id,
                ContentAsset.id != exclude_asset_id,
                ContentAsset.archived_at.is_(None),
                ContentAsset.status.not_in(_NOT_LIVE),
            )
            .order_by(distance)
            .limit(limit)
        )
    ).all()
    return [Neighbour(a, v, 1.0 - float(d)) for a, v, d in rows]


async def version_vector(
    session: AsyncSession, version_id: uuid.UUID, field: EmbeddingField
) -> tuple[str, list[float]] | None:
    row = (
        await session.execute(
            select(Embedding.embedding_model, Embedding.embedding)
            .where(
                Embedding.owner_type == EmbeddingOwnerType.CONTENT_VERSION,
                Embedding.owner_id == version_id,
                Embedding.field == field,
            )
            .order_by(Embedding.created_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if row is None:
        return None
    return str(row[0]), [float(x) for x in row[1]]


# ------------------------------------------------------------------ approval actions (§37)
async def add_approval_action(
    session: AsyncSession,
    *,
    asset: ContentAsset,
    version_id: uuid.UUID,
    resulting_version_id: uuid.UUID | None,
    action: ApprovalActionType,
    from_status: str,
    to_status: str,
    actor: Actor,
    correlation_id: str,
    comment: str | None = None,
    scheduled_for: datetime | None = None,
    platform_account_id: uuid.UUID | None = None,
) -> None:
    session.add(
        ApprovalAction(
            scheduled_for=scheduled_for,
            platform_account_id=platform_account_id,
            id=uuid7(),
            brand_id=asset.brand_id,
            content_asset_id=asset.id,
            content_version_id=version_id,
            resulting_version_id=resulting_version_id,
            action=action,
            from_status=ContentAssetStatus(from_status),
            to_status=ContentAssetStatus(to_status),
            comment=comment,
            actor_type=actor.type,
            actor_id=actor.id,
            correlation_id=correlation_id,
        )
    )
    await session.flush()


async def approval_history(session: AsyncSession, asset_id: uuid.UUID) -> list[ApprovalAction]:
    return list(
        (
            await session.execute(
                select(ApprovalAction)
                .where(ApprovalAction.content_asset_id == asset_id)
                .order_by(ApprovalAction.created_at, ApprovalAction.id)
            )
        ).scalars()
    )
