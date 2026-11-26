"""启动编排：探测已有看板、打开浏览器、启动本机服务。"""
from __future__ import annotations

import socket
import webbrowser

from .server import create_server


def dashboard_url(port: int) -> str:
    """看板页面的本机地址。"""
    return f"http://127.0.0.1:{port}/"


def _probe(port: int, timeout: float) -> bytes | None:
    """向登录页发起一次请求，返回响应字节；连接失败返回 None。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.sendall(
                f"GET /login HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode(
                    "ascii"
                )
            )
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"</html" in b"".join(chunks):
                    break
            return b"".join(chunks)
    except OSError:
        return None


def is_dashboard_running(port: int, timeout: float = 1.0) -> bool:
    """判断端口上是否已有本订阅管理服务在运行。"""
    response = _probe(port, timeout)
    if response is None:
        return False
    status_line = response.split(b"\r\n", 1)[0]
    return status_line.endswith(b"200 OK") and "订阅管理".encode("utf-8") in response


def open_in_browser(url: str) -> bool:
    """用系统默认浏览器打开看板页面。"""
    return webbrowser.open(url)


def start(port: int) -> int:
    """启动看板。

    已有服务在运行时直接打开浏览器，不再启动第二个服务；
    否则启动本机服务，打开浏览器后持续运行。
    """
    url = dashboard_url(port)
    if is_dashboard_running(port):
        open_in_browser(url)
        return 0
    try:
        httpd = create_server(port)
    except OSError:
        # 端口刚被并发启动抢占时，确认是本看板后直接复用。
        if not is_dashboard_running(port):
            raise
        open_in_browser(url)
        return 0
    with httpd:
        open_in_browser(url)
        httpd.serve_forever()
    return 0
