from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from .api import create_app, create_token_helper_app
from .config import ServerConfig
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

    subcommands.add_parser("tui", help="Run the Agent PBX TUI.")
    subcommands.add_parser("sim-agent", help="Run a simulated reporting agent.")
    subcommands.add_parser("sim-client", help="Run a simulated TUI client workflow.")
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

    raise SystemExit(f"`agent-pbx {args.command}` is not implemented yet")
