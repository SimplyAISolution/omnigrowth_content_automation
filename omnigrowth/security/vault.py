"""Envelope encryption for platform credentials (DATA_MODEL §3.8, A17; Phase 13).

* ``OMNIGROWTH_CREDENTIAL_KEK`` = base64url of 32 random bytes (the key-encryption key). It
  never touches the database; ``kek_id`` (a fingerprint) records which KEK wrapped a row.
* Each credential gets a fresh 32-byte data key (DEK). The secret is sealed with
  AES-256-GCM under the DEK; the DEK is sealed with AES-256-GCM under the KEK. Both use
  associated data binding the ciphertext to its account and kind, so a row copied onto
  another account or kind does not decrypt.
* Plaintext exists only inside ``open_secret`` callers (the publisher registry) and is never
  logged, audited or returned by any API.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import get_settings

ALGORITHM = "AES-256-GCM"
NONCE_BYTES = 12
KEY_BYTES = 32


@dataclass(frozen=True, slots=True)
class Sealed:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes  # nonce (12 bytes) + AES-GCM(KEK, DEK)
    kek_id: str
    enc_alg: str = ALGORITHM


def _config_error(message: str) -> OmniGrowthError:
    return OmniGrowthError(
        ErrorCode.CONFIGURATION_ERROR, message, details={"setting": "OMNIGROWTH_CREDENTIAL_KEK"}
    )


def _kek() -> bytes:
    raw = get_settings().credential_kek
    if raw is None or not raw.get_secret_value().strip():
        raise _config_error(
            "platform credentials need OMNIGROWTH_CREDENTIAL_KEK (base64url of 32 random bytes)"
        )
    text = raw.get_secret_value().strip()
    try:
        key = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise _config_error("OMNIGROWTH_CREDENTIAL_KEK is not valid base64url") from exc
    if len(key) != KEY_BYTES:
        raise _config_error("OMNIGROWTH_CREDENTIAL_KEK must decode to exactly 32 bytes")
    return key


def kek_id(key: bytes | None = None) -> str:
    """Stable, non-secret fingerprint of the KEK (first 12 hex chars of a domain hash)."""
    material = key if key is not None else _kek()
    return "kek-" + hashlib.sha256(b"omnigrowth/kek-id/" + material).hexdigest()[:12]


def _aad(account_id: uuid.UUID, kind: str) -> bytes:
    return f"omnigrowth/credential/{account_id}/{kind}".encode()


def seal(secret: str, *, account_id: uuid.UUID, kind: str) -> Sealed:
    if not secret:
        raise OmniGrowthError(ErrorCode.VALIDATION_FAILURE, "the credential is empty")
    key = _kek()
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_BYTES)
    aad = _aad(account_id, kind)
    ciphertext = AESGCM(dek).encrypt(nonce, secret.encode("utf-8"), aad)
    wrap_nonce = os.urandom(NONCE_BYTES)
    wrapped = wrap_nonce + AESGCM(key).encrypt(wrap_nonce, dek, b"omnigrowth/dek/" + aad)
    return Sealed(ciphertext=ciphertext, nonce=nonce, wrapped_dek=wrapped, kek_id=kek_id(key))


def open_secret(sealed: Sealed, *, account_id: uuid.UUID, kind: str) -> SecretStr:
    """Decrypt a stored credential. Any tampering, a different account/kind or another KEK
    fails closed with CONFIGURATION_ERROR (the credential must be entered again)."""
    key = _kek()
    if sealed.enc_alg != ALGORITHM:
        raise _config_error(f"unsupported credential algorithm {sealed.enc_alg}")
    if sealed.kek_id != kek_id(key):
        raise _config_error(
            "this credential was encrypted with a different OMNIGROWTH_CREDENTIAL_KEK; "
            "enter it again"
        )
    aad = _aad(account_id, kind)
    try:
        wrap_nonce, wrapped = sealed.wrapped_dek[:NONCE_BYTES], sealed.wrapped_dek[NONCE_BYTES:]
        dek = AESGCM(key).decrypt(wrap_nonce, wrapped, b"omnigrowth/dek/" + aad)
        plain = AESGCM(dek).decrypt(sealed.nonce, sealed.ciphertext, aad)
    except (InvalidTag, ValueError) as exc:
        raise _config_error("a stored credential could not be decrypted; enter it again") from exc
    return SecretStr(plain.decode("utf-8"))


def fingerprint(secret: str) -> str:
    """Last-resort identifier for humans ("which token is stored?"): 8 hex of a domain hash.
    Never reversible to the secret; never the secret's own characters."""
    return hashlib.sha256(b"omnigrowth/credential-fp/" + secret.encode()).hexdigest()[:8]
