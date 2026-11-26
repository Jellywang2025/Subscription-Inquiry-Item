"""订阅管理系统的本机 HTTP 服务：登录保护、管理页面与 JSON API。"""
from __future__ import annotations

import html
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from . import api, auth

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOGIN_PAGE_PATH = Path(__file__).resolve().parent / "pages" / "login.html"
DEFAULT_PORT = 8765

# 无需登录即可获取的静态资源（登录页也要引用其中的样式）。
PUBLIC_STATIC_PATHS = frozenset({"/styles.css", "/app.js", "/favicon.ico"})

SESSIONS = auth.SessionStore()
LOGIN_PAGE = LOGIN_PAGE_PATH.read_text(encoding="utf-8")


class DashboardServer(ThreadingHTTPServer):
    """看板服务。

    Windows 上关闭地址复用：SO_REUSEADDR 在 Windows 允许对已占用端口
    双绑定，会让「已有服务则复用」的探测失效。
    """

    allow_reuse_address = sys.platform != "win32"


class DashboardHandler(SimpleHTTPRequestHandler):
    """提供登录、管理页面与 JSON API。"""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)  # type: ignore[arg-type]

    # -- 会话与跳转 -----------------------------------------------------

    def _has_valid_session(self) -> bool:
        token = auth.session_token(self)
        return token is not None and SESSIONS.is_valid(token)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _redirect_to_login(self) -> None:
        self._redirect(f"/login?next={quote(self.path, safe='')}")

    # -- 请求入口 -------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - http.server 接口约定
        path = urlsplit(self.path).path
        if path == "/login":
            self._serve_login()
            return
        if path in PUBLIC_STATIC_PATHS:
            super().do_GET()
            return
        if not self._has_valid_session():
            self._redirect_to_login()
            return
        route = api.ROUTES.get(path)
        if route is not None:
            route(self)
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - http.server 接口约定
        path = urlsplit(self.path).path
        if path == "/login":
            self._handle_login()
            return
        route = api.POST_ROUTES.get(path)
        if route is not None:
            if not self._has_valid_session():
                self._redirect_to_login()
                return
            route(self)
            return
        self.send_error(404)

    def do_HEAD(self) -> None:  # noqa: N802 - http.server 接口约定
        path = urlsplit(self.path).path
        if path == "/login":
            self._serve_login(head_only=True)
            return
        if path in PUBLIC_STATIC_PATHS:
            super().do_HEAD()
            return
        if not self._has_valid_session():
            self._redirect_to_login()
            return
        super().do_HEAD()

    # -- 登录页 ---------------------------------------------------------

    def _serve_login(self, error: str = "", next_target: str | None = None, head_only: bool = False) -> None:
        if self._has_valid_session():
            self._redirect("/")
            return
        if next_target is None:
            query = parse_qs(urlsplit(self.path).query)
            next_target = query.get("next", [""])[0]
        page = (
            LOGIN_PAGE.replace("{{next}}", html.escape(auth.safe_next(next_target)))
            .replace("{{error}}", html.escape(error) if error else "")
            .replace("{{error_attr}}", "" if error else " hidden")
        )
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def _handle_login(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        payload = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        form = parse_qs(payload)
        password = form.get("password", [""])[0]
        target = auth.safe_next(form.get("next", [""])[0])
        if not auth.verify_password(password):
            self._serve_login(error="口令不正确，请重试。", next_target=target)
            return
        token = SESSIONS.create()
        self.send_response(302)
        # 回跳目标重新百分号编码：请求头按 latin-1 写出，非 ASCII 原字符会失败。
        self.send_header("Location", quote(target, safe="/%?&=#+,;:@!"))
        self.send_header(
            "Set-Cookie",
            f"{auth.SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={int(auth.SESSION_MAX_AGE_SECONDS)}",
        )
        self.send_header("Content-Length", "0")
        self.end_headers()


def create_server(port: int) -> DashboardServer:
    """在 127.0.0.1 的指定端口创建订阅管理服务。"""
    return DashboardServer(("127.0.0.1", port), DashboardHandler)
