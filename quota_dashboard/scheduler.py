"""后台定时刷新：服务端调度器按管理员设置的间隔查询全部已连接账号。"""
from __future__ import annotations

import math
import os
import threading
import time
from typing import Callable

from . import accounts, storage

# 测试时钟接缝：仅调度等待按此换算，页面与状态中的间隔仍以分钟记。
SECONDS_PER_MINUTE_VARIABLE = "QUOTA_REFRESH_TEST_SECONDS_PER_MINUTE"
ALLOWED_INTERVALS = (5, 10, 15, 30, 60)
POLL_SLICE_SECONDS = 0.5


def _seconds_per_minute() -> float:
    """时钟换算；非法或非正值回退真实 60 秒，避免零间隔空转。"""
    try:
        value = float(os.environ.get(SECONDS_PER_MINUTE_VARIABLE) or 60.0)
    except ValueError:
        return 60.0
    if math.isfinite(value) and value > 0:
        return value
    return 60.0


def interval_seconds() -> float:
    """当前调度间隔（秒）：配置分钟 × 时钟换算。"""
    return storage.load_refresh_interval() * _seconds_per_minute()


class Refresher:
    """后台刷新调度器：网页关闭后仍按周期执行；间隔修改立即重新计时。"""

    def __init__(self, refresh: Callable[[], object] = accounts.refresh_accounts) -> None:
        self._refresh = refresh
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """启动调度线程（重复调用无副作用）。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="background-refresh", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """停止调度并等待线程退出（测试与服务关闭时调用）。"""
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

    def reschedule(self) -> None:
        """间隔修改后唤醒调度线程：放弃旧周期的剩余等待，按新间隔重新计时。"""
        self._wake.set()

    def _loop(self) -> None:
        # 周期失败仅吞下以保证线程存活；单账号失败已由 refresh_accounts
        # 写入该账号的 failed/error 字段供页面呈现，整体性 I/O 故障会让
        # /api/accounts 同步返回 503（页面显示账号加载失败），均可观察。
        while True:
            outcome = self._wait_interval(interval_seconds())
            if outcome == "stop":
                return
            if outcome == "rescheduled":
                continue  # 间隔已修改：按新间隔重新计时，不立即查询。
            try:
                self._refresh()
            except Exception:  # noqa: BLE001
                continue

    def _wait_interval(self, seconds: float) -> str:
        """等待一个调度周期；返回 stop / rescheduled / elapsed。"""
        deadline = time.monotonic() + max(seconds, POLL_SLICE_SECONDS)
        while True:
            if self._stop.is_set():
                return "stop"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "elapsed"
            if self._wake.wait(min(remaining, POLL_SLICE_SECONDS)):
                if self._stop.is_set():
                    return "stop"
                self._wake.clear()
                return "rescheduled"


REFRESHER = Refresher()
