"""工单 02 实现侧用例：DeepSeek 数据源查询与余额 API。"""

from __future__ import annotations

import email.message
import json
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, build_opener

import pytest
from conftest import free_port, serve_in_thread

from quota_dashboard import api
from quota_dashboard.server import create_server
from quota_dashboard.sources import deepseek

HTTP = build_opener(ProxyHandler({}))

OFFICIAL_REPLY: dict[str, Any] = {
    "is_available": True,
    "balance_infos": [
        {
            "currency": "CNY",
            "total_balance": "80.00",
            "topped_up_balance": "70.00",
            "granted_balance": "10.00",
        },
        {
            "currency": "USD",
            "total_balance": "5.00",
            "topped_up_balance": "4.00",
            "granted_balance": "1.00",
        },
    ],
}


@contextmanager
def balance_stub(reply: dict[str, Any]) -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    """起一个实现官方 /user/balance 契约的本地服务，返回 (base_url, 请求记录)。"""
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Authorization")))
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
        yield f"http://127.0.0.1:{server.server_port}", seen


@contextmanager
def serving_dashboard() -> Iterator[int]:
    """起一个看板服务，返回端口。"""
    httpd = create_server(free_port())
    with httpd, serve_in_thread(httpd):
        yield int(httpd.server_address[1])


@pytest.fixture
def official_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with balance_stub(OFFICIAL_REPLY) as stub:
        yield stub


def test_settings_from_env_prefers_injection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", "http://127.0.0.1:9000/")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    assert deepseek.settings_from_env() == ("http://127.0.0.1:9000", "test-key")


def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUOTA_DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert deepseek.settings_from_env() == (deepseek.DEFAULT_BASE_URL, None)


def test_fetch_balance_sends_bearer_and_returns_reply(
    official_stub: tuple[str, list[tuple[str, str | None]]],
) -> None:
    base_url, seen = official_stub
    result = deepseek.fetch_balance(base_url, "test-key")
    assert result == OFFICIAL_REPLY
    assert seen == [("/user/balance", "Bearer test-key")]


def test_fetch_balance_rejects_reply_without_balances(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with balance_stub({"is_available": True}) as (base_url, _):
        with pytest.raises(ValueError, match="balance_infos"):
            deepseek.fetch_balance(base_url, "test-key")


def test_balance_api_returns_query_result(
    monkeypatch: pytest.MonkeyPatch, official_stub: tuple[str, list[tuple[str, str | None]]]
) -> None:
    base_url, seen = official_stub
    monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", base_url)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    with serving_dashboard() as port:
        with HTTP.open(f"http://127.0.0.1:{port}/api/sources/deepseek/balance") as response:
            assert response.status == 200
            payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["data"] == OFFICIAL_REPLY
    assert payload["fetched_at"]
    assert seen == [("/user/balance", "Bearer test-key")]


def test_balance_api_reports_query_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    with serving_dashboard() as port:
        with pytest.raises(urllib.error.HTTPError) as failure:
            HTTP.open(f"http://127.0.0.1:{port}/api/sources/deepseek/balance")
    assert failure.value.code == 502
    payload: dict[str, Any] = json.loads(failure.value.read().decode("utf-8"))
    assert payload["status"] == "error"
    assert payload["error"].startswith("连接失败")


def test_describe_query_error_classifies_timeout() -> None:
    error = urllib.error.URLError(TimeoutError("timed out"))
    assert api.describe_query_error(error) == "请求超时"


def test_describe_query_error_classifies_connection_failure() -> None:
    error = urllib.error.URLError(OSError("connection refused"))
    assert api.describe_query_error(error) == "连接失败（connection refused）"


def test_describe_query_error_classifies_http_error() -> None:
    error = urllib.error.HTTPError(
        "https://api.deepseek.com/user/balance",
        503,
        "Service Unavailable",
        email.message.Message(),
        None,
    )
    assert api.describe_query_error(error) == "官方接口返回 HTTP 503"


def test_describe_query_error_passes_through_permission_error() -> None:
    hint = "Codex 未登录：请在本机完成 Codex 登录"
    assert api.describe_query_error(PermissionError(hint)) == hint


def test_describe_query_error_falls_back_to_exception_text() -> None:
    assert api.describe_query_error(ValueError("boom")) == "查询异常（boom）"


def test_balance_api_persists_and_restores_last_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_file = tmp_path / "quota-state.json"
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("QUOTA_STATE_FILE", str(state_file))
    with balance_stub(OFFICIAL_REPLY) as (base_url, _):
        monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", base_url)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
        with serving_dashboard() as port:
            with HTTP.open(f"http://127.0.0.1:{port}/api/sources/deepseek/balance") as response:
                assert response.status == 200
                first: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    assert state_file.exists()

    # 同账户查询失败：502 信封携带 last_success，保留原成功时间。
    monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", f"http://127.0.0.1:{free_port()}")
    with serving_dashboard() as port:
        with pytest.raises(urllib.error.HTTPError) as failure:
            HTTP.open(f"http://127.0.0.1:{port}/api/sources/deepseek/balance")
    assert failure.value.code == 502
    same: dict[str, Any] = json.loads(failure.value.read().decode("utf-8"))
    assert same["status"] == "error"
    assert same["last_success"]["data"] == OFFICIAL_REPLY
    assert same["last_success"]["fetched_at"] == first["fetched_at"]

    # 换账户查询失败：不恢复旧账户数据。
    monkeypatch.setenv("DEEPSEEK_API_KEY", "other-key")
    with serving_dashboard() as port:
        with pytest.raises(urllib.error.HTTPError) as failure:
            HTTP.open(f"http://127.0.0.1:{port}/api/sources/deepseek/balance")
    switched: dict[str, Any] = json.loads(failure.value.read().decode("utf-8"))
    assert switched["status"] == "error"
    assert "last_success" not in switched
