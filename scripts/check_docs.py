#!/usr/bin/env python3
"""Validate repository documentation without mutating local services."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "docs" / "wiki"
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SHELL_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell|zsh)\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
AGENT_PBX_COMMAND_RE = re.compile(
    r"(?:^|&&|;|\|)\s*"
    r"(?:(?:[A-Z_][A-Z0-9_]*=(?:\"[^\"]*\"|'[^']*'|\S+))\s+)*"
    r"(?P<executable>(?:\S*/)?agent-pbx(?:-tui)?)\b"
    r"(?P<arguments>[^;&|]*)"
)
PRIVATE_WIKI_PATTERNS = {
    "absolute home path": re.compile(r"/home/[^/\s]+/"),
    "private SSH material": re.compile(r"(?:github-m|\.ssh/|BEGIN [A-Z ]+PRIVATE KEY)"),
    "private overlay identity": re.compile(r"Rosie-0|trusted human architect", re.I),
    "private IPv4 address": re.compile(
        r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
    ),
    "credential-shaped token": re.compile(
        r"\b(?:gh[opusr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,})\b"
    ),
}
WIKI_REVIEW_DATE_RE = re.compile(
    r"^> \*\*Last reviewed:\*\* \d{4}-\d{2}-\d{2}$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class DocumentedCommand:
    document: Path
    line: str
    executable: str
    arguments: tuple[str, ...]


def markdown_files() -> tuple[Path, ...]:
    files = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    return tuple(path for path in files if path.is_file())


def validate_local_links(files: tuple[Path, ...]) -> list[str]:
    errors: list[str] = []
    for document in files:
        body = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(body):
            target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
            if (
                not target
                or target.startswith(("#", "http://", "https://", "mailto:"))
            ):
                continue
            relative = unquote(target.split("#", 1)[0])
            resolved = (document.parent / relative).resolve()
            try:
                resolved.relative_to(ROOT)
            except ValueError:
                errors.append(
                    f"{document.relative_to(ROOT)}: link escapes repository: {target}"
                )
                continue
            if not resolved.exists():
                errors.append(
                    f"{document.relative_to(ROOT)}: missing link target: {target}"
                )
    return errors


def validate_wiki() -> list[str]:
    errors: list[str] = []
    pages = sorted(WIKI.glob("*.md")) if WIKI.is_dir() else []
    if not pages:
        return ["docs/wiki contains no Markdown source pages"]
    for page in pages:
        body = page.read_text(encoding="utf-8")
        relative = page.relative_to(ROOT)
        if "**Applies to:** Agent PBX 2.2.x" not in body:
            errors.append(f"{relative}: missing version applicability marker")
        if WIKI_REVIEW_DATE_RE.search(body) is None:
            errors.append(f"{relative}: missing review-date marker")
        for label, pattern in PRIVATE_WIKI_PATTERNS.items():
            if pattern.search(body):
                errors.append(f"{relative}: contains {label}")
    return errors


def _logical_shell_lines(block: str) -> tuple[str, ...]:
    logical: list[str] = []
    pending = ""
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if pending:
            line = f"{pending} {line}"
            pending = ""
        if line.endswith("\\"):
            pending = line[:-1].rstrip()
            continue
        logical.append(line)
    if pending:
        logical.append(pending)
    return tuple(logical)


def documented_commands(files: tuple[Path, ...]) -> tuple[DocumentedCommand, ...]:
    commands: list[DocumentedCommand] = []
    for document in files:
        body = document.read_text(encoding="utf-8")
        for block in SHELL_BLOCK_RE.findall(body):
            for line in _logical_shell_lines(block):
                for match in AGENT_PBX_COMMAND_RE.finditer(line):
                    executable = Path(match.group("executable")).name
                    try:
                        arguments = tuple(shlex.split(match.group("arguments")))
                    except ValueError as exc:
                        raise ValueError(
                            f"{document.relative_to(ROOT)}: invalid shell example: {line}"
                        ) from exc
                    commands.append(
                        DocumentedCommand(document, line, executable, arguments)
                    )
    return tuple(commands)


def _command_path(arguments: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(token for token in arguments if token and not token.startswith("-"))


def smoke_documented_commands(
    commands: tuple[DocumentedCommand, ...],
) -> list[str]:
    errors: list[str] = []
    checked: set[tuple[str, tuple[str, ...]]] = set()
    for command in commands:
        if command.executable == "agent-pbx-tui":
            path: tuple[str, ...] = ()
        else:
            positional = _command_path(command.arguments)
            if not positional:
                continue
            path = positional[:1]
            if path[0] in {
                "agent",
                "mcp",
                "setup",
                "codex",
                "migrate",
                "runtime",
                "remote",
                "uat",
            } and len(positional) > 1:
                path = positional[:2]
            if path == ("codex", "theme") and len(positional) > 2:
                path = positional[:3]
        key = (command.executable, path)
        if key in checked:
            continue
        checked.add(key)
        if command.executable == "agent-pbx-tui":
            expression = "from agent_pbx.tui_cli import main; raise SystemExit(main())"
        else:
            expression = "from agent_pbx.cli import main; raise SystemExit(main())"
        completed = subprocess.run(
            [sys.executable, "-c", expression, *path, "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0:
            rendered = " ".join((command.executable, *path, "--help"))
            errors.append(
                f"{command.document.relative_to(ROOT)}: command smoke failed: "
                f"{rendered}: {completed.stderr.strip()}"
            )
    return errors


def run_checks(*, command_smoke: bool = True) -> list[str]:
    files = markdown_files()
    errors = [*validate_local_links(files), *validate_wiki()]
    if command_smoke:
        try:
            commands = documented_commands(files)
        except ValueError as exc:
            errors.append(str(exc))
        else:
            errors.extend(smoke_documented_commands(commands))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-command-smoke",
        action="store_true",
        help="Skip safe --help probes for documented Agent PBX commands.",
    )
    args = parser.parse_args(argv)
    errors = run_checks(command_smoke=not args.no_command_smoke)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Documentation checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
