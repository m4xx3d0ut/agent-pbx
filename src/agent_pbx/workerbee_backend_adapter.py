from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Callable, Literal, Mapping, Protocol

from .workerbee_mcp import inspect_workerbee_mcp, inspect_workerbee_switcher
from .workerbee_modes import source_mode_from_legacy_strategy
from .workerbee_runtime import (
    WORKERBEE_BACKENDS,
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    canonical_workerbee_backend,
    runtime_fingerprint,
    workspace_strategy_for_backend,
)


WORKERBEE_SWITCH_BIN_ENV = "AGENT_PBX_WORKERBEE_SWITCHER_BIN"
WORKERBEE_SWITCH_STATUS_BIN_ENV = "AGENT_PBX_WORKERBEE_SWITCHER_STATUS_BIN"
WORKERBEE_PLATFORM_DEBUG_POSTURE_ENV = "AGENT_PBX_WORKERBEE_PLATFORM_DEBUG_POSTURE"
WORKERBEE_SWITCH_TARGETS = ("oss", WORKERBEE_BACKEND_PLATFORM_DEBUG)
WORKERBEE_PLATFORM_DEBUG_POSTURES = ("loopback", "native_lan")
DEFAULT_PLATFORM_DEBUG_POSTURE = "loopback"
WORKERBEE_SWITCH_BACKEND_SCHEMA = "agent-pbx.workerbee-switch-backend/v1"


@dataclass(frozen=True, slots=True)
class WorkerBeeSwitchBackend:
    """Host-configured target and its qualification, without configuration contents."""

    backend: str
    qualification: Literal["legacy_switcher", "verified", "unavailable"]
    configuration_sha256: str = ""
    unavailable_reason: str = ""
    posture: str = ""

    def __post_init__(self) -> None:
        if self.backend not in WORKERBEE_BACKENDS:
            raise ValueError("unknown WorkerBee switch backend descriptor")
        if self.qualification == "unavailable":
            if self.configuration_sha256 or not self.unavailable_reason:
                raise ValueError("unavailable backend requires a reason and no configuration digest")
        elif (
            self.qualification not in {"legacy_switcher", "verified"}
            or not re.fullmatch(r"[0-9a-f]{64}", self.configuration_sha256)
            or self.unavailable_reason
        ):
            raise ValueError("qualified backend requires a configuration SHA-256")
        if self.qualification == "legacy_switcher" and self.backend not in WORKERBEE_SWITCH_TARGETS:
            raise ValueError("the legacy switcher supports only OSS and platform debug")

    @property
    def available(self) -> bool:
        return self.qualification != "unavailable"

    def public_dict(self, active: Mapping[str, Any]) -> dict[str, Any]:
        strategy = workspace_strategy_for_backend(self.backend)
        observed = (
            active.get("ready") is True
            and canonical_workerbee_backend(active.get("backend")) == self.backend
            and bool(active.get("endpoint"))
        )
        return {
            "schema": WORKERBEE_SWITCH_BACKEND_SCHEMA,
            "backend": self.backend,
            "available": self.available,
            "workspace_strategy": strategy,
            "source_mode": source_mode_from_legacy_strategy(strategy),
            "qualification": self.qualification,
            "configuration_sha256": self.configuration_sha256 or None,
            "unavailable_reason": self.unavailable_reason,
            "instance_id": runtime_fingerprint(active)[:24] if observed else None,
            **({"posture": self.posture} if self.posture else {}),
        }


class WorkerBeeBackendAdapter(Protocol):
    """Host-owned adapter. A configured driver must qualify targets before use."""

    @property
    def platform_debug_posture(self) -> str: ...

    def discover(self) -> dict[str, Any]: ...
    def descriptors(self) -> tuple[WorkerBeeSwitchBackend, ...]: ...
    def preflight(self, target: str) -> str: ...
    def activate(self, target: str) -> None: ...
    def verify(
        self, target: str, *, expected_platform_debug_posture: str | None = None
    ) -> dict[str, Any]: ...
    def restore(
        self, target: str, *, expected_platform_debug_posture: str | None = None
    ) -> dict[str, Any]: ...


def canonical_platform_debug_posture(value: object) -> str:
    resolved = str(value or DEFAULT_PLATFORM_DEBUG_POSTURE).strip().lower()
    resolved = resolved.replace("-", "_")
    aliases = {
        "lan": "native_lan",
        "native": "native_lan",
        "native_caddy": "native_lan",
    }
    resolved = aliases.get(resolved, resolved)
    if resolved not in WORKERBEE_PLATFORM_DEBUG_POSTURES:
        choices = ", ".join(WORKERBEE_PLATFORM_DEBUG_POSTURES)
        raise ValueError(
            f"unsupported WorkerBee platform-debug posture {value!r}; choose {choices}"
        )
    return resolved


class LegacyWorkerBeeBackendAdapter:
    """Adapt the existing OSS/platform-debug host commands to the selector contract."""

    def __init__(
        self,
        *,
        switch_bin: str | Path | None = None,
        status_bin: str | Path | None = None,
        platform_debug_posture: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        capability_probe: Callable[[str], Mapping[str, Any]] | None = None,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.runner = runner
        self.capability_probe = capability_probe or self._default_capability_probe
        self.platform_debug_posture = canonical_platform_debug_posture(
            platform_debug_posture
            or os.getenv(WORKERBEE_PLATFORM_DEBUG_POSTURE_ENV)
            or DEFAULT_PLATFORM_DEBUG_POSTURE
        )
        configured_switch = str(switch_bin or os.getenv(WORKERBEE_SWITCH_BIN_ENV) or "").strip()
        configured_status = str(status_bin or os.getenv(WORKERBEE_SWITCH_STATUS_BIN_ENV) or "").strip()
        self.switch_bin = configured_switch or str(which("wb-mcp-switch") or "")
        self.status_bin = configured_status or str(which("wb-mcp-status") or "")

    def descriptors(self) -> tuple[WorkerBeeSwitchBackend, ...]:
        missing = (
            "wb-mcp-switch is not configured" if not self.switch_bin
            else "wb-mcp-status is not configured" if not self.status_bin
            else ""
        )
        descriptors = []
        for backend in WORKERBEE_BACKENDS:
            if backend == WORKERBEE_BACKEND_USER_APPLIANCE:
                descriptors.append(
                    WorkerBeeSwitchBackend(
                        backend, "unavailable",
                        unavailable_reason="Live user-appliance image qualification is deferred.",
                    )
                )
                continue
            posture = self.platform_debug_posture if backend == WORKERBEE_BACKEND_PLATFORM_DEBUG else ""
            if missing:
                descriptors.append(
                    WorkerBeeSwitchBackend(
                        backend, "unavailable", unavailable_reason=missing, posture=posture
                    )
                )
                continue
            # Fingerprint the configured adapter inputs. These are not image,
            # package or config-file content digests from backend qualification.
            configured = (WORKERBEE_SWITCH_BACKEND_SCHEMA, backend, self.switch_bin, self.status_bin, posture)
            digest = hashlib.sha256(json.dumps(configured, separators=(",", ":")).encode()).hexdigest()
            descriptors.append(WorkerBeeSwitchBackend(backend, "legacy_switcher", digest, posture=posture))
        return tuple(descriptors)

    def discover(self) -> dict[str, Any]:
        if not self.status_bin:
            return {"ready": False, "backend": "", "platform_debug_posture": "",
                    "code": "WORKERBEE_SWITCHER_NOT_CONFIGURED"}
        report: dict[str, Any] = {}

        def read_status(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            result = self.runner(*args, **kwargs)
            try:
                payload = json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError):
                return result
            if isinstance(payload, dict):
                report.update(payload)
            return result

        observation = inspect_workerbee_switcher(status_bin=self.status_bin, runner=read_status)
        provenance = observation.public_dict()
        status = report.get("status")
        config = report.get("config")
        # A v1 status command can exit nonzero for a repairable debug-posture
        # mismatch. Preserve bounded identity evidence from that same sample,
        # while retaining the inspector's non-ready result.
        if (
            isinstance(status, dict)
            and isinstance(config, dict)
            and status.get("api_version") == "workerbee.mcp_switch_status/v1"
            and config.get("api_version") == "workerbee.mcp_switch_config/v1"
            and status.get("backend")
            and status.get("mcp_endpoint")
        ):
            mcp = status.get("mcp") if isinstance(status.get("mcp"), dict) else {}
            server_info = mcp.get("server_info")
            server_info = server_info if isinstance(server_info, dict) else {}
            provenance.update(
                backend=canonical_workerbee_backend(status.get("backend")),
                endpoint=str(status.get("mcp_endpoint") or ""),
                server_name=str(server_info.get("name") or ""),
                server_version=str(server_info.get("version") or ""),
                config_path=str(config.get("path") or ""),
            )
        provenance["backend"] = canonical_workerbee_backend(provenance.get("backend"))
        provenance["platform_debug_posture"] = self._observed_platform_debug_posture(
            provenance, status=status if isinstance(status, dict) else None
        )
        return provenance

    def preflight(self, target: str) -> str:
        return self._run_switch(target, dry_run=True)

    def activate(self, target: str) -> None:
        self._run_switch(target, dry_run=False)

    def restore(
        self, target: str, *, expected_platform_debug_posture: str | None = None
    ) -> dict[str, Any]:
        self.activate(target)
        return self.verify(
            target, expected_platform_debug_posture=expected_platform_debug_posture
        )

    def verify(
        self,
        target: str,
        *,
        expected_platform_debug_posture: str | None = None,
    ) -> dict[str, Any]:
        after = self.discover()
        expected_posture = (
            canonical_platform_debug_posture(expected_platform_debug_posture)
            if target == "platform_debug"
            else ""
        )
        posture_ready = bool(
            target != "platform_debug"
            or after.get("platform_debug_posture") == expected_posture
        )
        backend_ready = bool(
            after.get("ready")
            and canonical_workerbee_backend(after.get("backend")) == target
            and after.get("endpoint")
            and posture_ready
        )
        capability: Mapping[str, Any] = {
            "ready": False,
            "code": "WORKERBEE_SWITCH_NOT_PROBED",
        }
        if backend_ready:
            try:
                capability = dict(self.capability_probe(str(after["endpoint"])))
            except Exception as exc:  # noqa: BLE001 - transaction records the failure
                capability = {
                    "ready": False,
                    "code": "WORKERBEE_SWITCH_CAPABILITY_FAILED",
                    "message": type(exc).__name__,
                }
        verified = backend_ready and bool(capability.get("ready"))
        return {
            "verified": verified,
            "after": after,
            "capability": dict(capability),
            "platform_debug_posture": {
                "expected": expected_posture,
                "observed": str(after.get("platform_debug_posture") or ""),
                "ready": posture_ready,
            },
            "message": (
                "WorkerBee backend and bootstrap capabilities verified."
                if verified
                else (
                    "WorkerBee platform-debug posture verification failed."
                    if target == "platform_debug" and not posture_ready
                    else "WorkerBee backend or bootstrap capability verification failed."
                )
            ),
        }

    def _run_switch(
        self,
        target: str,
        *,
        dry_run: bool,
    ) -> str:
        self._require_tools()
        if target not in WORKERBEE_SWITCH_TARGETS:
            raise ValueError("backend is not supported by the legacy WorkerBee switcher")
        command = [
            self.switch_bin,
            "switch-debug" if target == "platform_debug" else "switch-oss",
        ]
        if dry_run:
            command.append("--dry-run")
        command.append("--json")
        if target == "platform_debug":
            command.append("--confirm-platform-debug")
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=180 if not dry_run else 45,
            )
        except Exception as exc:
            raise RuntimeError(
                f"WorkerBee switch command failed ({type(exc).__name__})"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "switch command failed").strip()
            raise RuntimeError(f"WorkerBee switch command exited {result.returncode}: {detail}")
        return str(result.stdout or result.stderr or "").strip()

    def _observed_platform_debug_posture(
        self,
        provenance: Mapping[str, Any],
        *,
        status: Mapping[str, Any] | None = None,
    ) -> str:
        if canonical_workerbee_backend(provenance.get("backend")) != "platform_debug":
            return ""
        status = status if isinstance(status, Mapping) else {}
        candidates: list[object] = [
            status.get("platform_debug_observed_posture"),
            status.get("platform_debug_posture"),
            status.get("debug_posture"),
        ]
        observed_detail = status.get("observed_posture")
        if isinstance(observed_detail, Mapping):
            candidates.insert(0, observed_detail.get("posture"))
        else:
            candidates.insert(0, observed_detail)
        posture_detail = status.get("platform_debug_posture")
        if isinstance(posture_detail, dict):
            candidates = [
                posture_detail.get("observed"),
                posture_detail.get("runtime"),
                *candidates,
            ]
        for field in ("debug_lan", "debug_lan_bridge"):
            value = status.get(field)
            if isinstance(value, dict):
                candidates.extend(
                    (
                        value.get("platform_debug_posture"),
                        value.get("posture"),
                        value.get("mode"),
                    )
                )
                if str(value.get("status") or "").strip().lower() in {
                    "active",
                    "ready",
                }:
                    candidates.append("native_lan")
        for candidate in candidates:
            if not str(candidate or "").strip():
                continue
            try:
                return canonical_platform_debug_posture(candidate)
            except ValueError:
                continue
        # Older switchers expose no posture field. Their platform-debug command
        # is the committed loopback-only baseline, so retain that compatibility
        # while requiring explicit evidence for native-LAN verification.
        return DEFAULT_PLATFORM_DEBUG_POSTURE

    @staticmethod
    def _default_capability_probe(endpoint: str) -> Mapping[str, Any]:
        result = asyncio.run(inspect_workerbee_mcp(endpoint))
        return result.public_dict()

    def _require_tools(self) -> None:
        if not self.switch_bin:
            raise ValueError("wb-mcp-switch is not configured")
        if not self.status_bin:
            raise ValueError("wb-mcp-status is not configured")
