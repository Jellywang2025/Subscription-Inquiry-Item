"""工单 04 实现侧用例：Codex 设备码授权、窗口转换与登录失效。"""

from __future__ import annotations

import json
import threading
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import free_port, serve_in_thread

from quota_dashboard import accounts as accounts_module
from quota_dashboard import codex_auth, storage
from quota_dashboard.server import create_server

RESET_AT = 1769460000  # 固定时间戳：2026-01-26 附近，避免测试对当前时间敏感
USAGE_REPLY: dict[str, Any] = {
    "rate_limit": {
        "primary_window": {"used_percent": 25, "limit_window_seconds": 18000, "reset_at": RESET_AT},
        "secondary_window": {"used_percent": 70, "limit_window_seconds": 604800, "reset_at": None},
    },
    "plan_type": "pro",
}


@contextmanager
def device_service(
    *, approve: threading.Event, expired: bool = False, revoke: bool = False
) -> Iterator[str]:
    """按官方 CLI 形态提供设备码签发、轮询、交换与额度查询的可控服务。"""
    state = {"issued": 0, "token": "impl-codex-token-1"}

    class Handler(BaseHTTPRequestHandler):
        def send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            if self.path == "/api/accounts/deviceauth/usercode":
                state["issued"] += 1
                self.send_json(200, {"device_auth_id": f"device-{state['issued']}", "user_code": "IMPL-1234", "interval": "1"})
            elif self.path == "/api/accounts/deviceauth/token":
                if expired:
                    self.send_json(410, {"error": "expired_token"})
                elif not approve.is_set():
                    self.send_json(403, {"error": "authorization_pending"})
                else:
                    self.send_json(200, {"authorization_code": "code-1", "code_verifier": "verifier-1"})
            elif self.path == "/oauth/token":
                self.send_json(200, {"access_token": state["token"], "refresh_token": "r", "token_type": "Bearer", "expires_in": 3600})
            else:
                self.send_json(404, {"error": "not_found"})

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/backend-api/wham/usage":
                self.send_json(404, {"error": "not_found"})
            elif revoke or self.headers.get("Authorization") != f"Bearer {state['token']}":
                self.send_json(401, {"error": "invalid_token"})
            else:
                self.send_json(200, USAGE_REPLY)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    with server, serve_in_thread(server):
        yield f"http://127.0.0.1:{server.server_port}"


@pytest.fixture
def codex_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    return tmp_path


def test_window_label_groups_durations() -> None:
    assert accounts_module.window_label(18000) == "5 小时窗口"
    assert accounts_module.window_label(604800) == "每周窗口"
    assert accounts_module.window_label(86400) == "1 天窗口"
    assert accounts_module.window_label(None) == "额度窗口"
    assert accounts_module.window_label(0) == "额度窗口"


def test_codex_windows_computes_remaining_and_reset() -> None:
    from datetime import datetime

    windows = accounts_module.codex_windows(USAGE_REPLY)
    assert windows[0]["label"] == "5 小时窗口"
    assert windows[0]["remaining"] == 75
    assert windows[0]["reset"] == datetime.fromtimestamp(RESET_AT).astimezone().isoformat(timespec="seconds")
    assert windows[1] == {"label": "每周窗口", "remaining": 30, "reset": ""}


def test_create_codex_account_without_key(codex_env: Path) -> None:
    account = accounts_module.create_account(
        "codex", "云杉", "", {"plan": "ChatGPT Pro", "amount": "200", "currency": "USD", "cycle": "每月", "renewal": "2030-01-19"}
    )
    assert account["connection"] == "pending"
    assert account["plan"] == "ChatGPT Pro"
    assert account["windows"] == []
    assert "key" not in account


def test_update_codex_metadata_keeps_connection(codex_env: Path) -> None:
    account = accounts_module.create_account("codex", "云杉", "")
    account["connection"] = "connected"
    account["token"] = "tok"
    storage.save_account(account)
    updated = accounts_module.update_account(
        account["id"], "松涛", "", {"plan": "年度", "amount": "328", "currency": "CNY", "cycle": "每年", "renewal": "2030-02-20"}
    )
    assert updated["name"] == "松涛"
    assert updated["connection"] == "connected"
    assert updated["token"] == "tok"
    assert updated["plan"] == "年度"


def test_device_flow_authorizes_and_queries_usage(
    monkeypatch: pytest.MonkeyPatch, codex_env: Path
) -> None:
    approve = threading.Event()
    with device_service(approve=approve) as base:
        monkeypatch.setenv("QUOTA_CODEX_AUTH_BASE_URL", base)
        monkeypatch.setenv("QUOTA_CODEX_BASE_URL", f"{base}/backend-api")
        account = accounts_module.create_account("codex", "云杉", "")
        codex_auth.MANAGER.stop(account["id"])

        session = codex_auth.MANAGER.start(account["id"])
        assert session.user_code == "IMPL-1234"
        assert codex_auth.MANAGER.status(account["id"])["status"] in {"waiting", "connected"}

        approve.set()
        state = codex_auth.MANAGER.check(account["id"])
        deadline_state = state
        attempts = 0
        while deadline_state["status"] != "connected" and attempts < 20:
            deadline_state = codex_auth.MANAGER.check(account["id"])
            attempts += 1
        assert deadline_state["status"] == "connected", f"手动检查应完成授权，实得 {deadline_state}"

        stored = storage.load_accounts()[0]
        assert stored["connection"] == "connected"
        assert stored["token"] == "impl-codex-token-1"
        assert stored["windows"][0]["remaining"] == 75
        assert stored["updated"]
        codex_auth.MANAGER.stop(account["id"])


def test_device_flow_reports_expiry(monkeypatch: pytest.MonkeyPatch, codex_env: Path) -> None:
    approve = threading.Event()
    with device_service(approve=approve, expired=True) as base:
        monkeypatch.setenv("QUOTA_CODEX_AUTH_BASE_URL", base)
        account = accounts_module.create_account("codex", "云杉", "")
        codex_auth.MANAGER.stop(account["id"])
        codex_auth.MANAGER.start(account["id"])
        state = codex_auth.MANAGER.check(account["id"])
        assert state["status"] == "expired"
        assert storage.load_accounts()[0]["connection"] == "pending"
        codex_auth.MANAGER.stop(account["id"])


def test_expired_login_marks_reauthorization(
    monkeypatch: pytest.MonkeyPatch, codex_env: Path
) -> None:
    account = accounts_module.create_account("codex", "云杉", "")
    account["connection"] = "connected"
    account["token"] = "impl-codex-token-1"
    storage.save_account(account)
    approve = threading.Event()
    with device_service(approve=approve, revoke=True) as base:
        monkeypatch.setenv("QUOTA_CODEX_BASE_URL", f"{base}/backend-api")
        accounts_module.refresh_accounts()
    stored = storage.load_accounts()[0]
    assert stored["connection"] == "expired"
    assert "重新授权" in stored["error"]


def test_refresh_skips_unauthorized_codex(codex_env: Path) -> None:
    accounts_module.create_account("codex", "云杉", "")
    refreshed = accounts_module.refresh_accounts()
    assert refreshed == []


def test_delete_stops_device_session(monkeypatch: pytest.MonkeyPatch, codex_env: Path) -> None:
    import http.client

    approve = threading.Event()
    with device_service(approve=approve) as base:
        monkeypatch.setenv("QUOTA_CODEX_AUTH_BASE_URL", base)
        monkeypatch.setenv("QUOTA_CODEX_BASE_URL", f"{base}/backend-api")
        monkeypatch.setenv("QUOTA_ADMIN_PASSWORD", "impl-admin")
        account = accounts_module.create_account("codex", "云杉", "")
        port = free_port()
        httpd = create_server(port)
        with httpd, serve_in_thread(httpd):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                connection.request("POST", "/login", body=b"password=impl-admin&next=/",
                                    headers={"Content-Type": "application/x-www-form-urlencoded"})
                response = connection.getresponse()
                response.read()
                set_cookie = response.getheader("Set-Cookie") or ""
                assert set_cookie, "登录应返回会话 Cookie"
                cookie = set_cookie.split(";")[0]

                connection.request("POST", f"/api/accounts/{account['id']}/device/start",
                                   body=b"{}", headers={"Cookie": cookie, "Content-Type": "application/json"})
                response = connection.getresponse()
                assert response.status == 200

                connection.request("DELETE", f"/api/accounts/{account['id']}",
                                   headers={"Cookie": cookie})
                response = connection.getresponse()
                assert response.status == 200
                assert codex_auth.MANAGER.status(account["id"])["status"] == "idle"
            finally:
                connection.close()
