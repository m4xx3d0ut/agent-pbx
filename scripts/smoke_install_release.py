#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def venv_executable(root: Path, name: str) -> Path:
    directory = "Scripts" if sys.platform == "win32" else "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    return root / directory / f"{name}{suffix}"


def smoke_install(wheel: Path, *, version: str) -> None:
    wheel = wheel.resolve()
    if not wheel.is_file():
        raise ValueError(f"wheel does not exist: {wheel}")
    with tempfile.TemporaryDirectory(prefix="agent-pbx-release-smoke-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = venv_executable(environment, "python")
        pip = venv_executable(environment, "pip")
        agent_pbx = venv_executable(environment, "agent-pbx")
        agent_pbx_tui = venv_executable(environment, "agent-pbx-tui")
        subprocess.run(
            [str(pip), "install", "--disable-pip-version-check", "--no-cache-dir", str(wheel)],
            check=True,
        )
        version_result = subprocess.run(
            [str(agent_pbx), "--version"], check=True, capture_output=True, text=True
        )
        if version not in version_result.stdout:
            raise ValueError(
                f"agent-pbx --version returned {version_result.stdout.strip()!r}, expected {version}"
            )
        subprocess.run([str(agent_pbx), "--help"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(agent_pbx_tui), "--help"], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            [
                str(python),
                "-c",
                "import agent_pbx,sys; sys.exit(0 if agent_pbx.__version__ == %r else 1)" % version,
            ],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install and smoke-test an Agent PBX release wheel")
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        smoke_install(args.wheel, version=args.version)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"verified clean installation of {args.wheel.name} as Agent PBX {args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
