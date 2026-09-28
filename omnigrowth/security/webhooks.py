"""Webhook secrets and signatures (API_PLAN §6.6; Phase 12).

Secrets are DERIVED, never stored: ``HMAC-SHA256(OMNIGROWTH_WEBHOOK_KEY, "omnigrowth/webhook/
<kind>/<id>/<version>")``. A new version is a rotation; the previous version stays valid for
a grace period; revocation disables the row. A database dump alone reveals no secret. Changing
the server key invalidates every secret at once (documented as the emergency rotation).

Signature (both directions): ``X-OmniGrowth-Signature: v1=<hex HMAC-SHA256(secret,
"<timestamp>.<delivery id>.<raw body>")>`` with ``X-OmniGrowth-Timestamp`` (unix seconds) and
``X-OmniGrowth-Delivery-ID``. Signing the delivery id binds it to the body: a captured request
cannot be re-sent under a new id, and re-sending it under the same id only replays. During a
rotation grace period the header carries one ``v1=`` per active secret, comma-separated.
Receivers reject timestamps more than ``TOLERANCE_S`` away and deduplicate on the event id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import uuid
from collections.abc import Iterable
from typing import Literal

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import get_settings

TOLERANCE_S = 300
SIGNATURE_HEADER = "X-OmniGrowth-Signature"
TIMESTAMP_HEADER = "X-OmniGrowth-Timestamp"
DELIVERY_HEADER = "X-OmniGrowth-Delivery-ID"
EVENT_HEADER = "X-OmniGrowth-Event-ID"
EVENT_TYPE_HEADER = "X-OmniGrowth-Event-Type"
MIN_KEY_LENGTH = 32

Kind = Literal["endpoint", "subscription"]


def server_key() -> bytes:
    """The configured server key, or CONFIGURATION_ERROR (webhooks stay off without it)."""
    key = get_settings().webhook_key
    value = key.get_secret_value() if key is not None else ""
    if len(value) < MIN_KEY_LENGTH:
        raise OmniGrowthError(
            ErrorCode.CONFIGURATION_ERROR,
            "webhooks are not configured: set OMNIGROWTH_WEBHOOK_KEY (at least 32 characters)",
            details={"reason": "WEBHOOK_KEY_MISSING"},
        )
    return value.encode("utf-8")


def derive_secret(kind: Kind, row_id: uuid.UUID, version: int, key: bytes | None = None) -> str:
    digest = hmac.new(
        key if key is not None else server_key(),
        f"omnigrowth/webhook/{kind}/{row_id}/{version}".encode(),
        hashlib.sha256,
    ).digest()
    return "whsec_" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def signature(secret: str, timestamp: int, delivery_id: str, body: bytes) -> str:
    message = f"{timestamp}.{delivery_id}.".encode() + body
    return "v1=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def signature_header(secrets: Iterable[str], timestamp: int, delivery_id: str, body: bytes) -> str:
    """One signature per active secret (current first), so rotation has a grace period."""
    return ",".join(signature(s, timestamp, delivery_id, body) for s in secrets)


def verify(
    secrets: Iterable[str],
    timestamp_header: str | None,
    signature_header: str | None,
    delivery_id: str | None,
    body: bytes,
    now: float,
) -> bool:
    """True if the timestamp is fresh and the signature matches one of ``secrets``.

    Constant-time comparison for every candidate; never short-circuits on the first secret, so
    timing does not reveal which secret (current or previous) matched.
    """
    if not timestamp_header or not signature_header or not timestamp_header.isdigit():
        return False
    if not delivery_id or len(delivery_id) > 128:
        return False
    timestamp = int(timestamp_header)
    if abs(now - timestamp) > TOLERANCE_S:
        return False
    provided = [p.strip() for p in signature_header.split(",") if p.strip()][:4]
    matched = False
    for secret in secrets:
        expected = signature(secret, timestamp, delivery_id, body)
        for candidate in provided:
            matched |= hmac.compare_digest(expected, candidate)
    return matched


def fingerprint(secret: str) -> str:
    """Short non-secret identifier, safe to show and log (first 8 hex of sha256)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]
