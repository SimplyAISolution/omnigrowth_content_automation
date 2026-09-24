"""Integration: Phase 4 review fixes — login rate limit, job crash/reaper, queue-down retry."""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from omnigrowth.api.app import create_app
from omnigrowth.api.deps import get_job_queue, get_upload_store
from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.security.http import FetchResult
from omnigrowth.services.jobs import RecordingJobQueue
from omnigrowth.storage.db import get_engine
from omnigrowth.storage.uploads import UploadStore
from omnigrowth.workflows.tasks import execute_job, reap_stuck_jobs
from tests.integration.helpers import auth_headers, make_user

pytestmark = pytest.mark.integration


async def _brand(client: httpx.AsyncClient, headers: dict[str, str]) -> str:
    resp = await client.post(
        "/api/v1/brands",
        headers=headers,
        json={"slug": f"hard-{secrets.token_hex(3)}", "name": "Hardening", "display_name": "H"},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def test_login_is_rate_limited_per_ip(
    fresh_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OMNIGROWTH_RATE_LIMIT_LOGIN", "3/minute")
    from omnigrowth.core.settings import get_settings

    get_settings.cache_clear()
    app = create_app()
    email = await make_user(UserRole.VIEWER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        codes = [
            (
                await c.post(
                    "/api/v1/auth/login", json={"email": email, "password": "wrong password!!"}
                )
            ).status_code
            for _ in range(4)
        ]
        limited = await c.post("/api/v1/auth/login", json={"email": email, "password": "x" * 12})
    assert codes[:3] == [401, 401, 401]
    assert codes[3] == 429
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
    assert limited.json()["code"] == "RATE_LIMITED"
    await get_engine().dispose()


class _Env:
    client: httpx.AsyncClient
    queue: RecordingJobQueue
    store: UploadStore
    headers: dict[str, str]
    brand_id: str


@pytest.fixture
async def env(fresh_database: str, tmp_path: Path) -> AsyncIterator[_Env]:
    e = _Env()
    e.queue = RecordingJobQueue()
    e.store = UploadStore(tmp_path / "uploads")
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: e.queue
    app.dependency_overrides[get_upload_store] = lambda: e.store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        e.client = c
        e.headers = await auth_headers(c, UserRole.EDITOR)
        e.brand_id = await _brand(c, e.headers)
        yield e
    await get_engine().dispose()


async def _url_job(env: _Env, url: str = "https://site.example/a") -> str:
    resp = await env.client.post(
        "/api/v1/sources/url", headers=env.headers, json={"brand_id": env.brand_id, "url": url}
    )
    assert resp.status_code == 202, resp.text
    return str(resp.json()["id"])


async def test_unexpected_exception_fails_job_instead_of_leaving_it_running(env: _Env) -> None:
    job_id = await _url_job(env)

    async def crashing_fetch(url: str) -> FetchResult:
        raise ValueError("library blew up")

    status = await execute_job(uuid.UUID(job_id), store=env.store, fetcher=crashing_fetch)
    assert status is JobStatus.FAILED
    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job["status"] == "FAILED" and job["error"]["reason"] == "ValueError"
    source = (
        await env.client.get(f"/api/v1/sources/{job['subject_id']}", headers=env.headers)
    ).json()
    assert source["status"] == "FAILED"


async def test_job_error_hides_internal_addresses(env: _Env) -> None:
    job_id = await _url_job(env, "https://rebind.example/")

    async def blocked(url: str) -> FetchResult:
        raise OmniGrowthError(
            ErrorCode.SSRF_BLOCKED,
            "URL not allowed",
            details={"reason": "destination address not allowed", "address": "10.0.0.7"},
        )

    await execute_job(uuid.UUID(job_id), store=env.store, fetcher=blocked)
    job = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job["error_code"] == "SSRF_BLOCKED"
    assert "10.0.0.7" not in str(job)


async def test_reaper_fails_stuck_running_jobs(env: _Env) -> None:
    job_id = await _url_job(env)
    from omnigrowth.services import jobs as job_service
    from omnigrowth.storage.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        job = await job_service.get_job(session, uuid.UUID(job_id))
        await job_service.mark_running(session, job)  # a worker started ... and died
        await session.commit()
    reaped = await reap_stuck_jobs(now=datetime.now(UTC) + timedelta(hours=1))
    assert reaped >= 1
    job_json = (await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)).json()
    assert job_json["status"] == "FAILED" and job_json["error"]["reason"] == "STUCK"
    source = (
        await env.client.get(f"/api/v1/sources/{job_json['subject_id']}", headers=env.headers)
    ).json()
    assert source["status"] == "FAILED"


async def test_queue_down_cancels_job_and_source_can_be_retried(env: _Env) -> None:
    class DownQueue:
        def enqueue(self, queue_name: str, job_id: uuid.UUID) -> None:
            raise OmniGrowthError(ErrorCode.QUEUE_FAILURE, "redis down")

    env.client._transport.app.dependency_overrides[get_job_queue] = DownQueue  # type: ignore[attr-defined]
    resp = await env.client.post(
        "/api/v1/sources/url",
        headers=env.headers,
        json={"brand_id": env.brand_id, "url": "https://down.example/"},
    )
    assert resp.status_code == 503
    listing = (
        await env.client.get(
            "/api/v1/sources", headers=env.headers, params={"brand_id": env.brand_id}
        )
    ).json()
    failed = [s for s in listing["items"] if s["source_url"] == "https://down.example/"]
    assert failed and failed[0]["status"] == "FAILED"

    env.client._transport.app.dependency_overrides[get_job_queue] = lambda: env.queue  # type: ignore[attr-defined]
    retry = await env.client.post(f"/api/v1/sources/{failed[0]['id']}/retry", headers=env.headers)
    assert retry.status_code == 202, retry.text
    assert env.queue.enqueued[-1][1] == uuid.UUID(retry.json()["id"])
