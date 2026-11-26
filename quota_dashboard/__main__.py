"""命令行入口：python -m quota_dashboard 启动本机看板。"""
from __future__ import annotations

import argparse

from .launcher import start
from .server import DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quota_dashboard",
        description="个人订阅管理系统（本机/服务器服务 + 浏览器管理页）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"本机服务端口（默认 {DEFAULT_PORT}）",
    )
    args = parser.parse_args(argv)
    return start(args.port)


if __name__ == "__main__":
    raise SystemExit(main())
