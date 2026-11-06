from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .workerbee_runtime import (
    WORKERBEE_BACKEND_OSS,
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    WORKERBEE_WORKSPACE_DIRECT_HOST,
    WorkerBeeRuntimeSelection,
    canonical_workerbee_backend,
    workspace_strategy_for_backend,
)
from .workerbee_modes import (
    WORKERBEE_SOURCE_MANAGED_CLONE,
    WORKERBEE_SOURCE_MODE_VERSION,
    WORKERBEE_SOURCE_NATIVE_DIRECT,
    WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
    WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
    WorkerBeeModeError,
    canonical_workerbee_source_mode,
    legacy_strategy_for_source_mode,
    source_mode_from_legacy_strategy,
)

WORKERBEE_WORKSPACE_BINDING_API_VERSION = "agent-pbx.workerbee-workspace-binding/v3"
WORKERBEE_WORKSPACE_BINDING_LEGACY_API_VERSION = (
    "agent-pbx.workerbee-workspace-binding/v2"
)
WORKERBEE_WORKSPACE_HELPER_ENV = "AGENT_PBX_WORKERBEE_WORKSPACE_HELPER_BIN"
WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV = (
    "AGENT_PBX_WORKERBEE_WORKSPACE_INCLUDES_JSON"
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class WorkerBeeWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


class WorkerBeeWorkspaceIncludePolicyError(ValueError):
    """A host-owned workspace include policy is malformed or mismatched."""


def configured_workerbee_workspace_include_paths(
    *,
    agent_id: str,
    source_root: str | Path,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Return explicitly approved extra paths for one Agent and exact checkout.

    The policy is deliberately host configuration rather than Agent metadata:
    Agents can refresh their own registration metadata, while only the local
    controller should be able to broaden a content-addressed workspace import.
    """

    values = os.environ if env is None else env
    raw = str(values.get(WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV) or "").strip()
    if not raw:
        return ()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"{WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV} must be valid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"{WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV} must be a JSON object"
        )
    selected = payload.get(agent_id)
    if selected is None:
        return ()
    if not isinstance(selected, dict) or set(selected) != {
        "source_root",
        "include_paths",
    }:
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"workspace include policy for {agent_id} must contain only "
            "source_root and include_paths"
        )
    configured_root = str(selected.get("source_root") or "").strip()
    actual_root = Path(source_root).expanduser()
    expected_root = Path(configured_root).expanduser()
    if not configured_root or not expected_root.is_absolute():
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"workspace include policy for {agent_id} requires an absolute source_root"
        )
    if not actual_root.is_absolute():
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"workspace source root for {agent_id} must be absolute"
        )
    if actual_root.resolve(strict=False) != expected_root.resolve(strict=False):
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"workspace include policy source_root does not match {agent_id}"
        )
    configured_paths = selected.get("include_paths")
    if not isinstance(configured_paths, list):
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"workspace include policy for {agent_id} requires at most 64 paths"
        )
    try:
        return normalize_workerbee_workspace_include_paths(configured_paths)
    except WorkerBeeWorkspaceIncludePolicyError as exc:
        raise WorkerBeeWorkspaceIncludePolicyError(
            f"workspace include policy for {agent_id} {exc}"
        ) from exc


def normalize_workerbee_workspace_include_paths(
    configured_paths: list[object] | tuple[object, ...],
) -> tuple[str, ...]:
    """Normalize a bounded set of lexical source-root-relative include paths."""

    if len(configured_paths) > 64:
        raise WorkerBeeWorkspaceIncludePolicyError("requires at most 64 paths")
    result: list[str] = []
    seen: set[str] = set()
    for item in configured_paths:
        value = str(item).strip() if isinstance(item, str) else ""
        raw_parts = value.split("/")
        path = PurePosixPath(value)
        if (
            not value
            or len(value) > 4096
            or "\\" in value
            or "\x00" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_parts)
            or ".git" in path.parts
        ):
            raise WorkerBeeWorkspaceIncludePolicyError("contains an unsafe path")
        normalized = path.as_posix()
        if normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class WorkerBeeWorkspaceBinding:
    binding_id: str
    project: str
    source_root: str
    source_commit: str
    source_dirty: bool
    source_status_sha256: str
    content_sha256: str
    backend: str
    backend_instance_id: str
    backend_generation: int
    backend_fingerprint: str
    source_mode: str
    source_mode_version: int
    workspace_strategy: str
    runtime_workspace_path: str
    materializer_receipt: str
    helper_receipt_sha256: str
    capability_evidence_sha256: str
    state: str
    created_at: float
    verified_at: float
    released_at: float | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": WORKERBEE_WORKSPACE_BINDING_API_VERSION,
            **asdict(self),
        }

    def session_start_args(self, *, goal: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "cwd": self.runtime_workspace_path,
            "goal": goal,
            "project": self.project,
            "open_dashboard": False,
        }
        if self.source_mode in {
            WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
            WORKERBEE_SOURCE_MANAGED_CLONE,
        }:
            result.update(
                workspace_path=self.runtime_workspace_path,
                workspace_sha256=self.content_sha256,
                source_commit=self.source_commit,
                source_dirty=self.source_dirty,
            )
        return result


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


class WorkerBeeWorkspaceService:
    """Create backend-local WorkerBee workspace receipts without sharing roots."""

    def __init__(
        self,
        state_root: Path,
        *,
        helper_bin: str | Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.state_root = state_root.expanduser().resolve()
        self.helper_bin = str(
            helper_bin or os.getenv(WORKERBEE_WORKSPACE_HELPER_ENV) or ""
        ).strip()
        self.runner = runner
        self.clock = clock

    def prepare(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        project: str,
        source_root: str | Path,
        include_paths: tuple[str, ...] = (),
        source_mode: str | None = None,
        trusted_direct_bind_receipt: Mapping[str, Any] | None = None,
        administrator_ceiling: bool = False,
        approved_roots: tuple[str | Path, ...] = (),
    ) -> WorkerBeeWorkspaceBinding:
        selected_project = self._safe_project(project)
        source = Path(source_root).expanduser().resolve()
        backend = canonical_workerbee_backend(runtime.backend)
        selected_source_mode = self._source_mode_for_prepare(
            backend=backend,
            source_mode=source_mode,
        )
        try:
            normalized_include_paths = normalize_workerbee_workspace_include_paths(
                include_paths
            )
        except WorkerBeeWorkspaceIncludePolicyError as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_INCLUDE_INVALID",
                f"WorkerBee workspace include policy {exc}.",
                "Use at most 64 safe source-root-relative paths without .git or traversal.",
            ) from exc
        identity = self._source_identity(
            source,
            materialized=selected_source_mode
            in {WORKERBEE_SOURCE_SNAPSHOT_IMPORT, WORKERBEE_SOURCE_MANAGED_CLONE},
            include_paths=normalized_include_paths,
        )
        if selected_source_mode == WORKERBEE_SOURCE_NATIVE_DIRECT:
            binding = self._direct_binding(
                runtime=runtime,
                project=selected_project,
                source=source,
                identity=identity,
                source_mode=selected_source_mode,
            )
        elif selected_source_mode in {
            WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
            WORKERBEE_SOURCE_MANAGED_CLONE,
        }:
            binding = self._materialized_binding(
                runtime=runtime,
                project=selected_project,
                source=source,
                identity=identity,
                include_paths=normalized_include_paths,
                source_mode=selected_source_mode,
            )
        elif selected_source_mode == WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND:
            binding = self._trusted_direct_binding(
                runtime=runtime,
                project=selected_project,
                source=source,
                identity=identity,
                receipt=trusted_direct_bind_receipt,
                administrator_ceiling=administrator_ceiling,
                approved_roots=approved_roots,
            )
        else:  # pragma: no cover - runtime selection validates this first
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_BACKEND_UNSUPPORTED",
                f"Unsupported WorkerBee backend: {backend}",
            )
        _atomic_private_json(self._receipt_path(binding.binding_id), binding.public_dict())
        return binding

    def source_identity(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        source_root: str | Path,
        include_paths: tuple[str, ...] = (),
        source_mode: str | None = None,
    ) -> dict[str, Any]:
        """Return the identity used to address one backend workspace snapshot."""

        try:
            normalized_include_paths = normalize_workerbee_workspace_include_paths(
                include_paths
            )
        except WorkerBeeWorkspaceIncludePolicyError as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_INCLUDE_INVALID",
                f"WorkerBee workspace include policy {exc}.",
                "Use at most 64 safe source-root-relative paths without .git or traversal.",
            ) from exc
        source = Path(source_root).expanduser().resolve()
        selected_source_mode = self._source_mode_for_prepare(
            backend=canonical_workerbee_backend(runtime.backend),
            source_mode=source_mode,
        )
        return self._source_identity(
            source,
            materialized=selected_source_mode
            in {WORKERBEE_SOURCE_SNAPSHOT_IMPORT, WORKERBEE_SOURCE_MANAGED_CLONE},
            include_paths=normalized_include_paths,
        )

    def expected_binding_id(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        project: str,
        source_root: str | Path,
        include_paths: tuple[str, ...] = (),
        source_mode: str | None = None,
        source_mode_version: int = WORKERBEE_SOURCE_MODE_VERSION,
    ) -> str:
        """Return the binding ID represented by the checkout's current content."""

        identity = self.source_identity(
            runtime=runtime,
            source_root=source_root,
            include_paths=include_paths,
            source_mode=source_mode,
        )
        selected_source_mode = self._source_mode_for_prepare(
            backend=canonical_workerbee_backend(runtime.backend),
            source_mode=source_mode,
        )
        return self._binding_id(
            runtime,
            self._safe_project(project),
            identity,
            source_mode=selected_source_mode,
            source_mode_version=source_mode_version,
        )

    def load(self, binding_id: str) -> WorkerBeeWorkspaceBinding:
        path = self._receipt_path(binding_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BINDING_NOT_FOUND",
                "WorkerBee workspace binding was not found.",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BINDING_INVALID",
                "WorkerBee workspace binding receipt is unreadable.",
            ) from exc
        api_version = payload.pop("api_version", "")
        if api_version == WORKERBEE_WORKSPACE_BINDING_LEGACY_API_VERSION:
            try:
                payload["source_mode"] = source_mode_from_legacy_strategy(
                    payload.get("workspace_strategy")
                )
            except WorkerBeeModeError as exc:
                raise WorkerBeeWorkspaceError(
                    "WORKERBEE_WORKSPACE_BINDING_INVALID",
                    "Legacy WorkerBee workspace binding has an unknown source strategy.",
                ) from exc
            payload["source_mode_version"] = 0
            payload["helper_receipt_sha256"] = ""
            payload["capability_evidence_sha256"] = ""
        elif api_version != WORKERBEE_WORKSPACE_BINDING_API_VERSION:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BINDING_VERSION_UNSUPPORTED",
                "WorkerBee workspace binding receipt has an unsupported version.",
            )
        try:
            return WorkerBeeWorkspaceBinding(**payload)
        except (TypeError, ValueError) as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BINDING_INVALID",
                "WorkerBee workspace binding receipt has invalid fields.",
            ) from exc

    def verify_runtime(
        self,
        binding: WorkerBeeWorkspaceBinding,
        runtime: WorkerBeeRuntimeSelection,
    ) -> None:
        if binding.backend != runtime.backend:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BACKEND_MISMATCH",
                "Workspace binding belongs to a different WorkerBee backend.",
                "Prepare or select a binding for the active backend.",
            )
        if binding.backend_instance_id != runtime.instance_id:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_INSTANCE_MISMATCH",
                "Workspace binding belongs to a different WorkerBee backend instance.",
                "Verify the selected appliance and create a fresh binding.",
            )
        if binding.backend_generation != runtime.generation:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_BACKEND_REBIND_REQUIRED",
                "Workspace binding belongs to an earlier WorkerBee backend generation.",
                "Review the selected backend and rebind the session before mutation.",
            )
        if binding.state != "ready":
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BINDING_NOT_READY",
                f"Workspace binding is {binding.state!r}, not ready.",
            )

    def release(self, binding_id: str) -> WorkerBeeWorkspaceBinding:
        binding = self.load(binding_id)
        if binding.state == "released":
            return binding
        if binding.source_mode in {
            WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
            WORKERBEE_SOURCE_MANAGED_CLONE,
        }:
            self._run_helper("release", binding=binding)
        updated = WorkerBeeWorkspaceBinding(
            **{
                **asdict(binding),
                "state": "released",
                "released_at": self.clock(),
            }
        )
        _atomic_private_json(self._receipt_path(binding_id), updated.public_dict())
        return updated

    def _direct_binding(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        project: str,
        source: Path,
        identity: Mapping[str, Any],
        source_mode: str,
    ) -> WorkerBeeWorkspaceBinding:
        now = self.clock()
        return WorkerBeeWorkspaceBinding(
            binding_id=self._binding_id(
                runtime,
                project,
                identity,
                source_mode=source_mode,
            ),
            project=project,
            source_root=str(source),
            source_commit=str(identity["source_commit"]),
            source_dirty=bool(identity["source_dirty"]),
            source_status_sha256=str(identity["source_status_sha256"]),
            content_sha256="",
            backend=runtime.backend,
            backend_instance_id=runtime.instance_id,
            backend_generation=runtime.generation,
            backend_fingerprint=runtime.fingerprint,
            source_mode=source_mode,
            source_mode_version=WORKERBEE_SOURCE_MODE_VERSION,
            workspace_strategy=legacy_strategy_for_source_mode(source_mode),
            runtime_workspace_path=str(source),
            materializer_receipt="",
            helper_receipt_sha256="",
            capability_evidence_sha256="",
            state="ready",
            created_at=now,
            verified_at=now,
        )

    def _materialized_binding(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        project: str,
        source: Path,
        identity: Mapping[str, Any],
        include_paths: tuple[str, ...],
        source_mode: str,
    ) -> WorkerBeeWorkspaceBinding:
        if runtime.backend == WORKERBEE_BACKEND_USER_APPLIANCE:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_USER_APPLIANCE_DEFERRED",
                "The managed user-appliance workspace has not completed qualification.",
                "Use the OSS or platform-debug backend until the managed-volume contract is available.",
            )
        if not self.helper_bin:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_HELPER_NOT_CONFIGURED",
                "WorkerBee appliance workspace materializer is not configured.",
                f"Set {WORKERBEE_WORKSPACE_HELPER_ENV} to the versioned materializer helper.",
            )
        binding_id = self._binding_id(
            runtime,
            project,
            identity,
            source_mode=source_mode,
        )
        command = [
            self.helper_bin,
            "stage",
            "--backend",
            runtime.backend,
            "--source",
            str(source),
            "--project",
            project,
            "--binding-id",
            binding_id,
            "--json",
        ]
        for value in include_paths:
            command.extend(("--include", value))
        try:
            result = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001 - normalize helper failure
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_STAGE_FAILED",
                f"WorkerBee workspace materializer failed ({type(exc).__name__}).",
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "materializer failed").strip()
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_STAGE_FAILED",
                f"WorkerBee workspace materializer exited {result.returncode}: {detail}",
            )
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_STAGE_INVALID",
                "WorkerBee workspace materializer returned invalid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_STAGE_INVALID",
                "WorkerBee workspace materializer returned a non-object receipt.",
            )
        returned_backend = canonical_workerbee_backend(payload.get("backend"))
        if returned_backend != runtime.backend:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_STAGE_BACKEND_MISMATCH",
                "WorkerBee materializer staged into a different backend domain.",
            )
        runtime_path = str(payload.get("runtime_workspace_path") or "").strip()
        digest = str(payload.get("content_sha256") or "").strip().lower()
        if not runtime_path or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_STAGE_INVALID",
                "WorkerBee materializer receipt is missing a runtime path or canonical digest.",
            )
        if str(payload.get("source_commit") or "") != identity["source_commit"]:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_SOURCE_DRIFT",
                "WorkerBee materializer receipt source commit does not match the checkout.",
            )
        if bool(payload.get("source_dirty")) != bool(identity["source_dirty"]):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_SOURCE_DRIFT",
                "WorkerBee materializer receipt dirty state does not match the checkout.",
            )
        now = self.clock()
        return WorkerBeeWorkspaceBinding(
            binding_id=binding_id,
            project=project,
            source_root=str(source),
            source_commit=str(identity["source_commit"]),
            source_dirty=bool(identity["source_dirty"]),
            source_status_sha256=str(identity["source_status_sha256"]),
            content_sha256=digest,
            backend=runtime.backend,
            backend_instance_id=runtime.instance_id,
            backend_generation=runtime.generation,
            backend_fingerprint=runtime.fingerprint,
            source_mode=source_mode,
            source_mode_version=WORKERBEE_SOURCE_MODE_VERSION,
            workspace_strategy=legacy_strategy_for_source_mode(source_mode),
            runtime_workspace_path=runtime_path,
            materializer_receipt=str(payload.get("receipt_path") or "").strip(),
            helper_receipt_sha256="",
            capability_evidence_sha256="",
            state="ready",
            created_at=now,
            verified_at=now,
        )

    def _trusted_direct_binding(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        project: str,
        source: Path,
        identity: Mapping[str, Any],
        receipt: Mapping[str, Any] | None,
        administrator_ceiling: bool,
        approved_roots: tuple[str | Path, ...],
    ) -> WorkerBeeWorkspaceBinding:
        if runtime.backend != WORKERBEE_BACKEND_PLATFORM_DEBUG:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_BACKEND_DENIED",
                "trusted-direct-bind is limited to the platform-debug backend.",
            )
        if not administrator_ceiling:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_CEILING_REQUIRED",
                "trusted-direct-bind requires an explicit administrator ceiling.",
            )
        canonical_roots = self._trusted_direct_roots(approved_roots)
        if not any(source == root or root in source.parents for root in canonical_roots):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_DENIED",
                "The source checkout is outside the administrator-approved roots.",
            )
        self._require_owner_safe_real_path(source)
        evidence = self._validate_trusted_direct_receipt(
            runtime=runtime,
            source=source,
            receipt=receipt,
        )
        now = self.clock()
        return WorkerBeeWorkspaceBinding(
            binding_id=self._binding_id(
                runtime,
                project,
                identity,
                source_mode=WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
            ),
            project=project,
            source_root=str(source),
            source_commit=str(identity["source_commit"]),
            source_dirty=bool(identity["source_dirty"]),
            source_status_sha256=str(identity["source_status_sha256"]),
            content_sha256="",
            backend=runtime.backend,
            backend_instance_id=runtime.instance_id,
            backend_generation=runtime.generation,
            backend_fingerprint=runtime.fingerprint,
            source_mode=WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
            source_mode_version=WORKERBEE_SOURCE_MODE_VERSION,
            workspace_strategy=legacy_strategy_for_source_mode(
                WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
            ),
            runtime_workspace_path=evidence["runtime_workspace_path"],
            materializer_receipt="",
            helper_receipt_sha256=evidence["helper_receipt_sha256"],
            capability_evidence_sha256=evidence["capability_evidence_sha256"],
            state="ready",
            created_at=now,
            verified_at=now,
        )

    @staticmethod
    def _source_mode_for_prepare(*, backend: str, source_mode: str | None) -> str:
        if source_mode is None:
            legacy_strategy = workspace_strategy_for_backend(backend)
            return source_mode_from_legacy_strategy(legacy_strategy)
        try:
            selected = canonical_workerbee_source_mode(source_mode)
        except WorkerBeeModeError as exc:
            raise WorkerBeeWorkspaceError(exc.code, exc.message, exc.remediation) from exc
        allowed = {
            WORKERBEE_BACKEND_OSS: {WORKERBEE_SOURCE_NATIVE_DIRECT},
            WORKERBEE_BACKEND_PLATFORM_DEBUG: {
                WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
                WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
            },
            WORKERBEE_BACKEND_USER_APPLIANCE: {WORKERBEE_SOURCE_MANAGED_CLONE},
        }
        if selected not in allowed.get(backend, set()):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_SOURCE_MODE_BACKEND_INCOMPATIBLE",
                f"Source mode {selected!r} is not supported by backend {backend!r}.",
                "Select an explicit compatible source mode; Agent PBX will not fall back.",
            )
        return selected

    @staticmethod
    def _trusted_direct_roots(
        roots: tuple[str | Path, ...],
    ) -> tuple[Path, ...]:
        if not roots:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_ROOTS_REQUIRED",
                "trusted-direct-bind requires at least one approved canonical root.",
            )
        result: list[Path] = []
        for raw in roots:
            path = Path(raw).expanduser()
            if not path.is_absolute() or path.is_symlink():
                raise WorkerBeeWorkspaceError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_INVALID",
                    "An approved trusted-direct-bind root is not a real absolute path.",
                )
            try:
                canonical = path.resolve(strict=True)
            except OSError as exc:
                raise WorkerBeeWorkspaceError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_INVALID",
                    "An approved trusted-direct-bind root is unavailable.",
                ) from exc
            WorkerBeeWorkspaceService._require_owner_safe_real_path(canonical)
            result.append(canonical)
        return tuple(result)

    @staticmethod
    def _require_owner_safe_real_path(path: Path) -> None:
        if not path.is_absolute():
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                "trusted-direct-bind paths must be absolute.",
            )
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                details = current.lstat()
            except OSError as exc:
                raise WorkerBeeWorkspaceError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                    "trusted-direct-bind path ancestry is unavailable.",
                ) from exc
            if stat.S_ISLNK(details.st_mode):
                raise WorkerBeeWorkspaceError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_SYMLINK_DENIED",
                    "trusted-direct-bind path ancestry contains a symbolic link.",
                )
        if path.lstat().st_uid != os.getuid():
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_OWNER_MISMATCH",
                "trusted-direct-bind path ownership does not match the daemon user.",
            )

    def _validate_trusted_direct_receipt(
        self,
        *,
        runtime: WorkerBeeRuntimeSelection,
        source: Path,
        receipt: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        required = {
            "api_version",
            "backend",
            "backend_instance_id",
            "backend_fingerprint",
            "source_root",
            "runtime_workspace_path",
            "capability",
            "live",
            "verified_at",
            "expires_at",
            "helper_receipt_id",
            "helper_receipt_sha256",
        }
        if not isinstance(receipt, Mapping) or set(receipt) != required:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "trusted-direct-bind requires one exact verified helper receipt.",
            )
        payload = dict(receipt)
        supplied_digest = str(payload.pop("helper_receipt_sha256") or "").lower()
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        expected_digest = hashlib.sha256(encoded.encode()).hexdigest()
        runtime_path = str(payload.get("runtime_workspace_path") or "").strip()
        if (
            supplied_digest != expected_digest
            or payload.get("api_version")
            != "agent-pbx.workerbee-trusted-direct-bind-helper/v1"
            or canonical_workerbee_backend(payload.get("backend")) != runtime.backend
            or payload.get("backend_instance_id") != runtime.instance_id
            or payload.get("backend_fingerprint") != runtime.fingerprint
            or Path(str(payload.get("source_root") or "")).resolve(strict=False)
            != source
            or payload.get("capability") != WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
            or payload.get("live") is not True
            or not runtime_path.startswith("/")
            or not str(payload.get("helper_receipt_id") or "").strip()
        ):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH",
                "trusted-direct-bind helper evidence does not match the selected runtime and source.",
            )
        try:
            verified_at = float(payload["verified_at"])
            expires_at = float(payload["expires_at"])
        except (TypeError, ValueError) as exc:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "trusted-direct-bind helper receipt timestamps are invalid.",
            ) from exc
        now = self.clock()
        if verified_at > now or expires_at <= now or expires_at - verified_at > 300:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_STALE",
                "trusted-direct-bind helper evidence is stale or has an unsafe lifetime.",
            )
        capability_payload = {
            "backend": runtime.backend,
            "backend_instance_id": runtime.instance_id,
            "backend_fingerprint": runtime.fingerprint,
            "capability": WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND,
            "live": True,
        }
        return {
            "runtime_workspace_path": runtime_path,
            "helper_receipt_sha256": supplied_digest,
            "capability_evidence_sha256": hashlib.sha256(
                json.dumps(
                    capability_payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        }

    def _run_helper(self, operation: str, *, binding: WorkerBeeWorkspaceBinding) -> None:
        if not self.helper_bin:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_HELPER_NOT_CONFIGURED",
                "WorkerBee workspace materializer is not configured.",
            )
        result = self.runner(
            [
                self.helper_bin,
                operation,
                "--backend",
                binding.backend,
                "--binding-id",
                binding.binding_id,
                "--runtime-workspace-path",
                binding.runtime_workspace_path,
                "--content-sha256",
                binding.content_sha256,
                "--json",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "materializer failed").strip()
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_RELEASE_FAILED",
                f"WorkerBee workspace release failed: {detail}",
            )

    def _receipt_path(self, binding_id: str) -> Path:
        if not _SAFE_ID.fullmatch(binding_id):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_WORKSPACE_BINDING_ID_INVALID",
                "WorkerBee workspace binding ID is invalid.",
            )
        return self.state_root / "bindings" / f"{binding_id}.json"

    @staticmethod
    def _safe_project(project: str) -> str:
        selected = str(project or "").strip()
        if not _SAFE_ID.fullmatch(selected):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_PROJECT_INVALID",
                "WorkerBee project must use letters, numbers, dots, underscores, or hyphens.",
            )
        return selected

    @staticmethod
    def _source_identity(
        source: Path,
        *,
        materialized: bool = False,
        include_paths: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if not source.is_dir():
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_SOURCE_UNAVAILABLE",
                "WorkerBee source checkout is unavailable.",
            )
        root = _git(source, "rev-parse", "--show-toplevel")
        commit = _git(source, "rev-parse", "HEAD")
        if not root or Path(root).resolve() != source or not re.fullmatch(r"[a-f0-9]{40}", commit):
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_SOURCE_GIT_INVALID",
                "WorkerBee source must be the root of a Git checkout with a commit.",
            )
        result = subprocess.run(
            ["git", "-C", str(source), "status", "--porcelain=v1", "-z"],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_SOURCE_GIT_INVALID",
                "Unable to inspect WorkerBee source status.",
            )
        identity = {
            "source_commit": commit,
            "source_dirty": bool(result.stdout),
            "source_status_sha256": hashlib.sha256(result.stdout).hexdigest(),
        }
        if materialized:
            identity["materialization_sha256"] = (
                WorkerBeeWorkspaceService._materialization_identity(
                    source,
                    include_paths=include_paths,
                )
            )
        return identity

    @staticmethod
    def _materialization_identity(
        source: Path,
        *,
        include_paths: tuple[str, ...],
    ) -> str:
        """Fingerprint dirty tracked/submodule state and explicit extra content.

        WorkerBee remains authoritative for validating and hashing the staged
        snapshot. This host-side fingerprint only prevents two different dirty
        snapshots from being assigned the same content-addressed import path.
        """

        diff = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "diff",
                "--binary",
                "--submodule=diff",
                "HEAD",
                "--",
            ],
            capture_output=True,
            check=False,
        )
        if diff.returncode != 0:
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_SOURCE_GIT_INVALID",
                "Unable to fingerprint WorkerBee materialized source changes.",
            )
        digest = hashlib.sha256()
        digest.update(diff.stdout)
        for configured in sorted(set(include_paths)):
            encoded = configured.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            WorkerBeeWorkspaceService._hash_explicit_include(
                digest,
                source=source,
                relative=Path(configured),
            )
        return digest.hexdigest()

    @staticmethod
    def _hash_explicit_include(
        digest: Any,
        *,
        source: Path,
        relative: Path,
    ) -> None:
        selected = source / relative
        candidates = [selected]
        if selected.is_dir() and not selected.is_symlink():
            candidates.extend(sorted(selected.rglob("*")))
        for path in candidates:
            path_relative = path.relative_to(source).as_posix().encode("utf-8")
            digest.update(len(path_relative).to_bytes(8, "big"))
            digest.update(path_relative)
            try:
                path_stat = path.lstat()
            except FileNotFoundError:
                digest.update(b"missing")
                continue
            digest.update(stat.S_IFMT(path_stat.st_mode).to_bytes(8, "big"))
            if stat.S_ISLNK(path_stat.st_mode):
                link = os.readlink(path).encode("utf-8", errors="surrogateescape")
                digest.update(len(link).to_bytes(8, "big"))
                digest.update(link)
                continue
            if not stat.S_ISREG(path_stat.st_mode):
                continue
            digest.update(path_stat.st_size.to_bytes(8, "big"))
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)

    @staticmethod
    def _binding_id(
        runtime: WorkerBeeRuntimeSelection,
        project: str,
        identity: Mapping[str, Any],
        *,
        source_mode: str,
        source_mode_version: int = WORKERBEE_SOURCE_MODE_VERSION,
    ) -> str:
        seed_parts = [
            runtime.backend,
            runtime.instance_id,
            str(runtime.generation),
            project,
            str(identity["source_commit"]),
            str(identity["source_status_sha256"]),
        ]
        if identity.get("materialization_sha256"):
            seed_parts.append(str(identity["materialization_sha256"]))
        if source_mode_version > 0:
            seed_parts.extend((str(source_mode_version), source_mode))
        seed = "\0".join(seed_parts)
        return f"wb-{hashlib.sha256(seed.encode()).hexdigest()[:32]}"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""
