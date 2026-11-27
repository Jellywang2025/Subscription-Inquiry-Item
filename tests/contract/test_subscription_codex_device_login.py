"""Ticket 04 / Spec 0.2.0: single-account AC-02, AC-05, AC-06 and authorization AC-09."""

import base64
import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from codex_device_source import (
    EXCHANGE_PATH, FIRST, POLL_PATH, RESET_AT, SECOND, THIRD, USAGE_BASE_PATH, USAGE_PATH,
    USERCODE_PATH, VERIFICATION_URL, Attempt, CodexDeviceSource, serve_codex_device,
)
from deepseek_source import DeepSeekSource, Request
from test_codex_windows import has_percent
from test_deepseek_balance import Browser, money, open_chrome, poll, run_dashboard, success_time
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for
from test_subscription_deepseek_account import (
    ACCOUNT_NAME, CARD, SAVED_TIME, card_text, edit_account, resize, settings, submit, type_into, view,
)


NAME = "云杉研发额度"
RENAMED = "松涛研发额度"
INITIAL = {"plan": "ChatGPT Pro", "amount": "200", "currency": "USD", "cycle": "每月", "renewal": "2030-01-19"}
EDITED = {"plan": "研发年度套餐", "amount": "328", "currency": "CNY", "cycle": "每年", "renewal": "2030-02-20"}
WAITING = r"等待[^\n]{0,25}(?:授权|确认)|授权(?:进行)?中"
EXPIRY = r"有效期[^\n]{0,24}\d|(?:剩余|过期于|到期时间)[^\n]{0,24}\d"
EXPIRED = r"(?:设备码|授权)[^\n]{0,15}(?:已过期|已失效|超时)"
RETRY = r"重新(?:获取|生成)(?:一次性)?设备码|重新获取|重试"
CONNECTED = r"已连接|连接成功|授权成功"
OFFICIAL = f"shown('main a[href]').find(link => link.href === {json.dumps(VERIFICATION_URL)})"


@pytest.fixture
def dashboard(tmp_path: Path) -> Iterator[tuple[Browser, str, CodexDeviceSource]]:
    unavailable = "http://127.0.0.1:1"
    with (
        serve_codex_device() as source,
        run_dashboard(
            DeepSeekSource(unavailable), tmp_path,
            QUOTA_STATE_FILE=str(tmp_path / "state.json"), QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            QUOTA_CODEX_AUTH_BASE_URL=source.origin, QUOTA_CODEX_BASE_URL=source.origin + USAGE_BASE_PATH,
            QUOTA_CODEX_ACCESS_TOKEN="", DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_MIMO_BASE_URL=unavailable,
            CODEX_HOME=str(tmp_path / "codex-home"),
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        yield browser, origin, source


def accounts(browser: Browser) -> list[dict[str, Any]]:
    result = browser.call("Runtime.evaluate", expression="""(async () => {
        const response = await fetch('/api/accounts', {cache: 'no-store'});
        return {status: response.status, body: await response.json()};
    })()""", awaitPromise=True, returnByValue=True)
    assert "exceptionDetails" not in result, "浏览器应能读取 GET /api/accounts"
    response = result["result"]["value"]
    assert response["status"] == 200, f"GET /api/accounts 实得 HTTP {response['status']}"
    rows = response["body"].get("accounts")
    assert isinstance(rows, list), "GET /api/accounts 应返回账号列表"
    return rows


def set_field(browser: Browser, field: str, value: str) -> None:
    assert observe(browser, f"""(() => {{
        const field = shown('main form [name={field}]:not(:disabled)')[0];
        if (!field) return false;
        field.value = {json.dumps(value)};
        field.dispatchEvent(new Event('input', {{bubbles: true}}));
        field.dispatchEvent(new Event('change', {{bubbles: true}}));
        return field.value === {json.dumps(value)};
    }})()"""), f"表单应可填写 {field}={value!r}"


def fill_metadata(browser: Browser, metadata: dict[str, str]) -> None:
    type_into(browser, 'main form input[name="plan"]', metadata["plan"])
    for field in ("amount", "currency", "cycle", "renewal"):
        set_field(browser, field, metadata[field])


def assert_metadata(row: dict[str, Any], expected: dict[str, str]) -> None:
    for field, value in expected.items():
        actual = str(row.get(field))
        matches = re.fullmatch(rf"{re.escape(value)}(?:\.0+)?", actual) if field == "amount" else actual == value
        assert matches, f"应回读 {field}={value!r}，实得 {actual!r}"


def main_matches(pattern: str) -> str:
    return f"new RegExp({json.dumps(pattern)}).test(document.querySelector('main')?.innerText ?? '')"


def visible_main(browser: Browser) -> str:
    return str(observe(browser, "document.querySelector('main')?.innerText ?? ''"))


def assert_auth_route(browser: Browser, name: str, account_id: str) -> None:
    query = parse_qs(urlsplit(observe(browser, "location.href")).query)
    assert query.get("view") == ["auth"] and query.get("id") == [account_id], (
        f"应进入所选 Codex 账号的授权页，实得路由 {query!r}"
    )
    assert wait_for(browser, main_matches(re.escape(name))), (
        f"授权页应显示所选账号 {name!r}，实得 {visible_main(browser)!r}"
    )


def add_codex(browser: Browser, *, with_payment: bool = True) -> str:
    assert browser.click_control(r"添加(?:第一条)?订阅$"), "应可从总览打开「添加订阅」"
    assert wait_for(browser, "headings.includes('添加订阅')"), "应显示添加订阅表单"
    set_field(browser, "provider", "codex")
    type_into(browser, ACCOUNT_NAME, NAME)
    assert not observe(browser, """shown('main form label, main form legend, main form option')
        .some(element => /用途|分类|个人号|工作号/.test(element.innerText))"""), "无需选择账号用途分类"
    assert not observe(browser, "shown('main form input[type=password], main form [name=key]').length"), (
        "Codex 应使用设备码连接，表单无需输入密钥"
    )
    if with_payment:
        fill_metadata(browser, INITIAL)
    assert browser.click_control(r"^保存并去授权$"), "Codex 表单应提供「保存并去授权」"
    assert wait_for(browser, """!shown('main form button[type=submit]').length ||
        (shown('main form button[type=submit]:not(:disabled)').length &&
         shown('main form [role=status], main form [role=alert]').some(el => el.innerText.trim()))"""), (
        f"保存应返回可观察结果，实得 {visible_main(browser)!r}"
    )
    status = observe(browser, """performance.getEntriesByType('resource')
        .filter(entry => new URL(entry.name).pathname === '/api/accounts').at(-1)?.responseStatus""")
    rows = accounts(browser)
    assert [(row.get("provider"), row.get("name")) for row in rows] == [("codex", NAME)], (
        f"应可无密钥保存一个自命名 Codex 账号。保存 HTTP {status!r}，GET /api/accounts 回读 {len(rows)} 条，"
        f"页面 {visible_main(browser)!r}"
    )
    if with_payment:
        assert_metadata(rows[0], INITIAL)
    account_id = rows[0].get("id")
    assert isinstance(account_id, str) and account_id, "账号应有可回读的标识"
    assert_auth_route(browser, NAME, account_id)
    return account_id


def expect_device(browser: Browser, source: CodexDeviceSource, attempt: Attempt, *, start: bool = False) -> None:
    if start:
        assert browser.click_when_ready(r"^(?:开始授权|获取设备码|生成设备码|开始连接)$"), (
            "授权操作按钮应出现并可点击"
        )
    assert source.received(Request("POST", USERCODE_PATH, True, identity=attempt.user_code)), (
        f"服务端应请求设备码 {attempt.user_code}，实得上游请求 {source.snapshot()!r}，页面 {visible_main(browser)!r}"
    )
    for pattern, label in ((re.escape(attempt.user_code), "当前一次性设备码"), (EXPIRY, "有效期"), (WAITING, "等待进度")):
        assert wait_for(browser, main_matches(pattern)), f"授权页应显示{label}，实得 {visible_main(browser)!r}"
    assert observe(browser, f"Boolean({OFFICIAL})"), f"应有指向 {VERIFICATION_URL} 的可见链接"
    assert source.received(Request("POST", POLL_PATH, True, identity=attempt.user_code)), (
        f"服务端应按当前设备码轮询，实得 {source.snapshot()!r}"
    )
    assert not observe(browser, main_matches(CONNECTED)), "上游等待授权时授权页尚不能显示已连接"
    if attempt != THIRD:
        assert accounts(browser)[0].get("connection") != "connected", "首次授权成功前账号应保持未连接"


def copy_code(browser: Browser, origin: str, attempt: Attempt) -> None:
    browser.call("Browser.grantPermissions", origin=origin.rstrip("/"),
                 permissions=["clipboardReadWrite", "clipboardSanitizedWrite"])
    assert browser.click_control(r"^复制(?:设备码)?$"), "应提供可操作的复制按钮"

    def clipboard_text() -> str:
        result = browser.call("Runtime.evaluate", expression="navigator.clipboard.readText()",
                              awaitPromise=True, returnByValue=True)
        assert "exceptionDetails" not in result, "Chrome 无法读回测试剪贴板"
        return str(result["result"].get("value", ""))

    actual = poll(clipboard_text, lambda value: value == attempt.user_code)
    assert actual == attempt.user_code, f"复制应写入当前设备码 {attempt.user_code!r}，实得 {actual!r}"


def finish_authorization(browser: Browser, source: CodexDeviceSource, attempt: Attempt) -> None:
    source.resolve(attempt, "approved")
    assert browser.click_when_ready(r"^(?:我已完成授权|检查授权状态)$"), "完成授权按钮应出现并可点击"
    assert source.received(Request("POST", EXCHANGE_PATH, True, identity=attempt.user_code)), (
        f"收到轮询成功后服务端应交换令牌，实得 {source.snapshot()!r}"
    )
    assert wait_for(browser, main_matches(CONNECTED)), f"服务端确认授权后页面应显示已连接，实得 {visible_main(browser)!r}"
    assert accounts(browser)[0].get("connection") == "connected", "页面显示授权成功后账号 API 应回读已连接"


def expect_usage(browser: Browser, source: CodexDeviceSource, name: str, attempt: Attempt) -> None:
    percent = 60 if attempt == THIRD else 75
    short = re.compile(r"短(?:额度)?窗口|短周期|短期|5\s*(?:小时|h\b)", re.IGNORECASE)
    long = re.compile(r"长(?:额度)?窗口|长周期|长期|7\s*(?:天|d\b)|周(?:窗口|额度|限额)", re.IGNORECASE)
    local_reset = datetime.fromtimestamp(RESET_AT)
    clock = re.compile(rf"(?<!\d)0?{local_reset.hour}[:：]{local_reset.minute:02}(?!\d)")

    def check_windows(text: str) -> bool:
        start_short, start_long = short.search(text), long.search(text)
        if not start_short or not start_long:
            return False
        a, b = start_short.start(), start_long.start()
        first = text[a:b] if a < b else text[a:]
        second = text[b:] if a < b else text[b:a]
        return bool(has_percent(first, percent) and "重置" in first and clock.search(first)
                    and has_percent(second, 30) and "重置" in second and "未提供" in second)

    assert source.received(Request("GET", USAGE_PATH, True, identity=attempt.user_code)), (
        f"服务端应以本账号授权取得的令牌查询额度，实得 {source.snapshot()!r}"
    )
    card = poll(lambda: card_text(browser, name), check_windows)
    assert check_windows(card), (
        f"{name!r} 应显示短窗口 {percent}% 及 {local_reset:%H:%M} 重置、长窗口 30% 且重置时间「未提供」，"
        f"实得 {card!r}"
    )
    assert re.search(CONNECTED, card), f"额度卡应显示已连接，实得 {card!r}"
    assert success_time(card) or SAVED_TIME.search(card), f"额度卡应显示成功更新时间，实得 {card!r}"
    assert observe(browser, f"shown({json.dumps(CARD)}).length === 1"), "单账号应恰好显示一张卡"


def refresh_now(browser: Browser) -> None:
    assert browser.click_control(r"立即刷新|^刷新$"), "总览应可立即刷新"
    assert wait_for(browser, """shown('main button:not(:disabled)').some(
        button => /立即刷新|^刷新$/.test(button.innerText.trim()))"""), "立即刷新应执行完毕"


def test_ac02_ac05_connect_one_codex_edit_and_delete(dashboard: tuple[Browser, str, CodexDeviceSource]) -> None:
    browser, origin, source = dashboard
    account_id = add_codex(browser)
    expect_device(browser, source, FIRST, start=True)
    copy_code(browser, origin, FIRST)
    finish_authorization(browser, source, FIRST)
    view(browser, OVERVIEW)
    expect_usage(browser, source, NAME, FIRST)
    assert browser.click_control(r"明细|详情|展开", in_card=True, platform="Codex"), "Codex 卡应可展开付费信息"
    card = card_text(browser, NAME)
    assert all(INITIAL[field] in card for field in ("plan", "cycle", "renewal")), f"套餐、周期和续费日应可见，实得 {card!r}"
    assert money(r"(?:USD|\$)", r"200(?:\.00)?").search(card), f"费用应显示 USD 200，实得 {card!r}"

    open_entry(browser, settings(origin))
    edit_account(browser, NAME)
    for field, value in INITIAL.items():
        assert observe(browser, f"""shown('main form [name={field}]').some(el =>
            {f'Number(el.value) === {value}' if field == 'amount' else f'el.value === {json.dumps(value)}'})"""), (
            f"重开编辑页应回读 {field}={value!r}"
        )
    before_edit = len(source.snapshot())
    type_into(browser, ACCOUNT_NAME, RENAMED)
    fill_metadata(browser, EDITED)
    feedback = submit(browser)
    rows = accounts(browser)
    assert [(row.get("id"), row.get("name"), row.get("connection")) for row in rows] == [(account_id, RENAMED, "connected")], (
        f"编辑应保留账号标识与已授权连接，实得反馈 {feedback!r}"
    )
    assert_metadata(rows[0], EDITED)
    open_entry(browser, settings(origin))
    assert wait_for(browser, main_matches(re.escape(RENAMED))), "重开配置列表应显示改名后的账号"
    assert_metadata(accounts(browser)[0], EDITED)
    view(browser, OVERVIEW)
    before_refresh = len(source.snapshot())
    refresh_now(browser)
    assert source.received(Request("GET", USAGE_PATH, True, identity=FIRST.user_code), after=before_refresh), (
        "编辑名称及付费字段后立即刷新应继续使用原授权令牌"
    )
    assert all(item.path not in {USERCODE_PATH, EXCHANGE_PATH} for item in source.snapshot()[before_edit:]), (
        "编辑与刷新不应重新发起设备码或令牌交换"
    )
    expect_usage(browser, source, RENAMED, FIRST)
    assert not card_text(browser, NAME), "改名后应移除旧名称的卡片"

    view(browser, SETTINGS)
    dialog = f"""shown('dialog[open], [role=dialog]').some(el =>
        el.innerText.includes('Codex') && el.innerText.includes({json.dumps(RENAMED)}))"""
    assert browser.click_control(r"^删除$") and wait_for(browser, dialog), "删除确认应写明 Codex 和账号名"
    assert browser.click_control(r"^取消$"), "确认框应可取消"
    assert wait_for(browser, "!shown('dialog[open], [role=dialog]').length"), "取消应关闭确认框"
    open_entry(browser, settings(origin))
    assert wait_for(browser, main_matches(re.escape(RENAMED))), "取消删除后重开应保留该账号"
    retained = accounts(browser)
    assert [(row.get("id"), row.get("name"), row.get("connection")) for row in retained] == [
        (account_id, RENAMED, "connected"),
    ], "取消删除后 API 应保留原账号及连接"
    assert_metadata(retained[0], EDITED)
    assert browser.click_control(r"^删除$") and wait_for(browser, dialog), "应可再次打开删除确认"
    assert browser.click_control(r"^确认删除$"), "应可确认删除"
    assert wait_for(browser, f"!({main_matches(re.escape(RENAMED))})"), "删除完成后配置列表应移除此账号"
    open_entry(browser, settings(origin))
    assert accounts(browser) == [], "确认删除后重开不能回读已删除记录"
    open_entry(browser, f"{origin}?view=auth&id={account_id}")
    assert wait_for(browser, main_matches(r"找不到这条订阅|账号不存在|已删除")), "原授权地址应无法再操作已删除账号"
    view(browser, OVERVIEW)
    before_delete_refresh = len(source.snapshot())
    refresh_now(browser)
    assert source.snapshot()[before_delete_refresh:] == (), "删除后刷新应跳过该 Codex 账号"
    assert not observe(browser, f"shown({json.dumps(CARD)}).length"), "删除后总览不应出现已删除账号的额度缓存"


def expire_device(browser: Browser, source: CodexDeviceSource) -> None:
    source.resolve(FIRST, "expired")
    assert wait_for(browser, main_matches(EXPIRED)), f"上游设备码过期后应提示过期，实得 {visible_main(browser)!r}"


def test_ac06_expired_device_code_then_expired_login_can_reauthorize(
    dashboard: tuple[Browser, str, CodexDeviceSource],
) -> None:
    browser, _, source = dashboard
    account_id = add_codex(browser, with_payment=False)
    expect_device(browser, source, FIRST, start=True)
    expire_device(browser, source)
    assert browser.click_control(RETRY), "设备码过期后应可在原页重新获取"
    expect_device(browser, source, SECOND)
    assert not observe(browser, main_matches(re.escape(FIRST.user_code))), "新设备码应替换过期码"
    finish_authorization(browser, source, SECOND)
    view(browser, OVERVIEW)
    expect_usage(browser, source, NAME, SECOND)

    source.revoke(SECOND)
    before = len(source.snapshot())
    refresh_now(browser)
    assert source.received(Request("GET", USAGE_PATH, False, identity=SECOND.user_code), after=before), (
        "上游应拒绝已失效的原登录令牌"
    )
    view(browser, SETTINGS)
    assert wait_for(browser, main_matches(r"(?:登录|授权|连接)[^\n]{0,12}失效|待(?:重新)?授权|待连接|需(?:重新)?授权")), (
        f"配置页应提示此账号需要重新授权，实得 {visible_main(browser)!r}"
    )
    assert browser.click_control(r"^(?:重新授权|去授权)$"), "应能从同一账号重新发起授权"
    assert_auth_route(browser, NAME, account_id)
    expect_device(browser, source, THIRD, start=True)
    finish_authorization(browser, source, THIRD)
    view(browser, OVERVIEW)
    expect_usage(browser, source, NAME, THIRD)


def test_ac09_codex_authorization_at_approved_and_narrow_viewports(
    dashboard: tuple[Browser, str, CodexDeviceSource], tmp_path: Path,
) -> None:
    browser, _, source = dashboard
    account_id = add_codex(browser, with_payment=False)
    expect_device(browser, source, FIRST, start=True)
    sizes = [(1920, 1080), (1080, 1920), (390, 844)]

    for state in ("waiting", "expired"):
        if state == "expired":
            expire_device(browser, source)
        url = observe(browser, "location.href")
        for width, height in sizes:
            resize(browser, width, height)
            assert_auth_route(browser, NAME, account_id)
            assert observe(browser, "location.href") == url, "切换视口应保持当前授权页"
            required = [re.escape(NAME), WAITING, EXPIRY, re.escape(FIRST.user_code)] if state == "waiting" else [
                re.escape(NAME), EXPIRED, RETRY,
            ]
            assert observe(browser, f"""(() => {{
                const fits = element => [...element.getClientRects()].some(rect =>
                    rect.width > 0 && rect.height > 0 && rect.left >= 0 && rect.right <= innerWidth);
                const fields = shown('main *').filter(element => fits(element) &&
                    ![...element.children].some(child => child.innerText?.trim() === element.innerText?.trim()));
                return document.documentElement.scrollWidth <= innerWidth &&
                    {json.dumps(required)}.every(pattern => fields.some(element =>
                        new RegExp(pattern).test(element.innerText || ''))) &&
                    ({json.dumps(state)} === 'waiting' ? Boolean({OFFICIAL} && fits({OFFICIAL})) :
                        shown('main button:not(:disabled), main a[href]').some(control =>
                            fits(control) && new RegExp({json.dumps(RETRY)}).test(control.innerText)));
            }})()"""), f"{width}×{height} 的 {state} 授权页关键内容应可见且无横向溢出，实得 {visible_main(browser)!r}"
            image = browser.call("Page.captureScreenshot", format="png", fromSurface=True)
            (tmp_path / f"codex-{state}-{width}x{height}.png").write_bytes(base64.b64decode(image["data"]))

    assert browser.click_control(RETRY), "390×844 下应能点击重取设备码"
    expect_device(browser, source, SECOND)
