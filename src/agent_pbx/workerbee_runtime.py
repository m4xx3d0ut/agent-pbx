from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from .workerbee_cutover import DB_NAME, WorkerBeeCutover, WorkerBeeCutoverError, read_durable_selection
from .workerbee_runtime_identity import (
    _runtime_identity_fields,
    WORKERBEE_RUNTIME_API_VERSION as WORKERBEE_RUNTIME_API_VERSION,
    WORKERBEE_BACKENDS as WORKERBEE_BACKENDS,
    WORKERBEE_BACKEND_OSS as WORKERBEE_BACKEND_OSS,
    WORKERBEE_BACKEND_USER_APPLIANCE as WORKERBEE_BACKEND_USER_APPLIANCE,
    WORKERBEE_BACKEND_PLATFORM_DEBUG as WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WORKERBEE_WORKSPACE_DIRECT_HOST as WORKERBEE_WORKSPACE_DIRECT_HOST,
    WORKERBEE_WORKSPACE_MANAGED_APPLIANCE as WORKERBEE_WORKSPACE_MANAGED_APPLIANCE,
    WORKERBEE_WORKSPACE_CONTENT_IMPORT as WORKERBEE_WORKSPACE_CONTENT_IMPORT,
    WorkerBeeBackendDescriptor as WorkerBeeBackendDescriptor,
    WorkerBeeRuntimeSelection as WorkerBeeRuntimeSelection,
    canonical_workerbee_backend as canonical_workerbee_backend,
    runtime_fingerprint as runtime_fingerprint,
    workspace_strategy_for_backend as workspace_strategy_for_backend,
)


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


class WorkerBeeRuntimeRegistry:
    """Persist the one selected WorkerBee runtime and its binding generation."""

    def __init__(
        self,
        state_root: Path,
        *,
        clock: Callable[[], float] = time.time,
        user_appliance_available: bool = False,
        cutover: WorkerBeeCutover | None = None,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.clock = clock
        self.user_appliance_available = bool(user_appliance_available)
        self.cutover = cutover
        self._lock = threading.RLock()

    @property
    def selection_path(self) -> Path:
        return self.state_root / "selected-runtime.json"

    @classmethod
    def for_daemon(cls, state_root: Path) -> WorkerBeeRuntimeRegistry:
        return cls(state_root, cutover=WorkerBeeCutover(state_root))

    def admission(self, kind: str, **scope: Any):
        if self.cutover is None:
            return nullcontext()
        return self.cutover.operation(kind, **scope)

    def observe_status(self, discover: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if self.cutover is not None and self.cutover.current_operation() is not None:
            return {}
        try:
            with self.admission("runtime_discovery", require_runtime=False):
                status = discover()
                active = status.get("active")
                if (
                    isinstance(active, dict) and active.get("ready") is True
                    and active.get("backend") and active.get("endpoint")
                ):
                    self.observe(active, reason="selector_status")
                return status
        except WorkerBeeCutoverError as exc:
            return {"error": exc.public_dict()}

    @property
    def has_durable_selection(self) -> bool:
        return (self.state_root / DB_NAME).exists()

    def selected(self) -> WorkerBeeRuntimeSelection | None:
        if self.cutover is not None:
            return self.cutover.selected()
        if self.has_durable_selection:
            return read_durable_selection(self.state_root / DB_NAME)
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
        if self.cutover is not None:
            if force_generation:
                raise ValueError("Only the fenced cutover can advance a selected generation")
            return self.cutover.observe(provenance, reason=reason)
        if (self.state_root / DB_NAME).exists():
            raise ValueError("WorkerBee selection is owned by the durable cutover")
        selected_fields = _runtime_identity_fields(provenance)
        backend = selected_fields["backend"]
        if backend not in WORKERBEE_BACKENDS:
            raise ValueError(f"unsupported WorkerBee backend: {backend or '-'}")
        if not selected_fields["endpoint"]:
            raise ValueError("WorkerBee runtime provenance is missing an endpoint")
        fingerprint = runtime_fingerprint(selected_fields)
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
            **({"admission": self.cutover.posture()} if self.cutover is not None else {}),
            "selected": selected.public_dict() if selected else None,
            "backends": [
                item.public_dict()
                for item in self.catalog(available_backends=available_backends)
            ],
        }
