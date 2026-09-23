"""Error envelope (API_PLAN §1.4, GLOSSARY ``ErrorResponse``)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from omnigrowth.core.errors import ErrorCode


class ErrorResponse(BaseModel):
    """Body of every non-2xx response. ``message`` is user-safe; ``details`` is masked."""

    code: ErrorCode
    message: str
    correlation_id: str
    details: dict[str, Any] | None = Field(default=None)
