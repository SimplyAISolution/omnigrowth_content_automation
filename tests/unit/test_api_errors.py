"""ErrorResponse envelope tests (API_PLAN §1.4) using throwaway test routes."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from omnigrowth.api.app import create_app
from omnigrowth.core.errors import ErrorCode, OmniGrowthError

CID = "test-correlation-0001"


class Payload(BaseModel):
    count: int
    api_key: str


def build_app() -> FastAPI:
    app = create_app()

    @app.get("/api/v1/_test/og-error")
    async def og_error() -> None:
        raise OmniGrowthError(
            ErrorCode.INVALID_STATE_TRANSITION,
            "asset is REJECTED",
            details={"entity": "content_asset", "token": "should-be-masked"},
        )

    @app.get("/api/v1/_test/not-found")
    async def not_found() -> None:
        raise OmniGrowthError(ErrorCode.NOT_FOUND, "brand not found")

    @app.get("/api/v1/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals password=hunter2")

    @app.post("/api/v1/_test/validate")
    async def validate(payload: Payload) -> Payload:
        return payload

    return app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=False: Starlette re-raises after the 500 handler responds.
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def assert_envelope(resp: httpx.Response, status: int, code: ErrorCode) -> dict[str, object]:
    assert resp.status_code == status
    body: dict[str, object] = resp.json()
    assert set(body) == {"code", "message", "correlation_id", "details"}
    assert body["code"] == code.value
    assert body["correlation_id"] == resp.headers["X-Correlation-ID"]
    assert resp.headers["X-Request-ID"]
    return body


async def test_omnigrowth_error_maps_status_and_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/_test/og-error", headers={"X-Correlation-ID": CID})
    body = assert_envelope(resp, 409, ErrorCode.INVALID_STATE_TRANSITION)
    assert body["correlation_id"] == CID
    assert body["message"] == "asset is REJECTED"
    assert body["details"] == {"entity": "content_asset", "token": "***"}


async def test_omnigrowth_not_found(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/_test/not-found")
    body = assert_envelope(resp, 404, ErrorCode.NOT_FOUND)
    assert body["details"] is None


async def test_unhandled_exception_is_generic_500(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/_test/boom", headers={"X-Correlation-ID": CID})
    body = assert_envelope(resp, 500, ErrorCode.INTERNAL_ERROR)
    assert body["message"] == "Internal server error"
    assert body["correlation_id"] == CID
    assert "hunter2" not in resp.text and "RuntimeError" not in resp.text


async def test_unhandled_exception_logged_with_context_and_masked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Build app inside the test so configure_logging binds to capsys' stdout.
    transport = httpx.ASGITransport(app=build_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/v1/_test/boom", headers={"X-Correlation-ID": CID})
    assert resp.status_code == 500
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    errors = [rec for rec in lines if rec["msg"] == "unhandled exception"]
    assert len(errors) == 1
    record = errors[0]
    assert record["level"] == "ERROR"
    assert record["correlation_id"] == CID
    assert "RuntimeError" in record["exc"]
    assert "hunter2" not in json.dumps(record)
    access = [rec for rec in lines if rec["logger"] == "omnigrowth.api.access"]
    assert access and access[-1]["status"] == 500 and access[-1]["correlation_id"] == CID


async def test_validation_error_422_without_echoing_input(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/_test/validate", json={"count": "not-a-number", "api_key": 12345}
    )
    body = assert_envelope(resp, 422, ErrorCode.VALIDATION_FAILURE)
    details = body["details"]
    assert isinstance(details, dict)
    errors = details["errors"]
    assert {tuple(e["loc"]) for e in errors} == {("body", "count"), ("body", "api_key")}
    assert all(set(e) == {"loc", "msg", "type"} for e in errors)
    assert "not-a-number" not in resp.text and "12345" not in resp.text


async def test_unknown_route_404_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/does-not-exist")
    assert_envelope(resp, 404, ErrorCode.NOT_FOUND)


async def test_method_not_allowed_405_envelope(client: httpx.AsyncClient) -> None:
    resp = await client.delete("/api/v1/_test/boom")
    assert_envelope(resp, 405, ErrorCode.VALIDATION_FAILURE)
    assert "GET" in resp.headers["Allow"]
