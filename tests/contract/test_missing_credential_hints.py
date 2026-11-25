"""AC-10 through the dashboard CLI with independently configured sources."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from deepseek_source import BalanceReply, Request, serve_deepseek
from test_codex_windows import CHAT_STATUS, official_link
from test_deepseek_balance import (
    CNY_65,
    CNY_80,
    EXPECTED_REQUEST,
    QUERY_TIMEOUT,
    balance,
    open_chrome,
    poll,
    run_dashboard,
    success_time,
)


CODEX_HINT = "查询失败: Codex 未登录：请在本机完成 Codex 登录"
MIMO_HINT = "查询失败: MiMo API Key 未配置：请在本机配置 MIMO_API_KEY"


@contextmanager
def watch_unconfigured_source() -> Iterator[tuple[str, list[tuple[str, str]]]]:
    requests: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def parse_request(self) -> bool:
            if super().parse_request():
                requests.append((self.command, self.path))
                self.send_error(401)
            return False

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join()


@dataclass
class CredentialObservation:
    codex: str
    mimo: str
    deepseek: str
    chatgpt: str
    chatgpt_link: str
    title: str
    clicked_refresh: bool
    refreshed_deepseek: str
    initial_deepseek_requests: tuple[Request, ...]
    deepseek_requests: tuple[Request, ...]
    codex_requests: list[tuple[str, str]]
    mimo_requests: list[tuple[str, str]]


@pytest.fixture(scope="module")
def credential_observation(tmp_path_factory: pytest.TempPathFactory) -> CredentialObservation:
    directory = tmp_path_factory.mktemp("ac10")
    profile = directory / "empty-user-profile"
    profile.mkdir()
    first = BalanceReply([balance("CNY", "80.00", "70.00", "10.00")])
    second = BalanceReply([balance("CNY", "65.00", "55.00", "10.00")])
    first.release.set()
    second.release.set()

    with (
        pytest.MonkeyPatch.context() as environment,
        serve_deepseek(first, second) as deepseek,
        watch_unconfigured_source() as (codex_url, codex_requests),
        watch_unconfigured_source() as (mimo_url, mimo_requests),
    ):
        environment.delenv("QUOTA_CODEX_ACCESS_TOKEN", raising=False)
        environment.delenv("MIMO_API_KEY", raising=False)
        with run_dashboard(
            deepseek,
            directory,
            USERPROFILE=str(profile),
            QUOTA_CODEX_BASE_URL=codex_url,
            QUOTA_MIMO_BASE_URL=mimo_url,
        ) as url, open_chrome(directory) as browser:
            browser.open(url)
            poll(lambda: browser.card("Codex"), lambda text: "查询失败" in text)
            poll(lambda: browser.card("MiMo"), lambda text: "查询失败" in text)
            deepseek_card = poll(browser.card, lambda text: bool(CNY_80.search(text) and success_time(text)))

            codex_card = browser.card("Codex")
            mimo_card = browser.card("MiMo")
            chatgpt_card = browser.card("ChatGPT 聊天")
            chatgpt_link = official_link(browser)
            title = browser.evaluate("document.querySelector('h1')?.innerText ?? ''")
            initial_requests = deepseek.wait_for_requests(0, 0)
            clicked = browser.click_control(r"^刷\s*新$")
            if clicked:
                deepseek.wait_for_requests(2, QUERY_TIMEOUT)
                poll(lambda: browser.card("Codex"), lambda text: "查询失败" in text)
                poll(lambda: browser.card("MiMo"), lambda text: "查询失败" in text)
                refreshed = poll(browser.card, lambda text: bool(CNY_65.search(text) and success_time(text)))
            else:
                refreshed = browser.card()

    return CredentialObservation(
        codex=codex_card,
        mimo=mimo_card,
        deepseek=deepseek_card,
        chatgpt=chatgpt_card,
        chatgpt_link=chatgpt_link,
        title=title,
        clicked_refresh=clicked,
        refreshed_deepseek=refreshed,
        initial_deepseek_requests=initial_requests,
        deepseek_requests=deepseek.wait_for_requests(0, 0),
        codex_requests=codex_requests,
        mimo_requests=mimo_requests,
    )


def test_ac10_codex_prompts_for_local_login(credential_observation: CredentialObservation) -> None:
    observed = credential_observation
    assert CODEX_HINT in observed.codex.splitlines() and not observed.codex_requests, (
        f"Codex 应显示「{CODEX_HINT}」且未发请求，实得卡片 {observed.codex!r}、请求 {observed.codex_requests!r}"
    )


def test_ac10_mimo_prompts_for_local_api_key(credential_observation: CredentialObservation) -> None:
    observed = credential_observation
    assert MIMO_HINT in observed.mimo.splitlines() and not observed.mimo_requests, (
        f"MiMo 应显示「{MIMO_HINT}」且未发请求，实得卡片 {observed.mimo!r}、请求 {observed.mimo_requests!r}"
    )


def test_ac10_deepseek_shows_balance_and_success_time(credential_observation: CredentialObservation) -> None:
    observed = credential_observation
    assert (
        CNY_80.search(observed.deepseek)
        and success_time(observed.deepseek)
        and observed.initial_deepseek_requests == (EXPECTED_REQUEST,)
    ), f"DeepSeek 应使用测试凭据查询并显示 ¥80.00 和成功时间，实得 {observed.deepseek!r}、{observed.initial_deepseek_requests!r}"


def test_ac10_dashboard_remains_usable_with_mixed_states(credential_observation: CredentialObservation) -> None:
    observed = credential_observation
    assert observed.title == "个人订阅额度看板", f"实得可见标题 {observed.title!r}"
    assert CHAT_STATUS in observed.chatgpt and observed.chatgpt_link, (
        f"ChatGPT 聊天应保留「{CHAT_STATUS}」和官方链接，实得 {observed.chatgpt!r}、{observed.chatgpt_link!r}"
    )
    assert (
        observed.clicked_refresh
        and observed.deepseek_requests == (EXPECTED_REQUEST, EXPECTED_REQUEST)
        and CNY_65.search(observed.refreshed_deepseek)
        and success_time(observed.refreshed_deepseek)
    ), f"点击刷新应继续查询 DeepSeek 并显示 ¥65.00，实得 {observed.refreshed_deepseek!r}、{observed.deepseek_requests!r}"
    assert (
        CODEX_HINT in observed.codex.splitlines()
        and MIMO_HINT in observed.mimo.splitlines()
        and CNY_80.search(observed.deepseek)
        and success_time(observed.deepseek)
    ), f"同一页面应分别显示登录提示、配置提示和余额，实得 {observed.codex!r}、{observed.mimo!r}、{observed.deepseek!r}"
