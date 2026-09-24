"""Integration: syncing the prompt registry into prompt_versions (DATA_MODEL §6.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.models import PromptVersion
from omnigrowth.prompts import PromptRegistry
from omnigrowth.storage.prompt_versions import get_prompt_version_id, sync_prompt_versions

pytestmark = pytest.mark.integration

PROMPT = """---
id: itest/greet
version: 1
role: CHEAP_LOCAL
privacy_class: INTERNAL
description: integration test prompt
variables: [topic]
---
## system
{system}

## user
Topic: {{{{ topic }}}}
"""


def _tree(root: Path, system: str = "Be brief.") -> PromptRegistry:
    path = root / "itest" / "greet.v1.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(PROMPT.format(system=system), encoding="utf-8")
    return PromptRegistry.load(root)


async def test_sync_inserts_rows(session: AsyncSession, tmp_path: Path) -> None:
    registry = _tree(tmp_path)
    ids = await sync_prompt_versions(session, registry)
    assert set(ids) == {"itest/greet@v1"}

    row = (
        await session.execute(select(PromptVersion).where(PromptVersion.domain == "itest"))
    ).scalar_one()
    prompt = registry.get("itest/greet@v1")
    assert row.id == ids["itest/greet@v1"]
    assert (row.name, row.version) == ("greet", 1)
    assert row.content_hash == prompt.content_hash
    assert row.body == prompt.text
    assert row.front_matter["role"] == "CHEAP_LOCAL"
    assert row.front_matter["variables"] == ["topic"]
    assert await get_prompt_version_id(session, prompt) == row.id


async def test_sync_real_package_prompts(session: AsyncSession) -> None:
    ids = await sync_prompt_versions(session, PromptRegistry.load())
    assert "system/structured_probe@v1" in ids


async def test_second_sync_is_idempotent(session: AsyncSession, tmp_path: Path) -> None:
    registry = _tree(tmp_path)
    first = await sync_prompt_versions(session, registry)
    second = await sync_prompt_versions(session, PromptRegistry.load(tmp_path))
    assert first == second
    count = (
        await session.execute(select(PromptVersion.id).where(PromptVersion.domain == "itest"))
    ).all()
    assert len(count) == 1


async def test_changed_content_same_version_conflicts(
    session: AsyncSession, tmp_path: Path
) -> None:
    await sync_prompt_versions(session, _tree(tmp_path / "a"))
    edited = _tree(tmp_path / "b", system="Be verbose.")
    with pytest.raises(OmniGrowthError) as info:
        await sync_prompt_versions(session, edited)
    assert info.value.code is ErrorCode.CONFLICT
    assert "itest/greet@v1 changed after release; bump the version" in info.value.message
    with pytest.raises(OmniGrowthError) as info2:
        await get_prompt_version_id(session, edited.get("itest/greet@v1"))
    assert info2.value.code is ErrorCode.CONFLICT


async def test_get_prompt_version_id_absent(session: AsyncSession, tmp_path: Path) -> None:
    assert await get_prompt_version_id(session, _tree(tmp_path).get("itest/greet")) is None
