from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil
import subprocess
import time
from typing import Any


CODEX_MODEL_ENV = "AGENT_PBX_TUI_CODEX_MODEL"
CODEX_REASONING_EFFORT_ENV = "AGENT_PBX_TUI_CODEX_REASONING_EFFORT"
CODEX_REASONING_SUMMARY_ENV = "AGENT_PBX_TUI_CODEX_REASONING_SUMMARY"
CODEX_VERBOSITY_ENV = "AGENT_PBX_TUI_CODEX_VERBOSITY"
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
    supports_verbosity: bool | None = None
    default_verbosity: str = ""

    @property
    def hidden(self) -> bool:
        return self.visibility == "hide"


@dataclass(frozen=True)
class CodexCliPosture:
    shell_version: str
    app_server_version: str
    latest_stable_version: str
    configured_model: str
    configured_model_source: str
    model_catalog_count: int
    model_known: bool | None
    warnings: tuple[str, ...]
    install_method: str = ""
    npm_managed: bool | None = None


def codex_model_config_overrides(
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    reasoning_summary: str | None = None,
    verbosity: str | None = None,
    service_tier: str | None = None,
) -> list[str]:
    overrides: list[str] = []
    if cleaned_model := _clean_optional_value(model):
        overrides.append(f"model={json.dumps(cleaned_model)}")
    if cleaned_reasoning := _clean_optional_value(reasoning_effort):
        overrides.append(f"model_reasoning_effort={json.dumps(cleaned_reasoning)}")
    if cleaned_summary := _clean_optional_value(reasoning_summary):
        overrides.append(f"model_reasoning_summary={json.dumps(cleaned_summary)}")
    if cleaned_verbosity := _clean_optional_value(verbosity):
        overrides.append(f"model_verbosity={json.dumps(cleaned_verbosity)}")
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
        raw_supports_verbosity = item.get(
            "supportVerbosity",
            item.get("support_verbosity"),
        )
        models.append(
            CodexModelOption(
                slug=slug,
                display_name=str(
                    item.get("displayName")
                    or item.get("display_name")
                    or item.get("name")
                    or slug
                ),
                default_reasoning_level=str(
                    item.get("defaultReasoningLevel")
                    or item.get("default_reasoning_level")
                    or ""
                ),
                supported_reasoning_levels=_reasoning_level_names(
                    item.get("supportedReasoningLevels")
                    or item.get("supported_reasoning_levels")
                ),
                service_tiers=_service_tier_names(
                    item.get("serviceTiers") or item.get("service_tiers")
                ),
                default_service_tier=str(
                    item.get("defaultServiceTier")
                    or item.get("default_service_tier")
                    or ""
                ),
                visibility=str(item.get("visibility") or ""),
                shell_type=str(item.get("shellType") or item.get("shell_type") or ""),
                supported_in_api=bool(
                    item.get("supportedInApi") or item.get("supported_in_api")
                ),
                supports_verbosity=(
                    raw_supports_verbosity
                    if isinstance(raw_supports_verbosity, bool)
                    else None
                ),
                default_verbosity=str(
                    item.get("defaultVerbosity")
                    or item.get("default_verbosity")
                    or ""
                ),
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


def parse_codex_doctor_posture(doctor_text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in doctor_text.splitlines():
        match = re.match(r"^\s*(?P<field>[A-Za-z][A-Za-z0-9 -]*?)\s{2,}(?P<value>.+?)\s*$", line)
        if match is None:
            continue
        key = " ".join(match.group("field").strip().casefold().split())
        value = match.group("value").strip()
        if key == "model":
            fields["configured_model"] = value.split("·", 1)[0].strip()
        elif key == "app-server version":
            fields["app_server_version"] = value
        elif key == "latest version":
            fields["latest_stable_version"] = value
        elif key == "install method":
            fields["install_method"] = value
        elif key == "managed by":
            fields["managed_by"] = value
            if "npm:" in value.casefold():
                fields["npm_managed"] = (
                    "true" if re.search(r"\bnpm:\s*yes\b", value, re.IGNORECASE) else "false"
                )
    return fields


def configured_codex_model(
    *,
    env: dict[str, str] | None = None,
    doctor_fields: dict[str, str] | None = None,
) -> tuple[str, str]:
    source_env = env if env is not None else __import__("os").environ
    env_model = str(source_env.get(CODEX_MODEL_ENV) or "").strip()
    if env_model:
        return env_model, CODEX_MODEL_ENV
    doctor_model = str((doctor_fields or {}).get("configured_model") or "").strip()
    if doctor_model:
        return doctor_model, "codex doctor"
    return "", ""


def validate_codex_model_slug(
    model: str,
    catalog: tuple[CodexModelOption, ...],
) -> tuple[bool | None, tuple[str, ...]]:
    slug = str(model or "").strip()
    if not slug:
        return None, ()
    if not catalog:
        return None, (f"Unable to validate Codex model {slug!r}; model catalog is unavailable.",)
    known_slugs = {item.slug for item in catalog}
    if slug in known_slugs:
        return True, ()
    return False, (f"Configured Codex model {slug!r} is not in `codex debug models`.",)


def inspect_codex_posture(
    codex_command: str = "codex",
    *,
    timeout: float = 10.0,
    env: dict[str, str] | None = None,
) -> CodexCliPosture:
    argv = _codex_command_argv(codex_command)
    shell_version = ""
    doctor_fields: dict[str, str] = {}
    try:
        version_result = subprocess.run(
            [*argv, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if version_result.returncode == 0:
            shell_version = parse_codex_version(version_result.stdout)
    except Exception:
        shell_version = ""
    try:
        doctor_result = subprocess.run(
            [*argv, "doctor"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if doctor_result.returncode == 0:
            doctor_fields = parse_codex_doctor_posture(doctor_result.stdout)
    except Exception:
        doctor_fields = {}
    catalog = inspect_codex_model_catalog(codex_command, timeout=timeout)
    model, model_source = configured_codex_model(
        env=env,
        doctor_fields=doctor_fields,
    )
    model_known, model_warnings = validate_codex_model_slug(model, catalog)
    warnings: list[str] = list(model_warnings)
    app_server_version = doctor_fields.get("app_server_version", "")
    latest_stable_version = doctor_fields.get("latest_stable_version", "")
    if shell_version and app_server_version and shell_version != app_server_version:
        warnings.append(
            f"Codex shell CLI {shell_version} differs from app-server {app_server_version}."
        )
    if shell_version and latest_stable_version and shell_version != latest_stable_version:
        warnings.append(
            f"Codex shell CLI {shell_version} differs from latest stable {latest_stable_version}."
        )
    return CodexCliPosture(
        shell_version=shell_version,
        app_server_version=app_server_version,
        latest_stable_version=latest_stable_version,
        configured_model=model,
        configured_model_source=model_source,
        model_catalog_count=len(catalog),
        model_known=model_known,
        warnings=tuple(warnings),
        install_method=doctor_fields.get("install_method", ""),
        npm_managed=_bool_field(doctor_fields.get("npm_managed")),
    )


CODEX_CLI_UPDATE_TARGET_RE = re.compile(
    r"^(?:latest|alpha|\d+\.\d+\.\d+(?:[-+][A-Za-z0-9_.-]+)?)$"
)


def validate_codex_update_target(target: str) -> str:
    cleaned = str(target or "latest").strip() or "latest"
    if not CODEX_CLI_UPDATE_TARGET_RE.match(cleaned):
        raise ValueError(
            "Codex update target must be latest, alpha, or a concrete package version."
        )
    return cleaned


def update_codex_cli_package(
    *,
    target: str = "latest",
    codex_command: str = "codex",
    timeout: float = 180.0,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved_target = validate_codex_update_target(target)
    package_spec = f"@openai/codex@{resolved_target}"
    command = ["npm", "install", "-g", package_spec]
    started = time.monotonic()
    before = inspect_codex_posture(codex_command, timeout=min(30.0, timeout), env=env)
    if shutil.which("npm") is None:
        return _codex_update_result(
            ok=False,
            target=resolved_target,
            package_spec=package_spec,
            command=command,
            before=before,
            after=before,
            started=started,
            error="npm is not available on PATH; only npm-managed Codex installs are supported.",
        )
    if before.npm_managed is False:
        return _codex_update_result(
            ok=False,
            target=resolved_target,
            package_spec=package_spec,
            command=command,
            before=before,
            after=before,
            started=started,
            error="Codex install is not npm-managed according to `codex doctor`.",
        )
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        error = None if returncode == 0 else f"npm install exited with {returncode}."
    except Exception as exc:
        return _codex_update_result(
            ok=False,
            target=resolved_target,
            package_spec=package_spec,
            command=command,
            before=before,
            after=before,
            started=started,
            error=str(exc),
        )
    after = inspect_codex_posture(codex_command, timeout=min(30.0, timeout), env=env)
    return _codex_update_result(
        ok=returncode == 0,
        target=resolved_target,
        package_spec=package_spec,
        command=command,
        before=before,
        after=after,
        started=started,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        error=error,
    )


def _codex_update_result(
    *,
    ok: bool,
    target: str,
    package_spec: str,
    command: list[str],
    before: CodexCliPosture,
    after: CodexCliPosture,
    started: float,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "target": target,
        "package_spec": package_spec,
        "command": command,
        "returncode": returncode,
        "stdout": stdout[-8000:],
        "stderr": stderr[-8000:],
        "error": error,
        "duration_seconds": time.monotonic() - started,
        "before": _posture_summary(before),
        "after": _posture_summary(after),
    }


def _posture_summary(posture: CodexCliPosture) -> dict[str, Any]:
    return {
        "shell_version": posture.shell_version,
        "app_server_version": posture.app_server_version,
        "latest_stable_version": posture.latest_stable_version,
        "configured_model": posture.configured_model,
        "configured_model_source": posture.configured_model_source,
        "model_catalog_count": posture.model_catalog_count,
        "model_known": posture.model_known,
        "warnings": list(posture.warnings),
        "install_method": posture.install_method,
        "npm_managed": posture.npm_managed,
    }


def _bool_field(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


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
            name = str(
                item.get("value") or item.get("id") or item.get("name") or ""
            ).strip()
        else:
            name = ""
        if name:
            names.append(name)
    return tuple(names)
