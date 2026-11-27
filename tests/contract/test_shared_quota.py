"""AC-05 through the dashboard with injected Codex usage buckets."""

import re
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from test_codex_windows import (
    CHAT_STATUS,
    EXPECTED_REQUEST,
    UsageReply,
    codex_dashboard,
    has_percent,
    next_request,
    remaining_windows,
    reset_time_pattern,
)
from test_deepseek_balance import Browser, poll

# 0.2.0 迁移：共享额度随 Codex 账号授权模型重建，此契约待 04 票按新架构重建。
pytestmark = pytest.mark.skip(reason="v0.1 共享额度契约待 04 票按 0.2.0 设备码授权架构重建")


CHAT_CARD = "ChatGPT 聊天"
SHARED_LABEL = re.compile(r"ChatGPT\s*与\s*Codex\s*共享")
SHARED_PERCENT = re.compile(r"(?<![\d.+−-])70(?:\.0+)?\s*[%％]")
SHARED_REMAINING = re.compile(rf"(?:剩余|可用)(?:额度)?(?:比例)?\s*[:：]?\s*{SHARED_PERCENT.pattern}")
SHARED_RESET_AT = int(datetime(2026, 11, 26, 9, 13, 45, tzinfo=UTC).timestamp())
LOCAL_RESET = datetime.fromtimestamp(SHARED_RESET_AT)
SHARED_CLOCK = reset_time_pattern(LOCAL_RESET)


@pytest.fixture(
    scope="module",
    params=[
        pytest.param(("chatgpt_codex_shared", "Usage"), id="shared-key"),
        pytest.param(("combined_usage", "ChatGPT and Codex shared"), id="shared-name"),
    ],
)
def shared_dashboard(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Browser]:
    limit_id, limit_name = request.param
    shared_window = {
        "used_percent": 30,
        "limit_window_seconds": 3600,
        "reset_after_seconds": None,
        "reset_at": SHARED_RESET_AT,
    }
    reply = UsageReply(rate_limits_by_limit_id={
        "codex_extra": {
            "limit_name": "Codex shared",
            "primary_window": {**shared_window, "used_percent": 45, "reset_at": None},
        },
        limit_id: {"limit_name": limit_name, "primary_window": shared_window},
    })
    with codex_dashboard(tmp_path_factory.mktemp("shared-quota"), reply) as (browser, requests):
        actual_request = next_request(requests)
        assert actual_request == EXPECTED_REQUEST, f"应收到 {EXPECTED_REQUEST!r}，实得 {actual_request!r}"
        codex = poll(lambda: browser.card("Codex"), remaining_windows)
        assert remaining_windows(codex), f"Codex 专用双窗口查询应已完成，实得 {codex!r}"
        yield browser


def test_ac05_shared_label(shared_dashboard: Browser) -> None:
    def shows_shared_label(text: str) -> bool:
        return bool(SHARED_LABEL.search(text)) and CHAT_STATUS not in "".join(text.split())

    chat = poll(lambda: shared_dashboard.card(CHAT_CARD), shows_shared_label)
    assert shows_shared_label(chat), (
        f"ChatGPT 聊天应以「ChatGPT 与 Codex 共享」替代静态状态，实得 {chat!r}"
    )
    assert not has_percent(chat, 55), f"只标注 Codex 的额外桶不应当作聊天共享额度，实得 {chat!r}"


def test_ac05_shared_remaining_and_reset(shared_dashboard: Browser) -> None:
    def shows_remaining_and_reset(text: str) -> bool:
        return bool(SHARED_REMAINING.search(text) and SHARED_CLOCK.search(text))

    chat = poll(lambda: shared_dashboard.card(CHAT_CARD), shows_remaining_and_reset)
    assert shows_remaining_and_reset(chat), (
        f"共享额度应显示剩余 70% 和 Windows 本地重置时间 {LOCAL_RESET:%Y-%m-%d %H:%M:%S}，"
        f"实得 {chat!r}，剩余 70% 可见={bool(SHARED_REMAINING.search(chat))}，"
        f"重置时间匹配={bool(SHARED_CLOCK.search(chat))}"
    )


def test_ac05_shared_quota_appears_once(shared_dashboard: Browser) -> None:
    markers = (SHARED_LABEL, SHARED_PERCENT, SHARED_CLOCK)

    def counts(text: str) -> tuple[int, ...]:
        return tuple(len(marker.findall(text)) for marker in markers)

    chat = poll(lambda: shared_dashboard.card(CHAT_CARD), lambda text: all(counts(text)))
    codex = shared_dashboard.card("Codex")
    page = shared_dashboard.evaluate("document.body.innerText")
    assert counts(chat) == counts(page) == (1, 1, 1) and counts(codex) == (0, 0, 0), (
        "共享标注、70% 和该重置时间应在整页各出现一次，且均在 ChatGPT 聊天卡片。"
        f"实得出现次数 ChatGPT={counts(chat)}，Codex={counts(codex)}，整页={counts(page)}；"
        f"ChatGPT 卡片 {chat!r}，Codex 卡片 {codex!r}"
    )
