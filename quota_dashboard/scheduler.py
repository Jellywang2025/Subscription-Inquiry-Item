"""后台定时刷新：服务端调度器按管理员设置的间隔查询全部已连接账号。"""
from __future__ import annotations

import math
import os
import threading
from typing import Callable

from . import accounts, storage

# 测试时钟接缝：仅调度等待按此换算，页面与状态中的间隔仍以分钟记。
SECONDS_PER_MINUTE_VARIABLE = "QUOTA_REFRESH_TEST_SECONDS_PER_MINUTE"
ALLOWED_INTERVALS = (5, 10, 15, 30, 60)


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
    """后台刷新调度器：网页关闭后仍按周期执行；间隔修改在下一周期生效。"""

    def __init__(self, refresh: Callable[[], object] = accounts.refresh_accounts) -> None:
        self._refresh = refresh
        self._stop = threading.Event()
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
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)

    def _loop(self) -> None:
        # 周期失败仅吞下以保证线程存活；单账号失败已由 refresh_accounts
        # 写入该账号的 failed/error 字段供页面呈现，整体性 I/O 故障会让
        # /api/accounts 同步返回 503（页面显示账号加载失败），均可观察。
        while not self._stop.wait(interval_seconds()):
            try:
                self._refresh()
            except Exception:  # noqa: BLE001
                continue


REFRESHER = Refresher()
