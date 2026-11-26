"""订阅管理的 JSON API：向页面提供账号配置与各数据源的查询结果。"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Any

from . import accounts, storage
from .sources import codex, deepseek, mimo
from .sources.http import describe_query_error

__all__ = [
    "ROUTES",
    "POST_ROUTES",
    "serve_accounts",
    "serve_account_create",
    "serve_account_update",
    "serve_account_delete",
    "serve_refresh",
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


def serve_accounts(handler: BaseHTTPRequestHandler) -> None:
    """GET /api/accounts：已配置账号的列表。"""
    visible = [public_account(account) for account in storage.load_accounts()]
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
    """POST /api/accounts：新增账号（DeepSeek 密钥先验证后保存）。"""
    try:
        payload = _read_json_body(handler)
        provider = str(payload.get("provider") or "").strip()
        name = str(payload.get("name") or "").strip()
        key = str(payload.get("key") or "").strip()
        if not name:
            raise accounts.AccountError("请填写账号名称。")
        if not key:
            raise accounts.AccountError("请填写密钥。")
        account = accounts.create_account(provider, name, key)
    except accounts.AccountError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    _send_json(handler, 200, {"status": "ok", "account": public_account(account)})


def serve_account_update(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """POST /api/accounts/{id}：编辑账号（改名保留连接，新密钥先验证再替换）。"""
    try:
        payload = _read_json_body(handler)
        name = str(payload.get("name") or "").strip()
        key = str(payload.get("key") or "").strip()
        if not name:
            raise accounts.AccountError("请填写账号名称。")
        account = accounts.update_account(account_id, name, key)
    except accounts.AccountError as error:
        _send_json(handler, 400, {"status": "error", "error": str(error)})
        return
    _send_json(handler, 200, {"status": "ok", "account": public_account(account)})


def serve_account_delete(handler: BaseHTTPRequestHandler, account_id: str) -> None:
    """DELETE /api/accounts/{id}：删除账号记录、凭据与缓存。"""
    if not accounts.delete_account(account_id):
        _send_json(handler, 404, {"status": "error", "error": "找不到这条订阅。"})
        return
    _send_json(handler, 200, {"status": "ok"})


def serve_refresh(handler: BaseHTTPRequestHandler) -> None:
    """POST /api/refresh：立即查询全部已连接账号，各自独立更新。"""
    refreshed = accounts.refresh_accounts()
    _send_json(handler, 200, {"status": "ok", "refreshed": len(refreshed)})


ROUTES: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {
    "/api/sources/deepseek/balance": serve_deepseek_balance,
    "/api/sources/mimo/balance": serve_mimo_balance,
    "/api/sources/codex/balance": serve_codex_usage,
    "/api/accounts": serve_accounts,
}

POST_ROUTES: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {
    "/api/accounts": serve_account_create,
    "/api/refresh": serve_refresh,
}
