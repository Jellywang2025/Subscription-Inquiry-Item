"""后台定时刷新：服务端调度器按管理员设置的间隔查询全部已连接账号。"""
from __future__ import annotations

import os
import threading
from typing import Callable

from . import accounts, storage

DEFAULT_INTERVAL_MINUTES = 15
# 测试时钟接缝：仅调度等待按此换算，页面与状态中的间隔仍以分钟记。
SECONDS_PER_MINUTE_VARIABLE = "QUOTA_REFRESH_TEST_SECONDS_PER_MINUTE"
ALLOWED_INTERVALS = (5, 10, 15, 30, 60)


def _seconds_per_minute() -> float:
    try:
        return float(os.environ.get(SECONDS_PER_MINUTE_VARIABLE) or 60.0)
    except ValueError:
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
        """停止调度（测试与服务关闭时调用）。"""
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(interval_seconds()):
            try:
                self._refresh()
            except Exception:  # noqa: BLE001 - 后台周期失败不终止调度
                continue


REFRESHER = Refresher()
