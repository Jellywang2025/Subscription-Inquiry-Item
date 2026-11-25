"""上次成功结果的本机持久化：按数据来源与账户指纹绑定。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def default_state_path() -> Path:
    """本机默认状态文件路径（Windows 本地应用数据目录下）。"""
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("USERPROFILE") or str(Path.home())
    return Path(root) / "quota-dashboard" / "state.json"


def state_path() -> Path:
    """状态文件路径：QUOTA_STATE_FILE 注入优先（测试与验收用），否则本机默认。"""
    override = os.environ.get("QUOTA_STATE_FILE")
    return Path(override) if override else default_state_path()


def account_fingerprint(credential: str | None) -> str:
    """凭据的可识别指纹（截断哈希）：同凭据同账户，换凭据即换账户。"""
    return hashlib.sha256((credential or "").encode("utf-8")).hexdigest()[:16]


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save(source: str, credential: str | None, result: dict[str, Any]) -> None:
    """记录一个来源在当前账户下的上次成功结果（尽力而为，落盘失败不影响查询）。"""
    entry = {"account": account_fingerprint(credential), "result": result}
    try:
        path = state_path()
        with _LOCK:
            state = _load(path)
            state[source] = entry
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def restore(source: str, credential: str | None) -> dict[str, Any] | None:
    """取回同一来源、同一账户的上次成功结果；账户变化或无记录时返回 None。"""
    with _LOCK:
        entry = _load(state_path()).get(source)
    if not isinstance(entry, dict):
        return None
    if entry.get("account") != account_fingerprint(credential):
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else None
