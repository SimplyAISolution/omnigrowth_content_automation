"""``FakeProvider`` — deterministic replay for tests (A8).

Fixture mode: responses are loaded from
``<fixtures_dir>/<prompt_id>/v<version>/<input_hash>.json``::

    {"responses": [
        {"content": "...", "input_tokens": 12, "output_tokens": 30},
        {"error": "MODEL_TIMEOUT"}
    ]}

The n-th call for the same key returns the n-th entry (repair attempts / fallbacks).
A missing fixture or running past the last entry fails loudly — never fallback text.

Script mode (unit tests): ``FakeProvider(script=[...])`` returns ``ChatResult`` items or
raises exception items in order, regardless of key.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.base import ChatRequest, ChatResult


class FixtureMissingError(OmniGrowthError):
    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(
            ErrorCode.CONFIGURATION_ERROR,
            f"FakeProvider fixture missing: {detail}",
            details={"path": str(path)},
        )


class FakeProvider:
    def __init__(
        self,
        name: str = "fake",
        *,
        fixtures_dir: Path | None = None,
        script: Iterable[ChatResult | BaseException] | None = None,
        models: list[str] | None = None,
    ) -> None:
        if (fixtures_dir is None) == (script is None):
            raise ValueError("FakeProvider needs exactly one of fixtures_dir or script")
        self.name = name
        self._fixtures_dir = fixtures_dir
        self._script = list(script) if script is not None else None
        self._calls: dict[str, int] = defaultdict(int)
        self._models = models or ["fake-model"]
        self.requests: list[ChatRequest] = []

    async def aclose(self) -> None:
        return None

    async def list_models(self) -> list[str]:
        return list(self._models)

    async def chat(self, request: ChatRequest) -> ChatResult:
        self.requests.append(request)
        if self._script is not None:
            if not self._script:
                raise AssertionError("FakeProvider script exhausted")
            item = self._script.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return self._from_fixture(request)

    def _fixture_path(self, request: ChatRequest) -> Path:
        if self._fixtures_dir is None:
            raise ValueError("FakeProvider is in script mode")
        try:
            prompt_id = str(request.meta["prompt_id"])
            version = int(request.meta["prompt_version"])
            input_hash = str(request.meta["input_hash"])
        except KeyError as exc:
            raise FixtureMissingError(self._fixtures_dir, f"request meta lacks {exc}") from exc
        return self._fixtures_dir / prompt_id / f"v{version}" / f"{input_hash}.json"

    def _from_fixture(self, request: ChatRequest) -> ChatResult:
        path = self._fixture_path(request)
        if not path.exists():
            raise FixtureMissingError(path, str(path))
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        responses: list[dict[str, Any]] = data.get("responses", [])
        index = self._calls[str(path)]
        self._calls[str(path)] += 1
        if index >= len(responses):
            raise FixtureMissingError(path, f"no response #{index + 1} in {path.name}")
        entry = responses[index]
        if "error" in entry:
            raise OmniGrowthError(
                ErrorCode(entry["error"]), "fixture-injected provider error", retryable=True
            )
        return ChatResult(
            text=str(entry["content"]),
            input_tokens=entry.get("input_tokens"),
            output_tokens=entry.get("output_tokens"),
            latency_ms=int(entry.get("latency_ms", 1)),
        )
