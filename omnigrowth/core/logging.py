"""Structured JSON logging with correlation context (spec §28) and secret masking (§25).

Context (correlation_id, request_id, campaign_id, agent, model) lives in contextvars so it
flows through async code; ``bind_context`` sets it for a block.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from omnigrowth.core.masking import mask_mapping, mask_text

CONTEXT_FIELDS = ("correlation_id", "request_id", "campaign_id", "agent", "model")

_context: ContextVar[dict[str, str]] = ContextVar("omnigrowth_log_context", default={})  # noqa: B039

# LogRecord attributes that are not user "extra" fields.
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def get_context() -> dict[str, str]:
    return dict(_context.get())


@contextmanager
def bind_context(**values: str | None) -> Iterator[None]:
    merged = {**_context.get(), **{k: v for k, v in values.items() if v is not None}}
    token = _context.set(merged)
    try:
        yield
    finally:
        _context.reset(token)


def set_context(**values: str | None) -> None:
    """Set context for the remainder of the current task (e.g. per request middleware)."""
    _context.set({**_context.get(), **{k: v for k, v in values.items() if v is not None}})


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in _context.get().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


class MaskingFilter(logging.Filter):
    """Masks secrets in the message, args and extra fields of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(record.getMessage())
        record.args = None
        for key, value in list(vars(record).items()):
            if key in _RESERVED:
                continue
            if isinstance(value, dict):
                setattr(record, key, mask_mapping(value))
            elif isinstance(value, str):
                setattr(record, key, mask_mapping({key: value})[key])
        if record.exc_text:
            record.exc_text = mask_text(record.exc_text)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        payload = mask_mapping(payload)
        payload["msg"] = mask_text(str(payload["msg"]))
        if record.exc_info:
            payload["exc"] = mask_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class MaskingTextFormatter(logging.Formatter):
    """Plain-text formatter that masks the final line, including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_text(super().format(record))

    def formatException(self, ei: Any) -> str:  # noqa: N802 - stdlib override
        return mask_text(super().formatException(ei))


# Third-party loggers that install their own handlers; route them through ours instead.
_ADOPTED_LOGGERS = ("uvicorn", "uvicorn.error", "rq.worker")
# uvicorn's access log duplicates our structured access line and logs raw query strings.
_SILENCED_LOGGERS = ("uvicorn.access",)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(ContextFilter())
    handler.addFilter(MaskingFilter())
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(MaskingTextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    for name in _ADOPTED_LOGGERS:
        adopted = logging.getLogger(name)
        adopted.handlers = []
        adopted.propagate = True
    for name in _SILENCED_LOGGERS:
        silenced = logging.getLogger(name)
        silenced.handlers = []
        silenced.propagate = False
        silenced.disabled = True
