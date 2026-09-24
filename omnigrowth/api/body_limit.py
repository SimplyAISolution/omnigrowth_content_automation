"""Request-body size guard (API_PLAN §6.2: "streamed; aborted when exceeded (no full
buffering)"; JSON bodies max 1 MiB, uploads max 25 MiB).

FastAPI parses multipart forms (spooling files to disk) BEFORE dependencies such as auth
run, so the limit must be enforced here, below the framework: a declared Content-Length over
the limit is rejected immediately, and streamed bytes are counted as they arrive.
"""

from __future__ import annotations

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omnigrowth.core.errors import ErrorCode

JSON_LIMIT = 1024 * 1024
# Upload route: 25 MiB file + multipart framing/fields headroom.
UPLOAD_LIMIT = 25 * 1024 * 1024 + 64 * 1024
UPLOAD_PATH_SUFFIX = "/sources/upload"


class _BodyTooLarge(Exception):  # noqa: N818 - internal control-flow signal
    pass


def _limit_for(path: str) -> int:
    return UPLOAD_LIMIT if path.endswith(UPLOAD_PATH_SUFFIX) else JSON_LIMIT


async def _reject(scope: Scope, send: Send, limit: int) -> None:
    state = scope.get("state") or {}
    body = json.dumps(
        {
            "code": ErrorCode.VALIDATION_FAILURE.value,
            "message": "request body too large",
            "correlation_id": state.get("correlation_id", ""),
            "details": {"limit_bytes": limit},
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = _limit_for(scope["path"])
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = limit + 1
                if declared > limit:
                    await _reject(scope, send, limit)
                    return
        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge:
            if not response_started:
                await _reject(scope, send, limit)
