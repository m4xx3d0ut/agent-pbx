from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
from typing import Mapping


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_REF_PATTERN = re.compile(r"\$(?:{([A-Za-z_][A-Za-z0-9_]*)}|([A-Za-z_][A-Za-z0-9_]*))")


def parse_env_file(
    path: Path,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    scope = dict(base_env or os.environ)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        expanded = expand_env_refs(value, {**scope, **values})
        values[key] = expanded
    return values


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].lstrip()
    if "=" not in stripped:
        return None
    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not ENV_KEY_PATTERN.match(key):
        return None
    return key, shell_unquote(raw_value)


def shell_unquote(value: str) -> str:
    lexer = shlex.shlex(value, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    parts = list(lexer)
    if not parts:
        return ""
    return " ".join(parts)


def expand_env_refs(value: str, env: Mapping[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, match.group(0))

    return ENV_REF_PATTERN.sub(replace, value)


def apply_env_defaults(values: Mapping[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(key, value)
