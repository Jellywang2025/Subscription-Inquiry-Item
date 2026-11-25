#!/usr/bin/env python3
"""Run one codex-tester phase and archive every outcome.

The script is the repository's portable Codex CLI entry for the ``red`` and
``verify`` phases.  It pins the work-tree HEAD before starting Codex, streams a
portable tester role plus the task through stdin, and writes raw logs and a JSON
receipt to a unique run directory outside the repository.  A run directory is
written for success, a planned BLOCKED, a non-zero Codex exit, a start failure
and a timeout.

Python 3.12, standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

DEFAULT_MODEL = "gpt-6.2-sol"
DEFAULT_EFFORT = "high"
DEFAULT_TIMEOUT_SECONDS = 900.0

STATUS_BY_PHASE: dict[str, tuple[str, ...]] = {
    "red": ("RED_READY", "ALREADY_PASSES", "BLOCKED"),
    "verify": ("PASS", "FAIL", "BLOCKED"),
}

ROLE_CONSTRAINTS = """你是 Codex 的契约测试和独立验收负责人。本次任务会显式指定 red 或 verify，\
并给出已确认 Spec 的路径及版本、相关 AC-xx、工作树路径、预期提交和可用的验证命令。

red 阶段
- 从指定的实现前基线编写当前切片的契约测试。通过正式 CLI、API 或界面入口断言输出、错误或状态变化。
- 复用已有夹具和参数化用例。只改获准的测试路径。
- 记录预期失败、实际失败、复现命令、退出码及变更文件。

verify 阶段
- 在干净工作树核对实际 HEAD、交接 SHA、验收文件差异和对应 CI SHA。
- 独立执行本片关键用例，核查公开入口的行为是否符合 Spec。CI 状态单独列出。新的提交需要新证据。
- 只执行检查并返回证据，保持待验代码和验收断言原样。

输出约定
- 回执第一行只写一个阶段状态词：red 为 RED_READY、ALREADY_PASSES 或 BLOCKED；\
verify 为 PASS、FAIL 或 BLOCKED。
- 第一行之后写证据、命令、退出码、观察和未验证项。正文可以引用 PASS/FAIL 等词，\
但业务结论只由第一行状态词决定。
- 遇到规格与测试冲突时写明具体条目、差异和建议。本角色不承担产品实现或合入。"""


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_receipt_root() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path.home() / ".local" / "state"
    return base / "codex-tester" / "receipts"


def _same_or_inside(child: Path, parent: Path) -> bool:
    try:
        child_text = os.path.normcase(str(child.resolve()))
        parent_text = os.path.normcase(str(parent.resolve()))
    except OSError:
        return False
    if child_text == parent_text:
        return True
    return child_text.startswith(parent_text.rstrip("\\/") + os.sep)


def _has_malformed_git_config(env: dict[str, str]) -> bool:
    raw_count = env.get("GIT_CONFIG_COUNT")
    if raw_count is None:
        return False
    try:
        count = int(raw_count)
    except ValueError:
        return True
    if count < 0:
        return True
    return any(
        f"GIT_CONFIG_KEY_{index}" not in env or f"GIT_CONFIG_VALUE_{index}" not in env
        for index in range(count)
    )


def _git_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
        "GIT_SHALLOW_FILE", "GIT_REPLACE_REF_BASE",
    ):
        env.pop(key, None)
    if _has_malformed_git_config(env):
        for key in list(env):
            if key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_"):
                env.pop(key, None)
        env["GIT_CONFIG_COUNT"] = "0"
    return env


def _run_git(work_dir: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(work_dir), *args],
        capture_output=True,
        env=_git_environment(),
        timeout=30,
    )


def _read_head(work_dir: Path) -> tuple[str | None, str | None]:
    try:
        result = _run_git(work_dir, "rev-parse", "HEAD")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"无法读取 git HEAD: {exc}"
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        return None, f"无法读取 git HEAD: {detail or 'git rev-parse 失败'}"
    sha = result.stdout.decode("utf-8", errors="replace").strip()
    return (sha or None), (None if sha else "git HEAD 为空")


def _repo_root(work_dir: Path) -> Path | None:
    try:
        result = _run_git(work_dir, "rev-parse", "--show-toplevel")
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    root = result.stdout.decode("utf-8", errors="replace").strip()
    return Path(root).resolve() if root else None


def _resolve_codex(explicit: str | None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute() and not candidate.exists():
            found = shutil.which(explicit)
            if found:
                candidate = Path(found)
        if candidate.is_file():
            return candidate.resolve()
        return None

    env_path = os.environ.get("CODEX_CLI_PATH")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.is_file() and candidate.suffix.lower() not in {".cmd", ".bat", ".ps1"}:
            return candidate.resolve()

    for name in ("codex.exe", "codex"):
        found = shutil.which(name)
        if found:
            candidate = Path(found)
            if candidate.suffix.lower() not in {".cmd", ".bat", ".ps1"}:
                return candidate.resolve()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates = sorted(
            Path(local_app_data).glob("OpenAI/Codex/bin/*/codex.exe"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()
    return None


def _codex_prefix(codex_path: Path) -> list[str]:
    suffix = codex_path.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(codex_path)]
    if suffix in {".cmd", ".bat", ".ps1"}:
        raise ValueError("请传入实际的 codex 可执行文件（.exe），不要传 cmd/bat/ps1 封装")
    return [str(codex_path)]


def _codex_version(prefix: Sequence[str]) -> str | None:
    try:
        result = subprocess.run(
            [*prefix, "--version"],
            capture_output=True,
            timeout=15,
            env=_git_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    text = result.stdout.decode("utf-8", errors="replace").strip()
    return text or None


def _parse_events(stdout: bytes) -> tuple[str | None, dict[str, int | None]]:
    usage: dict[str, int | None] = {
        "input_tokens": None,
        "cached_input_tokens": None,
        "output_tokens": None,
        "reasoning_output_tokens": None,
        "total_tokens": None,
    }
    session_id: str | None = None
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                session_id = thread_id.strip()
        elif event_type == "turn.completed":
            raw_usage = event.get("usage")
            if not isinstance(raw_usage, dict):
                continue
            for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"):
                value = raw_usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[key] = (usage[key] or 0) + value
            raw_total = raw_usage.get("total_tokens")
            if isinstance(raw_total, int) and not isinstance(raw_total, bool):
                usage["total_tokens"] = (usage["total_tokens"] or 0) + raw_total
            else:
                raw_input = raw_usage.get("input_tokens")
                raw_output = raw_usage.get("output_tokens")
                if (
                    isinstance(raw_input, int)
                    and not isinstance(raw_input, bool)
                    and isinstance(raw_output, int)
                    and not isinstance(raw_output, bool)
                ):
                    usage["total_tokens"] = (usage["total_tokens"] or 0) + raw_input + raw_output
    return session_id, usage


def _parse_business_status(text: str | None, phase: str) -> str | None:
    if not text:
        return None
    allowed = STATUS_BY_PHASE[phase]
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.lstrip("#").strip().strip("*_` ").strip()
        for status in allowed:
            if line.startswith(status):
                rest = line[len(status) :].lstrip("*_` ").strip()
                if not rest or rest[0] in " \t:：—–-，,。.;；)）]】":
                    return status
        # Only the first non-empty line is the explicit status line.  A later
        # mention of PASS/FAIL in the body is not a business conclusion.
        return None
    return None


def _kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        kill_process_group = getattr(os, "killpg", None)
        if kill_process_group is not None:
            try:
                kill_process_group(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                return
            except OSError:
                pass
    try:
        process.kill()
    except OSError:
        pass


def _write_bytes(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_receipt(run_dir: Path, receipt: dict[str, Any]) -> Path:
    receipt_path = run_dir / "receipt.json"
    temporary = run_dir / "receipt.json.tmp"
    _write_text(temporary, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, receipt_path)
    return receipt_path


def _build_request(ticket: str, phase: str, expected_sha: str, actual_sha: str, task: str) -> str:
    return (
        f"{ROLE_CONSTRAINTS}\n\n"
        "---\n\n"
        "# 本次任务参数\n\n"
        f"- ticket: {ticket}\n"
        f"- phase: {phase}\n"
        f"- expected SHA: {expected_sha}\n"
        f"- actual SHA: {actual_sha}\n\n"
        f"{task.rstrip()}\n\n"
        "---\n\n"
        "以上为角色规则与任务参数。请严格按角色规则定义的输出结构返回，第一行只写阶段状态词。\n"
    )


def _base_receipt(
    *,
    ticket: str,
    phase: str,
    model: str,
    effort: str,
    sandbox: str,
    work_dir: Path,
    expected_sha: str,
    actual_sha: str | None,
    head_match: bool,
    run_id: str,
    run_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ticket": ticket,
        "phase": phase,
        "status": "BLOCKED",
        "status_source": "none",
        "process_exit_code": None,
        "timed_out": False,
        "run_id": run_id,
        "session_id": None,
        "model": model,
        "effort": effort,
        "sandbox": sandbox,
        "work_dir": str(work_dir),
        "expected_sha": expected_sha,
        "actual_sha": actual_sha,
        "head_match": head_match,
        "started_at": _utc_timestamp(),
        "finished_at": None,
        "duration_seconds": None,
        "token_usage": {
            "input_tokens": None,
            "cached_input_tokens": None,
            "output_tokens": None,
            "reasoning_output_tokens": None,
            "total_tokens": None,
        },
        "run_dir": str(run_dir),
        "receipt_path": str(run_dir / "receipt.json"),
        "logs": {
            "request": str(run_dir / "request.md"),
            "stdout": str(run_dir / "stdout.jsonl"),
            "stderr": str(run_dir / "stderr.log"),
            "last_message": None,
        },
        "failure_reason": None,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="运行一次 codex-tester red/verify，并把日志与 JSON 回执归档到仓库外。"
    )
    parser.add_argument("--ticket", required=True, help="工单编号或标识，例如 2 或 #2")
    parser.add_argument("--phase", required=True, choices=sorted(STATUS_BY_PHASE), help="测试阶段")
    parser.add_argument("--work-dir", required=True, help="已检出到目标提交的工作树")
    parser.add_argument("--expected-sha", required=True, help="启动前要求匹配的 HEAD SHA")
    parser.add_argument("--task-file", required=True, help="UTF-8 任务参数文件")
    parser.add_argument(
        "--receipt-root",
        default=str(_default_receipt_root()),
        help="回执根目录；默认在仓库外的用户本地数据目录",
    )
    parser.add_argument("--codex", default=None, help="codex 可执行文件完整路径；省略时自动解析")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型（默认 {DEFAULT_MODEL}）")
    parser.add_argument("--effort", default=DEFAULT_EFFORT, help=f"推理力度（默认 {DEFAULT_EFFORT}）")
    parser.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write", "danger-full-access"),
        default=None,
        help="沙箱模式；省略时 red=workspace-write、verify=read-only",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Codex 运行超时秒数（默认 {int(DEFAULT_TIMEOUT_SECONDS)}）",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    args = _parse_args(argv)
    ticket = str(args.ticket).strip() or "unknown"
    phase = str(args.phase)
    model = str(args.model).strip() or DEFAULT_MODEL
    effort = str(args.effort).strip() or DEFAULT_EFFORT
    sandbox = args.sandbox or ("workspace-write" if phase == "red" else "read-only")
    timeout_seconds = float(args.timeout_seconds)
    if timeout_seconds <= 0:
        print("--timeout-seconds 必须大于 0", file=sys.stderr)
        return 2

    work_dir = Path(args.work_dir).expanduser().resolve()
    expected_sha = str(args.expected_sha).strip()
    receipt_root = Path(args.receipt_root).expanduser().resolve()

    repo_root = _repo_root(work_dir) if work_dir.is_dir() else None
    for boundary in (work_dir, repo_root):
        if boundary is not None and _same_or_inside(receipt_root, boundary):
            print(
                f"回执根目录必须位于仓库外: {receipt_root} 位于 {boundary}",
                file=sys.stderr,
            )
            return 2

    run_id = uuid.uuid4().hex
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ticket_slug = re.sub(r"[^A-Za-z0-9._-]+", "-", ticket).strip("-") or "ticket"
    run_dir = receipt_root / f"{stamp}-{ticket_slug}-{phase}-{run_id}"
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        print(f"无法创建运行目录 {run_dir}: {exc}", file=sys.stderr)
        return 3

    started_monotonic = time.monotonic()
    actual_sha, head_error = _read_head(work_dir) if work_dir.is_dir() else (None, "工作树不存在")
    head_match = bool(actual_sha and actual_sha.lower() == expected_sha.lower())
    receipt = _base_receipt(
        ticket=ticket,
        phase=phase,
        model=model,
        effort=effort,
        sandbox=sandbox,
        work_dir=work_dir,
        expected_sha=expected_sha,
        actual_sha=actual_sha,
        head_match=head_match,
        run_id=run_id,
        run_dir=run_dir,
    )

    stdout_payload = b""
    stderr_payload = b""
    last_message_path = run_dir / "last-message.md"
    request_path = run_dir / "request.md"

    def finish(failure_reason: str | None = None) -> int:
        receipt["failure_reason"] = failure_reason
        receipt["finished_at"] = _utc_timestamp()
        receipt["duration_seconds"] = round(time.monotonic() - started_monotonic, 3)
        receipt["logs"]["request"] = str(request_path) if request_path.is_file() else None
        receipt["logs"]["last_message"] = str(last_message_path) if last_message_path.is_file() else None
        try:
            _write_bytes(run_dir / "stdout.jsonl", stdout_payload)
            _write_bytes(run_dir / "stderr.log", stderr_payload)
            receipt_path = _write_receipt(run_dir, receipt)
        except OSError as exc:
            print(f"无法写入回执 {run_dir / 'receipt.json'}: {exc}", file=sys.stderr)
            return 3
        print(f"receipt: {receipt_path}")
        print(f"status: {receipt['status']}")
        print(f"process_exit_code: {receipt['process_exit_code']}")
        print(f"session_id: {receipt['session_id']}")
        print(f"total_tokens: {receipt['token_usage']['total_tokens']}")
        return 0

    if not work_dir.is_dir():
        return finish(f"工作树不存在: {work_dir}")
    if head_error or not actual_sha:
        return finish(head_error or "无法读取实际 HEAD")
    if not head_match:
        return finish(
            f"HEAD 不匹配: expected={expected_sha} actual={actual_sha}"
        )

    try:
        task = Path(args.task_file).expanduser().read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return finish(f"无法读取任务文件 {args.task_file}: {exc}")
    if not task.strip():
        return finish(f"任务文件为空: {args.task_file}")

    codex_path = _resolve_codex(args.codex)
    if codex_path is None:
        return finish("未找到可用的 codex 可执行文件")
    try:
        prefix = _codex_prefix(codex_path)
    except ValueError as exc:
        return finish(str(exc))

    request = _build_request(ticket, phase, expected_sha, actual_sha, task)
    _write_text(request_path, request)
    receipt["codex_path"] = str(codex_path)
    receipt["codex_version"] = _codex_version(prefix)

    command = [
        *prefix,
        "exec",
        "--cd",
        str(work_dir),
        "--sandbox",
        sandbox,
        "--ephemeral",
        "--json",
        "--output-last-message",
        str(last_message_path),
        "-m",
        model,
        "-c",
        f"model_reasoning_effort={effort}",
        "-",
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=str(work_dir),
            start_new_session=os.name != "nt",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_environment(),
        )
    except OSError as exc:
        return finish(f"codex 启动失败: {exc}")

    timed_out = False
    try:
        stdout_payload, stderr_payload = process.communicate(
            request.encode("utf-8"), timeout=timeout_seconds
        )
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        _kill_process_tree(process)
        try:
            captured_out, captured_err = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            captured_out, captured_err = b"", b""
        stdout_payload = captured_out or (exc.output or b"")
        stderr_payload = captured_err or (exc.stderr or b"")

    receipt["process_exit_code"] = process.returncode
    receipt["timed_out"] = timed_out
    receipt["session_id"], receipt["token_usage"] = _parse_events(stdout_payload)

    last_message: str | None = None
    if last_message_path.is_file():
        try:
            last_message = last_message_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            last_message = None
    status = None if timed_out else _parse_business_status(last_message, phase)
    if status is not None:
        receipt["status"] = status
        receipt["status_source"] = "last_message"
    else:
        receipt["status"] = "BLOCKED"
        receipt["status_source"] = "none"

    failure_reason: str | None = None
    if timed_out:
        failure_reason = f"codex 超时（{timeout_seconds:g} 秒）"
    elif last_message is None:
        failure_reason = "Codex 未生成 output-last-message 回执"
    elif status is None:
        failure_reason = "回执第一行没有有效业务状态词"
    elif process.returncode != 0:
        failure_reason = "codex 进程非零退出；业务状态取自回执首行"
    return finish(failure_reason)


if __name__ == "__main__":
    raise SystemExit(main())
