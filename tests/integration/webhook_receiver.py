"""A local HTTP receiver standing in for n8n in the webhook tests (127.0.0.1 only)."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Received:
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class Receiver:
    port: int = 0
    received: list[Received] = field(default_factory=list)
    replies: deque[int] = field(default_factory=deque)  # next statuses; 200 when empty
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def start(self) -> Receiver:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("content-length", "0"))
                body = self.rfile.read(length)
                outer.received.append(
                    Received(self.path, {k.lower(): v for k, v in self.headers.items()}, body)
                )
                status = outer.replies.popleft() if outer.replies else 200
                self.send_response(status)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, format: str, *args: object) -> None:
                return

        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/webhook/omnigrowth"
