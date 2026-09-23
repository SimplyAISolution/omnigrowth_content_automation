"""Exception handlers producing the ``ErrorResponse`` envelope (API_PLAN §1.4)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from omnigrowth.api.middleware import CORRELATION_HEADER, REQUEST_ID_HEADER, request_ids
from omnigrowth.core.errors import ErrorCode, OmniGrowthError
from omnigrowth.core.ids import new_correlation_id
from omnigrowth.core.logging import bind_context, get_context
from omnigrowth.core.masking import mask_mapping, mask_text
from omnigrowth.schemas.errors import ErrorResponse

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "Internal server error"
VALIDATION_ERROR_MESSAGE = "Request validation failed"

_HTTP_STATUS_CODES: dict[int, ErrorCode] = {
    400: ErrorCode.VALIDATION_FAILURE,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.VALIDATION_FAILURE,
    409: ErrorCode.CONFLICT,
    413: ErrorCode.VALIDATION_FAILURE,
    415: ErrorCode.VALIDATION_FAILURE,
    422: ErrorCode.VALIDATION_FAILURE,
    429: ErrorCode.RATE_LIMITED,
}


def _ids(request: Request) -> tuple[str, str | None]:
    correlation_id, request_id = request_ids(request.scope)
    if correlation_id is None:
        correlation_id = get_context().get("correlation_id") or new_correlation_id()
    return correlation_id, request_id


def error_response(
    request: Request,
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    correlation_id, request_id = _ids(request)
    body = ErrorResponse(
        code=code,
        message=mask_text(message),
        correlation_id=correlation_id,
        details=mask_mapping(details) if details else None,
    )
    out_headers = dict(headers or {})
    # Also set here: the 500 handler runs outside the correlation middleware.
    out_headers[CORRELATION_HEADER] = correlation_id
    if request_id is not None:
        out_headers[REQUEST_ID_HEADER] = request_id
    return JSONResponse(
        status_code=status_code, content=body.model_dump(mode="json"), headers=out_headers
    )


async def omnigrowth_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, OmniGrowthError):
        raise exc
    level = logging.ERROR if exc.http_status >= 500 else logging.INFO
    logger.log(level, "request failed: %s", exc.code, extra={"error_code": str(exc.code)})
    return error_response(
        request,
        status_code=exc.http_status,
        code=exc.code,
        message=exc.message,
        details=exc.details or None,
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    # Only loc/msg/type are returned: raw ``input`` and ``ctx`` are dropped so no submitted
    # value (secret-looking or otherwise) is ever echoed back.
    errors = [
        {
            "loc": [str(part) for part in err.get("loc", ())],
            "msg": mask_text(str(err.get("msg", ""))),
            "type": str(err.get("type", "")),
        }
        for err in exc.errors()
    ]
    return error_response(
        request,
        status_code=422,
        code=ErrorCode.VALIDATION_FAILURE,
        message=VALIDATION_ERROR_MESSAGE,
        details={"errors": errors},
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    default = ErrorCode.INTERNAL_ERROR if exc.status_code >= 500 else ErrorCode.VALIDATION_FAILURE
    code = _HTTP_STATUS_CODES.get(exc.status_code, default)
    message = exc.detail if isinstance(exc.detail, str) else code.value
    return error_response(
        request,
        status_code=exc.status_code,
        code=code,
        message=message,
        headers=dict(exc.headers) if exc.headers else None,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    correlation_id, request_id = _ids(request)
    with bind_context(correlation_id=correlation_id, request_id=request_id):
        logger.exception(
            "unhandled exception",
            exc_info=exc,
            extra={"method": request.method, "path": request.url.path},
        )
    return error_response(
        request,
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        message=INTERNAL_ERROR_MESSAGE,
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(OmniGrowthError, omnigrowth_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
