"""Credentials primitives (ADR-0006): argon2id passwords and opaque bearer tokens.

Tokens look like ``og_<43 url-safe chars>``; only their SHA-256 hex digest is stored
(``api_tokens.token_hash``) plus an 8-char prefix for identification in UIs/logs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from omnigrowth.core.errors import ErrorCode, OmniGrowthError

TOKEN_PREFIX = "og_"  # noqa: S105  # nosec B105 - public prefix, not a secret
MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()  # argon2id with library defaults (RFC 9106 profile)
# Pre-computed hash used to equalise timing when the user does not exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def validate_password_strength(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise OmniGrowthError(
            ErrorCode.VALIDATION_FAILURE,
            f"password must be at least {MIN_PASSWORD_LENGTH} characters",
        )


def hash_password(password: str) -> str:
    validate_password_strength(password)
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-work verification; a missing user still pays for one argon2 verify."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str  # shown to the caller exactly once
    token_prefix: str
    token_hash: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token() -> IssuedToken:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return IssuedToken(
        token=token,
        token_prefix=token[len(TOKEN_PREFIX) : len(TOKEN_PREFIX) + 8],
        token_hash=hash_token(token),
    )


def token_hashes_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
