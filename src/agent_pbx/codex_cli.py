from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
from typing import Any


CODEX_MODEL_ENV = "AGENT_PBX_TUI_CODEX_MODEL"
CODEX_REASONING_EFFORT_ENV = "AGENT_PBX_TUI_CODEX_REASONING_EFFORT"
CODEX_SERVICE_TIER_ENV = "AGENT_PBX_TUI_CODEX_SERVICE_TIER"

_COMMAND_LINE_RE = re.compile(r"^\s{2}([a-z][a-z0-9-]*)\s{2,}")
_OPTION_RE = re.compile(r"^\s{2,}(?:-[A-Za-z],\s*)?(--[a-z0-9-]+)")


@dataclass(frozen=True)
class CodexCliCapabilities:
    version: str
    commands: tuple[str, ...]
    options: tuple[str, ...]

    @property
    def supports_queue(self) -> bool:
        return "queue" in self.commands

    @property
    def supports_fork(self) -> bool:
        return "fork" in self.commands

    @property
    def supports_model_flag(self) -> bool:
        return "--model" in self.options

    @property
    def supports_config_overrides(self) -> bool:
        return "--config" in self.options


@dataclass(frozen=True)
class CodexModelOption:
    slug: str
    display_name: str
    default_reasoning_level: str
    supported_reasoning_levels: tuple[str, ...]
    service_tiers: tuple[str, ...]
    default_service_tier: str
    visibility: str
    shell_type: str
    supported_in_api: bool

    @property
    def hidden(self) -> bool:
        return self.visibility == "hide"


def codex_model_config_overrides(
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    service_tier: str | None = None,
) -> list[str]:
    overrides: list[str] = []
    if cleaned_model := _clean_optional_value(model):
        overrides.append(f"model={json.dumps(cleaned_model)}")
    if cleaned_reasoning := _clean_optional_value(reasoning_effort):
        overrides.append(f"model_reasoning_effort={json.dumps(cleaned_reasoning)}")
    if cleaned_service_tier := _clean_optional_value(service_tier):
        overrides.append(f"service_tier={json.dumps(cleaned_service_tier)}")
    return overrides


def parse_codex_version(version_output: str) -> str:
    parts = version_output.strip().split()
    if not parts:
        return ""
    return parts[-1]


def parse_codex_help_capabilities(
    help_text: str,
    *,
    version: str = "",
) -> CodexCliCapabilities:
    commands: list[str] = []
    options: list[str] = []
    for line in help_text.splitlines():
        if match := _COMMAND_LINE_RE.match(line):
            commands.append(match.group(1))
        if match := _OPTION_RE.match(line):
            options.append(match.group(1))
    return CodexCliCapabilities(
        version=version,
        commands=tuple(dict.fromkeys(commands)),
        options=tuple(dict.fromkeys(options)),
    )


def inspect_codex_cli(codex_command: str = "codex", *, timeout: float = 5.0) -> CodexCliCapabilities:
    argv = _codex_command_argv(codex_command)
    version = ""
    try:
        version_result = subprocess.run(
            [*argv, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if version_result.returncode == 0:
            version = parse_codex_version(version_result.stdout)
    except Exception:
        version = ""
    help_text = ""
    try:
        help_result = subprocess.run(
            [*argv, "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if help_result.returncode == 0:
            help_text = help_result.stdout
    except Exception:
        help_text = ""
    return parse_codex_help_capabilities(help_text, version=version)


def parse_codex_model_catalog_json(catalog_json: str) -> tuple[CodexModelOption, ...]:
    try:
        payload = json.loads(catalog_json)
    except json.JSONDecodeError:
        return ()
    if isinstance(payload, dict):
        raw_models = payload.get("models")
    else:
        raw_models = payload
    if not isinstance(raw_models, list):
        return ()
    models: list[CodexModelOption] = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or item.get("id") or "").strip()
        if not slug:
            continue
        models.append(
            CodexModelOption(
                slug=slug,
                display_name=str(item.get("displayName") or item.get("name") or slug),
                default_reasoning_level=str(item.get("defaultReasoningLevel") or ""),
                supported_reasoning_levels=_reasoning_level_names(
                    item.get("supportedReasoningLevels")
                ),
                service_tiers=_service_tier_names(item.get("serviceTiers")),
                default_service_tier=str(item.get("defaultServiceTier") or ""),
                visibility=str(item.get("visibility") or ""),
                shell_type=str(item.get("shellType") or ""),
                supported_in_api=bool(item.get("supportedInApi")),
            )
        )
    return tuple(models)


def inspect_codex_model_catalog(
    codex_command: str = "codex",
    *,
    timeout: float = 10.0,
) -> tuple[CodexModelOption, ...]:
    try:
        result = subprocess.run(
            [*_codex_command_argv(codex_command), "debug", "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return ()
    if result.returncode != 0:
        return ()
    return parse_codex_model_catalog_json(result.stdout)


def _clean_optional_value(value: str | None) -> str:
    return str(value or "").strip()


def _codex_command_argv(codex_command: str) -> list[str]:
    import shlex

    argv = shlex.split(codex_command.strip())
    return argv or ["codex"]


def _reasoning_level_names(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("effort") or item.get("name") or "").strip()
        else:
            name = ""
        if name:
            names.append(name)
    return tuple(names)


def _service_tier_names(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
        elif isinstance(item, dict):
            name = str(item.get("value") or item.get("name") or "").strip()
        else:
            name = ""
        if name:
            names.append(name)
    return tuple(names)
