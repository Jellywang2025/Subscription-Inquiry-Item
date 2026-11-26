"""工单 02 实现侧用例：账号 CRUD、密钥验证、余额视图与刷新编排。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from conftest import admin_password_configured, free_port, login_headers, serve_in_thread

from quota_dashboard import accounts as accounts_module
from quota_dashboard import storage
from quota_dashboard.server import create_server

ORIGINAL_KEY = "impl-deepseek-original-X987"
REPLACEMENT_KEY = "impl-deepseek-replacement-Y654"
INVALID_KEY = "impl-deepseek-invalid-Z321"
REPLY: dict[str, Any] = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "80.00", "topped_up_balance": "70.00", "granted_balance": "10.00"},
        {"currency": "USD", "total_balance": "5.00", "topped_up_balance": "4.00", "granted_balance": "1.00"},
    ],
}
ZERO_REPLY: dict[str, Any] = {
    "is_available": True,
    "balance_infos": [
        {"currency": "CNY", "total_balance": "0.00", "topped_up_balance": "0.00", "granted_balance": "0.00"},
    ],
}


@contextmanager
def balance_service(
    routes: dict[str, tuple[int, dict[str, Any] | None]],
) -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    """按 Bearer 密钥路由回执的本地余额服务，返回 (base_url, 请求记录)。"""
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            authorization = self.headers.get("Authorization")
            seen.append((self.path, authorization))
            status, payload = routes.get(authorization or "", (401, None))
            if status != 200 or payload is None:
                self.send_error(status)
                return
            body = json.dumps(payload).encode("utf-8")
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


@pytest.fixture
def deepseek_stub(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[tuple[str, str | None]]]]:
    with balance_service(
        {
            f"Bearer {ORIGINAL_KEY}": (200, REPLY),
            f"Bearer {REPLACEMENT_KEY}": (200, ZERO_REPLY),
            f"Bearer {INVALID_KEY}": (401, None),
        }
    ) as stub:
        monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", stub[0])
        yield stub


def test_deepseek_balances_maps_each_currency() -> None:
    balances = accounts_module.deepseek_balances(REPLY)
    assert balances == [
        {"currency": "CNY", "value": "80.00", "description": "充值 ¥70.00 · 赠金 ¥10.00"},
        {"currency": "USD", "value": "5.00", "description": "充值 $4.00 · 赠金 $1.00"},
    ]


def test_deepseek_balances_tolerates_missing_infos() -> None:
    assert accounts_module.deepseek_balances({}) == []
    assert accounts_module.deepseek_balances({"balance_infos": "not-a-list"}) == []


def test_create_account_verifies_key_and_persists(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, seen = deepseek_stub
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    account = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    assert account["key_tail"] == ORIGINAL_KEY[-4:]
    assert account["connection"] == "connected"
    assert account["balances"][0]["value"] == "80.00"
    assert account["updated"]
    assert seen == [("/user/balance", f"Bearer {ORIGINAL_KEY}")]
    stored = storage.load_accounts()
    assert [item["id"] for item in stored] == [account["id"]]
    assert stored[0]["key"] == ORIGINAL_KEY  # 凭据保存在服务端


def test_create_account_rejects_invalid_key_without_saving(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    with pytest.raises(accounts_module.AccountError, match="密钥验证失败.*401"):
        accounts_module.create_account("deepseek", "青岚", INVALID_KEY)
    assert storage.load_accounts() == []


def test_create_account_rejects_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    with pytest.raises(accounts_module.AccountError, match="尚未开放"):
        accounts_module.create_account("codex", "任意名", "any-key")
    assert storage.load_accounts() == []


def test_update_account_rename_only_keeps_connection(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, seen = deepseek_stub
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    account = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    seen.clear()
    renamed = accounts_module.update_account(account["id"], "澄海", "")
    assert renamed["name"] == "澄海"
    assert renamed["key_tail"] == ORIGINAL_KEY[-4:]
    assert seen == [], "仅改名不应再次验证密钥"


def test_update_account_invalid_key_keeps_original(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    account = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    with pytest.raises(accounts_module.AccountError, match="密钥验证失败.*当前连接已保留"):
        accounts_module.update_account(account["id"], "青岚", INVALID_KEY)
    stored = storage.load_accounts()
    assert stored[0]["key"] == ORIGINAL_KEY
    assert stored[0]["name"] == "青岚"


def test_update_account_valid_key_replaces_connection(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    account = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    replaced = accounts_module.update_account(account["id"], "青岚", REPLACEMENT_KEY)
    assert replaced["key_tail"] == REPLACEMENT_KEY[-4:]
    assert replaced["balances"][0]["value"] == "0.00"
    assert storage.load_accounts()[0]["key"] == REPLACEMENT_KEY


def test_update_account_unknown_id_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    with pytest.raises(accounts_module.AccountError, match="找不到"):
        accounts_module.update_account("missing", "青岚", "")


def test_delete_account_removes_record(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    account = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    assert accounts_module.delete_account(account["id"])
    assert storage.load_accounts() == []
    assert not accounts_module.delete_account(account["id"])


def test_save_account_keeps_list_position(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    first = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    with balance_service({f"Bearer {REPLACEMENT_KEY}": (200, REPLY)}) as second_source:
        monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", second_source[0])
        second = accounts_module.create_account("deepseek", "松风", REPLACEMENT_KEY)
    monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", deepseek_stub[0])
    accounts_module.update_account(first["id"], "澄海", "")
    assert [item["id"] for item in storage.load_accounts()] == [first["id"], second["id"]], (
        "改名或换密钥不应改变账号在列表中的位置"
    )


def test_refresh_accounts_updates_each_independently(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _, seen = deepseek_stub
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    first = accounts_module.create_account("deepseek", "青岚", ORIGINAL_KEY)
    with balance_service({f"Bearer {ORIGINAL_KEY}": (503, None)}) as failing:
        monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", failing[0])
        seen.clear()
        refreshed = accounts_module.refresh_accounts()
        assert [item["id"] for item in refreshed] == [first["id"]]
        assert refreshed[0]["failed"] is True
        assert "503" in refreshed[0]["error"]
        assert refreshed[0]["balances"][0]["value"] == "80.00", "失败时应保留上次成功数据"
    monkeypatch.setenv("QUOTA_DEEPSEEK_BASE_URL", deepseek_stub[0])
    seen.clear()
    refreshed = accounts_module.refresh_accounts()
    assert refreshed[0]["failed"] is False
    assert seen == [("/user/balance", f"Bearer {ORIGINAL_KEY}")]


def test_http_account_endpoints_round_trip(
    deepseek_stub: tuple[str, list[tuple[str, str | None]]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import http.client

    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    with admin_password_configured():
        port = free_port()
        httpd = create_server(port)
        with httpd, serve_in_thread(httpd):

            def call(method: str, path: str, body: str | None = None) -> tuple[int, dict[str, Any]]:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                try:
                    headers = {"Cookie": login_headers(port)["Cookie"]}
                    if body is not None:
                        headers["Content-Type"] = "application/json"
                    connection.request(method, path, body=body, headers=headers)
                    response = connection.getresponse()
                    payload = json.loads(response.read().decode("utf-8"))
                    return response.status, payload
                finally:
                    connection.close()

            status, payload = call(
                "POST", "/api/accounts", json.dumps({"provider": "deepseek", "name": "青岚", "key": ORIGINAL_KEY})
            )
            assert status == 200 and payload["status"] == "ok"
            account = payload["account"]
            assert account["name"] == "青岚" and account["key_tail"] == ORIGINAL_KEY[-4:]
            assert "key" not in account

            status, payload = call("GET", "/api/accounts")
            assert status == 200 and len(payload["accounts"]) == 1

            status, payload = call(
                "POST", f"/api/accounts/{account['id']}", json.dumps({"name": "澄海", "key": ""})
            )
            assert status == 200 and payload["account"]["name"] == "澄海"

            status, payload = call(
                "POST", "/api/accounts/missing", json.dumps({"name": "任意", "key": ""})
            )
            assert status == 400 and "找不到" in payload["error"]

            status, payload = call(
                "POST",
                "/api/accounts",
                json.dumps({"provider": "deepseek", "name": "青岚", "key": INVALID_KEY}),
            )
            assert status == 400 and "密钥验证失败" in payload["error"]

            status, payload = call("POST", "/api/refresh", "")
            assert status == 200 and payload["refreshed"] == 1

            status, payload = call("DELETE", f"/api/accounts/{account['id']}")
            assert status == 200
            status, payload = call("GET", "/api/accounts")
            assert payload["accounts"] == []
