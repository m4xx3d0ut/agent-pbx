from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .envfile import apply_env_defaults, parse_env_file
from .tui import run_tui


DEFAULT_TUI_ENV_FILE = Path("agent-pbx/tui.env")


def default_tui_config_path() -> Path:
    value = os.getenv("AGENT_PBX_TUI_CONFIG", "").strip()
    if value:
        return Path(value).expanduser()
    config_home = os.getenv("XDG_CONFIG_HOME", "").strip()
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / DEFAULT_TUI_ENV_FILE


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


def load_tui_config(path: Path, *, explicit: bool = False) -> dict[str, str]:
    if explicit and not path.exists():
        raise FileNotFoundError(path)
    values = parse_env_file(path)
    apply_env_defaults(
        {
            key: value
            for key, value in values.items()
            if key.startswith("AGENT_PBX_")
        }
    )
    return values


def write_tui_config_template(path: Path) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "# Agent PBX TUI local config.",
                "# This file is read by the agent-pbx-tui command.",
                "# Keep it private when AGENT_PBX_TOKEN is set.",
                "",
                "# Use an IP or DNS name reachable from this terminal.",
                "AGENT_PBX_SERVER_URL=http://127.0.0.1:8767",
                "AGENT_PBX_TOKEN=change-me",
                "",
                "# Useful for slow or very small remote terminals.",
                "AGENT_PBX_TUI_LAYOUT=tiny",
                "AGENT_PBX_TUI_THEME=cyberpunk",
                "AGENT_PBX_TUI_TMUX=0",
                "AGENT_PBX_TUI_LOW_POWER=1",
                "AGENT_PBX_TUI_AGENT_REFRESH_SECONDS=15",
                "AGENT_PBX_TUI_ATTENTION_BLINK_SECONDS=3",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return True


def build_parser(config_path: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-pbx-tui",
        description="Run the Agent PBX TUI using a local user config file.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=config_path,
        help=f"Config file to read before launch. Default: {config_path}",
    )
    parser.add_argument(
        "--init-config",
        action="store_true",
        help="Create a starter config file and exit.",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Agent PBX server URL. Overrides config and environment.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Bearer token. Overrides config and environment.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=Path, default=None)
    pre_parser.add_argument("--init-config", action="store_true")
    pre_args, _ = pre_parser.parse_known_args(args_list)

    config_path = (pre_args.config or default_tui_config_path()).expanduser()
    if pre_args.init_config:
        created = write_tui_config_template(config_path)
        status = "created" if created else "exists"
        print(f"{status}: {config_path}")
        return 0

    try:
        load_tui_config(config_path, explicit=pre_args.config is not None)
    except FileNotFoundError:
        print(f"agent-pbx-tui: config file not found: {config_path}", file=sys.stderr)
        return 2

    parser = build_parser(config_path)
    args = parser.parse_args(args_list)
    server = args.server or default_client_server()
    token = args.token if args.token is not None else os.getenv("AGENT_PBX_TOKEN")
    run_tui(server=server, token=token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
