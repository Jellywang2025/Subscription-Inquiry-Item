"""Codex 设备码授权：发起、轮询、令牌交换与按账号保存登录状态。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

from . import storage

# 官方 Codex CLI 的公开客户端标识；授权页与令牌交换均按官方形态携带。
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
# 对齐官方 CLI 的请求标识：Cloudflare 会拒绝默认的 Python-urllib UA（HTTP 530）。
CLIENT_USER_AGENT = "codex_cli_rs/0.171.0"
# 官方设备码流程的固定回调地址；令牌交换时必须携带同值。
DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
DEVICE_USERCODE_PATH = "/api/accounts/deviceauth/usercode"
DEVICE_POLL_PATH = "/api/accounts/deviceauth/token"
DEVICE_EXCHANGE_PATH = "/oauth/token"
DEFAULT_AUTH_BASE_URL = "https://auth.openai.com"
POLL_INTERVAL_SECONDS = 1.0


class DeviceAuthError(Exception):
    """设备码授权流程的预期失败，消息可直接展示。"""


class DeviceSession:
    """一个账号的进行中设备码授权会话。"""

    def __init__(self, device_auth_id: str, user_code: str, interval: float) -> None:
        self.device_auth_id = device_auth_id
        self.user_code = user_code
        self.interval = interval
        # idle / waiting / expired / connected / failed
        self.status = "waiting"
        self.error = ""
        self.stop = threading.Event()
        # 手动检查与后台轮询线程共用一把会话锁，避免并发重复轮询或交换。
        self.lock = threading.Lock()


def auth_base_url() -> str:
    """授权服务根地址：QUOTA_CODEX_AUTH_BASE_URL 注入优先，默认官方。"""
    from .accounts import _env_base_url

    return _env_base_url("QUOTA_CODEX_AUTH_BASE_URL", DEFAULT_AUTH_BASE_URL)


def _post_json(url: str, payload: dict[str, str]) -> dict[str, Any]:
    """按官方 CLI 形态提交 JSON 并解析响应（设备码签发与轮询）。"""
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", CLIENT_USER_AGENT)
    with urllib.request.urlopen(request, timeout=15) as response:
        body: Any = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise DeviceAuthError("授权服务响应格式不正确。")
    return body


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    """按 OAuth 标准提交表单并解析响应（令牌交换）。"""
    data = urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    request.add_header("User-Agent", CLIENT_USER_AGENT)
    with urllib.request.urlopen(request, timeout=15) as response:
        body: Any = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise DeviceAuthError("授权服务响应格式不正确。")
    return body


def _stop_session(session: DeviceSession | None) -> None:
    if session is not None:
        session.stop.set()


class DeviceAuthManager:
    """按账号管理设备码会话与后台轮询。"""

    def __init__(self) -> None:
        self._sessions: dict[str, DeviceSession] = {}
        self._lock = threading.Lock()

    def status(self, account_id: str) -> dict[str, Any]:
        """当前授权会话状态（供页面轮询）。"""
        with self._lock:
            session = self._sessions.get(account_id)
        if session is None:
            return {"status": "idle"}
        return {
            "status": session.status,
            "user_code": session.user_code,
            "error": session.error,
        }

    def stop(self, account_id: str) -> None:
        """停止指定账号的后台轮询（删除账号时调用）。"""
        with self._lock:
            session = self._sessions.pop(account_id, None)
        _stop_session(session)

    def start(self, account_id: str) -> DeviceSession:
        """为账号发起新的设备码授权并启动后台轮询。"""
        with self._lock:
            previous = self._sessions.get(account_id)
            _stop_session(previous)
        try:
            reply = _post_json(
                f"{auth_base_url()}{DEVICE_USERCODE_PATH}",
                {"client_id": CLIENT_ID},
            )
        except DeviceAuthError:
            raise
        except urllib.error.HTTPError as error:
            raise DeviceAuthError(f"获取设备码失败（官方接口返回 HTTP {error.code}）。") from error
        except (OSError, ValueError) as error:
            raise DeviceAuthError(f"获取设备码失败（{error}）。") from error
        device_auth_id = str(reply.get("device_auth_id") or "")
        user_code = str(reply.get("user_code") or "")
        if not device_auth_id or not user_code:
            raise DeviceAuthError("官方接口未返回有效的设备码。")
        try:
            interval = float(reply.get("interval") or POLL_INTERVAL_SECONDS)
        except (TypeError, ValueError):
            interval = POLL_INTERVAL_SECONDS
        session = DeviceSession(device_auth_id, user_code, max(interval, POLL_INTERVAL_SECONDS))
        with self._lock:
            self._sessions[account_id] = session
        thread = threading.Thread(
            target=self._poll_loop, args=(account_id, session), daemon=True
        )
        thread.start()
        return session

    def check(self, account_id: str) -> dict[str, Any]:
        """手动触发一次立即轮询（页面「我已完成授权」）。"""
        with self._lock:
            session = self._sessions.get(account_id)
        if session is None:
            return self.status(account_id)
        self._poll_once(account_id, session)
        return self.status(account_id)

    def _poll_loop(self, account_id: str, session: DeviceSession) -> None:
        while not session.stop.wait(session.interval):
            self._poll_once(account_id, session)
            if session.status != "waiting":
                return

    def _poll_once(self, account_id: str, session: DeviceSession) -> None:
        # 会话锁串行化手动检查与后台轮询，避免并发重复轮询或令牌交换。
        with session.lock:
            self._poll_locked(account_id, session)

    def _poll_locked(self, account_id: str, session: DeviceSession) -> None:
        if session.status != "waiting":
            return
        try:
            # 官方协议：轮询只携带设备码标识与用户码；403/404 表示等待中。
            reply = _post_json(
                f"{auth_base_url()}{DEVICE_POLL_PATH}",
                {
                    "device_auth_id": session.device_auth_id,
                    "user_code": session.user_code,
                },
            )
        except DeviceAuthError as error:
            session.status = "failed"
            session.error = str(error)
            return
        except urllib.error.HTTPError as error:
            if error.code == 410:
                session.status = "expired"
                return
            if error.code == 403:
                return  # authorization_pending：继续等待
            session.status = "failed"
            session.error = f"官方接口返回 HTTP {error.code}"
            return
        except OSError as error:
            session.status = "failed"
            session.error = f"连接失败（{error}）"
            return
        except ValueError as error:
            session.status = "failed"
            session.error = f"授权服务响应格式不正确（{error}）。"
            return
        try:
            self._exchange_and_store(account_id, session, reply)
        except DeviceAuthError as error:
            session.status = "failed"
            session.error = str(error)
        except urllib.error.HTTPError as error:
            session.status = "failed"
            session.error = f"令牌交换失败（官方接口返回 HTTP {error.code}：{_error_body(error)}）"
        except OSError as error:
            session.status = "failed"
            session.error = f"连接失败（{error}）"
        except ValueError as error:
            session.status = "failed"
            session.error = f"授权服务响应格式不正确（{error}）。"

    def _exchange_and_store(
        self, account_id: str, session: DeviceSession, approval: dict[str, Any]
    ) -> None:
        code = str(approval.get("authorization_code") or "")
        verifier = str(approval.get("code_verifier") or "")
        if not code or not verifier:
            raise DeviceAuthError("官方接口未返回有效的授权码。")
        tokens = _post_form(
            f"{auth_base_url()}{DEVICE_EXCHANGE_PATH}",
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
                # 官方 CLI 的设备码流程固定回调地址，交换时必须原样携带。
                "redirect_uri": DEVICE_REDIRECT_URI,
            },
        )
        access_token = str(tokens.get("access_token") or "")
        if not access_token:
            raise DeviceAuthError("官方接口未返回访问令牌。")
        _store_token(account_id, access_token)
        session.status = "connected"
        session.stop.set()


def _error_body(error: urllib.error.HTTPError) -> str:
    """读取上游错误响应体，便于呈现真实的失败原因。"""
    try:
        return error.read().decode("utf-8", "replace")[:200]
    except OSError:
        return "无响应详情"


def _store_token(account_id: str, access_token: str) -> None:
    """把授权令牌写入账号记录并立即查询一次额度。"""
    from . import accounts as accounts_module

    for account in storage.load_accounts():
        if account.get("id") == account_id:
            account["token"] = access_token
            account["connection"] = "connected"
            account["failed"] = False
            account["error"] = ""
            accounts_module.refresh_one(account)
            # 查询结果与令牌一并落盘；期间被删除则放弃写回。
            storage.save_account_if_present(account)
            return


MANAGER = DeviceAuthManager()
