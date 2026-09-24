"""Provider interface shared by the OpenAI-compatible client and ``FakeProvider``."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from omnigrowth.core.errors import ErrorCode, OmniGrowthError


class StructuredMode(StrEnum):
    """How structured output is requested (A1): best supported first."""

    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_ONLY = "prompt_only"


@dataclass(frozen=True, slots=True)
class ChatRequest:
    model: str
    messages: list[dict[str, str]]
    temperature: float = 0.2
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    # Not sent to providers; lets FakeProvider find fixtures (A8).
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int


class UnsupportedResponseFormatError(OmniGrowthError):
    """The endpoint rejected ``response_format``; the caller downgrades the mode."""

    def __init__(self, mode: str, detail: str) -> None:
        super().__init__(
            ErrorCode.VALIDATION_FAILURE,
            f"response_format {mode} not supported",
            details={"mode": mode, "detail": detail},
        )
        self.mode = mode


class ChatProvider(Protocol):
    name: str

    async def chat(self, request: ChatRequest) -> ChatResult: ...

    async def list_models(self) -> list[str]: ...

    async def aclose(self) -> None: ...
