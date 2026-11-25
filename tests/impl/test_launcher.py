"""工单 01 实现侧用例：启动编排（探测复用、打开浏览器、起服务）。"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import free_port, serve_in_thread

from quota_dashboard import launcher
from quota_dashboard.server import create_server


@pytest.fixture
def opened_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(
        launcher, "open_in_browser", lambda url: bool(urls.append(url)) or True
    )
    return urls


@pytest.fixture
def served_servers(monkeypatch: pytest.MonkeyPatch) -> list[ThreadingHTTPServer]:
    """记录 start() 创建的服务实例，不阻塞在 serve_forever。

    只对 start() 新建的实例遮蔽 serve_forever；fixture 中的真实服务
    保持原方法，BaseServer.shutdown() 的关闭协议不受影响。
    """
    servers: list[ThreadingHTTPServer] = []
    original_create = launcher.create_server

    def recording_create(port: int) -> ThreadingHTTPServer:
        httpd = original_create(port)
        httpd.serve_forever = lambda poll_interval=0.5: servers.append(httpd)  # type: ignore[method-assign]
        return httpd

    monkeypatch.setattr(launcher, "create_server", recording_create)
    return servers


@pytest.fixture
def running_dashboard() -> Iterator[tuple[ThreadingHTTPServer, int]]:
    port = free_port()
    httpd = create_server(port)
    with httpd, serve_in_thread(httpd):
        yield httpd, port


def test_dashboard_url_formats_loopback_address() -> None:
    assert launcher.dashboard_url(8765) == "http://127.0.0.1:8765/"


def test_is_dashboard_running_true_for_running_dashboard(
    running_dashboard: tuple[ThreadingHTTPServer, int],
) -> None:
    _, port = running_dashboard
    assert launcher.is_dashboard_running(port)


def test_is_dashboard_running_false_for_free_port() -> None:
    assert not launcher.is_dashboard_running(free_port())


def test_is_dashboard_running_false_for_foreign_http_server() -> None:
    port = free_port()
    foreign = ThreadingHTTPServer(
        ("127.0.0.1", port), partial(BaseHTTPRequestHandler)
    )
    with foreign, serve_in_thread(foreign):
        assert not launcher.is_dashboard_running(port)


def test_start_serves_and_opens_browser_on_free_port(
    opened_urls: list[str], served_servers: list[ThreadingHTTPServer]
) -> None:
    port = free_port()
    assert launcher.start(port) == 0
    assert opened_urls == [launcher.dashboard_url(port)]
    assert len(served_servers) == 1


def test_start_reuses_running_dashboard_without_second_server(
    running_dashboard: tuple[ThreadingHTTPServer, int],
    opened_urls: list[str],
    served_servers: list[ThreadingHTTPServer],
) -> None:
    _, port = running_dashboard
    assert launcher.start(port) == 0
    assert opened_urls == [launcher.dashboard_url(port)]
    assert served_servers == []


def test_start_reuses_dashboard_when_bind_loses_race(
    running_dashboard: tuple[ThreadingHTTPServer, int],
    opened_urls: list[str],
    served_servers: list[ThreadingHTTPServer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, port = running_dashboard
    real_probe: Callable[..., bool] = launcher.is_dashboard_running
    probe_calls = {"count": 0}

    def probe_once_blind(target_port: int, timeout: float = 1.0) -> bool:
        probe_calls["count"] += 1
        if probe_calls["count"] == 1:
            return False  # 模拟并发启动窗口内的漏探
        return real_probe(target_port, timeout)

    def refusing_create(target_port: int) -> ThreadingHTTPServer:
        raise OSError("address already in use")

    monkeypatch.setattr(launcher, "is_dashboard_running", probe_once_blind)
    monkeypatch.setattr(launcher, "create_server", refusing_create)

    assert launcher.start(port) == 0
    assert opened_urls == [launcher.dashboard_url(port)]
    assert served_servers == []


def test_start_raises_and_skips_browser_when_port_held_by_foreign_service(
    opened_urls: list[str], served_servers: list[ThreadingHTTPServer]
) -> None:
    port = free_port()
    foreign = ThreadingHTTPServer(
        ("127.0.0.1", port), partial(BaseHTTPRequestHandler)
    )
    with foreign, serve_in_thread(foreign):
        with pytest.raises(OSError):
            launcher.start(port)
        assert opened_urls == []
