"""``MastodonLivePublisher`` (Phase 13, A17): the first real connector.

Every endpoint used here is VERIFIED in docs/PLATFORM_CAPABILITIES.md §4.9 (official docs,
checked 2026-09-27):

* ``POST /api/v1/statuses`` (scope ``write:statuses``) with an ``Idempotency-Key`` header
  (the instance keeps keys for up to one hour); threads are replies (``in_reply_to_id``).
* ``GET /api/v1/accounts/verify_credentials`` (``read:accounts``), then
  ``GET /api/v1/accounts/:id/statuses`` (max 40 per page) and
  ``GET /api/v1/statuses/:id/source`` (``read:statuses``) to reconcile an uncertain post by
  comparing our exact text: we never re-post to find out.
* ``GET /api/v1/statuses/:id`` for metrics: favourites / boosts / replies only. Impressions,
  reach and views do not exist in the API and stay ``None`` (never 0).

Outcome classes (base.py, §3.5): a request that provably never left (connect/DNS) is
retryable; 429 is retryable with the reset time; 401/403 and the documented rejections
404/422 are proven failures; any other answer (a timeout after send, a 5xx, a redirect, an
undocumented 4xx, an unreadable body) is UNCERTAIN, as is any failure after the first part
of a thread went out. The access token lives only in this
object's memory (from the vault) and is never logged or put in an error.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import SecretStr

from omnigrowth.core.enums import ContentFormat, Platform, PublishMode
from omnigrowth.core.errors import OmniGrowthError
from omnigrowth.publishing.base import (
    FailureKind,
    LookupOutcome,
    LookupStatus,
    MetricsResult,
    PublishError,
    PublishRequest,
    PublishResult,
)
from omnigrowth.security.http import (
    PlatformResponse,
    PlatformTransportError,
    Resolver,
    platform_request,
    system_resolver,
)

SUPPORTED_FORMATS = (ContentFormat.TEXT_POST, ContentFormat.THREAD)
VISIBILITIES = ("public", "unlisted", "private")  # "direct" is never used for publishing
URL_RE = re.compile(r"https?://\S+")
# Scopes the owner grants when creating the token (least privilege, docs §OAuth scopes).
REQUIRED_SCOPES = ("write:statuses", "read:statuses", "read:accounts")
LOOKUP_PAGE_SIZE = 40  # documented maximum for account statuses
LOOKUP_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class MastodonAccountConfig:
    """Per-account publishing settings (platform_accounts.settings, validated)."""

    instance_base_url: str
    visibility: str = "public"
    language: str | None = None
    max_characters: int = 500
    characters_reserved_per_url: int = 23


def status_length(text: str, reserved_per_url: int) -> int:
    """Mastodon counts every URL as ``characters_reserved_per_url`` (instance config)."""
    urls = URL_RE.findall(text)
    return len(URL_RE.sub("", text)) + reserved_per_url * len(urls)


def _normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))


def _reset_after(headers: dict[str, str], now: datetime) -> float | None:
    """Seconds until ``X-RateLimit-Reset`` (ISO 8601); falls back to ``Retry-After``."""
    reset = headers.get("x-ratelimit-reset")
    if reset:
        try:
            when = datetime.fromisoformat(reset.replace("Z", "+00:00"))
        except ValueError:
            when = None
        if when is not None:
            return max(0.0, (when - now).total_seconds())
    retry_after = headers.get("retry-after", "")
    return float(retry_after) if retry_after.isdigit() else None


class MastodonLivePublisher:
    mode = PublishMode.LIVE
    external_side_effect = True

    def __init__(
        self,
        config: MastodonAccountConfig,
        token: SecretStr,
        *,
        allowlist: frozenset[str],
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = system_resolver,
        lookup_pages: int = 3,
        max_source_checks: int = 60,
    ) -> None:
        self._config = config
        self._token = token
        self._allowlist = allowlist
        self._timeout_s = timeout_s
        self._transport = transport
        self._resolver = resolver
        self._lookup_pages = lookup_pages
        self._max_source_checks = max_source_checks

    def __repr__(self) -> str:  # never the token
        return f"MastodonLivePublisher({self._config.instance_base_url!r})"

    # ------------------------------------------------------------------ HTTP
    def _url(self, path: str) -> str:
        return self._config.instance_base_url.rstrip("/") + path

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, object] | None = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> PlatformResponse:
        headers = {"Authorization": f"Bearer {self._token.get_secret_value()}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            return await platform_request(
                method,
                self._url(path),
                headers=headers,
                allowlist=self._allowlist,
                timeout_s=self._timeout_s,
                json_body=json_body,
                params=params,
                resolver=self._resolver,
                transport=self._transport,
            )
        except OmniGrowthError as exc:
            # The SSRF guard refuses before anything is sent: proven not published.
            raise PublishError(
                FailureKind.VALIDATION, f"destination refused ({exc.code.value})"
            ) from exc

    @staticmethod
    def _json(response: PlatformResponse) -> Any:
        try:
            return json.loads(response.body)
        except (ValueError, UnicodeDecodeError):
            return None

    # ------------------------------------------------------------------ validate / preview
    def validate(self, request: PublishRequest) -> None:
        if request.platform is not Platform.MASTODON:
            raise PublishError(FailureKind.VALIDATION, "not a Mastodon post")
        if request.format not in SUPPORTED_FORMATS:
            raise PublishError(
                FailureKind.VALIDATION,
                f"{request.format.value} is not supported by the Mastodon connector",
            )
        if not request.segments or not all(s.strip() for s in request.segments):
            raise PublishError(FailureKind.VALIDATION, "empty post")
        if request.format is ContentFormat.TEXT_POST and len(request.segments) != 1:
            raise PublishError(FailureKind.VALIDATION, "a text post is exactly one status")
        if self._config.visibility not in VISIBILITIES:
            raise PublishError(FailureKind.VALIDATION, "invalid visibility setting")
        for index, segment in enumerate(request.segments, start=1):
            length = status_length(segment, self._config.characters_reserved_per_url)
            if length > self._config.max_characters:
                raise PublishError(
                    FailureKind.VALIDATION,
                    f"part {index} is {length} characters; this instance allows "
                    f"{self._config.max_characters}",
                )

    def _body(self, segment: str, reply_to: str | None) -> dict[str, object]:
        body: dict[str, object] = {"status": segment, "visibility": self._config.visibility}
        if self._config.language:
            body["language"] = self._config.language
        if reply_to is not None:
            body["in_reply_to_id"] = reply_to
        return body

    def preview(self, request: PublishRequest) -> dict[str, Any]:
        return preview_request(self._config, request)

    # ------------------------------------------------------------------ publish
    def _fail(self, kind: FailureKind, message: str, *, posted: int, **kw: Any) -> PublishError:
        """``posted`` = thread parts confirmed before this one."""
        if posted > 0:
            # Part of a thread is already public: the post is neither done nor absent.
            return PublishError(
                FailureKind.AFTER_SEND,
                f"thread partially published ({posted} part(s) posted) then: {message}",
            )
        return PublishError(kind, message, **kw)

    async def publish(self, request: PublishRequest) -> PublishResult:
        self.validate(request)
        root: PublishResult | None = None
        reply_to: str | None = None
        for index, segment in enumerate(request.segments):
            posted = index
            try:
                response = await self._call(
                    "POST",
                    "/api/v1/statuses",
                    json_body=self._body(segment, reply_to),
                    # Stable per part: a retry of a request that never left, or a replay
                    # within the instance's one-hour window, cannot create a second status.
                    idempotency_key=f"omnigrowth-{request.idempotency_key}-{index}",
                )
            except PlatformTransportError as exc:
                if exc.sent:
                    raise self._fail(
                        FailureKind.AFTER_SEND, f"no answer ({exc.reason})", posted=posted
                    ) from exc
                raise self._fail(
                    FailureKind.CONNECT_TIMEOUT, f"not sent ({exc.reason})", posted=posted
                ) from exc
            status = response.status_code
            if status == 429:
                raise self._fail(
                    FailureKind.RATE_LIMIT,
                    "rate limited",
                    posted=posted,
                    retry_after_s=_reset_after(response.headers, datetime.now(UTC)),
                )
            if status in (401, 403):
                raise self._fail(FailureKind.AUTH, f"HTTP {status}", posted=posted)
            if status in (404, 422):
                # Documented rejections (not found / validation): nothing was created.
                raise self._fail(FailureKind.VALIDATION, f"HTTP {status}", posted=posted)
            if status != 200:
                # 5xx or a redirect: the instance may have created the status.
                raise self._fail(FailureKind.AFTER_SEND, f"HTTP {status}", posted=posted)
            data = self._json(response)
            if not isinstance(data, dict) or not data.get("id"):
                raise self._fail(FailureKind.AFTER_SEND, "unreadable answer", posted=posted)
            post = PublishResult(
                external_post_id=str(data["id"]),
                canonical_url=str(data.get("url") or data.get("uri") or ""),
                published_at=_parse_time(data.get("created_at")) or datetime.now(UTC),
            )
            root = root or post
            reply_to = post.external_post_id
        assert root is not None  # noqa: S101 - validate() guarantees one segment
        return root

    # ------------------------------------------------------------------ reconcile
    async def _own_account_id(self) -> str:
        response = await self._call("GET", "/api/v1/accounts/verify_credentials")
        data = self._json(response)
        if response.status_code != 200 or not isinstance(data, dict) or not data.get("id"):
            kind = (
                FailureKind.AUTH if response.status_code in (401, 403) else FailureKind.AFTER_SEND
            )
            raise PublishError(kind, f"verify_credentials failed (HTTP {response.status_code})")
        return str(data["id"])

    async def lookup(
        self,
        request: PublishRequest,
        *,
        since: datetime | None = None,
        exclude_ids: frozenset[str] = frozenset(),
    ) -> LookupOutcome:
        """Find our post among the account's recent statuses by exact text (``/source``).

        Bounded: ``lookup_pages`` pages of 40 and ``max_source_checks`` source reads. Absence
        is reported as NOT_FOUND with the number searched, never as proof."""
        try:
            account_id = await self._own_account_id()
            floor = (since - LOOKUP_SKEW) if since is not None else None
            wanted = [_normalize(s) for s in request.segments]
            candidates: list[dict[str, Any]] = []
            max_id: str | None = None
            for _ in range(self._lookup_pages):
                params = {"limit": str(LOOKUP_PAGE_SIZE), "exclude_reblogs": "true"}
                if max_id is not None:
                    params["max_id"] = max_id
                page = await self._call(
                    "GET", f"/api/v1/accounts/{account_id}/statuses", params=params
                )
                items = self._json(page)
                if page.status_code != 200 or not isinstance(items, list):
                    raise PublishError(
                        FailureKind.AFTER_SEND, f"status list failed (HTTP {page.status_code})"
                    )
                if not items:
                    break
                older_than_floor = False
                for item in items:
                    created = _parse_time(item.get("created_at"))
                    if floor is not None and created is not None and created < floor:
                        older_than_floor = True
                        continue
                    if str(item.get("id")) in exclude_ids:
                        continue  # already recorded as another publication's post
                    candidates.append(item)
                max_id = str(items[-1].get("id"))
                if older_than_floor or len(items) < LOOKUP_PAGE_SIZE:
                    break
            found: dict[int, list[dict[str, Any]]] = {}
            checks = 0
            for item in candidates:
                if checks >= self._max_source_checks:
                    break
                checks += 1
                source = await self._call("GET", f"/api/v1/statuses/{item['id']}/source")
                text = self._json(source)
                if source.status_code != 200 or not isinstance(text, dict):
                    continue
                normalized = _normalize(str(text.get("text", "")))
                for index, want in enumerate(wanted):
                    if normalized == want:
                        found.setdefault(index, []).append(item)
                        break
        except PlatformTransportError as exc:
            raise PublishError(FailureKind.AFTER_SEND, f"lookup failed ({exc.reason})") from exc
        if any(len(items) > 1 for items in found.values()):
            # The same text was posted more than once in the window: never guess which one.
            return LookupOutcome(
                LookupStatus.AMBIGUOUS,
                found_post_ids=tuple(str(i["id"]) for items in found.values() for i in items),
                searched=checks,
                detail="the same text appears more than once; check the account",
            )
        matches = {index: items[0] for index, items in found.items()}
        # A thread only counts as found when its parts are chained replies.
        chained = [0] if 0 in matches else []
        for index in range(1, len(wanted)):
            prev, item = matches.get(index - 1), matches.get(index)
            if prev is None or item is None or str(item.get("in_reply_to_id")) != str(prev["id"]):
                break
            chained.append(index)
        found_ids = tuple(str(matches[i]["id"]) for i in sorted(matches))
        if matches and len(chained) == len(wanted):
            root = matches[0]
            return LookupOutcome(
                LookupStatus.FOUND,
                result=PublishResult(
                    external_post_id=str(root["id"]),
                    canonical_url=str(root.get("url") or root.get("uri") or ""),
                    published_at=_parse_time(root.get("created_at")) or datetime.now(UTC),
                ),
                found_post_ids=found_ids,
                searched=checks,
            )
        if matches:
            return LookupOutcome(
                LookupStatus.PARTIAL,
                found_post_ids=found_ids,
                searched=checks,
                detail=f"{len(matches)} of {len(wanted)} part(s) found",
            )
        return LookupOutcome(
            LookupStatus.NOT_FOUND,
            searched=checks,
            detail=f"not among the {checks} most recent post(s) compared",
        )

    # ------------------------------------------------------------------ metrics
    async def fetch_metrics(
        self, platform: Platform, external_post_id: str, *, seed: str
    ) -> MetricsResult:
        try:
            response = await self._call("GET", f"/api/v1/statuses/{external_post_id}")
        except PlatformTransportError as exc:
            raise PublishError(FailureKind.CONNECT_TIMEOUT, exc.reason) from exc
        if response.status_code == 429:
            raise PublishError(
                FailureKind.RATE_LIMIT,
                "rate limited",
                retry_after_s=_reset_after(response.headers, datetime.now(UTC)),
            )
        if response.status_code in (401, 403):
            raise PublishError(FailureKind.AUTH, f"HTTP {response.status_code}")
        data = self._json(response)
        if response.status_code != 200 or not isinstance(data, dict):
            raise PublishError(FailureKind.VALIDATION, f"HTTP {response.status_code}")

        def count(key: str) -> int | None:
            value = data.get(key)
            return value if isinstance(value, int) and value >= 0 else None

        values: dict[str, int | None] = {
            "likes": count("favourites_count"),
            "shares": count("reblogs_count"),
            "comments": count("replies_count"),
            # Not provided by the Mastodon API (VERIFIED absent): never zero-filled.
            "impressions": None,
            "saves": None,
            "clicks": None,
            "profile_visits": None,
            "followers_gained": None,
        }
        raw = {
            "external_post_id": external_post_id,
            "favourites_count": data.get("favourites_count"),
            "reblogs_count": data.get("reblogs_count"),
            "replies_count": data.get("replies_count"),
            "quotes_count": data.get("quotes_count"),
        }
        return MetricsResult(values=values, raw=raw)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def preview_request(config: MastodonAccountConfig, request: PublishRequest) -> dict[str, Any]:
    """Exactly what ``publish`` sends (no token needed, so previews never decrypt one)."""
    return {
        "publisher": "MastodonLivePublisher",
        "simulated": False,
        "instance": config.instance_base_url,
        "account": request.account_handle,
        "visibility": config.visibility,
        "language": config.language,
        "segments": list(request.segments),
        "characters": [
            status_length(s, config.characters_reserved_per_url) for s in request.segments
        ],
        "max_characters": config.max_characters,
        # Text-only connector: suggested media are listed, never uploaded.
        "media_attached": [],
        "media_not_attached": list(request.media_requirements),
        "thread": len(request.segments) > 1,
    }
