"""AC-07 through the public dashboard and a loopback MiMo SK balance source."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
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
    open_chrome,
    poll,
    run_dashboard,
)


MIMO_KEY = "sk-contract-mimo-balance"
MIMO_PATH = "/v1/platform/balance"
EXPECTED_REQUEST = Request("GET", MIMO_PATH, True)
YUAN = r"(?<![-−])[¥￥]\s*"


@dataclass
class MimoReply:
    payload: dict[str, object] | None
    release: Event | None = None


def official_balance(*, over_frozen: bool = False) -> dict[str, object]:
    return {
        "object": "balance",
        "is_available": not over_frozen,
        "balance_infos": [{
            "currency": "CNY",
            "cash_balance": "8.00" if over_frozen else "100.00",
            "gift_balance": "4.00" if over_frozen else "20.00",
            "frozen_balance": "20.00",
            "credit_limit": "500.00",
            "credit_available": "3.00" if over_frozen else "80.00",
            "total_available": "15.00" if over_frozen else "200.00",
        }],
    }


@contextmanager
def mimo_dashboard(directory: Path, *replies: MimoReply) -> Iterator[tuple[Browser, Queue[Request]]]:
    requests: Queue[Request] = Queue()
    remaining = Queue[MimoReply]()
    for reply in replies:
        remaining.put(reply)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            authorized = (
                self.headers.get("Authorization") == f"Bearer {MIMO_KEY}"
                or self.headers.get("api-key") == MIMO_KEY
            )
            requests.put(Request(self.command, self.path, authorized))
            if self.path != MIMO_PATH:
                self.send_error(404)
                return
            if not authorized:
                self.send_error(401)
                return
            try:
                reply = remaining.get_nowait()
            except Empty:
                self.send_error(429, "Unexpected extra balance request")
                return
            if reply.release is not None and not reply.release.wait(15):
                self.send_error(504, "Test did not release the balance response")
                return
            if reply.payload is None:
                self.send_error(503, "Balance source unavailable")
                return
            body = json.dumps(reply.payload).encode("utf-8")
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
        try:
            with run_dashboard(
                deepseek,
                directory,
                QUOTA_MIMO_BASE_URL=f"http://127.0.0.1:{server.server_port}",
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


def next_request(requests: Queue[Request]) -> Request | None:
    try:
        return requests.get(timeout=QUERY_TIMEOUT)
    except Empty:
        return None


def yuan_amount(amount: str) -> str:
    return rf"{YUAN}{re.escape(amount)}(?![\d.])"


@pytest.mark.parametrize(
    ("over_frozen", "amount"),
    [(False, "180.00"), (True, "0.00")],
    ids=["available-after-freeze", "clamped-to-zero"],
)
def test_ac07_initial_available_amount(tmp_path: Path, over_frozen: bool, amount: str) -> None:
    expected = re.compile(yuan_amount(amount))
    with mimo_dashboard(tmp_path, MimoReply(official_balance(over_frozen=over_frozen))) as (browser, requests):
        request = next_request(requests)
        card = (
            poll(lambda: browser.card("MiMo"), lambda text: bool(expected.search(text)))
            if request == EXPECTED_REQUEST else browser.card("MiMo")
        )
        assert expected.search(card), f"MiMo 首屏应显示可动用 ¥{amount}，实得卡片 {card!r}、请求 {request!r}"
        assert request == EXPECTED_REQUEST, f"应收到带测试凭据的 GET {MIMO_PATH}，实得 {request!r}"


def test_ac07_expanded_details_keep_official_amounts(tmp_path: Path) -> None:
    labels = [
        (r"现金(?:余额)?", "100.00"),
        (r"赠金(?:余额)?", "20.00"),
        (r"可用授信(?:额度)?", "80.00"),
        (r"冻结(?:金额|余额)?", "20.00"),
    ]
    details = [re.compile(rf"{label}\s*[:：]?\s*{yuan_amount(amount)}") for label, amount in labels]
    with mimo_dashboard(tmp_path, MimoReply(official_balance())) as (browser, requests):
        request = next_request(requests)
        card = partial(browser.card, "MiMo")
        before = (
            poll(card, lambda text: bool(re.search(yuan_amount("180.00"), text)))
            if request == EXPECTED_REQUEST else card()
        )
        assert browser.click_control(r"明细|详情|展开", in_card=True, platform="MiMo"), (
            f"MiMo 应提供明细展开控件，实得请求 {request!r}、卡片 {before!r}"
        )
        after = poll(card, lambda text: all(detail.search(text) for detail in details))
        assert request == EXPECTED_REQUEST
        assert not any(detail.search(before) for detail in details), f"展开前应折叠明细，实得 {before!r}"
        assert all(detail.search(after) for detail in details), (
            f"展开后应分列现金 ¥100.00、赠金 ¥20.00、可用授信 ¥80.00、冻结 ¥20.00，实得 {after!r}"
        )


def test_mimo_refresh_shows_querying_and_preserves_balance_after_failure(tmp_path: Path) -> None:
    initial, refresh = Event(), Event()
    amount = re.compile(yuan_amount("180.00"))
    failure = re.compile(r"刷新\s*(?:失败|未成功)")
    with mimo_dashboard(tmp_path, MimoReply(official_balance(), initial), MimoReply(None, refresh)) as (
        browser, requests,
    ):
        card = partial(browser.card, "MiMo")
        first = next_request(requests)
        assert first == EXPECTED_REQUEST, f"打开看板应自动查询 MiMo，实得 {first!r}、卡片 {card()!r}"
        initial_pending = poll(card, lambda text: bool(QUERYING.search(text)))
        initial.set()
        first_result = poll(card, lambda text: bool(amount.search(text)))
        assert amount.search(first_result), f"首次成功应显示 ¥180.00，实得 {first_result!r}"
        assert browser.click_control(r"^刷\s*新$"), "看板应提供全局刷新控件"
        second = next_request(requests)
        assert second == EXPECTED_REQUEST, f"全局刷新应重新查询 MiMo，实得 {second!r}、卡片 {card()!r}"
        refresh_pending = poll(card, lambda text: bool(QUERYING.search(text)))
        refresh.set()
        failed = poll(card, lambda text: bool(failure.search(text) and amount.search(text)))
        assert QUERYING.search(initial_pending) and QUERYING.search(refresh_pending), (
            f"上游响应放行前应显示查询状态，实得首次 {initial_pending!r}、刷新 {refresh_pending!r}"
        )
        assert failure.search(failed) and amount.search(failed), (
            f"刷新失败后应显示失败状态并保留上次余额 ¥180.00，实得 {failed!r}"
        )
