"""Health endpoint + service tests. All external checks are faked (no DB/Redis/LLM)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from omnigrowth.api.app import create_app
from omnigrowth.core.enums import HealthStatus
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import Settings
from omnigrowth.schemas.health import CheckResult
from omnigrowth.services import health as health_service

HEALTH_URL = "/api/v1/health"

OK = CheckResult(status=HealthStatus.OK, latency_ms=1.0)
DOWN = CheckResult(status=HealthStatus.DOWN, latency_ms=1.0, detail="ConnectionError")


def make_settings(**overrides: Any) -> Settings:
    # _env_file=None: a developer's local .env must not leak into unit tests.
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def patch_checks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database: CheckResult = OK,
    redis: CheckResult = OK,
    local_llm: CheckResult = OK,
) -> None:
    async def fake_db() -> CheckResult:
        return database

    async def fake_redis(_settings: Settings) -> CheckResult:
        return redis

    async def fake_llm(_settings: Settings) -> CheckResult:
        return local_llm

    monkeypatch.setattr(health_service, "check_database", fake_db)
    monkeypatch.setattr(health_service, "check_redis", fake_redis)
    monkeypatch.setattr(health_service, "check_local_llm", fake_llm)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ------------------------------------------------------------------ endpoint status
async def test_health_ok(monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient) -> None:
    patch_checks(monkeypatch)
    resp = await client.get(HEALTH_URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "OK"
    # Public contract (API_PLAN §2.1): component states only, no versions/hosts/detail.
    assert set(body) == {"status", "time", "checks"}
    assert body["checks"] == {"database": "OK", "redis": "OK", "local_llm": "OK"}


async def test_health_degraded_when_llm_down(
    monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    patch_checks(monkeypatch, local_llm=DOWN)
    resp = await client.get(HEALTH_URL)
    assert resp.status_code == 200
    assert resp.json()["status"] == "DEGRADED"
    assert resp.json()["checks"]["local_llm"] == "DOWN"


@pytest.mark.parametrize("which", ["database", "redis"])
async def test_health_down_returns_503(
    monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient, which: str
) -> None:
    patch_checks(monkeypatch, **{which: DOWN})
    resp = await client.get(HEALTH_URL)
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "DOWN"  # body is still HealthRead, not ErrorResponse
    assert body["checks"][which] == "DOWN"
    assert "ConnectionError" not in resp.text  # detail never leaks publicly


# ------------------------------------------------------------------ correlation id
async def test_correlation_id_echoed(
    monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient
) -> None:
    patch_checks(monkeypatch)
    resp = await client.get(HEALTH_URL, headers={"X-Correlation-ID": "abc-123_XYZ.9"})
    assert resp.headers["X-Correlation-ID"] == "abc-123_XYZ.9"
    assert resp.headers["X-Request-ID"]
    assert resp.headers["X-Request-ID"] != resp.headers["X-Correlation-ID"]


@pytest.mark.parametrize("inbound", [None, "short", "has space!!", "x" * 129, "bad/slash1"])
async def test_correlation_id_generated_when_missing_or_invalid(
    monkeypatch: pytest.MonkeyPatch, client: httpx.AsyncClient, inbound: str | None
) -> None:
    patch_checks(monkeypatch)
    headers = {"X-Correlation-ID": inbound} if inbound is not None else {}
    resp = await client.get(HEALTH_URL, headers=headers)
    generated = resp.headers["X-Correlation-ID"]
    assert generated != inbound
    assert len(generated) == 32 and all(c in "0123456789abcdef" for c in generated)
    assert resp.headers["X-Request-ID"]


# ------------------------------------------------------------------ aggregate logic
@pytest.mark.parametrize(
    ("db", "rd", "llm", "expected"),
    [
        (OK, OK, OK, HealthStatus.OK),
        (OK, OK, DOWN, HealthStatus.DEGRADED),
        (DOWN, OK, OK, HealthStatus.DOWN),
        (OK, DOWN, DOWN, HealthStatus.DOWN),
    ],
)
async def test_get_health_aggregate(
    monkeypatch: pytest.MonkeyPatch,
    db: CheckResult,
    rd: CheckResult,
    llm: CheckResult,
    expected: HealthStatus,
) -> None:
    patch_checks(monkeypatch, database=db, redis=rd, local_llm=llm)
    result = await health_service.get_health(make_settings())
    assert result.status is expected


# ------------------------------------------------------------------ URL resolution
def write_models_yaml(tmp_path: Path, body: str) -> Path:
    (tmp_path / "models.yaml").write_text(body, encoding="utf-8")
    return tmp_path


PRESETS_YAML = """
presets:
  lmstudio:
    base_url_host: "http://127.0.0.1:1234/v1"
  ollama:
    base_url_host: "http://127.0.0.1:11434/v1/"
"""


def test_env_url_overrides_preset(tmp_path: Path) -> None:
    settings = make_settings(
        OMNIGROWTH_LOCAL_LLM_URL="http://10.0.0.5:9999/v1/",
        OMNIGROWTH_CONFIG_DIR=write_models_yaml(tmp_path, PRESETS_YAML),
    )
    assert health_service.resolve_local_llm_url(settings) == "http://10.0.0.5:9999/v1"


def test_preset_used_without_env(tmp_path: Path) -> None:
    settings = make_settings(
        OMNIGROWTH_LOCAL_LLM_PRESET="ollama",
        OMNIGROWTH_CONFIG_DIR=write_models_yaml(tmp_path, PRESETS_YAML),
    )
    assert health_service.resolve_local_llm_url(settings) == "http://127.0.0.1:11434/v1"


def test_docker_runtime_uses_docker_preset(tmp_path: Path) -> None:
    cfg = write_models_yaml(
        tmp_path,
        PRESETS_YAML + '    base_url_docker: "http://host.docker.internal:11434/v1"\n',
    )
    settings = make_settings(
        OMNIGROWTH_CONFIG_DIR=str(cfg),
        OMNIGROWTH_LOCAL_LLM_PRESET="ollama",
        OMNIGROWTH_RUNTIME="docker",
    )
    assert health_service.resolve_local_llm_url(settings) == "http://host.docker.internal:11434/v1"


async def test_invalid_redis_url_is_down_not_500() -> None:
    settings = make_settings(REDIS_URL="not-a-redis-url")
    result = await health_service.check_redis(settings)
    assert result.status is HealthStatus.DOWN
    assert result.detail == "invalid REDIS_URL"


def test_repo_models_yaml_default_preset() -> None:
    settings = make_settings(OMNIGROWTH_CONFIG_DIR=Path(__file__).parents[2] / "config")
    assert health_service.resolve_local_llm_url(settings) == "http://127.0.0.1:1234/v1"


def test_unknown_preset_is_not_configured(tmp_path: Path) -> None:
    settings = make_settings(
        OMNIGROWTH_LOCAL_LLM_PRESET="nope",
        OMNIGROWTH_CONFIG_DIR=write_models_yaml(tmp_path, PRESETS_YAML),
    )
    assert health_service.resolve_local_llm_url(settings) is None


def test_missing_models_yaml_is_not_configured(tmp_path: Path) -> None:
    settings = make_settings(OMNIGROWTH_CONFIG_DIR=tmp_path)
    assert health_service.resolve_local_llm_url(settings) is None


async def test_llm_not_configured_is_down(tmp_path: Path) -> None:
    settings = make_settings(OMNIGROWTH_LOCAL_LLM_PRESET="nope", OMNIGROWTH_CONFIG_DIR=tmp_path)
    result = await health_service.check_local_llm(settings)
    assert result.status is HealthStatus.DOWN
    assert result.detail == "not configured"


# ------------------------------------------------------------------ individual checks
def llm_transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_llm_ok_counts_models() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})

    settings = make_settings(OMNIGROWTH_LOCAL_LLM_URL="http://llm.local:1234/v1")
    result = await health_service.check_local_llm(settings, transport=llm_transport(handler))
    assert result.status is HealthStatus.OK
    assert result.detail == "2 models"
    assert seen == ["http://llm.local:1234/v1/models"]


async def test_llm_non_200_is_down() -> None:
    settings = make_settings(OMNIGROWTH_LOCAL_LLM_URL="http://llm.local:1234/v1")
    transport = llm_transport(lambda _r: httpx.Response(500))
    result = await health_service.check_local_llm(settings, transport=transport)
    assert result.status is HealthStatus.DOWN
    assert result.detail == "HTTP 500"


async def test_llm_connect_error_is_down_and_masked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    settings = make_settings(OMNIGROWTH_LOCAL_LLM_URL="http://user:hunter2@llm.local:1234/v1")
    result = await health_service.check_local_llm(settings, transport=llm_transport(handler))
    assert result.status is HealthStatus.DOWN
    assert result.detail == "ConnectError"
    assert "hunter2" not in result.model_dump_json()


async def test_database_failure_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_ping() -> None:
        raise OmniGrowthError(
            ErrorCode.DATABASE_FAILURE,
            "database unreachable",
            details={"error": "OperationalError"},
        )

    monkeypatch.setattr(health_service, "ping_database", failing_ping)
    result = await health_service.check_database()
    assert result.status is HealthStatus.DOWN
    assert result.detail == "OperationalError"


async def test_database_unexpected_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_ping() -> None:
        raise OmniGrowthError(ErrorCode.INTERNAL_ERROR, "boom")

    monkeypatch.setattr(health_service, "ping_database", failing_ping)
    with pytest.raises(OmniGrowthError):
        await health_service.check_database()


async def test_database_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok_ping() -> None:
        return None

    monkeypatch.setattr(health_service, "ping_database", ok_ping)
    result = await health_service.check_database()
    assert result.status is HealthStatus.OK
    assert result.latency_ms is not None


class FakeRedis:
    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.closed = False

    async def ping(self) -> bool:
        if self.error is not None:
            raise self.error
        return True

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("error", "expected"),
    [(None, HealthStatus.OK), (RedisConnectionError("refused"), HealthStatus.DOWN)],
)
async def test_redis_check(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None, expected: HealthStatus
) -> None:
    fake = FakeRedis(error)
    monkeypatch.setattr(health_service.redis_asyncio, "from_url", lambda *_a, **_k: fake)
    result = await health_service.check_redis(make_settings())
    assert result.status is expected
    assert fake.closed
    if error is not None:
        assert result.detail == "ConnectionError"
