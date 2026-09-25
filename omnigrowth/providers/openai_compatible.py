"""``OpenAICompatibleProvider`` — the single provider class for LM Studio, Ollama and the
optional cloud endpoint (A1). Every transport failure is mapped to a categorized error.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.masking import mask_text
from omnigrowth.providers.base import ChatRequest, ChatResult, UnsupportedResponseFormatError


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _rejects_response_format(response: httpx.Response) -> bool:
    if response.status_code not in (400, 422):
        return False
    return "response_format" in response.text or "json_schema" in response.text


class OpenAICompatibleProvider:
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = False,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        # trust_env=False by default: environment / system proxies must not intercept local
        # LLM traffic (which may carry PRIVATE content).
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout_s,
            transport=transport,
            trust_env=trust_env,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise OmniGrowthError(
                ErrorCode.MODEL_TIMEOUT,
                f"{self.name}: model request timed out",
                details={"provider": self.name},
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:  # connect refused, DNS, reset, protocol
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"{self.name}: provider unreachable",
                details={"provider": self.name, "error": type(exc).__name__},
                retryable=True,
            ) from exc

    def _raise_for_status(self, response: httpx.Response, request: ChatRequest) -> None:
        if response.status_code == 429:
            raise OmniGrowthError(
                ErrorCode.PROVIDER_RATE_LIMIT,
                f"{self.name}: rate limited",
                details={"provider": self.name, "retry_after_s": _retry_after(response)},
                retryable=True,
            )
        if request.response_format is not None and _rejects_response_format(response):
            raise UnsupportedResponseFormatError(
                str(request.response_format.get("type")), mask_text(response.text[:300])
            )
        if response.status_code >= 500 or response.status_code == 404:
            # 404: model not loaded / unknown to this endpoint.
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"{self.name}: HTTP {response.status_code}",
                details={"provider": self.name, "model": request.model},
                retryable=True,
            )
        if response.status_code >= 400:
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"{self.name}: request rejected (HTTP {response.status_code})",
                details={"provider": self.name, "body": mask_text(response.text[:300])},
            )

    async def chat(self, request: ChatRequest) -> ChatResult:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "stream": False,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        start = time.perf_counter()
        response = await self._post("/chat/completions", payload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        self._raise_for_status(response, request)
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OmniGrowthError(
                ErrorCode.LLM_OUTPUT_INVALID,
                f"{self.name}: malformed completion envelope",
                details={"provider": self.name, "raw": mask_text(response.text[:2000])},
            ) from exc
        usage = body.get("usage") or {}
        return ChatResult(
            text=text if isinstance(text, str) else "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=latency_ms,
        )

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        response = await self._post("/embeddings", {"model": model, "input": texts})
        self._raise_for_status(response, ChatRequest(model=model, messages=[]))
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"{self.name}: malformed embeddings envelope",
                details={"provider": self.name},
            ) from exc
        if len(vectors) != len(texts) or len({len(v) for v in vectors}) > 1:
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"{self.name}: embeddings count/dimension mismatch",
                details={"provider": self.name, "expected": len(texts), "got": len(vectors)},
            )
        return vectors

    async def list_models(self) -> list[str]:
        try:
            response = await self._client.get("/models")
        except httpx.TimeoutException as exc:
            raise OmniGrowthError(
                ErrorCode.MODEL_TIMEOUT, f"{self.name}: /models timed out", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"{self.name}: provider unreachable",
                details={"error": type(exc).__name__},
                retryable=True,
            ) from exc
        if response.status_code != 200:
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE, f"{self.name}: /models HTTP {response.status_code}"
            )
        try:
            data = response.json().get("data", [])
            return [str(item["id"]) for item in data if isinstance(item, dict) and "id" in item]
        except (ValueError, AttributeError) as exc:
            raise OmniGrowthError(
                ErrorCode.MODEL_UNAVAILABLE, f"{self.name}: invalid /models payload"
            ) from exc
