"""订阅账号的配置与查询编排：验证密钥、查询余额、维护账号记录。"""
from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from . import storage
from .sources import deepseek
from .sources.http import describe_query_error

CURRENCY_SIGNS = {"CNY": "¥", "USD": "$"}


class AccountError(Exception):
    """账号操作的预期失败（验证不通过、找不到记录等），消息可直接展示。"""


def _deepseek_base_url() -> str:
    return os.environ.get("QUOTA_DEEPSEEK_BASE_URL", deepseek.DEFAULT_BASE_URL).rstrip("/")


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def deepseek_balances(data: dict[str, Any]) -> list[dict[str, str]]:
    """官方余额回执转页面卡片的 balances 结构（每币种一条）。"""
    infos = data.get("balance_infos")
    balances: list[dict[str, str]] = []
    if not isinstance(infos, list):
        return balances
    for info in infos:
        if not isinstance(info, dict):
            continue
        currency = str(info.get("currency") or "")
        sign = CURRENCY_SIGNS.get(currency, "")
        balances.append(
            {
                "currency": currency,
                "value": str(info.get("total_balance") or "0.00"),
                "description": (
                    f"充值 {sign}{info.get('topped_up_balance', '0.00')}"
                    f" · 赠金 {sign}{info.get('granted_balance', '0.00')}"
                ),
            }
        )
    return balances


def verify_deepseek_key(key: str) -> dict[str, Any]:
    """用提交的密钥向 DeepSeek 余额接口发起一次验证查询。

    验证失败抛出原始异常，由调用方转换为可展示的原因。
    """
    return deepseek.fetch_balance(_deepseek_base_url(), key)


def _find_account(account_id: str) -> dict[str, Any] | None:
    for account in storage.load_accounts():
        if account.get("id") == account_id:
            return account
    return None


def create_account(provider: str, name: str, key: str) -> dict[str, Any]:
    """新增一个平台账号；DeepSeek 需先通过密钥验证。"""
    if provider != "deepseek":
        raise AccountError("该平台的账号连接尚未开放。")
    try:
        data = verify_deepseek_key(key)
    except Exception as error:  # noqa: BLE001 - 验证失败统一转为页面提示
        raise AccountError(f"密钥验证失败：{describe_query_error(error)}。当前连接未保存。") from error
    account: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "provider": provider,
        "name": name,
        "key": key,
        "key_tail": key[-4:],
        "connection": "connected",
        "updated": _timestamp(),
        "failed": False,
        "error": "",
        "balances": deepseek_balances(data),
    }
    storage.save_account(account)
    return account


def update_account(account_id: str, name: str, key: str) -> dict[str, Any]:
    """编辑既有账号：改名保留原连接；填写新密钥时先验证再替换。"""
    account = _find_account(account_id)
    if account is None:
        raise AccountError("找不到这条订阅。")
    if account.get("provider") != "deepseek":
        raise AccountError("该平台的账号连接尚未开放。")
    if key:
        try:
            data = verify_deepseek_key(key)
        except Exception as error:  # noqa: BLE001 - 验证失败统一转为页面提示
            raise AccountError(
                f"密钥验证失败：{describe_query_error(error)}。当前连接已保留。"
            ) from error
        account.update(
            {
                "key": key,
                "key_tail": key[-4:],
                "updated": _timestamp(),
                "failed": False,
                "error": "",
                "balances": deepseek_balances(data),
            }
        )
    account["name"] = name
    storage.save_account(account)
    return account


def delete_account(account_id: str) -> bool:
    """删除账号记录（含凭据与缓存，随记录一并移除）。"""
    return storage.delete_account(account_id)


def refresh_accounts() -> list[dict[str, Any]]:
    """立即查询全部已连接账号，各自更新余额、时间与失败状态。"""
    refreshed: list[dict[str, Any]] = []
    for account in storage.load_accounts():
        if account.get("connection") != "connected":
            continue
        key = account.get("key")
        if not key:
            continue
        try:
            data = deepseek.fetch_balance(_deepseek_base_url(), key)
        except Exception as error:  # noqa: BLE001 - 单账号失败不阻断其他账号
            account["failed"] = True
            account["error"] = describe_query_error(error)
        else:
            account.update(
                {
                    "updated": _timestamp(),
                    "failed": False,
                    "error": "",
                    "balances": deepseek_balances(data),
                }
            )
        # 查询期间账号可能已被删除；被删除的记录不再写回，避免复活。
        if _find_account(str(account.get("id"))) is not None:
            storage.save_account(account)
            refreshed.append(account)
    return refreshed
