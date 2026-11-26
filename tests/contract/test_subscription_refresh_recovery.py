"""Ticket 06 / Spec 0.2.0: AC-02, AC-04 and manual refresh in AC-08."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from codex_device_source import FIRST, USAGE_BASE_PATH, USAGE_PATH, serve_codex_device
from deepseek_source import BALANCE_PATH, BalanceReply, DeepSeekSource, Request, serve_deepseek
from test_deepseek_balance import Browser, CNY, USD, balance, money, open_chrome, poll, run_dashboard
from test_subscription_admin_entry import ADMIN_PASSWORD, OVERVIEW, SETTINGS, observe, sign_in, wait_for
from test_subscription_codex_device_login import finish_authorization, refresh_now
from test_subscription_codex_second_account import (
    add_account as add_codex,
    by_name,
    cross_timestamp_tick,
    start_authorization,
    updated_at,
)
from test_subscription_deepseek_account import (
    CARD,
    NO_RESULT,
    SAVED_TIME,
    add_account as add_deepseek,
    card_text,
    expect_balance,
    reply,
    resize,
    view,
)


NAME_A = "云杉额度"
NAME_B = "松风余额"
KEY_A = "contract-refresh-first-A123"
KEY_B = "contract-refresh-second-B456"
START_REFRESH = r"立即刷新|^刷新$"
FAILURE = re.compile(r"刷新失败|查询失败")
NO_AVAILABLE = re.compile(r"暂无(?:可用|可查看的)?(?:余额|额度|数据|结果)|尚无(?:可用|查询)(?:额度|数据|结果)|尚未(?:取得|查得|完成查询)")
UPSTREAM_503 = re.compile(r"(?<!\d)503(?!\d)|服务(?:暂时)?不可用|Service Unavailable", re.IGNORECASE)
TIMEOUT = re.compile(r"超时|timed?\s*out|timeout", re.IGNORECASE)
ZERO = re.compile(r"(?<![\d.])0(?:\.00)?\s*%|[¥￥$]\s*0(?:\.00)?(?![\d.])")
USD_ZERO = money(USD, r"0\.00")
UNAVAILABLE = "http://127.0.0.1:1"


@contextmanager
def dashboard(source: DeepSeekSource, tmp_path: Path, **upstream: str) -> Iterator[Browser]:
    env = {
        "QUOTA_STATE_FILE": str(tmp_path / "state.json"),
        "QUOTA_ADMIN_PASSWORD": ADMIN_PASSWORD,
        "DEEPSEEK_API_KEY": "",
        "MIMO_API_KEY": "",
        "QUOTA_CODEX_ACCESS_TOKEN": "",
        "QUOTA_CODEX_BASE_URL": UNAVAILABLE,
        "QUOTA_MIMO_BASE_URL": UNAVAILABLE,
        "CODEX_HOME": str(tmp_path / "codex-home"),
    }
    env.update(upstream)
    with run_dashboard(source, tmp_path, **env) as origin, open_chrome(tmp_path) as browser:
        resize(browser, 1920, 1080)
        sign_in(browser, origin, OVERVIEW)
        yield browser


@contextmanager
def unavailable_codex_usage() -> Iterator[tuple[str, list[str]]]:
    """Device exchange succeeds; the authenticated usage endpoint returns 503."""
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != USAGE_PATH:
                self.send_error(404)
            elif self.headers.get("Authorization") != f"Bearer {FIRST.token}":
                self.send_error(401)
            else:
                requests.append(self.path)
                self.send_error(503)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}{USAGE_BASE_PATH}", requests
        finally:
            server.shutdown()
            thread.join()


def cny(amount: str) -> dict[str, str]:
    return balance("CNY", amount, amount, "0.00")


def refresh_accounts(browser: Browser, source: DeepSeekSource, *identities: str) -> None:
    before = len(source.wait_for_requests(0, 0))
    refresh_now(browser)
    actual = source.wait_for_requests(before + len(identities), 4)[before:]
    expected = [Request("GET", BALANCE_PATH, True, identity=identity) for identity in identities]
    assert len(actual) == len(expected) and set(actual) == set(expected), (
        f"一次「立即刷新」应分别查询已连接账号。预期 {expected!r}，实得 {actual!r}"
    )


def successful_card(browser: Browser, name: str, amount: str, currency: str = CNY) -> str:
    text = expect_balance(browser, name, money(currency, re.escape(amount)))
    assert SAVED_TIME.search(text), f"{name!r} 成功后应显示更新时间，实得卡片 {text!r}"
    assert not FAILURE.search(text) and not NO_RESULT.search(text), f"{name!r} 应显示可用结果，实得卡片 {text!r}"
    return text


def displayed_clock(text: str) -> str:
    match = re.search(r"(?:已更新|上次成功|更新于)[^\n]*?(\d{1,2}[:：]\d{2}(?:[:：]\d{2})?)", text)
    assert match, f"应显示实际成功时间，实得卡片 {text!r}"
    return match[1].replace("：", ":")


def expand_card_details(browser: Browser, name: str) -> str:
    assert observe(browser, f"""(() => {{
        const card = shown({json.dumps(CARD)}).find(item =>
            shown('h2,h3,h4,[role=heading]', item).some(title =>
                title.innerText.trim() === {json.dumps(name)}));
        if (!card) return false;
        const control = shown('summary, button, [role=button]', card).find(item =>
            /明细|详情|展开/.test(item.getAttribute('aria-label') || item.innerText));
        if (!control) return false;
        if (!control.closest('details')?.open && control.getAttribute('aria-expanded') !== 'true') control.click();
        return true;
    }})()"""), f"{name!r} 应能打开自己的余额明细"
    return card_text(browser, name)


def test_ac02_ac04_first_query_failure_differs_from_successful_zero(tmp_path: Path) -> None:
    zero = reply(balance("USD", "0.00", "0.00", "0.00"))
    with (
        serve_deepseek(accounts={KEY_B: ("B", zero)}) as deepseek,
        serve_codex_device((FIRST,)) as codex,
        unavailable_codex_usage() as (usage_url, usage_requests),
        dashboard(deepseek, tmp_path,
                  QUOTA_CODEX_AUTH_BASE_URL=codex.origin, QUOTA_CODEX_BASE_URL=usage_url) as browser,
    ):
        add_codex(browser, NAME_A, {NAME_A})
        start_authorization(browser, codex, NAME_A, FIRST)
        finish_authorization(browser, codex, FIRST)
        assert usage_requests == [USAGE_PATH], "A 授权后应发出首次额度查询，由上游返回 503"
        view(browser, OVERVIEW)
        add_deepseek(browser, deepseek, NAME_B, KEY_B, "B")
        view(browser, OVERVIEW)
        before = by_name(browser)
        cross_timestamp_tick(before[NAME_B])
        refresh_accounts(browser, deepseek, "B")
        assert usage_requests == [USAGE_PATH, USAGE_PATH], "同一次立即刷新也应查询已连接的 A"
        rows = by_name(browser)
        failed = card_text(browser, NAME_A)
        zero_card = successful_card(browser, NAME_B, "0.00", USD)
        assert updated_at(rows[NAME_B]) > updated_at(before[NAME_B]), "B 同轮零额查询应推进成功时间"
        assert rows[NAME_A]["failed"] and not rows[NAME_A]["updated"], "A 的 503 应记录为首次查询失败"
        assert failed and FAILURE.search(failed), f"首次失败应保留 A 卡片及失败状态，实得 {failed!r}"
        assert not ZERO.search(failed), f"首次失败不应把未查得额度显示为零，实得 {failed!r}"
        assert USD_ZERO.search(zero_card), f"成功零额应显示官方币种 USD 和 0.00，实得 {zero_card!r}"
        assert UPSTREAM_503.search(failed), f"首次失败应显示实际 503 原因，实得 {failed!r}"
        assert NO_AVAILABLE.search(failed), f"首次失败应指明暂无可用结果，实得 {failed!r}"


@pytest.mark.parametrize("has_other", [False, True], ids=["single-account", "two-accounts"])
def test_ac04_ac08_manual_refresh_preserves_success_then_recovers(tmp_path: Path, has_other: bool) -> None:
    replies = {KEY_A: ("A", reply(cny("80.00")))}
    if has_other:
        replies[KEY_B] = ("B", reply(cny("17.00")))
    with serve_deepseek(accounts=replies) as source, dashboard(source, tmp_path) as browser:
        add_deepseek(browser, source, NAME_A, KEY_A, "A")
        if has_other:
            view(browser, SETTINGS)
            add_deepseek(browser, source, NAME_B, KEY_B, "B")
        view(browser, OVERVIEW)
        assert observe(browser, f"shown({json.dumps(CARD)}).length === {len(replies)}"), "总览应按配置显示账号卡片"
        identities = ("A", "B") if has_other else ("A",)
        refresh_accounts(browser, source, *identities)
        first_card = successful_card(browser, NAME_A, "80.00")
        if has_other:
            successful_card(browser, NAME_B, "17.00")
        before = by_name(browser)

        replies[KEY_A] = ("A", BalanceReply([], status=503))
        if has_other:
            replies[KEY_B] = ("B", reply(cny("29.00")))
            cross_timestamp_tick(before[NAME_B])
        refresh_accounts(browser, source, *identities)
        failed = by_name(browser)
        failed_card = card_text(browser, NAME_A)
        assert failed[NAME_A]["failed"], f"A 应记录为刷新失败，实得卡片 {failed_card!r}"
        assert failed[NAME_A]["updated"] == before[NAME_A]["updated"], "A 失败后应保留上一轮实际成功时间"
        assert money(CNY, r"80\.00").search(failed_card), f"A 失败后应保留原余额，实得卡片 {failed_card!r}"
        assert FAILURE.search(failed_card) and displayed_clock(failed_card) == displayed_clock(first_card), (
            f"A 卡片应显示刷新失败并保留原成功时间，实得 {first_card!r} → {failed_card!r}"
        )
        if has_other:
            successful_card(browser, NAME_B, "29.00")
            assert updated_at(failed[NAME_B]) > updated_at(before[NAME_B]), "A 失败的同轮，B 应推进实际成功时间"
        # Accept the reason on the card or in its expandable details.
        reason_card = failed_card if UPSTREAM_503.search(failed_card) else expand_card_details(browser, NAME_A)

        replies[KEY_A] = ("A", reply(cny("65.00")))
        cross_timestamp_tick(before[NAME_A])
        refresh_accounts(browser, source, *identities)
        recovered = by_name(browser)[NAME_A]
        recovered_card = successful_card(browser, NAME_A, "65.00")
        assert not money(CNY, r"80\.00").search(recovered_card), f"A 应替换旧余额，实得卡片 {recovered_card!r}"
        assert updated_at(recovered) > updated_at(before[NAME_A]), "A 恢复后应推进实际成功时间"
        assert not recovered.get("failed") and not recovered.get("error"), "A 恢复后应清除失败和失败原因"
        recovered_details = expand_card_details(browser, NAME_A)
        assert not (FAILURE.search(recovered_details) or UPSTREAM_503.search(recovered_details)
                    or TIMEOUT.search(recovered_details)), f"恢复后卡片及其明细应清除失败提示，实得 {recovered_details!r}"
        assert UPSTREAM_503.search(reason_card) and not TIMEOUT.search(reason_card), (
            f"A 刷新失败应显示官方接口 503 原因，实得卡片及明细 {reason_card!r}"
        )


def test_ac08_other_account_completes_while_one_reply_is_held(tmp_path: Path) -> None:
    replies = {KEY_A: ("A", reply(cny("80.00"))), KEY_B: ("B", reply(cny("17.00")))}
    with serve_deepseek(accounts=replies) as source, dashboard(source, tmp_path) as browser:
        add_deepseek(browser, source, NAME_A, KEY_A, "A")
        view(browser, SETTINGS)
        add_deepseek(browser, source, NAME_B, KEY_B, "B")
        view(browser, OVERVIEW)
        before = by_name(browser)
        cross_timestamp_tick(before[NAME_B])
        held = BalanceReply([cny("65.00")])
        replies[KEY_A] = ("A", held)
        replies[KEY_B] = ("B", reply(cny("29.00")))
        offset = len(source.wait_for_requests(0, 0))
        assert browser.click_control(START_REFRESH), "应从总览发起立即刷新"
        request_a = Request("GET", BALANCE_PATH, True, identity="A")
        request_b = Request("GET", BALANCE_PATH, True, identity="B")
        while_held: dict[str, dict[str, Any]] = {}

        def observe_b_update() -> str:
            while_held.update(by_name(browser))
            return str(while_held[NAME_B]["updated"])

        try:
            assert request_a in source.wait_for_requests(offset + 2, 3)[offset:], "已连接的慢账号 A 应收到刷新请求"
            poll(observe_b_update, lambda stamp: stamp != before[NAME_B]["updated"], timeout=3)
            pending_requests = source.wait_for_requests(0, 0)[offset:]
            assert not held.release.is_set()
        finally:
            held.release.set()

        successful_card(browser, NAME_A, "65.00")
        successful_card(browser, NAME_B, "29.00")
        assert request_b in pending_requests, f"A 回执挂起期间也应查询 B，实得请求 {pending_requests!r}"
        assert updated_at(while_held[NAME_B]) > updated_at(before[NAME_B]), "A 仍挂起时 B 应已写入新的成功时间"
        balances = while_held[NAME_B]["balances"]
        assert balances != before[NAME_B]["balances"] and balances == by_name(browser)[NAME_B]["balances"], (
            "A 仍挂起时 B 应已写入本轮余额"
        )
        assert while_held[NAME_A]["updated"] == before[NAME_A]["updated"], "A 的回执未释放时应保留旧成功时间"
