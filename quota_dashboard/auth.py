"""管理员登录与会话：单管理员口令、内存会话存储与回跳目标校验。"""
from __future__ import annotations

import hmac
import os
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler

PASSWORD_VARIABLE = "QUOTA_ADMIN_PASSWORD"
SESSION_COOKIE = "quota_session"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def admin_password() -> str | None:
    """管理员口令：部署时通过环境变量提供，未配置时返回 None。"""
    password = os.environ.get(PASSWORD_VARIABLE)
    return password if password else None


def password_configured() -> bool:
    """管理员口令是否已配置。"""
    return admin_password() is not None


def verify_password(candidate: str) -> bool:
    """恒定时间比对管理员口令；未配置口令时一律拒绝。"""
    expected = admin_password()
    if expected is None:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


class SessionStore:
    """内存会话存储：服务重启后失效，重新登录即可。"""

    def __init__(self, max_age_seconds: float = SESSION_MAX_AGE_SECONDS) -> None:
        self._max_age_seconds = max_age_seconds
        self._created_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def create(self) -> str:
        """创建一个新会话并返回其令牌。"""
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._created_at[token] = now
            self._discard_expired(now)
        return token

    def is_valid(self, token: str) -> bool:
        """会话令牌是否有效；过期即销毁。"""
        now = time.monotonic()
        with self._lock:
            created_at = self._created_at.get(token)
            if created_at is None:
                return False
            if now - created_at > self._max_age_seconds:
                del self._created_at[token]
                return False
            return True

    def _discard_expired(self, now: float) -> None:
        expired = [
            token
            for token, created in self._created_at.items()
            if now - created > self._max_age_seconds
        ]
        for token in expired:
            del self._created_at[token]


def session_token(handler: BaseHTTPRequestHandler) -> str | None:
    """从请求头解析会话 Cookie；缺失或损坏时返回 None。"""
    raw = handler.headers.get("Cookie")
    if not raw:
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except CookieError:
        return None
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel is not None else None


def safe_next(raw: str | None) -> str:
    """登录成功后的回跳目标：仅接受本站相对路径，其余回首页。"""
    if not raw or not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return "/"
    return raw
