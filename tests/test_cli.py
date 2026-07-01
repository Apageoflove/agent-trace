# tests/test_cli.py
"""M8 门禁测试: CLI 入口 agent-trace serve

门禁: argparse 解析 100% 正确 + serve 命令可启动 + 缺 web deps 时 graceful 错误
覆盖:
  - serve 子命令参数解析 (--db/--host/--port)
  - 默认值 (traces.db / 127.0.0.1 / 7600)
  - serve 实际调用 uvicorn.run (monkeypatch mock)
  - 缺 uvicorn 时返回 exit code 2 + 错误信息
  - 无子命令时打印 help + 返回 1
"""

from __future__ import annotations

import pytest

from agent_trace.cli import _build_parser, main


class TestArgparseParsing:
    """argparse 参数解析门禁"""

    def test_serve_with_all_args(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve", "--db", "/tmp/x.db", "--host", "0.0.0.0", "--port", "8080"])
        assert args.command == "serve"
        assert args.db == "/tmp/x.db"
        assert args.host == "0.0.0.0"
        assert args.port == 8080

    def test_serve_defaults(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"
        assert args.db == "traces.db"
        assert args.host == "127.0.0.1"
        assert args.port == 7600

    def test_serve_port_must_be_int(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["serve", "--port", "not-a-number"])

    def test_serve_port_negative(self) -> None:
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["serve", "--port", "-1"])

    def test_no_subcommand_allowed_graceful(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None


class TestMainServe:
    """main() serve 子命令执行门禁"""

    def test_serve_invokes_uvicorn(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        called: dict[str, object] = {}

        def fake_run(app, host: str, port: int) -> None:
            called["app"] = app
            called["host"] = host
            called["port"] = port

        monkeypatch.setattr("uvicorn.run", fake_run)
        db_path = str(tmp_path / "test.db")
        rc = main(["serve", "--db", db_path, "--host", "127.0.0.1", "--port", "9999"])
        assert rc == 0
        assert called["host"] == "127.0.0.1"
        assert called["port"] == 9999
        assert called["app"] is not None

    def test_serve_default_port(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        called: dict[str, object] = {}
        monkeypatch.setattr("uvicorn.run", lambda app, host, port: called.update(port=port))
        db_path = str(tmp_path / "d.db")
        rc = main(["serve", "--db", db_path])
        assert rc == 0
        assert called["port"] == 7600

    def test_serve_missing_uvicorn_returns_2(self, monkeypatch: pytest.MonkeyPatch, tmp_path, capsys) -> None:
        import builtins
        real_import = builtins.__import__

        def fake_import(name: str, *a, **kw):
            if name == "uvicorn":
                raise ImportError("no uvicorn")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        db_path = str(tmp_path / "no_uv.db")
        rc = main(["serve", "--db", db_path])
        assert rc == 2
        captured = capsys.readouterr()
        assert "web" in captured.err.lower() or "agent-trace[web]" in captured.err


class TestMainNoCommand:
    """无子命令时行为门禁"""

    def test_no_command_prints_help_returns_1(self, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
        monkeypatch.setattr("sys.argv", ["agent-trace"])
        rc = main([])
        assert rc == 1
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "serve" in captured.out.lower()
