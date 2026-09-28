"""``/api/v1/platform-accounts`` — per-brand platform accounts (never live on create, A6).

Phase 13 (A17): write-only credentials and the live switch, OWNER only and audited.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from omnigrowth.api.deps import OwnerDep, SessionDep, ViewerDep
from omnigrowth.core.enums import Platform
from omnigrowth.schemas.common import Page
from omnigrowth.schemas.publishing import (
    CredentialCreate,
    CredentialRead,
    PlatformAccountCreate,
    PlatformAccountLiveRequest,
    PlatformAccountRead,
    PlatformAccountUpdate,
)
from omnigrowth.services import connectors
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


@router.patch("/{account_id}", response_model=PlatformAccountRead)
async def update_account(
    account_id: uuid.UUID, body: PlatformAccountUpdate, principal: OwnerDep, session: SessionDep
) -> PlatformAccountRead:
    return await publishing_service.update_account(session, principal.actor, account_id, body)


# ------------------------------------------------------------------ credentials (A17)
@router.get("/{account_id}/credentials", response_model=Page[CredentialRead])
async def list_credentials(
    account_id: uuid.UUID, _principal: OwnerDep, session: SessionDep
) -> Page[CredentialRead]:
    """Metadata only (kind, scopes, fingerprint, dates); secrets are never returned."""
    return Page[CredentialRead](items=await connectors.list_credentials(session, account_id))


@router.post(
    "/{account_id}/credentials",
    response_model=CredentialRead,
    status_code=status.HTTP_201_CREATED,
)
async def store_credential(
    account_id: uuid.UUID, body: CredentialCreate, principal: OwnerDep, session: SessionDep
) -> CredentialRead:
    """Store (or rotate) the account's credential, encrypted at rest. Write-only."""
    return await connectors.store_credential(session, principal.actor, account_id, body)


@router.delete("/{account_id}/credentials/{credential_id}", response_model=CredentialRead)
async def revoke_credential(
    account_id: uuid.UUID, credential_id: uuid.UUID, principal: OwnerDep, session: SessionDep
) -> CredentialRead:
    """Revoke at once; a live account is switched off as well (audited)."""
    return await connectors.revoke_credential(session, principal.actor, account_id, credential_id)


@router.post("/{account_id}/live", response_model=PlatformAccountRead)
async def set_live(
    account_id: uuid.UUID,
    body: PlatformAccountLiveRequest,
    principal: OwnerDep,
    session: SessionDep,
) -> PlatformAccountRead:
    """A6 gate #2. Enabling needs the exact phrase ``ENABLE LIVE PUBLISHING FOR <PLATFORM>``."""
    await connectors.set_live(session, principal.actor, account_id, body)
    return await publishing_service.get_account(session, account_id)
