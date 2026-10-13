from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping


WORKERBEE_RUNTIME_API_VERSION = "agent-pbx.workerbee-runtime/v1"
WORKERBEE_BACKEND_OSS = "oss"
WORKERBEE_BACKEND_USER_APPLIANCE = "user_appliance"
WORKERBEE_BACKEND_PLATFORM_DEBUG = "platform_debug"
WORKERBEE_BACKENDS = (
    WORKERBEE_BACKEND_OSS,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
)

WORKERBEE_WORKSPACE_DIRECT_HOST = "direct_host"
WORKERBEE_WORKSPACE_MANAGED_APPLIANCE = "managed_appliance_volume"
WORKERBEE_WORKSPACE_CONTENT_IMPORT = "content_addressed_import"


def canonical_workerbee_backend(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "local": WORKERBEE_BACKEND_OSS,
        "oss_local": WORKERBEE_BACKEND_OSS,
        "workerbee_oss": WORKERBEE_BACKEND_OSS,
        "user": WORKERBEE_BACKEND_USER_APPLIANCE,
        "appliance": WORKERBEE_BACKEND_USER_APPLIANCE,
        "user_facing": WORKERBEE_BACKEND_USER_APPLIANCE,
        "debug": WORKERBEE_BACKEND_PLATFORM_DEBUG,
        "platform": WORKERBEE_BACKEND_PLATFORM_DEBUG,
        "platform_admin": WORKERBEE_BACKEND_PLATFORM_DEBUG,
    }
    return aliases.get(normalized, normalized)


def workspace_strategy_for_backend(backend: str) -> str:
    selected = canonical_workerbee_backend(backend)
    if selected == WORKERBEE_BACKEND_OSS:
        return WORKERBEE_WORKSPACE_DIRECT_HOST
    if selected == WORKERBEE_BACKEND_USER_APPLIANCE:
        return WORKERBEE_WORKSPACE_MANAGED_APPLIANCE
    if selected == WORKERBEE_BACKEND_PLATFORM_DEBUG:
        return WORKERBEE_WORKSPACE_CONTENT_IMPORT
    raise ValueError(f"unsupported WorkerBee backend: {backend}")


def _atomic_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class WorkerBeeBackendDescriptor:
    backend: str
    label: str
    workspace_strategy: str
    available: bool
    selected: bool = False
    unavailable_reason: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkerBeeRuntimeSelection:
    backend: str
    endpoint: str
    server_name: str
    server_version: str
    config_path: str
    fingerprint: str
    instance_id: str
    generation: int
    workspace_strategy: str
    selected_at: float
    observed_at: float
    reason: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": WORKERBEE_RUNTIME_API_VERSION,
            **asdict(self),
        }


class WorkerBeeRuntimeRegistry:
    """Persist the one selected WorkerBee runtime and its binding generation."""

    def __init__(
        self,
        state_root: Path,
        *,
        clock: Callable[[], float] = time.time,
        user_appliance_available: bool = False,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.clock = clock
        self.user_appliance_available = bool(user_appliance_available)
        self._lock = threading.RLock()

    @property
    def selection_path(self) -> Path:
        return self.state_root / "selected-runtime.json"

    def selected(self) -> WorkerBeeRuntimeSelection | None:
        with self._lock:
            try:
                payload = json.loads(self.selection_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                return None
            if not isinstance(payload, dict):
                return None
            if payload.pop("api_version", "") != WORKERBEE_RUNTIME_API_VERSION:
                return None
            try:
                return WorkerBeeRuntimeSelection(**payload)
            except (TypeError, ValueError):
                return None

    def observe(
        self,
        provenance: Mapping[str, Any],
        *,
        reason: str = "observed",
        force_generation: bool = False,
    ) -> WorkerBeeRuntimeSelection:
        backend = canonical_workerbee_backend(provenance.get("backend"))
        if backend not in WORKERBEE_BACKENDS:
            raise ValueError(f"unsupported WorkerBee backend: {backend or '-'}")
        endpoint = str(provenance.get("endpoint") or "").strip()
        if not endpoint:
            raise ValueError("WorkerBee runtime provenance is missing an endpoint")
        selected_fields = {
            "backend": backend,
            "endpoint": endpoint.rstrip("/"),
            "server_name": str(provenance.get("server_name") or "").strip(),
            "server_version": str(provenance.get("server_version") or "").strip(),
            "config_path": str(provenance.get("config_path") or "").strip(),
        }
        encoded = json.dumps(selected_fields, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now = self.clock()
        with self._lock:
            prior = self.selected()
            changed = prior is None or prior.fingerprint != fingerprint
            generation = 1 if prior is None else prior.generation
            if changed or force_generation:
                generation += 0 if prior is None else 1
            selection = WorkerBeeRuntimeSelection(
                **selected_fields,
                fingerprint=fingerprint,
                instance_id=fingerprint[:24],
                generation=generation,
                workspace_strategy=workspace_strategy_for_backend(backend),
                selected_at=(now if changed or force_generation or prior is None else prior.selected_at),
                observed_at=now,
                reason=str(reason or "observed"),
            )
            _atomic_private_json(self.selection_path, selection.public_dict())
            return selection

    def catalog(
        self,
        *,
        available_backends: tuple[str, ...] | list[str] | None = None,
    ) -> list[WorkerBeeBackendDescriptor]:
        selected = self.selected()
        if available_backends is None:
            available = {
                WORKERBEE_BACKEND_OSS,
                WORKERBEE_BACKEND_PLATFORM_DEBUG,
            }
            if self.user_appliance_available:
                available.add(WORKERBEE_BACKEND_USER_APPLIANCE)
        else:
            available = {
                canonical_workerbee_backend(value) for value in available_backends
            }
        labels = {
            WORKERBEE_BACKEND_OSS: "OSS host runtime",
            WORKERBEE_BACKEND_USER_APPLIANCE: "User appliance",
            WORKERBEE_BACKEND_PLATFORM_DEBUG: "Platform debug appliance",
        }
        result: list[WorkerBeeBackendDescriptor] = []
        for backend in WORKERBEE_BACKENDS:
            is_available = backend in available
            result.append(
                WorkerBeeBackendDescriptor(
                    backend=backend,
                    label=labels[backend],
                    workspace_strategy=workspace_strategy_for_backend(backend),
                    available=is_available,
                    selected=bool(selected and selected.backend == backend),
                    unavailable_reason=(
                        ""
                        if is_available
                        else (
                            "Live user-appliance image qualification is deferred."
                            if backend == WORKERBEE_BACKEND_USER_APPLIANCE
                            else "Backend is not reported by the configured selector."
                        )
                    ),
                )
            )
        return result

    def posture(
        self,
        *,
        available_backends: tuple[str, ...] | list[str] | None = None,
    ) -> dict[str, Any]:
        selected = self.selected()
        return {
            "api_version": WORKERBEE_RUNTIME_API_VERSION,
            "single_active_backend": True,
            "selected": selected.public_dict() if selected else None,
            "backends": [
                item.public_dict()
                for item in self.catalog(available_backends=available_backends)
            ],
        }

