from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
from typing import Any

from ..codex_cli import inspect_codex_cli, inspect_codex_model_catalog
from ..contracts import CapabilityRecord, CapabilitySupport


CAPABILITY_CACHE_VERSION = "agent-pbx.codex-capabilities/v2"


@dataclass(frozen=True)
class CodexFeature:
    name: str
    stage: str
    enabled: bool


@dataclass(frozen=True)
class CodexCapabilitySnapshot:
    executable: str
    executable_hash: str
    version: str
    host: str
    profile_hash: str
    probed_at: float
    commands: tuple[str, ...]
    options: tuple[str, ...]
    models: tuple[str, ...]
    features: tuple[CodexFeature, ...]
    capabilities: tuple[CapabilityRecord, ...]
    schema_files: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def cache_key(self) -> str:
        return ":".join(
            (self.executable, self.executable_hash, self.version, self.host, self.profile_hash)
        )

    def capability(self, name: str) -> CapabilityRecord | None:
        return next((item for item in self.capabilities if item.name == name), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "api_version": CAPABILITY_CACHE_VERSION,
            **asdict(self),
            "features": [asdict(item) for item in self.features],
            "capabilities": [
                {
                    **asdict(item),
                    "support": item.support.value,
                }
                for item in self.capabilities
            ],
        }


def parse_feature_list(text: str) -> tuple[CodexFeature, ...]:
    features: list[CodexFeature] = []
    for raw_line in text.splitlines():
        parts = raw_line.split()
        if len(parts) < 3 or parts[-1].lower() not in {"true", "false"}:
            continue
        features.append(
            CodexFeature(
                name=parts[0],
                stage=" ".join(parts[1:-1]),
                enabled=parts[-1].lower() == "true",
            )
        )
    return tuple(features)


def executable_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CodexCapabilityProbe:
    def __init__(
        self,
        codex_command: str = "codex",
        *,
        timeout: float = 15.0,
        cache_path: Path | None = None,
    ) -> None:
        self.codex_command = codex_command
        self.timeout = max(1.0, float(timeout))
        self.cache_path = cache_path

    def inspect(
        self,
        *,
        profile: dict[str, Any] | None = None,
        use_cache: bool = True,
        generate_schema: bool = True,
    ) -> CodexCapabilitySnapshot:
        executable = shutil.which(self.codex_command) or self.codex_command
        executable_path = Path(executable).expanduser().resolve()
        digest = executable_digest(executable_path) if executable_path.is_file() else ""
        profile_hash = hashlib.sha256(
            json.dumps(profile or {}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        host = platform.node()
        cli = inspect_codex_cli(str(executable_path), timeout=self.timeout)
        expected_key = ":".join((str(executable_path), digest, cli.version, host, profile_hash))
        if use_cache and (cached := self._load_cache(expected_key)) is not None:
            return cached

        errors: list[str] = []
        features: tuple[CodexFeature, ...] = ()
        try:
            result = subprocess.run(
                [str(executable_path), "features", "list"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode == 0:
                features = parse_feature_list(result.stdout)
            else:
                errors.append((result.stderr or result.stdout).strip())
        except Exception as exc:
            errors.append(f"feature probe: {exc}")
        models = tuple(
            item.slug
            for item in inspect_codex_model_catalog(
                str(executable_path), timeout=self.timeout
            )
        )
        schema_files: tuple[str, ...] = ()
        schema_observed = False
        if generate_schema and "app-server" in cli.commands:
            try:
                schema_files = self._probe_schema(executable_path)
                schema_observed = bool(schema_files)
            except Exception as exc:
                errors.append(f"app-server schema probe: {exc}")
        capabilities = self._capabilities(
            features,
            has_app_server="app-server" in cli.commands,
            schema_observed=schema_observed,
            probed_at=time.time(),
        )
        snapshot = CodexCapabilitySnapshot(
            executable=str(executable_path),
            executable_hash=digest,
            version=cli.version,
            host=host,
            profile_hash=profile_hash,
            probed_at=time.time(),
            commands=cli.commands,
            options=cli.options,
            models=models,
            features=features,
            capabilities=capabilities,
            schema_files=schema_files,
            errors=tuple(error for error in errors if error),
        )
        self._save_cache(snapshot)
        return snapshot

    def _probe_schema(self, executable: Path) -> tuple[str, ...]:
        with tempfile.TemporaryDirectory(prefix="agent-pbx-codex-schema-") as temp:
            output = Path(temp)
            result = subprocess.run(
                [
                    str(executable),
                    "app-server",
                    "generate-json-schema",
                    "--experimental",
                    "--out",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout).strip())
            return tuple(
                sorted(str(path.relative_to(output)) for path in output.rglob("*.json"))
            )

    @staticmethod
    def _capabilities(
        features: tuple[CodexFeature, ...],
        *,
        has_app_server: bool,
        schema_observed: bool,
        probed_at: float,
    ) -> tuple[CapabilityRecord, ...]:
        by_name = {feature.name: feature for feature in features}

        def feature(name: str) -> CapabilityRecord:
            item = by_name.get(name)
            support = (
                CapabilitySupport.ADVERTISED
                if item is not None and item.stage != "removed"
                else CapabilitySupport.UNSUPPORTED
            )
            return CapabilityRecord(
                name=name,
                support=support,
                source="codex features list",
                observed_at=probed_at,
                detail={
                    "enabled": item.enabled if item else False,
                    "stage": item.stage if item else "missing",
                },
            )

        records = [
            feature("multi_agent"),
            feature("goals"),
            feature("hooks"),
            feature("shell_tool"),
            feature("unified_exec"),
            feature("shell_snapshot"),
            feature("skills"),
            feature("worktrees"),
        ]
        records.append(
            CapabilityRecord(
                name="app_server_schema",
                support=(
                    CapabilitySupport.OBSERVED
                    if schema_observed
                    else CapabilitySupport.ADVERTISED
                    if has_app_server
                    else CapabilitySupport.UNSUPPORTED
                ),
                source="codex app-server generate-json-schema",
                observed_at=probed_at,
            )
        )
        return tuple(records)

    def _load_cache(self, expected_key: str) -> CodexCapabilitySnapshot | None:
        if self.cache_path is None:
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if raw.get("api_version") != CAPABILITY_CACHE_VERSION:
                return None
            capabilities = tuple(
                CapabilityRecord(
                    name=item["name"],
                    support=CapabilitySupport(item["support"]),
                    source=item["source"],
                    observed_at=float(item["observed_at"]),
                    detail=dict(item.get("detail") or {}),
                )
                for item in raw.get("capabilities", [])
            )
            snapshot = CodexCapabilitySnapshot(
                executable=raw["executable"],
                executable_hash=raw["executable_hash"],
                version=raw["version"],
                host=raw["host"],
                profile_hash=raw["profile_hash"],
                probed_at=float(raw["probed_at"]),
                commands=tuple(raw.get("commands", [])),
                options=tuple(raw.get("options", [])),
                models=tuple(raw.get("models", [])),
                features=tuple(CodexFeature(**item) for item in raw.get("features", [])),
                capabilities=capabilities,
                schema_files=tuple(raw.get("schema_files", [])),
                errors=tuple(raw.get("errors", [])),
            )
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        return snapshot if snapshot.cache_key == expected_key else None

    def _save_cache(self, snapshot: CodexCapabilitySnapshot) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temp.write_text(json.dumps(snapshot.as_dict(), indent=2), encoding="utf-8")
        os.replace(temp, self.cache_path)

