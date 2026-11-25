"""MiMo SK 账户余额查询（官方 GET /v1/platform/balance）。"""
from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from .http import get_json, resolve_settings

DEFAULT_BASE_URL = "https://api.xiaomimimo.com"
BALANCE_PATH = "/v1/platform/balance"
DEFAULT_TIMEOUT_SECONDS = 10.0


def settings_from_env() -> tuple[str, str | None]:
    """从环境变量读取查询配置：base URL 与 API Key。

    测试与验收通过 QUOTA_MIMO_BASE_URL 注入可控数据源。
    """
    base_url = os.environ.get("QUOTA_MIMO_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    api_key = os.environ.get("MIMO_API_KEY") or None
    return base_url, api_key


def account_credential() -> str | None:
    """当前配置对应的账户标识（API Key），供持久化按账户绑定。"""
    return settings_from_env()[1]


def fetch_balance(
    base_url: str | None = None,
    api_key: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """查询 MiMo SK 账户余额，返回官方响应结构（金额保持字符串原值）。

    API Key 未配置时不发起请求，直接抛面向用户的补配置提示。
    """
    if base_url is None or api_key is None:
        base_url, api_key = resolve_settings(base_url, api_key, settings_from_env())
    if not api_key:
        raise PermissionError("MiMo API Key 未配置：请在本机配置 MIMO_API_KEY")
    payload = get_json(f"{base_url}{BALANCE_PATH}", api_key, timeout)
    _validate(payload)
    return payload


def available_amount(payload: dict[str, Any]) -> str:
    """官方可用性判式：total_available − frozen_balance，小于零时归零。

    官方响应当前固定单币种，取首个余额条目计算。
    """
    infos = payload.get("balance_infos") or []
    if not infos:
        return "0.00"
    info = infos[0]
    total = Decimal(str(info.get("total_available", "0")))
    frozen = Decimal(str(info.get("frozen_balance", "0")))
    return f"{max(total - frozen, Decimal('0')):.2f}"


def _validate(payload: dict[str, Any]) -> None:
    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        raise ValueError("MiMo 余额响应缺少 balance_infos")
    for item in infos:
        if not isinstance(item, dict) or "total_available" not in item:
            raise ValueError("MiMo 余额响应的 balance_infos 条目缺少 total_available")
