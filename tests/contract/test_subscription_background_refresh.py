"""Ticket 08 / Spec 0.2.0: AC-05 and AC-08 through the CLI, browser and provider."""

import json
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import ProxyHandler, Request as HttpRequest, build_opener

import pytest

from deepseek_source import BALANCE_PATH, BalanceReply, DeepSeekSource, Request, serve_deepseek
from test_deepseek_balance import Browser, open_chrome, run_dashboard
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, sign_in, wait_for
from test_subscription_codex_second_account import by_name, cross_timestamp_tick, updated_at
from test_subscription_deepseek_account import SEARCH, add_account, card_text, reply, resize, settings, type_into, view
from test_subscription_refresh_recovery import KEY_A, KEY_B, NAME_A, NAME_B, UNAVAILABLE, UPSTREAM_503, cny, successful_card
from test_subscription_search_bookmarks import account_entries

DEFAULT_MINUTES = 15
CHANGED_MINUTES = 5
# Test clock seam: only scheduler waits are scaled, so displayed/stored intervals
# stay in minutes, and success timestamps continue to use wall time.
SECONDS_PER_TEST_MINUTE = 1
DEADLINE = DEFAULT_MINUTES * SECONDS_PER_TEST_MINUTE + 5
NAME_DELETED = "待删除账号"
KEY_DELETED = "contract-background-deleted-C789"
INTERVAL_CONTROL = """shown('main select:not(:disabled)').find(select =>
    /刷新间隔/.test([select.getAttribute('aria-label'), ...[...select.labels].map(label => label.textContent),
        ...String(select.getAttribute('aria-labelledby') || '').split(/\\s+/)
            .map(id => document.getElementById(id)?.textContent)].join(' ')) ||
    (/后台刷新/.test(select.closest('section, fieldset')?.innerText || '') &&
        [...select.options].some(option => /分钟/.test(option.text))))"""
type AccountRows = dict[str, dict[str, Any]]
type ReadAccounts = Callable[[], AccountRows]


@contextmanager
def serve(source: DeepSeekSource, directory: Path, state_file: Path) -> Iterator[str]:
    directory.mkdir()
    with run_dashboard(
        source, directory,
        QUOTA_STATE_FILE=str(state_file), QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
        QUOTA_REFRESH_TEST_SECONDS_PER_MINUTE=str(SECONDS_PER_TEST_MINUTE),
        DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_CODEX_ACCESS_TOKEN="",
        QUOTA_CODEX_BASE_URL=UNAVAILABLE, QUOTA_MIMO_BASE_URL=UNAVAILABLE,
        CODEX_HOME=str(directory / "codex-home"),
    ) as origin:
        yield origin


@contextmanager
def browser_session(directory: Path, url: str, page: str = OVERVIEW) -> Iterator[Browser]:
    directory.mkdir()
    with open_chrome(directory) as browser:
        resize(browser, 1920, 1080)
        sign_in(browser, url, page)
        yield browser


def expect_interval(browser: Browser, minutes: int) -> None:
    text = f"(({INTERVAL_CONTROL})?.selectedOptions[0]?.text.trim() || '')"
    expected = json.dumps(rf"^(?:每\s*)?{minutes}\s*分钟$")
    assert wait_for(browser, f"new RegExp({expected}).test({text})"), (
        f"配置管理应显示 {minutes} 分钟的刷新间隔，实得 {observe(browser, text)!r}"
    )


def change_interval(browser: Browser, minutes: int) -> None:
    expected = json.dumps(rf"^(?:每\s*)?{minutes}\s*分钟$")
    assert observe(browser, f"""(() => {{
        const select = {INTERVAL_CONTROL};
        const option = select && [...select.options].find(item => !item.disabled &&
            new RegExp({expected}).test(item.text.trim()));
        if (!option) return false;
        select.value = option.value;
        select.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }})()"""), f"配置管理应能选择每 {minutes} 分钟自动刷新"
    expect_interval(browser, minutes)


def account_reader(browser: Browser, origin: str) -> ReadAccounts:
    # Retain the authenticated session for read-only API observations after Chrome exits.
    cookies = browser.call("Network.getCookies", urls=[origin])["cookies"]
    assert cookies, "管理员登录后应有服务端会话"
    cookie = "; ".join(f"{item['name']}={item['value']}" for item in cookies)
    opener = build_opener(ProxyHandler({}))

    def read_accounts() -> AccountRows:
        request = HttpRequest(f"{origin}api/accounts", headers={"Cookie": cookie, "Cache-Control": "no-cache"})
        with opener.open(request, timeout=4) as response:
            assert response.status == 200, f"账号回读应返回 HTTP 200，实得 {response.status}"
            return {row["name"]: row for row in json.load(response)["accounts"]}

    assert set(read_accounts()) == set(by_name(browser)), "关页前独立 HTTP 会话应能读到相同的账号列表"
    return read_accounts


def wait_for_accounts(read: ReadAccounts, condition: Callable[[AccountRows], bool], description: str) -> AccountRows:
    deadline = time.monotonic() + 4
    while True:
        rows = read()
        if condition(rows):
            return rows
        if time.monotonic() >= deadline:
            pytest.fail(f"{description}，实得账号 {rows!r}")
        time.sleep(0.05)


def request_count(source: DeepSeekSource) -> int:
    return len(source.wait_for_requests(0, 0))


def observe_cycle(source: DeepSeekSource, offset: int, *identities: str) -> tuple[int, float]:
    expected = {Request("GET", BALANCE_PATH, True, identity=identity) for identity in identities}
    observed = source.wait_for_requests(offset + len(expected), DEADLINE)[offset:]
    assert len(observed) == len(expected) and set(observed) == expected, (
        f"关页时应由服务器按周期查询 {sorted(identities)!r}，"
        f"等待 {DEADLINE} 秒内实得 {observed!r}"
    )
    return offset + len(observed), time.monotonic()


def expect_cadence(first: float, second: float, minutes: int) -> None:
    expected = minutes * SECONDS_PER_TEST_MINUTE
    elapsed = second - first
    assert expected - 1 <= elapsed <= expected + 3, (
        f"连续后台查询应相隔约 {expected} 测试秒（配置 {minutes} 分钟），实得 {elapsed:.2f} 秒"
    )


def expect_persisted_snapshot(actual: dict[str, Any], before: dict[str, Any]) -> None:
    for key in ("id", "name", "provider", "key_tail", "balances", "updated"):
        assert actual[key] == before[key], f"服务重启应保留账号的 {key}"


def test_ac08_default_interval_is_below_account_list(tmp_path: Path) -> None:
    replies = {KEY_A: ("A", reply(cny("80.00")))}
    with serve_deepseek(accounts=replies) as source, serve(source, tmp_path / "cli", tmp_path / "state.json") as origin:
        with browser_session(tmp_path / "browser", origin) as browser:
            add_account(browser, source, NAME_A, KEY_A, "A")
            view(browser, SETTINGS)
            assert wait_for(browser, f"({account_entries(SETTINGS)}).some(row => row.name === {json.dumps(NAME_A)})")
            expect_interval(browser, DEFAULT_MINUTES)
            assert observe(browser, f"""(() => {{
                const select = {INTERVAL_CONTROL};
                const rows = shown('main li, main tr, main [role=row]').filter(row =>
                    shown('button, a[href], [role=button]', row).some(control =>
                        (control.getAttribute('aria-label') || control.innerText.trim()) === '编辑'));
                return rows.length > 0 && rows.every(row =>
                    row.getBoundingClientRect().bottom <= select.getBoundingClientRect().top);
            }})()"""), "刷新间隔设置应位于配置账号列表下方"


def test_ac08_changed_interval_is_read_back_and_controls_cadence(tmp_path: Path) -> None:
    replies = {KEY_A: ("A", reply(cny("80.00")))}
    with serve_deepseek(accounts=replies) as source, serve(source, tmp_path / "cli", tmp_path / "state.json") as origin:
        with browser_session(tmp_path / "edit", origin) as browser:
            add_account(browser, source, NAME_A, KEY_A, "A")
            view(browser, SETTINGS)
            change_interval(browser, CHANGED_MINUTES)
            with browser_session(tmp_path / "readback", settings(origin), SETTINGS) as fresh_browser:
                expect_interval(fresh_browser, CHANGED_MINUTES)

        offset, first = observe_cycle(source, request_count(source), "A")
        _, second = observe_cycle(source, offset, "A")
        expect_cadence(first, second, CHANGED_MINUTES)


def test_ac08_refreshes_with_all_pages_closed_and_reopens_with_latest_result(tmp_path: Path) -> None:
    replies = {KEY_A: ("A", reply(cny("80.00")))}
    with serve_deepseek(accounts=replies) as source, serve(source, tmp_path / "cli", tmp_path / "state.json") as origin:
        with browser_session(tmp_path / "before-close", origin) as browser:
            add_account(browser, source, NAME_A, KEY_A, "A")
            view(browser, OVERVIEW)
            successful_card(browser, NAME_A, "80.00")
            before = by_name(browser)[NAME_A]
            read = account_reader(browser, origin)
            cross_timestamp_tick(before)

        closed_at = datetime.now().astimezone().replace(microsecond=0)
        replies[KEY_A] = ("A", reply(cny("65.00")))
        offset, first = observe_cycle(source, request_count(source), "A")
        latest = wait_for_accounts(read, lambda rows: rows[NAME_A]["balances"] != before["balances"],
                                   "重新开页前服务端应已保存关页期间的余额")
        success = updated_at(latest[NAME_A])
        assert success > updated_at(before) and closed_at <= success <= datetime.now().astimezone(), (
            f"实际成功时间应发生在关页后，关闭时刻 {closed_at!s}，成功时间 {success!s}"
        )
        _, second = observe_cycle(source, offset, "A")
        expect_cadence(first, second, DEFAULT_MINUTES)

        with browser_session(tmp_path / "reopened", origin) as reopened:
            successful_card(reopened, NAME_A, "65.00")
            assert updated_at(by_name(reopened)[NAME_A]) >= success


@pytest.mark.parametrize("minutes", [DEFAULT_MINUTES, CHANGED_MINUTES], ids=["default", "changed"])
def test_ac08_restart_recovers_saved_result_and_resumes_background_query(tmp_path: Path, minutes: int) -> None:
    state_file = tmp_path / "state.json"
    replies = {KEY_A: ("A", reply(cny("80.00")))}
    with serve_deepseek(accounts=replies) as source:
        with serve(source, tmp_path / "cli-first", state_file) as origin:
            with browser_session(tmp_path / "first", origin) as browser:
                add_account(browser, source, NAME_A, KEY_A, "A")
                if minutes == CHANGED_MINUTES:
                    view(browser, SETTINGS)
                    change_interval(browser, minutes)
                    with browser_session(tmp_path / "saved-settings", settings(origin), SETTINGS) as readback:
                        expect_interval(readback, minutes)
                view(browser, OVERVIEW)
                successful_card(browser, NAME_A, "80.00")
                before = by_name(browser)[NAME_A]

        # Keep a possible startup query pending so the old result must come from disk.
        pending = BalanceReply([cny("52.00")])
        replies[KEY_A] = ("A", pending)
        with serve(source, tmp_path / "cli-restarted", state_file) as restarted:
            try:
                with browser_session(tmp_path / "after-restart", restarted) as browser:
                    successful_card(browser, NAME_A, "80.00")
                    expect_persisted_snapshot(by_name(browser)[NAME_A], before)
                    if minutes == CHANGED_MINUTES:
                        view(browser, SETTINGS)
                        expect_interval(browser, minutes)
                    read = account_reader(browser, restarted)
            finally:
                pending.release.set()

            offset, first = observe_cycle(source, request_count(source), "A")
            refreshed = wait_for_accounts(read, lambda rows: rows[NAME_A]["balances"] != before["balances"],
                                          "重启后的后台周期应写入新余额")
            assert updated_at(refreshed[NAME_A]) > updated_at(before), "重启后自动刷新应推进实际成功时间"
            _, second = observe_cycle(source, offset, "A")
            expect_cadence(first, second, minutes)
            with browser_session(tmp_path / "after-restart-cycles", restarted) as reopened:
                successful_card(reopened, NAME_A, "52.00")


def test_ac05_ac08_background_skips_deleted_account_and_preserves_only_failed_account(tmp_path: Path) -> None:
    replies = {
        KEY_A: ("A", reply(cny("80.00"))),
        KEY_B: ("B", reply(cny("17.00"))),
        KEY_DELETED: ("deleted", reply(cny("33.00"))),
    }
    with serve_deepseek(accounts=replies) as source, serve(source, tmp_path / "cli", tmp_path / "state.json") as origin:
        with browser_session(tmp_path / "before-delete", origin) as browser:
            for name, key, identity in [(NAME_A, KEY_A, "A"), (NAME_B, KEY_B, "B"),
                                        (NAME_DELETED, KEY_DELETED, "deleted")]:
                add_account(browser, source, name, key, identity)
                view(browser, SETTINGS)
            type_into(browser, SEARCH, NAME_DELETED)
            assert wait_for(browser, f"""({account_entries(SETTINGS)}).map(row => row.name).join() ===
                {json.dumps(NAME_DELETED)}"""), "应先通过名称筛选定位待删除账号"
            assert browser.click_control(r"^删除$")
            assert wait_for(browser, f"""shown('dialog[open], [role=dialog]').some(dialog =>
                dialog.innerText.includes({json.dumps(NAME_DELETED)}) && dialog.innerText.includes('DeepSeek'))""")
            assert browser.click_control(r"^确认删除$")
            assert wait_for(browser, "headings.includes('没有匹配的订阅')")
            before = by_name(browser)
            assert set(before) == {NAME_A, NAME_B}, "删除后账号 API 应只返回两条保留记录"
            read = account_reader(browser, origin)
            cross_timestamp_tick(before[NAME_B])

        begin = request_count(source)
        replies[KEY_A] = ("A", BalanceReply([], status=503))
        replies[KEY_B] = ("B", reply(cny("29.00")))
        offset, _ = observe_cycle(source, begin, "A", "B")
        failed = wait_for_accounts(
            read, lambda rows: bool(rows[NAME_A].get("failed")) and rows[NAME_B]["balances"] != before[NAME_B]["balances"],
            "同一后台周期应写回 A 的失败及 B 的新余额",
        )
        assert set(failed) == {NAME_A, NAME_B}
        assert failed[NAME_A]["balances"] == before[NAME_A]["balances"]
        assert failed[NAME_A]["updated"] == before[NAME_A]["updated"], "失败账号应保留上次成功时间"
        assert UPSTREAM_503.search(failed[NAME_A].get("error", "")), "失败账号应保留上游 503 原因"
        assert not failed[NAME_B].get("failed") and updated_at(failed[NAME_B]) > updated_at(before[NAME_B])

        replies[KEY_A] = ("A", reply(cny("65.00")))
        observe_cycle(source, offset, "A", "B")
        recovered = wait_for_accounts(
            read, lambda rows: not rows[NAME_A].get("failed") and rows[NAME_A]["balances"] != before[NAME_A]["balances"],
            "后续后台周期应在 A 查询恢复后清除失败并替换原余额",
        )
        assert not recovered[NAME_A].get("error") and updated_at(recovered[NAME_A]) > updated_at(before[NAME_A])
        removed_request = Request("GET", BALANCE_PATH, True, identity="deleted")
        assert removed_request not in source.wait_for_requests(0, 0)[begin:], "已删除账号不应再提交上游查询"
        with browser_session(tmp_path / "after-cycles", origin) as browser:
            assert not card_text(browser, NAME_DELETED), "重开网页也不应显示已删除账号"
            successful_card(browser, NAME_A, "65.00")
            successful_card(browser, NAME_B, "29.00")
