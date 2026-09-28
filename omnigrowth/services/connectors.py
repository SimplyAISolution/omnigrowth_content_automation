"""Real connectors (Phase 13, A17): credentials, the live switch, live readiness, the
pre-post preview and platform-assisted reconciliation.

* **Credentials** are write-only (OWNER): envelope-encrypted (security/vault.py), one active
  per account and kind, rotation revokes the previous one in the same transaction, and every
  store / rotate / revoke is audited with a non-secret fingerprint, never the secret.
* **Live switch**: ``live_enabled`` (A6 gate #2) is turned on only by an OWNER typing the
  exact phrase ``ENABLE LIVE PUBLISHING FOR <PLATFORM>`` for an account whose platform has a
  VERIFIED connector, valid settings and a stored credential. Audited. Revoking the
  credential turns it off again.
* **Preview**: exactly what the dispatcher would send (account, text, media, visibility,
  timing, evidence class) and what would block it, without decrypting anything.
* **Lookup**: searches the platform for an uncertain post by its exact text. FOUND reconciles
  it (J12/P5 by SYSTEM, requested by the person); PARTIAL / NOT_FOUND change nothing,
  because absence is never proof (A3). It never re-posts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.enums import (
    ActorType,
    Environment,
    Platform,
    PublicationJobStatus,
    PublicationStatus,
    PublishMode,
)
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.settings import get_settings
from omnigrowth.core.state_machines import Actor
from omnigrowth.models import (
    ContentAsset,
    ContentVersion,
    PlatformAccount,
    PlatformCredential,
    Publication,
    PublicationJob,
)
from omnigrowth.observability.audit import record_audit
from omnigrowth.publishing import mastodon
from omnigrowth.publishing.base import LookupStatus, PublishError
from omnigrowth.publishing.mock import MockPublisher
from omnigrowth.publishing.registry import (
    LIVE_PLATFORMS,
    LiveFactory,
    LiveUnavailableError,
    active_credential,
    credential_context,
    live_publisher_for,
    mastodon_config,
)
from omnigrowth.publishing.scheduler import (
    asset_follows_publication,
    build_request,
    evidence_class,
    human_approved,
    transition_job,
    transition_publication,
)
from omnigrowth.schemas.publishing import (
    CredentialCreate,
    CredentialRead,
    PlatformAccountLiveRequest,
    PublicationLookupRead,
    PublicationPreviewRead,
    PublicationRead,
)
from omnigrowth.security import vault

REQUIRED_SCOPES: dict[Platform, tuple[str, ...]] = {Platform.MASTODON: mastodon.REQUIRED_SCOPES}
_LOOKUP_ACTOR = Actor(ActorType.SYSTEM, "platform-lookup")


def has_live_connector(platform: Platform) -> bool:
    """A VERIFIED live connector exists for this platform (publishing/registry.py)."""
    return platform in LIVE_PLATFORMS


def live_phrase(platform: Platform) -> str:
    return f"ENABLE LIVE PUBLISHING FOR {platform.value}"


def missing_scopes(granted: list[str], required: tuple[str, ...]) -> list[str]:
    """``write`` covers ``write:statuses`` (OAuth scope hierarchy); exact names otherwise."""
    return [
        need
        for need in required
        if not any(have == need or need.startswith(have + ":") for have in granted)
    ]


def _not_found(entity: str) -> OmniGrowthError:
    return OmniGrowthError(ErrorCode.NOT_FOUND, f"{entity} not found")


async def _account(
    session: AsyncSession, account_id: uuid.UUID, *, lock: bool = False
) -> PlatformAccount:
    account = await session.get(PlatformAccount, account_id, with_for_update=lock or None)
    if account is None or account.archived_at is not None:
        raise _not_found("platform account")
    return account


async def _open_uncertain(session: AsyncSession, account_id: uuid.UUID) -> bool:
    found = (
        await session.execute(
            select(Publication.id)
            .where(
                Publication.platform_account_id == account_id,
                Publication.status == PublicationStatus.PUBLICATION_UNCERTAIN,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


# ------------------------------------------------------------------ readiness
async def live_blockers(session: AsyncSession, account: PlatformAccount) -> list[str]:
    """Account-level reasons a live post would be refused now (the dispatcher re-checks
    everything, plus the per-job approval guards, at dispatch time)."""
    kind = LIVE_PLATFORMS.get(account.platform)
    if kind is None:
        return ["NO_VERIFIED_ADAPTER"]
    settings = get_settings()
    blockers: list[str] = []
    if settings.env is Environment.TEST:
        blockers.append("ENV_TEST")
    if settings.publish_mode is not PublishMode.LIVE:
        blockers.append("PUBLISH_MODE_NOT_LIVE")
    if not account.live_enabled:
        blockers.append("ACCOUNT_NOT_LIVE_ENABLED")
    try:
        mastodon_config(account)
    except LiveUnavailableError as exc:
        blockers.append(exc.reason)
    credential = await active_credential(session, account, kind)
    if credential is None:
        blockers.append("NO_CREDENTIAL")
    elif credential.expires_at is not None and credential.expires_at <= datetime.now(UTC):
        blockers.append("CREDENTIAL_EXPIRED")
    if await _open_uncertain(session, account.id):
        blockers.append("OPEN_UNCERTAIN")
    return blockers


async def has_credential(session: AsyncSession, account: PlatformAccount) -> bool:
    kind = LIVE_PLATFORMS.get(account.platform)
    return kind is not None and await active_credential(session, account, kind) is not None


# ------------------------------------------------------------------ credentials
async def list_credentials(session: AsyncSession, account_id: uuid.UUID) -> list[CredentialRead]:
    await _account(session, account_id)
    rows = (
        await session.execute(
            select(PlatformCredential)
            .where(PlatformCredential.platform_account_id == account_id)
            .order_by(PlatformCredential.created_at.desc())
        )
    ).scalars()
    return [CredentialRead.model_validate(r) for r in rows]


async def store_credential(
    session: AsyncSession, actor: Actor, account_id: uuid.UUID, body: CredentialCreate
) -> CredentialRead:
    account = await _account(session, account_id, lock=True)
    kind = LIVE_PLATFORMS.get(account.platform)
    if kind is None:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"{account.platform.value} has no verified connector; no credential is needed",
            details={"reason": "NO_VERIFIED_ADAPTER"},
        )
    if body.kind is not kind:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"{account.platform.value} uses a {kind.value} credential",
            details={"field": "kind", "expected": kind.value},
        )
    missing = missing_scopes(body.scopes, REQUIRED_SCOPES.get(account.platform, ()))
    if missing:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "the token must be granted these scopes",
            details={"field": "scopes", "missing": missing},
        )
    try:
        mastodon_config(account)  # the token is bound to the configured server
    except LiveUnavailableError as exc:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            "set the account's instance address before storing its token",
            details={"reason": exc.reason},
        ) from exc
    now = datetime.now(UTC)
    if body.expires_at is not None and body.expires_at <= now:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "the credential has already expired")
    secret = body.secret.get_secret_value()
    sealed = vault.seal(secret, account_id=account.id, kind=credential_context(account, kind))
    previous = await active_credential(session, account, kind)
    if previous is not None:
        _destroy(previous, actor, now)
        await session.flush([previous])  # the partial unique index allows one active row
    row = PlatformCredential(
        id=uuid7(),
        platform_account_id=account.id,
        kind=kind,
        enc_alg=sealed.enc_alg,
        ciphertext=sealed.ciphertext,
        nonce=sealed.nonce,
        wrapped_dek=sealed.wrapped_dek,
        kek_id=sealed.kek_id,
        scopes=body.scopes,
        expires_at=body.expires_at,
        fingerprint=vault.fingerprint(secret),
        created_by=actor.id,
    )
    session.add(row)
    await session.flush([row])
    await record_audit(
        session,
        actor=actor,
        action="credential.rotated" if previous is not None else "credential.stored",
        entity_type="platform_credentials",
        entity_id=row.id,
        brand_id=account.brand_id,
        metadata={
            "platform_account_id": str(account.id),
            "platform": account.platform.value,
            "kind": kind.value,
            "scopes": body.scopes,
            "fingerprint": row.fingerprint,
            "encryption_key_id": row.kek_id,
            "replaced_id": str(previous.id) if previous is not None else None,
        },
    )
    await session.commit()
    await session.refresh(row)
    return CredentialRead.model_validate(row)


def _destroy(row: PlatformCredential, actor: Actor, now: datetime) -> None:
    """Revoke and destroy the secret material; the row stays as metadata for the audit
    trail (secrets are not domain data, API_PLAN §2.14)."""
    row.revoked_at = now
    row.revoked_by = actor.id
    row.ciphertext = b""
    row.wrapped_dek = b""


async def destroy_credentials(
    session: AsyncSession, actor: Actor, account: PlatformAccount, *, reason: str
) -> int:
    """Revoke every active credential of the account (e.g. its server changed)."""
    rows = (
        (
            await session.execute(
                select(PlatformCredential).where(
                    PlatformCredential.platform_account_id == account.id,
                    PlatformCredential.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    for row in rows:
        _destroy(row, actor, now)
        await record_audit(
            session,
            actor=actor,
            action="credential.revoked",
            entity_type="platform_credentials",
            entity_id=row.id,
            brand_id=account.brand_id,
            metadata={
                "platform_account_id": str(account.id),
                "kind": row.kind.value,
                "fingerprint": row.fingerprint,
                "reason": reason,
            },
        )
    return len(rows)


async def revoke_credential(
    session: AsyncSession, actor: Actor, account_id: uuid.UUID, credential_id: uuid.UUID
) -> CredentialRead:
    account = await _account(session, account_id, lock=True)
    row = await session.get(PlatformCredential, credential_id, with_for_update=True)
    if row is None or row.platform_account_id != account.id:
        raise _not_found("credential")
    if row.revoked_at is None:
        _destroy(row, actor, datetime.now(UTC))
        await record_audit(
            session,
            actor=actor,
            action="credential.revoked",
            entity_type="platform_credentials",
            entity_id=row.id,
            brand_id=account.brand_id,
            metadata={
                "platform_account_id": str(account.id),
                "kind": row.kind.value,
                "fingerprint": row.fingerprint,
            },
        )
        if account.live_enabled:
            # No credential, no live publishing: switch it off explicitly (audited).
            account.live_enabled = False
            await record_audit(
                session,
                actor=actor,
                action="platform_account.live_enabled.set",
                entity_type="platform_accounts",
                entity_id=account.id,
                brand_id=account.brand_id,
                before={"live_enabled": True},
                after={"live_enabled": False},
                metadata={"reason": "credential revoked"},
            )
    await session.commit()
    await session.refresh(row)
    return CredentialRead.model_validate(row)


# ------------------------------------------------------------------ live switch
async def set_live(
    session: AsyncSession, actor: Actor, account_id: uuid.UUID, body: PlatformAccountLiveRequest
) -> PlatformAccount:
    account = await _account(session, account_id, lock=True)
    if body.enabled:
        expected = live_phrase(account.platform)
        if body.confirmation != expected:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                "type the exact confirmation phrase to enable live publishing",
                details={"field": "confirmation", "expected": expected},
            )
        blockers = [
            b
            for b in await live_blockers(session, account)
            # Settled elsewhere: the server's PUBLISH_MODE, this switch itself, and uncertain
            # posts (they hold every dispatch until reconciled). ENV=test is NOT settled
            # elsewhere here: live publishing can never be switched on in a test environment.
            if b
            not in (
                "PUBLISH_MODE_NOT_LIVE",
                "ACCOUNT_NOT_LIVE_ENABLED",
                "OPEN_UNCERTAIN",
            )
        ]
        if blockers:
            raise OmniGrowthError(
                ErrorCode.LIVE_PUBLISH_BLOCKED,
                "this account cannot publish live yet",
                details={"blockers": blockers},
            )
    before = account.live_enabled
    if before != body.enabled:
        account.live_enabled = body.enabled
        await record_audit(
            session,
            actor=actor,
            action="platform_account.live_enabled.set",
            entity_type="platform_accounts",
            entity_id=account.id,
            brand_id=account.brand_id,
            before={"live_enabled": before},
            after={"live_enabled": body.enabled},
            metadata={
                "platform": account.platform.value,
                "sandbox": account.sandbox,
                "confirmation": body.confirmation if body.enabled else None,
            },
        )
    await session.commit()
    await session.refresh(account)
    return account


# ------------------------------------------------------------------ preview
async def _job_rows(
    session: AsyncSession, job: PublicationJob
) -> tuple[ContentAsset, ContentVersion, PlatformAccount]:
    asset = await session.get(ContentAsset, job.content_asset_id)
    version = await session.get(ContentVersion, job.content_version_id)
    account = await session.get(PlatformAccount, job.platform_account_id)
    if asset is None or version is None or account is None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "publication job references missing rows")
    return asset, version, account


async def preview_job(session: AsyncSession, job_id: uuid.UUID) -> PublicationPreviewRead:
    job = await session.get(PublicationJob, job_id)
    if job is None:
        raise _not_found("publication job")
    asset, version, account = await _job_rows(session, job)
    request = build_request(job, asset, version, account.handle)
    live = get_settings().publish_mode is PublishMode.LIVE
    details: dict[str, Any]
    blockers: list[str] = []
    if account.brand_id != job.brand_id or asset.brand_id != job.brand_id:
        blockers.append("BRAND_MISMATCH")
    if live:
        blockers += await live_blockers(session, account)
        if asset.approved_version_id != job.content_version_id:
            blockers.append("VERSION_NOT_APPROVED")
        elif not await human_approved(session, asset.id, job.content_version_id):
            blockers.append("NOT_HUMAN_APPROVED")
        try:
            details = mastodon.preview_request(mastodon_config(account), request)
        except LiveUnavailableError as exc:
            details = {"publisher": None, "simulated": False, "unavailable": exc.reason}
    else:
        details = MockPublisher().preview(request)
    mode = PublishMode.LIVE if live else PublishMode.MOCK
    return PublicationPreviewRead(
        publication_job_id=job.id,
        brand_id=job.brand_id,
        platform=account.platform,
        platform_account_id=account.id,
        account_handle=account.handle,
        due_at=job.due_at,
        publish_mode=mode,
        evidence_class=evidence_class(mode, account),
        simulated=not live,
        content_version_id=job.content_version_id,
        details=details,
        blockers=sorted(set(blockers)),
    )


# ------------------------------------------------------------------ lookup
async def lookup_publication(
    session: AsyncSession,
    actor: Actor,
    publication_id: uuid.UUID,
    *,
    live_factory: LiveFactory = live_publisher_for,
) -> PublicationLookupRead:
    pub = await session.get(Publication, publication_id)
    if pub is None:
        raise _not_found("publication")
    if pub.status is not PublicationStatus.PUBLICATION_UNCERTAIN:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION, "only an uncertain publication can be looked up"
        )
    if pub.publish_mode is not PublishMode.LIVE:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "a simulated publication never reached a platform; reconcile it manually",
            details={"publish_mode": pub.publish_mode.value},
        )
    job = await session.get(PublicationJob, pub.publication_job_id)
    assert job is not None  # noqa: S101 - FK
    asset, version, account = await _job_rows(session, job)
    request = build_request(job, asset, version, account.handle)
    since = job.dispatch_started_at
    brand_id, job_id = pub.brand_id, job.id
    try:
        publisher = await live_factory(session, account)
    except LiveUnavailableError as exc:
        raise OmniGrowthError(
            ErrorCode.LIVE_PUBLISH_BLOCKED,
            "this account cannot reach its platform",
            details={"reason": exc.reason},
        ) from exc
    claimed = frozenset(
        str(x)
        for x in (
            await session.execute(
                select(Publication.external_post_id).where(
                    Publication.platform_account_id == account.id,
                    Publication.external_post_id.is_not(None),
                    Publication.id != publication_id,
                )
            )
        ).scalars()
    )
    await session.rollback()  # no transaction stays open during the platform calls
    try:
        outcome = await publisher.lookup(request, since=since, exclude_ids=claimed)
    except PublishError as exc:
        raise OmniGrowthError(
            exc.code, "the platform lookup failed", details={"reason": str(exc)}
        ) from exc
    await record_audit(
        session,
        actor=actor,
        action="publication.lookup",
        entity_type="publications",
        entity_id=publication_id,
        brand_id=brand_id,
        metadata={
            "status": outcome.status.value,
            "searched": outcome.searched,
            "found_post_ids": list(outcome.found_post_ids),
            "detail": outcome.detail,
        },
    )
    if outcome.status is LookupStatus.FOUND and outcome.result is not None:
        job = await session.get(
            PublicationJob, job_id, populate_existing=True, with_for_update=True
        )
        if job is not None and job.status is PublicationJobStatus.UNCERTAIN:
            now = datetime.now(UTC)
            reason = f"found on the platform (lookup requested by {actor.id})"
            await transition_job(
                session,
                job,
                "reconciled_found",
                _LOOKUP_ACTOR,
                values={"completed_at": now, "last_error_code": None, "last_error": None},
                reason=reason,
            )
            await transition_publication(
                session,
                job,
                "reconciled_found",
                _LOOKUP_ACTOR,
                expect=PublicationStatus.PUBLICATION_UNCERTAIN,
                values={
                    "external_post_id": outcome.result.external_post_id,
                    "canonical_url": outcome.result.canonical_url,
                    "published_at": outcome.result.published_at,
                    "reconciled_at": now,
                    "reconciled_by_actor_type": ActorType.SYSTEM,
                    "reconciled_by_actor_id": _LOOKUP_ACTOR.id,
                },
            )
            await asset_follows_publication(session, job, "publication_published", reason=reason)
    await session.commit()
    fresh = await session.get(Publication, publication_id, populate_existing=True)
    assert fresh is not None  # noqa: S101 - never deleted
    return PublicationLookupRead(
        status=outcome.status.value,
        searched=outcome.searched,
        found_post_ids=list(outcome.found_post_ids),
        detail=outcome.detail,
        publication=PublicationRead.model_validate(fresh),
    )
