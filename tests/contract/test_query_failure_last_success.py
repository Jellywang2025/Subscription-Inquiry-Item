"""AC-08 / AC-09 through the dashboard with an isolated DeepSeek balance source."""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from deepseek_source import BalanceReply, serve_deepseek
from test_codex_windows import CODEX_TOKEN, USAGE_BASE_PATH, USAGE_PATH
from test_deepseek_balance import (
    CNY,
    CNY_65,
    CNY_80,
    EXPECTED_REQUEST,
    QUERY_TIMEOUT,
    USD,
    balance,
    money,
    open_chrome,
    poll,
    run_dashboard,
    success_time,
    wait_for_new_clock_value,
)
from test_mimo_balance import MIMO_KEY, MIMO_PATH, official_balance
from test_startup_dashboard import PLATFORMS

# 0.2.0 迁移：查询失败与恢复随账号刷新模型重建，此契约待 06 票按新架构重建。
pytestmark = pytest.mark.skip(reason="v0.1 查询失败契约待 06 票按 0.2.0 账号刷新架构重建")


# Allow the dashboard's own timeout to fire before the fixture closes the connection.
FAILURE_WAIT = 20
QUERY_FAILED = re.compile(r"查询\s*(?:失败|未成功|超时)")
REFRESH_FAILED = "刷新失败"
TIMEOUT_REASON = re.compile(r"超时|超过[^\n]{0,12}时限|timed\s+out|time[\s-]?out", re.IGNORECASE)
FAILURE = re.compile(rf"{QUERY_FAILED.pattern}|{REFRESH_FAILED}|{TIMEOUT_REASON.pattern}", re.IGNORECASE)
OTHER_PLATFORMS = tuple(platform for platform in PLATFORMS if platform != "DeepSeek")
DYNAMIC_OTHER_PLATFORMS = ("Codex", "MiMo")
ZERO_AMOUNTS = [(currency, money(symbol, r"0\.00")) for currency, symbol in (("CNY", CNY), ("USD", USD))]


@pytest.fixture(scope="module")
def zero_card(tmp_path_factory: pytest.TempPathFactory) -> str:
    reply = BalanceReply([balance(currency, "0.00", "0.00", "0.00") for currency, _ in ZERO_AMOUNTS])
    reply.release.set()
    directory = tmp_path_factory.mktemp("ac08-zero")
    with serve_deepseek(reply) as source, run_dashboard(source, directory) as url, open_chrome(directory) as browser:
        browser.open(url)
        assert source.wait_for_requests(1, QUERY_TIMEOUT) == (EXPECTED_REQUEST,)
        return poll(browser.card, lambda card: bool(success_time(card)))


@pytest.mark.parametrize(("currency", "amount"), ZERO_AMOUNTS, ids=["CNY", "USD"])
def test_ac08_official_zero_shows_currency_and_two_decimals(
    zero_card: str, currency: str, amount: re.Pattern[str],
) -> None:
    assert success_time(zero_card), f"零额回执应显示为查询成功，实得卡片 {zero_card!r}"
    assert amount.search(zero_card), f"应显示 {currency} 0.00，实得卡片 {zero_card!r}"


def test_ac08_first_timeout_shows_failure_and_reason(tmp_path: Path) -> None:
    with serve_deepseek(BalanceReply(None)) as source, run_dashboard(source, tmp_path) as url, open_chrome(tmp_path) as browser:
        browser.open(url)
        assert source.wait_for_requests(1, QUERY_TIMEOUT) == (EXPECTED_REQUEST,)
        card = poll(
            browser.card, lambda text: bool(QUERY_FAILED.search(text) and TIMEOUT_REASON.search(text)),
            timeout=FAILURE_WAIT,
        )
        assert QUERY_FAILED.search(card), f"首次查询超时应显示查询失败状态，实得卡片 {card!r}"
        assert TIMEOUT_REASON.search(card), f"首次查询失败应显示可识别的超时原因，实得卡片 {card!r}"


@dataclass
class RefreshAfterTimeout:
    first: str
    failed: str
    recovered: str
    heading_on_failure: str
    others_before: dict[str, str]
    others_on_failure: dict[str, str]


@contextmanager
def serve_other_sources() -> Iterator[dict[str, str]]:
    responses = {
        USAGE_PATH: {"rate_limit": {"primary_window": {"used_percent": 25}}},
        MIMO_PATH: official_balance(),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path not in responses:
                self.send_error(404)
                return
            body = json.dumps(responses[self.path]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        try:
            yield {
                "QUOTA_CODEX_BASE_URL": f"{base_url}{USAGE_BASE_PATH}",
                "QUOTA_CODEX_ACCESS_TOKEN": CODEX_TOKEN,
                "QUOTA_MIMO_BASE_URL": base_url,
                "MIMO_API_KEY": MIMO_KEY,
            }
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture(scope="module")
def refresh_after_timeout(tmp_path_factory: pytest.TempPathFactory) -> RefreshAfterTimeout:
    first = BalanceReply([balance("CNY", "80.00", "70.00", "10.00")])
    recovery = BalanceReply([balance("CNY", "65.00", "55.00", "10.00")])
    first.release.set()
    recovery.release.set()
    directory = tmp_path_factory.mktemp("ac09-timeout")
    with (
        serve_deepseek(first, BalanceReply(None), recovery) as source,
        serve_other_sources() as source_environment,
        run_dashboard(source, directory, **source_environment) as url,
        open_chrome(directory) as browser,
    ):
        browser.open(url)
        assert source.wait_for_requests(1, QUERY_TIMEOUT) == (EXPECTED_REQUEST,)
        first_card = poll(browser.card, lambda card: bool(CNY_80.search(card) and success_time(card)))
        before = success_time(first_card)
        assert CNY_80.search(first_card) and before, f"超时前应已有 ¥80 与成功时间，实得卡片 {first_card!r}"
        others_before = {platform: browser.card(platform) for platform in OTHER_PLATFORMS}
        for platform in DYNAMIC_OTHER_PLATFORMS:
            others_before[platform] = poll(lambda: browser.card(platform), lambda card: bool(success_time(card)))
        # Cross the displayed clock's resolution before the failed request starts.
        wait_for_new_clock_value(before)
        assert browser.click_control(r"^刷\s*新$"), "首次成功后应能点击「刷新」"
        assert source.wait_for_requests(2, QUERY_TIMEOUT) == (EXPECTED_REQUEST,) * 2
        failed = poll(browser.card, lambda card: bool(FAILURE.search(card)), timeout=FAILURE_WAIT)
        if FAILURE.search(failed) and REFRESH_FAILED not in failed:
            failed = poll(browser.card, lambda card: REFRESH_FAILED in card)
        heading = browser.evaluate("document.querySelector('h1')?.innerText")
        others_on_failure = {platform: browser.card(platform) for platform in OTHER_PLATFORMS}
        assert browser.click_control(r"^刷\s*新$"), "刷新超时后应能再次点击「刷新」"
        assert source.wait_for_requests(3, QUERY_TIMEOUT) == (EXPECTED_REQUEST,) * 3
        recovered = poll(
            browser.card,
            lambda card: bool(CNY_65.search(card) and success_time(card) and not FAILURE.search(card)),
        )
        return RefreshAfterTimeout(first_card, failed, recovered, heading, others_before, others_on_failure)


def test_ac09_timeout_keeps_previous_result_and_marks_refresh_failure(refresh_after_timeout: RefreshAfterTimeout) -> None:
    first, failed = refresh_after_timeout.first, refresh_after_timeout.failed
    assert CNY_80.search(failed), f"刷新超时后应保留 ¥80，实得卡片 {failed!r}"
    assert success_time(failed) == success_time(first), f"应保留上次成功时间，实得 {first!r} → {failed!r}"
    assert REFRESH_FAILED in failed, f"刷新超时后应标记「刷新失败」，实得卡片 {failed!r}"


def test_ac09_timeout_leaves_dashboard_and_other_cards_usable(refresh_after_timeout: RefreshAfterTimeout) -> None:
    observation = refresh_after_timeout
    assert FAILURE.search(observation.failed), f"应先观察到 DeepSeek 刷新失败，实得卡片 {observation.failed!r}"
    assert observation.heading_on_failure == "个人订阅额度看板"
    assert all(observation.others_before.values()), f"超时前应看到其他来源卡片，实得 {observation.others_before!r}"
    for platform, card in observation.others_on_failure.items():
        assert card and not FAILURE.search(card), f"DeepSeek 超时后 {platform} 卡片应可见且无失败状态，实得 {card!r}"
    for platform in DYNAMIC_OTHER_PLATFORMS:
        before_card = observation.others_before[platform]
        refreshed_card = observation.others_on_failure[platform]
        before, after = success_time(before_card), success_time(refreshed_card)
        assert before and after and before != after, (
            f"DeepSeek 超时时 {platform} 应更新成功时间，实得 {before_card!r} → {refreshed_card!r}"
        )


def test_ac09_success_after_timeout_updates_result_and_clears_failure(refresh_after_timeout: RefreshAfterTimeout) -> None:
    observation = refresh_after_timeout
    assert FAILURE.search(observation.failed), f"恢复前应看到失败状态，实得卡片 {observation.failed!r}"
    recovered = observation.recovered
    assert CNY_65.search(recovered) and not CNY_80.search(recovered), f"恢复后应将 ¥80 更新为 ¥65，实得卡片 {recovered!r}"
    after = success_time(recovered)
    assert after and after != success_time(observation.first), f"恢复后应更新成功时间，实得 {observation.first!r} → {recovered!r}"
    assert not FAILURE.search(recovered), f"恢复后应清除失败状态，实得卡片 {recovered!r}"
