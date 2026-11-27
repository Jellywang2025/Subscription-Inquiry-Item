"""Ticket 02 / Spec 0.2.0: AC-02, AC-05, AC-07, and configuration aspects of AC-09."""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from deepseek_source import BALANCE_PATH, BalanceReply, DeepSeekSource, Request, serve_deepseek
from test_deepseek_balance import Browser, CNY, USD, balance, money, open_chrome, poll, run_dashboard, success_time
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for


NAME = "青岚工作号"
RENAMED = "澄海备用额度"
OTHER = "松风"
ORIGINAL_KEY = "contract-deepseek-original-A123"
REPLACEMENT_KEY = "contract-deepseek-replacement-B456"
OTHER_KEY = "contract-deepseek-independent-C789"
INVALID_KEY = "contract-deepseek-rejected-D012"
KEYS = (ORIGINAL_KEY, REPLACEMENT_KEY, OTHER_KEY, INVALID_KEY)
ACCOUNT_NAME = 'main form input[name="name"]'
SECRET = 'main form input[type="password"]'
SEARCH = 'main input[type="search"], main input[role="searchbox"]'
CARD = "main article, main [role=article]"
SAVED_TIME = re.compile(r"(?:已更新|上次成功|更新于)\s*.*?\d{1,2}:\d{2}")
NO_RESULT = re.compile(r"暂无(?:可用)?(?:余额|结果)|尚未完成查询|等待首次更新")


def resize(browser: Browser, width: int, height: int) -> None:
    browser.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
    assert wait_for(browser, f"innerWidth === {width} && innerHeight === {height}"), (
        f"浏览器视口未切换为 {width} × {height}"
    )


def type_into(browser: Browser, selector: str, value: str) -> None:
    assert observe(browser, f"""shown({json.dumps(selector)}).some(input => {{
        input.focus();
        input.select();
        return document.activeElement === input;
    }})"""), f"找不到可输入的控件 {selector}"
    browser.call("Input.insertText", text=value)


def choose_deepseek(browser: Browser) -> None:
    assert observe(browser, """shown('main select:not(:disabled)').some(select => {
        const option = [...select.options].find(option => option.text.trim() === 'DeepSeek' && !option.disabled);
        if (!option) return false;
        select.value = option.value;
        select.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    })"""), "添加表单应可选择 DeepSeek"


def view(browser: Browser, heading: str) -> None:
    assert browser.click_control(f"^{heading}$"), f"应有可点击的「{heading}」入口"
    assert wait_for(browser, f"headings.includes({json.dumps(heading)})"), f"应进入「{heading}」"


def settings(origin: str) -> str:
    return f"{origin}?view=settings"


def reply(*balances: dict[str, str]) -> BalanceReply:
    result = BalanceReply(list(balances))
    result.release.set()
    return result


@pytest.fixture
def zero_balance() -> bool:
    return False


@pytest.fixture
def dashboard(zero_balance: bool, tmp_path: Path) -> Iterator[tuple[Browser, str, DeepSeekSource]]:
    original = (
        reply(balance("CNY", "0.00", "0.00", "0.00"))
        if zero_balance
        else reply(balance("CNY", "80.00", "70.00", "10.00"), balance("USD", "5.00", "4.00", "1.00"))
    )
    with (
        serve_deepseek(accounts={
            ORIGINAL_KEY: ("original", original),
            REPLACEMENT_KEY: ("replacement", reply(balance("CNY", "92.00", "90.00", "2.00"))),
            OTHER_KEY: ("independent", reply(balance("USD", "17.00", "14.00", "3.00"))),
            INVALID_KEY: ("rejected", BalanceReply([], status=401)),
        }) as source,
        run_dashboard(
            source, tmp_path,
            QUOTA_STATE_FILE=str(tmp_path / "state.json"),
            QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_CODEX_ACCESS_TOKEN="",
            QUOTA_CODEX_BASE_URL="http://127.0.0.1:1", QUOTA_MIMO_BASE_URL="http://127.0.0.1:1",
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        yield browser, origin, source


def accounts(browser: Browser) -> list[dict[str, Any]]:
    result = browser.call(
        "Runtime.evaluate",
        expression="""(async () => {
            const response = await fetch('/api/accounts', {cache: 'no-store'});
            return {status: response.status, text: await response.text()};
        })()""",
        awaitPromise=True,
        returnByValue=True,
    )
    assert "exceptionDetails" not in result, "浏览器无法读取 GET /api/accounts"
    response = result["result"]["value"]
    assert response["status"] == 200, f"登录后 GET /api/accounts 应可读取，实际 HTTP {response['status']}"
    assert all(key not in response["text"] for key in KEYS), "GET /api/accounts 泄漏完整测试密钥"
    payload = json.loads(response["text"])
    assert isinstance(payload.get("accounts"), list), "GET /api/accounts 应返回账号列表"
    return payload["accounts"]


def assert_page_masks(browser: Browser, key: str) -> None:
    assert observe(browser, f"document.body.innerText.includes({json.dumps(key[-4:])})"), "页面应展示已保存密钥末四位"
    assert not observe(browser, f"""{json.dumps(KEYS)}.some(key =>
        document.documentElement.outerHTML.includes(key) ||
        [...document.querySelectorAll('input, textarea')].some(field => field.value.includes(key)))"""), (
        "保存后重新打开的页面泄漏完整测试密钥"
    )


def submit(browser: Browser) -> str:
    assert browser.click_control(r"验证并保存|保存(?:修改|订阅)?$"), "表单应有可点击的保存操作"
    assert wait_for(browser, """!shown('main form button[type=submit]').length ||
        shown('main form button[type=submit]:not(:disabled)').length"""), "表单未完成提交"
    return str(observe(browser, "shown('main [role=status], main [role=alert]').map(el => el.innerText).join(' ')"))


def add_account(browser: Browser, source: DeepSeekSource, name: str, key: str, identity: str) -> None:
    before = len(source.wait_for_requests(0, 0))
    assert browser.click_control(r"添加(?:第一条)?订阅$"), "应可从网页打开「添加订阅」"
    assert wait_for(browser, "headings.includes('添加订阅')"), "应显示添加订阅表单"
    choose_deepseek(browser)
    type_into(browser, ACCOUNT_NAME, name)
    type_into(browser, SECRET, key)
    feedback = submit(browser)
    status = observe(browser, """performance.getEntriesByType('resource')
        .filter(entry => new URL(entry.name).pathname === '/api/accounts').at(-1)?.responseStatus""")
    result = accounts(browser)
    matches = [row for row in result if row.get("name") == name and row.get("provider") == "deepseek"]
    assert len(matches) == 1, (
        f"应保存自命名 DeepSeek 账号 {name!r}。回读账号数 {len(result)}，"
        f"提交 HTTP 状态 {status!r}，页面反馈 {feedback!r}，"
        f"提交后的余额请求 {source.wait_for_requests(0, 0)[before:]!r}"
    )
    assert Request("GET", BALANCE_PATH, True, identity=identity) in source.wait_for_requests(before + 1, 4)[before:], (
        f"保存前应由服务端使用 {identity} 测试身份验证 DeepSeek 余额接口"
    )
    assert matches[0].get("key_tail") == key[-4:], "账号 API 应返回已保存密钥末四位"


def card_text(browser: Browser, name: str) -> str:
    return str(observe(browser, f"""shown({json.dumps(CARD)}).find(
        card => shown('h2,h3,h4,[role=heading]', card).some(title => title.innerText.trim() === {json.dumps(name)})
    )?.innerText || ''"""))


def expect_balance(browser: Browser, name: str, expected: re.Pattern[str]) -> str:
    assert wait_for(browser, f"shown({json.dumps(CARD)}).some(card => card.innerText.includes({json.dumps(name)}))"), (
        f"订阅总览应显示账号 {name!r} 的卡片"
    )
    text = poll(lambda: card_text(browser, name), lambda text: bool(expected.search(text)))
    assert expected.search(text), f"账号 {name!r} 应显示对应币种金额 {expected.pattern!r}，实得卡片 {text!r}"
    return text


def edit_account(browser: Browser, name: str) -> None:
    type_into(browser, SEARCH, name)
    assert wait_for(browser, f"document.querySelector('main').innerText.includes({json.dumps(name)})"), (
        f"配置管理应可按账号名称查找 {name!r}"
    )
    assert browser.click_control(r"^编辑$"), f"应可编辑找到的账号 {name!r}"
    assert wait_for(browser, f"shown({json.dumps(ACCOUNT_NAME)}).some(input => input.value === {json.dumps(name)})"), (
        f"编辑表单应回读账号名称 {name!r}"
    )


def refresh(browser: Browser, source: DeepSeekSource) -> tuple[Request, ...]:
    before = len(source.wait_for_requests(0, 0))
    assert browser.click_control(r"立即刷新|^刷新$"), "总览应提供手动刷新"
    assert wait_for(browser, """shown('main button:not(:disabled)').some(
        button => /立即刷新|^刷新$/.test(button.innerText.trim()))"""), "手动刷新未完成"
    return source.wait_for_requests(before + 1, 4)[before:]


@pytest.mark.parametrize("zero_balance", [False, True], ids=["cny-usd", "successful-zero"])
def test_ac02_single_account_shows_currency_balances_and_details(
    dashboard: tuple[Browser, str, DeepSeekSource], zero_balance: bool,
) -> None:
    browser, origin, source = dashboard
    add_account(browser, source, NAME, ORIGINAL_KEY, "original")
    open_entry(browser, settings(origin))
    assert wait_for(browser, f"headings.includes('配置管理') && document.body.innerText.includes({json.dumps(NAME)})"), (
        "重开配置管理应读回自命名账号"
    )
    assert_page_masks(browser, ORIGINAL_KEY)
    view(browser, OVERVIEW)

    total = money(CNY, r"0\.00") if zero_balance else money(CNY, r"80(?:\.00)?")
    before = expect_balance(browser, NAME, total)
    assert observe(browser, f"shown({json.dumps(CARD)}).length === 1"), "一个已配置账号应恰好对应一张卡"
    assert not re.search(r"账号用途|账号分类|工作号|个人号", before.replace(NAME, "")), "总览不应从名称推断用途"
    assert success_time(before) or SAVED_TIME.search(before), f"账号卡应显示实际查询成功时间，实得 {before!r}"
    assert not NO_RESULT.search(before), f"成功回执应区别于无结果，实得 {before!r}"
    expected = [(CNY, "0.00", "0.00")] if zero_balance else [(CNY, "70.00", "10.00"), (USD, "4.00", "1.00")]
    if not zero_balance:
        assert money(USD, r"5(?:\.00)?").search(before), f"折叠状态也应独立显示 USD 可用余额，实得 {before!r}"
    assert browser.click_control(r"明细|详情|展开", in_card=True), "DeepSeek 账号卡应能展开余额明细"
    after = card_text(browser, NAME)
    for currency, topped_up, granted in expected:
        for label, amount in [(r"充值(?:余额)?", topped_up), (r"赠金(?:余额)?", granted)]:
            labeled = re.compile(rf"{label}\s*[:：]?\s*{money(currency, re.escape(amount)).pattern}")
            assert not labeled.search(before) and labeled.search(after), (
                f"展开后应按币种显示对应的充值余额和赠金，缺少 {label} {currency} {amount}，实得 {after!r}"
            )


def test_ac05_ac07_rename_failed_and_successful_key_replacement(
    dashboard: tuple[Browser, str, DeepSeekSource],
) -> None:
    browser, origin, source = dashboard
    add_account(browser, source, NAME, ORIGINAL_KEY, "original")
    open_entry(browser, settings(origin))
    edit_account(browser, NAME)
    assert_page_masks(browser, ORIGINAL_KEY)
    assert observe(browser, f"shown({json.dumps(SECRET)}).some(input => input.value === '')"), (
        "编辑时新密钥输入框应留空"
    )
    type_into(browser, ACCOUNT_NAME, RENAMED)
    submit(browser)
    rows = accounts(browser)
    assert [(row.get("name"), row.get("key_tail")) for row in rows] == [(RENAMED, ORIGINAL_KEY[-4:])], (
        "仅改名应保存新名称并保留原密钥末四位"
    )
    view(browser, OVERVIEW)
    assert Request("GET", BALANCE_PATH, True, identity="original") in refresh(browser, source), "改名后刷新应使用原密钥"
    expect_balance(browser, RENAMED, money(CNY, r"80(?:\.00)?"))
    assert not card_text(browser, NAME), "改名后的总览不应再显示旧名称"
    open_entry(browser, settings(origin))
    edit_account(browser, RENAMED)
    type_into(browser, SECRET, INVALID_KEY)
    before = len(source.wait_for_requests(0, 0))
    feedback = submit(browser)
    assert Request("GET", BALANCE_PATH, False, identity="rejected") in source.wait_for_requests(before + 1, 4)[before:], (
        "无效新密钥应由服务端提交给 DeepSeek 校验"
    )
    assert re.search(r"401|(?:密钥|Key).{0,12}(?:无效|错误|不正确)|认证失败|未授权", feedback, re.IGNORECASE), (
        f"验证失败应说明原因，实得 {feedback!r}"
    )
    open_entry(browser, settings(origin))
    assert wait_for(browser, f"document.body.innerText.includes({json.dumps(RENAMED)})"), "配置列表应显示改后的名称"
    assert_page_masks(browser, ORIGINAL_KEY)
    assert INVALID_KEY[-4:] not in str(observe(browser, "document.body.innerText")), "无效新密钥不应被保存"
    view(browser, OVERVIEW)
    assert Request("GET", BALANCE_PATH, True, identity="original") in refresh(browser, source), (
        "无效替换后，刷新仍应使用原密钥"
    )
    expect_balance(browser, RENAMED, money(CNY, r"80(?:\.00)?"))
    view(browser, SETTINGS)
    edit_account(browser, RENAMED)
    type_into(browser, SECRET, REPLACEMENT_KEY)
    before = len(source.wait_for_requests(0, 0))
    submit(browser)
    assert Request("GET", BALANCE_PATH, True, identity="replacement") in source.wait_for_requests(before + 1, 4)[before:], (
        "应由服务端先验证有效新密钥"
    )
    rows = accounts(browser)
    assert [(row.get("name"), row.get("key_tail")) for row in rows] == [(RENAMED, REPLACEMENT_KEY[-4:])], (
        "有效密钥验证成功后应替换当前连接"
    )
    open_entry(browser, settings(origin))
    assert wait_for(browser, f"document.body.innerText.includes({json.dumps(RENAMED)})"), "替换后仍应读回账号"
    assert_page_masks(browser, REPLACEMENT_KEY)
    view(browser, OVERVIEW)
    requests = refresh(browser, source)
    assert requests and all(item == Request("GET", BALANCE_PATH, True, identity="replacement") for item in requests), (
        f"替换成功后应按新密钥查询，实际请求 {requests!r}"
    )
    expect_balance(browser, RENAMED, money(CNY, r"92(?:\.00)?"))


def test_ac05_cancel_then_delete_one_account_and_refresh_the_other(
    dashboard: tuple[Browser, str, DeepSeekSource],
) -> None:
    browser, origin, source = dashboard
    add_account(browser, source, NAME, ORIGINAL_KEY, "original")
    view(browser, SETTINGS)
    add_account(browser, source, OTHER, OTHER_KEY, "independent")
    view(browser, OVERVIEW)
    expect_balance(browser, NAME, money(CNY, r"80(?:\.00)?"))
    expect_balance(browser, OTHER, money(USD, r"17(?:\.00)?"))
    view(browser, SETTINGS)
    type_into(browser, SEARCH, NAME)
    assert wait_for(browser, f"""document.querySelector('main').innerText.includes({json.dumps(NAME)}) &&
        !document.querySelector('main').innerText.includes({json.dumps(OTHER)})"""), "按名称搜索应仅显示匹配账号"
    assert browser.click_control(r"^删除$"), "应可删除按名称查到的账号"
    dialog = f"""shown('dialog[open], [role=dialog]').some(dialog =>
        dialog.innerText.includes({json.dumps(NAME)}) && dialog.innerText.includes('DeepSeek'))"""
    assert wait_for(browser, dialog), "删除确认框应显示 DeepSeek 及待删除账号的自定义名称"
    assert browser.click_control(r"^取消$"), "删除确认框应能取消"
    assert wait_for(browser, f"!({dialog})"), "取消后应关闭删除确认框"
    open_entry(browser, settings(origin))
    rows = accounts(browser)
    assert {row.get("name") for row in rows} == {NAME, OTHER}, "取消删除后两个账号都应可回读"
    type_into(browser, SEARCH, NAME)
    assert browser.click_control(r"^删除$") and wait_for(browser, dialog), "应再次打开指定账号的删除确认框"
    assert browser.click_control(r"^确认删除$"), "应可确认删除所选账号"
    assert wait_for(browser, "headings.includes('没有匹配的订阅')"), "确认删除后配置列表应移除所选账号"
    open_entry(browser, settings(origin))
    rows = accounts(browser)
    assert [(row.get("name"), row.get("key_tail")) for row in rows] == [(OTHER, OTHER_KEY[-4:])], (
        "重开后 API 应只返回另一账号及其凭据末四位"
    )
    assert not observe(browser, f"document.body.innerText.includes({json.dumps(ORIGINAL_KEY[-4:])})"), (
        "重开配置页不应残留已删除账号的末四位"
    )
    view(browser, OVERVIEW)
    requests = refresh(browser, source)
    assert requests and all(item == Request("GET", BALANCE_PATH, True, identity="independent") for item in requests), (
        f"刷新应跳过已删除账号，只查询独立账号，实际请求 {requests!r}"
    )
    assert not card_text(browser, NAME), "已删除账号的余额缓存不应在总览重新出现"
    expect_balance(browser, OTHER, money(USD, r"17(?:\.00)?"))


def test_ac09_deepseek_form_keeps_draft_at_approved_and_narrow_viewports(
    dashboard: tuple[Browser, str, DeepSeekSource],
) -> None:
    browser, _, _ = dashboard
    view(browser, SETTINGS)
    type_into(browser, SEARCH, NAME)
    for width, height in [(1920, 1080), (1080, 1920), (390, 844)]:
        resize(browser, width, height)
        assert observe(browser, f"""headings.includes('配置管理') &&
            shown({json.dumps(SEARCH)}).some(input => input.value === {json.dumps(NAME)})"""), (
            f"{width}×{height} 下应保留配置管理及搜索词"
        )
        assert observe(browser, "document.documentElement.scrollWidth <= innerWidth"), "配置页不应横向溢出"
    resize(browser, 1920, 1080)
    assert browser.click_control(r"添加(?:第一条)?订阅$"), "配置管理应提供添加订阅"
    choose_deepseek(browser)
    type_into(browser, ACCOUNT_NAME, NAME)
    type_into(browser, SECRET, ORIGINAL_KEY)
    for width, height in [(1920, 1080), (1080, 1920), (390, 844)]:
        resize(browser, width, height)
        assert observe(browser, f"""headings.includes('添加订阅') &&
            shown('main select').some(select => select.selectedOptions[0]?.text.trim() === 'DeepSeek') &&
            shown({json.dumps(ACCOUNT_NAME)}).some(input => input.value === {json.dumps(NAME)}) &&
            shown({json.dumps(SECRET)}).some(input => input.value === {json.dumps(ORIGINAL_KEY)})"""), (
            f"{width}×{height} 下应保留 DeepSeek 选择、名称和未提交密钥"
        )
        assert observe(browser, "document.documentElement.scrollWidth <= innerWidth"), "密钥表单不应横向溢出"
    assert not observe(browser, """shown('main label, main option, main legend')
        .some(el => /账号用途|账号分类|工作号|个人号/.test(el.innerText.trim()))"""), (
        "DeepSeek 表单不应询问账号用途分类"
    )
