"""Google 契约测试的可控 OAuth 和 Antigravity 进程边界。

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
import time
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
SECOND_EMAIL = "google-02-personal@example.test"
CODES = {EMAIL: CODE, SECOND_EMAIL: "contract-google-02-approval"}
IDS = {EMAIL: "contract-google-01", SECOND_EMAIL: "contract-google-02"}
ACCESS_TOKENS = {EMAIL: ACCESS_TOKEN, SECOND_EMAIL: "contract-google-02-access"}
REFRESH_TOKENS = {EMAIL: "contract-google-01-refresh", SECOND_EMAIL: "contract-google-02-refresh"}
RESET_SHORT = "2031-02-17T04:23:00Z"
RESET_WEEK = "2031-02-22T13:47:00Z"
USAGE: dict[str, object] = {
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
CREDITS: dict[str, object] = {"remaining_credits": 1347}


def cli_replies_path(log: Path, identity: str) -> Path:
    filename = "agy-replies.json" if identity == EMAIL else "agy-replies-second.json"
    return log.with_name(filename)


def cli_log_path(log: Path, identity: str) -> Path:
    # 两个账号的 CLI 可能并行启动，各自写入独立日志。
    return log.with_name("agy-calls-second.jsonl") if identity == SECOND_EMAIL else log


@dataclass
class GoogleSource:
    origin: str
    cli: Path
    cli_log: Path
    _approvals: dict[str, tuple[str, str]] = field(default_factory=dict)
    _exchanged: set[str] = field(default_factory=set)
    _revoked: set[str] = field(default_factory=set)
    _userinfo_overrides: dict[str, str | None] = field(default_factory=dict)
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

    def approve(self, authorization_url: str, identity: str = EMAIL) -> str:
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
            self._approvals[identity] = (redirect_uri, challenge)
        values = {"code": CODES[identity]}
        if params.get("state"):
            values["state"] = params["state"][0]
        return urlunsplit(redirect._replace(query=urlencode(values)))

    def exchange(self, params: dict[str, list[str]]) -> str | None:
        verifier, = params.get("code_verifier", [""])
        valid_verifier = re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
        digest = hashlib.sha256(verifier.encode()).digest()
        challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        redirect = params.get("redirect_uri", [""])[0]
        with self._lock:
            identity = None
            if params.get("client_id") == [CLIENT_ID]:
                if params.get("grant_type") == ["authorization_code"] and valid_verifier is not None:
                    for email, code in CODES.items():
                        if params.get("code") == [code] and self._approvals.get(email) == (redirect, challenge):
                            identity = email
                            self._approvals.pop(email)
                            self._exchanged.add(email)
                            self._revoked.discard(email)
                            break
                elif params.get("grant_type") == ["refresh_token"]:
                    identity = next((email for email, token in REFRESH_TOKENS.items()
                                     if params.get("refresh_token") == [token]
                                     and email in self._exchanged and email not in self._revoked), None)
            self._requests.append(Request("POST", TOKEN_PATH, identity is not None, identity=identity))
            return identity

    def set_userinfo_email(self, identity: str, email: str | None) -> None:
        """让已签发令牌的身份回执缺少邮箱，或返回另一受控邮箱。"""
        with self._lock:
            self._userinfo_overrides[identity] = email

    def revoke(self, identity: str) -> None:
        """撤销该账号的访问与刷新能力，直到收到新的授权。"""
        with self._lock:
            self._revoked.add(identity)

    def userinfo(self, authorization: str | None) -> dict[str, object] | None:
        with self._lock:
            identity = next((email for email, token in ACCESS_TOKENS.items()
                             if authorization == f"Bearer {token}" and email in self._exchanged), None)
            valid = identity is not None and identity not in self._revoked
            self._requests.append(Request("GET", USERINFO_PATH, valid, identity=identity))
            if not valid or identity is None:
                return None
            email = self._userinfo_overrides.get(identity, identity)
            if email is None:
                return {}
            return {"id": IDS[email], "email": email, "verified_email": True}

    def requests(self) -> tuple[Request, ...]:
        with self._lock:
            return tuple(self._requests)

    def cli_calls(self) -> list[tuple[str, list[str]]]:
        return [(identity, args) for _, identity, args in self.cli_events()]

    def cli_events(self) -> list[tuple[float, str, list[str]]]:
        events: list[tuple[float, str, list[str]]] = []
        for identity in CODES:
            log = cli_log_path(self.cli_log, identity)
            if log.exists():
                # 查询可能正在落日志，只读取已写完的行。
                complete = log.read_text(encoding="utf-8").rpartition("\n")[0]
                for line in complete.splitlines():
                    at, account, args = json.loads(line)
                    events.append((at, account, args))
        return sorted(events)

    def set_cli_replies(
        self, *, identity: str = EMAIL, usage: dict[str, object] | str = USAGE,
        credits: dict[str, object] | str = CREDITS,
    ) -> None:
        """配置下一轮回执。空字典表示空 data，字符串表示非零退出时的 stderr。"""
        assert identity in CODES
        path = cli_replies_path(self.cli_log, identity)
        pending = path.with_suffix(".pending")
        pending.write_text(json.dumps({"/usage": usage, "/credits": credits}), encoding="utf-8")
        pending.replace(path)


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
    with cli_log_path(log, identity).open("a", encoding="utf-8") as evidence:
        evidence.write(json.dumps([time.monotonic(), identity, args]) + "\n")
    if identity not in CODES:
        print("未找到与该授权身份匹配的测试 CLI 连接", file=sys.stderr)
        return 1
    replies = cli_replies_path(log, identity)
    payloads = json.loads(replies.read_text(encoding="utf-8")) if replies.exists() else {
        "/usage": USAGE, "/credits": CREDITS,
    }
    for command, data in payloads.items():
        if args == ["-p", command, "--output-format", "json"]:
            if isinstance(data, str):
                print(data, file=sys.stderr)
                return 1
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
            identity = source.exchange(parse_qs(raw.decode()))
            if identity is None:
                self.send_json(400, {"error": "invalid_grant"})
                return
            self.send_json(200, {
                "access_token": ACCESS_TOKENS[identity], "refresh_token": REFRESH_TOKENS[identity],
                "token_type": "Bearer", "expires_in": 3600,
            })

        def do_GET(self) -> None:
            if self.path != USERINFO_PATH:
                self.send_error(404)
                return
            payload = source.userinfo(self.headers.get("Authorization"))
            if payload is None:
                self.send_json(401, {"error": "invalid_token"})
                return
            self.send_json(200, payload)

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
