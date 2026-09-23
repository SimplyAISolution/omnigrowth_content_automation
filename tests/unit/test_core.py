"""Core: settings guards, secret masking (Phase 2 gate), logging, ids."""

from __future__ import annotations

import io
import json
import logging
import uuid

import pytest
from pydantic import ValidationError

from omnigrowth.core.enums import ContentAssetStatus, PublishMode, pg_enum_name
from omnigrowth.core.errors import ConfigurationError
from omnigrowth.core.ids import uuid7
from omnigrowth.core.logging import (
    ContextFilter,
    JsonFormatter,
    MaskingFilter,
    bind_context,
)
from omnigrowth.core.masking import REDACTED, mask_mapping, mask_text
from omnigrowth.core.settings import Settings

BASE_ENV = {
    "DATABASE_URL": "postgresql://og:pw@127.0.0.1:55432/og",
    "REDIS_URL": "redis://127.0.0.1:56379/0",
}


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in ("ENV", "PUBLISH_MODE", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in {**BASE_ENV, **env}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


# ------------------------------------------------------------------ settings
def test_live_publishing_is_impossible_in_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises((ConfigurationError, ValidationError)) as exc:
        _settings(monkeypatch, ENV="test", PUBLISH_MODE="live")
    assert "ENV=test" in str(exc.value)


def test_publish_mode_is_case_insensitive_and_defaults_to_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _settings(monkeypatch, ENV="dev").publish_mode is PublishMode.MOCK
    assert _settings(monkeypatch, ENV="dev", PUBLISH_MODE="manual").publish_mode is (
        PublishMode.MANUAL
    )


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("REDIS_URL", BASE_ENV["REDIS_URL"])
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_generic_sdk_keys_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used-000000000000")
    settings = _settings(monkeypatch, ENV="dev")
    assert settings.cloud_api_key is None


def test_secrets_do_not_leak_through_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, ENV="dev")
    assert "pw@" not in repr(settings)


# ------------------------------------------------------------------ masking (gate)
@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        ("postgresql://og:hunter2@db:5432/og", "hunter2"),
        ("Authorization: Bearer abcdef123456789", "abcdef123456789"),
        ("api_key=sk-1234567890abcdefghij", "sk-1234567890abcdefghij"),
        ('{"password": "s3cr3t!"}', "s3cr3t!"),
        ("token: og_abcdefghijklmnopqrstuv", "og_abcdefghijklmnopqrstuv"),
        ("key sk-ant-abcdefghijklmnopqrstuv in text", "sk-ant-abcdefghijklmnopqrstuv"),
        ("aws AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
        ("client_secret=xyz789&x=1", "xyz789"),
        ("redis://:s3cretpass@redis:6379/0", "s3cretpass"),
        ("Authorization: Token abcdef1234567", "abcdef1234567"),
        ('password="hello world"', "world"),
        ("password='two words'", "words"),
    ],
)
def test_mask_text_redacts(raw: str, leaked: str) -> None:
    masked = mask_text(raw)
    assert leaked not in masked
    assert "***" in masked


def test_mask_text_keeps_ordinary_text() -> None:
    text = "Local AI agents will matter more than chatbots."
    assert mask_text(text) == text


def test_mask_mapping_redacts_by_key_recursively() -> None:
    data = {
        "user": "alice",
        "password": "pw",
        "nested": {"api_key": "k", "note": "Bearer abcdefghijkl"},
        "items": [{"token": "t"}],
    }
    out = mask_mapping(data)
    assert out["user"] == "alice"
    assert out["password"] == REDACTED
    assert out["nested"]["api_key"] == REDACTED
    assert "abcdefghijkl" not in out["nested"]["note"]
    assert out["items"][0]["token"] == REDACTED


def test_log_records_are_masked_and_json() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.addFilter(MaskingFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("omnigrowth.test.masking")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with bind_context(correlation_id="corr-123", campaign_id="camp-9"):
        logger.info(
            "connecting to %s",
            "postgresql://og:hunter2@db/og",
            extra={"api_key": "sk-abcdefghijklmnopqrstuv", "payload": {"secret": "x"}},
        )
    record = json.loads(stream.getvalue())
    assert "hunter2" not in stream.getvalue()
    assert "sk-abcdefghijklmnopqrstuv" not in stream.getvalue()
    assert record["correlation_id"] == "corr-123"
    assert record["campaign_id"] == "camp-9"
    assert record["api_key"] == REDACTED
    assert record["payload"] == {"secret": REDACTED}


def test_plain_text_logging_masks_tracebacks() -> None:
    from omnigrowth.core.logging import MaskingTextFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(MaskingFilter())
    handler.setFormatter(MaskingTextFormatter("%(levelname)s %(message)s"))
    logger = logging.getLogger("omnigrowth.test.text")
    logger.handlers = [handler]
    logger.propagate = False
    try:
        raise RuntimeError("connect failed postgresql://u:hunter2secret@h/db")
    except RuntimeError:
        logger.exception("boom")
    assert "hunter2secret" not in stream.getvalue()
    assert "RuntimeError" in stream.getvalue()


def test_configure_logging_adopts_uvicorn_loggers() -> None:
    from omnigrowth.core.logging import configure_logging

    logging.getLogger("uvicorn.error").handlers = [logging.StreamHandler(io.StringIO())]
    configure_logging("INFO", json_output=True)
    assert logging.getLogger("uvicorn.error").handlers == []
    assert logging.getLogger("uvicorn.error").propagate is True
    assert logging.getLogger("uvicorn.access").disabled is True


def test_json_formatter_masks_non_string_extras() -> None:
    record = logging.makeLogRecord(
        {"msg": "x", "levelname": "INFO", "items": ["postgresql://u:pw123456@h/db"]}
    )
    out = JsonFormatter().format(record)
    assert "pw123456" not in out


def test_log_exception_text_is_masked() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(MaskingFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("omnigrowth.test.exc")
    logger.handlers = [handler]
    logger.propagate = False
    try:
        raise RuntimeError("failed connecting postgresql://og:hunter2@db/og")
    except RuntimeError:
        logger.exception("boom")
    assert "hunter2" not in stream.getvalue()


# ------------------------------------------------------------------ ids / enums
def test_uuid7_version_order_and_uniqueness() -> None:
    ids = [uuid7() for _ in range(200)]
    assert all(isinstance(i, uuid.UUID) and i.version == 7 for i in ids)
    assert all(i.variant == uuid.RFC_4122 for i in ids)
    assert len(set(ids)) == 200
    timestamps = [i.int >> 80 for i in ids]
    assert timestamps == sorted(timestamps)


def test_pg_enum_name_rule() -> None:
    assert pg_enum_name(ContentAssetStatus) == "content_asset_status"
