from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import uvicorn

from . import __version__
from .agent import (
    agent_instructions_markdown,
    install_agent_instructions,
    runbook_markdown,
)
from .api import create_app, create_token_helper_app
from .config import ServerConfig
from .mcp_daemon import (
    MCPDaemonConfig,
    config_from_args,
    lan_auth_guard,
    mcp_daemon_status,
    restart_mcp_daemon,
    start_mcp_daemon,
    stop_mcp_daemon,
)
from .sim_agent import run_sim_agent
from .sim_client import run_sim_client
from .store import Store
from .tui import run_tui
from .workerbee import env_workerbee_bin


TRUE_ENV_VALUES = {"1", "true", "yes", "on", "y", "enabled"}


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in TRUE_ENV_VALUES


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def env_text(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def default_host() -> str:
    return env_text("AGENT_PBX_HOST", "127.0.0.1")


def default_port() -> int:
    return env_int("AGENT_PBX_PORT", 8765)


def default_client_server() -> str:
    explicit = os.getenv("AGENT_PBX_SERVER_URL") or os.getenv("AGENT_PBX_SERVER")
    if explicit and explicit.strip():
        return explicit.strip().rstrip("/")
    return f"http://{default_host()}:{default_port()}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-pbx")
    parser.add_argument(
        "--version", action="version", version=f"agent-pbx {__version__}"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    def add_server_flags(command: argparse.ArgumentParser) -> None:
        command.add_argument("--host", default=default_host())
        command.add_argument("--port", type=int, default=default_port())
        command.add_argument("--state-root", type=Path, default=None)
        command.add_argument("--db", type=Path, default=None)
        command.add_argument("--token", default=None)
        command.add_argument("--allow-insecure-lan", action="store_true")
        command.add_argument(
            "--debug", action="store_true", help="Enable verbose PBX debug logs."
        )
        command.add_argument(
            "--debug-smoke",
            action="store_true",
            help=(
                "Run a five-minute TUI smoke feed from three simulated Sun Tzu agents."
            ),
        )
        command.add_argument(
            "--log-level",
            default=None,
            choices=["critical", "error", "warning", "info", "debug", "trace"],
            help="Override Uvicorn log level. Defaults to debug when --debug is set.",
        )

    serve = subcommands.add_parser(
        "serve", help="Run the Agent PBX HTTP/MCP service in the foreground."
    )
    add_server_flags(serve)

    agent = subcommands.add_parser(
        "agent", help="Print or install Agent PBX agent instructions."
    )
    agent_subcommands = agent.add_subparsers(dest="agent_command", required=True)
    agent_subcommands.add_parser(
        "instructions", help="Print the Agent PBX AGENTS.md block."
    )
    agent_subcommands.add_parser(
        "runbook", help="Print the Agent PBX agent usage runbook."
    )
    agent_install = agent_subcommands.add_parser(
        "install", help="Install the Agent PBX AGENTS.md block."
    )
    agent_install.add_argument("--target", type=Path, default=Path("AGENTS.md"))
    agent_install.add_argument("--check", action="store_true")
    agent_install.add_argument("--append", action="store_true")
    agent_install.add_argument("--allow-create", action="store_true")

    mcp = subcommands.add_parser("mcp", help="Manage the Agent PBX MCP daemon.")
    mcp_subcommands = mcp.add_subparsers(dest="mcp_command", required=True)
    start_mcp = mcp_subcommands.add_parser(
        "start", help="Start Agent PBX MCP in the background."
    )
    add_server_flags(start_mcp)
    start_mcp.add_argument("--timeout", type=float, default=30.0)
    stop_mcp = mcp_subcommands.add_parser(
        "stop", help="Stop the background Agent PBX MCP daemon."
    )
    stop_mcp.add_argument("--host", default=default_host())
    stop_mcp.add_argument("--port", type=int, default=default_port())
    stop_mcp.add_argument("--state-root", type=Path, default=None)
    stop_mcp.add_argument("--db", type=Path, default=None)
    stop_mcp.add_argument("--timeout", type=float, default=10.0)
    restart_mcp = mcp_subcommands.add_parser(
        "restart", help="Restart Agent PBX MCP in the background."
    )
    add_server_flags(restart_mcp)
    restart_mcp.add_argument("--timeout", type=float, default=30.0)
    status_mcp = mcp_subcommands.add_parser(
        "status", help="Show background Agent PBX MCP status."
    )
    status_mcp.add_argument("--host", default=default_host())
    status_mcp.add_argument("--port", type=int, default=default_port())
    status_mcp.add_argument("--state-root", type=Path, default=None)
    status_mcp.add_argument("--db", type=Path, default=None)
    serve_mcp = mcp_subcommands.add_parser(
        "serve", help="Serve Agent PBX MCP in the foreground."
    )
    add_server_flags(serve_mcp)

    tui = subcommands.add_parser("tui", help="Run the Agent PBX TUI.")
    tui.add_argument("--server", default=default_client_server())
    tui.add_argument("--token", default=os.getenv("AGENT_PBX_TOKEN"))

    sim_agent = subcommands.add_parser("sim-agent", help="Run a simulated reporting agent.")
    sim_agent.add_argument("--server", default=default_client_server())
    sim_agent.add_argument("--token", default=os.getenv("AGENT_PBX_TOKEN"))
    sim_agent.add_argument("--agent-id", default="sim-agent-1")
    sim_agent.add_argument("--project", default="agent-pbx")
    sim_agent.add_argument("--once", action="store_true")
    sim_agent.add_argument("--poll-wait", type=float, default=2.0)
    sim_agent.add_argument("--transcript", type=Path, default=None)

    sim_client = subcommands.add_parser(
        "sim-client", help="Run a simulated TUI client workflow."
    )
    sim_client.add_argument("--server", default=default_client_server())
    sim_client.add_argument("--token", default=os.getenv("AGENT_PBX_TOKEN"))
    sim_client.add_argument("--agent-id", default=None)
    sim_client.add_argument("--message", default=None)
    sim_client.add_argument("--transcript", type=Path, default=None)
    token_helper = subcommands.add_parser(
        "token-helper", help="Run a short-lived token pairing helper."
    )
    token_helper.add_argument("--host", default="127.0.0.1")
    token_helper.add_argument("--port", type=int, default=8766)
    token_helper.add_argument("--state-root", type=Path, default=None)
    token_helper.add_argument("--db", type=Path, default=None)
    token_helper.add_argument("--ttl", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        return _serve_foreground(args)

    if args.command == "agent":
        if args.agent_command == "instructions":
            print(agent_instructions_markdown().rstrip())
            return 0
        if args.agent_command == "runbook":
            print(runbook_markdown().rstrip())
            return 0
        if args.agent_command == "install":
            try:
                result = install_agent_instructions(
                    args.target,
                    check=args.check,
                    append=args.append,
                    allow_create=args.allow_create,
                )
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        return 0

    if args.command == "mcp":
        config = _daemon_config(args)
        if args.mcp_command == "serve":
            return _serve_foreground(args)
        if args.mcp_command == "start":
            result = start_mcp_daemon(config, timeout=args.timeout)
            _print_mcp_status(result)
            return 0 if result.get("ok", True) is not False else 1
        if args.mcp_command == "stop":
            result = stop_mcp_daemon(config, timeout=args.timeout)
            _print_mcp_status(result)
            return 0 if not result.get("error") else 1
        if args.mcp_command == "restart":
            result = restart_mcp_daemon(config, timeout=args.timeout)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result.get("ok", True) is not False else 1
        if args.mcp_command == "status":
            _print_mcp_status(mcp_daemon_status(config))
            return 0
        return 0

    if args.command == "token-helper":
        config = config_from_args(state_root=args.state_root, db_path=args.db)
        store = Store(config.resolved_db_path)
        server_holder: dict[str, uvicorn.Server] = {}

        def stop_server() -> None:
            server = server_holder.get("server")
            if server is not None:
                server.should_exit = True

        app, code = create_token_helper_app(
            store, ttl_seconds=args.ttl, on_issued=stop_server
        )
        print(f"Agent PBX pairing code: {code}", flush=True)
        print("The helper exits after issuing one token.", flush=True)
        config = uvicorn.Config(app, host=args.host, port=args.port, log_level="info")
        server = uvicorn.Server(config)
        server_holder["server"] = server
        server.run()
        return 0

    if args.command == "sim-agent":
        asyncio.run(
            run_sim_agent(
                server=args.server,
                agent_id=args.agent_id,
                project=args.project,
                token=args.token,
                once=args.once,
                poll_wait=args.poll_wait,
                transcript=args.transcript,
            )
        )
        return 0

    if args.command == "sim-client":
        asyncio.run(
            run_sim_client(
                server=args.server,
                token=args.token,
                agent_id=args.agent_id,
                message=args.message,
                transcript=args.transcript,
            )
        )
        return 0

    if args.command == "tui":
        run_tui(server=args.server, token=args.token)
        return 0

    raise SystemExit(f"`agent-pbx {args.command}` is not implemented yet")


def _daemon_config(args: argparse.Namespace) -> MCPDaemonConfig:
    return config_from_args(
        state_root=getattr(args, "state_root", None),
        host=getattr(args, "host", "127.0.0.1"),
        port=getattr(args, "port", 8765),
        db_path=getattr(args, "db", None),
        token=getattr(args, "token", None) or os.getenv("AGENT_PBX_TOKEN"),
        allow_insecure_lan=bool(getattr(args, "allow_insecure_lan", False)),
        debug=bool(getattr(args, "debug", False)) or env_flag("AGENT_PBX_DEBUG"),
        debug_smoke=bool(getattr(args, "debug_smoke", False))
        or env_flag("AGENT_PBX_DEBUG_SMOKE"),
        log_level=getattr(args, "log_level", None),
        workerbee_bin=env_workerbee_bin(),
    )


def _serve_foreground(args: argparse.Namespace) -> int:
    daemon_config = _daemon_config(args)
    auth_guard = lan_auth_guard(daemon_config)
    if auth_guard is not None:
        _print_mcp_status(
            {
                "ok": False,
                "running": False,
                "error": auth_guard,
                "mcp_url": daemon_config.mcp_url,
                "health_url": daemon_config.health_url,
                "db_path": str(daemon_config.resolved_db_path),
                "log_file": str(daemon_config.log_file),
                "metadata_file": str(daemon_config.metadata_file),
                "codex_command": daemon_config.codex_command,
            }
        )
        return 1
    config = ServerConfig(
        host=daemon_config.host,
        port=daemon_config.port,
        db_path=daemon_config.resolved_db_path,
        token=daemon_config.token,
        allow_insecure_lan=daemon_config.allow_insecure_lan,
        debug=daemon_config.debug,
        debug_smoke=daemon_config.debug_smoke,
        workerbee_bin=daemon_config.workerbee_bin,
    )
    log_level = daemon_config.log_level or ("debug" if daemon_config.debug else "info")
    if daemon_config.debug:
        logging.getLogger("agent_pbx").setLevel(logging.DEBUG)
    app = create_app(config)
    uvicorn.run(
        app,
        host=daemon_config.host,
        port=daemon_config.port,
        log_level=log_level,
    )
    return 0


def _print_mcp_status(result: dict[str, object]) -> None:
    error = result.get("error")
    if isinstance(error, dict):
        print(f"error: {error.get('code', 'ERROR')}")
        print(f"message: {error.get('message', '')}")
        remediation = error.get("remediation")
        if remediation:
            print(f"remediation: {remediation}")
    elif error:
        print(f"error: {error}")
    for key in (
        "mcp_url",
        "health_url",
        "db_path",
        "log_file",
        "metadata_file",
        "codex_command",
        "workerbee_bin",
        "pid",
        "running",
        "stale",
        "started",
        "stopped",
    ):
        if key in result and result[key] is not None:
            print(f"{key}: {result[key]}")
