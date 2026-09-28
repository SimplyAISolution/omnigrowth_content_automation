"""Live publisher registry (Phase 13, A17).

A platform gets a live publisher only when its adapter exists here AND its publish, auth
and rate-limit capabilities are VERIFIED in docs/PLATFORM_CAPABILITIES.md (AGENTS.md §6).
Everything else is refused with ``NO_VERIFIED_ADAPTER`` by the dispatcher's live guard.

``live_publisher_for`` builds one publisher per account: validated account settings plus
the account's own active credential, decrypted only here (vault) and held in memory for
the dispatch. Credentials are never shared across accounts, so a job can only ever post
with the credential of the account (and therefore brand) it was scheduled for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.content.platform_profiles import MastodonSettings
from omnigrowth.core.enums import CredentialKind, Platform
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.core.settings import get_settings
from omnigrowth.models import PlatformAccount, PlatformCredential
from omnigrowth.publishing.base import PlatformPublisher
from omnigrowth.publishing.config import cached_publishing_config
from omnigrowth.publishing.mastodon import MastodonAccountConfig, MastodonLivePublisher
from omnigrowth.security import vault
from omnigrowth.security.http import parse_host_allowlist

# Platforms with a VERIFIED live adapter, and the credential kind each one uses.
LIVE_PLATFORMS: dict[Platform, CredentialKind] = {Platform.MASTODON: CredentialKind.OAUTH2}


class LiveUnavailableError(Exception):
    """No live publisher for this account; ``reason`` is the live-guard refusal code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


LiveFactory = Callable[[AsyncSession, PlatformAccount], Awaitable[PlatformPublisher]]


def credential_context(account: PlatformAccount, kind: CredentialKind) -> str:
    """Associated data for the vault: the credential only decrypts for this kind AND the
    server it was stored for, so repointing the account at another host can never send the
    token there (A17)."""
    origin = str((account.settings or {}).get("instance_base_url") or "")
    return f"{kind.value}@{origin}"


def platform_allowlist() -> frozenset[str]:
    return parse_host_allowlist(
        get_settings().platform_host_allowlist, "OMNIGROWTH_PLATFORM_HOST_ALLOWLIST"
    )


async def active_credential(
    session: AsyncSession, account: PlatformAccount, kind: CredentialKind
) -> PlatformCredential | None:
    return (
        await session.execute(
            select(PlatformCredential).where(
                PlatformCredential.platform_account_id == account.id,
                PlatformCredential.kind == kind,
                PlatformCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()


def mastodon_config(account: PlatformAccount) -> MastodonAccountConfig:
    try:
        settings = MastodonSettings.model_validate(dict(account.settings or {}))
    except ValidationError as exc:
        raise LiveUnavailableError("ACCOUNT_SETTINGS_INVALID") from exc
    if not settings.instance_base_url:
        raise LiveUnavailableError("ACCOUNT_NOT_CONFIGURED")
    return MastodonAccountConfig(
        instance_base_url=settings.instance_base_url,
        visibility=settings.visibility,
        language=settings.language,
        max_characters=settings.max_characters or 500,
        characters_reserved_per_url=settings.characters_reserved_per_url,
    )


async def live_publisher_for(
    session: AsyncSession,
    account: PlatformAccount,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> PlatformPublisher:
    kind = LIVE_PLATFORMS.get(account.platform)
    if kind is None:
        raise LiveUnavailableError("NO_VERIFIED_ADAPTER")
    config = mastodon_config(account)
    credential = await active_credential(session, account, kind)
    if credential is None:
        raise LiveUnavailableError("NO_CREDENTIAL")
    if credential.expires_at is not None and credential.expires_at <= (now or datetime.now(UTC)):
        raise LiveUnavailableError("CREDENTIAL_EXPIRED")
    try:
        token = vault.open_secret(
            vault.Sealed(
                ciphertext=credential.ciphertext,
                nonce=credential.nonce,
                wrapped_dek=credential.wrapped_dek,
                kek_id=credential.kek_id,
                enc_alg=credential.enc_alg,
            ),
            account_id=account.id,
            kind=credential_context(account, kind),
        )
        allowlist = platform_allowlist()
    except OmniGrowthError as exc:
        raise LiveUnavailableError("CREDENTIAL_UNREADABLE") from exc
    timeout = cached_publishing_config(get_settings().config_dir).scheduler
    return MastodonLivePublisher(
        config,
        token,
        allowlist=allowlist,
        timeout_s=float(timeout.request_timeout_seconds),
        transport=transport,
    )
