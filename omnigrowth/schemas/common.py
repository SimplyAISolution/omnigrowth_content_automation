"""Shared API schemas (API_PLAN §1): cursor pagination."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):  # noqa: UP046 - pydantic generic model
    """``Page[T]`` (API_PLAN §1.3). ``next_cursor = None`` means last page."""

    items: list[T]
    next_cursor: str | None = None
