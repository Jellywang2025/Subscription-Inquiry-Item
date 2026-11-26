"""订阅账号的配置与查询编排：验证密钥、查询余额、维护账号记录。"""
from __future__ import annotations

import os
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from . import storage
from .sources import codex, deepseek, mimo
from .sources.http import describe_query_error

CURRENCY_SIGNS = {"CNY": "¥", "USD": "$"}
# 以密钥连接且先验证后保存的平台；其余平台在后续工单开放。
KEY_PROVIDERS = frozenset({"deepseek", "mimo"})
# 以设备码授权连接的平台。
DEVICE_PROVIDERS = frozenset({"codex"})
CODEX_METADATA_FIELDS = ("plan", "amount", "currency", "cycle", "renewal")


class AccountError(Exception):
    """账号操作的预期失败（验证不通过、找不到记录等），消息可直接展示。"""


def _env_base_url(variable: str, default: str) -> str:
    return os.environ.get(variable, default).rstrip("/")


def _provider_base_url(provider: str) -> str:
    if provider == "deepseek":
        return _env_base_url("QUOTA_DEEPSEEK_BASE_URL", deepseek.DEFAULT_BASE_URL)
    return _env_base_url("QUOTA_MIMO_BASE_URL", mimo.DEFAULT_BASE_URL)


def codex_usage_base_url() -> str:
    """Codex 额度接口根地址：QUOTA_CODEX_BASE_URL 注入优先。"""
    return _env_base_url("QUOTA_CODEX_BASE_URL", codex.DEFAULT_BASE_URL)


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


def mimo_balances(data: dict[str, Any]) -> list[dict[str, str]]:
    """MiMo 官方回执转卡片 balances：可动用余额 + 现金/赠金/授信/冻结明细。

    官方响应当前固定单币种（CNY）；可动用金额取总额减冻结，最低为零。
    """
    infos = data.get("balance_infos")
    if not isinstance(infos, list) or not infos or not isinstance(infos[0], dict):
        return []
    info = infos[0]
    sign = CURRENCY_SIGNS.get("CNY", "")
    return [
        {
            "currency": "CNY",
            "value": mimo.available_amount(data),
            "description": (
                f"现金 {sign}{info.get('cash_balance', '0.00')}"
                f" · 赠金 {sign}{info.get('gift_balance', '0.00')}"
                f" · 可用授信 {sign}{info.get('credit_available', '0.00')}"
                f" · 冻结 {sign}{info.get('frozen_balance', '0.00')}"
            ),
        }
    ]


def _balances_for(provider: str, data: dict[str, Any]) -> list[dict[str, str]]:
    if provider == "mimo":
        return mimo_balances(data)
    return deepseek_balances(data)


def _query_with_key(provider: str, key: str) -> dict[str, Any]:
    """用指定密钥向对应平台的余额接口发起一次验证查询。

    验证失败抛出原始异常，由调用方转换为可展示的原因。
    """
    if provider == "deepseek":
        return deepseek.fetch_balance(_provider_base_url(provider), key)
    if provider == "mimo":
        return mimo.fetch_balance(_provider_base_url(provider), key)
    raise AccountError("该平台的账号连接尚未开放。")


def window_label(seconds: Any) -> str:
    """按官方窗口时长生成中文标签（5 小时窗口 / 每周窗口等）。"""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return "额度窗口"
    week = 7 * 86400
    if seconds >= week and seconds % week == 0:
        return "每周窗口"
    if seconds >= 86400:
        return f"{int(seconds // 86400)} 天窗口"
    if seconds >= 3600:
        return f"{round(seconds / 3600)} 小时窗口"
    return f"{round(seconds / 60)} 分钟窗口"


def codex_windows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """官方用量回执转卡片 windows：剩余比例（100% − 已用）与重置时间。"""
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return []
    windows: list[dict[str, Any]] = []
    for key in ("primary_window", "secondary_window"):
        window = rate_limit.get(key)
        if not isinstance(window, dict):
            continue
        used = window.get("used_percent")
        if not isinstance(used, (int, float)) or isinstance(used, bool):
            continue
        reset_at = window.get("reset_at")
        reset = ""
        if isinstance(reset_at, (int, float)) and not isinstance(reset_at, bool):
            reset = datetime.fromtimestamp(reset_at).astimezone().isoformat(timespec="seconds")
        windows.append(
            {
                "label": window_label(window.get("limit_window_seconds")),
                "remaining": max(0, 100 - round(used)),
                "reset": reset,
            }
        )
    return windows


def _find_account(account_id: str) -> dict[str, Any] | None:
    for account in storage.load_accounts():
        if account.get("id") == account_id:
            return account
    return None


def create_account(provider: str, name: str, key: str, metadata: dict[str, str] | None = None) -> dict[str, Any]:
    """新增一个平台账号；密钥平台先验证再保存，Codex 无需密钥。"""
    if provider in DEVICE_PROVIDERS:
        account: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "provider": provider,
            "name": name,
            "connection": "pending",
            "updated": "",
            "failed": False,
            "error": "",
            "windows": [],
        }
        for field in CODEX_METADATA_FIELDS:
            account[field] = str((metadata or {}).get(field, "") or "")
        storage.save_account(account)
        return account
    if provider not in KEY_PROVIDERS:
        raise AccountError("该平台的账号连接尚未开放。")
    try:
        data = _query_with_key(provider, key)
    except Exception as error:  # noqa: BLE001 - 验证失败统一转为页面提示
        raise AccountError(f"密钥验证失败：{describe_query_error(error)}。当前连接未保存。") from error
    account = {
        "id": uuid.uuid4().hex,
        "provider": provider,
        "name": name,
        "key": key,
        "key_tail": key[-4:],
        "connection": "connected",
        "updated": _timestamp(),
        "failed": False,
        "error": "",
        "balances": _balances_for(provider, data),
    }
    storage.save_account(account)
    return account


def update_account(
    account_id: str, name: str, key: str, metadata: dict[str, str] | None = None
) -> dict[str, Any]:
    """编辑既有账号：改名与付费字段保留原连接；填写新密钥时先验证再替换。"""
    account = _find_account(account_id)
    if account is None:
        raise AccountError("找不到这条订阅。")
    provider = str(account.get("provider") or "")
    if provider in DEVICE_PROVIDERS:
        for field in CODEX_METADATA_FIELDS:
            account[field] = str((metadata or {}).get(field, "") or "")
        account["name"] = name
        storage.save_account(account)
        return account
    if provider not in KEY_PROVIDERS:
        raise AccountError("该平台的账号连接尚未开放。")
    if key:
        try:
            data = _query_with_key(provider, key)
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
                "balances": _balances_for(provider, data),
            }
        )
    account["name"] = name
    storage.save_account(account)
    return account


def delete_account(account_id: str) -> bool:
    """删除账号记录（含凭据与缓存，随记录一并移除）。"""
    return storage.delete_account(account_id)


def refresh_one(account: dict[str, Any]) -> dict[str, Any]:
    """查询单个已连接账号并就地更新；返回更新后的记录。"""
    provider = str(account.get("provider") or "")
    if provider in DEVICE_PROVIDERS:
        token = account.get("token")
        if not token:
            return account
        try:
            data = codex.fetch_usage(codex_usage_base_url(), token)
        except urllib.error.HTTPError as error:
            if error.code == 401:
                # 已保存的登录失效：账号需要重新授权，保留其余数据。
                account["connection"] = "expired"
                account["error"] = "登录已失效，请重新授权。"
            else:
                account["failed"] = True
                account["error"] = describe_query_error(error)
        except Exception as error:  # noqa: BLE001 - 单账号失败不阻断其他账号
            account["failed"] = True
            account["error"] = describe_query_error(error)
        else:
            account.update(
                {
                    "updated": _timestamp(),
                    "failed": False,
                    "error": "",
                    "windows": codex_windows(data),
                }
            )
        return account
    key = account.get("key")
    if not key:
        return account
    try:
        data = _query_with_key(provider, key)
    except Exception as error:  # noqa: BLE001 - 单账号失败不阻断其他账号
        account["failed"] = True
        account["error"] = describe_query_error(error)
    else:
        account.update(
            {
                "updated": _timestamp(),
                "failed": False,
                "error": "",
                "balances": _balances_for(provider, data),
            }
        )
    return account


def refresh_accounts() -> list[dict[str, Any]]:
    """并行查询全部已连接账号，各自更新余额、时间与失败状态。

    每个账号的查询互不阻塞：一个上游挂起或超时不影响其他账号完成更新。
    """
    connected = [
        account
        for account in storage.load_accounts()
        if account.get("connection") == "connected" and (account.get("key") or account.get("token"))
    ]
    if not connected:
        return []
    refreshed: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(connected))) as pool:
        futures = [pool.submit(refresh_one, account) for account in connected]
        # 先完成的账号先写回：一个上游挂起不拖延其他账号的更新。
        for future in as_completed(futures):
            account = future.result()
            # 查询期间账号可能已被删除；原子检查存在才写回，避免复活。
            if storage.save_account_if_present(account):
                refreshed.append(account)
    return refreshed
