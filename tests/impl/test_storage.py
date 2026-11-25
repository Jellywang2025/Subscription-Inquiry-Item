"""工单 08 实现侧用例：上次成功结果的本机持久化。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from quota_dashboard import storage


def test_state_path_prefers_injection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    injected = tmp_path / "injected-state.json"
    monkeypatch.setenv("QUOTA_STATE_FILE", str(injected))
    assert storage.state_path() == injected


def test_state_path_defaults_to_local_app_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUOTA_STATE_FILE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\fake-local")
    assert storage.state_path() == Path(r"C:\fake-local") / "quota-dashboard" / "state.json"


def test_account_fingerprint_separates_credentials() -> None:
    assert storage.account_fingerprint("key-a") == storage.account_fingerprint("key-a")
    assert storage.account_fingerprint("key-a") != storage.account_fingerprint("key-b")
    assert storage.account_fingerprint(None) == storage.account_fingerprint(None)
    assert storage.account_fingerprint(None) != storage.account_fingerprint("key-a")


def test_save_and_restore_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    result: dict[str, Any] = {"fetched_at": "2026-11-25T02:00:00+08:00", "data": {"balance": 80}}
    storage.save("deepseek", "key-a", result)
    assert storage.restore("deepseek", "key-a") == result


def test_restore_rejects_other_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    storage.save("deepseek", "key-a", {"fetched_at": "t", "data": {}})
    assert storage.restore("deepseek", "key-b") is None


def test_restore_rejects_other_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    storage.save("deepseek", "key-a", {"fetched_at": "t", "data": {}})
    assert storage.restore("mimo", "key-a") is None


def test_save_overwrites_same_source_for_new_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    first: dict[str, Any] = {"fetched_at": "t1", "data": {"balance": 80}}
    second: dict[str, Any] = {"fetched_at": "t2", "data": {"balance": 65}}
    storage.save("deepseek", "key-a", first)
    storage.save("deepseek", "key-b", second)
    assert storage.restore("deepseek", "key-b") == second
    assert storage.restore("deepseek", "key-a") is None


def test_restore_tolerates_missing_or_broken_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "state.json"
    monkeypatch.setenv("QUOTA_STATE_FILE", str(path))
    assert storage.restore("deepseek", "key-a") is None  # 文件不存在
    path.write_text("{not json", encoding="utf-8")
    assert storage.restore("deepseek", "key-a") is None  # 内容损坏
    path.write_text('["not", "an", "object"]', encoding="utf-8")
    assert storage.restore("deepseek", "key-a") is None  # 顶层不是对象
    path.write_text(json.dumps({"deepseek": {"account": "x"}}), encoding="utf-8")
    assert storage.restore("deepseek", "key-a") is None  # 条目缺少结果
