"""Integration: source ingestion API — text (sync), upload + URL (async SOURCE_INGEST jobs)."""

from __future__ import annotations

import io
import secrets
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import docx
import httpx
import pytest

from omnigrowth.api.app import create_app
from omnigrowth.api.deps import get_job_queue, get_upload_store
from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.security.http import FetchResult
from omnigrowth.services.jobs import RecordingJobQueue
from omnigrowth.storage.uploads import UploadStore
from omnigrowth.workflows.tasks import execute_job
from tests.integration.helpers import IK_HOOKS, auth_headers

pytestmark = pytest.mark.integration

ARTICLE = (
    "# Why Local AI Agents Will Matter\n\n"
    "Local AI agents keep proprietary data on the machine.\n\n"
    "## Cost\n\nThey avoid per-token cloud fees for routine work.\n"
)


class Env:
    def __init__(self, client: httpx.AsyncClient, queue: RecordingJobQueue, store: UploadStore):
        self.client = client
        self.queue = queue
        self.store = store
        self.headers: dict[str, str] = {}
        self.brand_id = ""


@pytest.fixture
async def env(fresh_database: str, tmp_path: Path) -> AsyncIterator[Env]:
    queue = RecordingJobQueue()
    store = UploadStore(tmp_path / "uploads")
    app = create_app()
    app.dependency_overrides[get_job_queue] = lambda: queue
    app.dependency_overrides[get_upload_store] = lambda: store
    async with httpx.AsyncClient(
        event_hooks=IK_HOOKS, transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        e = Env(client, queue, store)
        e.headers = await auth_headers(client, UserRole.EDITOR)
        resp = await client.post(
            "/api/v1/brands",
            headers=e.headers,
            json={
                "slug": f"simply-ai-{secrets.token_hex(3)}",
                "name": "Simply AI Solutions",
                "display_name": "Simply AI Solutions",
            },
        )
        assert resp.status_code == 201, resp.text
        e.brand_id = resp.json()["id"]
        yield e
    from omnigrowth.storage import db

    if db.get_engine.cache_info().currsize:
        await db.get_engine().dispose()


async def _job(env: Env, job_id: str) -> dict[str, object]:
    resp = await env.client.get(f"/api/v1/jobs/{job_id}", headers=env.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()  # type: ignore[no-any-return]


# ------------------------------------------------------------------ text (sync)
async def test_paste_markdown_is_normalized_synchronously_and_deduped(env: Env) -> None:
    body = {"brand_id": env.brand_id, "source_type": "MARKDOWN", "content": ARTICLE}
    resp = await env.client.post("/api/v1/sources/text", headers=env.headers, json=body)
    assert resp.status_code == 201, resp.text
    source = resp.json()
    assert source["status"] == "NORMALIZED"
    assert source["title"] == "Why Local AI Agents Will Matter"
    content = await env.client.get(f"/api/v1/sources/{source['id']}/content", headers=env.headers)
    assert "proprietary data" in content.json()["content"]

    again = await env.client.post("/api/v1/sources/text", headers=env.headers, json=body)
    assert again.status_code == 200
    assert again.headers["X-Duplicate-Of"] == source["id"]


async def test_viewer_cannot_ingest(env: Env) -> None:
    viewer = await auth_headers(env.client, UserRole.VIEWER)
    resp = await env.client.post(
        "/api/v1/sources/text",
        headers=viewer,
        json={"brand_id": env.brand_id, "content": "hello"},
    )
    assert resp.status_code == 403


async def test_unknown_brand_is_404(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/sources/text",
        headers=env.headers,
        json={"brand_id": str(uuid.uuid4()), "content": "hello"},
    )
    assert resp.status_code == 404


# ------------------------------------------------------------------ upload (async job)
def _docx_bytes() -> bytes:
    document = docx.Document()
    document.add_heading("Local agents", level=1)
    document.add_paragraph("Private data stays local.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


async def test_docx_upload_runs_through_ingest_job(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/sources/upload",
        headers=env.headers,
        data={"brand_id": env.brand_id},
        files={
            "file": (
                "notes.docx",
                _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] == "QUEUED" and job["kind"] == "SOURCE_INGEST"
    assert env.queue.enqueued == [("ingest", uuid.UUID(job["id"]))]

    assert await execute_job(uuid.UUID(job["id"]), store=env.store) is JobStatus.SUCCEEDED
    done = await _job(env, job["id"])
    assert done["status"] == "SUCCEEDED"
    source = await env.client.get(f"/api/v1/sources/{job['subject_id']}", headers=env.headers)
    assert source.json()["status"] == "NORMALIZED"
    assert source.json()["metadata"]["filename"] == "notes.docx"

    # Re-running the same job is a no-op (idempotent).
    assert await execute_job(uuid.UUID(job["id"]), store=env.store) is JobStatus.SUCCEEDED


async def test_upload_with_lying_extension_is_rejected_before_storage(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/sources/upload",
        headers=env.headers,
        data={"brand_id": env.brand_id},
        files={"file": ("report.pdf", b"just some text, not a pdf", "application/pdf")},
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_FAILURE"
    assert env.queue.enqueued == []


async def test_corrupted_pdf_upload_fails_job_and_source(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/sources/upload",
        headers=env.headers,
        data={"brand_id": env.brand_id},
        files={"file": ("broken.pdf", b"%PDF-1.7\n" + secrets.token_bytes(200), "application/pdf")},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["id"]
    assert await execute_job(uuid.UUID(job_id), store=env.store) is JobStatus.FAILED
    job = await _job(env, job_id)
    assert job["error_code"] == "INGESTION_FAILURE"
    source = await env.client.get(f"/api/v1/sources/{job['subject_id']}", headers=env.headers)
    assert source.json()["status"] == "FAILED"


async def test_audio_upload_is_accepted_but_unsupported(env: Env) -> None:
    mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + secrets.token_bytes(512)
    resp = await env.client.post(
        "/api/v1/sources/upload",
        headers=env.headers,
        data={"brand_id": env.brand_id},
        files={"file": ("memo.mp3", mp3, "audio/mpeg")},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["id"]
    assert await execute_job(uuid.UUID(job_id), store=env.store) is JobStatus.FAILED
    job = await _job(env, job_id)
    source = await env.client.get(f"/api/v1/sources/{job['subject_id']}", headers=env.headers)
    assert source.json()["status"] == "UNSUPPORTED"


# ------------------------------------------------------------------ URL (async job)
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://[::ffff:127.0.0.1]/",
        "http://2130706433/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ],
)
async def test_url_precheck_blocks_ssrf_synchronously(env: Env, url: str) -> None:
    resp = await env.client.post(
        "/api/v1/sources/url", headers=env.headers, json={"brand_id": env.brand_id, "url": url}
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == "SSRF_BLOCKED"
    assert env.queue.enqueued == []


async def test_url_ingest_job_uses_guarded_fetcher(env: Env) -> None:
    resp = await env.client.post(
        "/api/v1/sources/url",
        headers=env.headers,
        json={"brand_id": env.brand_id, "url": "https://blog.example/local-agents"},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["id"]
    fetched: list[str] = []

    async def fake_fetch(url: str) -> FetchResult:
        fetched.append(url)
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body=b"<html><head><title>Local agents</title><script>x()</script></head>"
            b"<body><article><h1>Local agents</h1><p>Run on your hardware.</p></article>"
            b"</body></html>",
            resolved_ip="93.184.215.14",
        )

    status = await execute_job(uuid.UUID(job_id), store=env.store, fetcher=fake_fetch)
    assert status is JobStatus.SUCCEEDED
    assert fetched == ["https://blog.example/local-agents"]
    job = await _job(env, job_id)
    content = await env.client.get(
        f"/api/v1/sources/{job['subject_id']}/content", headers=env.headers
    )
    text = content.json()["content"]
    assert "Run on your hardware." in text and "x()" not in text


async def test_url_fetch_blocked_at_worker_marks_source_failed(env: Env) -> None:
    """DNS says private at fetch time (rebinding): the worker-side guard must fail the job."""
    resp = await env.client.post(
        "/api/v1/sources/url",
        headers=env.headers,
        json={"brand_id": env.brand_id, "url": "https://rebind.example/"},
    )
    job_id = resp.json()["id"]

    async def blocked_fetch(url: str) -> FetchResult:
        raise OmniGrowthError(
            ErrorCode.SSRF_BLOCKED, "URL not allowed", details={"reason": "private"}
        )

    assert (
        await execute_job(uuid.UUID(job_id), store=env.store, fetcher=blocked_fetch)
        is JobStatus.FAILED
    )
    job = await _job(env, job_id)
    assert job["error_code"] == "SSRF_BLOCKED"
