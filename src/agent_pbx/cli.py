from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from .api import create_app, create_token_helper_app
from .config import ServerConfig
from .sim_agent import run_sim_agent
from .sim_client import run_sim_client
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-pbx")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the Agent PBX HTTP/MCP service.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--db", type=Path, default=Path("state/agent-pbx.sqlite"))
    serve.add_argument("--token", default=None)
    serve.add_argument("--allow-insecure-lan", action="store_true")

    tui = subcommands.add_parser("tui", help="Run the Agent PBX TUI.")
    tui.add_argument("--server", default="http://127.0.0.1:8765")
    tui.add_argument("--token", default=None)

    sim_agent = subcommands.add_parser("sim-agent", help="Run a simulated reporting agent.")
    sim_agent.add_argument("--server", default="http://127.0.0.1:8765")
    sim_agent.add_argument("--token", default=None)
    sim_agent.add_argument("--agent-id", default="sim-agent-1")
    sim_agent.add_argument("--project", default="agent-pbx")
    sim_agent.add_argument("--once", action="store_true")
    sim_agent.add_argument("--poll-wait", type=float, default=2.0)

    sim_client = subcommands.add_parser(
        "sim-client", help="Run a simulated TUI client workflow."
    )
    sim_client.add_argument("--server", default="http://127.0.0.1:8765")
    sim_client.add_argument("--token", default=None)
    sim_client.add_argument("--agent-id", default=None)
    sim_client.add_argument("--message", default=None)
    token_helper = subcommands.add_parser(
        "token-helper", help="Run a short-lived token pairing helper."
    )
    token_helper.add_argument("--host", default="127.0.0.1")
    token_helper.add_argument("--port", type=int, default=8766)
    token_helper.add_argument("--db", type=Path, default=Path("state/agent-pbx.sqlite"))
    token_helper.add_argument("--ttl", type=int, default=120)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        config = ServerConfig(
            host=args.host,
            port=args.port,
            db_path=args.db,
            token=args.token,
            allow_insecure_lan=args.allow_insecure_lan,
        )
        app = create_app(config)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    if args.command == "token-helper":
        store = Store(args.db)
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
            )
        )
        return 0

    raise SystemExit(f"`agent-pbx {args.command}` is not implemented yet")
