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

WORKERBEE_WORKSPACE_BINDING_API_VERSION = "agent-pbx.workerbee-workspace-binding/v2"
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
    workspace_strategy: str
    runtime_workspace_path: str
    materializer_receipt: str
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
        if self.workspace_strategy != WORKERBEE_WORKSPACE_DIRECT_HOST:
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
    ) -> WorkerBeeWorkspaceBinding:
        selected_project = self._safe_project(project)
        source = Path(source_root).expanduser().resolve()
        backend = canonical_workerbee_backend(runtime.backend)
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
            materialized=(backend != WORKERBEE_BACKEND_OSS),
            include_paths=normalized_include_paths,
        )
        if backend == WORKERBEE_BACKEND_OSS:
            binding = self._direct_binding(
                runtime=runtime,
                project=selected_project,
                source=source,
                identity=identity,
            )
        elif backend in {
            WORKERBEE_BACKEND_PLATFORM_DEBUG,
            WORKERBEE_BACKEND_USER_APPLIANCE,
        }:
            binding = self._materialized_binding(
                runtime=runtime,
                project=selected_project,
                source=source,
                identity=identity,
                include_paths=normalized_include_paths,
            )
        else:  # pragma: no cover - runtime selection validates this first
            raise WorkerBeeWorkspaceError(
                "WORKERBEE_BACKEND_UNSUPPORTED",
                f"Unsupported WorkerBee backend: {backend}",
            )
        _atomic_private_json(self._receipt_path(binding.binding_id), binding.public_dict())
        return binding

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
        if payload.pop("api_version", "") != WORKERBEE_WORKSPACE_BINDING_API_VERSION:
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
        if binding.workspace_strategy != WORKERBEE_WORKSPACE_DIRECT_HOST:
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
    ) -> WorkerBeeWorkspaceBinding:
        now = self.clock()
        return WorkerBeeWorkspaceBinding(
            binding_id=self._binding_id(runtime, project, identity),
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
            workspace_strategy=workspace_strategy_for_backend(runtime.backend),
            runtime_workspace_path=str(source),
            materializer_receipt="",
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
    ) -> WorkerBeeWorkspaceBinding:
        if not self.helper_bin:
            code = (
                "WORKERBEE_USER_APPLIANCE_DEFERRED"
                if runtime.backend == WORKERBEE_BACKEND_USER_APPLIANCE
                else "WORKERBEE_WORKSPACE_HELPER_NOT_CONFIGURED"
            )
            raise WorkerBeeWorkspaceError(
                code,
                "WorkerBee appliance workspace materializer is not configured.",
                f"Set {WORKERBEE_WORKSPACE_HELPER_ENV} to the versioned materializer helper.",
            )
        binding_id = self._binding_id(runtime, project, identity)
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
            workspace_strategy=workspace_strategy_for_backend(runtime.backend),
            runtime_workspace_path=runtime_path,
            materializer_receipt=str(payload.get("receipt_path") or "").strip(),
            state="ready",
            created_at=now,
            verified_at=now,
        )

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

