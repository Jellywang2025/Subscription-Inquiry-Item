"""Loopback Codex issuer and usage API for the subscription browser contract.

QUOTA_CODEX_AUTH_BASE_URL points to the issuer root; QUOTA_CODEX_BASE_URL
points to its /backend-api. The official CLI's deviceauth and OAuth exchange
shape is reused. HTTP 410 / expired_token is the fixture's explicit expiry
signal so the browser test does not have to wait for the official timeout.
"""

import base64
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Condition, Thread
from urllib.parse import parse_qs

from deepseek_source import Request
from test_codex_windows import CODEX_USER_AGENT_PREFIX, USAGE_BASE_PATH, USAGE_PATH


USERCODE_PATH = "/api/accounts/deviceauth/usercode"
POLL_PATH = "/api/accounts/deviceauth/token"
EXCHANGE_PATH = "/oauth/token"
VERIFICATION_URL = "https://auth.openai.com/codex/device"
RESET_AT = int((datetime.now(UTC) + timedelta(hours=3)).replace(second=0, microsecond=0).timestamp())


@dataclass(frozen=True)
class Attempt:
    number: int
    user_code: str

    @property
    def device_id(self) -> str:
        return f"contract-device-{self.number}"

    @property
    def token(self) -> str:
        return f"contract-codex-access-{self.number}"


FIRST = Attempt(1, "Q7KM-2G4T")
SECOND = Attempt(2, "H8DR-6V3P")
THIRD = Attempt(3, "J9WF-5X2N")
ATTEMPTS = (FIRST, SECOND, THIRD)


@dataclass
class CodexDeviceSource:
    origin: str
    issued: int = 0
    outcomes: dict[Attempt, str] = field(default_factory=dict)
    revoked: set[Attempt] = field(default_factory=set)
    requests: list[Request] = field(default_factory=list)
    changed: Condition = field(default_factory=Condition)

    def resolve(self, attempt: Attempt, outcome: str) -> None:
        with self.changed:
            self.outcomes[attempt] = outcome

    def revoke(self, attempt: Attempt) -> None:
        with self.changed:
            self.revoked.add(attempt)

    def snapshot(self) -> tuple[Request, ...]:
        with self.changed:
            return tuple(self.requests)

    def received(self, request: Request, *, after: int = 0) -> bool:
        with self.changed:
            return self.changed.wait_for(lambda: request in self.requests[after:], timeout=8)


def id_token() -> str:
    claims = {"https://api.openai.com/auth": {"chatgpt_account_id": "contract-account", "chatgpt_plan_type": "pro"}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.{payload}.c2ln"


@contextmanager
def serve_codex_device() -> Iterator[CodexDeviceSource]:
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def record(self, valid: bool, attempt: Attempt | None = None) -> None:
            # Echo only a synthetic device code in diagnostics, never an authorization header.
            source.requests.append(Request(self.command, self.path, valid, identity=attempt.user_code if attempt else None))
            source.changed.notify_all()

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if self.headers.get_content_type() == "application/x-www-form-urlencoded":
                payload = {key: values[0] for key, values in parse_qs(raw.decode()).items()}
            else:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    self.send_json(400, {"error": "invalid_request"})
                    return
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "invalid_request"})
                return
            with source.changed:
                reply = self.device_reply(payload)
            self.send_json(*reply)

        def device_reply(self, payload: Mapping[str, object]) -> tuple[int, dict[str, object]]:
            if self.path == USERCODE_PATH:
                if not payload.get("client_id") or source.issued >= len(ATTEMPTS):
                    self.record(False)
                    return 400, {"error": "invalid_request"}
                attempt = ATTEMPTS[source.issued]
                source.issued += 1
                self.record(True, attempt)
                return 200, {"device_auth_id": attempt.device_id, "user_code": attempt.user_code, "interval": "1"}

            if self.path == POLL_PATH:
                attempt = next((item for item in ATTEMPTS[:source.issued]
                                if payload.get("device_auth_id") == item.device_id
                                and payload.get("user_code") == item.user_code), None)
                self.record(attempt is not None, attempt)
                if attempt is None:
                    return 400, {"error": "invalid_request"}
                outcome = source.outcomes.get(attempt)
                if outcome == "expired":
                    return 410, {"error": "expired_token"}
                if outcome != "approved":
                    return 403, {"error": "authorization_pending"}
                return 200, {
                    "authorization_code": f"approval-{attempt.number}",
                    "code_challenge": f"challenge-{attempt.number}",
                    "code_verifier": f"verifier-{attempt.number}",
                }

            if self.path == EXCHANGE_PATH:
                attempt = next((item for item, outcome in source.outcomes.items()
                                if outcome == "approved" and payload.get("code") == f"approval-{item.number}"
                                and payload.get("code_verifier") == f"verifier-{item.number}"), None)
                valid = (
                    attempt is not None and payload.get("grant_type") == "authorization_code"
                    and bool(payload.get("client_id"))
                )
                self.record(valid, attempt)
                if not valid or attempt is None:
                    return 400, {"error": "invalid_grant"}
                return 200, {"access_token": attempt.token, "refresh_token": f"contract-refresh-{attempt.number}",
                             "id_token": id_token(), "token_type": "Bearer", "expires_in": 3600}

            self.record(False)
            return 404, {"error": "not_found"}

        def do_GET(self) -> None:
            with source.changed:
                attempt = next((item for item, outcome in source.outcomes.items() if outcome == "approved"
                                and self.headers.get("Authorization") == f"Bearer {item.token}"), None)
                valid = (self.path == USAGE_PATH and attempt is not None and attempt not in source.revoked
                         and self.headers.get("User-Agent", "").startswith(CODEX_USER_AGENT_PREFIX))
                self.record(valid, attempt)
            if self.path != USAGE_PATH:
                self.send_json(404, {"error": "not_found"})
            elif not valid or attempt is None:
                self.send_json(401, {"error": "invalid_token"})
            else:
                self.send_json(200, {"rate_limit": {
                    "primary_window": {"used_percent": 25 if attempt != THIRD else 40,
                                       "limit_window_seconds": 18000, "reset_at": RESET_AT},
                    "secondary_window": {"used_percent": 70, "limit_window_seconds": 604800,
                                         "reset_at": None, "reset_after_seconds": None},
                }, "plan_type": "pro"})

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        source = CodexDeviceSource(f"http://127.0.0.1:{server.server_port}")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield source
        finally:
            server.shutdown()
            thread.join()
