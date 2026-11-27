"""工单 05 实现侧用例：Codex 用量查询、CLI 登录回退与用量 API。"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from conftest import admin_password_configured, free_port, login_headers, serve_in_thread

from quota_dashboard.server import create_server
from quota_dashboard.sources import codex

HTTP = build_opener(ProxyHandler({}))

OFFICIAL_REPLY: dict[str, Any] = {
    "rate_limit": {
        "primary_window": {
            "used_percent": 25,
            "limit_window_seconds": 300,
            "reset_after_seconds": 120,
            "reset_at": 1795639643,
        },
        "secondary_window": {
            "used_percent": 70,
            "limit_window_seconds": 604800,
            "reset_after_seconds": None,
            "reset_at": None,
        },
    },
    "plan_type": "pro",
}


@contextmanager
def usage_stub(
    reply: dict[str, Any],
) -> Iterator[tuple[str, list[tuple[str, str | None, str | None]]]]:
    """起一个实现官方 /backend-api/wham/usage 契约的本地服务。"""
    seen: list[tuple[str, str | None, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization"), self.headers.get("User-Agent")))
            body = json.dumps(reply).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    with server, serve_in_thread(server):
        yield f"http://127.0.0.1:{server.server_port}/backend-api", seen


@contextmanager
def serving_dashboard() -> Iterator[int]:
    with admin_password_configured():
        httpd = create_server(free_port())
        with httpd, serve_in_thread(httpd):
            yield int(httpd.server_address[1])


def api_request(port: int, path: str) -> Request:
    """带管理员会话的 API 请求。"""
    return Request(
        f"http://127.0.0.1:{port}{path}", headers=login_headers(port)
    )


def test_settings_from_env_prefers_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_CODEX_BASE_URL", "http://127.0.0.1:9000/backend-api/")
    monkeypatch.setenv("QUOTA_CODEX_ACCESS_TOKEN", "test-token")
    assert codex.settings_from_env() == ("http://127.0.0.1:9000/backend-api", "test-token")


def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUOTA_CODEX_BASE_URL", raising=False)
    monkeypatch.delenv("QUOTA_CODEX_ACCESS_TOKEN", raising=False)
    assert codex.settings_from_env() == (codex.DEFAULT_BASE_URL, None)


def test_access_token_from_auth_file_reads_tokens(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "cli-token"}}), encoding="utf-8")
    assert codex.access_token_from_auth_file(auth) == "cli-token"


def test_access_token_from_auth_file_falls_back_to_api_key(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"OPENAI_API_KEY": "sk-cli"}), encoding="utf-8")
    assert codex.access_token_from_auth_file(auth) == "sk-cli"


def test_access_token_from_auth_file_tolerates_missing_or_broken(tmp_path: Path) -> None:
    assert codex.access_token_from_auth_file(tmp_path / "absent.json") is None
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    assert codex.access_token_from_auth_file(broken) is None


def test_effective_token_prefers_injection_over_auth_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUOTA_CODEX_ACCESS_TOKEN", "injected-token")
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "cli-token"}}), encoding="utf-8")
    assert codex.effective_token(auth) == "injected-token"


def test_effective_token_falls_back_to_auth_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("QUOTA_CODEX_ACCESS_TOKEN", raising=False)
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "cli-token"}}), encoding="utf-8")
    assert codex.effective_token(auth) == "cli-token"
    assert codex.effective_token(tmp_path / "absent.json") is None


def test_account_id_from_auth_file_reads_tokens(tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"tokens": {"access_token": "cli-token", "account_id": "acct-1"}}),
        encoding="utf-8",
    )
    assert codex.account_id_from_auth_file(auth) == "acct-1"


def test_account_id_from_auth_file_tolerates_missing(tmp_path: Path) -> None:
    assert codex.account_id_from_auth_file(tmp_path / "absent.json") is None
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "cli-token"}}), encoding="utf-8")
    assert codex.account_id_from_auth_file(auth) is None
    empty = tmp_path / "empty-account.json"
    empty.write_text(json.dumps({"tokens": {"account_id": ""}}), encoding="utf-8")
    assert codex.account_id_from_auth_file(empty) is None
    broken = tmp_path / "broken.json"
    broken.write_text("not json", encoding="utf-8")
    assert codex.account_id_from_auth_file(broken) is None


def test_request_headers_include_ua_and_account_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps({"tokens": {"access_token": "cli-token", "account_id": "acct-1"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(codex, "AUTH_FILE", auth)
    assert codex.request_headers() == {"User-Agent": codex.USER_AGENT, "chatgpt-account-id": "acct-1"}


def test_request_headers_without_account_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(codex, "AUTH_FILE", tmp_path / "absent.json")
    assert codex.request_headers() == {"User-Agent": codex.USER_AGENT}


def test_fetch_usage_sends_bearer_ua_and_returns_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with usage_stub(OFFICIAL_REPLY) as (base_url, seen):
        result = codex.fetch_usage(base_url, "test-token")
    assert result == OFFICIAL_REPLY
    assert seen == [("/backend-api/wham/usage", "Bearer test-token", codex.USER_AGENT)]


def test_fetch_usage_without_token_raises_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.delenv("QUOTA_CODEX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(codex, "AUTH_FILE", tmp_path / "absent.json")
    with pytest.raises(PermissionError, match="未登录"):
        codex.fetch_usage("http://127.0.0.1:9", None)


def test_fetch_usage_rejects_reply_without_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with usage_stub({"plan_type": "pro"}) as (base_url, _):
        with pytest.raises(ValueError, match="rate_limit"):
            codex.fetch_usage(base_url, "test-token")


def test_usage_api_returns_official_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with usage_stub(OFFICIAL_REPLY) as (base_url, _):
        monkeypatch.setenv("QUOTA_CODEX_BASE_URL", base_url)
        monkeypatch.setenv("QUOTA_CODEX_ACCESS_TOKEN", "test-token")
        with serving_dashboard() as port:
            with HTTP.open(api_request(port, "/api/sources/codex/balance")) as response:
                assert response.status == 200
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["data"] == OFFICIAL_REPLY
    assert payload["shared"] is None
    assert payload["fetched_at"]


SHARED_WINDOW = {"used_percent": 30, "limit_window_seconds": 3600, "reset_after_seconds": None, "reset_at": 1795639643}


def usage_with_buckets(buckets: dict[str, Any]) -> dict[str, Any]:
    return {**OFFICIAL_REPLY, "rate_limits_by_limit_id": buckets}


@pytest.mark.parametrize(
    ("limit_id", "limit_name"),
    [("chatgpt_codex_shared", "Usage"), ("combined_usage", "ChatGPT and Codex shared")],
    ids=["shared-key", "shared-name"],
)
def test_shared_quota_view_matches_marked_buckets(limit_id: str, limit_name: str) -> None:
    payload = usage_with_buckets({
        "codex_extra": {"limit_name": "Codex shared", "primary_window": {"used_percent": 45, "reset_at": None}},
        limit_id: {"limit_name": limit_name, "primary_window": SHARED_WINDOW},
    })
    view = codex.shared_quota_view(payload)
    assert view == {
        "label": "ChatGPT 与 Codex 共享",
        "remaining_percent": 70,
        "reset_at": 1795639643,
    }


def test_shared_quota_view_ignores_partial_platform_bucket() -> None:
    payload = usage_with_buckets({
        "codex_extra": {"limit_name": "Codex shared", "primary_window": {"used_percent": 45, "reset_at": None}},
    })
    assert codex.shared_quota_view(payload) is None


def test_shared_quota_view_requires_all_markers() -> None:
    assert codex.shared_quota_view(usage_with_buckets({"shared": {"limit_name": "shared"}})) is None
    assert codex.shared_quota_view(usage_with_buckets({"chatgpt": {"limit_name": "chatgpt"}})) is None


def test_shared_quota_view_handles_missing_or_malformed_buckets() -> None:
    assert codex.shared_quota_view(OFFICIAL_REPLY) is None
    assert codex.shared_quota_view(usage_with_buckets({"chatgpt_codex_shared": {"limit_name": "shared"}})) is None
    assert codex.shared_quota_view(usage_with_buckets({"chatgpt_codex_shared": "broken"})) is None


def test_shared_quota_view_clamps_remaining_to_zero() -> None:
    payload = usage_with_buckets({
        "chatgpt_codex_shared": {
            "limit_name": "shared",
            "primary_window": {**SHARED_WINDOW, "used_percent": 130},
        },
    })
    view = codex.shared_quota_view(payload)
    assert view is not None
    assert view["remaining_percent"] == 0


def test_usage_api_returns_shared_view(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = usage_with_buckets({
        "chatgpt_codex_shared": {"limit_name": "Usage", "primary_window": SHARED_WINDOW},
    })
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with usage_stub(reply) as (base_url, _):
        monkeypatch.setenv("QUOTA_CODEX_BASE_URL", base_url)
        monkeypatch.setenv("QUOTA_CODEX_ACCESS_TOKEN", "test-token")
        with serving_dashboard() as port:
            with HTTP.open(api_request(port, "/api/sources/codex/balance")) as response:
                assert response.status == 200
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    assert payload["shared"] == {
        "label": "ChatGPT 与 Codex 共享",
        "remaining_percent": 70,
        "reset_at": 1795639643,
    }
