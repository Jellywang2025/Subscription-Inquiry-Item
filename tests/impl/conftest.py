"""实现侧测试共享的助手：空闲端口、服务线程化运行与状态文件隔离。"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from quota_dashboard.sources import codex


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
