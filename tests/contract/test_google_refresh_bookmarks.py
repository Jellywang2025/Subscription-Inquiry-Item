"""02 票，Spec 0.3.0：AC-11，复用 AC-01、AC-03、AC-04、AC-05 和 AC-08。"""

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from google_source import EMAIL, RESET_SHORT, RESET_WEEK, TOKEN_PATH, GoogleSource
from test_deepseek_balance import Browser, open_chrome, success_time, wait_for_new_clock_value
from test_google_account_overview import (
    CONNECTED, GROUPS, ITEMS, NAME, add_google, connect_google, dashboard, expect_window, official_links, quota_windows,
)
from test_subscription_admin_entry import OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for
from test_subscription_codex_device_login import main_matches, refresh_now, set_field
from test_subscription_deepseek_account import ACCOUNT_NAME, CARD, SEARCH, edit_account, submit, type_into, view
from test_subscription_search_bookmarks import FIRST_USE, NO_MATCH, PAGES, ROUTES, account_entries, expect_view, select_platform


type Session = tuple[Browser, str, GoogleSource]
MANUAL, USAGE_ITEM, CREDITS_ITEM = ITEMS
GOOGLE = "Google AI（Google One）"
NEIGHBOR = f"{NAME} Codex 备用"
RENAMED = "云岭家庭订阅"
UPSTREAM_TIMEOUT = "upstream timed out"
TIMEOUT_REASON = re.compile(r"upstream timed out|超时", re.IGNORECASE)
FAILED = re.compile(r"刷新失败|查询失败|取数失败|超时|upstream timed out", re.IGNORECASE)
RESET_AFTER = "2031-02-18T06:16:00Z"


def google_items(browser: Browser, name: str = NAME) -> dict[str, str]:
    # 分别读每项的可见内容，避免把卡片页脚的账号级状态或时间算到积分项。
    content = observe(browser, f"""(() => {{
        const names = {json.dumps(ITEMS)};
        const card = shown({json.dumps(CARD)}).find(element =>
            shown('h2,h3,h4,[role=heading]', element).some(title => title.innerText.trim() === {json.dumps(name)}));
        if (!card) return {{}};
        const nodes = shown('*', card);
        const labels = names.map(name => nodes.find(node => node.innerText?.trim() === name));
        return Object.fromEntries(names.map((name, index) => {{
            let section = labels[index];
            if (!section) return [name, ''];
            while (section.parentElement !== card && !labels.some((other, i) =>
                i !== index && other && section.parentElement.contains(other))) {{
                section = section.parentElement;
            }}
            return [name, section.innerText];
        }}));
    }})()""")
    assert all(content.get(name) for name in ITEMS), f"Google 卡片应分别展示三项，实得 {content!r}"
    return content


def item_time(section: str) -> str | None:
    return success_time(section.replace("已更新", "更新时间"))


def expect_credits(section: str, value: int) -> None:
    number = r"[,，\s]?".join(re.escape(group) for group in f"{value:,}".split(","))
    assert re.search(rf"(?<![\d,.]){number}\s*积分|积分\s*[:：]?\s*{number}(?![\d,.])", section), (
        f"Google One 应按积分展示 {value}，实得 {section!r}"
    )


def expect_usage(browser: Browser, section: str, percent: int, reset: str) -> None:
    primary = section.split(GROUPS[0], 1)[-1].split(GROUPS[1], 1)[0]
    windows = quota_windows(primary, "short", "weekly")
    expect_window(browser, windows["short"], percent, reset)
    expect_window(browser, windows["weekly"], 27, RESET_WEEK)


def refresh_google(browser: Browser, source: GoogleSource) -> None:
    before = len(source.cli_calls())
    refresh_now(browser)
    calls = source.cli_calls()[before:]
    assert all((EMAIL, ["-p", command, "--output-format", "json"]) in calls for command in ("/usage", "/credits")), (
        f"立即刷新应使用原授权身份分别查询额度和积分，实际调用 {calls!r}"
    )


def test_ac04_ac11_first_empty_manual_and_confirmed_zero(dashboard: Session) -> None:
    browser, _, source = dashboard
    assert wait_for(browser, FIRST_USE), "订阅为空应显示首次使用引导"
    assert wait_for(browser, """shown('main button, main a[href]').some(
        control => control.innerText.trim() === '添加第一条订阅')"""), "应提供添加第一条订阅的入口"
    assert not observe(browser, NO_MATCH), "尚无订阅时应显示首次使用空态"

    source.set_cli_replies(usage={}, credits={})
    add_google(browser)
    connect_google(browser, source)
    view(browser, OVERVIEW)
    refresh_google(browser, source)
    empty = google_items(browser)
    assert re.search(r"暂无|未取得|无可用|未提供", empty[USAGE_ITEM])
    assert re.search(r"(?:没有|未返回|缺少|未提供).*模型组|官方回执.*(?:空|无数据)", empty[USAGE_ITEM]), empty[USAGE_ITEM]
    assert re.search(r"暂无|未取得|无可用|未提供", empty[CREDITS_ITEM])
    assert re.search(r"(?:没有|未返回|缺少|未提供).*积分|官方回执.*(?:空|无数据)", empty[CREDITS_ITEM]), empty[CREDITS_ITEM]
    assert all(item_time(text) is None for text in empty.values()), f"首次无值及人工查询均无成功时间 {empty!r}"
    assert "请到 Gemini 查看" in empty[MANUAL] and re.search(r"设置\s*→\s*使用限额", empty[MANUAL]), empty[MANUAL]
    assert ("gemini.google.com", "") in official_links(browser), "人工查询项应提供 Gemini 官方入口"
    assert not re.search(r"(?<!\d)0\s*积分", empty[CREDITS_ITEM]), empty[CREDITS_ITEM]

    source.set_cli_replies(usage={}, credits={"remaining_credits": 0})
    refresh_google(browser, source)
    zero = google_items(browser)
    expect_credits(zero[CREDITS_ITEM], 0)
    assert item_time(zero[CREDITS_ITEM]), f"官方零积分是成功结果，实得 {zero[CREDITS_ITEM]!r}"
    assert not re.search(r"暂无|没有积分|等待首次|查询失败", zero[CREDITS_ITEM]), zero[CREDITS_ITEM]
    assert item_time(zero[USAGE_ITEM]) is None and item_time(zero[MANUAL]) is None, zero


@pytest.mark.parametrize("failed_item", [USAGE_ITEM, CREDITS_ITEM], ids=["usage-fails", "credits-fails"])
def test_ac08_ac11_partial_refresh_preserves_and_recovers_only_failed_item(dashboard: Session, failed_item: str) -> None:
    browser, _, source = dashboard
    add_google(browser)
    connect_google(browser, source)
    view(browser, OVERVIEW)
    refresh_google(browser, source)
    initial = google_items(browser)
    expect_usage(browser, initial[USAGE_ITEM], 63, RESET_SHORT)
    expect_credits(initial[CREDITS_ITEM], 1347)
    successful_item = CREDITS_ITEM if failed_item == USAGE_ITEM else USAGE_ITEM
    failed_time = item_time(initial[failed_item])
    successful_time = item_time(initial[successful_item])
    assert failed_time and successful_time, f"两个自动查询项均应有首次成功时间 {initial!r}"

    wait_for_new_clock_value(successful_time)
    if failed_item == USAGE_ITEM:
        source.set_cli_replies(usage=UPSTREAM_TIMEOUT, credits={"remaining_credits": 1640})
    else:
        source.set_cli_replies(credits=UPSTREAM_TIMEOUT)
    refresh_google(browser, source)
    partial = google_items(browser)
    expect_usage(browser, partial[USAGE_ITEM], 63, RESET_SHORT)
    expect_credits(partial[CREDITS_ITEM], 1640 if failed_item == USAGE_ITEM else 1347)
    assert item_time(partial[failed_item]) == failed_time, f"失败项应保留原成功时间 {partial!r}"
    partial_success = item_time(partial[successful_item])
    assert partial_success and partial_success != successful_time, f"成功项应更新成功时间，包括数值未变时 {partial!r}"
    assert not FAILED.search(partial[successful_item]), f"同轮成功项不应带失败提示 {partial[successful_item]!r}"

    source.set_cli_replies(
        usage={"groups": [{"name": GROUPS[0], "buckets": [
            {"window": "5h", "remaining_fraction": 0.46, "reset_time": RESET_AFTER},
            {"window": "weekly", "remaining_fraction": 0.27, "reset_time": RESET_WEEK},
        ]}]},
        credits={"remaining_credits": 2086},
    )
    refresh_google(browser, source)
    recovered = google_items(browser)
    expect_usage(browser, recovered[USAGE_ITEM], 46, RESET_AFTER)
    expect_credits(recovered[CREDITS_ITEM], 2086)
    restored_time = item_time(recovered[failed_item])
    assert restored_time and restored_time != failed_time, f"恢复项应刷新原成功时间 {recovered[failed_item]!r}"
    assert not FAILED.search(recovered[failed_item]), f"恢复项应清除失败提示 {recovered[failed_item]!r}"
    assert TIMEOUT_REASON.search(partial[failed_item]), (
        f"AC-11 {failed_item} 在失败轮应就地显示超时原因并保留旧值和时间，失败轮 {partial[failed_item]!r}，"
        f"恢复轮 {recovered[failed_item]!r}"
    )


def settings_row(browser: Browser, name: str) -> str:
    return str(observe(browser, f"({account_entries(SETTINGS)}).find(row => row.name === {json.dumps(name)})?.text || ''"))


def expect_bookmarked_result(browser: Browser, page: str) -> None:
    expect_view(browser, page, GOOGLE, NAME, [NAME])
    if page == OVERVIEW:
        expect_credits(google_items(browser)[CREDITS_ITEM], 2187)
    else:
        row = settings_row(browser, NAME)
        assert re.search(CONNECTED, row) and not FAILED.search(row), f"配置管理应回读恢复后的连接状态，实得 {row!r}"


@PAGES
def test_ac01_ac03_ac04_ac05_google_bookmark_reopens_and_rename_keeps_connection(
    dashboard: Session, tmp_path: Path, page: str,
) -> None:
    browser, origin, source = dashboard
    add_google(browser)
    connect_google(browser, source)
    view(browser, OVERVIEW)
    # 保存无需授权的另一平台待连接记录，使原搜索词也能命中该平台。
    assert browser.click_control(r"添加订阅$"), "应可从总览添加另一平台的订阅"
    assert wait_for(browser, "headings.includes('添加订阅')"), "应打开添加订阅表单"
    set_field(browser, "provider", "codex")
    type_into(browser, ACCOUNT_NAME, NEIGHBOR)
    assert browser.click_control(r"^保存并去授权$"), "应可保存 Codex 待连接记录"
    assert wait_for(browser, f"headings.includes('连接 Codex 账号') && {main_matches(re.escape(NEIGHBOR))}"), (
        "保存后应进入该待连接订阅的授权页"
    )
    view(browser, OVERVIEW)
    expect_view(browser, OVERVIEW, "全部平台", "", [NAME, NEIGHBOR])
    expect_credits(google_items(browser)[CREDITS_ITEM], 1347)
    if page == SETTINGS:
        source.set_cli_replies(usage=UPSTREAM_TIMEOUT, credits=UPSTREAM_TIMEOUT)
        refresh_google(browser, source)
    view(browser, page)
    select_platform(browser, GOOGLE)
    type_into(browser, SEARCH, NAME)
    expect_view(browser, page, GOOGLE, NAME, [NAME])
    if page == SETTINGS:
        row = settings_row(browser, NAME)
        assert FAILED.search(row), f"配置管理应在重开前显示本轮查询失败，实得 {row!r}"
    bookmark = str(observe(browser, "location.href"))
    params = parse_qs(urlsplit(bookmark).query)
    assert {key: params.get(key) for key in ("view", "platform", "q")} == {
        "view": [ROUTES[page]], "platform": ["google"], "q": [NAME],
    }, f"书签应保存页面、平台和搜索词，实得 {bookmark!r}"

    open_entry(browser, origin)
    expect_view(browser, OVERVIEW, "全部平台", "", [NAME, NEIGHBOR])
    assert not urlsplit(str(observe(browser, "location.href"))).query, "直接打开首页应显示全部订阅"
    source.set_cli_replies(credits={"remaining_credits": 2187})
    refresh_google(browser, source)
    expect_credits(google_items(browser)[CREDITS_ITEM], 2187)
    previous_document = observe(browser, "performance.timeOrigin")
    open_entry(browser, bookmark)
    expect_bookmarked_result(browser, page)
    assert observe(browser, "performance.timeOrigin") != previous_document, "应重开书签并重新加载文档"

    new_profile = tmp_path / "signed-out"
    new_profile.mkdir()
    with open_chrome(new_profile) as signed_out:
        sign_in(signed_out, bookmark, page)
        expect_bookmarked_result(signed_out, page)

    view(browser, SETTINGS)
    oauth_before = source.requests()
    edit_account(browser, NAME)
    type_into(browser, ACCOUNT_NAME, RENAMED)
    submit(browser)
    assert wait_for(browser, "headings.includes('配置管理')"), "仅改名应保存并返回配置管理"
    open_entry(browser, bookmark)
    expect_view(browser, page, GOOGLE, NAME, [])
    assert wait_for(browser, NO_MATCH), "改名后原 Google 书签应显示无匹配"
    assert not observe(browser, FIRST_USE), "还有订阅时应显示无匹配空态"
    assert browser.click_control(r"^清除条件$"), "无匹配应可清除条件"
    expect_view(browser, page, "全部平台", "", [RENAMED, NEIGHBOR])
    params = parse_qs(urlsplit(str(observe(browser, "location.href"))).query)
    assert not params.get("platform") and not params.get("q"), f"清除条件后网址应同步更新，实得 {params!r}"

    view(browser, OVERVIEW)
    source.set_cli_replies(credits={"remaining_credits": 2764})
    refresh_google(browser, source)
    expect_credits(google_items(browser, RENAMED)[CREDITS_ITEM], 2764)
    assert not any(
        request.method == "POST" and request.path == TOKEN_PATH
        for request in source.requests()[len(oauth_before):]
    ), "改名后应沿用原 Google 授权，无需再次授权"
