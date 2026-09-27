"""Token counting for the context budget (docs/audit/safeguards_post_mvp.md §4).

Enforcement uses the SERVER's own tokenizer and chat template wherever one exists:

* ``LMStudioTokenCounter`` asks LM Studio (official ``lmstudio`` SDK, same server as the
  OpenAI-compatible endpoint) to render the request with the model's chat template
  (``apply_prompt_template`` — system/user/assistant markers, the ``<think>`` prefix) and to
  tokenize it with the loaded model's vocabulary, and reads the context length the model was
  actually LOADED with (not the configured value). The count was verified against the
  server's own ``usage.prompt_tokens`` (tests/live/test_token_budget_live.py).
* ``ApproximateTokenCounter`` is a character estimate (default 3 chars/token) for providers
  without a tokenizer facility (unit tests, a remote API). Every result it produces is
  labelled ``exact=False`` / ``counted_by="approximate"``; it is never used for a provider
  configured with ``budget_counting: lmstudio``.

The structured-output schema (``response_format``) is counted in addition to the rendered
prompt, as tokens of its compact JSON: whether a server injects it into the prompt or only
uses it as a sampling grammar varies, so it is counted conservatively.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from omnigrowth.core.errors import ErrorCode, OmniGrowthError


@dataclass(frozen=True, slots=True)
class TokenCount:
    prompt_tokens: int  # rendered chat template, tokenized
    schema_tokens: int  # response_format schema (counted conservatively)
    exact: bool
    counted_by: str
    loaded_context: int | None  # what the server loaded the model with, if known

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.schema_tokens


class TokenCounter(Protocol):
    async def count_chat(
        self, model: str, messages: Sequence[dict[str, Any]], response_format: Any | None
    ) -> TokenCount: ...

    async def count_text(self, model: str, text: str, *, embedding: bool) -> TokenCount: ...

    async def aclose(self) -> None: ...


def _schema_text(response_format: Any | None) -> str:
    return "" if response_format is None else json.dumps(response_format, separators=(",", ":"))


class ApproximateTokenCounter:
    """APPROXIMATE: ceil(characters / chars_per_token). Not exact for any real tokenizer."""

    def __init__(self, chars_per_token: float = 3.0) -> None:
        self.chars_per_token = chars_per_token

    def _estimate(self, text: str) -> int:
        return math.ceil(len(text) / self.chars_per_token)

    async def count_chat(
        self, model: str, messages: Sequence[dict[str, Any]], response_format: Any | None
    ) -> TokenCount:
        text = "".join(str(m.get("content") or "") for m in messages)
        return TokenCount(
            self._estimate(text),
            self._estimate(_schema_text(response_format)),
            False,
            "approximate",
            None,
        )

    async def count_text(self, model: str, text: str, *, embedding: bool) -> TokenCount:
        return TokenCount(self._estimate(text), 0, False, "approximate", None)

    async def aclose(self) -> None:
        return None


class LMStudioTokenCounter:
    """EXACT: the loaded model's chat template + tokenizer, via the LM Studio SDK."""

    def __init__(self, base_url: str) -> None:
        parsed = urlparse(base_url)
        self.host = f"{parsed.hostname}:{parsed.port or 1234}"
        self._client: Any = None

    async def _connected(self) -> Any:
        if self._client is None:
            from lmstudio import AsyncClient  # imported lazily: only LM Studio needs it

            client = AsyncClient(self.host)
            await client.__aenter__()
            self._client = client
        return self._client

    async def _loaded(self, model: str, *, embedding: bool) -> Any:
        """The handle of an ALREADY loaded model. ``client.llm.model()`` would load a missing
        model onto the GPU (outside OmniGrowth's lease and idle check, with SDK defaults), so
        it is never used: a model that is not loaded blocks the call instead."""
        client = await self._connected()
        session = client.embedding if embedding else client.llm
        for handle in await session.list_loaded():
            if handle.identifier == model:
                return handle
        raise OmniGrowthError(
            ErrorCode.MODEL_UNAVAILABLE,
            f"model {model!r} is not loaded in LM Studio; load it with the configured context "
            f"(lms load {model} --context-length <context_tokens>)",
            details={"reason": "MODEL_NOT_LOADED", "model": model},
            retryable=True,
        )

    def _unavailable(self, exc: Exception) -> OmniGrowthError:
        return OmniGrowthError(
            ErrorCode.MODEL_UNAVAILABLE,
            "cannot count tokens with the server's tokenizer; the request is not sent "
            "(the context budget cannot be verified)",
            details={"reason": "TOKEN_COUNT_UNAVAILABLE", "error": type(exc).__name__},
            retryable=True,
        )

    async def count_chat(
        self, model: str, messages: Sequence[dict[str, Any]], response_format: Any | None
    ) -> TokenCount:
        from lmstudio import Chat, LMStudioError

        try:
            handle = await self._loaded(model, embedding=False)
            history = Chat()
            for message in messages:
                content = str(message.get("content") or "")
                role = message.get("role")
                # Production requests carry plain-string system/user/assistant content only;
                # other roles/parts would be approximated (str); none are sent today.
                if role == "system":
                    history.add_system_prompt(content)
                elif role == "assistant":
                    history.add_assistant_response(content)
                else:
                    history.add_user_message(content)
            rendered = await handle.apply_prompt_template(history)
            prompt = len(await handle.tokenize(rendered))
            schema_text = _schema_text(response_format)
            schema = len(await handle.tokenize(schema_text)) if schema_text else 0
            loaded = int(await handle.get_context_length())
        except (LMStudioError, OSError, ValueError) as exc:
            raise self._unavailable(exc) from exc
        return TokenCount(prompt, schema, True, "lmstudio-sdk", loaded)

    async def count_text(self, model: str, text: str, *, embedding: bool) -> TokenCount:
        from lmstudio import LMStudioError

        try:
            handle = await self._loaded(model, embedding=embedding)
            tokens = len(await handle.tokenize(text))
            loaded = int(await handle.get_context_length())
        except (LMStudioError, OSError, ValueError) as exc:
            raise self._unavailable(exc) from exc
        return TokenCount(tokens, 0, True, "lmstudio-sdk", loaded)

    async def aclose(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.__aexit__(None, None, None)
