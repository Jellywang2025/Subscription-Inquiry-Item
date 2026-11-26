"""AC-02 / AC-06 through the dashboard CLI and its visible browser controls."""

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from deepseek_source import BALANCE_PATH, TEST_KEY, BalanceReply, DeepSeekSource, Request, serve_deepseek
from test_startup_dashboard import ROOT, STARTUP_TIMEOUT, Launch, browser_environment

# 0.2.0 迁移：凭据改为网页配置的账号模型，DeepSeek 端到端契约待 02 票按新架构重建。
pytestmark = pytest.mark.skip(reason="v0.1 DeepSeek 端到端契约待 02 票按 0.2.0 账号配置架构重建")


CHROME = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe"
BRIDGE = Path(__file__).with_name("browser_cdp.cjs")
QUERY_TIMEOUT = 4
EXPECTED_REQUEST = Request("GET", BALANCE_PATH, True)
QUERYING = re.compile(r"查询中|正在查询|刷新中|正在刷新|加载中|正在加载|获取中|正在获取")
TIME_LABEL = re.compile(
    r"(?:成功时间|(?:上次|最后|最近)(?:查询成功|成功查询|成功更新|成功|更新)(?:时间)?|"
    r"更新时间|更新于|查询成功)\s*[:：]?\s*"
    r"(?:\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}[日 T\s]*)?"
    r"(?:上午|下午|凌晨|中午)?\s*(?P<clock>\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)"
)


def card_lookup(platform: str) -> str:
    return f"""
    const heading = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,[role=heading]')]
        .find(element => element.innerText.trim() === {json.dumps(platform)});
    const card = heading?.closest('section, article, [role=region]') ?? heading?.parentElement;
    """


@dataclass
class Browser:
    bridge: subprocess.Popen[str]

    def call(self, method: str, **params: object) -> dict[str, Any]:
        assert self.bridge.stdin is not None and self.bridge.stdout is not None
        command = json.dumps({"method": method, "params": params})
        self.bridge.stdin.write(command + "\n")
        self.bridge.stdin.flush()
        line = self.bridge.stdout.readline()
        if not line:
            raise RuntimeError(f"Chrome CDP bridge exited while calling {method}")
        message = json.loads(line)
        if "error" in message:
            raise RuntimeError(f"Chrome CDP {method}: {message['error']}")
        return message["result"]

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if "exceptionDetails" in result:
            raise RuntimeError(f"Browser evaluation failed: {result['exceptionDetails']}")
        return result["result"].get("value")

    def open(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        title = poll(lambda: self.evaluate("document.title"), lambda value: value == "个人订阅额度看板")
        if title != "个人订阅额度看板":
            pytest.fail(f"浏览器未打开看板 {url}，实得页面标题 {title!r}")

    def card(self, platform: str = "DeepSeek") -> str:
        return self.evaluate(f"""(() => {{
            {card_lookup(platform)}
            return card?.getClientRects().length && getComputedStyle(card).visibility === 'visible'
                ? card.innerText : '';
        }})()""")

    def click_control(self, label: str, *, in_card: bool = False, platform: str = "DeepSeek") -> bool:
        root = "card" if in_card else "document"
        point = self.evaluate(f"""(() => {{
            {card_lookup(platform) if in_card else ''}
            const controls = {root}?.querySelectorAll('button, [role=button], summary, a[href]') ?? [];
            const name = new RegExp({json.dumps(label)});
            const control = [...controls].find(element =>
                element.getClientRects().length && getComputedStyle(element).visibility === 'visible'
                && !element.matches(':disabled, [aria-disabled=true]')
                && name.test(element.getAttribute('aria-label') || element.innerText.trim()
                    || element.getAttribute('title') || ''));
            if (!control) return null;
            control.scrollIntoView({{block: 'center'}});
            const box = control.getBoundingClientRect();
            return {{ x: box.x + box.width / 2, y: box.y + box.height / 2 }};
        }})()""")
        if point is None:
            return False
        self.call("Input.dispatchMouseEvent", type="mousePressed", button="left", clickCount=1, **point)
        self.call("Input.dispatchMouseEvent", type="mouseReleased", button="left", clickCount=1, **point)
        return True


def poll(observe: Callable[[], str], matches: Callable[[str], bool], *, timeout: float = QUERY_TIMEOUT) -> str:
    deadline = time.monotonic() + timeout
    last = observe()
    while not matches(last) and time.monotonic() < deadline:
        time.sleep(0.05)
        last = observe()
    return last


def stop(process: subprocess.Popen[Any]) -> None:
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=5)


@contextmanager
def open_chrome(directory: Path) -> Iterator[Browser]:
    node = shutil.which("node")
    if node is None or not CHROME.is_file():
        pytest.fail(f"需要 Chrome ({CHROME}) 及支持原生 WebSocket 的 Node.js 22+，实得 node={node!r}")
    profile = directory / "chrome-profile"
    port_file = profile / "DevToolsActivePort"
    chrome_log = directory / "chrome.log"
    flags = [
        "--headless", "--disable-gpu", "--disable-background-networking", "--disable-default-apps",
        "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", f"--user-data-dir={profile}",
    ]
    if os.environ.get("QUOTA_CONTRACT_CHROME_NO_SANDBOX") == "1":
        # Opt-in for running the loopback fixture in an already sandboxed Windows process.
        flags.append("--no-sandbox")
    with chrome_log.open("wb") as log:
        chrome = subprocess.Popen(
            [str(CHROME), *flags, "about:blank"],
            stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while chrome.poll() is None and time.monotonic() < deadline:
                try:
                    port, newline, _ = port_file.read_text(encoding="utf-8").partition("\n")
                except (FileNotFoundError, PermissionError):
                    # On Windows Chrome may still have the new file open for writing.
                    pass
                else:
                    if newline and port.isdecimal():
                        break
                time.sleep(0.05)
            else:
                pytest.fail(f"无法读取 Chrome 调试端口文件 {port_file}，退出码 {chrome.poll()}，日志 {chrome_log}")
            with subprocess.Popen(
                [node, str(BRIDGE), port], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                encoding="utf-8", bufsize=1,
            ) as bridge:
                browser = Browser(bridge)
                try:
                    yield browser
                finally:
                    try:
                        browser.call("Browser.close")
                    except (RuntimeError, OSError):
                        pass  # Chrome can close its transport before replying.
                    stop(bridge)
                    try:
                        chrome.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        stop(chrome)
        finally:
            stop(chrome)


@contextmanager
def run_dashboard(source: DeepSeekSource, directory: Path, **source_environment: str) -> Iterator[str]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log_path = directory / "dashboard.log"
    browser_log = directory / "browser-open.txt"
    env = browser_environment(browser_log)
    env.update(QUOTA_DEEPSEEK_BASE_URL=source.base_url, DEEPSEEK_API_KEY=TEST_KEY, NO_PROXY="127.0.0.1,localhost")
    env.update(source_environment)
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "quota_dashboard", "--port", str(port)],
            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT
            # A TCP probe avoids loading the page before the real browser opens it.
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                launch = Launch(process, log_path, browser_log)
                pytest.fail(f"看板 CLI 未能监听 {port}，退出码 {process.poll()}\n{launch.diagnostics()}")
            yield f"http://127.0.0.1:{port}/"
        finally:
            stop(process)


def balance(currency: str, total: str, topped_up: str, granted: str) -> dict[str, str]:
    return {"currency": currency, "total_balance": total, "topped_up_balance": topped_up, "granted_balance": granted}


def money(currency: str, amount: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:{currency}\s*(?:可用余额\s*)?[:：]?\s*{amount}(?![\d.])|(?<![\d.]){amount}\s*{currency})",
        re.IGNORECASE,
    )


CNY = r"(?:[¥￥]|CNY|RMB|人民币)"
USD = r"(?:US\$|\$|USD|美元)"
CNY_80 = money(CNY, r"80(?:\.00)?")
CNY_65 = money(CNY, r"65(?:\.00)?")
USD_5 = money(USD, r"5(?:\.00)?")
TOPPED_UP = re.compile(rf"充值(?:余额)?\s*[:：]?\s*(?:{CNY}\s*)?70\.00(?![\d.])", re.IGNORECASE)
GRANTED = re.compile(rf"赠金(?:余额)?\s*[:：]?\s*(?:{CNY}\s*)?10\.00(?![\d.])", re.IGNORECASE)


def success_time(text: str) -> str | None:
    match = TIME_LABEL.search(text)
    return match["clock"].replace("：", ":") if match else None


def wait_for_new_clock_value(before: str | None) -> None:
    if before is not None:
        # The Spec leaves display precision open. Cross the next displayed unit.
        unit = 60 if before.count(":") == 1 else 1
        time.sleep(unit - time.time() % unit + 0.1)


@dataclass
class RefreshObservation:
    before_click: tuple[Request, ...]
    after_click: tuple[Request, ...]
    initial_pending: str
    first: str
    clicked: bool
    refresh_pending: str
    second: str


@pytest.fixture(scope="module")
def refresh(tmp_path_factory: pytest.TempPathFactory) -> RefreshObservation:
    first = BalanceReply([balance("CNY", "80.00", "70.00", "10.00")])
    second = BalanceReply([balance("CNY", "65.00", "55.00", "10.00")])
    directory = tmp_path_factory.mktemp("ac02")
    with serve_deepseek(first, second) as source, run_dashboard(source, directory) as url, open_chrome(directory) as browser:
        browser.open(url)
        arrived = source.wait_for_requests(1, QUERY_TIMEOUT)
        initial_pending = poll(browser.card, lambda text: bool(QUERYING.search(text))) if arrived else browser.card()
        first.release.set()
        first_text = poll(browser.card, lambda text: bool(CNY_80.search(text) and success_time(text))) if arrived else browser.card()
        wait_for_new_clock_value(success_time(first_text))
        before_click = source.wait_for_requests(0, 0)
        clicked = browser.click_control(r"^刷\s*新$")
        after_click = source.wait_for_requests(2, QUERY_TIMEOUT) if clicked else before_click
        refresh_pending = (
            poll(browser.card, lambda text: bool(QUERYING.search(text)))
            if len(after_click) > len(before_click) else browser.card()
        )
        second.release.set()
        second_text = poll(
            browser.card,
            lambda text: bool(CNY_65.search(text) and success_time(text) and success_time(text) != success_time(first_text)),
        ) if len(after_click) >= 2 else browser.card()
        return RefreshObservation(
            before_click, source.wait_for_requests(0, 0), initial_pending, first_text, clicked, refresh_pending, second_text,
        )


def test_ac02_opens_and_shows_available_balance(refresh: RefreshObservation) -> None:
    assert len(refresh.before_click) == 1, f"打开看板应自动查询一次，实得请求 {refresh.before_click!r}，卡片 {refresh.first!r}"
    assert CNY_80.search(refresh.first), f"首次查询后应显示 ¥80，实得卡片 {refresh.first!r}"


def test_ac02_refresh_updates_balance_and_success_time(refresh: RefreshObservation) -> None:
    assert refresh.clicked, f"看板应提供可点击的刷新控件，实得 DeepSeek 卡片 {refresh.first!r}"
    assert len(refresh.after_click) == 2 and len(refresh.before_click) == 1, (
        f"刷新应再查询一次，点击前 {refresh.before_click!r}，点击后 {refresh.after_click!r}"
    )
    assert CNY_80.search(refresh.first) and CNY_65.search(refresh.second), (
        f"刷新应将余额从 ¥80 更新为 ¥65，实得 {refresh.first!r} → {refresh.second!r}"
    )
    before, after = success_time(refresh.first), success_time(refresh.second)
    assert before and after and before != after, (
        f"两次成功时间应可见且刷新后更新，实得 {refresh.first!r} → {refresh.second!r}"
    )


def test_ac02_shows_querying_while_response_is_pending(refresh: RefreshObservation) -> None:
    observations = [
        ("自动查询", bool(refresh.before_click), refresh.initial_pending),
        ("手动刷新", len(refresh.after_click) > len(refresh.before_click), refresh.refresh_pending),
    ]
    missing = [(stage, arrived, text) for stage, arrived, text in observations if not (arrived and QUERYING.search(text))]
    assert not missing, f"模拟响应放行前应收到请求并显示查询中，未满足的阶段、请求是否到达和卡片文本 {missing!r}"


def test_ac02_uses_injected_official_balance_endpoint(refresh: RefreshObservation) -> None:
    assert refresh.after_click == (EXPECTED_REQUEST, EXPECTED_REQUEST), (
        f"注入的本地数据源应收到两次带测试 Bearer 凭据的 GET {BALANCE_PATH}，实得 {refresh.after_click!r}"
    )


@dataclass
class DetailObservation:
    before: str
    expanded: bool
    after: str


@pytest.fixture(scope="module")
def details(tmp_path_factory: pytest.TempPathFactory) -> DetailObservation:
    reply = BalanceReply([balance("CNY", "80.00", "70.00", "10.00"), balance("USD", "5.00", "4.00", "1.00")])
    reply.release.set()
    directory = tmp_path_factory.mktemp("ac06")
    with serve_deepseek(reply) as source, run_dashboard(source, directory) as url, open_chrome(directory) as browser:
        browser.open(url)
        arrived = source.wait_for_requests(1, QUERY_TIMEOUT)
        before = poll(browser.card, lambda text: bool(CNY_80.search(text))) if arrived else browser.card()
        expanded = browser.click_control(r"明细|详情|展开", in_card=True)
        after = poll(browser.card, lambda text: bool(USD_5.search(text) and TOPPED_UP.search(text) and GRANTED.search(text))) if expanded else browser.card()
        return DetailObservation(before, expanded, after)


@pytest.mark.parametrize(
    ("expected", "amount"), [(CNY_80, "CNY 80.00"), (USD_5, "USD 5.00")], ids=["CNY", "USD"],
)
def test_ac06_expanded_card_shows_each_currency(details: DetailObservation, expected: re.Pattern[str], amount: str) -> None:
    assert details.expanded, f"DeepSeek 卡片应提供明细展开控件，实得 {details.before!r}"
    assert expected.search(details.after), f"展开后应按币种显示可用余额 {amount}，实得 {details.after!r}"


@pytest.mark.parametrize(
    ("expected", "label"), [(TOPPED_UP, "充值余额 ¥70.00"), (GRANTED, "赠金 ¥10.00")],
    ids=["topped-up", "granted"],
)
def test_ac06_expanded_cny_detail_shows_labeled_amount(details: DetailObservation, expected: re.Pattern[str], label: str) -> None:
    assert details.expanded, f"DeepSeek 卡片应提供明细展开控件，实得 {details.before!r}"
    assert not expected.search(details.before) and expected.search(details.after), (
        f"展开明细后应分列显示 {label}，实得 {details.before!r} → {details.after!r}"
    )
