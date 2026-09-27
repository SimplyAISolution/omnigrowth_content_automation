"""``GET /api/v1/platforms`` (Phase 11): supported formats come from config/qa.yaml, every
platform has a writer, and publishing is reported as MOCK (simulated), never live."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from omnigrowth.content.qa import load_qa_config
from omnigrowth.core.enums import Platform, UserRole
from tests.integration.helpers import auth_headers

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]


async def test_catalog_matches_qa_config_and_reports_simulated_publishing(
    api_client: httpx.AsyncClient,
) -> None:
    denied = await api_client.get("/api/v1/platforms")
    assert denied.status_code == 401

    viewer = await auth_headers(api_client, UserRole.VIEWER)
    resp = await api_client.get("/api/v1/platforms", headers=viewer)
    assert resp.status_code == 200, resp.text
    items = {i["platform"]: i for i in resp.json()["items"]}
    assert set(items) == {p.value for p in Platform}
    rules = load_qa_config(REPO / "config").platforms
    for platform, item in items.items():
        assert item["has_writer"] is True
        assert item["formats"] == [f.value for f in rules[Platform(platform)].formats]
        assert item["capability_status"] == "UNVERIFIED"
        assert item["effective_publisher"] == "MOCK"
        assert item["live_publishing_available"] is False
    assert items["FACEBOOK"]["formats"] == ["TEXT_POST", "IMAGE_POST"]
    assert items["RUMBLE"]["formats"] == ["LONG_VIDEO_OUTLINE", "LONG_VIDEO_SCRIPT", "SHORT"]
    assert items["MASTODON"]["formats"] == ["TEXT_POST", "THREAD"]
    assert items["MASTODON"]["limits"]["max_chars_per_post"] == 500
