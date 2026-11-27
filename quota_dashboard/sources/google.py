"""Google AI（Google One）数据源：OAuth 授权端点与 Antigravity CLI 只读查询。

授权走 Google 官方 OAuth Authorization Code + PKCE（loopback 回调）。
服务器场景不监听回调端口：授权页展示官方授权 URL，管理员在浏览器完成
Google 登录后把返回的授权码（或整个回调地址）粘贴回看板，服务端用
授权码向令牌端点交换凭据，并用 userinfo 核对授权身份后绑定订阅记录。

三项额度中的 Antigravity 模型组余量与 Google One AI 积分通过官方 CLI
只读查询取得（`agy -p /usage|/credits --output-format json`）。每个账号
的 CLI 连接使用独立的 HOME 目录（token 文件落盘），保证多账号隔离。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

# 官方 Gemini CLI 的公共桌面 OAuth 客户端（随官方开源包分发，非机密；
# 安装类客户端的安全性由 PKCE 保障）。凭据经 QUOTA_GOOGLE_CLIENT_ID /
# QUOTA_GOOGLE_CLIENT_SECRET 环境变量部署时注入，不写入源码（仓库
# 推送保护会拦截凭据形态的字符串）。
DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"
OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/cloud-platform "
    "https://www.googleapis.com/auth/userinfo.email "
    "https://www.googleapis.com/auth/userinfo.profile "
    "openid"
)
# 授权 URL 的 loopback 回调：服务器不监听该端口，管理员从浏览器地址栏
# 复制授权码；交换令牌时必须携带同值。
REDIRECT_PORT = 8085
CALLBACK_PATH = "/oauth2callback"

# Antigravity CLI 只读查询：不启动会话、不消耗额度（官方 1.1.11 起）。
USAGE_COMMAND = "/usage"
CREDITS_COMMAND = "/credits"
CLI_TIMEOUT_SECONDS = 45.0
# 替身与真实 CLI 共用的身份传递：服务端把已核对的授权邮箱交给子进程。
CLI_ACCOUNT_VARIABLE = "QUOTA_GOOGLE_CLI_ACCOUNT"


class GoogleAuthError(Exception):
    """Google OAuth 流程的预期失败，消息可直接展示。"""


class GoogleAuthExpired(GoogleAuthError):
    """已保存的授权被官方拒绝（refresh_token 失效），需要重新授权。"""


class GoogleQueryError(Exception):
    """Antigravity CLI 查询失败，消息可直接展示。"""


def client_id() -> str:
    """OAuth 客户端标识：QUOTA_GOOGLE_CLIENT_ID 部署时注入。"""
    return os.environ.get("QUOTA_GOOGLE_CLIENT_ID") or ""


def client_secret() -> str:
    """OAuth 客户端密钥：QUOTA_GOOGLE_CLIENT_SECRET 部署时注入。"""
    return os.environ.get("QUOTA_GOOGLE_CLIENT_SECRET") or ""


def token_url() -> str:
    """令牌端点：QUOTA_GOOGLE_TOKEN_URL 注入优先，默认官方。"""
    return os.environ.get("QUOTA_GOOGLE_TOKEN_URL") or DEFAULT_TOKEN_URL


def userinfo_url() -> str:
    """身份核对端点：QUOTA_GOOGLE_USERINFO_URL 注入优先，默认官方。"""
    return os.environ.get("QUOTA_GOOGLE_USERINFO_URL") or DEFAULT_USERINFO_URL


def cli_path() -> str:
    """Antigravity CLI 可执行文件：QUOTA_GOOGLE_CLI 注入优先，默认 PATH 上的 agy。"""
    return os.environ.get("QUOTA_GOOGLE_CLI") or "agy"


def generate_pkce_pair() -> tuple[str, str]:
    """生成 PKCE (verifier, challenge) 对，S256。"""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def redirect_uri() -> str:
    """授权流程携带的 loopback 回调地址。"""
    return f"http://127.0.0.1:{REDIRECT_PORT}{CALLBACK_PATH}"


def build_authorization_url(challenge: str, state: str) -> str:
    """构造 Google 官方授权页 URL（Authorization Code + PKCE，离线访问）。"""
    params = {
        "client_id": client_id(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{DEFAULT_AUTH_URL}?{urlencode(params)}"


def parse_authorization_result(pasted: str) -> tuple[str, str | None]:
    """解析管理员粘贴的授权结果：返回 (授权码, 回传 state 或 None)。

    接受授权码、回调 URL 或查询串；回调中带回 state 时供会话校验。
    """
    text = pasted.strip()
    if not text:
        raise GoogleAuthError("请粘贴 Google 返回的授权码。")
    if text.startswith(("http://", "https://", "?")):
        query = urlparse(text).query if not text.startswith("?") else text[1:]
        values = parse_qs(query)
        code = (values.get("code") or [""])[0]
        if code:
            state = (values.get("state") or [""])[0]
            return code, (state or None)
        raise GoogleAuthError("粘贴的地址中没有授权码（code）。")
    if " " in text or "\n" in text:
        raise GoogleAuthError("授权码不应包含空格或换行。")
    return text, None


def parse_authorization_code(pasted: str) -> str:
    """解析管理员粘贴的授权结果并只返回授权码。"""
    code, _state = parse_authorization_result(pasted)
    return code


def _post_form(url: str, payload: dict[str, str]) -> dict[str, Any]:
    """按 OAuth 标准提交表单并解析 JSON 响应。"""
    data = urlencode(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    request.add_header("Accept", "application/json")
    with urllib.request.urlopen(request, timeout=15) as response:
        body: Any = json.loads(response.read().decode("utf-8"))
    if not isinstance(body, dict):
        raise GoogleAuthError("授权服务响应格式不正确。")
    return body


def exchange_code(code: str, verifier: str) -> dict[str, Any]:
    """用授权码交换访问与刷新令牌。"""
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": client_id(),
        "client_secret": client_secret(),
        "redirect_uri": redirect_uri(),
    }
    try:
        return _post_form(token_url(), payload)
    except urllib.error.HTTPError as error:
        raise GoogleAuthError(f"授权码交换失败（官方接口返回 HTTP {error.code}）。") from error
    except OSError as error:
        raise GoogleAuthError(f"连接授权服务失败（{error}）。") from error
    except ValueError as error:
        raise GoogleAuthError(f"授权服务响应格式不正确（{error}）。") from error


def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """用刷新令牌换取新的访问令牌；官方拒绝时提示重新授权。"""
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id(),
        "client_secret": client_secret(),
    }
    try:
        return _post_form(token_url(), payload)
    except urllib.error.HTTPError as error:
        if error.code in (400, 401):
            raise GoogleAuthExpired("授权已失效，请重新授权。") from error
        raise GoogleAuthError(f"刷新访问令牌失败（官方接口返回 HTTP {error.code}）。") from error
    except OSError as error:
        raise GoogleAuthError(f"连接授权服务失败（{error}）。") from error
    except ValueError as error:
        raise GoogleAuthError(f"授权服务响应格式不正确（{error}）。") from error


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    """用访问令牌核对授权身份（官方 userinfo）。"""
    request = urllib.request.Request(userinfo_url(), method="GET")
    request.add_header("Authorization", f"Bearer {access_token}")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body: Any = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise GoogleAuthError(f"无法核对 Google 身份（官方接口返回 HTTP {error.code}）。") from error
    except OSError as error:
        raise GoogleAuthError(f"连接身份服务失败（{error}）。") from error
    except ValueError as error:
        raise GoogleAuthError(f"身份服务响应格式不正确（{error}）。") from error
    if not isinstance(body, dict):
        raise GoogleAuthError("身份服务响应格式不正确。")
    return body


def homes_root() -> Path:
    """各账号 CLI 隔离 HOME 的根目录：跟随状态文件目录。"""
    from .. import storage

    return storage.state_path().parent / "google-homes"


def _token_file(home: Path) -> Path:
    """Antigravity CLI 在 HOME 下的登录凭据文件（官方布局）。"""
    return home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"


def write_cli_credentials(account_id: str, token: dict[str, Any]) -> Path | None:
    """把该账号的令牌写入隔离 HOME，供官方 CLI 文件态登录使用。"""
    if not account_id or not isinstance(token, dict):
        return None
    home = homes_root() / account_id
    target = _token_file(home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "token": {
                "access_token": str(token.get("access_token") or ""),
                "token_type": "Bearer",
                "refresh_token": str(token.get("refresh_token") or ""),
                "expiry": str(token.get("expiry") or ""),
            },
            "auth_method": "consumer",
        }
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None
    return home


def remove_cli_credentials(account_id: str) -> None:
    """删除账号的隔离 HOME（含 CLI 登录凭据）；删除账号时调用。"""
    if not account_id:
        return
    home = homes_root() / account_id
    try:
        shutil.rmtree(home, ignore_errors=True)
    except OSError:
        return


def run_agy(command: str, account_email: str, account_id: str = "", token: dict[str, Any] | None = None) -> dict[str, Any]:
    """运行官方只读查询并返回其 command.data；失败抛出可展示异常。

    子进程环境携带已核对的授权邮箱（QUOTA_GOOGLE_CLI_ACCOUNT），并把
    HOME 指向该账号的隔离目录（写入官方布局的登录凭据，检测 SSH 环境
    时 CLI 使用文件态存储）。
    """
    executable = cli_path()
    env = os.environ.copy()
    env[CLI_ACCOUNT_VARIABLE] = account_email
    home: Path | None = None
    if account_id and isinstance(token, dict) and token.get("access_token"):
        home = write_cli_credentials(account_id, token)
        if home is None:
            # 凭据落盘失败时不得回退到服务器进程的真实 HOME：
            # 那会把其他登录账号的数据记到当前账号名下。
            raise GoogleQueryError("无法为该账号准备隔离的 CLI 登录目录。")
    if home is not None:
        # 官方 CLI 的数据目录只随 HOME 移动；SSH 环境下使用文件态令牌存储。
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env.setdefault("SSH_CONNECTION", "127.0.0.1 0 127.0.0.1 0")
        env.setdefault("SSH_CLIENT", "127.0.0.1 0 22")
    try:
        with tempfile.TemporaryDirectory(prefix="quota-agy-") as working_dir:
            result = subprocess.run(
                [executable, "-p", command, "--output-format", "json"],
                env=env,
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT_SECONDS,
            )
    except OSError as error:
        raise GoogleQueryError(f"无法运行 Antigravity CLI（{error}）。") from error
    except subprocess.TimeoutExpired as error:
        raise GoogleQueryError("Antigravity CLI 查询超时。") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        reason = detail[-1][:160] if detail else f"退出码 {result.returncode}"
        raise GoogleQueryError(f"Antigravity CLI 查询失败：{reason}")
    try:
        payload: Any = json.loads(result.stdout)
    except ValueError as error:
        raise GoogleQueryError("Antigravity CLI 输出不是有效的 JSON。") from error
    if not isinstance(payload, dict) or payload.get("status") == "ERROR":
        raise GoogleQueryError("Antigravity CLI 未能返回额度数据。")
    data = payload.get("command")
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        raise GoogleQueryError("Antigravity CLI 响应缺少额度数据。")
    return data["data"]


def _window_seconds(window: str) -> int | None:
    """官方窗口标识换算为秒；未知标识返回 None。"""
    text = window.strip().lower()
    if text in ("5h", "five_hour", "five-hour"):
        return 5 * 3600
    if text in ("weekly", "week", "7d"):
        return 7 * 86400
    if text in ("daily", "24h"):
        return 86400
    if text in ("monthly", "month", "30d"):
        return 30 * 86400
    return None


def _bucket_remaining(quota: dict[str, Any], reset: str) -> int | None:
    """官方回执的余量换算为剩余百分比；缺失但有重置时间表示用尽。"""
    remaining = quota.get("remaining_fraction")
    if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
        return max(0, min(100, round(remaining * 100)))
    used = quota.get("used_percentage")
    if isinstance(used, (int, float)) and not isinstance(used, bool):
        return max(0, min(100, 100 - round(used)))
    if reset:
        return 0
    return None


def usage_groups(data: dict[str, Any]) -> list[dict[str, Any]]:
    """官方 /usage 回执转卡片的模型组窗口：同组多个模型只计一次。

    兼容官方 1.4.3 的 buckets 形态与 1.4.2 的 windows/组级 quota_info 形态；
    名称含 Gemini 的模型组同样属于 Antigravity 项。
    """
    from ..accounts import window_label

    raw_groups = data.get("groups")
    if not isinstance(raw_groups, list):
        return []
    groups: list[dict[str, Any]] = []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        name = str(
            group.get("name") or group.get("group_name") or group.get("display_name") or ""
        ).strip()
        windows: list[dict[str, Any]] = []
        for key in ("buckets", "windows"):
            buckets = group.get(key)
            if not isinstance(buckets, list):
                continue
            for bucket in buckets:
                if not isinstance(bucket, dict):
                    continue
                window_id = str(bucket.get("window") or bucket.get("window_id") or "")
                info = bucket.get("quota_info")
                quota: dict[str, Any] = info if isinstance(info, dict) else bucket
                reset = str(quota.get("reset_time") or "")
                remaining = _bucket_remaining(quota, reset)
                if remaining is None:
                    continue
                seconds = _window_seconds(window_id)
                windows.append(
                    {
                        "label": window_label(seconds) if seconds else (window_id or "额度窗口"),
                        "remaining": remaining,
                        "reset": reset,
                    }
                )
            if windows:
                break
        if not windows:
            # 官方 1.4.2 形态：余量直接挂在组级 quota_info（或组本身）。
            info = group.get("quota_info")
            quota = info if isinstance(info, dict) else group
            reset = str(quota.get("reset_time") or "")
            remaining = _bucket_remaining(quota, reset)
            if remaining is not None:
                windows.append({"label": "额度窗口", "remaining": remaining, "reset": reset})
        if not name or not windows:
            continue
        groups.append({"name": name, "windows": windows})
    return groups


def credits_value(data: dict[str, Any]) -> int | None:
    """官方 /credits 回执的剩余 Google One AI 积分；缺失返回 None。"""
    value = data.get("remaining_credits")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
