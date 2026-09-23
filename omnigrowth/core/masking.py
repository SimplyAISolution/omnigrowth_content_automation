"""Secret masking (spec §25 "masked logs", AGENTS.md).

``mask_text`` redacts secrets inside free text (URLs with passwords, bearer tokens,
key=value pairs with secret-looking keys, well-known API key shapes).
``mask_mapping`` redacts values whose *key* looks secret, recursively.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "***"

_SECRET_KEY_RE = re.compile(
    r"(pass(word)?|passwd|secret|token|api[_-]?key|apikey|authorization|auth|credential|"
    r"private[_-]?key|kek|cookie|session|signature|dsn)",
    re.IGNORECASE,
)

_TEXT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # scheme://user:password@host and scheme://:password@host (redis)  ->  ...:***@host
    (re.compile(r"(?P<pre>[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/\s@]*:)[^@\s/]+(?=@)"), r"\g<pre>***"),
    # Authorization schemes: Bearer / Basic / Token / ApiKey
    (
        re.compile(r"(?i)\b(bearer|basic|token|apikey|api-key)\s+[A-Za-z0-9._~+/=\-]{6,}"),
        r"\1 ***",
    ),
    # quoted value (may contain spaces) after a secret-looking key: password="hello world"
    (
        re.compile(
            r"(?i)(?P<key>[a-z0-9_\-]*(?:password|passwd|secret|token|api[_-]?key|apikey|"
            r"credential|kek)[a-z0-9_\-]*[\"']?\s*[:=]\s*)(?P<q>[\"'])[^\"']*(?P=q)"
        ),
        r"\g<key>\g<q>***\g<q>",
    ),
    # key=value / key: value / "key": "value" with secret-looking key
    (
        re.compile(
            r"(?i)(?P<key>[\"']?[a-z0-9_\-]*(?:password|passwd|secret|token|api[_-]?key|"
            r"apikey|credential|kek)[a-z0-9_\-]*[\"']?\s*[:=]\s*[\"']?)(?P<val>[^\s\"',;&}]+)"
        ),
        r"\g<key>***",
    ),
    # common provider key shapes
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b"), REDACTED),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}\b"), REDACTED),
    (re.compile(r"\bog_[A-Za-z0-9_\-]{16,}\b"), REDACTED),  # OmniGrowth API tokens
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9\-]{10,}\b"), REDACTED),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),
]


def mask_text(text: str) -> str:
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def is_secret_key(key: str) -> bool:
    return bool(_SECRET_KEY_RE.search(key))


def mask_value(value: Any) -> Any:
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, Mapping):
        return mask_mapping(value)
    if isinstance(value, list | tuple):
        return [mask_value(v) for v in value]
    return value


def mask_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        if is_secret_key(str(key)) and value is not None:
            out[key] = REDACTED
        else:
            out[key] = mask_value(value)
    return out
