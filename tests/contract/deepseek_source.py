"""Controllable loopback implementation of the official DeepSeek balance API."""

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Event, Thread


TEST_KEY = "contract-test-key"
BALANCE_PATH = "/user/balance"


@dataclass
class BalanceReply:
    """Hold the connection open without replying when balances is None."""

    balances: list[dict[str, str]] | None
    release: Event = field(default_factory=Event)
    status: int = 200


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    authorized: bool
    identity: str | None = field(default=None, kw_only=True)


@dataclass
class DeepSeekSource:
    base_url: str
    requests: list[Request] = field(default_factory=list)
    arrived: Condition = field(default_factory=Condition)

    def wait_for_requests(self, count: int, timeout: float) -> tuple[Request, ...]:
        with self.arrived:
            self.arrived.wait_for(lambda: len(self.requests) >= count, timeout)
            return tuple(self.requests)


@contextmanager
def serve_deepseek(
    *replies: BalanceReply,
    accounts: Mapping[str, tuple[str, BalanceReply]] | None = None,
) -> Iterator[DeepSeekSource]:
    """Serve sequential replies or a reusable reply for each configured test key."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.handle_balance()

        def do_POST(self) -> None:
            self.handle_balance()

        def handle_balance(self) -> None:
            authorization = self.headers.get("Authorization")
            account = accounts.get(authorization.removeprefix("Bearer ")) if (
                accounts is not None and authorization and authorization.startswith("Bearer ")
            ) else None
            authorized = (
                account is not None and account[1].status != 401
                if accounts is not None else authorization == f"Bearer {TEST_KEY}"
            )
            request = Request(self.command, self.path, authorized, identity=account[0] if account else None)
            with source.arrived:
                index = len(source.requests)
                source.requests.append(request)
                source.arrived.notify_all()
            if request.method != "GET" or request.path != BALANCE_PATH:
                self.send_error(404)
                return
            if not request.authorized:
                self.send_error(401)
                return
            if account is not None:
                reply = account[1]
            elif index < len(replies):
                reply = replies[index]
            else:
                self.send_error(429, "Unexpected extra balance request")
                return
            if reply.status != 200:
                self.send_error(reply.status)
                return
            if reply.balances is None:
                reply.release.wait(30)
                return
            if not reply.release.wait(15):
                self.send_error(504, "Test did not release the balance response")
                return
            body = json.dumps({"is_available": True, "balance_infos": reply.balances}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        source = DeepSeekSource(f"http://127.0.0.1:{server.server_port}")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield source
        finally:
            for reply in replies:
                reply.release.set()
            if accounts is not None:
                for _, reply in accounts.values():
                    reply.release.set()
            server.shutdown()
            thread.join()
