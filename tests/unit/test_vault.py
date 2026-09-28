"""Phase 13: envelope encryption of platform credentials (security/vault.py)."""

from __future__ import annotations

import base64
import dataclasses
import os
import uuid

import pytest

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import get_settings
from omnigrowth.security import vault

SECRET = "tok_" + "x7Qp" * 10  # test value only


def _kek() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")


@pytest.fixture
def kek(monkeypatch: pytest.MonkeyPatch) -> str:
    value = _kek()
    monkeypatch.setenv("OMNIGROWTH_CREDENTIAL_KEK", value)
    get_settings.cache_clear()
    yield value
    get_settings.cache_clear()


def test_round_trip_and_no_plaintext_at_rest(kek: str) -> None:
    account = uuid.uuid4()
    sealed = vault.seal(SECRET, account_id=account, kind="OAUTH2")
    assert SECRET.encode() not in sealed.ciphertext + sealed.wrapped_dek + sealed.nonce
    assert len(sealed.nonce) == 12 and sealed.enc_alg == "AES-256-GCM"
    assert sealed.kek_id.startswith("kek-") and kek not in sealed.kek_id
    opened = vault.open_secret(sealed, account_id=account, kind="OAUTH2")
    assert opened.get_secret_value() == SECRET
    assert SECRET not in repr(opened) and SECRET not in str(opened)
    # A fresh data key and nonce every time.
    again = vault.seal(SECRET, account_id=account, kind="OAUTH2")
    assert again.ciphertext != sealed.ciphertext and again.wrapped_dek != sealed.wrapped_dek


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "wrapped_dek"])
def test_tampering_fails_closed(kek: str, field: str) -> None:
    account = uuid.uuid4()
    sealed = vault.seal(SECRET, account_id=account, kind="OAUTH2")
    raw = bytearray(getattr(sealed, field))
    raw[-1] ^= 0x01
    broken = dataclasses.replace(sealed, **{field: bytes(raw)})
    with pytest.raises(OmniGrowthError) as info:
        vault.open_secret(broken, account_id=account, kind="OAUTH2")
    assert info.value.code is ErrorCode.CONFIGURATION_ERROR
    assert SECRET not in str(info.value)


def test_a_row_moved_to_another_account_or_kind_does_not_decrypt(kek: str) -> None:
    account = uuid.uuid4()
    sealed = vault.seal(SECRET, account_id=account, kind="OAUTH2")
    for other_account, kind in ((uuid.uuid4(), "OAUTH2"), (account, "API_KEY")):
        with pytest.raises(OmniGrowthError):
            vault.open_secret(sealed, account_id=other_account, kind=kind)


def test_another_kek_is_detected_before_decrypting(
    kek: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = uuid.uuid4()
    sealed = vault.seal(SECRET, account_id=account, kind="OAUTH2")
    monkeypatch.setenv("OMNIGROWTH_CREDENTIAL_KEK", _kek())
    get_settings.cache_clear()
    with pytest.raises(OmniGrowthError) as info:
        vault.open_secret(sealed, account_id=account, kind="OAUTH2")
    assert "different OMNIGROWTH_CREDENTIAL_KEK" in info.value.message


@pytest.mark.parametrize(
    "value", ["", "not base64 !!", base64.urlsafe_b64encode(b"short").decode()]
)
def test_missing_or_malformed_kek_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("OMNIGROWTH_CREDENTIAL_KEK", value)
    get_settings.cache_clear()
    try:
        with pytest.raises(OmniGrowthError) as info:
            vault.seal(SECRET, account_id=uuid.uuid4(), kind="OAUTH2")
        assert info.value.code is ErrorCode.CONFIGURATION_ERROR
    finally:
        get_settings.cache_clear()


def test_fingerprint_identifies_without_revealing() -> None:
    fp = vault.fingerprint(SECRET)
    assert len(fp) == 8 and fp == vault.fingerprint(SECRET)
    assert fp not in SECRET and fp != vault.fingerprint(SECRET + "y")
