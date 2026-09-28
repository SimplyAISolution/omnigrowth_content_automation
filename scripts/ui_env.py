"""Throwaway environment for the frontend's browser tests and manual UI checks (Phase 11).

Nothing here touches a real LLM, a real platform or the dev database:

- ``prepare`` creates a new database ``og_ui_<hex>`` on the configured Postgres server, runs
  the migrations and seeds it through the real API with FIXTURE REPLAY (hand-written
  canonical answers from ``tests/fixtures/llm_canonical``, recorded into a temp dir): users
  with freshly generated test passwords, the "Simply AI Solutions" demo brand, mock platform
  accounts, one planned + generated campaign whose content waits for approval, and a second,
  fully published campaign (MockPublisher, simulated analytics, recommendations). The state
  (database name, test credentials) goes to ``.pytest-tmp/ui-env.json`` (git-ignored, outside
  every served directory).
- ``serve`` runs the API on 127.0.0.1:8012 against that database (ENV=test, PUBLISH_MODE=MOCK,
  Redis DB 15 so no running worker ever picks up its jobs).
- ``tick`` runs one scheduler pass with the MockPublisher at a far-future clock, so every
  scheduled post is "published" — simulated, no platform call, no audience.
- ``drop`` deletes the database and the state file.

    uv run python scripts/ui_env.py prepare|serve|tick|drop
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
# Outside frontend/ so no dev server can serve it; .pytest-tmp/ is git-ignored.
STATE = REPO / ".pytest-tmp" / "ui-env.json"
PORT = int(os.environ.get("OG_UI_API_PORT", "8012"))
DEMO_LABEL = "Demo data (fixture replay)"
# Per-process random key (the throwaway API derives webhook secrets from it); never persisted.
_THROWAWAY_WEBHOOK_KEY = secrets.token_urlsafe(48)
# Per-process credential KEK (Phase 13) so the UI can store a test token; never persisted.
_THROWAWAY_CREDENTIAL_KEK = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def _server_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
    if not url:
        raise SystemExit("DATABASE_URL is not configured (.env)")
    return url


def _redis_url() -> str:
    url = os.environ.get("REDIS_URL")
    if not url:
        for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("REDIS_URL="):
                url = line.split("=", 1)[1].strip()
    base = (url or "redis://127.0.0.1:56379/0").rsplit("/", 1)[0]
    return f"{base}/15"


def _with_db(url: str, name: str) -> str:
    from sqlalchemy.engine import make_url

    plain = url.replace("postgresql+psycopg://", "postgresql://", 1)
    return make_url(plain).set(database=name).render_as_string(hide_password=False)


def _env(db_url: str) -> dict[str, str]:
    return {
        **os.environ,
        "DATABASE_URL": db_url,
        "REDIS_URL": _redis_url(),
        "ENV": "test",
        "PUBLISH_MODE": "MOCK",
        "LOG_JSON": "false",
        # Throwaway environment only: a per-process limiter (no state left in Redis between
        # runs) with room for a browser suite that signs in several times. The limiter stays
        # on; production defaults (5/minute, 20/hour) are unchanged.
        "OMNIGROWTH_RATE_LIMIT_BACKEND": "memory",
        "OMNIGROWTH_RATE_LIMIT_LOGIN": "60/minute;600/hour",
        # Phase 12: webhooks on, with a key that exists only for this throwaway environment.
        "OMNIGROWTH_WEBHOOK_KEY": _THROWAWAY_WEBHOOK_KEY,
        "OMNIGROWTH_WEBHOOK_TARGET_ALLOWLIST": "127.0.0.1:5678",
        # Phase 13: credentials can be stored (encrypted with a throwaway key); publishing
        # stays MOCK, so nothing ever contacts a platform from this environment.
        "OMNIGROWTH_CREDENTIAL_KEK": _THROWAWAY_CREDENTIAL_KEK,
    }


# ------------------------------------------------------------------ prepare
def prepare() -> None:
    import psycopg

    if STATE.exists():
        drop()
    server = _server_url()
    name = f"og_ui_{secrets.token_hex(4)}"
    with psycopg.connect(_with_db(server, "postgres"), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    db_url = _with_db(server, name)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    # Written before anything can fail, so `drop` always finds the database to remove.
    STATE.write_text(json.dumps({"database": name, "api_port": PORT}, indent=2), encoding="utf-8")
    migrate = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO,
        env=_env(db_url),
        capture_output=True,
        text=True,
        check=False,
    )
    if migrate.returncode != 0:
        drop()
        raise SystemExit(migrate.stderr[-2000:])
    state: dict[str, Any] = {"database": name, "api_port": PORT}
    os.environ.update(_env(db_url))
    from omnigrowth.core.aio import run as aio_run

    try:
        seeded = aio_run(_seed)
    except BaseException:
        drop()
        raise
    state.update(seeded)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"prepared {name}; state in {STATE.relative_to(REPO)}")


async def _seed() -> dict[str, Any]:
    import httpx

    from omnigrowth.agents.base import AgentDeps
    from omnigrowth.api.app import create_app
    from omnigrowth.api.deps import get_job_queue
    from omnigrowth.core.enums import JobStatus, UserRole
    from omnigrowth.prompts.registry import PromptRegistry
    from omnigrowth.providers.fake import CannedProvider, FakeProvider
    from omnigrowth.publishing.config import load_publishing_config
    from omnigrowth.publishing.mock import MockPublisher
    from omnigrowth.publishing.scheduler import run_once
    from omnigrowth.security.auth import hash_password
    from omnigrowth.services.jobs import RecordingJobQueue
    from omnigrowth.storage import auth as auth_repo
    from omnigrowth.storage.db import get_engine, get_sessionmaker
    from omnigrowth.workflows.tasks import execute_job
    from tests.integration.campaign_support import ARTICLE, TZ, _router

    users: dict[str, dict[str, str]] = {}
    async with get_sessionmaker()() as session:
        for role in (UserRole.OWNER, UserRole.EDITOR, UserRole.VIEWER):
            email = f"{role.value.lower()}@omnigrowth.test"
            password = secrets.token_urlsafe(18)  # test-only, throwaway database
            await auth_repo.create_user(
                session,
                email=email,
                display_name=f"Demo {role.value.title()}",
                role=role,
                password_hash=hash_password(password),
            )
            users[role.value] = {"email": email, "password": password}
        await session.commit()

    replay_dir = Path(tempfile.mkdtemp(prefix="og-ui-fixtures-"))

    def deps() -> AgentDeps:
        provider = FakeProvider(
            "lmstudio",
            fixtures_dir=replay_dir,
            record_from=CannedProvider(REPO / "tests" / "fixtures" / "llm_canonical"),
        )
        return AgentDeps(router=_router(provider), registry=PromptRegistry.load())

    async def run(job_id: str) -> None:
        status = await execute_job(uuid.UUID(job_id), agent_deps=deps())
        if status is not JobStatus.SUCCEEDED:
            from omnigrowth.services.jobs import get_job

            async with get_sessionmaker()() as session:
                job = await get_job(session, uuid.UUID(job_id))
            raise SystemExit(
                f"seed job {job.kind} {job_id} ended {status}: {job.error_code} {job.error}"
            )

    app = create_app()
    recorded = RecordingJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: recorded
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://seed") as client:

        async def login(role: str) -> dict[str, str]:
            r = await client.post("/api/v1/auth/login", json=users[role])
            r.raise_for_status()
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        async def call(method: str, path: str, headers: dict[str, str], **kw: Any) -> Any:
            h = {**headers}
            if method == "POST":
                h["Idempotency-Key"] = uuid.uuid4().hex
            r = await client.request(method, f"/api/v1{path}", headers=h, **kw)
            if r.status_code >= 400:
                raise SystemExit(f"{method} {path}: {r.status_code} {r.text}")
            return r.json()

        owner, editor = await login("OWNER"), await login("EDITOR")
        brand = await call(
            "POST",
            "/brands",
            owner,
            json={
                "slug": "simply-ai-demo",
                "name": "Simply AI Solutions",
                "display_name": "Simply AI Solutions (demo)",
                "timezone": TZ,
                "profile": {
                    "niche": "Private, local-first AI for small businesses",
                    "differentiators": ["Runs on hardware you own"],
                    "content_pillars": ["Data privacy", "AI costs"],
                    "banned_phrases": ["game-changer"],
                },
            },
        )
        audience = await call(
            "POST",
            f"/brands/{brand['id']}/audiences",
            editor,
            json={"name": "Small-business owners", "is_primary": True, "pains": ["AI fees"]},
        )
        accounts: dict[str, str] = {}
        for platform in ("LINKEDIN", "X", "YOUTUBE", "INSTAGRAM", "REDDIT", "MASTODON"):
            acct = await call(
                "POST",
                "/platform-accounts",
                owner,
                json={
                    "brand_id": brand["id"],
                    "platform": platform,
                    "account_type": "ORGANIZATION",
                    "handle": f"simplyai-demo-{platform.lower()}",
                    "timezone": TZ,
                },
            )
            accounts[platform] = acct["id"]

        async def campaign(name: str, start: datetime) -> str:
            wizard = await call(
                "POST",
                "/campaigns/wizard",
                editor,
                json={
                    "brand_id": brand["id"],
                    "name": name,
                    "objective": "AUTHORITY",
                    "topic": "Local AI agents will become an important alternative to "
                    "fully cloud-hosted AI.",
                    "audience_id": audience["id"],
                    "primary_platform": "LINKEDIN",
                    "secondary_platforms": ["X", "YOUTUBE", "INSTAGRAM"],
                    "source_text": ARTICLE,
                    "source_title": "Why Local AI Agents Will Matter for Small Businesses",
                    "start_date": start.date().isoformat(),
                    "timezone": TZ,
                    "generate": True,
                },
            )
            await run(wizard["job"]["id"])
            cid = str(wizard["campaign"]["id"])
            await call("POST", f"/campaigns/{cid}/activate", editor)
            gen = await call("POST", f"/campaigns/{cid}/generate-assets", editor)
            await run(gen["id"])
            return cid

        now = datetime.now(UTC)
        # Campaign 1 — published end to end (simulated), with analytics + recommendations.
        done = await campaign("Local AI launch week (published, simulated)", now)
        queue = await call("GET", "/approvals", editor, params={"campaign_id": done})
        due = now + timedelta(minutes=5)
        for asset in queue["items"]:
            approved = await call(
                "POST",
                f"/approvals/{asset['id']}/actions",
                editor,
                json={"action": "APPROVE", "content_version_id": asset["current_version_id"]},
            )
            await call(
                "POST",
                f"/approvals/{asset['id']}/actions",
                editor,
                json={
                    "action": "SCHEDULE",
                    "content_version_id": approved["asset"]["approved_version_id"],
                    "platform_account_id": accounts[asset["platform"]],
                    "scheduled_at": due.isoformat(),
                },
            )
        await run_once(
            publisher=MockPublisher(),
            config=load_publishing_config(REPO / "config"),
            now=due + timedelta(seconds=1),
        )
        pull = await call("POST", "/analytics/pull", editor, json={"campaign_id": done})
        await run(pull["id"])
        recs = await call("POST", "/recommendations/generate", editor, json={"campaign_id": done})
        await run(recs["id"])

        # Campaign 2 — generated, waiting for human approval (the E2E journey).
        review = await campaign("Local AI follow-up (awaiting approval)", now + timedelta(days=7))
    await get_engine().dispose()
    return {
        "label": DEMO_LABEL,
        "users": users,
        "brand_id": brand["id"],
        "accounts": accounts,
        "campaigns": {"published": done, "review": review},
    }


# ------------------------------------------------------------------ serve / tick / drop
def _state() -> dict[str, Any]:
    if not STATE.exists():
        raise SystemExit("no UI environment: run `uv run python scripts/ui_env.py prepare`")
    data: dict[str, Any] = json.loads(STATE.read_text(encoding="utf-8"))
    return data


def serve() -> None:
    state = _state()
    # Runs in the foreground until stopped (Playwright's webServer or Ctrl+C).
    subprocess.run(  # noqa: S603 - fixed argv
        [
            sys.executable,
            "-m",
            "uvicorn",
            "omnigrowth.api.app:create_app",
            "--factory",
            "--loop",
            "omnigrowth.core.aio:loop_factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(state["api_port"]),
        ],
        cwd=REPO,
        env=_env(_with_db(_server_url(), state["database"])),
        check=False,
    )


def tick() -> None:
    state = _state()
    os.environ.update(_env(_with_db(_server_url(), state["database"])))

    async def once() -> None:
        from omnigrowth.publishing.config import load_publishing_config
        from omnigrowth.publishing.mock import MockPublisher
        from omnigrowth.publishing.scheduler import run_once
        from omnigrowth.storage.db import get_engine

        result = await run_once(
            publisher=MockPublisher(),
            config=load_publishing_config(REPO / "config"),
            now=datetime.now(UTC) + timedelta(days=60),
        )
        await get_engine().dispose()
        print(f"tick: claimed={result.claimed} succeeded={result.succeeded}")

    from omnigrowth.core.aio import run as aio_run

    aio_run(once)


def drop() -> None:
    import psycopg

    if not STATE.exists():
        return
    state = json.loads(STATE.read_text(encoding="utf-8"))
    with psycopg.connect(_with_db(_server_url(), "postgres"), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{state["database"]}" WITH (FORCE)')
    STATE.unlink()
    print(f"dropped {state['database']}")


if __name__ == "__main__":
    # Relative settings (config/, data/) resolve from the repository root, wherever this is
    # started from (npm runs it from frontend/).
    os.chdir(REPO)
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    actions = {"prepare": prepare, "serve": serve, "tick": tick, "drop": drop}
    if command not in actions:
        raise SystemExit(__doc__)
    actions[command]()
