"""订阅管理的 JSON API：向页面提供账号配置与各数据源的查询结果。"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Any

from . import accounts, codex_auth, google_auth, scheduler, storage
from .sources import codex, deepseek, google, mimo
from .sources.http import describe_query_error

__all__ = [
    "ROUTES",
    "POST_ROUTES",
    "serve_accounts",
    "serve_account_create",
    "serve_account_update",
    "serve_account_delete",
    "serve_device_status",
    "serve_device_start",
    "serve_device_check",
    "serve_google_auth_status",
    "serve_google_auth_start",
    "serve_google_auth_submit",
    "serve_refresh",
    "serve_settings",
    "serve_refresh_interval",
    "serve_deepseek_balance",
    "serve_mimo_balance",
    "serve_codex_usage",
    "describe_query_error",
]

# 账号记录中对页面可见的字段；凭据只在服务端保存。
ACCOUNT_PUBLIC_FIELDS = (
    "id",
    "provider",
    "name",
    "connection",
    "updated",
    "failed",
    "error",
    "plan",
    "amount",
    "currency",
    "cycle",
    "renewal",
    "key_tail",
    "windows",
    "balances",
    "details",
    "google_email",
    "google_items",
)


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _serve_source_query(
    handler: BaseHTTPRequestHandler,
    fetch: Callable[[], dict[str, Any]],
    source: str,
    credential: str | None,
    extra: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> None:
    """GET /api/sources/<name>/balance 的通用信封：查询、持久化、错误出口与时间戳。"""
    try:
        data = fetch()
        fields = extra(data) if extra is not None else {}
    except Exception as error:  # noqa: BLE001 - 面向页面的统一错误出口
        envelope: dict[str, Any] = {"status": "error", "error": describe_query_error(error)}
        restored = storage.restore(source, credential)
        if restored is not None:
            envelope["last_success"] = restored
        _send_json(handler, 502, envelope)
        return
    result: dict[str, Any] = {
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data": data,
        **fields,
    }
    storage.save(source, credential, result)
    _send_json(handler, 200, {"status": "ok", **result})


def serve_deepseek_balance(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/sources/deepseek/balance：即时查询 DeepSeek 账户余额。"""
    _serve_source_query(
        handler, deepseek.fetch_balance, source="deepseek", credential=deepseek.account_credential()
    )


def serve_mimo_balance(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/sources/mimo/balance：即时查询 MiMo SK 账户余额。"""
    _serve_source_query(
        handler,
        mimo.fetch_balance,
        source="mimo",
        credential=mimo.account_credential(),
        extra=lambda data: {"available": mimo.available_amount(data)},
    )


def serve_codex_usage(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/sources/codex/balance：即时查询 Codex 额度窗口。"""
    _serve_source_query(
        handler,
        codex.fetch_usage,
        source="codex",
        credential=codex.effective_token(),
        extra=lambda data: {"shared": codex.shared_quota_view(data)},
    )


def public_account(account: dict[str, Any]) -> dict[str, Any]:
    """账号记录的页面投影：只保留可见字段，凭据不离开服务端。"""
    return {key: account[key] for key in ACCOUNT_PUBLIC_FIELDS if key in account}


STATE_UNAVAILABLE: dict[str, Any] = {
    "status": "error",
    "error": "状态暂时不可读写，请稍后重试。",
}


def serve_accounts(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/accounts：已配置账号的列表。"""
    try:
        visible = [public_account(account) for account in storage.load_accounts()]
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    _send_json(handler, 200, {"status": "ok", "accounts": visible})


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """读取请求体的 JSON 对象；缺失或损坏时抛出账号错误。"""
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError as error:
        raise accounts.AccountError("请求格式不正确。") from error
    raw = handler.rfile.read(length) if length > 0 else b""
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text) if text else {}
    except (UnicodeDecodeError, ValueError) as error:
        raise accounts.AccountError("请求体不是有效的 JSON。") from error
    if not isinstance(payload, dict):
        raise accounts.AccountError("请求体应为 JSON 对象。")
    return payload


def serve_account_create(handler: BaseHTTPRequestHandler) -> None:
    """POST /api/accounts：新增账号（密钥平台先验证后保存，Codex 无需密钥）。"""
    try:
        payload = _read_json_body(handler)
        provider = str(payload.get("provider") or "").strip()
        name = str(payload.get("name") or "").strip()
        key = str(payload.get("key") or "").strip()
        if not name:
            raise accounts.AccountError("请填写账号名称。")
        if provider not in accounts.DEVICE_PROVIDERS and provider not in accounts.GOOGLE_PROVIDERS and not key:
            raise accounts.AccountError("请填写密钥。")
        account = accounts.create_account(provider, name, key, _metadata_from(payload))
    except accounts.AccountError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    _send_json(handler, 200, {"status": "ok", "account": public_account(account)})


def _metadata_from(payload: dict[str, Any]) -> dict[str, str]:
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        return {field: str(metadata.get(field) or "") for field in accounts.CODEX_METADATA_FIELDS}
    return {field: str(payload.get(field) or "") for field in accounts.CODEX_METADATA_FIELDS}


def serve_account_update(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """POST /api/accounts/{id}：编辑账号（改名保留连接，新密钥先验证再替换）。"""
    try:
        payload = _read_json_body(handler)
        name = str(payload.get("name") or "").strip()
        key = str(payload.get("key") or "").strip()
        if not name:
            raise accounts.AccountError("请填写账号名称。")
        account = accounts.update_account(account_id, name, key, _metadata_from(payload))
    except accounts.AccountError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    _send_json(handler, 200, {"status": "ok", "account": public_account(account)})


def serve_account_delete(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """DELETE /api/accounts/{id}：删除账号记录、凭据与缓存。"""
    try:
        codex_auth.MANAGER.stop(account_id)
        google_auth.MANAGER.stop(account_id)
        removed = accounts.delete_account(account_id)
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    if not removed:
        _send_json(handler, 404, {"status": "error", "error": "找不到这条订阅。"})
        return
    # 并发刷新可能在 stop 与删除之间重建该账号的 CLI 凭据目录：
    # 记录删除后补一次清理，收窄窗口（后续写回会被 save_account_if_present 拒绝）。
    google.remove_cli_credentials(account_id)
    _send_json(handler, 200, {"status": "ok"})


def _codex_account_or_error(handler: BaseHTTPRequestHandler, account_id: str) -> dict[str, Any] | None:
    """设备码端点的前置校验：账号存在且为 Codex 平台。"""
    try:
        account = _find_public_account(account_id)
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return None
    if account is None:
        _send_json(handler, 404, {"status": "error", "error": "找不到这条订阅。"})
        return None
    if account.get("provider") not in accounts.DEVICE_PROVIDERS:
        _send_json(handler, 400, {"status": "error", "error": "该账号不使用设备码授权。"})
        return None
    return account


def serve_device_status(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """GET /api/accounts/{id}/device：当前设备码授权状态。"""
    account = _codex_account_or_error(handler, account_id)
    if account is None:
        return
    state = codex_auth.MANAGER.status(account_id)
    if state.get("status") == "connected" and account.get("connection") != "connected":
        # 登录已失效（如额度查询 401）：旧授权会话不再报告已连接，需重新授权。
        state = {"status": "idle", "user_code": "", "error": ""}
    _send_json(handler, 200, {"status": "ok", "device": state})


def serve_device_start(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """POST /api/accounts/{id}/device/start：发起设备码授权并开始轮询。"""
    if _codex_account_or_error(handler, account_id) is None:
        return
    try:
        session = codex_auth.MANAGER.start(account_id)
    except codex_auth.DeviceAuthError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    _send_json(
        handler,
        200,
        {
            "status": "ok",
            "device": {"status": session.status, "user_code": session.user_code, "error": ""},
        },
    )


def serve_device_check(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """POST /api/accounts/{id}/device/check：手动触发一次授权轮询。"""
    if _codex_account_or_error(handler, account_id) is None:
        return
    state = codex_auth.MANAGER.check(account_id)
    _send_json(handler, 200, {"status": "ok", "device": state})


def _google_account_or_error(handler: BaseHTTPRequestHandler, account_id: str) -> dict[str, Any] | None:
    """Google 授权端点的前置校验：账号存在且为 Google AI 平台。"""
    try:
        account = _find_public_account(account_id)
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return None
    if account is None:
        _send_json(handler, 404, {"status": "error", "error": "找不到这条订阅。"})
        return None
    if account.get("provider") not in accounts.GOOGLE_PROVIDERS:
        _send_json(handler, 400, {"status": "error", "error": "该账号不使用 Google 授权。"})
        return None
    return account


def serve_google_auth_status(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """GET /api/accounts/{id}/google-auth：当前 Google 授权会话状态。"""
    account = _google_account_or_error(handler, account_id)
    if account is None:
        return
    state = google_auth.MANAGER.status(account_id)
    if state.get("status") == "connected" and account.get("connection") != "connected":
        # 登录已失效（如刷新令牌被拒）：旧会话不再报告已连接，需重新授权。
        state = {"status": "idle", "authorization_url": "", "error": ""}
    _send_json(handler, 200, {"status": "ok", "auth": state})


def serve_google_auth_start(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """POST /api/accounts/{id}/google-auth/start：发起 Google 官方授权。"""
    if _google_account_or_error(handler, account_id) is None:
        return
    try:
        session = google_auth.MANAGER.start(account_id)
    except google_auth.GoogleAuthError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    _send_json(
        handler,
        200,
        {
            "status": "ok",
            "auth": {
                "status": session.status,
                "authorization_url": session.authorization_url,
                "error": "",
            },
        },
    )


def serve_google_auth_submit(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """POST /api/accounts/{id}/google-auth/submit：提交授权码完成连接。"""
    if _google_account_or_error(handler, account_id) is None:
        return
    try:
        payload = _read_json_body(handler)
        pasted = str(payload.get("code") or "").strip()
        if not pasted:
            raise google_auth.GoogleAuthError("请粘贴 Google 返回的授权码。")
        state = google_auth.MANAGER.submit(account_id, pasted)
    except (google_auth.GoogleAuthError, accounts.AccountError) as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    _send_json(handler, 200, {"status": "ok", "auth": state})


def _find_public_account(account_id: str) -> dict[str, Any] | None:
    for account in storage.load_accounts():
        if account.get("id") == account_id:
            return account
    return None


def serve_refresh(handler: BaseHTTPRequestHandler) -> None:
    """POST /api/refresh：立即查询全部已连接账号，各自独立更新。"""
    try:
        refreshed = accounts.refresh_accounts()
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    _send_json(handler, 200, {"status": "ok", "refreshed": len(refreshed)})


def serve_settings(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/settings：可在线调整的服务端设置。"""
    # load_refresh_interval 对读取失败内部兜底为默认值，不会抛出。
    minutes = storage.load_refresh_interval()
    _send_json(
        handler,
        200,
        {"status": "ok", "refresh_interval_minutes": minutes, "allowed_intervals": list(scheduler.ALLOWED_INTERVALS)},
    )


def serve_refresh_interval(handler: BaseHTTPRequestHandler) -> None:
    """POST /api/settings/refresh-interval：修改后台刷新间隔（分钟）。"""
    try:
        payload = _read_json_body(handler)
        minutes = payload.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes not in scheduler.ALLOWED_INTERVALS:
            raise accounts.AccountError("刷新间隔仅支持 5、10、15、30、60 分钟。")
        storage.save_refresh_interval(minutes)
    except accounts.AccountError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    except OSError:
        _send_json(handler, 503, STATE_UNAVAILABLE)
        return
    # 唤醒调度线程：放弃旧周期剩余等待，按新间隔重新计时。
    scheduler.REFRESHER.reschedule()
    _send_json(handler, 200, {"status": "ok", "refresh_interval_minutes": minutes})


ROUTES: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {
    "/api/sources/deepseek/balance": serve_deepseek_balance,
    "/api/sources/mimo/balance": serve_mimo_balance,
    "/api/sources/codex/balance": serve_codex_usage,
    "/api/accounts": serve_accounts,
    "/api/settings": serve_settings,
}

POST_ROUTES: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {
    "/api/accounts": serve_account_create,
    "/api/refresh": serve_refresh,
    "/api/settings/refresh-interval": serve_refresh_interval,
}
