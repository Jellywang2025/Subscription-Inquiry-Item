"""AC-11 through fresh dashboard CLI processes and browser profiles."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from deepseek_source import BALANCE_PATH, DeepSeekSource
from test_codex_windows import CODEX_TOKEN, USAGE_BASE_PATH, USAGE_PATH
from test_deepseek_balance import (
    CNY,
    TIME_LABEL,
    USD,
    balance,
    money,
    open_chrome,
    poll,
    run_dashboard,
    success_time,
    wait_for_new_clock_value,
)
from test_mimo_balance import MIMO_KEY, MIMO_PATH, official_balance


DEEPSEEK_KEY = "contract-deepseek-account-a"
OTHER_DEEPSEEK_KEY = "contract-deepseek-account-b"
QUERY_FAILURE = "查询失败: 官方接口返回 HTTP 503"
ANY_AMOUNT = money(rf"(?:{CNY}|{USD})", r"[-−]?\d+(?:\.\d+)?")
CNY_80 = money(CNY, r"80\.00")
CNY_180 = money(CNY, r"180\.00")
CNY_160 = money(CNY, r"160\.00")


@dataclass(frozen=True)
class Cards:
    deepseek: str
    mimo: str


@dataclass(frozen=True)
class LaunchHistory:
    initial: Cards
    same_account: Cards
    switched_account: Cards


@contextmanager
def serve_balances(
    deepseek_key: str, deepseek_ok: bool, mimo_balance: dict[str, object] | None,
) -> Iterator[DeepSeekSource]:
    deepseek = {"is_available": True, "balance_infos": [balance("CNY", "80.00", "70.00", "10.00")]}
    routes = {
        BALANCE_PATH: (deepseek_key, deepseek if deepseek_ok else None),
        MIMO_PATH: (MIMO_KEY, mimo_balance),
        USAGE_PATH: (CODEX_TOKEN, {"rate_limit": {"primary_window": {"used_percent": 25}}}),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = routes.get(self.path)
            if route is None:
                self.send_error(404)
                return
            key, payload = route
            bearer = self.headers.get("Authorization") == f"Bearer {key}"
            mimo_key = self.path == MIMO_PATH and self.headers.get("api-key") == key
            if not (bearer or mimo_key):
                self.send_error(401)
                return
            if payload is None:
                self.send_error(503)
                return
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield DeepSeekSource(f"http://127.0.0.1:{server.server_port}")
        finally:
            server.shutdown()
            thread.join()


def observe_launch(
    directory: Path, state_file: Path, *,
    deepseek_key: str, deepseek_ok: bool, mimo_balance: dict[str, object] | None,
) -> Cards:
    with (
        serve_balances(deepseek_key, deepseek_ok, mimo_balance) as source,
        run_dashboard(
            source,
            directory,
            QUOTA_STATE_FILE=str(state_file),
            DEEPSEEK_API_KEY=deepseek_key,
            QUOTA_MIMO_BASE_URL=source.base_url,
            MIMO_API_KEY=MIMO_KEY,
            QUOTA_CODEX_BASE_URL=f"{source.base_url}{USAGE_BASE_PATH}",
            QUOTA_CODEX_ACCESS_TOKEN=CODEX_TOKEN,
        ) as url,
        open_chrome(directory) as browser,
    ):
        browser.open(url)
        deepseek = poll(browser.card, lambda card: bool(success_time(card)) if deepseek_ok else QUERY_FAILURE in card)
        mimo = poll(
            lambda: browser.card("MiMo"),
            lambda card: bool(success_time(card)) if mimo_balance is not None else QUERY_FAILURE in card,
        )
        return Cards(deepseek, mimo)


def visible_success_time(card: str) -> str | None:
    match = TIME_LABEL.search(card)
    return match[0] if match else None


@pytest.fixture(scope="module")
def launch_history(tmp_path_factory: pytest.TempPathFactory) -> LaunchHistory:
    directory = tmp_path_factory.mktemp("ac11")
    state_file = directory / "quota-state.json"
    initial_dir, same_dir, switched_dir = (directory / name for name in ("initial", "same", "switched"))
    for launch_dir in (initial_dir, same_dir, switched_dir):
        launch_dir.mkdir()

    initial = observe_launch(
        initial_dir, state_file, deepseek_key=DEEPSEEK_KEY, deepseek_ok=True, mimo_balance=official_balance(),
    )
    assert CNY_80.search(initial.deepseek) and success_time(initial.deepseek), (
        f"启动一 DeepSeek 应查到 ¥80.00 和成功时间，实得 {initial.deepseek!r}"
    )
    assert CNY_180.search(initial.mimo) and success_time(initial.mimo), (
        f"启动一 MiMo 应查到 ¥180.00 和成功时间，实得 {initial.mimo!r}"
    )
    # Separate the launches by the visible clock's precision to detect time being rewritten.
    wait_for_new_clock_value(success_time(initial.deepseek))

    updated_mimo = {**official_balance(), "balance_infos": [{
        "currency": "CNY", "cash_balance": "80.00", "gift_balance": "20.00",
        "frozen_balance": "20.00", "credit_limit": "500.00", "credit_available": "80.00",
        "total_available": "180.00",
    }]}
    same = observe_launch(
        same_dir, state_file, deepseek_key=DEEPSEEK_KEY, deepseek_ok=False, mimo_balance=updated_mimo,
    )
    wait_for_new_clock_value(success_time(same.mimo))

    switched = observe_launch(
        switched_dir, state_file, deepseek_key=OTHER_DEEPSEEK_KEY, deepseek_ok=False, mimo_balance=None,
    )
    return LaunchHistory(initial, same, switched)


def test_ac11_same_account_retains_balance_and_original_time(launch_history: LaunchHistory) -> None:
    before, after = launch_history.initial.deepseek, launch_history.same_account.deepseek
    assert QUERY_FAILURE in after, f"同一账户重启时应显示 HTTP 503 查询失败，实得 {after!r}"
    assert CNY_80.search(after) and visible_success_time(after) == visible_success_time(before), (
        f"同一账户重启查询失败应保留 ¥80.00 与原成功时间，实得 {before!r} → {after!r}"
    )


def test_ac11_changed_account_shows_first_query_failure(launch_history: LaunchHistory) -> None:
    after = launch_history.switched_account.deepseek
    assert QUERY_FAILURE in after, f"新账户应显示 HTTP 503 首次查询失败，实得 {after!r}"
    assert not ANY_AMOUNT.search(after) and visible_success_time(after) is None, (
        f"新账户首次失败时金额和成功时间应为空，实得 {after!r}"
    )


def test_ac11_sources_update_and_persist_independently(launch_history: LaunchHistory) -> None:
    deepseek, updated, restarted = (
        launch_history.same_account.deepseek,
        launch_history.same_account.mimo,
        launch_history.switched_account.mimo,
    )
    assert CNY_160.search(updated) and visible_success_time(updated) and "查询失败" not in updated, (
        f"DeepSeek 查询失败当轮，MiMo 应更新为 ¥160.00 并显示成功时间，实得 {updated!r}"
    )
    assert QUERY_FAILURE in deepseek and QUERY_FAILURE in restarted, (
        f"应先观察到 DeepSeek 查询失败，再观察到 MiMo 重启后的查询失败，实得 {deepseek!r}、{restarted!r}"
    )
    assert (
        CNY_80.search(deepseek) and not CNY_160.search(deepseek)
        and CNY_160.search(restarted) and not CNY_80.search(restarted) and not CNY_180.search(restarted)
        and visible_success_time(restarted) == visible_success_time(updated)
    ), (
        "同轮 DeepSeek 应保留 ¥80.00，MiMo 再次重启后应保留自己的最新值 ¥160.00 和原成功时间，"
        f"实得 DeepSeek {deepseek!r}，MiMo {updated!r} → {restarted!r}"
    )
