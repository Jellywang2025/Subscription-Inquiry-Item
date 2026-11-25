"""AC-03 / AC-04 through the dashboard and the injected Codex usage API."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Thread

import pytest

from deepseek_source import BalanceReply, Request, serve_deepseek
from test_deepseek_balance import (
    QUERY_TIMEOUT,
    QUERYING,
    Browser,
    balance,
    card_lookup,
    open_chrome,
    poll,
    run_dashboard,
)
from test_mimo_balance import MIMO_KEY, MIMO_PATH, official_balance


CODEX_TOKEN = "contract-codex-access-token"
USAGE_BASE_PATH = "/backend-api"
USAGE_PATH = f"{USAGE_BASE_PATH}/wham/usage"
CODEX_USER_AGENT_PREFIX = "codex_cli_rs/"
RESET_AT = int(datetime(2026, 11, 25, 20, 47, 23, tzinfo=UTC).timestamp())
SHORT_LABELS = (r"短(?:额度)?窗口|短周期|短期", r"(?<!\d)5\s*(?:分钟|mins?\b|m\b)")
LONG_LABELS = (
    r"长(?:额度)?窗口|长周期|长期",
    r"(?<!\d)(?:7\s*(?:天|days?\b|d\b)|1\s*周)|周(?:窗口|额度|限额)",
)
CHAT_STATUS = "需前往官方查看"


@dataclass(frozen=True)
class UsageRequest(Request):
    has_codex_user_agent: bool


EXPECTED_REQUEST = UsageRequest("GET", USAGE_PATH, True, True)


@dataclass
class UsageReply:
    status: int = 200
    release: Event | None = None
    rate_limits_by_limit_id: dict[str, object] | None = None


@contextmanager
def codex_dashboard(directory: Path, *replies: UsageReply) -> Iterator[tuple[Browser, Queue[UsageRequest]]]:
    requests: Queue[UsageRequest] = Queue()
    remaining: Queue[UsageReply] = Queue()
    for reply in replies:
        remaining.put(reply)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == MIMO_PATH:
                key = self.headers.get("api-key")
                if key != MIMO_KEY and self.headers.get("Authorization") != f"Bearer {MIMO_KEY}":
                    self.send_error(401)
                    return
                self.send_json(official_balance())
                return
            self.handle_usage()

        def do_POST(self) -> None:
            self.handle_usage()

        def handle_usage(self) -> None:
            authorized = self.headers.get("Authorization") == f"Bearer {CODEX_TOKEN}"
            has_codex_user_agent = self.headers.get("User-Agent", "").startswith(CODEX_USER_AGENT_PREFIX)
            requests.put(UsageRequest(self.command, self.path, authorized, has_codex_user_agent))
            if self.command != "GET" or self.path != USAGE_PATH:
                self.send_error(404)
                return
            if not authorized:
                self.send_error(401)
                return
            if not has_codex_user_agent:
                self.send_error(403, "Expected a Codex CLI User-Agent")
                return
            try:
                reply = remaining.get_nowait()
            except Empty:
                self.send_error(429, "Unexpected extra usage request")
                return
            if reply.release is not None and not reply.release.wait(15):
                self.send_error(504, "Test did not release the usage response")
                return
            if reply.status != 200:
                self.send_error(reply.status, "Usage source unavailable")
                return
            payload: dict[str, object] = {
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 25,
                        "limit_window_seconds": 300,
                        "reset_after_seconds": 120,
                        "reset_at": RESET_AT,
                    },
                    "secondary_window": {
                        "used_percent": 70,
                        "limit_window_seconds": 604800,
                        "reset_after_seconds": None,
                        "reset_at": None,
                    },
                },
                "plan_type": "pro",
            }
            if reply.rate_limits_by_limit_id is not None:
                payload["rate_limits_by_limit_id"] = reply.rate_limits_by_limit_id
            self.send_json(payload)

        def send_json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    deepseek_reply = BalanceReply([balance("CNY", "50.00", "40.00", "10.00")])
    deepseek_reply.release.set()
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server, serve_deepseek(
        *[deepseek_reply] * len(replies),
    ) as deepseek:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            with run_dashboard(
                deepseek,
                directory,
                QUOTA_CODEX_BASE_URL=f"{base_url}{USAGE_BASE_PATH}",
                QUOTA_CODEX_ACCESS_TOKEN=CODEX_TOKEN,
                QUOTA_MIMO_BASE_URL=base_url,
                MIMO_API_KEY=MIMO_KEY,
            ) as url, open_chrome(directory) as browser:
                browser.open(url)
                yield browser, requests
        finally:
            for reply in replies:
                if reply.release is not None:
                    reply.release.set()
            server.shutdown()
            thread.join()


def next_request(requests: Queue[UsageRequest]) -> UsageRequest | None:
    try:
        return requests.get(timeout=QUERY_TIMEOUT)
    except Empty:
        return None


def window_start(text: str, labels: tuple[str, str]) -> int | None:
    for label in labels:
        if match := re.search(label, text, re.IGNORECASE):
            return match.start()
    return None


def windows(text: str) -> tuple[str, str]:
    short = window_start(text, SHORT_LABELS)
    long = window_start(text, LONG_LABELS)
    if short is None or long is None:
        return "", ""
    if short < long:
        return text[short:long], text[long:]
    return text[short:], text[long:short]


def has_percent(text: str, percent: int) -> bool:
    return bool(re.search(rf"(?<![\d.+−-]){percent}(?:\.0+)?\s*[%％]", text))


def remaining_windows(text: str) -> bool:
    short, long = windows(text)
    return has_percent(short, 75) and has_percent(long, 30)


def reset_time_pattern(local_reset: datetime) -> re.Pattern[str]:
    hour = f"0?{local_reset.hour}" if local_reset.hour < 10 else str(local_reset.hour)
    date = rf"{local_reset.year}[-/.年]0?{local_reset.month}[-/.月]0?{local_reset.day}日?"
    return re.compile(
        rf"重置(?:时间|于)?\s*[:：]?\s*(?:{date}[\sT]*)?{hour}[:：]{local_reset.minute:02}"
        rf"(?:[:：]{local_reset.second:02})?(?![\d:：])"
    )


def official_link(browser: Browser) -> str:
    return browser.evaluate(f"""(() => {{
        {card_lookup("ChatGPT 聊天")}
        const links = [...(card?.querySelectorAll('a[href]') ?? [])];
        const link = links.find(element => {{
            const url = new URL(element.href, location.href);
            return element.getClientRects().length && getComputedStyle(element).visibility === 'visible'
                && !element.matches('[download], [aria-disabled=true]') && url.protocol === 'https:'
                && (url.hostname === 'chatgpt.com' || url.hostname.endsWith('.chatgpt.com'));
        }});
        return link?.href ?? '';
    }})()""")


def click_official_link(browser: Browser, href: str) -> str:
    point = browser.evaluate(f"""(() => {{
        {card_lookup("ChatGPT 聊天")}
        const link = [...(card?.querySelectorAll('a[href]') ?? [])]
            .find(element => element.href === {json.dumps(href)} && element.getClientRects().length
                && getComputedStyle(element).visibility === 'visible'
                && !element.matches('[download], [aria-disabled=true]'));
        if (!link) return null;
        // Observe the trusted click after page handlers and suppress external navigation in the test.
        window.addEventListener('click', event => {{
            if (!event.isTrusted || !event.composedPath().includes(link) || event.defaultPrevented) return;
            window.__contractOfficialDestination = link.href;
            event.preventDefault();
        }}, {{ once: true }});
        link.scrollIntoView({{block: 'center'}});
        const box = link.getBoundingClientRect();
        return {{ x: box.x + box.width / 2, y: box.y + box.height / 2 }};
    }})()""")
    if point is None:
        return ""
    browser.call("Input.dispatchMouseEvent", type="mousePressed", button="left", clickCount=1, **point)
    browser.call("Input.dispatchMouseEvent", type="mouseReleased", button="left", clickCount=1, **point)
    return browser.evaluate("window.__contractOfficialDestination ?? ''")


@pytest.fixture(scope="module")
def usage_dashboard(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Browser, UsageRequest | None]]:
    with codex_dashboard(tmp_path_factory.mktemp("codex-usage"), UsageReply()) as (browser, requests):
        yield browser, next_request(requests)


def test_ac03_remaining_percent_by_window(usage_dashboard: tuple[Browser, UsageRequest | None]) -> None:
    browser, request = usage_dashboard
    card = poll(lambda: browser.card("Codex"), remaining_windows) if request == EXPECTED_REQUEST else browser.card("Codex")
    assert remaining_windows(card), f"Codex 应显示短窗口剩余 75%、长窗口剩余 30%，实得卡片 {card!r}、请求 {request!r}"
    assert request == EXPECTED_REQUEST, f"应收到 {EXPECTED_REQUEST!r}，实得 {request!r}"


def test_ac03_reset_time_by_window(usage_dashboard: tuple[Browser, UsageRequest | None]) -> None:
    browser, request = usage_dashboard
    local_reset = datetime.fromtimestamp(RESET_AT)
    clock = reset_time_pattern(local_reset)

    def resets_match(card: str) -> bool:
        short, long = windows(card)
        return bool(clock.search(short) and re.search(r"重置(?:时间)?\s*[:：]?\s*未提供", long))

    card = poll(lambda: browser.card("Codex"), resets_match) if request == EXPECTED_REQUEST else browser.card("Codex")
    assert resets_match(card), (
        f"Codex 短窗口应显示 Windows 本地重置时间 {local_reset:%Y-%m-%d %H:%M:%S}，"
        f"长窗口重置应显示未提供，实得卡片 {card!r}、请求 {request!r}"
    )
    assert request == EXPECTED_REQUEST


def test_ac04_chatgpt_official_link(usage_dashboard: tuple[Browser, UsageRequest | None]) -> None:
    browser, request = usage_dashboard
    if request == EXPECTED_REQUEST:
        poll(lambda: browser.card("Codex"), remaining_windows)
    href = poll(lambda: official_link(browser), bool) if request == EXPECTED_REQUEST else official_link(browser)
    assert href, f"ChatGPT 聊天应提供可点击的 HTTPS 官方链接，实得卡片 {browser.card('ChatGPT 聊天')!r}、请求 {request!r}"
    assert click_official_link(browser, href) == href, f"真实鼠标点击应激活官方链接 {href!r}"
    assert request == EXPECTED_REQUEST


def test_ac04_chatgpt_remaining_requires_official_view(usage_dashboard: tuple[Browser, UsageRequest | None]) -> None:
    browser, request = usage_dashboard
    if request == EXPECTED_REQUEST:
        poll(lambda: browser.card("Codex"), remaining_windows)

    def requires_official_view(card: str) -> bool:
        return CHAT_STATUS in "".join(card.split()) and not re.search(r"\d\s*[%％]", card)

    def observe() -> str:
        return browser.card("ChatGPT 聊天")
    card = poll(observe, requires_official_view) if request == EXPECTED_REQUEST else observe()
    assert requires_official_view(card), (
        f"只返回 Codex 额度时，ChatGPT 聊天剩余值应为「{CHAT_STATUS}」，"
        f"实得卡片 {card!r}、请求 {request!r}"
    )
    assert request == EXPECTED_REQUEST


def test_codex_auto_query_and_failed_refresh(tmp_path: Path) -> None:
    initial, refresh = Event(), Event()
    with codex_dashboard(tmp_path, UsageReply(release=initial), UsageReply(503, refresh)) as (browser, requests):
        def card() -> str:
            return browser.card("Codex")

        first = next_request(requests)
        assert first == EXPECTED_REQUEST, f"打开看板应自动查询 Codex，实得 {first!r}、卡片 {card()!r}"
        pending = poll(card, lambda text: bool(QUERYING.search(text)))
        assert QUERYING.search(pending), f"首次查询应显示查询状态，实得 {pending!r}"
        initial.set()
        result = poll(card, remaining_windows)
        assert remaining_windows(result), f"查询成功应显示两个窗口的剩余比例，实得 {result!r}"
        assert browser.click_control(r"^刷\s*新$"), "看板应提供全局刷新控件"
        second = next_request(requests)
        assert second == EXPECTED_REQUEST, f"全局刷新应再次查询 Codex，实得 {second!r}、卡片 {card()!r}"
        pending = poll(card, lambda text: bool(QUERYING.search(text)))
        assert QUERYING.search(pending), f"刷新应显示查询状态，实得 {pending!r}"
        refresh.set()
        failure = re.compile(r"刷新\s*(?:失败|未成功)")
        failed = poll(card, lambda text: bool(failure.search(text) and remaining_windows(text)))
        assert failure.search(failed) and remaining_windows(failed), (
            f"刷新失败应标记失败并保留两窗口的剩余比例，实得 {failed!r}"
        )
