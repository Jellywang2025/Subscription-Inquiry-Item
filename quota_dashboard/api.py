"""看板的 JSON API：向页面提供各数据源的查询结果。"""
from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from typing import Any

from . import storage
from .sources import codex, deepseek, mimo


def describe_query_error(error: Exception) -> str:
    """把数据源查询异常转换为页面可展示的中文原因。"""
    if isinstance(error, urllib.error.HTTPError):
        return f"官方接口返回 HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, TimeoutError):
            return "请求超时"
        return f"连接失败（{reason}）"
    if isinstance(error, TimeoutError):
        return "请求超时"
    if isinstance(error, PermissionError):
        return str(error)
    return f"查询异常（{error}）"


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


ROUTES: dict[str, Callable[[BaseHTTPRequestHandler], None]] = {
    "/api/sources/deepseek/balance": serve_deepseek_balance,
    "/api/sources/mimo/balance": serve_mimo_balance,
    "/api/sources/codex/balance": serve_codex_usage,
}
