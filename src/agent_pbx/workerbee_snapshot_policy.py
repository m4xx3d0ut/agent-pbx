from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence

from .security import now_ts
from .store import Store
from .workerbee_runtime import (
    WORKERBEE_BACKEND_OSS,
    canonical_workerbee_backend,
)
from .workerbee_workspace import (
    WorkerBeeWorkspaceIncludePolicyError,
    configured_workerbee_workspace_include_paths,
    normalize_workerbee_workspace_include_paths,
)


WORKERBEE_SNAPSHOT_POLICY_TRACKED = "tracked"
WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS = "approved-paths"
WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE = "safe-working-tree"
WORKERBEE_SNAPSHOT_POLICIES = (
    WORKERBEE_SNAPSHOT_POLICY_TRACKED,
    WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS,
    WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
)

WORKERBEE_SNAPSHOT_CONTROL_EDITABLE = "editable"
WORKERBEE_SNAPSHOT_CONTROL_READ_ONLY = "read_only"
WORKERBEE_SNAPSHOT_CONTROL_HIDDEN = "hidden"
WORKERBEE_SNAPSHOT_CONTROL_MODES = (
    WORKERBEE_SNAPSHOT_CONTROL_EDITABLE,
    WORKERBEE_SNAPSHOT_CONTROL_READ_ONLY,
    WORKERBEE_SNAPSHOT_CONTROL_HIDDEN,
)

WORKERBEE_SNAPSHOT_CONTROL_MODE_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_CONTROL_MODE"
WORKERBEE_SNAPSHOT_POLICY_CEILING_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_POLICY_CEILING"
WORKERBEE_SNAPSHOT_ROOT_CEILING_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_ROOT_CEILING_JSON"
WORKERBEE_SNAPSHOT_MAX_BYTES_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_MAX_BYTES"
WORKERBEE_SNAPSHOT_MAX_FILES_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_MAX_FILES"
WORKERBEE_SNAPSHOT_MAX_FILE_BYTES_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_MAX_FILE_BYTES"
WORKERBEE_SNAPSHOT_DEFAULT_POLICY_ENV = "AGENT_PBX_WORKERBEE_SNAPSHOT_DEFAULT_POLICY"
WORKERBEE_WORKFLOW_PRESET_ENV = "AGENT_PBX_WORKERBEE_WORKFLOW_PRESET"
WORKERBEE_WORKFLOW_LABEL_ENV = "AGENT_PBX_WORKERBEE_WORKFLOW_LABEL"

WORKERBEE_WORKFLOW_STANDARD_OSS = "standard-oss"
WORKERBEE_WORKFLOW_PERSONAL_APPLIANCE = "personal-appliance"
WORKERBEE_WORKFLOW_TRUSTED_PLATFORM = "trusted-platform-workbench"
WORKERBEE_WORKFLOW_HIGH_ASSURANCE = "high-assurance-platform"
WORKERBEE_WORKFLOW_PRESETS = (
    WORKERBEE_WORKFLOW_STANDARD_OSS,
    WORKERBEE_WORKFLOW_PERSONAL_APPLIANCE,
    WORKERBEE_WORKFLOW_TRUSTED_PLATFORM,
    WORKERBEE_WORKFLOW_HIGH_ASSURANCE,
)

DEFAULT_SNAPSHOT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_SNAPSHOT_MAX_FILES = 20_000
DEFAULT_SNAPSHOT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_SNAPSHOT_MAX_INCLUDE_ROOTS = 64

_POLICY_RANK = {
    WORKERBEE_SNAPSHOT_POLICY_TRACKED: 0,
    WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS: 1,
    WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE: 2,
}
_BULK_COMPONENTS = frozenset(
    {
        ".cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "target",
        "venv",
    }
)
_SENSITIVE_COMPONENTS = frozenset(
    {
        ".aws",
        ".gnupg",
        ".ssh",
        "credentials",
        "private",
        "secrets",
    }
)
_SENSITIVE_NAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "netrc",
    }
)
_SENSITIVE_SUFFIXES = (
    ".key",
    ".p12",
    ".pfx",
    ".pem",
)


class WorkerBeeSnapshotPolicyError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def public_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class WorkerBeeSnapshotAdminPosture:
    control_mode: str
    policy_ceiling: str
    root_ceiling: tuple[str, ...]
    max_bytes: int
    max_files: int
    max_file_bytes: int

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["root_ceiling"] = list(self.root_ceiling)
        return result


@dataclass(frozen=True, slots=True)
class WorkerBeeSnapshotPolicy:
    scope: str
    source_root: str
    policy: str
    include_paths: tuple[str, ...]
    max_bytes: int
    max_files: int
    max_file_bytes: int
    profile_label: str | None
    updated_by: str | None
    created_at: float | None
    updated_at: float | None

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["include_paths"] = list(self.include_paths)
        return result


@dataclass(frozen=True, slots=True)
class WorkerBeeSnapshotSelection:
    policy: str
    include_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...]
    file_count: int
    total_bytes: int
    compatibility_include_count: int = 0

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["include_paths"] = list(self.include_paths)
        result["excluded_paths"] = list(self.excluded_paths)
        return result


def workerbee_workflow_preset_values(
    preset: str | None,
    *,
    project_roots: Sequence[Path] = (),
) -> dict[str, str]:
    """Return private-environment values for one reviewed workflow preset.

    Presets set defaults and ceilings. They do not select a backend, grant an
    Agent local-deploy, or alter a live gateway lease.
    """

    selected = str(preset or "").strip().lower()
    if not selected:
        return {}
    if selected not in WORKERBEE_WORKFLOW_PRESETS:
        raise ValueError(f"unsupported WorkerBee workflow preset: {preset}")
    roots = [str(Path(root).expanduser().resolve(strict=False)) for root in project_roots]
    root_json = json.dumps(roots, separators=(",", ":"))
    values = {
        WORKERBEE_WORKFLOW_PRESET_ENV: selected,
        WORKERBEE_WORKFLOW_LABEL_ENV: {
            WORKERBEE_WORKFLOW_STANDARD_OSS: "Standard OSS",
            WORKERBEE_WORKFLOW_PERSONAL_APPLIANCE: "Personal WorkerBee Appliance",
            WORKERBEE_WORKFLOW_TRUSTED_PLATFORM: "Trusted Platform Workbench",
            WORKERBEE_WORKFLOW_HIGH_ASSURANCE: "High-Assurance Platform",
        }[selected],
        "AGENT_PBX_WORKERBEE_WORKSPACE_CONTROL_MODE": "editable",
        WORKERBEE_SNAPSHOT_CONTROL_MODE_ENV: "editable",
    }
    if selected == WORKERBEE_WORKFLOW_TRUSTED_PLATFORM:
        values.update(
            {
                "AGENT_PBX_WORKERBEE_WORKSPACE_DEFAULT_POLICY": "scoped-workbench",
                "AGENT_PBX_WORKERBEE_WORKSPACE_DEFAULT_ROOTS_JSON": root_json,
                "AGENT_PBX_WORKERBEE_WORKSPACE_REQUEST_MODE": "automatic",
                "AGENT_PBX_WORKERBEE_WORKSPACE_POLICY_CEILING": "scoped-workbench",
                "AGENT_PBX_WORKERBEE_WORKSPACE_ROOT_CEILING_JSON": root_json,
                WORKERBEE_SNAPSHOT_DEFAULT_POLICY_ENV: WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
                WORKERBEE_SNAPSHOT_POLICY_CEILING_ENV: WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
                WORKERBEE_SNAPSHOT_ROOT_CEILING_ENV: root_json,
            }
        )
    elif selected == WORKERBEE_WORKFLOW_HIGH_ASSURANCE:
        values.update(
            {
                "AGENT_PBX_WORKERBEE_WORKSPACE_DEFAULT_POLICY": "pinned",
                "AGENT_PBX_WORKERBEE_WORKSPACE_REQUEST_MODE": "approval",
                WORKERBEE_SNAPSHOT_DEFAULT_POLICY_ENV: WORKERBEE_SNAPSHOT_POLICY_TRACKED,
                WORKERBEE_SNAPSHOT_POLICY_CEILING_ENV: WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS,
                WORKERBEE_SNAPSHOT_ROOT_CEILING_ENV: root_json,
            }
        )
    else:
        values.update(
            {
                "AGENT_PBX_WORKERBEE_WORKSPACE_DEFAULT_POLICY": "pinned",
                "AGENT_PBX_WORKERBEE_WORKSPACE_REQUEST_MODE": "approval",
                WORKERBEE_SNAPSHOT_DEFAULT_POLICY_ENV: WORKERBEE_SNAPSHOT_POLICY_TRACKED,
                WORKERBEE_SNAPSHOT_POLICY_CEILING_ENV: WORKERBEE_SNAPSHOT_POLICY_APPROVED_PATHS,
            }
        )
    return values


class WorkerBeeSnapshotPolicyService:
    """Resolve host-owned appliance snapshot policy and bounded content roots."""

    def __init__(
        self,
        store: Store,
        *,
        environ: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    ) -> None:
        self.store = store
        self.environ = dict(os.environ if environ is None else environ)
        self.runner = runner

    def admin_posture(self) -> WorkerBeeSnapshotAdminPosture:
        return WorkerBeeSnapshotAdminPosture(
            control_mode=self._choice(
                self.environ.get(WORKERBEE_SNAPSHOT_CONTROL_MODE_ENV),
                WORKERBEE_SNAPSHOT_CONTROL_MODES,
                WORKERBEE_SNAPSHOT_CONTROL_EDITABLE,
                "snapshot control mode",
            ),
            policy_ceiling=self._choice(
                self.environ.get(WORKERBEE_SNAPSHOT_POLICY_CEILING_ENV),
                WORKERBEE_SNAPSHOT_POLICIES,
                WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
                "snapshot policy ceiling",
            ),
            root_ceiling=self._environment_roots(WORKERBEE_SNAPSHOT_ROOT_CEILING_ENV),
            max_bytes=self._positive_int(
                self.environ.get(WORKERBEE_SNAPSHOT_MAX_BYTES_ENV),
                DEFAULT_SNAPSHOT_MAX_BYTES,
                WORKERBEE_SNAPSHOT_MAX_BYTES_ENV,
            ),
            max_files=self._positive_int(
                self.environ.get(WORKERBEE_SNAPSHOT_MAX_FILES_ENV),
                DEFAULT_SNAPSHOT_MAX_FILES,
                WORKERBEE_SNAPSHOT_MAX_FILES_ENV,
            ),
            max_file_bytes=self._positive_int(
                self.environ.get(WORKERBEE_SNAPSHOT_MAX_FILE_BYTES_ENV),
                DEFAULT_SNAPSHOT_MAX_FILE_BYTES,
                WORKERBEE_SNAPSHOT_MAX_FILE_BYTES_ENV,
            ),
        )

    def get_policy(
        self,
        agent_id: str | None = None,
        *,
        source_root: str | Path | None = None,
        backend: str | None = None,
    ) -> dict[str, Any]:
        root = self._normalized_source_root(source_root)
        global_default = self._load("global", "*") or self._default_policy()
        candidates: list[WorkerBeeSnapshotPolicy | None] = []
        if agent_id:
            scope = self._entity_scope(agent_id)
            if root != "*":
                candidates.append(self._load(scope, root))
            candidates.append(self._load(scope, "*"))
        if root != "*":
            candidates.append(self._load("global", root))
        candidates.append(global_default)
        requested = next((item for item in candidates if item is not None), global_default)
        posture = self.admin_posture()
        policy = requested.policy
        constrained = False
        if _POLICY_RANK[policy] > _POLICY_RANK[posture.policy_ceiling]:
            policy = posture.policy_ceiling
            constrained = True
        within_ceiling = root == "*" or not posture.root_ceiling or self._within_any(
            Path(root), posture.root_ceiling
        )
        if not within_ceiling:
            policy = WORKERBEE_SNAPSHOT_POLICY_TRACKED
            constrained = True
        effective = WorkerBeeSnapshotPolicy(
            scope=requested.scope,
            source_root=root,
            policy=policy,
            include_paths=(requested.include_paths if within_ceiling else ()),
            max_bytes=min(requested.max_bytes, posture.max_bytes),
            max_files=min(requested.max_files, posture.max_files),
            max_file_bytes=min(requested.max_file_bytes, posture.max_file_bytes),
            profile_label=requested.profile_label,
            updated_by=requested.updated_by,
            created_at=requested.created_at,
            updated_at=requested.updated_at,
        )
        selected_backend = canonical_workerbee_backend(backend) if backend else ""
        return {
            "api_version": "agent-pbx.workerbee-snapshot-policy/v1",
            "requested": requested.public_dict(),
            "effective": effective.public_dict(),
            "admin": posture.public_dict(),
            "constrained": constrained,
            "applicable": not selected_backend or selected_backend != WORKERBEE_BACKEND_OSS,
            "backend": selected_backend or None,
            "workflow_preset": str(
                self.environ.get(WORKERBEE_WORKFLOW_PRESET_ENV) or ""
            ).strip()
            or None,
            "workflow_label": str(
                self.environ.get(WORKERBEE_WORKFLOW_LABEL_ENV) or ""
            ).strip()
            or None,
        }

    def set_policy(
        self,
        *,
        agent_id: str | None,
        source_root: str | Path | None,
        policy: str,
        include_paths: Sequence[str],
        max_bytes: int | None,
        max_files: int | None,
        max_file_bytes: int | None,
        profile_label: str | None,
        updated_by: str | None,
    ) -> dict[str, Any]:
        posture = self.admin_posture()
        if posture.control_mode != WORKERBEE_SNAPSHOT_CONTROL_EDITABLE:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_POLICY_LOCKED",
                "WorkerBee appliance content policy is administrator-managed.",
            )
        selected = self._choice(
            policy,
            WORKERBEE_SNAPSHOT_POLICIES,
            WORKERBEE_SNAPSHOT_POLICY_TRACKED,
            "snapshot policy",
        )
        if _POLICY_RANK[selected] > _POLICY_RANK[posture.policy_ceiling]:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_POLICY_CEILING",
                f"Snapshot policy {selected!r} exceeds the administrator ceiling.",
            )
        root = self._normalized_source_root(source_root)
        if agent_id and root == "*":
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_SOURCE_REQUIRED",
                "Agent appliance content policy requires an exact source root.",
            )
        if root != "*" and posture.root_ceiling and not self._within_any(
            Path(root), posture.root_ceiling
        ):
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_ROOT_CEILING",
                "Snapshot source root is outside the administrator ceiling.",
            )
        try:
            normalized_paths = normalize_workerbee_workspace_include_paths(
                tuple(include_paths)
            )
        except WorkerBeeWorkspaceIncludePolicyError as exc:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_INCLUDE_INVALID",
                f"WorkerBee appliance content policy {exc}.",
            ) from exc
        if selected == WORKERBEE_SNAPSHOT_POLICY_TRACKED:
            normalized_paths = ()
        requested_limits = (
            self._bounded_limit(max_bytes, posture.max_bytes, "max_bytes"),
            self._bounded_limit(max_files, posture.max_files, "max_files"),
            self._bounded_limit(
                max_file_bytes, posture.max_file_bytes, "max_file_bytes"
            ),
        )
        scope = self._entity_scope(agent_id) if agent_id else "global"
        current = now_ts()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO workerbee_snapshot_policies(
                    scope, source_root, policy, include_paths_json,
                    max_bytes, max_files, max_file_bytes, profile_label,
                    updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, source_root) DO UPDATE SET
                    policy = excluded.policy,
                    include_paths_json = excluded.include_paths_json,
                    max_bytes = excluded.max_bytes,
                    max_files = excluded.max_files,
                    max_file_bytes = excluded.max_file_bytes,
                    profile_label = excluded.profile_label,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    scope,
                    root,
                    selected,
                    json.dumps(list(normalized_paths), separators=(",", ":")),
                    *requested_limits,
                    str(profile_label or "").strip() or None,
                    str(updated_by or "").strip() or None,
                    current,
                    current,
                ),
            )
        return self.get_policy(agent_id, source_root=root)

    def clear_policy(
        self,
        agent_id: str,
        *,
        source_root: str | Path,
    ) -> dict[str, Any]:
        posture = self.admin_posture()
        if posture.control_mode != WORKERBEE_SNAPSHOT_CONTROL_EDITABLE:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_POLICY_LOCKED",
                "WorkerBee appliance content policy is administrator-managed.",
            )
        root = self._normalized_source_root(source_root)
        with self.store.connect() as conn:
            conn.execute(
                "DELETE FROM workerbee_snapshot_policies WHERE scope = ? AND source_root = ?",
                (self._entity_scope(agent_id), root),
            )
        return self.get_policy(agent_id, source_root=root)

    def preview(
        self,
        *,
        agent_id: str,
        source_root: str | Path,
        backend: str,
    ) -> dict[str, Any]:
        view = self.get_policy(agent_id, source_root=source_root, backend=backend)
        selection = self.resolve_selection(
            agent_id=agent_id,
            source_root=source_root,
            backend=backend,
        )
        return {**view, "selection": selection.public_dict()}

    def resolve_include_paths(
        self,
        *,
        agent_id: str,
        source_root: str | Path,
        backend: str,
    ) -> tuple[str, ...]:
        return self.resolve_selection(
            agent_id=agent_id,
            source_root=source_root,
            backend=backend,
        ).include_paths

    def resolve_selection(
        self,
        *,
        agent_id: str,
        source_root: str | Path,
        backend: str,
    ) -> WorkerBeeSnapshotSelection:
        selected_backend = canonical_workerbee_backend(backend)
        if selected_backend == WORKERBEE_BACKEND_OSS:
            return WorkerBeeSnapshotSelection(
                policy="direct-host",
                include_paths=(),
                excluded_paths=(),
                file_count=0,
                total_bytes=0,
            )
        root = Path(source_root).expanduser().resolve()
        view = self.get_policy(agent_id, source_root=root, backend=selected_backend)
        effective = view["effective"]
        policy = str(effective["policy"])
        approved = (
            tuple(str(value) for value in effective.get("include_paths") or ())
            if policy != WORKERBEE_SNAPSHOT_POLICY_TRACKED
            else ()
        )
        try:
            legacy = configured_workerbee_workspace_include_paths(
                agent_id=agent_id,
                source_root=root,
                env=self.environ,
            )
        except WorkerBeeWorkspaceIncludePolicyError as exc:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_COMPATIBILITY_POLICY_INVALID",
                f"Legacy WorkerBee workspace include policy {exc}.",
                "Correct or remove the private compatibility include policy.",
            ) from exc
        selected_paths = tuple(dict.fromkeys((*approved, *legacy)))
        file_count = 0
        total_bytes = 0
        excluded: tuple[str, ...] = ()
        if policy == WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE:
            automatic = self._safe_working_tree_selection(
                root,
                max_files=int(effective["max_files"]),
                max_bytes=int(effective["max_bytes"]),
                max_file_bytes=int(effective["max_file_bytes"]),
            )
            selected_paths = tuple(
                dict.fromkeys((*selected_paths, *automatic.include_paths))
            )
            try:
                selected_paths = normalize_workerbee_workspace_include_paths(
                    selected_paths
                )
            except WorkerBeeWorkspaceIncludePolicyError as exc:
                raise WorkerBeeSnapshotPolicyError(
                    "WORKERBEE_SNAPSHOT_INCLUDE_LIMIT",
                    f"Safe working-tree selection {exc}.",
                    "Approve a smaller generated root set or use tracked content only.",
                ) from exc
            excluded = automatic.excluded_paths
        file_count, total_bytes = self._inspect_explicit_paths(
            root,
            selected_paths,
            max_files=int(effective["max_files"]),
            max_bytes=int(effective["max_bytes"]),
            max_file_bytes=int(effective["max_file_bytes"]),
        )
        return WorkerBeeSnapshotSelection(
            policy=policy,
            include_paths=selected_paths,
            excluded_paths=excluded,
            file_count=file_count,
            total_bytes=total_bytes,
            compatibility_include_count=len(legacy),
        )

    def _safe_working_tree_selection(
        self,
        root: Path,
        *,
        max_files: int,
        max_bytes: int,
        max_file_bytes: int,
    ) -> WorkerBeeSnapshotSelection:
        candidates = self._git_extra_paths(root)
        selected: list[PurePosixPath] = []
        excluded: list[str] = []
        file_count = 0
        total_bytes = 0
        for relative in candidates:
            reason = self._excluded_reason(relative)
            path = root / relative.as_posix()
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if reason:
                excluded.append(f"{relative.as_posix()} ({reason})")
                continue
            if stat.S_ISLNK(metadata.st_mode):
                excluded.append(f"{relative.as_posix()} (symlink)")
                continue
            if not stat.S_ISREG(metadata.st_mode):
                excluded.append(f"{relative.as_posix()} (special-or-directory)")
                continue
            if metadata.st_size > max_file_bytes:
                excluded.append(f"{relative.as_posix()} (file-limit)")
                continue
            file_count += 1
            total_bytes += metadata.st_size
            if file_count > max_files or total_bytes > max_bytes:
                raise WorkerBeeSnapshotPolicyError(
                    "WORKERBEE_SNAPSHOT_LIMIT_EXCEEDED",
                    "Safe working-tree content exceeds the configured snapshot limit.",
                    "Approve narrower generated roots or raise the private administrator ceiling.",
                )
            selected.append(relative)
        includes = self._compress_include_paths(selected, excluded)
        return WorkerBeeSnapshotSelection(
            policy=WORKERBEE_SNAPSHOT_POLICY_SAFE_WORKING_TREE,
            include_paths=includes,
            excluded_paths=tuple(excluded[:100]),
            file_count=file_count,
            total_bytes=total_bytes,
        )

    def _git_extra_paths(self, root: Path) -> tuple[PurePosixPath, ...]:
        values: list[PurePosixPath] = []
        for command in (
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"],
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
            ],
        ):
            result = self.runner(command, capture_output=True, check=False)
            if result.returncode != 0:
                raise WorkerBeeSnapshotPolicyError(
                    "WORKERBEE_SNAPSHOT_GIT_FAILED",
                    "Unable to inspect untracked and ignored workspace content.",
                )
            raw = result.stdout if isinstance(result.stdout, bytes) else str(result.stdout or "").encode()
            for item in raw.split(b"\0"):
                if not item:
                    continue
                value = item.decode("utf-8", errors="surrogateescape")
                path = PurePosixPath(value)
                if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
                    continue
                values.append(path)
        return tuple(sorted(set(values), key=lambda item: item.as_posix()))

    @staticmethod
    def _excluded_reason(path: PurePosixPath) -> str | None:
        lowered = tuple(part.lower() for part in path.parts)
        name = lowered[-1] if lowered else ""
        if any(part in _BULK_COMPONENTS for part in lowered):
            return "bulk-cache"
        if any(part in _SENSITIVE_COMPONENTS for part in lowered):
            return "sensitive-directory"
        if name in _SENSITIVE_NAMES or name.startswith(".env."):
            return "sensitive-name"
        if name.endswith(_SENSITIVE_SUFFIXES):
            return "sensitive-suffix"
        return None

    @staticmethod
    def _compress_include_paths(
        paths: Sequence[PurePosixPath],
        excluded: Sequence[str],
    ) -> tuple[str, ...]:
        blocked = {
            PurePosixPath(item.rsplit(" (", 1)[0])
            for item in excluded
        }

        def blocked_under(prefix: PurePosixPath) -> bool:
            return any(
                candidate == prefix or prefix in candidate.parents
                for candidate in blocked
            )

        grouped: dict[str, list[PurePosixPath]] = {}
        for path in paths:
            grouped.setdefault(path.parts[0], []).append(path)
        result: list[str] = []
        for first, members in sorted(grouped.items()):
            prefix = PurePosixPath(first)
            if len(members) > 1 and not blocked_under(prefix):
                result.append(prefix.as_posix())
            else:
                result.extend(member.as_posix() for member in members)
        if len(result) > DEFAULT_SNAPSHOT_MAX_INCLUDE_ROOTS:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_INCLUDE_LIMIT",
                "Safe working-tree content spans more than 64 generated roots.",
                "Approve a smaller set of generated directories for this project.",
            )
        return tuple(result)

    def _inspect_explicit_paths(
        self,
        root: Path,
        include_paths: Sequence[str],
        *,
        max_files: int,
        max_bytes: int,
        max_file_bytes: int,
    ) -> tuple[int, int]:
        files = 0
        total = 0
        for value in include_paths:
            selected = root / value
            candidates = [selected]
            if selected.is_dir() and not selected.is_symlink():
                candidates.extend(selected.rglob("*"))
            for path in candidates:
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    continue
                if metadata.st_size > max_file_bytes:
                    raise WorkerBeeSnapshotPolicyError(
                        "WORKERBEE_SNAPSHOT_FILE_LIMIT",
                        f"Approved snapshot file exceeds the per-file limit: {path.name}",
                    )
                files += 1
                total += metadata.st_size
                if files > max_files or total > max_bytes:
                    raise WorkerBeeSnapshotPolicyError(
                        "WORKERBEE_SNAPSHOT_LIMIT_EXCEEDED",
                        "Approved appliance content exceeds the configured snapshot limit.",
                    )
        return files, total

    def _load(self, scope: str, source_root: str) -> WorkerBeeSnapshotPolicy | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workerbee_snapshot_policies WHERE scope = ? AND source_root = ?",
                (scope, source_root),
            ).fetchone()
        if row is None:
            return None
        try:
            include_paths = json.loads(str(row["include_paths_json"] or "[]"))
        except json.JSONDecodeError:
            include_paths = []
        return WorkerBeeSnapshotPolicy(
            scope=str(row["scope"]),
            source_root=str(row["source_root"]),
            policy=str(row["policy"]),
            include_paths=tuple(
                str(item) for item in include_paths if isinstance(item, str)
            ),
            max_bytes=int(row["max_bytes"]),
            max_files=int(row["max_files"]),
            max_file_bytes=int(row["max_file_bytes"]),
            profile_label=row["profile_label"],
            updated_by=row["updated_by"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    def _default_policy(self) -> WorkerBeeSnapshotPolicy:
        selected = self._choice(
            self.environ.get(WORKERBEE_SNAPSHOT_DEFAULT_POLICY_ENV),
            WORKERBEE_SNAPSHOT_POLICIES,
            WORKERBEE_SNAPSHOT_POLICY_TRACKED,
            "default snapshot policy",
        )
        return WorkerBeeSnapshotPolicy(
            scope="global",
            source_root="*",
            policy=selected,
            include_paths=(),
            max_bytes=DEFAULT_SNAPSHOT_MAX_BYTES,
            max_files=DEFAULT_SNAPSHOT_MAX_FILES,
            max_file_bytes=DEFAULT_SNAPSHOT_MAX_FILE_BYTES,
            profile_label=(
                str(self.environ.get(WORKERBEE_WORKFLOW_LABEL_ENV) or "").strip()
                or "Safe default"
            ),
            updated_by=None,
            created_at=None,
            updated_at=None,
        )

    @staticmethod
    def _entity_scope(agent_id: str | None) -> str:
        selected = str(agent_id or "").strip()
        if not selected or len(selected) > 255:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_AGENT_INVALID",
                "Agent identity is missing or invalid.",
            )
        return f"agent:{selected}"

    @staticmethod
    def _normalized_source_root(source_root: str | Path | None) -> str:
        value = str(source_root or "").strip()
        if not value or value == "*":
            return "*"
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_SOURCE_INVALID",
                "Snapshot source root must be absolute.",
            )
        return str(path.resolve(strict=False))

    @staticmethod
    def _choice(value: object, choices: Sequence[str], default: str, label: str) -> str:
        selected = str(value or default).strip().lower().replace("_", "-")
        aliases = {"read-only": "read_only", "safe": "safe-working-tree"}
        selected = aliases.get(selected, selected)
        if selected not in choices:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_CONFIG_INVALID",
                f"Unsupported WorkerBee {label}: {value!r}.",
            )
        return selected

    @staticmethod
    def _positive_int(value: object, default: int, label: str) -> int:
        if value in {None, ""}:
            return default
        try:
            selected = int(str(value))
        except ValueError as exc:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_CONFIG_INVALID",
                f"{label} must be a positive integer.",
            ) from exc
        if selected <= 0:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_CONFIG_INVALID",
                f"{label} must be a positive integer.",
            )
        return selected

    @staticmethod
    def _bounded_limit(value: int | None, ceiling: int, label: str) -> int:
        selected = ceiling if value is None else int(value)
        if selected <= 0 or selected > ceiling:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_LIMIT_CEILING",
                f"{label} must be between 1 and the administrator ceiling {ceiling}.",
            )
        return selected

    def _environment_roots(self, key: str) -> tuple[str, ...]:
        raw = str(self.environ.get(key) or "").strip()
        if not raw:
            return ()
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_CONFIG_INVALID",
                f"{key} must be a JSON list of absolute paths.",
            ) from exc
        if not isinstance(values, list):
            raise WorkerBeeSnapshotPolicyError(
                "WORKERBEE_SNAPSHOT_CONFIG_INVALID",
                f"{key} must be a JSON list of absolute paths.",
            )
        roots: list[str] = []
        for item in values:
            path = Path(str(item or "")).expanduser()
            if not path.is_absolute():
                raise WorkerBeeSnapshotPolicyError(
                    "WORKERBEE_SNAPSHOT_CONFIG_INVALID",
                    f"{key} contains a non-absolute path.",
                )
            roots.append(str(path.resolve(strict=False)))
        return tuple(dict.fromkeys(roots))

    @staticmethod
    def _within_any(path: Path, roots: Sequence[str]) -> bool:
        resolved = path.expanduser().resolve(strict=False)
        for root_value in roots:
            root = Path(root_value).expanduser().resolve(strict=False)
            if resolved == root or root in resolved.parents:
                return True
        return False
