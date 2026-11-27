"""01 票，Spec 0.3.0：AC-10，复用 AC-02、AC-05 和 AC-09。"""

import json
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from deepseek_source import DeepSeekSource, Request
from google_source import AUTH_URL, EMAIL, RESET_SHORT, RESET_WEEK, TOKEN_PATH, USERINFO_PATH, GoogleSource, serve_google
from test_deepseek_balance import USD, Browser, money, open_chrome, poll, run_dashboard, success_time
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, open_entry, sign_in, wait_for
from test_subscription_codex_device_login import fill_metadata, main_matches, visible_main
from test_subscription_deepseek_account import (
    ACCOUNT_NAME, CARD, SAVED_TIME, card_text, edit_account, resize, settings, type_into, view,
)


NAME = "松云家庭订阅"
PAYMENT = {"plan": "家庭年度套餐", "amount": "149.99", "currency": "USD", "cycle": "每年", "renewal": "2030-10-19"}
CONNECTED = r"已连接|连接成功|授权成功"
ITEMS = ("Gemini Apps", "Antigravity", "Google One AI 积分")
GROUPS = ("Gemini Models", "Contract Other Models")
SHORT = r"5\s*(?:小时|h\b)|五小时"
WEEKLY = r"每周|周(?:窗口|额度|限额)|7\s*(?:天|d\b)|weekly"
GOOGLE_OPTION = r"^Google\s*AI(?:\s*[（(]Google One[）)])?$"
AUTH_LINK = f"""shown('main a[href]').find(link =>
    link.href.startsWith({json.dumps(AUTH_URL + "?")}) && !link.matches('[aria-disabled=true]'))"""
CODE_FIELD = """shown('main input:not(:disabled):not([readonly]), main textarea:not(:disabled):not([readonly])')
    .find(field => /授权码|验证码|回调|authorization code|callback/i.test([
        ...[...(field.labels ?? [])].map(label => label.innerText), field.placeholder, field.getAttribute('aria-label'),
    ].join(' ')))"""


@pytest.fixture
def dashboard(tmp_path: Path) -> Iterator[tuple[Browser, str, GoogleSource]]:
    unavailable = "http://127.0.0.1:1"
    with (
        serve_google(tmp_path) as source,
        run_dashboard(
            DeepSeekSource(unavailable), tmp_path,
            QUOTA_STATE_FILE=str(tmp_path / "state.json"), QUOTA_ADMIN_PASSWORD=ADMIN_PASSWORD,
            DEEPSEEK_API_KEY="", MIMO_API_KEY="", QUOTA_CODEX_ACCESS_TOKEN="",
            QUOTA_MIMO_BASE_URL=unavailable, QUOTA_CODEX_BASE_URL=unavailable,
            QUOTA_CODEX_AUTH_BASE_URL=unavailable, CODEX_HOME=str(tmp_path / "codex-home"),
            **source.environment(),
        ) as origin,
        open_chrome(tmp_path) as browser,
    ):
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        yield browser, origin, source


def add_google(browser: Browser) -> None:
    assert not observe(browser, "accountCards.length"), "首次登录应从空订阅总览开始"
    assert browser.click_control(r"^添加(?:第一条)?订阅$"), "总览应提供添加订阅入口"
    selector = "shown('main select:not(:disabled)').find(select => select.labels?.length && " \
               "[...select.labels].some(label => label.innerText.includes('平台')))"
    assert wait_for(browser, f"headings.includes('添加订阅') && Boolean({selector})"), (
        f"应打开带平台选择器的添加订阅表单，实得 {visible_main(browser)!r}"
    )
    options = observe(browser, f"""[...({selector}).options]
        .filter(option => !option.disabled).map(option => option.text.trim())""")
    assert any(re.fullmatch(GOOGLE_OPTION, name) for name in options), (
        f"AC-10 添加订阅应可选择 Google AI（Google One），实际可选平台 {options!r}"
    )
    assert observe(browser, f"""(() => {{
        const select = {selector};
        const option = [...select.options].find(option =>
            !option.disabled && new RegExp({json.dumps(GOOGLE_OPTION)}).test(option.text.trim()));
        select.value = option.value;
        select.dispatchEvent(new Event('input', {{bubbles: true}}));
        select.dispatchEvent(new Event('change', {{bubbles: true}}));
        return select.value === option.value;
    }})()"""), "Google AI 平台选项应可选中"
    type_into(browser, ACCOUNT_NAME, NAME)
    fill_metadata(browser, PAYMENT)
    assert browser.click_control(r"^(?:保存并(?:去|开始)?授权|连接 Google(?: AI)?|保存)$"), "应可保存账号并进入官方授权"


def connect_google(browser: Browser, source: GoogleSource) -> None:
    start = """shown('main button, main [role=button]').some(button =>
        /^(开始|发起|去)(Google )?授权$/.test(button.innerText.trim()))"""
    assert wait_for(browser, f"Boolean({AUTH_LINK}) || ({start})"), (
        f"保存后应可开始 Google 官方授权，实得 {visible_main(browser)!r}"
    )
    if not observe(browser, f"Boolean({AUTH_LINK})"):
        assert browser.click_control(r"^(?:开始|发起|去)(?:Google )?授权$"), "应可发起 Google 官方授权"
    assert wait_for(browser, f"Boolean({AUTH_LINK})"), f"应提供 Google 官方授权链接，实得 {visible_main(browser)!r}"
    assert NAME in visible_main(browser), "官方授权前应让管理员辨认正在连接的订阅"
    assert not source.cli_calls(), "获得所选账号授权前不得执行额度查询"
    callback = source.approve(observe(browser, f"({AUTH_LINK}).href"))
    assert wait_for(browser, f"Boolean({CODE_FIELD})"), f"应提供授权码或回调地址输入框，实得 {visible_main(browser)!r}"
    use_code = observe(browser, f"""(() => {{
        const field = {CODE_FIELD};
        const labels = [...(field.labels ?? [])].map(label => label.innerText).join(' ');
        return !/回调|callback/i.test([labels, field.placeholder, field.getAttribute('aria-label')].join(' '));
    }})()""")
    value = parse_qs(urlsplit(callback).query)["code"][0] if use_code else callback
    assert observe(browser, f"(() => {{ const field = {CODE_FIELD}; field.focus(); return document.activeElement === field; }})()"), (
        "Google 授权结果输入框应可输入"
    )
    browser.call("Input.insertText", text=value)
    assert browser.click_control(r"^(?:提交(?:授权码|回调地址)?|完成(?:授权|连接)|确认(?:授权|连接)|验证并连接)$"), (
        "应可提交 Google 授权结果"
    )
    assert wait_for(browser, main_matches(CONNECTED)), (
        f"授权成功后应显示已连接，上游 {source.requests()!r}，页面 {visible_main(browser)!r}"
    )
    requests = source.requests()
    exchange = Request("POST", TOKEN_PATH, True, identity=EMAIL)
    identity = Request("GET", USERINFO_PATH, True, identity=EMAIL)
    assert exchange in requests and identity in requests and requests.index(exchange) < requests.index(identity), (
        f"服务端应按 PKCE 交换令牌并以该令牌核对 Google 邮箱，实得 {requests!r}"
    )


def named_sections(text: str, names: tuple[str, ...]) -> dict[str, str]:
    pieces = re.split("(" + "|".join(re.escape(name) for name in names) + ")", text)
    labels = pieces[1::2]
    assert sorted(labels) == sorted(names), f"这些项目应各出现一次 {names!r}，实得 {text!r}"
    return dict(zip(labels, pieces[2::2], strict=True))


def quota_windows(text: str, *expected: str) -> dict[str, str]:
    labels = list(re.finditer(f"(?P<short>{SHORT})|(?P<weekly>{WEEKLY})", text, re.IGNORECASE))
    assert sorted(label.lastgroup or "" for label in labels) == sorted(expected), (
        f"应按模型组各显示一次官方窗口 {expected!r}，实得 {text!r}"
    )
    ends = [label.start() for label in labels[1:]] + [len(text)]
    return {label.lastgroup or "": text[label.start():end] for label, end in zip(labels, ends, strict=True)}


def expect_window(browser: Browser, text: str, percent: int, reset: str) -> None:
    # 允许页面按浏览器时区显示，或保留官方回执的 UTC 时间。
    local, utc = observe(browser, f"""(() => {{
        const time = new Date({json.dumps(reset)});
        return [{{year: time.getFullYear(), month: time.getMonth() + 1, day: time.getDate(),
            hour: time.getHours(), minute: time.getMinutes()}},
            {{year: time.getUTCFullYear(), month: time.getUTCMonth() + 1, day: time.getUTCDate(),
            hour: time.getUTCHours(), minute: time.getUTCMinutes()}}];
    }})()""")
    assert re.search(rf"(?<![\d.]){percent}(?:\.0+)?\s*[%％]", text), f"窗口应显示剩余 {percent}%，实得 {text!r}"
    matches = []
    for parts in (local, utc):
        year, month, day, hour, minute = (parts[key] for key in ("year", "month", "day", "hour", "minute"))
        date = rf"(?<![\d/\.年-])(?:{year}\s*[-/.年]\s*)?0?{month}\s*[-/.月]\s*0?{day}(?!\d)日?"
        clock = rf"(?<!\d)0?{hour}[:：]{minute:02}(?!\d)"
        matches.append(re.search(rf"{date}[T\s]+{clock}", text))
    assert "重置" in text and any(matches), (
        f"窗口应显示官方重置时刻 {reset}，实得 {text!r}"
    )


def official_links(browser: Browser) -> set[tuple[str, str]]:
    links = observe(browser, f"""shown({json.dumps(CARD)}).flatMap(card => shown('a[href]', card)
        .filter(link => !link.matches('[aria-disabled=true]'))
        .map(link => new URL(link.href)).filter(url => url.protocol === 'https:')
        .map(url => [url.hostname, url.pathname.replace(/\\/$/, '')]))""")
    return {(host, path) for host, path in links}


def expect_collapsed_card(browser: Browser, text: str) -> None:
    assert observe(browser, f"shown({json.dumps(CARD)}).length === 1"), "单账号应恰好显示一张订阅卡"
    assert re.search(CONNECTED, text), f"该账号卡应显示连接状态，实得 {text!r}"
    assert PAYMENT["plan"] not in text and PAYMENT["amount"] not in text, "付费详情应在卡片展开后显示"
    sections = named_sections(text, ITEMS)
    gemini, agy, credits = (sections[name] for name in ITEMS)
    assert "请到 Gemini 查看" in gemini and re.search(r"设置\s*→\s*使用限额", gemini), (
        f"Gemini Apps 应提供官方查看指引，实得 {gemini!r}"
    )
    assert not (success_time(gemini) or SAVED_TIME.search(gemini)), (
        f"待人工查看的 Gemini Apps 没有成功取数时间，实得 {gemini!r}"
    )
    links = official_links(browser)
    assert ("gemini.google.com", "") in links, f"卡片应提供可见的 Gemini 官方入口，实得 {links!r}"
    assert ("one.google.com", "/ai/activity") in links, f"卡片应提供 Google One 活动页核对入口，实得 {links!r}"

    groups = named_sections(agy, GROUPS)
    gemini_windows = quota_windows(groups[GROUPS[0]], "short", "weekly")
    expect_window(browser, gemini_windows["short"], 63, RESET_SHORT)
    expect_window(browser, gemini_windows["weekly"], 27, RESET_WEEK)
    other_windows = quota_windows(groups[GROUPS[1]], "short")
    expect_window(browser, other_windows["short"], 84, RESET_SHORT)

    amount = r"(?<![\d,.])1[,，]?347(?![\d,.])"
    assert len(re.findall(amount, text)) == 1 and re.search(
        rf"{amount}\s*积分|积分\s*[:：]?\s*{amount}", "积分" + credits,
    ), (
        f"同账号返回的家庭共享余额应以积分显示一次，实得 {credits!r}"
    )
    for name, section in ((ITEMS[1], agy), (ITEMS[2], credits)):
        assert success_time(section) or SAVED_TIME.search(section), (
            f"{name} 应显示本项最近成功时间，实得 {section!r}"
        )


def expect_readable(browser: Browser, width: int, height: int) -> None:
    assert observe(browser, "document.documentElement.scrollWidth <= innerWidth && document.body.scrollWidth <= innerWidth"), (
        f"{width} × {height} 的总览不能横向滚动"
    )
    clipped = observe(browser, f"""(() => {{
        const card = shown({json.dumps(CARD)})[0];
        const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
        const range = document.createRange();
        const clipped = [];
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {{
            const text = node.textContent.trim();
            if (!text || getComputedStyle(node.parentElement).visibility !== 'visible') continue;
            range.selectNodeContents(node);
            const rects = [...range.getClientRects()];
            if (rects.some(rect => rect.width > 0 && (rect.left < -1 || rect.right > innerWidth + 1))) clipped.push(text);
        }}
        return clipped;
    }})()""")
    assert not clipped, f"{width} × {height} 下应可逐项完整阅读，横向溢出文字 {clipped!r}"


def test_ac10_single_google_account_shows_three_quota_items(
    dashboard: tuple[Browser, str, GoogleSource],
) -> None:
    browser, origin, source = dashboard
    add_google(browser)
    connect_google(browser, source)
    view(browser, OVERVIEW)
    card = poll(
        lambda: card_text(browser, NAME),
        lambda text: bool(re.search(r"84(?:\.0+)?\s*[%％]", text) and re.search(r"1[,，]?347", text)),
        timeout=12,
    )
    assert card, f"总览应出现自命名账号 {NAME!r}，实得 {visible_main(browser)!r}"
    calls = source.cli_calls()
    assert all((EMAIL, ["-p", command, "--output-format", "json"]) in calls for command in ("/usage", "/credits")), (
        f"两个查询都应使用已核对的 Google 身份，实得 {calls!r}"
    )
    assert all(identity == EMAIL for identity, _ in calls), f"额度查询不得落到其他连接，实得 {calls!r}"

    for width, height in ((1920, 1080), (1080, 1920), (390, 844)):
        resize(browser, width, height)
        expect_collapsed_card(browser, card_text(browser, NAME))
        expect_readable(browser, width, height)

    assert browser.click_control(r"查看明细|付费(?:详情|信息)|展开"), "订阅卡应可展开付费信息"
    expanded = card_text(browser, NAME)
    assert all(PAYMENT[key] in expanded for key in ("plan", "cycle", "renewal")), (
        f"展开后应回读套餐、周期和下次续费日，实得 {expanded!r}"
    )
    assert money(USD, r"149\.99").search(expanded), f"展开后应回读 USD 149.99，实得 {expanded!r}"

    open_entry(browser, settings(origin))
    assert wait_for(browser, f"headings.includes({json.dumps(SETTINGS)}) && {main_matches(re.escape(NAME))}"), (
        f"重开配置管理应显示自定义账号名称，实得 {visible_main(browser)!r}"
    )
    assert re.search(CONNECTED, visible_main(browser)), "重开配置管理应保留已连接状态"
    edit_account(browser, NAME)
    for field, value in PAYMENT.items():
        match = f"Number(element.value) === {value}" if field == "amount" else f"element.value === {json.dumps(value)}"
        assert observe(browser, f"shown('main form [name={field}]').some(element => {match})"), (
            f"编辑表单应回读 {field}={value!r}，实得 {visible_main(browser)!r}"
        )
