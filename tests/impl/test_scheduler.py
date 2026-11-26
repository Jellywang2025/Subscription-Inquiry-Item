"""08 票实现侧测试：后台刷新调度器、间隔存储与设置 API。"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlencode

import pytest
from conftest import free_port, login_headers, serve_in_thread

from quota_dashboard import scheduler, storage
from quota_dashboard.server import create_server

ADMIN_PASSWORD = "impl-scheduler-password"


@contextmanager
def configured_password() -> Iterator[None]:
    import os

    previous = os.environ.get("QUOTA_ADMIN_PASSWORD")
    os.environ["QUOTA_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("QUOTA_ADMIN_PASSWORD", None)
        else:
            os.environ["QUOTA_ADMIN_PASSWORD"] = previous


def call(port: int, path: str, method: str = "GET", body: str | None = None) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body.encode("utf-8") if body is not None else None,
        method=method,
        headers={**login_headers(port, ADMIN_PASSWORD), "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def test_default_interval_is_fifteen_minutes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    assert storage.load_refresh_interval() == 15


def test_saved_interval_round_trips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    storage.save_refresh_interval(5)
    assert storage.load_refresh_interval() == 5


def test_interval_survives_corrupt_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state.json"
    monkeypatch.setenv("QUOTA_STATE_FILE", str(state))
    state.write_text(json.dumps({"settings": {"refresh_interval_minutes": "bogus"}}), encoding="utf-8")
    assert storage.load_refresh_interval() == 15


def test_interval_seconds_uses_test_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    storage.save_refresh_interval(15)
    monkeypatch.setenv(scheduler.SECONDS_PER_MINUTE_VARIABLE, "1")
    assert scheduler.interval_seconds() == 15.0


def test_refresher_runs_on_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    calls: list[float] = []
    refresher = scheduler.Refresher(refresh=lambda: calls.append(time.monotonic()))
    monkeypatch.setenv(scheduler.SECONDS_PER_MINUTE_VARIABLE, "1")
    storage.save_refresh_interval(5)
    refresher.start()
    try:
        deadline = time.monotonic() + 15
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        refresher.stop()
    assert len(calls) >= 2, "调度器应在间隔周期到达时执行刷新"


def test_refresher_survives_refresh_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    refresher = scheduler.Refresher(refresh=flaky)
    monkeypatch.setenv(scheduler.SECONDS_PER_MINUTE_VARIABLE, "1")
    storage.save_refresh_interval(5)
    refresher.start()
    try:
        deadline = time.monotonic() + 15
        while len(calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        refresher.stop()
    assert len(calls) >= 2, "首个周期抛异常后调度器应继续运行"


def test_interval_change_takes_effect_next_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    calls: list[float] = []
    refresher = scheduler.Refresher(refresh=lambda: calls.append(time.monotonic()))
    monkeypatch.setenv(scheduler.SECONDS_PER_MINUTE_VARIABLE, "1")
    storage.save_refresh_interval(15)
    refresher.start()
    try:
        deadline = time.monotonic() + 15
        while len(calls) < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        storage.save_refresh_interval(5)
        gap_deadline = time.monotonic() + 12
        while len(calls) < 2 and time.monotonic() < gap_deadline:
            time.sleep(0.05)
    finally:
        refresher.stop()
    assert len(calls) >= 2
    gap = calls[1] - calls[0]
    assert gap < 10, f"间隔改为 5 测试秒后下一周期应缩短，实得 {gap:.1f} 秒"


def test_settings_api_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    with configured_password():
        httpd = create_server(free_port())
        with httpd, serve_in_thread(httpd):
            port = int(httpd.server_address[1])
            initial = call(port, "/api/settings")
            assert initial["refresh_interval_minutes"] == 15
            changed = call(
                port,
                "/api/settings/refresh-interval",
                method="POST",
                body=json.dumps({"minutes": 5}),
            )
            assert changed["refresh_interval_minutes"] == 5
            after = call(port, "/api/settings")
            assert after["refresh_interval_minutes"] == 5
    assert storage.load_refresh_interval() == 5


def test_refresh_interval_rejects_unknown_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    with configured_password():
        httpd = create_server(free_port())
        with httpd, serve_in_thread(httpd):
            port = int(httpd.server_address[1])
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/settings/refresh-interval",
                data=json.dumps({"minutes": 7}).encode("utf-8"),
                method="POST",
                headers={**login_headers(port, ADMIN_PASSWORD), "Content-Type": "application/json"},
            )
            with pytest.raises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request, timeout=5)
            assert raised.value.code == 400
    assert storage.load_refresh_interval() == 15


def test_persisted_interval_reloads_for_new_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUOTA_STATE_FILE", str(tmp_path / "state.json"))
    storage.save_refresh_interval(30)
    # 新进程语义：每次读取都从磁盘状态文件恢复。
    assert storage.load_refresh_interval() == 30


def test_scheduler_singleton_exists() -> None:
    # launcher.start/stop 会复用模块级单例；这里只验证它可被启动与停止。
    scheduler.REFRESHER.start()
    scheduler.REFRESHER.stop()
    assert scheduler.REFRESHER is not None
