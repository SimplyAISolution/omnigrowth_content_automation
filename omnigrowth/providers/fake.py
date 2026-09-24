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

Record mode (``record_from=<provider>``, used only by ``scripts/record_fixtures.py``): a
missing fixture is fetched from ``record_from`` and appended to the fixture file. Normal
tests never pass ``record_from``, so a missing fixture still fails loudly (A8).

``CannedProvider`` answers from hand-authored canonical responses, one file per prompt id
(``<canonical_dir>/<prompt_id with / -> __>.json``), so canonical fixtures can be recorded
deterministically without a model.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.providers.base import ChatProvider, ChatRequest, ChatResult


class FixtureMissingError(OmniGrowthError):
    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(
            ErrorCode.CONFIGURATION_ERROR,
            f"FakeProvider fixture missing: {detail}",
            details={"path": str(path)},
        )


def _write_fixture(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


class FakeProvider:
    def __init__(
        self,
        name: str = "fake",
        *,
        fixtures_dir: Path | None = None,
        script: Iterable[ChatResult | BaseException] | None = None,
        models: list[str] | None = None,
        record_from: ChatProvider | None = None,
    ) -> None:
        if (fixtures_dir is None) == (script is None):
            raise ValueError("FakeProvider needs exactly one of fixtures_dir or script")
        self.name = name
        self._fixtures_dir = fixtures_dir
        self._script = list(script) if script is not None else None
        self._calls: dict[str, int] = defaultdict(int)
        self._models = models or ["fake-model"]
        self._record_from = record_from
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
        return await self._from_fixture(request)

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

    async def _record(
        self, path: Path, request: ChatRequest, responses: list[dict[str, Any]]
    ) -> None:
        assert self._record_from is not None  # noqa: S101 - guarded by caller
        result = await self._record_from.chat(request)
        responses.append(
            {
                "content": result.text,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
        )
        body = json.dumps(
            {"prompt": request.meta.get("prompt_id"), "responses": responses},
            indent=2,
            ensure_ascii=False,
        )
        await asyncio.to_thread(_write_fixture, path, body + "\n")

    async def _from_fixture(self, request: ChatRequest) -> ChatResult:
        path = self._fixture_path(request)
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        responses: list[dict[str, Any]] = data.get("responses", [])
        index = self._calls[str(path)]
        self._calls[str(path)] += 1
        if index >= len(responses):
            if self._record_from is None:
                raise FixtureMissingError(
                    path, f"no response #{index + 1} in {path}" if responses else str(path)
                )
            await self._record(path, request, responses)
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


class CannedProvider:
    """Replies with hand-authored canonical responses per prompt id (recording helper)."""

    name = "canned"

    def __init__(self, canonical_dir: Path) -> None:
        self._dir = canonical_dir
        self._calls: dict[str, int] = defaultdict(int)

    async def aclose(self) -> None:
        return None

    async def list_models(self) -> list[str]:
        return ["canned"]

    async def chat(self, request: ChatRequest) -> ChatResult:
        prompt_id = str(request.meta.get("prompt_id", ""))
        path = self._dir / (prompt_id.replace("/", "__") + ".json")
        if not path.exists():
            raise FixtureMissingError(path, f"no canonical response for {prompt_id}")
        responses: list[Any] = json.loads(path.read_text(encoding="utf-8"))["responses"]
        index = min(self._calls[prompt_id], len(responses) - 1)
        self._calls[prompt_id] += 1
        entry = responses[index]
        content = entry if isinstance(entry, str) else json.dumps(entry, ensure_ascii=False)
        return ChatResult(text=content, input_tokens=None, output_tokens=None, latency_ms=1)
