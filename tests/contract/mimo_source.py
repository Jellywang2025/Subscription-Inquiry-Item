"""Controllable loopback MiMo balance replies, selected by synthetic SK."""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread

from deepseek_source import Request
from test_mimo_balance import MIMO_PATH


@dataclass
class MiMoSource:
    base_url: str
    _requests: list[Request] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def record(self, request: Request) -> None:
        with self._lock:
            self._requests.append(request)

    def requests(self) -> tuple[Request, ...]:
        with self._lock:
            return tuple(self._requests)


@contextmanager
def serve_mimo(
    accounts: Mapping[str, tuple[str, dict[str, object] | None]],
) -> Iterator[MiMoSource]:
    """A configured identity with no payload returns HTTP 401."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.handle_balance()

        def do_POST(self) -> None:
            self.handle_balance()

        def handle_balance(self) -> None:
            auth = self.headers.get("Authorization", "")
            key = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else self.headers.get("api-key", "")
            identity, payload = accounts.get(key, (None, None))
            source.record(Request(self.command, self.path, payload is not None, identity=identity))
            if self.command != "GET" or self.path != MIMO_PATH:
                self.send_error(404)
                return
            if payload is None:
                self.send_error(401)
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        source = MiMoSource(f"http://127.0.0.1:{server.server_port}")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield source
        finally:
            server.shutdown()
            thread.join()
