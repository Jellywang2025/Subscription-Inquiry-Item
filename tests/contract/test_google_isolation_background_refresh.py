"""03 票，Spec 0.3.0：AC-12，并复用 AC-05 / AC-08。"""

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from deepseek_source import DeepSeekSource, Request
from google_source import (
    ACCESS_TOKENS, EMAIL, REFRESH_TOKENS, RESET_SHORT, RESET_WEEK, SECOND_EMAIL,
    TOKEN_PATH, USERINFO_PATH, GoogleSource, serve_google,
)
from test_deepseek_balance import Browser, poll, run_dashboard
from test_google_account_overview import AUTH_LINK, CODE_FIELD, CONNECTED, GROUPS, NAME
from test_google_refresh_bookmarks import CREDITS_ITEM, USAGE_ITEM, expect_credits, expect_usage, google_items
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, wait_for
from test_subscription_background_refresh import (
    CHANGED_MINUTES, DEADLINE, DEFAULT_MINUTES, SECONDS_PER_TEST_MINUTE, account_reader,
    browser_session, change_interval, expect_cadence, expect_interval, wait_for_accounts,
)
from test_subscription_codex_device_login import main_matches, refresh_now, set_field, visible_main
from test_subscription_codex_second_account import account_action, by_name
from test_subscription_deepseek_account import ACCOUNT_NAME, CARD, card_text, settings, type_into, view


NAME_B = "竹泉 Google 订阅"
UNAVAILABLE = "http://127.0.0.1:1"
USAGE_ARGS = ["-p", "/usage", "--output-format", "json"]
CREDITS_ARGS = ["-p", "/credits", "--output-format", "json"]
INITIAL = {NAME: (63, 1347), NAME_B: (81, 2694)}
NEXT = {NAME: (46, 1783), NAME_B: (52, 2735)}
FOREIGN = (96, 8193)
ITEM_FIELDS = (("antigravity", "groups"), ("credits", "value"))


@contextmanager
def dashboard(
    source: GoogleSource, directory: Path, state: Path, *, accelerate: bool = False,
) -> Iterator[str]:
    directory.mkdir()
    with run_dashboard(
        DeepSeekSource(UNAVAILABLE), directory,
        QUOTA_STATE_FILE=str(state), QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
        QUOTA_REFRESH_TEST_SECONDS_PER_MINUTE=str(SECONDS_PER_TEST_MINUTE if accelerate else 60),
        DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_CODEX_ACCESS_TOKEN="",
        QUOTA_MIMO_BASE_URL=UNAVAILABLE, QUOTA_CODEX_BASE_URL=UNAVAILABLE,
        QUOTA_CODEX_AUTH_BASE_URL=UNAVAILABLE, CODEX_HOME=str(directory / "codex-home"),
        **source.environment(),
    ) as origin:
        yield origin


def set_values(source: GoogleSource, identity: str, values: tuple[int, int]) -> None:
    percentage, credits = values
    source.set_cli_replies(
        identity=identity,
        usage={"groups": [{"name": GROUPS[0], "buckets": [
            {"window": "5h", "remaining_fraction": percentage / 100, "reset_time": RESET_SHORT},
            {"window": "weekly", "remaining_fraction": 0.27, "reset_time": RESET_WEEK},
        ]}]},
        credits={"remaining_credits": credits},
    )


def finish_authorization(browser: Browser, source: GoogleSource, name: str, identity: str) -> None:
    assert wait_for(browser, main_matches(re.escape(name))), f"授权页面应对应 {name!r}"
    link = f"Boolean({AUTH_LINK})"
    start = r"^(?:(?:开始|发起|去)(?: Google)?授权|重新(?:开始|发起)?授权)$"
    assert wait_for(browser, f"""{link} || shown('main button, main [role=button]').some(control =>
        new RegExp({json.dumps(start)}).test(control.innerText.trim()))"""), (
        f"{name!r} 的授权页应有授权链接或发起授权入口，页面 {visible_main(browser)!r}"
    )
    if not observe(browser, link):
        assert browser.click_control(start), (
            f"{name!r} 应可从本条记录发起或重新发起授权，页面 {visible_main(browser)!r}"
        )
    assert wait_for(browser, link), f"应展示 {name!r} 的官方授权链接"
    begin = len(source.requests())
    callback = source.approve(observe(browser, f"({AUTH_LINK}).href"), identity)
    assert wait_for(browser, f"Boolean({CODE_FIELD})"), "授权页面应提供授权码或回调输入框"
    use_callback = observe(browser, f"""(() => {{
        const field = {CODE_FIELD};
        field.focus();
        return /回调|callback/i.test([
            ...[...(field.labels ?? [])].map(label => label.innerText), field.placeholder,
            field.getAttribute('aria-label'),
        ].join(' '));
    }})()""")
    browser.call("Input.insertText", text=callback if use_callback else parse_qs(urlsplit(callback).query)["code"][0])
    assert browser.click_control(r"^(?:提交(?:授权码|回调地址)?|完成(?:授权|连接)|确认(?:授权|连接)|验证并连接)$")
    assert wait_for(browser, main_matches(CONNECTED)), (
        f"{name!r} 应完成授权，实得 {visible_main(browser)!r}"
    )
    requests = source.requests()[begin:]
    token = Request("POST", TOKEN_PATH, True, identity=identity)
    userinfo = Request("GET", USERINFO_PATH, True, identity=identity)
    assert token in requests and userinfo in requests and requests.index(token) < requests.index(userinfo), (
        f"{name!r} 的授权应使用对应身份完成令牌交换和邮箱核对，实得 {requests!r}"
    )


def expect_card(browser: Browser, name: str, values: tuple[int, int]) -> None:
    percentage, credits = values
    assert poll(lambda: card_text(browser, name), bool), f"总览应加载 {name!r} 的卡片"
    items = google_items(browser, name)
    expect_usage(browser, items[USAGE_ITEM], percentage, RESET_SHORT)
    expect_credits(items[CREDITS_ITEM], credits)


def add_accounts(browser: Browser, source: GoogleSource) -> None:
    for name, identity in ((NAME, EMAIL), (NAME_B, SECOND_EMAIL)):
        set_values(source, identity, INITIAL[name])
        view(browser, SETTINGS)
        assert browser.click_control(r"添加(?:第一条)?订阅$"), "配置管理应提供添加订阅入口"
        assert wait_for(browser, "headings.includes('添加订阅')")
        set_field(browser, "provider", "google")
        type_into(browser, ACCOUNT_NAME, name)
        assert browser.click_control(r"^(?:保存并(?:去|开始)?授权|连接 Google(?: AI)?|保存)$")
        finish_authorization(browser, source, name, identity)
    view(browser, OVERVIEW)
    rows = by_name(browser)
    assert {name: row["google_email"] for name, row in rows.items()} == {NAME: EMAIL, NAME_B: SECOND_EMAIL}
    assert rows[NAME]["id"] != rows[NAME_B]["id"]
    for name, values in INITIAL.items():
        expect_card(browser, name, values)
        item_snapshot(rows[name])


def item_snapshot(row: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    snapshot = {
        key: (row["google_items"][key][field], row["google_items"][key]["updated"])
        for key, field in ITEM_FIELDS
    }
    assert all(updated for _, updated in snapshot.values()), f"{row['name']!r} 的两个已取数项应有成功时间"
    return snapshot


def expect_calls(calls: list[tuple[str, list[str]]], *identities: str) -> None:
    expected = sorted((identity, args) for identity in identities for args in (USAGE_ARGS, CREDITS_ARGS))
    assert sorted(calls) == expected, f"刷新应分别使用各记录的连接，预期 {expected!r}，实得 {calls!r}"


def expect_reason(browser: Browser, name: str, reason: str) -> None:
    assert card_text(browser, name), f"总览应显示 {name!r}"
    if not re.search(reason, card_text(browser, name)):
        observe(browser, f"""(() => {{
            const card = shown({json.dumps(CARD)}).find(element =>
                shown('h2,h3,h4,[role=heading]', element).some(title =>
                    title.innerText.trim() === {json.dumps(name)}));
            const details = card && shown('summary', card)[0];
            if (details && !details.parentElement.open) details.click();
        }})()""")
    text = card_text(browser, name)
    assert re.search(reason, text), f"AC-12 {name!r} 应显示身份或授权故障原因 {reason!r}，实得 {text!r}"


@pytest.mark.parametrize(
    ("fault", "reason"),
    [
        ("missing", r"无法.{0,12}(?:核对|校验|确认|验证)|(?:邮箱|身份).{0,12}(?:缺失|未返回|无法核对)"),
        ("mismatch", r"错配|串号|不匹配|不一致|不符|另一.{0,6}(?:账号|身份)"),
        ("revoked", r"授权.{0,8}(?:失效|过期)|重新授权|401"),
    ],
    ids=["identity-missing", "other-account-reply", "reauthorize-expired"],
)
def test_ac12_identity_failure_preserves_previous_results_and_other_connection(
    tmp_path: Path, fault: str, reason: str,
) -> None:
    with (
        serve_google(tmp_path) as source,
        dashboard(source, tmp_path / "server", tmp_path / "state.json") as origin,
        browser_session(tmp_path / "browser", origin) as browser,
    ):
        add_accounts(browser, source)
        before = by_name(browser)
        if fault == "revoked":
            source.revoke(EMAIL)
        else:
            source.set_userinfo_email(EMAIL, SECOND_EMAIL if fault == "mismatch" else None)
        # 故障期间若继续用旧绑定取数，会收到可辨认的新值。
        set_values(source, EMAIL, FOREIGN)
        set_values(source, SECOND_EMAIL, NEXT[NAME_B])
        offset = len(source.cli_calls())
        refresh_now(browser)
        expect_reason(browser, NAME, reason)
        after = by_name(browser)
        assert after[NAME]["google_email"] == EMAIL, "身份异常后应保留 A 原绑定"
        assert item_snapshot(after[NAME]) == item_snapshot(before[NAME]), (
            "身份异常时，A 的额度、积分和逐项原成功时间应原样保留"
        )
        expect_card(browser, NAME, INITIAL[NAME])
        assert after[NAME_B]["google_email"] == SECOND_EMAIL
        expect_card(browser, NAME_B, NEXT[NAME_B])
        expect_calls([call for call in source.cli_calls()[offset:] if call[0] == SECOND_EMAIL], SECOND_EMAIL)

        if fault == "revoked":
            view(browser, SETTINGS)
            account_action(browser, NAME, NAME_B, r"^(?:去授权|重新授权)$")
            set_values(source, EMAIL, NEXT[NAME])
            finish_authorization(browser, source, NAME, EMAIL)
            assert by_name(browser)[NAME_B] == after[NAME_B], "A 重新授权时应保留 B 的连接和结果"
            view(browser, OVERVIEW)
            expect_card(browser, NAME, NEXT[NAME])
            expect_card(browser, NAME_B, NEXT[NAME_B])
            set_values(source, SECOND_EMAIL, (71, 3021))
            offset = len(source.cli_calls())
            refresh_now(browser)
            expect_calls(source.cli_calls()[offset:], EMAIL, SECOND_EMAIL)
            expect_card(browser, NAME, NEXT[NAME])
            expect_card(browser, NAME_B, (71, 3021))


def test_ac05_ac12_cancel_then_delete_one_google_account_and_refresh_other(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    with (
        serve_google(tmp_path) as source,
        dashboard(source, tmp_path / "server", state) as origin,
        browser_session(tmp_path / "browser", origin) as browser,
    ):
        add_accounts(browser, source)
        before = by_name(browser)
        id_a, id_b = before[NAME]["id"], before[NAME_B]["id"]
        dialog = f"""shown('dialog[open], [role=dialog]').some(dialog =>
            dialog.innerText.includes('Google') && dialog.innerText.includes({json.dumps(NAME)}) &&
            !dialog.innerText.includes({json.dumps(NAME_B)}))"""
        view(browser, SETTINGS)
        account_action(browser, NAME, NAME_B, r"^删除$")
        assert wait_for(browser, dialog), "确认框应显示所选 Google 账号名称"
        assert browser.click_control(r"^取消$")
        assert wait_for(browser, "!shown('dialog[open], [role=dialog]').length")
        open_entry(browser, settings(origin))
        assert by_name(browser) == before, "取消后重开配置，应保留两个账号及原连接和结果"

        view(browser, OVERVIEW)
        for name, identity in ((NAME, EMAIL), (NAME_B, SECOND_EMAIL)):
            set_values(source, identity, NEXT[name])
        offset = len(source.cli_calls())
        refresh_now(browser)
        expect_calls(source.cli_calls()[offset:], EMAIL, SECOND_EMAIL)
        for name, values in NEXT.items():
            expect_card(browser, name, values)
        retained = by_name(browser)[NAME_B]

        # 凭据清理用隔离的测试文件佐证，操作和账号结果仍从网页验证。
        homes = state.parent / "google-homes"
        persisted = json.loads(state.read_text(encoding="utf-8"))
        assert (homes / id_a).is_dir() and (homes / id_b).is_dir(), "两条已查询记录应各有 CLI 凭据目录"
        serialized = json.dumps(persisted)
        assert all(marker in serialized for marker in (id_a, ACCESS_TOKENS[EMAIL], REFRESH_TOKENS[EMAIL]))
        view(browser, SETTINGS)
        account_action(browser, NAME, NAME_B, r"^删除$")
        assert wait_for(browser, dialog) and browser.click_control(r"^确认删除$")
        assert wait_for(browser, "headings.includes('没有匹配的订阅')")
        open_entry(browser, settings(origin))
        assert by_name(browser) == {NAME_B: retained}, "确认删除后，配置应只剩 B 及其原连接和结果"
        after = json.loads(state.read_text(encoding="utf-8"))
        assert after["accounts"] == [row for row in persisted["accounts"] if row["id"] == id_b], (
            "落盘账号应仅保留 B 的配置、凭据和缓存"
        )
        serialized = json.dumps(after, ensure_ascii=False)
        assert all(marker not in serialized for marker in (id_a, NAME, EMAIL, ACCESS_TOKENS[EMAIL], REFRESH_TOKENS[EMAIL]))
        assert not (homes / id_a).exists() and (homes / id_b).is_dir(), "应只清除 A 的 CLI 凭据"

        open_entry(browser, f"{origin}?view=auth&id={id_a}")
        assert wait_for(browser, main_matches(r"找不到这条订阅|账号不存在|已删除")), "A 的旧授权地址应失效"
        view(browser, OVERVIEW)
        set_values(source, SECOND_EMAIL, (71, 3021))
        offset = len(source.cli_calls())
        refresh_now(browser)
        expect_calls(source.cli_calls()[offset:], SECOND_EMAIL)
        assert not card_text(browser, NAME), "刷新后 A 的卡片和缓存应继续消失"
        expect_card(browser, NAME_B, (71, 3021))


def observe_google_cycle(source: GoogleSource, since: float) -> dict[str, float]:
    deadline = time.monotonic() + DEADLINE
    while True:
        calls = [(at, identity) for at, identity, args in source.cli_events() if at >= since and args == CREDITS_ARGS]
        if {identity for _, identity in calls} >= {EMAIL, SECOND_EMAIL}:
            return {identity: next(at for at, email in calls if email == identity) for identity in (EMAIL, SECOND_EMAIL)}
        if time.monotonic() >= deadline:
            pytest.fail(f"关闭网页后 {DEADLINE} 秒内应观察到两个 Google 连接的积分查询，实得 {calls!r}")
        time.sleep(0.05)


def expect_background_cadence(source: GoogleSource, first: dict[str, float], minutes: int) -> None:
    second = observe_google_cycle(source, max(first.values()) + 0.001)
    for identity in (EMAIL, SECOND_EMAIL):
        expect_cadence(first[identity], second[identity], minutes)


def credits_match(rows: dict[str, dict[str, Any]], values: dict[str, tuple[int, int]]) -> bool:
    return all(rows[name]["google_items"]["credits"].get("value") == credits for name, (_, credits) in values.items())


def test_ac08_ac12_default_google_refresh_runs_with_browser_closed(tmp_path: Path) -> None:
    with (
        serve_google(tmp_path) as source,
        dashboard(source, tmp_path / "server", tmp_path / "state.json", accelerate=True) as origin,
    ):
        with browser_session(tmp_path / "before-close", origin) as browser:
            add_accounts(browser, source)
            view(browser, SETTINGS)
            expect_interval(browser, DEFAULT_MINUTES)
            before = by_name(browser)
            read = account_reader(browser, origin)

        closed_at = datetime.now().astimezone().replace(microsecond=0)
        for name, identity in ((NAME, EMAIL), (NAME_B, SECOND_EMAIL)):
            set_values(source, identity, NEXT[name])
        first = observe_google_cycle(source, time.monotonic())
        latest = wait_for_accounts(read, lambda rows: credits_match(rows, NEXT), "在重新开页前应存入两个账号的新积分")
        for name in NEXT:
            for item, _ in ITEM_FIELDS:
                old = datetime.fromisoformat(before[name]["google_items"][item]["updated"])
                success = datetime.fromisoformat(latest[name]["google_items"][item]["updated"])
                assert old < success and closed_at <= success <= datetime.now().astimezone(), (
                    f"{name!r} {item} 应记录发生在关页期间的成功时间"
                )
        expect_background_cadence(source, first, DEFAULT_MINUTES)

        with browser_session(tmp_path / "reopened", origin) as browser:
            for name, values in NEXT.items():
                expect_card(browser, name, values)


def test_ac08_ac12_changed_google_interval_survives_restart_and_controls_queries(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    with serve_google(tmp_path) as source:
        with dashboard(source, tmp_path / "server", state, accelerate=True) as origin:
            with browser_session(tmp_path / "settings", origin) as browser:
                add_accounts(browser, source)
                view(browser, SETTINGS)
                expect_interval(browser, DEFAULT_MINUTES)
                change_interval(browser, CHANGED_MINUTES)
                with browser_session(tmp_path / "saved-settings", settings(origin), SETTINGS) as saved:
                    expect_interval(saved, CHANGED_MINUTES)
            first = observe_google_cycle(source, time.monotonic())
            expect_background_cadence(source, first, CHANGED_MINUTES)

            # 停止产生新成功时间后再记录快照，避免关服务前正好开始下一轮。
            for identity in (EMAIL, SECOND_EMAIL):
                source.set_cli_replies(identity=identity, usage="upstream timed out", credits="upstream timed out")
            with browser_session(tmp_path / "before-restart", origin) as browser:
                read = account_reader(browser, origin)
                observe_google_cycle(source, time.monotonic())
                before = wait_for_accounts(
                    read, lambda rows: all(row["google_items"]["credits"].get("failed") for row in rows.values()),
                    "停止服务前应读到两个账号保留的最近成功结果",
                )

        # 保持上游失败，重启时能读到的成功结果应来自已保存状态。
        with dashboard(source, tmp_path / "restarted", state, accelerate=True) as restarted:
            with browser_session(tmp_path / "after-restart", restarted) as browser:
                rows = by_name(browser)
                assert rows.keys() == before.keys()
                for name, values in INITIAL.items():
                    for key in ("id", "name", "provider", "google_email", "connection"):
                        assert rows[name][key] == before[name][key], f"重启应回读 {name!r} 的 {key}"
                    assert item_snapshot(rows[name]) == item_snapshot(before[name]), (
                        f"重启应回读 {name!r} 原额度、积分及其各自成功时间"
                    )
                    expect_card(browser, name, values)
                view(browser, SETTINGS)
                expect_interval(browser, CHANGED_MINUTES)
                read = account_reader(browser, restarted)

            for name, identity in ((NAME, EMAIL), (NAME_B, SECOND_EMAIL)):
                set_values(source, identity, NEXT[name])
            first = observe_google_cycle(source, time.monotonic())
            latest = wait_for_accounts(read, lambda rows: credits_match(rows, NEXT), "重启后的后台刷新应保存两账号的新回执")
            for name in NEXT:
                assert all(
                    datetime.fromisoformat(latest[name]["google_items"][item]["updated"])
                    > datetime.fromisoformat(before[name]["google_items"][item]["updated"])
                    for item, _ in ITEM_FIELDS
                ), f"{name!r} 重启后两个取数项的成功时间均应更新"
            expect_background_cadence(source, first, CHANGED_MINUTES)
            with browser_session(tmp_path / "after-restarted-cycles", restarted) as browser:
                for name, values in NEXT.items():
                    expect_card(browser, name, values)
