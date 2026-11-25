"""看板的本机 HTTP 服务：向浏览器提供看板页面与查询 API。"""
from __future__ import annotations

import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import api

STATIC_DIR = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765


class DashboardServer(ThreadingHTTPServer):
    """看板服务。

    Windows 上关闭地址复用：SO_REUSEADDR 在 Windows 允许对已占用端口
    双绑定，会让「已有服务则复用」的探测失效。
    """

    allow_reuse_address = sys.platform != "win32"


class DashboardHandler(SimpleHTTPRequestHandler):
    """提供看板静态页面与 JSON API。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:  # noqa: N802 - http.server 接口约定
        route = api.ROUTES.get(urlsplit(self.path).path)
        if route is not None:
            route(self)
            return
        super().do_GET()


def create_server(port: int) -> DashboardServer:
    """在 127.0.0.1 的指定端口创建看板服务。"""
    return DashboardServer(("127.0.0.1", port), DashboardHandler)
