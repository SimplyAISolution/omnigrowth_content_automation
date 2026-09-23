"""Correlation-id / request-id middleware and access log (API_PLAN §1.5, spec §28).

Pure ASGI so the ids are available (``scope["state"]``) to exception handlers, including
the 500 handler that Starlette runs *outside* user middleware.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omnigrowth.core.ids import new_correlation_id
from omnigrowth.core.logging import bind_context

CORRELATION_HEADER = "X-Correlation-ID"
REQUEST_ID_HEADER = "X-Request-ID"
_CORRELATION_RE = re.compile(r"[A-Za-z0-9._-]{8,128}")

access_logger = logging.getLogger("omnigrowth.api.access")
logger = logging.getLogger(__name__)


def is_valid_correlation_id(value: str) -> bool:
    return _CORRELATION_RE.fullmatch(value) is not None


def _inbound_correlation_id(scope: Scope) -> str | None:
    wanted = CORRELATION_HEADER.lower().encode("latin-1")
    for name, value in scope.get("headers", []):
        if name.lower() == wanted:
            return bytes(value).decode("latin-1")
    return None


def request_ids(scope: Scope) -> tuple[str | None, str | None]:
    """Return ``(correlation_id, request_id)`` stored by the middleware, if any."""
    state: Mapping[str, Any] = scope.get("state") or {}
    return state.get("correlation_id"), state.get("request_id")


class CorrelationIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound = _inbound_correlation_id(scope)
        if inbound is not None and is_valid_correlation_id(inbound):
            correlation_id = inbound
        else:
            correlation_id = new_correlation_id()
        request_id = new_correlation_id()
        scope.setdefault("state", {})
        scope["state"]["correlation_id"] = correlation_id
        scope["state"]["request_id"] = request_id

        status_code: int | None = None
        start = time.perf_counter()

        async def send_with_ids(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers[CORRELATION_HEADER] = correlation_id
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        with bind_context(correlation_id=correlation_id, request_id=request_id):
            if inbound is not None and correlation_id != inbound:
                # Never log the rejected value itself: it is untrusted input.
                logger.info(
                    "invalid inbound correlation id replaced",
                    extra={"inbound_length": len(inbound)},
                )
            try:
                await self.app(scope, receive, send_with_ids)
            finally:
                # An unhandled exception propagates on to ServerErrorMiddleware (-> 500);
                # the access line is still written and the exception is not swallowed.
                access_logger.info(
                    "request",
                    extra={
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status_code if status_code is not None else 500,
                        "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                    },
                )
