"""Ticket 05 / Spec 0.2.0: AC-02, AC-05 and AC-06 for two Codex accounts."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from codex_device_source import (
    EXCHANGE_PATH, FIRST, POLL_PATH, RESET_AT, THIRD, USAGE_BASE_PATH, USAGE_PATH,
    USERCODE_PATH, Attempt, CodexDeviceSource, serve_codex_device,
)
from deepseek_source import DeepSeekSource, Request
from test_codex_windows import has_percent
from test_deepseek_balance import Browser, open_chrome, poll, run_dashboard, wait_for_new_clock_value
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for
from test_subscription_codex_device_login import (
    CONNECTED, WAITING, accounts, assert_auth_route, main_matches, refresh_now, set_field, visible_main,
)
from test_subscription_deepseek_account import ACCOUNT_NAME, CARD, SAVED_TIME, SEARCH, card_text, resize, settings, type_into, view


NAME_A = "云杉研发额度"
NAME_B = "松涛工作账号"
ATTEMPT_B = Attempt(4, "N6YT-4K8X", account_id="contract-second-account",
                    primary_used_percent=53, secondary_used_percent=20)
DEVICES = (FIRST, ATTEMPT_B, THIRD)
START = r"^(?:开始授权|获取设备码|生成设备码|开始连接)$"
CHECK = r"^(?:我已完成授权|检查授权状态)$"
NEEDS_AUTH = re.compile(r"待连接|待(?:重新)?授权|需(?:重新)?授权|(?:登录|授权|连接)[^\n]{0,12}失效")
SHORT_WINDOW = re.compile(r"短(?:额度)?窗口|短周期|短期|5\s*(?:小时|h\b)", re.IGNORECASE)
LONG_WINDOW = re.compile(r"长(?:额度)?窗口|长周期|长期|7\s*(?:天|d\b)|周(?:窗口|额度|限额)", re.IGNORECASE)


def by_name(browser: Browser) -> dict[str, dict[str, Any]]:
    rows = accounts(browser)
    records = {row["name"]: row for row in rows}
    assert len(records) == len(rows), "两个自定义账号名称必须分别保存"
    return records


def add_account(browser: Browser, name: str, expected_names: set[str]) -> str:
    assert browser.click_control(r"添加(?:第一条)?订阅$"), "应从总览的添加订阅入口新增 Codex 账号"
    assert wait_for(browser, "headings.includes('添加订阅')"), "应显示添加订阅表单"
    set_field(browser, "provider", "codex")
    type_into(browser, ACCOUNT_NAME, name)
    assert not observe(browser, """shown('main form label, main form legend, main form option')
        .some(element => /用途|分类|个人号|工作号/.test(element.innerText))"""), "添加 Codex 账号无需选择用途分类"
    assert browser.click_control(r"^保存并去授权$"), "应可保存自命名账号并去授权"
    assert wait_for(browser, "headings.includes('连接 Codex 账号')"), (
        f"应成功保存 {name!r} 并进入其授权页，实际页面 {visible_main(browser)!r}"
    )
    rows = by_name(browser)
    assert rows.keys() == expected_names, f"保存后应有账号 {expected_names!r}，实得 {set(rows)!r}"
    assert rows[name]["provider"] == "codex", "应按选择的平台保存该账号"
    account_id = rows[name]["id"]
    assert_auth_route(browser, name, account_id)
    return account_id


def start_authorization(browser: Browser, source: CodexDeviceSource, name: str, attempt: Attempt) -> None:
    before = len(source.snapshot())
    assert wait_for(browser, main_matches(r"开始授权|获取设备码")), f"{name!r} 应可发起授权"
    assert browser.click_control(START), f"{name!r} 应可点击开始授权"
    assert source.received(Request("POST", USERCODE_PATH, True, identity=attempt.user_code), after=before), (
        f"{name!r} 应向上游领取自己的设备码，实得 {source.snapshot()[before:]!r}"
    )
    assert_own_waiting_page(browser, name, attempt)
    assert source.received(Request("POST", POLL_PATH, True, identity=attempt.user_code), after=before), (
        f"{name!r} 应仅用自己的设备码轮询授权"
    )


def assert_own_waiting_page(browser: Browser, name: str, attempt: Attempt) -> None:
    assert wait_for(browser, main_matches(re.escape(attempt.user_code))), (
        f"{name!r} 的授权页应显示设备码 {attempt.user_code!r}，实得 {visible_main(browser)!r}"
    )
    text = visible_main(browser)
    assert name in text and re.search(WAITING, text), f"应显示 {name!r} 的等待授权进度，实得 {text!r}"
    assert all(other.user_code not in text for other in DEVICES if other != attempt), (
        f"{name!r} 的授权页只能显示当前设备码，实得 {text!r}"
    )


def approve(browser: Browser, source: CodexDeviceSource, name: str, attempt: Attempt) -> None:
    before = len(source.snapshot())
    source.resolve(attempt, "approved")
    browser.click_control(CHECK)  # The background poll can finish before the manual action.
    for method, path in (("POST", EXCHANGE_PATH), ("GET", USAGE_PATH)):
        assert source.received(Request(method, path, True, identity=attempt.user_code), after=before), (
            f"{name!r} 授权后应使用本次设备码交换令牌并查询额度，实得 {source.snapshot()[before:]!r}"
        )
    assert wait_for(browser, main_matches(CONNECTED)), f"{name!r} 授权页应确认已连接"
    assert by_name(browser)[name]["connection"] == "connected", f"{name!r} 应独立保存登录状态"


def assert_card(browser: Browser, name: str, expected: tuple[int, int], status: re.Pattern[str]) -> None:
    def has_windows(text: str) -> bool:
        short, long = SHORT_WINDOW.search(text), LONG_WINDOW.search(text)
        if not short or not long:
            return False
        a, b = short.start(), long.start()
        short_text, long_text = (text[a:b], text[b:]) if a < b else (text[a:], text[b:a])
        reset = datetime.fromtimestamp(RESET_AT).strftime("%H:%M")
        return (has_percent(short_text, expected[0]) and reset in short_text
                and has_percent(long_text, expected[1]) and "未提供" in long_text)

    card = poll(lambda: card_text(browser, name), has_windows)
    assert has_windows(card), f"{name!r} 应展示短、长窗口 {expected!r} 和各自重置时间，实得 {card!r}"
    assert status.search(card), f"{name!r} 卡片应展示对应连接状态，实得 {card!r}"
    assert SAVED_TIME.search(card), f"{name!r} 卡片应展示成功更新时间，实得 {card!r}"


def account_action(browser: Browser, selected: str, other: str, label: str) -> None:
    type_into(browser, SEARCH, selected)
    assert wait_for(browser, f"({main_matches(re.escape(selected))}) && !({main_matches(re.escape(other))})"), (
        f"配置管理应能按名称定位 {selected!r}，实得 {visible_main(browser)!r}"
    )
    assert browser.click_control(label), f"应能在 {selected!r} 的配置行执行 {label!r}"


def refresh(browser: Browser, source: CodexDeviceSource, *expected: Request) -> None:
    before = len(source.snapshot())
    refresh_now(browser)
    actual = [item for item in source.snapshot()[before:] if item.path == USAGE_PATH]
    assert set(actual) == set(expected), f"手动刷新应按已连接账号的各自令牌查询，预期 {expected!r}，实得 {actual!r}"


def usage(attempt: Attempt, valid: bool = True) -> Request:
    return Request("GET", USAGE_PATH, valid, identity=attempt.user_code)


def updated_at(row: dict[str, Any]) -> datetime:
    assert row.get("updated"), f"应保存账号 {row.get('name')!r} 的成功时间"
    return datetime.fromisoformat(row["updated"])


def cross_timestamp_tick(row: dict[str, Any]) -> None:
    wait_for_new_clock_value(updated_at(row).strftime("%H:%M:%S"))


def test_ac02_ac05_ac06_add_reauthorize_and_delete_one_of_two_codex_accounts(tmp_path: Path) -> None:
    unavailable = "http://127.0.0.1:1"
    state_file = tmp_path / "state.json"
    with (
        serve_codex_device(DEVICES) as source,
        run_dashboard(
            DeepSeekSource(unavailable), tmp_path,
            QUOTA_STATE_FILE=str(state_file), QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            QUOTA_CODEX_AUTH_BASE_URL=source.origin, QUOTA_CODEX_BASE_URL=source.origin + USAGE_BASE_PATH,
            QUOTA_CODEX_ACCESS_TOKEN="", DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_MIMO_BASE_URL=unavailable,
            CODEX_HOME=str(tmp_path / "codex-home"),
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        connected = re.compile(CONNECTED)

        id_a = add_account(browser, NAME_A, {NAME_A})
        start_authorization(browser, source, NAME_A, FIRST)
        approve(browser, source, NAME_A, FIRST)
        view(browser, OVERVIEW)
        assert_card(browser, NAME_A, (75, 30), connected)
        assert observe(browser, f"shown({json.dumps(CARD)}).length === 1"), "只有 A 时总览只显示一张卡"
        refresh(browser, source, usage(FIRST))
        first_a = by_name(browser)[NAME_A]
        cross_timestamp_tick(first_a)

        id_b = add_account(browser, NAME_B, {NAME_A, NAME_B})
        assert id_a != id_b, "第二个 Codex 账号应有独立标识"
        start_authorization(browser, source, NAME_B, ATTEMPT_B)
        assert by_name(browser)[NAME_A] == first_a, "B 等待授权时 A 的连接、额度和成功时间应保持原样"
        approve(browser, source, NAME_B, ATTEMPT_B)
        view(browser, OVERVIEW)
        assert observe(browser, f"shown({json.dumps(CARD)}).length === 2"), "添加 B 后总览应显示两张卡"
        assert_card(browser, NAME_A, (75, 30), connected)
        assert_card(browser, NAME_B, (47, 80), connected)
        both = by_name(browser)
        assert both[NAME_A] == first_a, "B 首次授权和额度查询不能覆盖 A 已保存的结果"
        assert updated_at(both[NAME_B]) > updated_at(first_a), "B 应保存自己的首次查询成功时间"

        cross_timestamp_tick(both[NAME_B])
        source.revoke(FIRST)
        refresh(browser, source, usage(FIRST, False), usage(ATTEMPT_B))
        assert_card(browser, NAME_A, (75, 30), NEEDS_AUTH)
        assert_card(browser, NAME_B, (47, 80), connected)
        expired = by_name(browser)
        assert expired[NAME_A]["connection"] == "expired", "上游只对 A 的令牌返回 401，A 应待重新授权"
        assert expired[NAME_A]["updated"] == first_a["updated"], "A 失败时应保留实际的上次成功时间"
        assert expired[NAME_B]["connection"] == "connected", "A 的 401 不能使 B 断开连接"
        assert updated_at(expired[NAME_B]) > updated_at(both[NAME_B]), "A 失败的同一轮，B 的刷新应成功"

        view(browser, SETTINGS)
        account_action(browser, NAME_A, NAME_B, r"^(?:重新授权|去授权)$")
        assert_auth_route(browser, NAME_A, id_a)
        start_authorization(browser, source, NAME_A, THIRD)

        view(browser, SETTINGS)
        account_action(browser, NAME_B, NAME_A, r"^(?:重新授权|去授权)$")
        assert_auth_route(browser, NAME_B, id_b)
        assert wait_for(browser, main_matches(CONNECTED)), "查看 B 的授权页应显示原连接仍有效"
        b_page = visible_main(browser)
        assert all(attempt.user_code not in b_page for attempt in DEVICES), "B 的授权页不能显示 A 的授权进度或设备码"
        view(browser, OVERVIEW)
        refresh(browser, source, usage(ATTEMPT_B))
        assert_card(browser, NAME_B, (47, 80), connected)
        before_approval = by_name(browser)[NAME_B]

        view(browser, SETTINGS)
        account_action(browser, NAME_A, NAME_B, r"^(?:重新授权|去授权)$")
        assert_auth_route(browser, NAME_A, id_a)
        assert_own_waiting_page(browser, NAME_A, THIRD)
        approve(browser, source, NAME_A, THIRD)
        view(browser, OVERVIEW)
        assert_card(browser, NAME_A, (60, 30), connected)
        assert_card(browser, NAME_B, (47, 80), connected)
        restored = by_name(browser)
        assert updated_at(restored[NAME_A]) > updated_at(first_a), "A 恢复后应保存新的成功时间"
        assert restored[NAME_B] == before_approval, "A 重新授权不能覆盖 B 的连接、额度或成功时间"
        refresh(browser, source, usage(THIRD), usage(ATTEMPT_B))

        view(browser, SETTINGS)
        before_delete = by_name(browser)
        dialog = f"""shown('dialog[open], [role=dialog]').some(dialog =>
            dialog.innerText.includes('Codex') && dialog.innerText.includes({json.dumps(NAME_A)}) &&
            !dialog.innerText.includes({json.dumps(NAME_B)}))"""
        account_action(browser, NAME_A, NAME_B, r"^删除$")
        assert wait_for(browser, dialog), "删除确认框应明确显示所选 A 的平台及自定义名称"
        assert browser.click_control(r"^取消$"), "应可取消删除"
        assert wait_for(browser, "!shown('dialog[open], [role=dialog]').length"), "取消应关闭确认框"
        open_entry(browser, settings(origin))
        assert by_name(browser) == before_delete, "取消删除并重开后应完整保留两个账号"
        persisted = json.loads(state_file.read_text(encoding="utf-8"))
        assert {row["id"] for row in persisted["accounts"]} == {id_a, id_b}
        account_action(browser, NAME_A, NAME_B, r"^删除$")
        assert wait_for(browser, dialog) and browser.click_control(r"^确认删除$"), "应可确认删除 A"
        assert wait_for(browser, "headings.includes('没有匹配的订阅')"), "删除后 A 应从配置列表消失"
        open_entry(browser, settings(origin))
        assert by_name(browser) == {NAME_B: before_delete[NAME_B]}, "重开配置仅应保留 B 及其原连接和缓存"

        # Inspect the isolated backing file only for persisted credential/cache cleanup.
        after_delete = json.loads(state_file.read_text(encoding="utf-8"))
        assert after_delete["accounts"] == [row for row in persisted["accounts"] if row["id"] == id_b], (
            "落盘应只保留 B 的完整账号记录，包括原凭据和缓存"
        )
        serialized = json.dumps(after_delete, ensure_ascii=False)
        assert all(marker not in serialized for marker in (id_a, NAME_A, FIRST.token, THIRD.token)), (
            "落盘状态应清除 A 的标识、配置和授权令牌"
        )
        open_entry(browser, f"{origin}?view=auth&id={id_a}")
        assert wait_for(browser, main_matches(r"找不到这条订阅|账号不存在|已删除")), "A 的旧授权地址应失效"
        view(browser, OVERVIEW)
        refresh(browser, source, usage(ATTEMPT_B))
        assert not card_text(browser, NAME_A), "确认删除后，刷新不得重新显示 A 的额度缓存"
        assert_card(browser, NAME_B, (47, 80), connected)
