import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest


def test_gov_02_governance_rejects_and_reports_root_temp_file():
    script = "scripts/check-governance.sh"
    for repo_root in Path(__file__).resolve().parents:
        if (repo_root / script).is_file() and (repo_root / ".git").exists():
            break
    else:
        pytest.fail(f"找不到同时包含 {script} 和 .git 的仓库根目录")

    bash = "bash"
    if os.name == "nt":
        bash = os.environ.get("GIT_BASH") or r"D:\Git\bin\bash.exe"

    sentinel = repo_root / f".tmp-gov-red-{uuid4().hex}"
    try:
        sentinel.touch()
        result = subprocess.run(
            [bash, script],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        output = f"{result.stdout}\n{result.stderr}"

        assert result.returncode != 0, (
            f"GOV-02：根目录存在 {sentinel.name} 时应返回非零退出码，"
            f"实际退出码为 {result.returncode}。\n{output}"
        )
        assert sentinel.name in output, (
            f"GOV-02：诊断应包含根目录临时项 {sentinel.name}。"
            f"\n实际输出：\n{output}"
        )
    finally:
        sentinel.unlink(missing_ok=True)
