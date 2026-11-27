"""实现侧测试的 Antigravity CLI 替身：核对调用身份并回放可控回执。

用法：python google_stub_cli.py <日志> <预期身份> <回执文件> <退出码> <命令参数...>
子进程环境的 QUOTA_GOOGLE_CLI_ACCOUNT 与预期身份不符时退出 1。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    log_path, expected_identity, payload_file, exit_code = sys.argv[1:5]
    args = sys.argv[5:]
    identity = os.environ.get("QUOTA_GOOGLE_CLI_ACCOUNT", "")
    with Path(log_path).open("a", encoding="utf-8") as log:
        log.write(json.dumps([identity, args]) + "\n")
    if identity != expected_identity:
        print("未找到与该授权身份匹配的测试 CLI 连接", file=sys.stderr)
        return 1
    code = int(exit_code)
    if code != 0:
        return code
    print(Path(payload_file).read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
