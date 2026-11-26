"""上次成功结果的本机持久化：按数据来源与账户指纹绑定。"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
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
    """读取状态文件；文件不存在或内容损坏视为空，其他读取错误在短重试后上抛。"""
    for attempt in range(2):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except OSError:
            # Windows 上杀软或并发进程可能短暂持有文件；重试一次再向上报告。
            if attempt == 0:
                time.sleep(0.05)
                continue
            raise
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def save(source: str, credential: str | None, result: dict[str, Any]) -> None:
    """记录一个来源在当前账户下的上次成功结果（尽力而为，落盘失败不影响查询）。"""
    entry = {"account": account_fingerprint(credential), "result": result}
    try:
        path = state_path()
        with _LOCK:
            state = _load(path)
            state[source] = entry
            _write_state(path, state)
    except OSError:
        return


def restore(source: str, credential: str | None) -> dict[str, Any] | None:
    """取回同一来源、同一账户的上次成功结果；账户变化或无记录时返回 None。"""
    try:
        with _LOCK:
            entry = _load(state_path()).get(source)
    except OSError:
        return None
    if not isinstance(entry, dict):
        return None
    if entry.get("account") != account_fingerprint(credential):
        return None
    result = entry.get("result")
    return result if isinstance(result, dict) else None


def load_accounts() -> list[dict[str, Any]]:
    """已配置的订阅账号列表；未配置或状态损坏时为空列表。"""
    with _LOCK:
        accounts = _load(state_path()).get("accounts")
    if not isinstance(accounts, list):
        return []
    return [account for account in accounts if isinstance(account, dict)]


def save_account(account: dict[str, Any]) -> None:
    """按 id 新增或整体替换一条账号记录，保持账号在列表中的原有位置。"""
    with _LOCK:
        state = _load(state_path())
        accounts = state.get("accounts")
        if not isinstance(accounts, list):
            accounts = []
        account_id = account.get("id")
        for index, item in enumerate(accounts):
            if isinstance(item, dict) and item.get("id") == account_id:
                accounts[index] = account
                break
        else:
            accounts.append(account)
        state["accounts"] = accounts
        _write_state(state_path(), state)


def save_account_if_present(account: dict[str, Any]) -> bool:
    """仅在账号仍存在时替换记录；已被删除时返回 False（不复活）。"""
    with _LOCK:
        state = _load(state_path())
        accounts = state.get("accounts")
        if not isinstance(accounts, list):
            return False
        account_id = account.get("id")
        for index, item in enumerate(accounts):
            if isinstance(item, dict) and item.get("id") == account_id:
                accounts[index] = account
                state["accounts"] = accounts
                _write_state(state_path(), state)
                return True
        return False


def delete_account(account_id: str) -> bool:
    """删除指定 id 的账号记录；不存在时返回 False。"""
    with _LOCK:
        state = _load(state_path())
        accounts = state.get("accounts")
        if not isinstance(accounts, list):
            return False
        remaining = [
            item for item in accounts
            if not (isinstance(item, dict) and item.get("id") == account_id)
        ]
        if len(remaining) == len(accounts):
            return False
        state["accounts"] = remaining
        _write_state(state_path(), state)
        return True


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """先写临时文件再原子替换，避免崩溃或磁盘异常截断既有状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


DEFAULT_REFRESH_INTERVAL_MINUTES = 15


def load_refresh_interval() -> int:
    """后台刷新间隔（分钟）；未配置或损坏时取默认 15。"""
    try:
        with _LOCK:
            settings = _load(state_path()).get("settings")
    except OSError:
        return DEFAULT_REFRESH_INTERVAL_MINUTES
    if not isinstance(settings, dict):
        return DEFAULT_REFRESH_INTERVAL_MINUTES
    minutes = settings.get("refresh_interval_minutes")
    if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
        return minutes
    return DEFAULT_REFRESH_INTERVAL_MINUTES


def save_refresh_interval(minutes: int) -> None:
    """保存后台刷新间隔（分钟）。"""
    with _LOCK:
        state = _load(state_path())
        settings = state.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        settings["refresh_interval_minutes"] = minutes
        state["settings"] = settings
        _write_state(state_path(), state)
