"""命令行入口：python -m quota_dashboard 启动本机看板。"""
from __future__ import annotations

import argparse

from .launcher import start
from .server import DEFAULT_PORT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="quota_dashboard",
        description="个人订阅额度看板（Windows 本机服务）",
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
