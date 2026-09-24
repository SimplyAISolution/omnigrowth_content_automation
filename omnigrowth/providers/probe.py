"""Structured-output capability probe (A1) and the probe prompt's output schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProbeOutput(BaseModel):
    """Output schema of prompt ``system/structured_probe@v1``."""

    topic: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=3, max_length=3)
