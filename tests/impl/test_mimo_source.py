"""工单 04 实现侧用例：MiMo 数据源查询、可动用判式与余额 API。"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.request import ProxyHandler, Request, build_opener

import pytest
from conftest import admin_password_configured, free_port, login_headers, serve_in_thread

from quota_dashboard.server import create_server
from quota_dashboard.sources import mimo

HTTP = build_opener(ProxyHandler({}))

OFFICIAL_REPLY: dict[str, Any] = {
    "object": "balance",
    "is_available": True,
    "balance_infos": [
        {
            "currency": "CNY",
            "cash_balance": "100.00",
            "gift_balance": "20.00",
            "frozen_balance": "20.00",
            "credit_limit": "500.00",
            "credit_available": "80.00",
            "total_available": "200.00",
        }
    ],
}


@contextmanager
def balance_stub(reply: dict[str, Any]) -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    """起一个实现官方 /v1/platform/balance 契约的本地服务，返回 (base_url, 请求记录)。"""
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
    """起一个配置了管理员口令的看板服务，返回端口。"""
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
    monkeypatch.setenv("QUOTA_MIMO_BASE_URL", "http://127.0.0.1:9000/")
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    assert mimo.settings_from_env() == ("http://127.0.0.1:9000", "sk-test")


def test_settings_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUOTA_MIMO_BASE_URL", raising=False)
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    assert mimo.settings_from_env() == (mimo.DEFAULT_BASE_URL, None)


def test_fetch_balance_sends_bearer_and_returns_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with balance_stub(OFFICIAL_REPLY) as (base_url, seen):
        result = mimo.fetch_balance(base_url, "sk-test")
    assert result == OFFICIAL_REPLY
    assert seen == [(mimo.BALANCE_PATH, "Bearer sk-test")]


def test_fetch_balance_rejects_reply_without_infos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with balance_stub({"object": "balance"}) as (base_url, _):
        with pytest.raises(ValueError, match="balance_infos"):
            mimo.fetch_balance(base_url, "sk-test")


@pytest.mark.parametrize(
    ("total", "frozen", "expected"),
    [("200.00", "20.00", "180.00"), ("15.00", "20.00", "0.00"), ("20.00", "20.00", "0.00")],
    ids=["after-freeze", "negative-clamped", "exact-zero"],
)
def test_available_amount_applies_official_rule(
    total: str, frozen: str, expected: str
) -> None:
    payload = {"balance_infos": [{"total_available": total, "frozen_balance": frozen}]}
    assert mimo.available_amount(payload) == expected


def test_available_amount_defaults_to_zero_without_infos() -> None:
    assert mimo.available_amount({"balance_infos": []}) == "0.00"


def test_balance_api_returns_official_data_and_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    with balance_stub(OFFICIAL_REPLY) as (base_url, _):
        monkeypatch.setenv("QUOTA_MIMO_BASE_URL", base_url)
        monkeypatch.setenv("MIMO_API_KEY", "sk-test")
        with serving_dashboard() as port:
            with HTTP.open(api_request(port, "/api/sources/mimo/balance")) as response:
                assert response.status == 200
                payload: dict[str, Any] = json.loads(response.read().decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["data"] == OFFICIAL_REPLY
    assert payload["available"] == "180.00"
    assert payload["fetched_at"]


def test_fetch_balance_without_api_key_raises_hint_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    # 指向无人监听的端口：若守卫缺失会先发起连接并报 URLError，而不是补配置提示。
    with pytest.raises(PermissionError, match="MiMo API Key 未配置：请在本机配置 MIMO_API_KEY"):
        mimo.fetch_balance(f"http://127.0.0.1:{free_port()}", None)


def test_balance_api_reports_missing_key_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("QUOTA_MIMO_BASE_URL", f"http://127.0.0.1:{free_port()}")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)
    with serving_dashboard() as port:
        with pytest.raises(urllib.error.HTTPError) as failure:
            HTTP.open(api_request(port, "/api/sources/mimo/balance"))
    assert failure.value.code == 502
    payload: dict[str, Any] = json.loads(failure.value.read().decode("utf-8"))
    assert payload["status"] == "error"
    assert payload["error"] == "MiMo API Key 未配置：请在本机配置 MIMO_API_KEY"


def test_balance_api_reports_query_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("QUOTA_MIMO_BASE_URL", f"http://127.0.0.1:{free_port()}")
    monkeypatch.setenv("MIMO_API_KEY", "sk-test")
    with serving_dashboard() as port:
        with pytest.raises(urllib.error.HTTPError) as failure:
            HTTP.open(api_request(port, "/api/sources/mimo/balance"))
    assert failure.value.code == 502
    payload: dict[str, Any] = json.loads(failure.value.read().decode("utf-8"))
    assert payload["status"] == "error"
    assert payload["error"].startswith("连接失败")
