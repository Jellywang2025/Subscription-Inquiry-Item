"""Subscription management 0.2.0, AC-01 and the first-use portion of AC-04."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from deepseek_source import DeepSeekSource
from test_deepseek_balance import Browser, open_chrome, poll, run_dashboard


ADMIN_PASSWORD = "contract-admin-entry-password"
SEARCH_TERM = "青岚 & A+B"
LOGIN = r"^(?:管理员\s*)?登\s*录(?:系统)?$"
FIRST_SUBSCRIPTION = "添加第一条订阅"
OVERVIEW = "订阅总览"
SETTINGS = "配置管理"

VISIBLE_UI = """
    const shown = (selector, root = document) => [...root.querySelectorAll(selector)].filter(
        element => element.getClientRects().length && getComputedStyle(element).visibility === 'visible'
    );
    const headings = shown('h1,h2,h3,h4,h5,h6,[role=heading]').map(element => element.innerText.trim());
    const password = shown('input[type=password]:not(:disabled):not([readonly])')[0];
    const accountCards = shown('main article, main [role=article], main section[data-source]')
        .map(element => element.innerText.trim().split('\\n')[0]);
"""


@pytest.fixture
def empty_dashboard(tmp_path: Path) -> Iterator[tuple[Browser, str]]:
    # The shared runner defaults to a funded-source fixture. Supply no provider credentials here.
    no_provider = "http://127.0.0.1:1"
    with (
        run_dashboard(
            DeepSeekSource(no_provider),
            tmp_path,
            QUOTA_STATE_FILE=str(tmp_path / "new-state.json"),
            QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            DEEPSEEK_API_KEY="",
            MIMO_API_KEY="",
            QUOTA_CODEX_ACCESS_TOKEN="",
            QUOTA_MIMO_BASE_URL=no_provider,
            QUOTA_CODEX_BASE_URL=no_provider,
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        yield browser, origin


def observe(browser: Browser, expression: str) -> Any:
    return browser.evaluate(f"(() => {{ {VISIBLE_UI} return ({expression}); }})()")


def wait_for(browser: Browser, condition: str) -> bool:
    def probe() -> str:
        try:
            return "ready" if observe(browser, condition) else ""
        except RuntimeError as error:
            # A full-page redirect can replace the JavaScript context between CDP calls.
            if "context was destroyed" in str(error) or "Cannot find context" in str(error):
                return ""
            raise

    return bool(poll(probe, bool))


def page_details(browser: Browser) -> str:
    return str(observe(browser, """({url: location.href, title: document.title, headings, accountCards,
        firstSubscriptionAction: shown('button, [role=button], a[href]').some(control =>
            (control.getAttribute('aria-label') || control.innerText.trim()) === '添加第一条订阅')})"""))


def open_entry(browser: Browser, url: str) -> None:
    result = browser.call("Page.navigate", url=url)
    if result.get("errorText"):
        pytest.fail(f"Chrome 无法打开 {url}，导航错误 {result['errorText']}")
    origin = json.dumps(f"http://{urlsplit(url).netloc}")
    if not wait_for(browser, f"location.origin === {origin} && document.readyState === 'complete'"):
        pytest.fail(f"Chrome 未完成加载 {url}，实际页面 {page_details(browser)}")


def sign_in(browser: Browser, url: str, heading: str) -> None:
    open_entry(browser, url)
    assert wait_for(browser, "password"), (
        f"未登录访问 {url} 应先显示管理员口令输入框，实际页面 {page_details(browser)}"
    )
    assert observe(browser, "!headings.includes('订阅总览') && !headings.includes('配置管理') && !accountCards.length"), (
        f"未登录时应先完成管理员登录，实际可见受保护内容 {page_details(browser)}"
    )
    assert observe(browser, "(password.focus(), document.activeElement === password)"), "管理员口令框无法输入"
    browser.call("Input.insertText", text=ADMIN_PASSWORD)
    assert browser.click_control(LOGIN), "应有可点击的管理员登录按钮"
    assert wait_for(browser, f"!password && headings.includes({json.dumps(heading)})"), (
        f"提交管理员口令后应显示「{heading}」，实际页面 {page_details(browser)}"
    )

    expected = urlsplit(url)
    actual = urlsplit(observe(browser, "location.href"))
    assert (actual.scheme, actual.netloc, actual.path) == (expected.scheme, expected.netloc, expected.path), (
        f"登录后应回到原网址 {url}，实际 {actual.geturl()}"
    )
    if expected.query:
        assert parse_qs(actual.query, keep_blank_values=True) == parse_qs(expected.query, keep_blank_values=True), (
            f"登录后应保留原查询参数，预期 {expected.query!r}，实际 {actual.query!r}"
        )


@pytest.mark.parametrize(
    ("query", "heading"),
    [
        pytest.param({}, OVERVIEW, id="home"),
        pytest.param({"view": "settings"}, SETTINGS, id="settings"),
        pytest.param({"platform": "deepseek", "q": SEARCH_TERM}, OVERVIEW, id="filtered-overview"),
    ],
)
def test_ac01_login_returns_to_requested_view(
    empty_dashboard: tuple[Browser, str], query: dict[str, str], heading: str,
) -> None:
    browser, origin = empty_dashboard
    sign_in(browser, f"{origin}?{urlencode(query)}" if query else origin, heading)
    if heading == OVERVIEW:
        platform_selected = (
            f"select.value === {json.dumps(query['platform'])}"
            if "platform" in query else "select.selectedOptions[0]?.text.trim() === '全部平台'"
        )
        assert wait_for(
            browser,
            "shown('input[type=search], input[role=searchbox]').some(input => input.value === "
            f"{json.dumps(query.get('q', ''))}) && shown('select').some(select => {platform_selected})",
        ), (
            f"登录后筛选控件应恢复原选项，预期平台 {query.get('platform', '全部平台')!r}、"
            f"搜索词 {query.get('q', '')!r}，实际页面 {page_details(browser)}"
        )


def test_ac04_first_use_overview_starts_adding_a_subscription(empty_dashboard: tuple[Browser, str]) -> None:
    browser, origin = empty_dashboard
    sign_in(browser, origin, OVERVIEW)
    assert wait_for(browser, "headings.some(text => /还没有订阅|暂无订阅|尚未配置订阅/.test(text))"), (
        f"空库应显示首次使用空态，实际页面 {page_details(browser)}"
    )
    assert not observe(browser, "accountCards.length"), f"空库出现预置账号卡片 {page_details(browser)}"
    assert not observe(browser, """document.body.innerText.includes('ChatGPT 聊天') ||
        shown('a[href]').some(link => new URL(link.href).hostname === 'chatgpt.com'
            && new URL(link.href).pathname === '/')"""), "空总览出现旧版 ChatGPT 聊天卡片或聊天入口"

    assert browser.click_control(f"^{FIRST_SUBSCRIPTION}$"), f"空态应有可点击的「{FIRST_SUBSCRIPTION}」"
    assert wait_for(browser, """headings.includes('添加订阅') && shown('select:not(:disabled)').some(select =>
        ['Codex', 'DeepSeek', 'MiMo'].every(platform =>
            [...select.options].some(option => option.text.trim() === platform && !option.disabled)))"""), (
        f"点击后应进入可选择平台的添加订阅流程，实际页面 {page_details(browser)}"
    )
