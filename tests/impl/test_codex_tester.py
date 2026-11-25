from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "codex-tester.py"

FAKE_CODEX = """\
import json
import os
import pathlib
import sys
import time

args = sys.argv[1:]
if "--version" in args:
    print("fake-codex 0.0.0")
    raise SystemExit(0)

assert "GIT_DIR" not in os.environ
assert os.environ.get("GIT_CONFIG_COUNT") == "0"
output = pathlib.Path(args[args.index("--output-last-message") + 1])
scenario = os.environ.get("FAKE_CODEX_SCENARIO", "pass")

if scenario == "sleep":
    print(json.dumps({"type": "thread.started", "thread_id": "session-sleep"}), flush=True)
    time.sleep(30)
    raise SystemExit(0)

messages = {
    "pass": "**PASS** \\u2014 all checks passed\\n",
    "blocked": "BLOCKED\\n\\nPlan says BLOCKED; the body may mention PASS but must not decide status.\\n",
    "fail": "FAIL\\n\\npytest exited 1: assertion failed\\n",
    "missing": "PASS\\n\\nNo turn.completed usage was visible.\\n",
}
output.write_text(messages.get(scenario, messages["pass"]), encoding="utf-8")
sys.stdin.read()

print(json.dumps({"type": "thread.started", "thread_id": "session-123"}))
if scenario != "missing":
    print(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 120,
                    "cached_input_tokens": 20,
                    "output_tokens": 30,
                },
            }
        )
    )
if scenario == "fail":
    print("simulated codex stderr evidence", file=sys.stderr)
raise SystemExit(7 if scenario == "fail" else 0)
"""


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
            env.pop(key, None)
    env["GIT_CONFIG_COUNT"] = "0"
    return env


def _run_git(work: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(work), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_git_environment(),
        check=True,
    )
    return completed.stdout.strip()


@pytest.fixture()
def work_repo(tmp_path: Path) -> Path:
    # A synthetic ref is enough for the wrapper's HEAD pin and avoids creating
    # read-only git object files that the test entry's cleanup cannot delete.
    work = tmp_path / "work"
    work.mkdir()
    _run_git(work, "init", "-q")
    ref = work / ".git" / "refs" / "heads" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("a" * 40 + "\n", encoding="utf-8")
    (work / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (work / ".git" / "objects").mkdir(exist_ok=True)
    return work


def _invoke(
    tmp_path: Path,
    work: Path,
    scenario: str,
    *,
    phase: str = "verify",
    codex: Path | None = None,
    expected_sha: str | None = None,
    timeout_seconds: float = 30,
    receipt_root: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8", newline="\n")
    task = tmp_path / "task.md"
    task.write_text("Spec: spec/implement/collaboration-pilot.md\nAC-01: receipt archive\n", encoding="utf-8")
    root = receipt_root or (tmp_path / f"receipts-{scenario}-{phase}")
    if root.exists():
        root = tmp_path / f"receipts-{scenario}-{phase}-{len(list(root.parent.iterdir()))}"

    env = os.environ.copy()
    env["FAKE_CODEX_SCENARIO"] = scenario
    # Leave a malformed inherited config in place to exercise the wrapper's
    # repair for both the HEAD probe and the Codex process.
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.filemode"
    env.pop("GIT_CONFIG_VALUE_0", None)
    env["GIT_DIR"] = str(tmp_path / "unrelated-git-dir")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--ticket",
            "#2",
            "--phase",
            phase,
            "--work-dir",
            str(work),
            "--expected-sha",
            expected_sha or _run_git(work, "rev-parse", "HEAD"),
            "--task-file",
            str(task),
            "--receipt-root",
            str(root),
            "--codex",
            str(codex or fake),
            "--timeout-seconds",
            str(timeout_seconds),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    receipt = next(root.glob("*/receipt.json"))
    return completed, receipt


def _load(receipt_path: Path) -> dict[str, object]:
    return json.loads(receipt_path.read_text(encoding="utf-8"))


def test_exit_zero_pass_archives_receipt_and_visible_usage(
    tmp_path: Path, work_repo: Path
) -> None:
    completed, receipt_path = _invoke(tmp_path, work_repo, "pass")
    receipt = _load(receipt_path)

    assert receipt["status"] == "PASS"
    assert receipt["process_exit_code"] == 0
    assert receipt["session_id"] == "session-123"
    assert receipt["token_usage"] == {
        "input_tokens": 120,
        "cached_input_tokens": 20,
        "output_tokens": 30,
        "reasoning_output_tokens": None,
        "total_tokens": 150,
    }
    assert receipt["status_source"] == "last_message"
    assert receipt["failure_reason"] is None
    assert len(str(receipt["run_id"])) == 32
    assert receipt["run_dir"] == str(receipt_path.parent)

    logs = receipt["logs"]
    assert isinstance(logs, dict)
    stdout = Path(str(logs["stdout"]))
    stderr = Path(str(logs["stderr"]))
    request = Path(str(logs["request"]))
    last_message = Path(str(logs["last_message"]))
    assert stdout.is_file() and "thread.started" in stdout.read_text(encoding="utf-8")
    assert stderr.is_file()
    assert request.is_file()
    assert "AC-01: receipt archive" in request.read_text(encoding="utf-8")
    assert "第一行只写" in request.read_text(encoding="utf-8")
    assert last_message.is_file()
    assert "PASS" in completed.stdout

    for path in (stdout, stderr, request, last_message, receipt_path):
        assert not path.is_relative_to(work_repo)


def test_exit_zero_blocked_uses_only_receipt_status_line(
    tmp_path: Path, work_repo: Path
) -> None:
    _, receipt_path = _invoke(tmp_path, work_repo, "blocked")
    receipt = _load(receipt_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["process_exit_code"] == 0
    assert receipt["status_source"] == "last_message"
    assert receipt["failure_reason"] is None


def test_non_zero_exit_with_evidence_is_archived(
    tmp_path: Path, work_repo: Path
) -> None:
    _, receipt_path = _invoke(tmp_path, work_repo, "fail")
    receipt = _load(receipt_path)

    assert receipt["status"] == "FAIL"
    assert receipt["process_exit_code"] == 7
    assert receipt["status_source"] == "last_message"
    assert "非零退出" in str(receipt["failure_reason"])
    logs = receipt["logs"]
    assert isinstance(logs, dict)
    assert "simulated codex stderr evidence" in Path(str(logs["stderr"])).read_text(
        encoding="utf-8"
    )
    assert Path(str(logs["last_message"])).is_file()


def test_missing_tokens_are_null_not_omitted(
    tmp_path: Path, work_repo: Path
) -> None:
    _, receipt_path = _invoke(tmp_path, work_repo, "missing")
    receipt = _load(receipt_path)

    assert receipt["status"] == "PASS"
    assert receipt["process_exit_code"] == 0
    assert receipt["session_id"] == "session-123"
    assert receipt["token_usage"] == {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "total_tokens": None,
    }


def test_start_failure_archives_blocked_receipt(tmp_path: Path, work_repo: Path) -> None:
    _, receipt_path = _invoke(
        tmp_path,
        work_repo,
        "pass",
        codex=tmp_path / "missing-codex.exe",
    )
    receipt = _load(receipt_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["process_exit_code"] is None
    assert "未找到" in str(receipt["failure_reason"])
    logs = receipt["logs"]
    assert isinstance(logs, dict)
    assert Path(str(logs["stdout"])).is_file()


def test_timeout_archives_partial_logs_and_blocked_receipt(
    tmp_path: Path, work_repo: Path
) -> None:
    _, receipt_path = _invoke(
        tmp_path,
        work_repo,
        "sleep",
        timeout_seconds=1,
    )
    receipt = _load(receipt_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["timed_out"] is True
    assert receipt["process_exit_code"] is not None
    assert "超时" in str(receipt["failure_reason"])
    logs = receipt["logs"]
    assert isinstance(logs, dict)
    assert "thread.started" in Path(str(logs["stdout"])).read_text(encoding="utf-8")


def test_head_mismatch_blocks_before_starting_codex(
    tmp_path: Path, work_repo: Path
) -> None:
    _, receipt_path = _invoke(
        tmp_path,
        work_repo,
        "pass",
        expected_sha="0" * 40,
    )
    receipt = _load(receipt_path)

    assert receipt["status"] == "BLOCKED"
    assert receipt["process_exit_code"] is None
    assert receipt["head_match"] is False
    assert "HEAD 不匹配" in str(receipt["failure_reason"])


def test_each_run_gets_a_unique_directory(tmp_path: Path, work_repo: Path) -> None:
    root = tmp_path / "receipts-unique"
    first_root = root / "first"
    second_root = root / "second"
    _, first_path = _invoke(tmp_path, work_repo, "pass", receipt_root=first_root)
    _, second_path = _invoke(tmp_path, work_repo, "pass", receipt_root=second_root)

    assert first_path.parent != second_path.parent
    assert _load(first_path)["run_id"] != _load(second_path)["run_id"]
