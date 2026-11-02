from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def docs_check_module():  # type: ignore[no-untyped-def]
    path = ROOT / "scripts" / "check_docs.py"
    spec = importlib.util.spec_from_file_location("check_docs", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_repository_documentation_links_and_wiki_metadata() -> None:
    module = docs_check_module()

    files = module.markdown_files()

    assert module.validate_local_links(files) == []
    assert module.validate_wiki() == []


def test_documented_agent_pbx_commands_accept_help() -> None:
    module = docs_check_module()

    commands = module.documented_commands(module.markdown_files())

    assert commands
    assert module.smoke_documented_commands(commands) == []


def test_documented_command_parser_ignores_agent_pbx_text_in_env_values() -> None:
    module = docs_check_module()
    line = (
        'AGENT_PBX_DEMO_STATE_ROOT="/tmp/agent-pbx-capture" '
        './scripts/record_tui_demo.sh'
    )

    assert list(module.AGENT_PBX_COMMAND_RE.finditer(line)) == []


def test_documented_command_parser_accepts_env_prefixed_executable_path() -> None:
    module = docs_check_module()
    line = (
        'AGENT_PBX_STATE_ROOT="/tmp/agent-pbx-state" '
        '.venv/bin/agent-pbx doctor --json'
    )

    matches = list(module.AGENT_PBX_COMMAND_RE.finditer(line))

    assert len(matches) == 1
    assert matches[0].group("executable") == ".venv/bin/agent-pbx"
    assert tuple(module.shlex.split(matches[0].group("arguments"))) == (
        "doctor",
        "--json",
    )
