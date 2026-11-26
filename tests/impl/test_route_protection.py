"""工单 01 实现侧用例：登录保护、回跳、会话与 API 门禁。"""

from __future__ import annotations

import http.client
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

import pytest
from conftest import free_port, serve_in_thread

from quota_dashboard import auth
from quota_dashboard.server import create_server

ADMIN_PASSWORD = "impl-admin-password"


@pytest.fixture
def protected_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[int, str]]:
    monkeypatch.setenv(auth.PASSWORD_VARIABLE, ADMIN_PASSWORD)
    port = free_port()
    httpd = create_server(port)
    with httpd, serve_in_thread(httpd):
        yield port, f"http://127.0.0.1:{port}"


def request(
    port: int,
    method: str,
    path: str,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        connection.close()


def login(port: int, password: str, next_path: str = "/") -> tuple[int, dict[str, str], bytes]:
    form = urlencode({"password": password, "next": next_path}).encode("utf-8")
    return request(
        port,
        "POST",
        "/login",
        body=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )


def session_cookie(headers: dict[str, str]) -> str:
    set_cookie = headers.get("set-cookie", "")
    assert set_cookie, "登录成功应返回 Set-Cookie"
    cookie = set_cookie.split(";")[0]
    name, _, value = cookie.partition("=")
    assert name == auth.SESSION_COOKIE
    return f"{name}={value}"


@pytest.mark.parametrize(
    "path",
    ["/", "/?view=settings", "/?platform=deepseek&q=%E9%9D%92%E5%B2%9A", "/api/accounts"],
)
def test_unauthenticated_requests_redirect_to_login(
    protected_server: tuple[int, str], path: str
) -> None:
    port, _ = protected_server
    status, headers, _ = request(port, "GET", path)
    assert status == 302, f"{path} 未登录应重定向登录页"
    location = headers["location"]
    assert location.startswith("/login?next="), location
    assert "quota_session" not in headers.get("set-cookie", "")


def test_redirect_preserves_full_target_url(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    status, headers, _ = request(port, "GET", "/?view=settings&platform=deepseek&q=a%2Bb")
    assert status == 302
    from urllib.parse import parse_qs, unquote

    next_value = unquote(headers["location"].split("next=", 1)[1])
    assert next_value == "/?view=settings&platform=deepseek&q=a%2Bb"
    assert parse_qs(urlsplit_query(next_value)) == {
        "view": ["settings"],
        "platform": ["deepseek"],
        "q": ["a+b"],
    }


def urlsplit_query(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).query


def test_login_page_serves_without_session(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    status, _, body = request(port, "GET", "/login")
    assert status == 200
    page = body.decode("utf-8")
    assert 'type="password"' in page
    assert "订阅总览" not in page
    assert "配置管理" not in page


def test_login_page_round_trips_next_target(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    status, _, body = request(
        port, "GET", "/login?next=%2F%3Fview%3Dsettings%26platform%3Ddeepseek"
    )
    assert status == 200
    page = body.decode("utf-8")
    assert 'name="next" value="/?view=settings&amp;platform=deepseek"' in page


def test_login_rejects_wrong_password(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    status, _, body = login(port, "wrong-password")
    assert status == 200
    assert "口令不正确" in body.decode("utf-8")


def test_login_rejects_when_password_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(auth.PASSWORD_VARIABLE, raising=False)
    port = free_port()
    httpd = create_server(port)
    with httpd, serve_in_thread(httpd):
        status, _, body = login(port, "any-password")
        assert status == 200
        assert "口令不正确" in body.decode("utf-8")


def test_login_success_sets_cookie_and_redirects_to_next(
    protected_server: tuple[int, str],
) -> None:
    port, _ = protected_server
    status, headers, _ = login(port, ADMIN_PASSWORD, next_path="/?view=settings")
    assert status == 302
    assert headers["location"] == "/?view=settings"
    cookie = session_cookie(headers)
    assert "httponly" in headers["set-cookie"].lower()

    status, _, body = request(port, "GET", "/?view=settings", headers={"Cookie": cookie})
    assert status == 200
    assert "订阅管理" in body.decode("utf-8")


@pytest.mark.parametrize("next_path", ["//evil.example/", "https://evil.example/"])
def test_login_sanitizes_non_local_next(
    protected_server: tuple[int, str], next_path: str
) -> None:
    port, _ = protected_server
    status, headers, _ = login(port, ADMIN_PASSWORD, next_path=next_path)
    assert status == 302
    assert headers["location"] == "/"


def test_login_page_redirects_authenticated_visitor_home(
    protected_server: tuple[int, str],
) -> None:
    port, _ = protected_server
    _, headers, _ = login(port, ADMIN_PASSWORD)
    cookie = session_cookie(headers)
    status, headers, _ = request(port, "GET", "/login", headers={"Cookie": cookie})
    assert status == 302
    assert headers["location"] == "/"


def test_static_assets_serve_without_session(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    status, _, _ = request(port, "GET", "/styles.css")
    assert status == 200
    status, _, _ = request(port, "GET", "/app.js")
    assert status == 200


def test_accounts_api_requires_session(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    _, headers, _ = login(port, ADMIN_PASSWORD)
    cookie = session_cookie(headers)

    status, _, body = request(port, "GET", "/api/accounts", headers={"Cookie": cookie})
    assert status == 200
    import json

    payload: dict[str, Any] = json.loads(body.decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["accounts"] == []


def test_account_create_returns_unavailable_for_now(
    protected_server: tuple[int, str],
) -> None:
    port, _ = protected_server
    _, headers, _ = login(port, ADMIN_PASSWORD)
    cookie = session_cookie(headers)
    status, _, body = request(
        port,
        "POST",
        "/api/accounts",
        body=b"{}",
        headers={"Cookie": cookie, "Content-Type": "application/json"},
    )
    assert status == 501
    assert "暂时无法保存" in body.decode("utf-8")


def test_unknown_post_returns_not_found(protected_server: tuple[int, str]) -> None:
    port, _ = protected_server
    status, _, _ = request(port, "POST", "/api/nothing")
    assert status == 404
