"""Codex 额度窗口查询（官方 GET /backend-api/wham/usage，复用本机 CLI 登录）。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .http import get_json, resolve_settings

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
USAGE_PATH = "/wham/usage"
DEFAULT_TIMEOUT_SECONDS = 10.0
AUTH_FILE = Path.home() / ".codex" / "auth.json"
# 官方网关拦截非 codex 客户端 UA（实测 Python 默认 UA 被 403），对齐官方客户端风格。
USER_AGENT = "codex_cli_rs/0.171.0 (Windows 11; x86_64) quota-dashboard"


def _read_auth_file(path: Path | None = None) -> dict[str, Any] | None:
    """读取 codex CLI 登录凭据文件，损坏或缺失时返回 None。"""
    target = AUTH_FILE if path is None else path
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def access_token_from_auth_file(path: Path | None = None) -> str | None:
    """从 codex CLI 的登录凭据读取 access token（复用本机 CLI 的 ChatGPT 登录）。"""
    payload = _read_auth_file(path)
    if payload is None:
        return None
    tokens = payload.get("tokens")
    if isinstance(tokens, dict):
        token = tokens.get("access_token")
        if isinstance(token, str) and token:
            return token
    api_key = payload.get("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key:
        return api_key
    return None


def account_id_from_auth_file(path: Path | None = None) -> str | None:
    """从 codex CLI 的登录凭据读取 ChatGPT 账户 ID（官方客户端随请求携带）。"""
    payload = _read_auth_file(path)
    if payload is None:
        return None
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return None
    account_id = tokens.get("account_id")
    return account_id if isinstance(account_id, str) and account_id else None


def request_headers() -> dict[str, str]:
    """对齐官方客户端的请求头：codex 风格 UA 过网关，账户头用于路由到正确账户。"""
    headers = {"User-Agent": USER_AGENT}
    account_id = account_id_from_auth_file()
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def settings_from_env() -> tuple[str, str | None]:
    """从环境变量读取查询配置：base URL 与 access token。

    测试与验收通过 QUOTA_CODEX_BASE_URL 注入可控数据源；
    token 未注入时回退读取本机 codex CLI 登录。
    """
    base_url = os.environ.get("QUOTA_CODEX_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    token = os.environ.get("QUOTA_CODEX_ACCESS_TOKEN") or None
    return base_url, token


def effective_token(auth_file: Path | None = None) -> str | None:
    """当前生效的 access token：环境注入优先，回退本机 codex CLI 登录凭据。"""
    token = os.environ.get("QUOTA_CODEX_ACCESS_TOKEN") or None
    return token or access_token_from_auth_file(auth_file)


def fetch_usage(
    base_url: str | None = None,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """查询 Codex 官方用量（rate_limit 双窗口），返回官方响应结构。"""
    if base_url is None or token is None:
        base_url, token = resolve_settings(base_url, token, settings_from_env())
    if not token:
        token = effective_token()
    if not token:
        raise PermissionError("Codex 未登录：请在本机完成 Codex 登录")
    payload = get_json(f"{base_url}{USAGE_PATH}", token, timeout, request_headers())
    _validate(payload)
    return payload


def _validate(payload: dict[str, Any]) -> None:
    rate_limit = payload.get("rate_limit")
    if not isinstance(rate_limit, dict):
        raise ValueError("Codex 用量响应缺少 rate_limit")


SHARED_LABEL = "ChatGPT 与 Codex 共享"


def _is_shared_bucket(limit_id: str, limit_name: str) -> bool:
    """判断多桶数据中的一个桶是否明确标注覆盖 ChatGPT 与 Codex 的共享。"""
    text = f"{limit_id} {limit_name}".lower()
    has_shared = "shared" in text or "共享" in text
    has_chatgpt = "chatgpt" in text
    has_codex = "codex" in text
    return has_shared and has_chatgpt and has_codex


def shared_quota_view(payload: dict[str, Any]) -> dict[str, Any] | None:
    """从官方多桶数据中提取明确标注为 ChatGPT 与 Codex 共享的额度视图。

    判据（Spec 约束「可读取且明确标注为共享的账户数据为准」）：
    rate_limits_by_limit_id 中桶的 key 或 limit_name 同时标注共享与
    ChatGPT、Codex 两者；只标注部分平台的桶不构成共享额度。
    """
    by_limit_id = payload.get("rate_limits_by_limit_id")
    if not isinstance(by_limit_id, dict):
        return None
    for limit_id, bucket in by_limit_id.items():
        if not isinstance(bucket, dict):
            continue
        limit_name = str(bucket.get("limit_name") or "")
        if not _is_shared_bucket(str(limit_id), limit_name):
            continue
        window = bucket.get("primary_window")
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent")
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        return {
            "label": SHARED_LABEL,
            "remaining_percent": max(0, 100 - round(used)),
            "reset_at": window.get("reset_at"),
        }
    return None
