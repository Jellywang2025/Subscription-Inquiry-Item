"""订阅账号的配置与查询编排：验证密钥、查询余额、维护账号记录。"""
from __future__ import annotations

import os
import urllib.error
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

from . import storage
from .sources import codex, deepseek, google, mimo
from .sources.http import describe_query_error

CURRENCY_SIGNS = {"CNY": "¥", "USD": "$"}
# 以密钥连接且先验证后保存的平台；其余平台在后续工单开放。
KEY_PROVIDERS = frozenset({"deepseek", "mimo"})
# 以设备码授权连接的平台。
DEVICE_PROVIDERS = frozenset({"codex"})
# 以 Google 官方 OAuth 授权连接的平台（授权码粘贴 + 身份核对）。
GOOGLE_PROVIDERS = frozenset({"google"})
# 支持套餐、费用、币种、续费周期与下次续费日的平台。
METADATA_FIELDS = ("plan", "amount", "currency", "cycle", "renewal")
# 兼容旧名：Codex 与 Google AI 共用同一组付费字段。
CODEX_METADATA_FIELDS = METADATA_FIELDS


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


def google_initial_items() -> dict[str, Any]:
    """Google 卡三项额度的初始状态：Gemini Apps 实号核实前仅提供官方指引。"""
    return {
        "gemini_apps": {"status": "manual", "updated": "", "failed": False, "error": ""},
        "antigravity": {"status": "empty", "updated": "", "failed": False, "error": ""},
        "credits": {"status": "empty", "updated": "", "failed": False, "error": ""},
    }


def _refresh_google_item(
    items: dict[str, Any],
    key: str,
    query: Callable[[], Any],
) -> dict[str, Any]:
    """刷新 Google 卡的一个额度项：失败保留旧值与原成功时间，成功更新并记录时间。"""
    previous = items.get(key)
    previous = dict(previous) if isinstance(previous, dict) else {}
    try:
        payload = query()
    except Exception as error:  # noqa: BLE001 - 单项失败不影响其他项
        previous.update({"status": "failed", "failed": True, "error": describe_query_error(error)})
        return previous
    entry = dict(previous)
    entry.update({"status": "ok", "failed": False, "error": "", "updated": _timestamp()})
    entry.update(payload)
    return entry


def _ensure_google_access_token(account: dict[str, Any]) -> dict[str, Any]:
    """Google 访问令牌过期时用刷新令牌换新；官方拒绝时标记需重新授权。"""
    token = account.get("google_token")
    if not isinstance(token, dict) or not token:
        return account
    expiry = str(token.get("expiry") or "")
    if expiry:
        try:
            from datetime import datetime

            if datetime.now().astimezone() < datetime.fromisoformat(expiry):
                return account
        except ValueError:
            pass  # 损坏的时间按过期处理
    refresh_token = str(token.get("refresh_token") or "")
    if not refresh_token:
        account["connection"] = "expired"
        account["error"] = "授权已失效，请重新授权。"
        return account
    try:
        tokens = google.refresh_access_token(refresh_token)
    except google.GoogleAuthExpired:
        account["connection"] = "expired"
        account["error"] = "授权已失效，请重新授权。"
        return account
    except google.GoogleAuthError as error:
        # 刷新暂时失败：保留连接，本轮流为查询失败。
        account["failed"] = True
        account["error"] = str(error)
        return account
    expires_in = tokens.get("expires_in")
    try:
        lifetime = int(expires_in) if expires_in is not None else 3600
    except (TypeError, ValueError):
        lifetime = 3600
    from datetime import datetime, timedelta

    account["google_token"] = {
        "access_token": str(tokens.get("access_token") or token.get("access_token") or ""),
        "refresh_token": str(tokens.get("refresh_token") or refresh_token),
        "expiry": (datetime.now() + timedelta(seconds=max(60, lifetime))).astimezone().isoformat(timespec="seconds"),
    }
    return account


def _refresh_google_account(account: dict[str, Any]) -> dict[str, Any]:
    """逐项刷新 Google 账号的三项额度（Gemini Apps 保持官方指引态）。"""
    token = account.get("google_token")
    email = str(account.get("google_email") or "")
    if not isinstance(token, dict) or not token or not email:
        return account
    items = dict(account.get("google_items") or {})
    if not items:
        items = google_initial_items()
    account_id = str(account.get("id") or "")

    def query_usage() -> dict[str, Any]:
        data = google.run_agy(google.USAGE_COMMAND, email, account_id, token)
        groups = google.usage_groups(data)
        if not groups:
            raise ValueError("官方回执没有可展示的模型组")
        return {"groups": groups}

    def query_credits() -> dict[str, Any]:
        data = google.run_agy(google.CREDITS_COMMAND, email, account_id, token)
        value = google.credits_value(data)
        if value is None:
            raise ValueError("官方回执没有积分数据")
        return {"value": value}

    items["antigravity"] = _refresh_google_item(items, "antigravity", query_usage)
    items["credits"] = _refresh_google_item(items, "credits", query_credits)
    gemini_apps = dict(items.get("gemini_apps") or {})
    gemini_apps.setdefault("status", "manual")
    items["gemini_apps"] = gemini_apps
    account["google_items"] = items

    # 账号级状态：任一项刷新失败时提示，但保留已成功项的最新数据。
    failures = [items[key].get("error") for key in ("antigravity", "credits") if items[key].get("failed")]
    account["failed"] = bool(failures)
    account["error"] = str(failures[0]) if failures else ""
    latest = max(
        (str(items[key].get("updated") or "") for key in ("antigravity", "credits")),
        default="",
    )
    account["updated"] = latest or account.get("updated") or ""
    return account


def create_account(provider: str, name: str, key: str, metadata: dict[str, str] | None = None) -> dict[str, Any]:
    """新增一个平台账号；密钥平台先验证再保存，授权平台无需密钥。"""
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
        for field in METADATA_FIELDS:
            account[field] = str((metadata or {}).get(field, "") or "")
        storage.save_account(account)
        return account
    if provider in GOOGLE_PROVIDERS:
        account = {
            "id": uuid.uuid4().hex,
            "provider": provider,
            "name": name,
            "connection": "pending",
            "updated": "",
            "failed": False,
            "error": "",
            "google_email": "",
            "google_token": {},
            "google_items": google_initial_items(),
        }
        for field in METADATA_FIELDS:
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
    if provider in DEVICE_PROVIDERS or provider in GOOGLE_PROVIDERS:
        for field in METADATA_FIELDS:
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
    if provider in GOOGLE_PROVIDERS:
        token = account.get("google_token")
        if not isinstance(token, dict) or not token:
            return account
        account = _ensure_google_access_token(account)
        if account.get("connection") == "expired":
            return account
        return _refresh_google_account(account)
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
        if account.get("connection") == "connected"
        and (account.get("key") or account.get("token") or account.get("google_token"))
    ]
    if not connected:
        return []
    refreshed: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, len(connected))) as pool:
        future_map = {pool.submit(refresh_one, account): account for account in connected}
        # 先完成的账号先写回：一个上游挂起不拖延其他账号的更新。
        for future in as_completed(future_map):
            account = future_map[future]
            try:
                account = future.result()
            except Exception as error:  # noqa: BLE001 - 意外异常按该账号失败处理
                account["failed"] = True
                account["error"] = describe_query_error(error)
            # 查询期间账号可能已被删除；原子检查存在才写回，避免复活。
            if storage.save_account_if_present(account):
                refreshed.append(account)
    return refreshed
