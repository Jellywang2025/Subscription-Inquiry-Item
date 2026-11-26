"""实现侧测试共享的助手：空闲端口、服务线程化运行与状态文件隔离。"""

from __future__ import annotations

import http.client
import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

from quota_dashboard.sources import codex

ADMIN_PASSWORD = "impl-source-password"


@pytest.fixture(autouse=True)
def isolated_state_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """默认把持久化状态文件指向临时目录，用例不读写真实用户目录。"""
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "quota-state.json"))
    monkeypatch.setattr(codex, "AUTH_FILE", tmp_path / "codex-auth.json")


def free_port() -> int:
    """探测一个空闲的本机回环端口。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def serve_in_thread(server: ThreadingHTTPServer) -> Iterator[None]:
    """后台线程运行服务，退出时停机并回收线程。"""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        thread.join(timeout=5)


@contextmanager
def admin_password_configured() -> Iterator[None]:
    """临时配置管理员口令，退出时恢复原状。"""
    previous = os.environ.get("QUOTA_ADMIN_PASSWORD")
    os.environ["QUOTA_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("QUOTA_ADMIN_PASSWORD", None)
        else:
            os.environ["QUOTA_ADMIN_PASSWORD"] = previous


def login_headers(port: int, password: str = ADMIN_PASSWORD) -> dict[str, str]:
    """向本地服务登录并返回携带会话 Cookie 的请求头。"""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        body = urlencode({"password": password, "next": "/"}).encode("utf-8")
        connection.request(
            "POST",
            "/login",
            body=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = connection.getresponse()
        response.read()
        set_cookie = response.getheader("Set-Cookie", "")
    finally:
        connection.close()
    cookie = set_cookie.split(";")[0]
    assert cookie.startswith("quota_session="), f"登录未返回会话 Cookie：{set_cookie!r}"
    return {"Cookie": cookie}
