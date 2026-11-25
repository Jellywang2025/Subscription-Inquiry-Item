"""数据源共享的 HTTP 查询助手。"""
from __future__ import annotations

import json
import urllib.request
from typing import Any


def resolve_settings(
    base_url: str | None,
    api_key: str | None,
    defaults: tuple[str, str | None],
) -> tuple[str, str | None]:
    """合并显式参数与环境变量默认值（显式参数优先）。"""
    env_base, env_key = defaults
    resolved_base = base_url if base_url is not None else env_base
    resolved_key = api_key if api_key is not None else env_key
    return resolved_base, resolved_key


def get_json(
    url: str,
    api_key: str | None,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """GET 一个 JSON 接口（Bearer 认证，可附额外请求头），返回解析后的对象。"""
    request = urllib.request.Request(url, method="GET")
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload: Any = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{url} 响应不是 JSON 对象")
    return payload
