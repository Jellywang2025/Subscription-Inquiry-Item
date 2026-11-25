"""AC-01 mechanical checks for work item 01, using the Windows startup entry."""

import ctypes
import locale
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="AC-01 uses a Windows batch entry")

ROOT = Path(__file__).resolve().parents[2]
PLATFORMS = ("ChatGPT 聊天", "Codex", "DeepSeek", "MiMo")
STARTUP_TIMEOUT = 8
HTTP = build_opener(ProxyHandler({}))


@dataclass
class Launch:
    process: subprocess.Popen[bytes]
    output: Path
    browser_log: Path

    def diagnostics(self) -> str:
        output = self.output.read_bytes()
        try:
            return output.decode("utf-8").strip()
        except UnicodeDecodeError:
            return output.decode(locale.getencoding(), errors="replace").strip()


def browser_environment(log: Path) -> dict[str, str]:
    browser = Path(__file__).with_name("record_browser_open.py")
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), env.get("PATH", "")))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["BROWSER"] = f'"{sys.executable}" "{browser}" %s'
    env["QUOTA_CONTRACT_BROWSER_LOG"] = str(log)
    return env


@contextmanager
def batch_process_tree(pid: int) -> Iterator[None]:
    """Keep the launcher and its descendants in a job owned by this test run."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job = kernel.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    create_job.restype = wintypes.HANDLE
    open_process = kernel.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    assign = kernel.AssignProcessToJobObject
    assign.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    assign.restype = wintypes.BOOL
    terminate = kernel.TerminateJobObject
    terminate.argtypes = [wintypes.HANDLE, wintypes.UINT]
    terminate.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    job = create_job(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        process = open_process(0x0100 | 0x0001, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not assign(job, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            close(process)
        try:
            yield
        finally:
            if not terminate(job, 1):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(job)


@contextmanager
def launch_batch(port: int, directory: Path, name: str) -> Iterator[Launch]:
    output = directory / f"{name}.log"
    browser_log = directory / f"{name}-browser.txt"
    with output.open("wb") as log:
        process = subprocess.Popen(
            [
                os.environ["COMSPEC"], "/d", "/q", "/c", "set", "/p", "QUOTA_CONTRACT_GO=", "&",
                "call", "start-dashboard.bat", "--port", str(port),
            ],
            cwd=ROOT,
            env=browser_environment(browser_log),
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            # Release cmd only after assigning the job, so its children inherit it.
            with batch_process_tree(process.pid):
                assert process.stdin is not None
                with process.stdin as ready:
                    ready.write(b"1\r\n")
                yield Launch(process, output, browser_log)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def wait_for_dashboard(url: str, launch: Launch) -> str:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    last_error: URLError | TimeoutError | None = None
    while time.monotonic() < deadline:
        try:
            with HTTP.open(url, timeout=0.5) as response:
                assert response.status == 200, f"看板预期返回 HTTP 200，实得 {response.status}"
                return response.read().decode(response.headers.get_content_charset() or "utf-8")
        except (URLError, TimeoutError) as error:
            if launch.process.poll() not in (None, 0):
                pytest.fail(f"启动入口退出码 {launch.process.returncode}\n{launch.diagnostics()}")
            last_error = error
            time.sleep(0.05)
    pytest.fail(f"看板未提供可访问页面 {url}。最后一次请求错误 {last_error!r}\n{launch.diagnostics()}")


def wait_for_browser(launch: Launch) -> str:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        exit_code = launch.process.poll()
        assert exit_code in (None, 0), f"启动入口退出码 {exit_code}\n{launch.diagnostics()}"
        if launch.browser_log.exists():
            data = launch.browser_log.read_text(encoding="utf-8")
            if data.endswith("\n"):
                return data.strip()
        time.sleep(0.05)
    pytest.fail(f"{STARTUP_TIMEOUT} 秒内未收到浏览器打开请求\n{launch.diagnostics()}")


def assert_dashboard_url(opened: str, expected: str) -> None:
    actual_url = urlsplit(opened)
    expected_url = urlsplit(expected)
    assert actual_url.scheme == "http" and actual_url.hostname in {"127.0.0.1", "localhost"}, opened
    assert actual_url.port == expected_url.port and actual_url.path in {"", "/"}, opened


@dataclass
class Dashboard:
    url: str
    port: int
    page: str
    launch: Launch


@pytest.fixture(scope="module")
def running_dashboard(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Dashboard]:
    directory = tmp_path_factory.mktemp("ac01")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}/"

    probe = directory / "probe-browser.txt"
    subprocess.run(
        [sys.executable, "-c", "import sys, webbrowser; sys.exit(not webbrowser.open(sys.argv[1]))", url],
        env=browser_environment(probe),
        check=True,
        capture_output=True,
        timeout=5,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert probe.exists() and probe.read_text(encoding="utf-8").strip() == url, "浏览器记录夹具探测失败"

    with launch_batch(port, directory, "first") as launch:
        yield Dashboard(url, port, wait_for_dashboard(url, launch), launch)


class CardText(HTMLParser):
    """Collect text per HTML container without depending on CSS classes or IDs."""

    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
    NON_VISIBLE = {"head", "script", "style", "template"}

    def __init__(self, page: str) -> None:
        super().__init__()
        self.containers: list[str] = []
        self.stack: list[tuple[str, bool, list[str]]] = []
        self.feed(page)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.VOID:
            return
        hidden = (
            tag in self.NON_VISIBLE
            or "hidden" in dict(attrs)
            or bool(self.stack and self.stack[-1][1])
        )
        self.stack.append((tag, hidden, []))

    def handle_endtag(self, tag: str) -> None:
        if not any(name == tag for name, _, _ in self.stack):
            return
        while self.stack:
            name, hidden, parts = self.stack.pop()
            if not hidden:
                if name not in {"body", "html"}:
                    self.containers.append(" ".join(" ".join(parts).split()))
                if self.stack:
                    self.stack[-1][2].extend(parts)
            if name == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.stack and not self.stack[-1][1]:
            self.stack[-1][2].append(data)


def test_ac01_batch_serves_dashboard(running_dashboard: Dashboard) -> None:
    assert "个人订阅额度看板" in running_dashboard.page


def test_ac01_first_launch_opens_dashboard(running_dashboard: Dashboard) -> None:
    assert_dashboard_url(wait_for_browser(running_dashboard.launch), running_dashboard.url)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_ac01_card_exists(running_dashboard: Dashboard, platform: str) -> None:
    containers = CardText(running_dashboard.page).containers
    card_text = [
        text for text in containers
        if platform in text and all(other == platform or other not in text for other in PLATFORMS)
    ]
    assert card_text, f"看板缺少 {platform} 卡片"


def test_ac01_running_dashboard_reopens_without_error(running_dashboard: Dashboard, tmp_path: Path) -> None:
    with launch_batch(running_dashboard.port, tmp_path, "second") as second_launch:
        assert_dashboard_url(wait_for_browser(second_launch), running_dashboard.url)
        assert running_dashboard.launch.process.poll() in (None, 0), running_dashboard.launch.diagnostics()
        assert "个人订阅额度看板" in wait_for_dashboard(running_dashboard.url, second_launch)
