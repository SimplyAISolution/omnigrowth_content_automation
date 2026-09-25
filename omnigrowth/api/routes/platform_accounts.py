"""``/api/v1/platform-accounts`` — per-brand platform accounts (never live on create, A6)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from omnigrowth.api.deps import OwnerDep, SessionDep, ViewerDep
from omnigrowth.core.enums import Platform
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.publishing import PlatformAccountCreate, PlatformAccountRead
from omnigrowth.services import publishing as publishing_service

router = APIRouter(prefix="/platform-accounts", tags=["platform-accounts"])


@router.get("", response_model=Page[PlatformAccountRead])
async def list_accounts(
    _principal: ViewerDep,
    session: SessionDep,
    brand_id: uuid.UUID | None = None,
    platform: Platform | None = None,
) -> Page[PlatformAccountRead]:
    items = await publishing_service.list_accounts(session, brand_id, platform)
    return Page[PlatformAccountRead](items=items)


@router.post("", response_model=PlatformAccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    body: PlatformAccountCreate, principal: OwnerDep, session: SessionDep
) -> PlatformAccountRead:
    return await publishing_service.create_account(session, principal.actor, body)


@router.get("/{account_id}", response_model=PlatformAccountRead)
async def get_account(
    account_id: uuid.UUID, _principal: ViewerDep, session: SessionDep
) -> PlatformAccountRead:
    return await publishing_service.get_account(session, account_id)
