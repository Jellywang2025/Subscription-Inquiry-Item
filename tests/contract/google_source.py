"""01 票的可控 Google OAuth 和 Antigravity 进程边界。

QUOTA_GOOGLE_CLIENT_ID / QUOTA_GOOGLE_CLIENT_SECRET 提供测试 OAuth 客户端。
QUOTA_GOOGLE_TOKEN_URL / QUOTA_GOOGLE_USERINFO_URL 覆盖两个服务端端点；
向管理员展示的授权 URL 仍指向 Google。测试模拟管理员在 Google 完成登录。
QUOTA_GOOGLE_CLI 指向替身可执行文件。仅在使用此替身时，通过子进程环境
QUOTA_GOOGLE_CLI_ACCOUNT 传递该次连接已核对的邮箱，命令参数保持官方形态。
"""

import base64
import hashlib
import json
import os
import re
import shlex
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from deepseek_source import Request


AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_PATH = "/token"
USERINFO_PATH = "/oauth2/v1/userinfo"
CLIENT_ID = "contract-google.apps.googleusercontent.com"
EMAIL = "google-01-family@example.test"
CODE = "contract-google-01-approval"
ACCESS_TOKEN = "contract-google-01-access"
RESET_SHORT = "2031-02-17T04:23:00Z"
RESET_WEEK = "2031-02-22T13:47:00Z"
USAGE = {
    "plan_name": "Google AI Pro",
    "groups": [
        {
            "name": "Gemini Models",
            "models": ["Gemini Contract Flash", "Gemini Contract Pro"],
            "buckets": [
                {"window": "5h", "remaining_fraction": 0.63, "reset_time": RESET_SHORT},
                {"window": "weekly", "remaining_fraction": 0.27, "reset_time": RESET_WEEK},
            ],
        },
        {
            "name": "Contract Other Models",
            "models": ["Contract Other"],
            "buckets": [{"window": "5h", "remaining_fraction": 0.84, "reset_time": RESET_SHORT}],
        },
    ],
}
CREDITS = {"remaining_credits": 1347}


@dataclass
class GoogleSource:
    origin: str
    cli: Path
    cli_log: Path
    _approval: tuple[str, str] | None = None
    _requests: list[Request] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def environment(self) -> dict[str, str]:
        return {
            "QUOTA_GOOGLE_CLIENT_ID": CLIENT_ID,
            "QUOTA_GOOGLE_CLIENT_SECRET": "contract-google-client-secret",
            "QUOTA_GOOGLE_TOKEN_URL": self.origin + TOKEN_PATH,
            "QUOTA_GOOGLE_USERINFO_URL": self.origin + USERINFO_PATH,
            "QUOTA_GOOGLE_CLI": str(self.cli),
            "QUOTA_GOOGLE_CLI_ACCOUNT": "",
        }

    def approve(self, authorization_url: str) -> str:
        """模拟 Google 批准该请求，生成可粘贴回看板的回调 URL。"""
        scheme, host, path, query, _ = urlsplit(authorization_url)
        assert (scheme, host, path) == ("https", "accounts.google.com", "/o/oauth2/v2/auth")
        params = parse_qs(query)
        assert params.get("client_id") == [CLIENT_ID]
        assert params.get("response_type") == ["code"]
        assert params.get("code_challenge_method") == ["S256"]
        challenge, = params.get("code_challenge", [""])
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", challenge), "授权 URL 应携带 S256 PKCE challenge"
        redirect_uri, = params.get("redirect_uri", [""])
        redirect = urlsplit(redirect_uri)
        assert redirect.scheme == "http" and redirect.hostname in {"127.0.0.1", "localhost", "::1"}
        assert redirect.path == "/oauth2callback" and not redirect.query and not redirect.fragment
        with self._lock:
            self._approval = (redirect_uri, challenge)
        values = {"code": CODE}
        if params.get("state"):
            values["state"] = params["state"][0]
        return urlunsplit(redirect._replace(query=urlencode(values)))

    def exchange(self, params: dict[str, list[str]]) -> bool:
        verifier, = params.get("code_verifier", [""])
        valid_verifier = re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        redirect = params.get("redirect_uri", [""])[0]
        with self._lock:
            valid = (valid_verifier is not None and self._approval == (redirect, challenge)
                     and params.get("grant_type") == ["authorization_code"]
                     and params.get("client_id") == [CLIENT_ID] and params.get("code") == [CODE])
            self._requests.append(Request("POST", TOKEN_PATH, valid, identity=EMAIL if valid else None))
            if valid:
                self._approval = None
        return valid

    def userinfo(self, authorization: str | None) -> bool:
        with self._lock:
            exchanged = Request("POST", TOKEN_PATH, True, identity=EMAIL) in self._requests
            valid = exchanged and authorization == f"Bearer {ACCESS_TOKEN}"
            self._requests.append(Request("GET", USERINFO_PATH, valid, identity=EMAIL if valid else None))
        return valid

    def requests(self) -> tuple[Request, ...]:
        with self._lock:
            return tuple(self._requests)

    def cli_calls(self) -> list[tuple[str, list[str]]]:
        if not self.cli_log.exists():
            return []
        return [tuple(json.loads(line)) for line in self.cli_log.read_text(encoding="utf-8").splitlines()]


def write_cli(directory: Path, log: Path) -> Path:
    module = Path(__file__).resolve()
    if os.name == "nt":
        cli = directory / "agy.cmd"
        cli.write_text(f'@echo off\n"{sys.executable}" "{module}" "{log}" %*\n', encoding="utf-8")
    else:
        cli = directory / "agy"
        command = shlex.join((sys.executable, str(module), str(log)))
        cli.write_text(f'#!/bin/sh\nexec {command} "$@"\n', encoding="utf-8")
        cli.chmod(0o700)
    return cli


def respond_to_cli(log: Path, args: list[str]) -> int:
    identity = os.environ.get("QUOTA_GOOGLE_CLI_ACCOUNT", "")
    with log.open("a", encoding="utf-8") as evidence:
        evidence.write(json.dumps([identity, args]) + "\n")
    if identity != EMAIL:
        print("未找到与该授权身份匹配的测试 CLI 连接", file=sys.stderr)
        return 1
    for command, data in (("/usage", USAGE), ("/credits", CREDITS)):
        if args == ["-p", command, "--output-format", "json"]:
            print(json.dumps({"command": {"data": data}}))
            return 0
    return 2


@contextmanager
def serve_google(directory: Path) -> Iterator[GoogleSource]:
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != TOKEN_PATH:
                self.send_error(404)
                return
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if not source.exchange(parse_qs(raw.decode())):
                self.send_json(400, {"error": "invalid_grant"})
                return
            self.send_json(200, {
                "access_token": ACCESS_TOKEN, "refresh_token": "contract-google-01-refresh",
                "token_type": "Bearer", "expires_in": 3600,
            })

        def do_GET(self) -> None:
            if self.path != USERINFO_PATH:
                self.send_error(404)
                return
            if not source.userinfo(self.headers.get("Authorization")):
                self.send_json(401, {"error": "invalid_token"})
                return
            self.send_json(200, {"id": "contract-google-01", "email": EMAIL, "verified_email": True})

        def log_message(self, format: str, *args: object) -> None:
            pass

    log = directory / "agy-calls.jsonl"
    cli = write_cli(directory, log)
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        source = GoogleSource(f"http://127.0.0.1:{server.server_port}", cli, log)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield source
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    sys.exit(respond_to_cli(Path(sys.argv[1]), sys.argv[2:]))
