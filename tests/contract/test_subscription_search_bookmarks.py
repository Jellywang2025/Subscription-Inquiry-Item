"""Ticket 07 / Spec 0.2.0: AC-01, AC-03, AC-04 and AC-09."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from deepseek_source import BalanceReply, DeepSeekSource, serve_deepseek
from mimo_source import serve_mimo
from test_deepseek_balance import Browser, CNY, money, open_chrome, run_dashboard
from test_mimo_balance import official_balance
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for
from test_subscription_codex_device_login import refresh_now
from test_subscription_deepseek_account import (
    ACCOUNT_NAME, CARD, SEARCH, SECRET, add_account as add_deepseek, edit_account, reply, resize, submit, type_into, view,
)
from test_subscription_mimo_account import ORIGINAL_SK, open_mimo_form
from test_subscription_refresh_recovery import cny


# The query matches both providers. The second DeepSeek account needs to be excluded by name.
TARGET = "青岚 & A+B"
MIMO_NAME = f"{TARGET} 备用"
OTHER_DEEPSEEK = "松风额度"
RENAMED = "澄海余额"
TARGET_KEY = "contract-bookmark-target-B123"
OTHER_KEY = "contract-bookmark-neighbor-B456"
ALL_NAMES = [TARGET, OTHER_DEEPSEEK, MIMO_NAME]
ROUTES = {OVERVIEW: "overview", SETTINGS: "settings"}
PAGES = pytest.mark.parametrize("page", list(ROUTES), ids=list(ROUTES.values()))
FIRST_USE = "headings.some(text => /还没有订阅|暂无订阅|尚未配置订阅/.test(text))"
NO_MATCH = "headings.some(text => /没有匹配|无匹配|未找到匹配/.test(text))"
type Session = tuple[Browser, str, DeepSeekSource, dict[str, tuple[str, BalanceReply]]]


@pytest.fixture
def session(tmp_path: Path) -> Iterator[Session]:
    replies = {TARGET_KEY: ("target", reply(cny("80.00"))), OTHER_KEY: ("neighbor", reply(cny("17.00")))}
    with (
        serve_deepseek(accounts=replies) as deepseek,
        serve_mimo({ORIGINAL_SK: ("original", official_balance(over_frozen=False))}) as mimo,
        run_dashboard(
            deepseek, tmp_path,
            QUOTA_STATE_FILE=str(tmp_path / "state.json"), QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_CODEX_ACCESS_TOKEN="",
            QUOTA_CODEX_BASE_URL="http://127.0.0.1:1", QUOTA_MIMO_BASE_URL=mimo.base_url,
            CODEX_HOME=str(tmp_path / "codex-home"),
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        yield browser, origin, deepseek, replies


def add_subscriptions(browser: Browser, deepseek: DeepSeekSource) -> None:
    add_deepseek(browser, deepseek, TARGET, TARGET_KEY, "target")
    add_deepseek(browser, deepseek, OTHER_DEEPSEEK, OTHER_KEY, "neighbor")
    open_mimo_form(browser)
    type_into(browser, ACCOUNT_NAME, MIMO_NAME)
    type_into(browser, SECRET, ORIGINAL_SK)
    submit(browser)
    assert wait_for(browser, f"headings.includes({json.dumps(SETTINGS)})"), "应保存 MiMo 账号并返回配置管理"


def select_platform(browser: Browser, label: str) -> None:
    assert observe(browser, f"""shown('main select:not(:disabled)').some(select => {{
        const option = [...select.options].find(item => item.text.trim() === {json.dumps(label)} && !item.disabled);
        if (!option || ![...select.options].some(item => item.text.trim() === '全部平台')) return false;
        select.value = option.value;
        select.dispatchEvent(new Event('change', {{bubbles: true}}));
        return true;
    }})"""), f"应能在平台筛选中选择「{label}」"


def account_entries(page: str) -> str:
    if page == OVERVIEW:
        return f"""shown({json.dumps(CARD)}).map(card => ({{
            name: shown('h3,h4,[role=heading]', card)[0]?.innerText.trim(), text: card.innerText
        }}))"""
    return """shown('main li, main tr, main [role=row]')
        .filter(row => shown('button, a[href], [role=button]', row).some(control =>
            (control.getAttribute('aria-label') || control.innerText.trim()) === '编辑'))
        .map(row => ({name: shown('b,strong,[role=rowheader],th[scope=row]', row)[0]?.innerText.trim(),
            text: row.innerText}))"""


def expect_view(browser: Browser, page: str, platform: str, query: str, names: list[str]) -> None:
    state = f"""({{
        page: shown('main h1')[0]?.innerText.trim(),
        platform: shown('main select').find(select => [...select.options].some(option =>
            option.text.trim() === '全部平台'))?.selectedOptions[0]?.text.trim(),
        query: shown({json.dumps(SEARCH)})[0]?.value,
        names: ({account_entries(page)}).map(entry => entry.name ?? '').sort(),
    }})"""
    # Sort in the browser to use a single ordering for the Chinese account names.
    expected = f"""({{page: {json.dumps(page)}, platform: {json.dumps(platform)}, query: {json.dumps(query)},
        names: {json.dumps(names)}.sort()}})"""
    assert wait_for(browser, f"JSON.stringify({state}) === JSON.stringify({expected})"), (
        f"应显示「{page}」、平台「{platform}」、搜索词 {query!r}、账号 {names!r}，"
        f"实际 {observe(browser, state)!r}，网址 {observe(browser, 'location.href')!r}"
    )


def expect_account_text(
    browser: Browser, page: str, pattern: re.Pattern[str], *, replaced: re.Pattern[str] | None = None,
) -> None:
    text = f"({account_entries(page)}).find(entry => entry.name === {json.dumps(TARGET)})?.text || ''"
    assert wait_for(browser, f"new RegExp({json.dumps(pattern.pattern)}, 'i').test({text})"), (
        f"「{page}」的 {TARGET!r} 应显示 {pattern.pattern!r}，实际 {observe(browser, text)!r}"
    )
    if replaced is not None:
        actual = str(observe(browser, text))
        assert not replaced.search(actual), f"「{page}」刷新后仍显示已替换的数据，实际 {actual!r}"


@PAGES
def test_ac03_filters_use_current_account_name(session: Session, page: str) -> None:
    browser, _, deepseek, _ = session
    add_subscriptions(browser, deepseek)
    view(browser, page)
    expect_view(browser, page, "全部平台", "", ALL_NAMES)
    select_platform(browser, "DeepSeek")
    expect_view(browser, page, "DeepSeek", "", [TARGET, OTHER_DEEPSEEK])
    type_into(browser, SEARCH, TARGET)
    expect_view(browser, page, "DeepSeek", TARGET, [TARGET])
    select_platform(browser, "MiMo")
    expect_view(browser, page, "MiMo", TARGET, [MIMO_NAME])

    view(browser, SETTINGS)
    select_platform(browser, "DeepSeek")
    edit_account(browser, TARGET)
    type_into(browser, ACCOUNT_NAME, RENAMED)
    submit(browser)
    assert wait_for(browser, f"headings.includes({json.dumps(SETTINGS)})"), "应保存新名称并返回配置管理"
    view(browser, page)
    select_platform(browser, "DeepSeek")
    type_into(browser, SEARCH, RENAMED)
    expect_view(browser, page, "DeepSeek", RENAMED, [RENAMED])
    type_into(browser, SEARCH, TARGET)
    expect_view(browser, page, "DeepSeek", TARGET, [])


@PAGES
def test_ac01_ac03_bookmark_reopens_with_latest_results(session: Session, tmp_path: Path, page: str) -> None:
    browser, origin, deepseek, replies = session
    add_subscriptions(browser, deepseek)
    view(browser, OVERVIEW)
    previous_balance = money(CNY, r"80\.00")
    failed_status = re.compile(r"刷新失败|查询失败")
    expect_account_text(browser, OVERVIEW, previous_balance)
    # Change the visible connection status as well as the balance so both pages expose fresh data.
    replies[TARGET_KEY] = ("target", BalanceReply([], status=503))
    refresh_now(browser)
    view(browser, page)
    select_platform(browser, "DeepSeek")
    type_into(browser, SEARCH, TARGET)
    expect_view(browser, page, "DeepSeek", TARGET, [TARGET])
    expect_account_text(browser, page, failed_status)

    bookmark = str(observe(browser, "location.href"))
    params = parse_qs(urlsplit(bookmark).query)
    assert {key: params.get(key) for key in ("view", "platform", "q")} == {
        "view": [ROUTES[page]], "platform": ["deepseek"], "q": [TARGET],
    }, f"筛选后地址栏应能保存页面、平台及原搜索词，实际 {bookmark!r}"

    open_entry(browser, origin)
    expect_view(browser, OVERVIEW, "全部平台", "", ALL_NAMES)
    replies[TARGET_KEY] = ("target", reply(cny("65.00")))
    refresh_now(browser)
    latest = money(CNY, r"65\.00")
    expect_account_text(browser, OVERVIEW, latest, replaced=previous_balance)
    fresh_result = latest if page == OVERVIEW else re.compile("已连接")
    replaced_result = previous_balance if page == OVERVIEW else failed_status

    home_document = observe(browser, "performance.timeOrigin")
    open_entry(browser, bookmark)
    expect_view(browser, page, "DeepSeek", TARGET, [TARGET])
    assert observe(browser, "performance.timeOrigin") != home_document, "应在已登录浏览器重新加载书签文档"
    expect_account_text(browser, page, fresh_result, replaced=replaced_result)

    open_entry(browser, origin)
    expect_view(browser, OVERVIEW, "全部平台", "", ALL_NAMES)
    assert not urlsplit(str(observe(browser, "location.href"))).query, "直接打开首页应使用无查询参数的网址"

    new_profile = tmp_path / "signed-out"
    new_profile.mkdir()
    with open_chrome(new_profile) as signed_out:
        sign_in(signed_out, bookmark, page)
        expect_view(signed_out, page, "DeepSeek", TARGET, [TARGET])
        expect_account_text(signed_out, page, fresh_result, replaced=replaced_result)


@PAGES
def test_ac04_clear_conditions_differs_from_first_use(session: Session, page: str) -> None:
    browser, _, deepseek, _ = session
    view(browser, page)
    assert wait_for(browser, FIRST_USE), f"「{page}」无配置时应显示首次使用空态"
    assert wait_for(browser, "shown('main button, main a[href]').some(control => control.innerText.trim() === '添加第一条订阅')"), (
        "首次使用时应提供添加第一条订阅操作"
    )
    assert not observe(browser, NO_MATCH), "首次使用时不应显示无匹配提示"
    add_subscriptions(browser, deepseek)
    view(browser, page)
    expect_view(browser, page, "全部平台", "", ALL_NAMES)

    select_platform(browser, "DeepSeek")
    query = "不存在的订阅 & Z+9"
    type_into(browser, SEARCH, query)
    expect_view(browser, page, "DeepSeek", query, [])
    assert wait_for(browser, NO_MATCH), f"「{page}」有订阅但筛选不到时应显示无匹配提示"
    assert not observe(browser, FIRST_USE), "无匹配时不应显示首次使用提示"
    assert browser.click_control(r"^清除条件$"), "无匹配时应提供可点击的「清除条件」"
    expect_view(browser, page, "全部平台", "", ALL_NAMES)
    params = parse_qs(urlsplit(str(observe(browser, "location.href"))).query)
    assert not params.get("platform") and not params.get("q"), f"清除后书签也应移除筛选条件，实际 {params!r}"


@PAGES
def test_ac09_resize_keeps_current_view_and_filters(session: Session, page: str) -> None:
    browser, _, deepseek, _ = session
    add_subscriptions(browser, deepseek)
    view(browser, page)
    select_platform(browser, "DeepSeek")
    type_into(browser, SEARCH, TARGET)
    expect_view(browser, page, "DeepSeek", TARGET, [TARGET])
    bookmark = observe(browser, "location.href")
    original_document = observe(browser, "performance.timeOrigin")

    for width, height in [(1080, 1920), (390, 844), (1920, 1080)]:
        resize(browser, width, height)
        expect_view(browser, page, "DeepSeek", TARGET, [TARGET])
        assert observe(browser, "location.href") == bookmark, f"{width}×{height} 下应保留筛选网址"
        assert observe(browser, "performance.timeOrigin") == original_document, f"{width}×{height} 下应保留当前文档"
