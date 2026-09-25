"""Version diffing (spec §36): field-level changes plus a unified text diff of the body."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any, Protocol

DIFF_FIELDS = ("title", "hook", "body", "cta")


class VersionLike(Protocol):
    version_no: int
    title: str | None
    hook: str | None
    body: str
    cta: str | None
    platform_payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class VersionDiff:
    changes: list[FieldChange]
    unified: str


def diff_versions(before: VersionLike, after: VersionLike) -> VersionDiff:
    changes = [
        FieldChange(name, getattr(before, name), getattr(after, name))
        for name in DIFF_FIELDS
        if getattr(before, name) != getattr(after, name)
    ]
    tags_before = before.platform_payload.get("hashtags", [])
    tags_after = after.platform_payload.get("hashtags", [])
    if tags_before != tags_after:
        changes.append(FieldChange("hashtags", tags_before, tags_after))
    unified = "".join(
        difflib.unified_diff(
            before.body.splitlines(keepends=True),
            after.body.splitlines(keepends=True),
            fromfile=f"v{before.version_no}",
            tofile=f"v{after.version_no}",
        )
    )
    return VersionDiff(changes=changes, unified=unified)
