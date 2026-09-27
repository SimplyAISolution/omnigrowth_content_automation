"""Phase 10 gate: the repurposing graph (§9) with cycle prevention.

- A derivative knows its source (parent, root, depth, source version) and is written from
  that version by the target platform's writer, then goes through QA as usual.
- Creating a cycle fails: the database rejects any change of lineage and any child whose
  root/depth do not follow from its parent; the API refuses (409) a derivative that repeats
  the platform/format of its source or any ancestor, a duplicate sibling, and a chain
  deeper than 5.
All LLM calls replay recorded fixtures (A8).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from omnigrowth.agents.base import AgentDeps
from omnigrowth.core.enums import JobStatus, UserRole
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.prompts.registry import PromptRegistry
from omnigrowth.providers.fake import CannedProvider, FakeProvider
from omnigrowth.storage.db import get_engine
from omnigrowth.workflows.tasks import execute_job
from tests.integration.campaign_support import (
    CANONICAL,
    FIXTURES,
    TZ,
    Env,
    _router,
    approve_all,
    generated_campaign,
)
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration


@pytest.fixture
def env(campaign_env: Env) -> Env:
    return campaign_env


async def _get(env: Env, path: str, **params: Any) -> Any:
    resp = await env.client.get(f"/api/v1{path}", headers=env.headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _repurpose(env: Env, asset_id: str, platform: str, fmt: str, **extra: Any) -> Any:
    return await env.client.post(
        f"/api/v1/content/{asset_id}/repurpose",
        headers={**env.headers, **extra.pop("headers", {})},
        json={"target_platform": platform, "target_format": fmt, **extra},
    )


def _recording_deps() -> tuple[AgentDeps, FakeProvider]:
    """``fixture_deps`` whose provider stays reachable, to inspect what the writer saw."""
    record = os.environ.get("OMNIGROWTH_RECORD_FIXTURES") == "1"
    provider = FakeProvider(
        "lmstudio", fixtures_dir=FIXTURES, record_from=CannedProvider(CANONICAL) if record else None
    )
    return AgentDeps(router=_router(provider), registry=PromptRegistry.load()), provider


async def _derive(
    env: Env, asset_id: str, platform: str, fmt: str, *, seen: list[str] | None = None, **extra: Any
) -> dict[str, Any]:
    """Repurpose, run the generation job, return the new child asset."""
    resp = await _repurpose(env, asset_id, platform, fmt, **extra)
    assert resp.status_code == 202, resp.text
    job_id = uuid.UUID(resp.json()["id"])
    deps, provider = _recording_deps()
    assert await execute_job(job_id, agent_deps=deps) is JobStatus.SUCCEEDED
    if seen is not None:
        seen += [
            str(m.get("content", ""))
            for r in provider.requests
            for m in r.messages
            if m.get("role") == "user"
        ]
    children = (await _get(env, f"/content/{asset_id}/derivatives"))["items"]
    (child,) = [c for c in children if (c["platform"], c["format"]) == (platform, fmt)]
    return dict(child)


async def _root(env: Env) -> dict[str, Any]:
    campaign_id = await generated_campaign(env)
    approved = await approve_all(env, campaign_id)
    # Several LinkedIn posts are created in one transaction; pick by content, not row order,
    # so the writer's input (and its recorded fixture) is the same on every run.
    posts = [a for a in approved if (a["platform"], a["format"]) == ("LINKEDIN", "TEXT_POST")]
    return min(
        posts, key=lambda a: tuple(a["current_version"][k] or "" for k in ("hook", "body", "cta"))
    )


async def test_derivative_knows_its_source_and_is_written_from_it(env: Env) -> None:
    root = await _root(env)
    seen: list[str] = []
    child = await _derive(
        env, root["id"], "MASTODON", "THREAD", seen=seen, instructions="Keep it conversational."
    )
    assert child["parent_asset_id"] == root["id"]
    assert child["root_asset_id"] == root["id"]
    assert child["lineage_depth"] == 1
    assert child["source_version_id"] == root["approved_version_id"]
    assert child["repurpose_instructions"] == "Keep it conversational."
    assert child["plan_item_id"] is None and child["campaign_id"] == root["campaign_id"]
    assert child["status"] == "AWAITING_APPROVAL", child["qa"]
    version = child["current_version"]
    assert version["body"].count("\n---\n") == 2  # three Mastodon posts
    assert version["author_actor_id"] == "mastodon_writer"
    # The writer saw the source version (not the plan item alone) and the instructions.
    (prompt,) = seen
    source = await _get(env, f"/content/{root['id']}/versions/{root['approved_version_id']}")
    assert "REPURPOSE SOURCE: an existing LINKEDIN TEXT_POST" in prompt
    assert source["hook"] in prompt
    assert "REVISION NOTES\nKeep it conversational." in prompt
    assert "Platform: MASTODON\nFormat: THREAD" in prompt


async def test_cycles_and_recursive_duplicates_are_refused(env: Env) -> None:
    root = await _root(env)
    mastodon = await _derive(env, root["id"], "MASTODON", "THREAD")
    facebook = await _derive(env, mastodon["id"], "FACEBOOK", "TEXT_POST")
    assert facebook["lineage_depth"] == 2 and facebook["root_asset_id"] == root["id"]

    # Back to the root's form two levels up, or to the parent's form: a recursive duplicate.
    for asset, platform, fmt, conflicting in (
        (facebook, "LINKEDIN", "TEXT_POST", root["id"]),
        (facebook, "MASTODON", "THREAD", mastodon["id"]),
        (facebook, "FACEBOOK", "TEXT_POST", facebook["id"]),  # same as the source itself
    ):
        resp = await _repurpose(env, asset["id"], platform, fmt)
        assert resp.status_code == 409, resp.text
        error = resp.json()
        assert error["code"] == "CONFLICT"
        assert error["details"]["reason"] == "RECURSIVE_DUPLICATE"
        assert error["details"]["conflicting_asset_id"] == conflicting

    # A second derivative with the same platform/format from the same source.
    again = await _repurpose(env, root["id"], "MASTODON", "THREAD")
    assert again.status_code == 409, again.text
    assert again.json()["details"] == {
        "reason": "DUPLICATE_DERIVATIVE",
        "existing_asset_id": mastodon["id"],
    }
    # The same platform/format elsewhere in the tree (another branch) is refused too.
    sibling = await _derive(env, root["id"], "SUBSTACK", "NEWSLETTER")
    branch = await _repurpose(env, sibling["id"], "MASTODON", "THREAD")
    assert branch.status_code == 409, branch.text
    assert branch.json()["details"] == {
        "reason": "DUPLICATE_DERIVATIVE",
        "existing_asset_id": mastodon["id"],
    }
    # Nothing was created by the refused requests.
    assert len((await _get(env, f"/content/{facebook['id']}/derivatives"))["items"]) == 0
    assert len((await _get(env, f"/content/{root['id']}/derivatives"))["items"]) == 2
    assert (await _get(env, f"/content/{sibling['id']}/derivatives"))["items"] == []


async def test_database_rejects_any_cycle_or_inconsistent_lineage(env: Env) -> None:
    root = await _root(env)
    child = await _derive(env, root["id"], "MASTODON", "THREAD")
    statements = {
        # Re-parent the root under its own child: a cycle.
        "cycle": (
            "UPDATE content_assets SET parent_asset_id = :child, lineage_depth = 2, "
            "source_version_id = :child_version WHERE id = :root",
            "lineage is immutable",
        ),
        "self-parent": (
            "UPDATE content_assets SET parent_asset_id = id WHERE id = :child",
            "lineage is immutable",
        ),
        "move source": (
            "UPDATE content_assets SET source_version_id = NULL WHERE id = :child",
            "lineage is immutable",
        ),
        # A new row that claims the wrong depth / root for its parent.
        "bad depth": (
            "INSERT INTO content_assets (id, brand_id, campaign_id, parent_asset_id, "
            "root_asset_id, lineage_depth, source_version_id, platform, format) "
            "SELECT :new, brand_id, campaign_id, id, root_asset_id, 3, current_version_id, "
            "'REDDIT', 'DISCUSSION' FROM content_assets WHERE id = :child",
            "derivative lineage must follow its parent",
        ),
        "bad root": (
            "INSERT INTO content_assets (id, brand_id, campaign_id, parent_asset_id, "
            "root_asset_id, lineage_depth, source_version_id, platform, format) "
            "SELECT :new, brand_id, campaign_id, id, id, 2, current_version_id, "
            "'REDDIT', 'DISCUSSION' FROM content_assets WHERE id = :child",
            "derivative lineage must follow its parent",
        ),
        # The source version must belong to the parent.
        "foreign source": (
            "INSERT INTO content_assets (id, brand_id, campaign_id, parent_asset_id, "
            "root_asset_id, lineage_depth, source_version_id, platform, format) "
            "SELECT :new, brand_id, campaign_id, :child, root_asset_id, 2, :root_version, "
            "'REDDIT', 'DISCUSSION' FROM content_assets WHERE id = :child",
            "fk_content_assets_source_version",
        ),
        # A derivative must say which version it came from.
        "missing source": (
            "INSERT INTO content_assets (id, brand_id, campaign_id, parent_asset_id, "
            "root_asset_id, lineage_depth, source_version_id, platform, format) "
            "SELECT :new, brand_id, campaign_id, id, root_asset_id, 2, NULL, "
            "'REDDIT', 'DISCUSSION' FROM content_assets WHERE id = :child",
            "ck_content_assets_source_iff_parent",
        ),
        # Two live derivatives of one parent with the same platform/format.
        "duplicate sibling": (
            "INSERT INTO content_assets (id, brand_id, campaign_id, parent_asset_id, "
            "root_asset_id, lineage_depth, source_version_id, platform, format) "
            "SELECT :new, brand_id, campaign_id, parent_asset_id, root_asset_id, 1, "
            "source_version_id, platform, format FROM content_assets WHERE id = :child",
            "content_assets_one_derivative_idx",
        ),
    }
    params = {
        "root": root["id"],
        "child": child["id"],
        "child_version": child["current_version_id"],
        "root_version": root["approved_version_id"],
        "new": str(uuid.uuid4()),
    }
    for name, (sql, message) in statements.items():
        async with get_engine().connect() as conn:
            with pytest.raises(DBAPIError) as caught:
                await conn.execute(text(sql), params)
            await conn.rollback()
        assert message and message in str(caught.value), (name, str(caught.value)[:300])
    after = await _get(env, f"/content/{root['id']}")
    assert after["parent_asset_id"] is None and after["lineage_depth"] == 0


async def test_chain_depth_is_capped_at_five(env: Env) -> None:
    asset = await _root(env)
    chain = [
        ("MASTODON", "THREAD"),
        ("FACEBOOK", "TEXT_POST"),
        ("SUBSTACK", "NEWSLETTER"),
        ("RUMBLE", "SHORT"),
        ("REDDIT", "DISCUSSION"),
    ]
    for depth, (platform, fmt) in enumerate(chain, start=1):
        asset = await _derive(env, asset["id"], platform, fmt)
        assert asset["lineage_depth"] == depth
        assert asset["status"] == "AWAITING_APPROVAL", (platform, asset["qa"])
    resp = await _repurpose(env, asset["id"], "INSTAGRAM", "CAROUSEL")
    assert resp.status_code == 409, resp.text
    assert resp.json()["details"]["reason"] == "LINEAGE_TOO_DEEP"


async def test_repurpose_guards_and_idempotency(env: Env) -> None:
    root = await _root(env)
    bad_format = await _repurpose(env, root["id"], "INSTAGRAM", "THREAD")
    assert bad_format.status_code == 422, bad_format.text
    assert bad_format.json()["details"]["reason"] == "UNSUPPORTED_FORMAT"
    missing = await _repurpose(env, str(uuid.uuid4()), "MASTODON", "THREAD")
    assert missing.status_code == 404
    viewer = await auth_headers(env.client, UserRole.VIEWER)
    denied = await env.client.post(
        f"/api/v1/content/{root['id']}/repurpose",
        headers=viewer,
        json={"target_platform": "MASTODON", "target_format": "THREAD"},
    )
    assert denied.status_code == 403

    keyed = {"Idempotency-Key": "repurpose-once-0001"}
    first = await _repurpose(env, root["id"], "MASTODON", "THREAD", headers=keyed)
    replay = await _repurpose(env, root["id"], "MASTODON", "THREAD", headers=keyed)
    assert first.status_code == replay.status_code == 202
    assert replay.headers["Idempotent-Replayed"] == "true"
    assert replay.json()["id"] == first.json()["id"]
    (child,) = (await _get(env, f"/content/{root['id']}/derivatives"))["items"]
    assert child["status"] == "DRAFT"  # job not run yet

    # A DRAFT derivative has no QA-passed content to repurpose from.
    early = await _repurpose(env, child["id"], "FACEBOOK", "TEXT_POST")
    assert early.status_code == 409, early.text
    assert early.json()["details"]["failed_guard"] == "source_passed_qa"


async def test_platform_fields_are_edited_and_validated(env: Env) -> None:
    root = await _root(env)
    child = await _derive(env, root["id"], "MASTODON", "THREAD")
    version = child["current_version"]
    edit = await env.client.post(
        f"/api/v1/content/{child['id']}/versions",
        headers=env.headers,
        json={
            "based_on_version_id": version["id"],
            "body": version["body"],
            "platform_fields": {"content_warning": "AI and data privacy"},
        },
    )
    assert edit.status_code == 201, edit.text
    edited = edit.json()
    assert edited["platform_payload"]["content_warning"] == "AI and data privacy"
    assert edited["platform_payload"]["hashtags"] == version["platform_payload"]["hashtags"]
    bad = await env.client.post(
        f"/api/v1/content/{child['id']}/versions",
        headers=env.headers,
        json={
            "based_on_version_id": edited["id"],
            "body": version["body"],
            "platform_fields": {"subtitle": "not a Mastodon field"},
        },
    )
    assert bad.status_code == 422, bad.text


async def test_subreddit_profile_from_account_settings_feeds_writer_and_qa(env: Env) -> None:
    owner = await auth_headers(env.client, UserRole.OWNER)
    created = await env.client.post(
        "/api/v1/platform-accounts",
        headers=owner,
        json={
            "brand_id": env.brand_id,
            "platform": "REDDIT",
            "account_type": "PERSONAL",
            "handle": "simplyai-reddit",
            "timezone": TZ,
            "settings": {"subreddits": [{"subreddit": "r/LocalLLaMA"}]},
        },
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["settings"]["subreddits"][0]["subreddit"] == "LocalLLaMA"
    invalid = await env.client.patch(
        f"/api/v1/platform-accounts/{account['id']}",
        headers=owner,
        json={"settings": {"subreddits": [{"subreddit": "a", "unknown": True}]}},
    )
    assert invalid.status_code == 422, invalid.text
    banned = await env.client.patch(
        f"/api/v1/platform-accounts/{account['id']}",
        headers=owner,
        json={
            "settings": {
                "subreddits": [
                    {
                        "subreddit": "LocalLLaMA",
                        "banned_phrases": ["client drafting"],
                        "allowed_flairs": ["Discussion"],
                        "last_reviewed_at": "2026-09-20T00:00:00Z",
                    }
                ]
            }
        },
    )
    assert banned.status_code == 200, banned.text
    root = await _root(env)
    child = await _derive(env, root["id"], "REDDIT", "DISCUSSION")
    assert child["status"] == "QA_FAILED"
    platform_stage = next(r for r in child["qa"] if r["stage"] == "PLATFORM")
    assert {f["code"] for f in platform_stage["findings"]} >= {"SUBREDDIT_BANNED_PHRASE"}


async def test_unwritten_derivative_is_claimed_again_not_blocked(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review M1: a DRAFT derivative whose job failed or never ran is written by a retry."""
    root = await _root(env)

    def down(queue_name: str, job_id: uuid.UUID) -> None:
        raise OmniGrowthError(ErrorCode.QUEUE_FAILURE, "queue unavailable")

    with monkeypatch.context() as patched:
        patched.setattr(env.queue, "enqueue", down)
        failed = await _repurpose(env, root["id"], "MASTODON", "THREAD")
    assert failed.status_code == 503, failed.text
    (draft,) = (await _get(env, f"/content/{root['id']}/derivatives"))["items"]
    assert draft["status"] == "DRAFT"

    first = await _repurpose(env, root["id"], "MASTODON", "THREAD")
    assert first.status_code == 202, first.text
    second = await _repurpose(env, root["id"], "MASTODON", "THREAD", instructions="Shorter.")
    assert second.status_code == 202, second.text
    # The superseded job finds nothing to write (fenced on generation_request_id).
    stale = uuid.UUID(first.json()["id"])
    assert await execute_job(stale, agent_deps=_recording_deps()[0]) is JobStatus.SUCCEEDED
    assert (await _get(env, f"/content/{draft['id']}"))["status"] == "DRAFT"
    deps, _ = _recording_deps()
    assert await execute_job(uuid.UUID(second.json()["id"]), agent_deps=deps) is (
        JobStatus.SUCCEEDED
    )
    (child,) = (await _get(env, f"/content/{root['id']}/derivatives"))["items"]
    assert child["id"] == draft["id"] and child["status"] == "AWAITING_APPROVAL"
    assert child["repurpose_instructions"] == "Shorter."


async def test_race_on_the_unique_index_is_a_conflict(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If two requests pass the app check together, the unique index decides (409)."""
    from omnigrowth.storage import content as content_repo

    root = await _root(env)
    await _derive(env, root["id"], "MASTODON", "THREAD")

    async def nothing(*_args: object, **_kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(content_repo, "live_in_tree", nothing)
    resp = await _repurpose(env, root["id"], "MASTODON", "THREAD")
    assert resp.status_code == 409, resp.text
    assert resp.json()["details"] == {"reason": "DUPLICATE_DERIVATIVE"}
    assert len((await _get(env, f"/content/{root['id']}/derivatives"))["items"]) == 1


async def test_edited_hook_follows_thread_and_title(env: Env) -> None:
    """Review M2: after an edit, Mastodon's hook is the first post and Reddit's the title."""
    root = await _root(env)
    thread = await _derive(env, root["id"], "MASTODON", "THREAD")
    version = thread["current_version"]
    body = "A new opening post about local AI.\n\n---\n\nSecond post stays."
    edit = await env.client.post(
        f"/api/v1/content/{thread['id']}/versions",
        headers=env.headers,
        json={"based_on_version_id": version["id"], "body": body},
    )
    assert edit.status_code == 201, edit.text
    assert edit.json()["hook"] == "A new opening post about local AI."

    post = await _derive(env, root["id"], "REDDIT", "DISCUSSION")
    version = post["current_version"]
    edit = await env.client.post(
        f"/api/v1/content/{post['id']}/versions",
        headers=env.headers,
        json={
            "based_on_version_id": version["id"],
            "body": version["body"],
            "title": "A plainer title for the community",
        },
    )
    assert edit.status_code == 201, edit.text
    assert edit.json()["hook"] == edit.json()["title"] == "A plainer title for the community"


async def test_only_owners_change_account_settings(env: Env) -> None:
    owner = await auth_headers(env.client, UserRole.OWNER)
    created = await env.client.post(
        "/api/v1/platform-accounts",
        headers=owner,
        json={
            "brand_id": env.brand_id,
            "platform": "MASTODON",
            "account_type": "ORGANIZATION",
            "handle": "simplyai-mastodon",
            "timezone": TZ,
            "settings": {"max_characters": 800},
        },
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    for role in (UserRole.EDITOR, UserRole.VIEWER):
        denied = await env.client.patch(
            f"/api/v1/platform-accounts/{account_id}",
            headers=await auth_headers(env.client, role),
            json={"settings": {"max_characters": 400}},
        )
        assert denied.status_code == 403, (role, denied.text)
    wrong = await env.client.post(
        "/api/v1/platform-accounts",
        headers=owner,
        json={
            "brand_id": env.brand_id,
            "platform": "LINKEDIN",
            "account_type": "ORGANIZATION",
            "handle": "simplyai-li-settings",
            "settings": {"max_characters": 800},
        },
    )
    assert wrong.status_code == 422, wrong.text
