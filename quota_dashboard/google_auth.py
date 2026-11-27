"""Google 账号授权：授权 URL 发起、授权码提交、身份核对与按账号绑定。

管理员在授权页点击「开始授权」后，服务端生成 PKCE 与 state 并展示
Google 官方授权 URL；管理员在浏览器完成官方登录，把返回的授权码粘贴
回页面。服务端用授权码交换令牌、以 userinfo 核对授权身份（邮箱），
通过后把令牌与身份绑定到该条订阅记录并立即查询一次三项额度。
"""
from __future__ import annotations

import secrets
import threading
from typing import Any

from . import storage
from .sources import google


class GoogleAuthError(Exception):
    """Google 授权流程的预期失败，消息可直接展示。"""


class GoogleAuthSession:
    """一条订阅记录的进行中授权会话。"""

    def __init__(self, authorization_url: str, verifier: str) -> None:
        self.authorization_url = authorization_url
        self.verifier = verifier
        # waiting / connected / failed
        self.status = "waiting"
        self.error = ""
        self.email = ""
        # 手动提交与写回共用一把会话锁，避免并发重复交换。
        self.lock = threading.Lock()


class GoogleAuthManager:
    """按账号管理 Google 授权会话。"""

    def __init__(self) -> None:
        self._sessions: dict[str, GoogleAuthSession] = {}
        self._lock = threading.Lock()

    def status(self, account_id: str) -> dict[str, Any]:
        """当前授权会话状态（供页面轮询）。"""
        with self._lock:
            session = self._sessions.get(account_id)
        if session is None:
            return {"status": "idle", "authorization_url": "", "error": ""}
        return {
            "status": session.status,
            "authorization_url": session.authorization_url,
            "error": session.error,
        }

    def stop(self, account_id: str) -> None:
        """丢弃指定账号的进行中会话（删除账号时调用）。"""
        with self._lock:
            self._sessions.pop(account_id, None)

    def start(self, account_id: str) -> GoogleAuthSession:
        """为账号发起新的官方授权并生成授权 URL。"""
        if not google.client_id() or not google.client_secret():
            raise GoogleAuthError(
                "未配置 Google OAuth 客户端：请在服务端设置 QUOTA_GOOGLE_CLIENT_ID 与 "
                "QUOTA_GOOGLE_CLIENT_SECRET（官方 Gemini CLI 的公共桌面客户端凭据）。"
            )
        with self._lock:
            self._sessions.pop(account_id, None)
        verifier, challenge = google.generate_pkce_pair()
        state = secrets.token_urlsafe(16)
        session = GoogleAuthSession(google.build_authorization_url(challenge, state), verifier)
        with self._lock:
            self._sessions[account_id] = session
        return session

    def submit(self, account_id: str, pasted: str) -> dict[str, Any]:
        """提交管理员粘贴的授权结果：交换令牌、核对身份并绑定账号。"""
        with self._lock:
            session = self._sessions.get(account_id)
        if session is None:
            raise GoogleAuthError("请先开始授权。")
        with session.lock:
            return self._submit_locked(account_id, session, pasted)

    def _submit_locked(self, account_id: str, session: GoogleAuthSession, pasted: str) -> dict[str, Any]:
        if session.status == "connected":
            return self.status(account_id)
        try:
            code = google.parse_authorization_code(pasted)
            tokens = google.exchange_code(code, session.verifier)
        except google.GoogleAuthError as error:
            session.status = "failed"
            session.error = str(error)
            raise GoogleAuthError(str(error)) from error
        access_token = str(tokens.get("access_token") or "")
        refresh_token = str(tokens.get("refresh_token") or "")
        if not access_token or not refresh_token:
            session.status = "failed"
            session.error = "官方接口未返回有效的访问与刷新令牌。"
            raise GoogleAuthError(session.error)
        try:
            identity = google.fetch_userinfo(access_token)
        except google.GoogleAuthError as error:
            session.status = "failed"
            session.error = str(error)
            raise GoogleAuthError(str(error)) from error
        email = str(identity.get("email") or "").strip()
        if not email:
            session.status = "failed"
            session.error = "官方接口未返回账号邮箱，无法核对授权身份。"
            raise GoogleAuthError(session.error)
        stored = _store_authorization(account_id, tokens, email)
        if not stored:
            session.status = "failed"
            session.error = "找不到这条订阅。"
            raise GoogleAuthError(session.error)
        session.status = "connected"
        session.email = email
        session.error = ""
        return self.status(account_id)


def _store_authorization(account_id: str, tokens: dict[str, Any], email: str) -> bool:
    """把授权结果写入账号记录并立即查询一次三项额度。"""
    from . import accounts as accounts_module
    from .sources.google import GoogleAuthExpired

    expires_in = tokens.get("expires_in")
    try:
        lifetime = int(expires_in) if expires_in is not None else 3600
    except (TypeError, ValueError):
        lifetime = 3600
    from datetime import datetime, timedelta

    expiry = (datetime.now() + timedelta(seconds=max(60, lifetime))).astimezone().isoformat(timespec="seconds")
    for account in storage.load_accounts():
        if account.get("id") != account_id:
            continue
        account["google_email"] = email
        account["google_token"] = {
            "access_token": str(tokens.get("access_token") or ""),
            "refresh_token": str(tokens.get("refresh_token") or ""),
            "expiry": expiry,
        }
        account["connection"] = "connected"
        account["failed"] = False
        account["error"] = ""
        try:
            accounts_module.refresh_one(account)
        except GoogleAuthExpired:
            # 交换成功但查询阶段发现授权立刻失效：保留绑定，交由刷新提示。
            pass
        # 查询结果与令牌一并落盘；期间被删除则放弃写回。
        return storage.save_account_if_present(account)
    return False


MANAGER = GoogleAuthManager()
