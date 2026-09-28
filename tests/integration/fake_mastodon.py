"""A local stand-in for a Mastodon instance (127.0.0.1 only) for the Phase 13 tests.

Implements just the documented endpoints the connector uses (docs/PLATFORM_CAPABILITIES.md
§4.9): bearer auth, POST /api/v1/statuses honouring Idempotency-Key, verify_credentials,
account statuses (newest first, max_id paging), /statuses/:id/source and /statuses/:id.
``plan`` scripts the next POST answers to exercise every outcome class.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

ACCOUNT_ID = "4242"


@dataclass
class FakeMastodon:
    token: str
    port: int = 0
    statuses: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[str, str] = field(default_factory=dict)
    posts: list[dict[str, Any]] = field(default_factory=list)  # every POST received
    requests: list[str] = field(default_factory=list)  # "METHOD path"
    # Next POST behaviours: "ok", "reject:<code>" (nothing created), "create_then:<code>"
    # (created, then the answer is an error = lost response), "rate_limit".
    plan: deque[str] = field(default_factory=deque)
    auth_headers_seen: list[str] = field(default_factory=list)
    _next_id: int = 1000
    _server: ThreadingHTTPServer | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ------------------------------------------------------------------ helpers
    def _create(self, body: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        sid = str(self._next_id)
        status = {
            "id": sid,
            "uri": f"http://127.0.0.1:{self.port}/users/brand/statuses/{sid}",
            "url": f"http://127.0.0.1:{self.port}/@brand/{sid}",
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "in_reply_to_id": body.get("in_reply_to_id"),
            "visibility": body.get("visibility", "public"),
            "text": body.get("status", ""),
            "favourites_count": 5,
            "reblogs_count": 3,
            "replies_count": 1,
        }
        self.statuses[sid] = status
        return status

    def created_texts(self) -> list[str]:
        return [s["text"] for s in self.statuses.values()]

    # ------------------------------------------------------------------ server
    def start(self) -> FakeMastodon:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code: int, payload: Any, headers: dict[str, str] | None = None) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def _authorized(self) -> bool:
                header = self.headers.get("authorization", "")
                outer.auth_headers_seen.append("Bearer ***" if header else "")
                return header == f"Bearer {outer.token}"

            def do_POST(self) -> None:
                path = urlsplit(self.path).path
                outer.requests.append(f"POST {path}")
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if not self._authorized():
                    self._send(401, {"error": "The access token is invalid"})
                    return
                if path != "/api/v1/statuses":
                    self._send(404, {"error": "Not found"})
                    return
                key = self.headers.get("idempotency-key")
                with outer._lock:
                    outer.posts.append({"body": body, "idempotency_key": key})
                    step = outer.plan.popleft() if outer.plan else "ok"
                    if step == "rate_limit":
                        reset = datetime.now(UTC) + timedelta(seconds=60)
                        self._send(
                            429,
                            {"error": "Too many requests"},
                            {
                                "X-RateLimit-Limit": "300",
                                "X-RateLimit-Remaining": "0",
                                "X-RateLimit-Reset": reset.isoformat().replace("+00:00", "Z"),
                            },
                        )
                        return
                    if step.startswith("reject:"):
                        self._send(int(step.split(":")[1]), {"error": "rejected"})
                        return
                    if key and key in outer.idempotency:
                        status = outer.statuses[outer.idempotency[key]]  # replay, no duplicate
                    else:
                        status = outer._create(body)
                        if key:
                            outer.idempotency[key] = status["id"]
                    if step.startswith("create_then:"):
                        self._send(int(step.split(":")[1]), {"error": "upstream"})
                        return
                    self._send(200, {k: v for k, v in status.items() if k != "text"})

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                path = parts.path
                outer.requests.append(f"GET {path}")
                if not self._authorized():
                    self._send(401, {"error": "The access token is invalid"})
                    return
                if path == "/api/v1/accounts/verify_credentials":
                    self._send(200, {"id": ACCOUNT_ID, "acct": "brand"})
                    return
                if path == f"/api/v1/accounts/{ACCOUNT_ID}/statuses":
                    query = parse_qs(parts.query)
                    max_id = query.get("max_id", [None])[0]
                    limit = int(query.get("limit", ["20"])[0])
                    items = sorted(outer.statuses.values(), key=lambda s: -int(s["id"]))
                    if max_id is not None:
                        items = [s for s in items if int(s["id"]) < int(max_id)]
                    self._send(
                        200, [{k: v for k, v in s.items() if k != "text"} for s in items[:limit]]
                    )
                    return
                segments = path.strip("/").split("/")
                if len(segments) >= 4 and segments[:3] == ["api", "v1", "statuses"]:
                    status = outer.statuses.get(segments[3])
                    if status is None:
                        self._send(404, {"error": "Record not found"})
                    elif len(segments) == 5 and segments[4] == "source":
                        self._send(
                            200, {"id": status["id"], "text": status["text"], "spoiler_text": ""}
                        )
                    else:
                        self._send(200, {k: v for k, v in status.items() if k != "text"})
                    return
                self._send(404, {"error": "Not found"})

            def log_message(self, format: str, *args: object) -> None:
                return

        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"
