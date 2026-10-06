from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).parents[1] / "scripts" / "demo" / "demo_codex.py"


def run_demo_codex(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_demo_codex_exposes_version_and_model_catalog() -> None:
    version = run_demo_codex("--version")
    assert version.returncode == 0
    assert version.stdout.strip() == "codex-cli 0.159.3"

    catalog = run_demo_codex("debug", "models")
    assert catalog.returncode == 0
    models = json.loads(catalog.stdout)["models"]
    assert [item["slug"] for item in models] == [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
    ]
    assert models[0]["supportedReasoningLevels"] == ["high", "xhigh", "max"]


def test_demo_codex_supports_managed_mcp_configuration() -> None:
    result = run_demo_codex(
        "mcp",
        "add",
        "agent-pbx",
        "--url",
        "http://127.0.0.1:8765/mcp",
    )
    assert result.returncode == 0
    assert "demo MCP add: ok" in result.stdout


def test_demo_codex_refuses_noninteractive_runtime_without_tty() -> None:
    result = run_demo_codex("-c", 'model="gpt-5.6-sol"')
    assert result.returncode == 2
    assert "requires a TTY" in result.stderr
