"""Google AI 数据源与账号编排的实现侧测试。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from quota_dashboard import accounts, google_auth, storage
from quota_dashboard.sources import google


USAGE = {
    "plan_name": "Google AI Pro",
    "groups": [
        {
            "name": "Gemini Models",
            "buckets": [
                {"window": "5h", "remaining_fraction": 0.63, "reset_time": "2031-02-17T04:23:00Z"},
                {"window": "weekly", "remaining_fraction": 0.27, "reset_time": "2031-02-22T13:47:00Z"},
            ],
        },
        {"name": "Other Models", "buckets": [{"window": "5h", "used_percentage": 16}]},
    ],
}


def test_usage_groups_converts_buckets_with_original_units() -> None:
    groups = google.usage_groups(USAGE)
    assert [group["name"] for group in groups] == ["Gemini Models", "Other Models"]
    gemini = groups[0]["windows"]
    assert gemini[0]["label"] == "5 小时窗口" and gemini[0]["remaining"] == 63
    assert gemini[0]["reset"] == "2031-02-17T04:23:00Z"
    assert gemini[1]["label"] == "每周窗口" and gemini[1]["remaining"] == 27
    other = groups[1]["windows"]
    assert other[0]["remaining"] == 84  # 100 - used_percentage
    assert other[0]["reset"] == ""


def test_usage_groups_accepts_legacy_window_shapes() -> None:
    legacy = {"groups": [{"group_name": "Gemini 4 Argon", "quota_info": {"remaining_fraction": 0.62, "reset_time": "later"}}]}
    groups = google.usage_groups(legacy)
    assert groups == [{"name": "Gemini 4 Argon", "windows": [{"label": "额度窗口", "remaining": 62, "reset": "later"}]}]


def test_usage_groups_exhausted_bucket_kept_and_missing_data_dropped() -> None:
    data = {"groups": [{"name": "G", "buckets": [
        {"window": "5h", "reset_time": "soon"},  # 无余量但有重置：已用尽
        {"window": "weekly"},  # 既无余量也无重置：丢弃
    ]}]}
    groups = google.usage_groups(data)
    assert groups == [{"name": "G", "windows": [{"label": "5 小时窗口", "remaining": 0, "reset": "soon"}]}]


def test_credits_value_reads_remaining_credits() -> None:
    assert google.credits_value({"remaining_credits": 1347}) == 1347
    assert google.credits_value({"remaining_credits": 0}) == 0
    assert google.credits_value({}) is None
    assert google.credits_value({"remaining_credits": True}) is None


def test_parse_authorization_code_accepts_code_url_and_query() -> None:
    assert google.parse_authorization_code("abc/DEF-123") == "abc/DEF-123"
    assert google.parse_authorization_code("http://127.0.0.1:8085/oauth2callback?code=X&state=Y") == "X"
    assert google.parse_authorization_code("?code=Z") == "Z"
    with pytest.raises(google.GoogleAuthError):
        google.parse_authorization_code("")
    with pytest.raises(google.GoogleAuthError):
        google.parse_authorization_code("http://127.0.0.1:8085/oauth2callback?state=Y")
    with pytest.raises(google.GoogleAuthError):
        google.parse_authorization_code("bad code")


def test_build_authorization_url_carries_pkce_and_offline_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_GOOGLE_CLIENT_ID", "test-client.apps.googleusercontent.com")
    verifier, challenge = google.generate_pkce_pair()
    url = google.build_authorization_url(challenge, "state-1")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    from urllib.parse import parse_qs, urlsplit

    params = parse_qs(urlsplit(url).query)
    assert params["client_id"] == ["test-client.apps.googleusercontent.com"]
    assert params["response_type"] == ["code"]
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert params["redirect_uri"] == [google.redirect_uri()]
    assert params["access_type"] == ["offline"]
    assert params["state"] == ["state-1"]
    import base64
    import hashlib

    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert digest == challenge


def _write_stub_cli(directory: Path, log: Path, *, identity: str, payload: dict[str, object], exit_code: int = 0) -> Path:
    module = Path(__file__).with_name("google_stub_cli.py")
    payload_file = directory / "reply.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    if os.name == "nt":
        cli = directory / "agy.cmd"
        cli.write_text(
            f'@echo off\r\n"{sys.executable}" "{module}" "{log}" "{identity}" "{payload_file}" {exit_code} %*\r\n',
            encoding="utf-8",
        )
    else:
        cli = directory / "agy"
        cli.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{module}" "{log}" "{identity}" "{payload_file}" {exit_code} "$@"\n',
            encoding="utf-8",
        )
        cli.chmod(0o700)
    return cli


def test_run_agy_passes_verified_identity_and_parses_command_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.jsonl"
    cli = _write_stub_cli(tmp_path, log, identity="user@example.test", payload={"command": {"data": USAGE}})
    monkeypatch.setenv("QUOTA_GOOGLE_CLI", str(cli))
    data = google.run_agy("/usage", "user@example.test")
    assert data == USAGE
    recorded = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert recorded == [["user@example.test", ["-p", "/usage", "--output-format", "json"]]]


def test_run_agy_rejects_mismatched_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "calls.jsonl"
    cli = _write_stub_cli(tmp_path, log, identity="expected@example.test", payload={}, exit_code=1)
    monkeypatch.setenv("QUOTA_GOOGLE_CLI", str(cli))
    with pytest.raises(google.GoogleQueryError):
        google.run_agy("/credits", "other@example.test")


def test_google_account_lifecycle_keeps_items_and_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "calls.jsonl"
    cli = _write_stub_cli(
        tmp_path, log,
        identity="family@example.test",
        payload={"command": {"data": {"remaining_credits": 1347, **USAGE}}},
    )
    monkeypatch.setenv("QUOTA_GOOGLE_CLI", str(cli))
    created = accounts.create_account(
        "google", "松云家庭订阅", "", {"plan": "家庭年度套餐", "amount": "149.99", "currency": "USD", "cycle": "每年", "renewal": "2030-10-19"}
    )
    assert created["connection"] == "pending"
    assert created["google_email"] == "" and created["google_items"]["gemini_apps"]["status"] == "manual"

    edited = accounts.update_account(created["id"], "松云家庭订阅二", "", {"plan": "新套餐"})
    assert edited["name"] == "松云家庭订阅二" and edited["plan"] == "新套餐"
    assert edited["connection"] == "pending"  # 改名保留原连接状态

    # 模拟授权完成后的记录，再走刷新。
    record = accounts._find_account(created["id"])
    assert record is not None
    record.update(
        {
            "connection": "connected",
            "google_email": "family@example.test",
            "google_token": {"access_token": "a", "refresh_token": "r", "expiry": "2099-01-01T00:00:00+00:00"},
        }
    )
    storage.save_account(record)
    refreshed = accounts.refresh_one(dict(record))
    items = refreshed["google_items"]
    assert items["antigravity"]["status"] == "ok"
    assert items["antigravity"]["updated"]
    assert [group["name"] for group in items["antigravity"]["groups"]] == ["Gemini Models", "Other Models"]
    assert items["credits"]["value"] == 1347 and items["credits"]["updated"]
    assert items["gemini_apps"]["status"] == "manual"


def test_refresh_google_item_keeps_previous_value_on_failure() -> None:
    previous = {"status": "ok", "updated": "2026-01-01T08:00:00+08:00", "groups": [{"name": "G", "windows": []}], "value": 5}

    def fail() -> object:
        raise RuntimeError("boom")

    result = accounts._refresh_google_item({"credits": dict(previous)}, "credits", fail)
    assert result["status"] == "failed" and result["failed"] is True
    assert result["updated"] == previous["updated"] and result["value"] == previous["value"]
    assert "boom" in result["error"]
