"""DeepSeek 账户余额查询（官方 GET /user/balance）。"""
from __future__ import annotations

import os
from typing import Any

from .http import get_json, resolve_settings

DEFAULT_BASE_URL = "https://api.deepseek.com"
BALANCE_PATH = "/user/balance"
DEFAULT_TIMEOUT_SECONDS = 10.0


def settings_from_env() -> tuple[str, str | None]:
    """从环境变量读取查询配置：base URL 与 API Key。

    测试与验收通过 QUOTA_DEEPSEEK_BASE_URL 注入可控数据源。
    """
    base_url = os.environ.get("QUOTA_DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ.get("DEEPSEEK_API_KEY") or None
    return base_url, api_key


def account_credential() -> str | None:
    """当前配置对应的账户标识（API Key），供持久化按账户绑定。"""
    return settings_from_env()[1]


def fetch_balance(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """查询 DeepSeek 账户余额，返回官方响应结构。

    返回形如 {"is_available": bool, "balance_infos": [{"currency",
    "total_balance", "topped_up_balance", "granted_balance"}, ...]}，
    金额保持官方字符串原值。
    """
    if base_url is None or api_key is None:
        base_url, api_key = resolve_settings(base_url, api_key, settings_from_env())
    payload = get_json(f"{base_url}{BALANCE_PATH}", api_key, timeout)
    _validate(payload)
    return payload


def _validate(payload: dict[str, Any]) -> None:
    balances = payload.get("balance_infos")
    if not isinstance(balances, list):
        raise ValueError("DeepSeek 余额响应缺少 balance_infos 列表")
    for item in balances:
        if not isinstance(item, dict) or "currency" not in item or "total_balance" not in item:
            raise ValueError("DeepSeek 余额响应的 balance_infos 条目缺少币种或余额字段")
