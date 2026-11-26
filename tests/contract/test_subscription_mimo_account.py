"""Ticket 03 / Spec 0.2.0: AC-02, AC-05, AC-07, and the MiMo form in AC-09."""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from deepseek_source import BALANCE_PATH as DEEPSEEK_PATH
from deepseek_source import DeepSeekSource, Request, serve_deepseek
from mimo_source import MiMoSource, serve_mimo
from test_deepseek_balance import Browser, CNY, USD, balance, money, open_chrome, poll, run_dashboard, success_time
from test_mimo_balance import MIMO_PATH, official_balance
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for
from test_subscription_deepseek_account import (
    ACCOUNT_NAME, CARD, NO_RESULT, SAVED_TIME, SEARCH, SECRET,
    add_account as add_deepseek, card_text, edit_account, expect_balance, refresh,
    reply, resize, settings, submit, type_into, view,
)


NAME = "晴岚工作号"
RENAMED = "雨后云栖"
OTHER = "临溪额度"
ORIGINAL_SK = "sk-contract-mimo-original-M123"
REPLACEMENT_SK = "sk-contract-mimo-replacement-M456"
INVALID_SK = "sk-contract-mimo-rejected-M789"
DEEPSEEK_KEY = "contract-deepseek-mimo-neighbor-D321"
SECRETS = (ORIGINAL_SK, REPLACEMENT_SK, INVALID_SK, DEEPSEEK_KEY)


@pytest.fixture
def over_frozen() -> bool:
    return False


@pytest.fixture
def dashboard(over_frozen: bool, tmp_path: Path) -> Iterator[tuple[Browser, str, MiMoSource, DeepSeekSource]]:
    with (
        serve_mimo({
            ORIGINAL_SK: ("original", official_balance(over_frozen=over_frozen)),
            REPLACEMENT_SK: ("replacement", official_balance(over_frozen=True)),
            INVALID_SK: ("rejected", None),
        }) as mimo,
        serve_deepseek(accounts={DEEPSEEK_KEY: ("neighbor", reply(balance("USD", "17.00", "14.00", "3.00")))}) as deepseek,
        run_dashboard(
            deepseek, tmp_path,
            QUOTA_STATE_FILE=str(tmp_path / "state.json"),
            QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_CODEX_ACCESS_TOKEN="",
            QUOTA_CODEX_BASE_URL="http://127.0.0.1:1", QUOTA_MIMO_BASE_URL=mimo.base_url,
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        yield browser, origin, mimo, deepseek


def open_mimo_form(browser: Browser) -> None:
    assert browser.click_control(r"添加(?:第一条)?订阅$"), "应可从网页打开「添加订阅」"
    assert wait_for(browser, "headings.includes('添加订阅')"), "应显示添加订阅表单"
    assert observe(browser, """shown('main form select:not(:disabled)').some(select => {
        const option = [...select.options].find(option => option.text.trim() === 'MiMo' && !option.disabled);
        if (!option) return false;
        select.value = option.value;
        select.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    })"""), "添加表单应可选择 MiMo"


def account_summaries(browser: Browser) -> list[tuple[str, str, str]]:
    result = browser.call(
        "Runtime.evaluate",
        expression="""(async () => {
            const response = await fetch('/api/accounts', {cache: 'no-store'});
            return {status: response.status, text: await response.text()};
        })()""",
        awaitPromise=True, returnByValue=True,
    )
    assert "exceptionDetails" not in result, "浏览器无法读取 GET /api/accounts"
    response = result["result"]["value"]
    assert response["status"] == 200, f"登录后 GET /api/accounts 应可回读，实得 HTTP {response['status']}"
    assert all(secret not in response["text"] for secret in SECRETS), "GET /api/accounts 泄漏完整测试密钥"
    rows = json.loads(response["text"]).get("accounts")
    assert isinstance(rows, list), "GET /api/accounts 应返回账号列表"
    return [(row.get("provider"), row.get("name"), row.get("key_tail")) for row in rows]


def assert_page_masks(browser: Browser, key: str) -> None:
    assert wait_for(browser, f"document.body.innerText.includes({json.dumps(key[-4:])})"), "页面应展示已保存 SK 末四位"
    assert not observe(browser, f"""{json.dumps(SECRETS)}.some(key =>
        document.documentElement.outerHTML.includes(key) ||
        [...document.querySelectorAll('input, textarea')].some(field => field.value.includes(key)))"""), (
        "重新打开的页面泄漏完整测试密钥"
    )


def add_mimo(browser: Browser, source: MiMoSource) -> None:
    before = len(source.requests())
    open_mimo_form(browser)
    type_into(browser, ACCOUNT_NAME, NAME)
    type_into(browser, SECRET, ORIGINAL_SK)
    feedback = submit(browser)
    status = observe(browser, """performance.getEntriesByType('resource')
        .filter(entry => new URL(entry.name).pathname === '/api/accounts').at(-1)?.responseStatus""")
    rows = account_summaries(browser)
    requests = source.requests()[before:]
    assert ("mimo", NAME, ORIGINAL_SK[-4:]) in rows, (
        f"验证后应保存自命名 MiMo 账号及 SK 末四位。提交 HTTP {status!r}，页面反馈 {feedback!r}，"
        f"GET /api/accounts 回读账号数 {len(rows)}，余额请求 {requests!r}"
    )
    assert Request("GET", MIMO_PATH, True, identity="original") in requests, (
        f"保存前应由服务端用所填 SK 验证 MiMo 余额，实得 {requests!r}"
    )


def expect_mimo_balance(browser: Browser, name: str, amount: str) -> str:
    text = expect_balance(browser, name, money(CNY, re.escape(amount)))
    assert success_time(text) or SAVED_TIME.search(text), f"账号卡应显示成功更新时间，实得 {text!r}"
    assert not NO_RESULT.search(text), f"成功余额 {amount} 应能与缺少结果区分，实得 {text!r}"
    return text


def refresh_mimo(browser: Browser, source: MiMoSource, identity: str) -> None:
    before = len(source.requests())
    assert browser.click_control(r"立即刷新|^刷新$"), "总览应提供手动刷新"
    assert wait_for(browser, """shown('main button:not(:disabled)').some(
        button => /立即刷新|^刷新$/.test(button.innerText.trim()))"""), "手动刷新未完成"
    requests = source.requests()[before:]
    expected = Request("GET", MIMO_PATH, True, identity=identity)
    assert requests and all(request == expected for request in requests), (
        f"刷新应以 {identity} 测试身份查询 MiMo，实得 {requests!r}"
    )


@pytest.mark.parametrize("over_frozen", [False, True], ids=["total-200-frozen-20", "frozen-exceeds-total"])
def test_ac02_single_mimo_account_shows_spendable_balance_and_official_details(
    dashboard: tuple[Browser, str, MiMoSource, DeepSeekSource], over_frozen: bool,
) -> None:
    browser, origin, source, _ = dashboard
    add_mimo(browser, source)
    open_entry(browser, settings(origin))
    assert wait_for(browser, f"headings.includes('配置管理') && document.body.innerText.includes({json.dumps(NAME)})"), (
        "重开配置管理应显示自命名 MiMo 账号"
    )
    assert account_summaries(browser) == [("mimo", NAME, ORIGINAL_SK[-4:])], "仅配置一个 MiMo 账号就应可用"
    assert_page_masks(browser, ORIGINAL_SK)
    view(browser, OVERVIEW)

    before = expect_mimo_balance(browser, NAME, "0.00" if over_frozen else "180.00")
    assert observe(browser, f"shown({json.dumps(CARD)}).length === 1"), "单账号在总览应恰好对应一张卡"
    assert not re.search(r"账号用途|账号分类|工作号|个人号", before.replace(NAME, "")), "总览不应从名称推断用途"
    labels = [
        (r"现金(?:余额)?", "8.00" if over_frozen else "100.00"),
        (r"赠金(?:余额)?", "4.00" if over_frozen else "20.00"),
        (r"可用授信(?:额度)?", "3.00" if over_frozen else "80.00"),
        (r"冻结(?:金额|余额)?", "20.00"),
    ]
    details = [
        re.compile(rf"{label}\s*[:：]?\s*{money(CNY, re.escape(amount)).pattern}")
        for label, amount in labels
    ]
    assert not any(detail.search(before) for detail in details), f"展开前应折叠余额明细，实得 {before!r}"
    assert browser.click_control(r"明细|详情|展开", in_card=True, platform="MiMo"), "MiMo 卡应能展开余额明细"
    after = poll(lambda: card_text(browser, NAME), lambda text: all(detail.search(text) for detail in details))
    for detail, (label, amount) in zip(details, labels, strict=True):
        assert detail.search(after), f"展开后应显示官方 {label} 金额 CNY {amount}，实得 {after!r}"


def test_ac05_ac07_rename_then_reject_and_replace_mimo_sk(
    dashboard: tuple[Browser, str, MiMoSource, DeepSeekSource],
) -> None:
    browser, origin, source, deepseek = dashboard
    add_mimo(browser, source)
    open_entry(browser, settings(origin))
    add_deepseek(browser, deepseek, OTHER, DEEPSEEK_KEY, "neighbor")
    edit_account(browser, NAME)
    assert_page_masks(browser, ORIGINAL_SK)
    assert observe(browser, f"shown({json.dumps(SECRET)}).some(input => input.value === '')"), "新 SK 输入框应为空"
    type_into(browser, ACCOUNT_NAME, RENAMED)
    submit(browser)
    neighbor = ("deepseek", OTHER, DEEPSEEK_KEY[-4:])
    original = sorted([("mimo", RENAMED, ORIGINAL_SK[-4:]), neighbor])
    assert sorted(account_summaries(browser)) == original, "仅改名应保留原 SK 末四位和另一账号"
    view(browser, OVERVIEW)
    refresh_mimo(browser, source, "original")
    expect_mimo_balance(browser, RENAMED, "180.00")
    assert not card_text(browser, NAME), "改名后应移除旧名称的卡片"

    view(browser, SETTINGS)
    edit_account(browser, RENAMED)
    type_into(browser, SECRET, INVALID_SK)
    before = len(source.requests())
    feedback = submit(browser)
    assert Request("GET", MIMO_PATH, False, identity="rejected") in source.requests()[before:], (
        "服务端应携带新 SK 向 MiMo 验证"
    )
    assert re.search(r"401|密钥验证失败|(?:密钥|SK).{0,12}(?:无效|错误|不正确)|认证失败|未授权", feedback, re.IGNORECASE), (
        f"错误 SK 应提示验证失败原因，实得 {feedback!r}"
    )
    open_entry(browser, settings(origin))
    assert sorted(account_summaries(browser)) == original, "验证失败的 SK 应保留两个账号的原连接"
    assert_page_masks(browser, ORIGINAL_SK)
    view(browser, OVERVIEW)
    refresh_mimo(browser, source, "original")
    expect_mimo_balance(browser, RENAMED, "180.00")

    view(browser, SETTINGS)
    edit_account(browser, RENAMED)
    type_into(browser, SECRET, REPLACEMENT_SK)
    before = len(source.requests())
    submit(browser)
    assert Request("GET", MIMO_PATH, True, identity="replacement") in source.requests()[before:], (
        "应先由服务端验证有效新 SK"
    )
    open_entry(browser, settings(origin))
    replaced = sorted([("mimo", RENAMED, REPLACEMENT_SK[-4:]), neighbor])
    assert sorted(account_summaries(browser)) == replaced, "有效新 SK 应只替换选中账号的连接"
    assert_page_masks(browser, REPLACEMENT_SK)
    view(browser, OVERVIEW)
    refresh_mimo(browser, source, "replacement")
    expect_mimo_balance(browser, RENAMED, "0.00")


def test_ac05_cancel_then_delete_mimo_without_affecting_deepseek(
    dashboard: tuple[Browser, str, MiMoSource, DeepSeekSource],
) -> None:
    browser, origin, source, deepseek = dashboard
    view(browser, SETTINGS)
    add_deepseek(browser, deepseek, OTHER, DEEPSEEK_KEY, "neighbor")
    add_mimo(browser, source)
    view(browser, OVERVIEW)
    expect_mimo_balance(browser, NAME, "180.00")
    expect_balance(browser, OTHER, money(USD, r"17\.00"))
    view(browser, SETTINGS)
    type_into(browser, SEARCH, NAME)
    assert wait_for(browser, f"""document.querySelector('main').innerText.includes({json.dumps(NAME)}) &&
        !document.querySelector('main').innerText.includes({json.dumps(OTHER)})"""), "按名称搜索应只匹配 MiMo 账号"

    dialog = f"""shown('dialog[open], [role=dialog]').some(dialog =>
        dialog.innerText.includes({json.dumps(NAME)}) && dialog.innerText.includes('MiMo'))"""
    assert browser.click_control(r"^删除$") and wait_for(browser, dialog), "删除确认框应显示 MiMo 和所选账号名称"
    assert browser.click_control(r"^取消$"), "应可取消删除"
    assert wait_for(browser, "!shown('dialog[open], [role=dialog]').length"), "取消后确认框应关闭"
    open_entry(browser, settings(origin))
    neighbor = ("deepseek", OTHER, DEEPSEEK_KEY[-4:])
    assert sorted(account_summaries(browser)) == sorted([("mimo", NAME, ORIGINAL_SK[-4:]), neighbor]), (
        "取消后重新打开应仍能读回两个账号及各自的密钥末四位"
    )
    view(browser, OVERVIEW)
    before = len(source.requests())
    assert Request("GET", DEEPSEEK_PATH, True, identity="neighbor") in refresh(browser, deepseek)
    assert Request("GET", MIMO_PATH, True, identity="original") in source.requests()[before:], (
        "取消删除后 MiMo 仍应能以原 SK 刷新"
    )

    view(browser, SETTINGS)
    type_into(browser, SEARCH, NAME)
    assert browser.click_control(r"^删除$") and wait_for(browser, dialog), "应可重新打开 MiMo 删除确认框"
    assert browser.click_control(r"^确认删除$"), "应可确认删除 MiMo"
    assert wait_for(browser, "headings.includes('没有匹配的订阅')"), "确认后配置列表应移除选中的 MiMo 账号"
    open_entry(browser, settings(origin))
    assert account_summaries(browser) == [neighbor], "重开后应只回读 DeepSeek 账号及其原密钥末四位"
    assert_page_masks(browser, DEEPSEEK_KEY)
    assert not observe(browser, f"document.body.innerText.includes({json.dumps(ORIGINAL_SK[-4:])})"), (
        "重开配置页不应残留已删 MiMo 的 SK 末四位"
    )
    view(browser, OVERVIEW)
    before = source.requests()
    requests = refresh(browser, deepseek)
    assert requests and all(item == Request("GET", DEEPSEEK_PATH, True, identity="neighbor") for item in requests), (
        f"剩余 DeepSeek 账号应按原密钥刷新，实得 {requests!r}"
    )
    assert source.requests() == before, "确认删除后刷新应跳过 MiMo"
    assert not card_text(browser, NAME), "已删除的 MiMo 余额及缓存不应在总览重新出现"
    expect_balance(browser, OTHER, money(USD, r"17\.00"))


def test_ac09_mimo_sk_form_layout_and_draft_at_approved_and_narrow_viewports(
    dashboard: tuple[Browser, str, MiMoSource, DeepSeekSource],
) -> None:
    browser, _, _, _ = dashboard
    view(browser, SETTINGS)
    open_mimo_form(browser)
    type_into(browser, ACCOUNT_NAME, NAME)
    type_into(browser, SECRET, ORIGINAL_SK)
    for width, height in [(1920, 1080), (1080, 1920), (390, 844)]:
        resize(browser, width, height)
        assert observe(browser, f"""headings.includes('添加订阅') &&
            shown('main form select').some(select => select.selectedOptions[0]?.text.trim() === 'MiMo') &&
            shown({json.dumps(ACCOUNT_NAME)}).some(input => input.value === {json.dumps(NAME)}) &&
            shown({json.dumps(SECRET)}).some(input => input.value === {json.dumps(ORIGINAL_SK)})"""), (
            f"{width}×{height} 下应保留 MiMo 选择、名称和未提交的 SK"
        )
        assert observe(browser, fr"""shown({json.dumps(SECRET)}).some(input =>
            (input.getAttribute('aria-label') ?? '') === 'MiMo SK' ||
            [...input.labels].some(label => /^MiMo SK\b/.test(label.innerText.trim())))"""), "密钥字段应标为「MiMo SK」"
        assert not observe(browser, """shown('main form label, main form option, main form legend')
            .some(el => /续费日|用途|分类|工作号|个人号/.test(el.innerText.trim())) ||
            shown('main form input[type=date], main form [name=renewal]').length"""), "MiMo 表单不应询问续费日或用途分类"
        position = "side.top >= form.bottom" if width <= 660 else "side.left >= form.right"
        assert observe(browser, f"""(() => {{
            const form = shown('main form')[0]?.getBoundingClientRect();
            const side = shown('main aside, main [role=complementary]')[0]?.getBoundingClientRect();
            return form && side && {position};
        }})()"""), f"{width}×{height} 下表单和连接说明应按核准密钥页面排列"
        assert observe(browser, "document.documentElement.scrollWidth <= innerWidth"), "密钥表单不应横向溢出"
