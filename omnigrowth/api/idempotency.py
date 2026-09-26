"""``Idempotency-Key`` handling for [IK] routes (API_PLAN §1.6).

A route wraps its work in ``idempotent(...)``: the first request with a key runs and its
response is stored; a retry with the same key and the same input replays that response with
``Idempotent-Replayed: true`` and has no side effects; a concurrent duplicate or a reused
key with different input is a 409. A failed request forgets its key so it can be retried.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from omnigrowth.api.deps import Principal
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.settings import get_settings
from omnigrowth.services import idempotency as store

HEADER = "Idempotency-Key"
REPLAYED = "Idempotent-Replayed"

Outcome = tuple[int, BaseModel | None, dict[str, str]]


def fingerprint(value: Any) -> str:
    """sha256 of the canonical (sorted-keys JSON) form of the route's parsed input."""
    canonical = json.dumps(jsonable_encoder(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def idempotent(
    request: Request,
    principal: Principal,
    inputs: Any,
    run: Callable[[], Awaitable[Outcome]],
    *,
    required: bool = True,
) -> JSONResponse:
    key = request.headers.get(HEADER)
    if key is None:
        if required and get_settings().require_idempotency_key:
            raise OmniGrowthError(
                ErrorCode.VALIDATION_FAILURE,
                f"this request requires an {HEADER} header",
                details={"reason": "IDEMPOTENCY_KEY_REQUIRED"},
            )
        status, body, headers = await run()
        return JSONResponse(jsonable_encoder(body), status_code=status, headers=headers)

    route = getattr(request.scope.get("route"), "path", request.url.path)
    claim = await store.begin(
        principal_id=principal.user_id,
        method=request.method,
        route=route,
        key=key,
        body_hash=fingerprint(inputs),
    )
    if isinstance(claim, store.Replay):
        stored = claim.body or {}
        return JSONResponse(
            stored.get("body"),
            status_code=claim.status,
            headers={**stored.get("headers", {}), REPLAYED: "true"},
        )
    completed = False
    try:
        status, body, headers = await run()
        payload = jsonable_encoder(body)
        await store.complete(claim, status, {"body": payload, "headers": headers})
        completed = True
        return JSONResponse(payload, status_code=status, headers=headers)
    finally:
        if not completed:
            await store.abandon(claim)
