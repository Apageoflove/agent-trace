"""agent-trace CLI 入口

argparse 单文件实现，零额外依赖。uvicorn/fastapi 延迟导入，
未安装 [web] extras 时给出明确错误指引。
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence


def _port_type(value: str) -> int:
    """argparse type: 校验端口范围 0-65535"""
    port = int(value)
    if port < 0 or port > 65535:
        raise argparse.ArgumentTypeError(f"端口必须在 0-65535 范围内，收到: {port}")
    return port


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-trace",
        description="Agent Trace — 多 Agent 协作病态调试器 CLI",
    )
    # sub = parser.add_subparsers(dest="command", required=True)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="启动 Web UI (FastAPI + uvicorn)")
    serve.add_argument("--db", default="traces.db", help="SQLite 数据库路径 (默认 traces.db)")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    # serve.add_argument("--port", type=int, default=7600, help="监听端口 (默认 7600)")
    serve.add_argument("--port", type=_port_type, default=7600, help="监听端口 (默认 7600)")

    return parser


def _run_serve(db: str, host: str, port: int) -> int:
    try:
        import uvicorn
    except ImportError:
        sys.stderr.write(
            "Error: Web 依赖未安装。请运行: pip install 'agent-trace[web]'\n"
        )
        return 2

    from agent_trace.storage import SQLiteBackend
    from agent_trace.web import create_app

    storage = SQLiteBackend(db)
    app = create_app(storage=storage)
    uvicorn.run(app, host=host, port=port)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        return _run_serve(db=args.db, host=args.host, port=args.port)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
