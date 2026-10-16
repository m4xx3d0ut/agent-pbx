from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from .security import now_ts
from .store import Store
from .workerbee_workspace import WorkerBeeWorkspaceBinding

WORKERBEE_WORKSPACE_POLICY_PINNED = "pinned"
WORKERBEE_WORKSPACE_POLICY_SAME_REPOSITORY = "same-repository"
WORKERBEE_WORKSPACE_POLICY_SCOPED_WORKBENCH = "scoped-workbench"
WORKERBEE_WORKSPACE_POLICIES = (
    WORKERBEE_WORKSPACE_POLICY_PINNED,
    WORKERBEE_WORKSPACE_POLICY_SAME_REPOSITORY,
    WORKERBEE_WORKSPACE_POLICY_SCOPED_WORKBENCH,
)

WORKERBEE_WORKSPACE_REQUEST_APPROVAL = "approval"
WORKERBEE_WORKSPACE_REQUEST_AUTOMATIC = "automatic"
WORKERBEE_WORKSPACE_REQUEST_DISABLED = "disabled"
WORKERBEE_WORKSPACE_REQUEST_MODES = (
    WORKERBEE_WORKSPACE_REQUEST_APPROVAL,
    WORKERBEE_WORKSPACE_REQUEST_AUTOMATIC,
    WORKERBEE_WORKSPACE_REQUEST_DISABLED,
)

WORKERBEE_WORKSPACE_CONTROL_EDITABLE = "editable"
WORKERBEE_WORKSPACE_CONTROL_READ_ONLY = "read_only"
WORKERBEE_WORKSPACE_CONTROL_HIDDEN = "hidden"
WORKERBEE_WORKSPACE_CONTROL_MODES = (
    WORKERBEE_WORKSPACE_CONTROL_EDITABLE,
    WORKERBEE_WORKSPACE_CONTROL_READ_ONLY,
    WORKERBEE_WORKSPACE_CONTROL_HIDDEN,
)

WORKERBEE_WORKSPACE_CONTROL_MODE_ENV = "AGENT_PBX_WORKERBEE_WORKSPACE_CONTROL_MODE"
WORKERBEE_WORKSPACE_POLICY_CEILING_ENV = "AGENT_PBX_WORKERBEE_WORKSPACE_POLICY_CEILING"
WORKERBEE_WORKSPACE_ROOT_CEILING_ENV = "AGENT_PBX_WORKERBEE_WORKSPACE_ROOT_CEILING_JSON"
WORKERBEE_WORKSPACE_REQUEST_MODE_ENV = "AGENT_PBX_WORKERBEE_WORKSPACE_REQUEST_MODE"

_POLICY_RANK = {
    WORKERBEE_WORKSPACE_POLICY_PINNED: 0,
    WORKERBEE_WORKSPACE_POLICY_SAME_REPOSITORY: 1,
    WORKERBEE_WORKSPACE_POLICY_SCOPED_WORKBENCH: 2,
}
_SAFE_PROJECT = re.compile(r"[^A-Za-z0-9_.-]+")


class WorkerBeeWorkspacePolicyError(RuntimeError):
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


@dataclass(frozen=True, slots=True)
class WorkerBeeWorkspaceAdminPosture:
    control_mode: str
    policy_ceiling: str
    root_ceiling: tuple[str, ...]
    request_mode_override: str | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "control_mode": self.control_mode,
            "policy_ceiling": self.policy_ceiling,
            "root_ceiling": list(self.root_ceiling),
            "request_mode_override": self.request_mode_override,
        }


@dataclass(frozen=True, slots=True)
class WorkerBeeWorkspacePolicy:
    scope: str
    policy: str
    request_mode: str
    allowed_roots: tuple[str, ...]
    managed_scratch_root: str | None
    profile_label: str | None
    updated_by: str | None
    created_at: float | None
    updated_at: float | None

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["allowed_roots"] = list(self.allowed_roots)
        return result


@dataclass(frozen=True, slots=True)
class WorkerBeeWorkspaceTarget:
    source_root: str
    working_subpath: str
    base_project: str
    runtime_project: str
    scenario_label: str | None
    policy: str
    request_mode: str
    requires_transition: bool

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkerBeeWorkspacePolicyService:
    """Resolve daemon-owned WorkerBee workspace policy and journal transitions."""

    def __init__(
        self,
        store: Store,
        *,
        environ: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.store = store
        self.environ = dict(os.environ if environ is None else environ)
        self.runner = runner

    def admin_posture(self) -> WorkerBeeWorkspaceAdminPosture:
        control = self._normalize_choice(
            self.environ.get(WORKERBEE_WORKSPACE_CONTROL_MODE_ENV),
            WORKERBEE_WORKSPACE_CONTROL_MODES,
            WORKERBEE_WORKSPACE_CONTROL_EDITABLE,
            "control mode",
        )
        ceiling = self._normalize_choice(
            self.environ.get(WORKERBEE_WORKSPACE_POLICY_CEILING_ENV),
            WORKERBEE_WORKSPACE_POLICIES,
            WORKERBEE_WORKSPACE_POLICY_SCOPED_WORKBENCH,
            "policy ceiling",
        )
        request_override_raw = str(
            self.environ.get(WORKERBEE_WORKSPACE_REQUEST_MODE_ENV) or ""
        ).strip()
        request_override = (
            self._normalize_choice(
                request_override_raw,
                WORKERBEE_WORKSPACE_REQUEST_MODES,
                WORKERBEE_WORKSPACE_REQUEST_APPROVAL,
                "request mode override",
            )
            if request_override_raw
            else None
        )
        return WorkerBeeWorkspaceAdminPosture(
            control_mode=control,
            policy_ceiling=ceiling,
            root_ceiling=self._environment_roots(
                WORKERBEE_WORKSPACE_ROOT_CEILING_ENV
            ),
            request_mode_override=request_override,
        )

    def get_policy(self, agent_id: str | None = None) -> dict[str, Any]:
        global_policy = self._load("global") or self._default_policy()
        entity_policy = self._load(self._entity_scope(agent_id)) if agent_id else None
        requested = entity_policy or global_policy
        posture = self.admin_posture()
        effective_policy = requested.policy
        constrained = False
        if _POLICY_RANK[effective_policy] > _POLICY_RANK[posture.policy_ceiling]:
            effective_policy = posture.policy_ceiling
            constrained = True
        request_mode = posture.request_mode_override or requested.request_mode
        allowed_roots = tuple(
            root
            for root in requested.allowed_roots
            if not posture.root_ceiling
            or self._within_any(Path(root), posture.root_ceiling)
        )
        if len(allowed_roots) != len(requested.allowed_roots):
            constrained = True
        effective = WorkerBeeWorkspacePolicy(
            scope=requested.scope,
            policy=effective_policy,
            request_mode=request_mode,
            allowed_roots=allowed_roots,
            managed_scratch_root=(
                requested.managed_scratch_root
                if not requested.managed_scratch_root
                or not posture.root_ceiling
                or self._within_any(
                    Path(requested.managed_scratch_root), posture.root_ceiling
                )
                else None
            ),
            profile_label=requested.profile_label,
            updated_by=requested.updated_by,
            created_at=requested.created_at,
            updated_at=requested.updated_at,
        )
        return {
            "api_version": "agent-pbx.workerbee-workspace-policy/v1",
            "global": global_policy.public_dict(),
            "entity": entity_policy.public_dict() if entity_policy else None,
            "effective": effective.public_dict(),
            "admin": posture.public_dict(),
            "constrained": constrained,
        }

    def set_policy(
        self,
        *,
        agent_id: str | None,
        policy: str,
        request_mode: str,
        allowed_roots: list[str] | tuple[str, ...],
        managed_scratch_root: str | None,
        profile_label: str | None,
        updated_by: str | None,
    ) -> dict[str, Any]:
        posture = self.admin_posture()
        if posture.control_mode != WORKERBEE_WORKSPACE_CONTROL_EDITABLE:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_POLICY_LOCKED",
                "WorkerBee workspace policy is administrator-managed.",
            )
        selected_policy = self._normalize_choice(
            policy,
            WORKERBEE_WORKSPACE_POLICIES,
            WORKERBEE_WORKSPACE_POLICY_PINNED,
            "workspace policy",
        )
        if _POLICY_RANK[selected_policy] > _POLICY_RANK[posture.policy_ceiling]:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_POLICY_CEILING",
                f"Policy {selected_policy!r} exceeds the administrator ceiling.",
            )
        selected_request_mode = self._normalize_choice(
            request_mode,
            WORKERBEE_WORKSPACE_REQUEST_MODES,
            WORKERBEE_WORKSPACE_REQUEST_APPROVAL,
            "request mode",
        )
        roots = self._normalize_roots(allowed_roots)
        for root in roots:
            if posture.root_ceiling and not self._within_any(
                Path(root), posture.root_ceiling
            ):
                raise WorkerBeeWorkspacePolicyError(
                    "WORKERBEE_WORKSPACE_ROOT_CEILING",
                    f"Allowed root {root!r} is outside the administrator root ceiling.",
                )
        scratch = self._normalize_optional_root(managed_scratch_root)
        if scratch and posture.root_ceiling and not self._within_any(
            Path(scratch), posture.root_ceiling
        ):
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_ROOT_CEILING",
                "Managed scratch root is outside the administrator root ceiling.",
            )
        if scratch:
            Path(scratch).mkdir(parents=True, exist_ok=True, mode=0o700)
        if selected_policy == WORKERBEE_WORKSPACE_POLICY_SCOPED_WORKBENCH and not roots:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_ROOT_REQUIRED",
                "Scoped workbench policy requires at least one allowed root.",
            )
        scope = self._entity_scope(agent_id) if agent_id else "global"
        current = now_ts()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO workerbee_workspace_policies(
                    scope, policy, request_mode, allowed_roots_json,
                    managed_scratch_root, profile_label, updated_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope) DO UPDATE SET
                    policy = excluded.policy,
                    request_mode = excluded.request_mode,
                    allowed_roots_json = excluded.allowed_roots_json,
                    managed_scratch_root = excluded.managed_scratch_root,
                    profile_label = excluded.profile_label,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    scope,
                    selected_policy,
                    selected_request_mode,
                    json.dumps(list(roots)),
                    scratch,
                    self._optional_text(profile_label, 120),
                    self._optional_text(updated_by, 255),
                    current,
                    current,
                ),
            )
        result = self.get_policy(agent_id)
        self.store.append_event(
            "workerbee_workspace_policy_updated",
            {
                "scope": scope,
                "policy": selected_policy,
                "request_mode": selected_request_mode,
                "allowed_root_count": len(roots),
                "managed_scratch_configured": bool(scratch),
                "profile_label": self._optional_text(profile_label, 120),
                "updated_by": self._optional_text(updated_by, 255),
            },
            agent_id,
        )
        return result

    def clear_policy(self, agent_id: str, *, updated_by: str | None) -> dict[str, Any]:
        posture = self.admin_posture()
        if posture.control_mode != WORKERBEE_WORKSPACE_CONTROL_EDITABLE:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_POLICY_LOCKED",
                "WorkerBee workspace policy is administrator-managed.",
            )
        scope = self._entity_scope(agent_id)
        with self.store.connect() as conn:
            conn.execute(
                "DELETE FROM workerbee_workspace_policies WHERE scope = ?", (scope,)
            )
        self.store.append_event(
            "workerbee_workspace_policy_inherited",
            {"scope": scope, "updated_by": self._optional_text(updated_by, 255)},
            agent_id,
        )
        return self.get_policy(agent_id)

    def resolve_target(
        self,
        *,
        agent_id: str,
        binding: WorkerBeeWorkspaceBinding,
        candidate_cwd: str | None,
        requested_project: str | None,
        current_working_subpath: str = ".",
        allow_disabled: bool = False,
    ) -> WorkerBeeWorkspaceTarget:
        policy_view = self.get_policy(agent_id)
        effective = policy_view["effective"]
        policy = str(effective["policy"])
        request_mode = str(effective["request_mode"])
        candidate = self._candidate_path(
            binding,
            candidate_cwd,
            current_working_subpath=current_working_subpath,
        )
        root, common = self._git_identity(candidate)
        current_root = Path(binding.source_root).expanduser().resolve()
        current_common = self._git_identity(current_root)[1]
        if policy == WORKERBEE_WORKSPACE_POLICY_PINNED and root != current_root:
            self._deny("This session is pinned to its current Git checkout.")
        if (
            policy == WORKERBEE_WORKSPACE_POLICY_SAME_REPOSITORY
            and common != current_common
        ):
            self._deny("This session may move only within the same Git repository.")
        if policy == WORKERBEE_WORKSPACE_POLICY_SCOPED_WORKBENCH:
            roots = tuple(str(item) for item in effective.get("allowed_roots") or ())
            if not roots or not self._within_any(root, roots):
                self._deny("The requested Git checkout is outside the allowed workbench roots.")
        try:
            owner_uid = root.stat().st_uid
        except OSError as exc:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_TARGET_UNREADABLE",
                f"Unable to inspect requested workspace: {exc}",
            ) from exc
        if owner_uid != os.getuid():
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_OWNER_MISMATCH",
                "The requested Git checkout is not owned by the Agent PBX user.",
            )
        subpath = candidate.relative_to(root).as_posix() or "."
        base_project = (
            self._base_project(binding)
            if common == current_common
            else self._safe_project_name(root.name)
        )
        requested = str(requested_project or "").strip()
        scenario = requested if requested and requested not in {binding.project, base_project} else None
        runtime_project = (
            self._scenario_project(base_project, scenario) if scenario else base_project
        )
        changed = (
            root != current_root
            or subpath != (str(current_working_subpath or ".").strip() or ".")
            or runtime_project != binding.project
        )
        if (
            changed
            and request_mode == WORKERBEE_WORKSPACE_REQUEST_DISABLED
            and not allow_disabled
        ):
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_REQUEST_DISABLED",
                "Workspace changes are disabled by Agent PBX policy.",
            )
        return WorkerBeeWorkspaceTarget(
            source_root=str(root),
            working_subpath=subpath,
            base_project=base_project,
            runtime_project=runtime_project,
            scenario_label=scenario,
            policy=policy,
            request_mode=request_mode,
            requires_transition=changed,
        )

    def create_transition(
        self,
        *,
        agent_id: str,
        lease_id: str,
        from_binding_id: str,
        target: WorkerBeeWorkspaceTarget,
        status: str,
        requested_by: str | None,
        reason: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        transition_id = uuid.uuid4().hex
        current = now_ts()
        approved_at = current if status == "approved" else None
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO workerbee_workspace_transitions(
                    transition_id, agent_id, lease_id, from_binding_id,
                    source_root, working_subpath, base_project, runtime_project,
                    scenario_label, policy, request_mode, status, reason,
                    requested_by, approved_by, metadata_json, created_at, approved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition_id,
                    agent_id,
                    lease_id,
                    from_binding_id,
                    target.source_root,
                    target.working_subpath,
                    target.base_project,
                    target.runtime_project,
                    target.scenario_label,
                    target.policy,
                    target.request_mode,
                    status,
                    self._optional_text(reason, 2000),
                    self._optional_text(requested_by, 255),
                    self._optional_text(approved_by, 255),
                    json.dumps(dict(metadata or {})),
                    current,
                    approved_at,
                ),
            )
        transition = self.get_transition(transition_id)
        assert transition is not None
        self.store.append_event(
            "workerbee_workspace_transition_requested",
            self._transition_event(transition),
            agent_id,
        )
        return transition

    def approve_transition(self, transition_id: str, approved_by: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT status FROM workerbee_workspace_transitions WHERE transition_id = ?",
                (transition_id,),
            ).fetchone()
            if row is None:
                raise WorkerBeeWorkspacePolicyError(
                    "WORKERBEE_WORKSPACE_TRANSITION_NOT_FOUND",
                    "Workspace transition was not found.",
                )
            if str(row["status"]) not in {"pending", "approved"}:
                raise WorkerBeeWorkspacePolicyError(
                    "WORKERBEE_WORKSPACE_TRANSITION_NOT_PENDING",
                    "Only a pending workspace transition can be approved.",
                )
            conn.execute(
                """
                UPDATE workerbee_workspace_transitions
                SET status = 'approved', approved_by = ?, approved_at = ?
                WHERE transition_id = ?
                """,
                (approved_by, now_ts(), transition_id),
            )
        transition = self.get_transition(transition_id)
        assert transition is not None
        self.store.append_event(
            "workerbee_workspace_transition_approved",
            self._transition_event(transition),
            transition["agent_id"],
        )
        return transition

    def find_open_transition(
        self,
        *,
        agent_id: str,
        lease_id: str,
        target: WorkerBeeWorkspaceTarget,
    ) -> dict[str, Any] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM workerbee_workspace_transitions
                WHERE agent_id = ? AND lease_id = ?
                  AND status IN ('pending', 'approved')
                  AND source_root = ? AND working_subpath = ?
                  AND runtime_project = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    agent_id,
                    lease_id,
                    target.source_root,
                    target.working_subpath,
                    target.runtime_project,
                ),
            ).fetchone()
        return self._transition_from_row(row) if row else None

    def update_transition(
        self,
        transition_id: str,
        *,
        status: str,
        to_binding_id: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        timestamp_column = {
            "applied": "applied_at",
            "failed": "failed_at",
            "reverted": "reverted_at",
        }.get(status)
        assignments = ["status = ?", "to_binding_id = COALESCE(?, to_binding_id)"]
        values: list[Any] = [status, to_binding_id]
        if reason is not None:
            assignments.append("reason = ?")
            values.append(self._optional_text(reason, 2000))
        if timestamp_column:
            assignments.append(f"{timestamp_column} = ?")
            values.append(now_ts())
        values.append(transition_id)
        with self.store.connect() as conn:
            conn.execute(
                f"UPDATE workerbee_workspace_transitions SET {', '.join(assignments)} WHERE transition_id = ?",
                values,
            )
        transition = self.get_transition(transition_id)
        if transition is None:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_TRANSITION_NOT_FOUND",
                "Workspace transition was not found.",
            )
        self.store.append_event(
            f"workerbee_workspace_transition_{status}",
            self._transition_event(transition),
            transition["agent_id"],
        )
        return transition

    def get_transition(self, transition_id: str) -> dict[str, Any] | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workerbee_workspace_transitions WHERE transition_id = ?",
                (transition_id,),
            ).fetchone()
        return self._transition_from_row(row) if row else None

    def list_transitions(self, agent_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM workerbee_workspace_transitions
                WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?
                """,
                (agent_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [self._transition_from_row(row) for row in rows]

    def _load(self, scope: str) -> WorkerBeeWorkspacePolicy | None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM workerbee_workspace_policies WHERE scope = ?",
                (scope,),
            ).fetchone()
        if row is None:
            return None
        try:
            roots = json.loads(str(row["allowed_roots_json"] or "[]"))
        except json.JSONDecodeError:
            roots = []
        return WorkerBeeWorkspacePolicy(
            scope=str(row["scope"]),
            policy=str(row["policy"]),
            request_mode=str(row["request_mode"]),
            allowed_roots=tuple(str(item) for item in roots if isinstance(item, str)),
            managed_scratch_root=row["managed_scratch_root"],
            profile_label=row["profile_label"],
            updated_by=row["updated_by"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _default_policy() -> WorkerBeeWorkspacePolicy:
        return WorkerBeeWorkspacePolicy(
            scope="global",
            policy=WORKERBEE_WORKSPACE_POLICY_PINNED,
            request_mode=WORKERBEE_WORKSPACE_REQUEST_APPROVAL,
            allowed_roots=(),
            managed_scratch_root=None,
            profile_label="Safe default",
            updated_by=None,
            created_at=None,
            updated_at=None,
        )

    @staticmethod
    def _entity_scope(agent_id: str | None) -> str:
        selected = str(agent_id or "").strip()
        if not selected or len(selected) > 255:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_AGENT_INVALID",
                "Agent identity is missing or invalid.",
            )
        return f"agent:{selected}"

    def _candidate_path(
        self,
        binding: WorkerBeeWorkspaceBinding,
        candidate_cwd: str | None,
        *,
        current_working_subpath: str,
    ) -> Path:
        supplied = str(candidate_cwd or "").strip()
        source = Path(binding.source_root).expanduser().resolve()
        if not supplied:
            return (source / current_working_subpath).resolve()
        runtime = PurePosixPath(binding.runtime_workspace_path)
        supplied_posix = PurePosixPath(supplied)
        try:
            relative = supplied_posix.relative_to(runtime)
        except ValueError:
            relative = None
        if relative is not None:
            return (source / Path(relative.as_posix())).resolve()
        candidate = Path(supplied).expanduser()
        if not candidate.is_absolute():
            candidate = source / candidate
        return candidate.resolve()

    def _git_identity(self, candidate: Path) -> tuple[Path, Path]:
        if not candidate.is_dir():
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_TARGET_INVALID",
                f"Requested workspace directory does not exist: {candidate}",
            )
        root = self._git(candidate, "--show-toplevel")
        common_raw = self._git(candidate, "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = candidate / common
        return Path(root).resolve(), common.resolve()

    def _git(self, cwd: Path, option: str) -> str:
        result = self.runner(
            ["git", "-C", str(cwd), "rev-parse", option],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        value = str(result.stdout or "").strip()
        if result.returncode != 0 or not value:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_GIT_REQUIRED",
                f"Requested path is not inside an accessible Git checkout: {cwd}",
            )
        return value

    @staticmethod
    def _base_project(binding: WorkerBeeWorkspaceBinding) -> str:
        return str(binding.project or "project").split("--scenario-")[0]

    @staticmethod
    def _scenario_project(base: str, scenario: str) -> str:
        slug = _SAFE_PROJECT.sub("-", scenario.strip()).strip("-._") or "scenario"
        digest = hashlib.sha256(scenario.encode("utf-8")).hexdigest()[:8]
        suffix = f"--scenario-{slug[:48]}-{digest}"
        return f"{base[: max(1, 128 - len(suffix))]}{suffix}"

    @staticmethod
    def _safe_project_name(value: str) -> str:
        selected = _SAFE_PROJECT.sub("-", value.strip()).strip("-._") or "project"
        return selected[:128]

    @staticmethod
    def _within_any(path: Path, roots: tuple[str, ...] | list[str]) -> bool:
        resolved = path.expanduser().resolve()
        for raw in roots:
            root = Path(raw).expanduser().resolve()
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return True
        return False

    @staticmethod
    def _normalize_choice(
        value: str | None,
        choices: tuple[str, ...],
        default: str,
        label: str,
    ) -> str:
        selected = str(value or default).strip().lower().replace("-", "_")
        normalized = {choice.replace("-", "_"): choice for choice in choices}
        if selected not in normalized:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_POLICY_INVALID",
                f"Unsupported WorkerBee workspace {label}: {value!r}",
            )
        return normalized[selected]

    @staticmethod
    def _normalize_roots(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for raw in values:
            path = Path(str(raw)).expanduser().resolve()
            if not path.is_dir():
                raise WorkerBeeWorkspacePolicyError(
                    "WORKERBEE_WORKSPACE_ROOT_INVALID",
                    f"Allowed workspace root does not exist: {path}",
                )
            text = str(path)
            if text not in normalized:
                normalized.append(text)
        return tuple(normalized)

    @staticmethod
    def _normalize_optional_root(value: str | None) -> str | None:
        if not str(value or "").strip():
            return None
        path = Path(str(value)).expanduser().resolve()
        return str(path)

    def _environment_roots(self, name: str) -> tuple[str, ...]:
        raw = str(self.environ.get(name) or "").strip()
        if not raw:
            return ()
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_ENV_INVALID",
                f"{name} must contain a JSON list of directory paths.",
            ) from exc
        if not isinstance(values, list):
            raise WorkerBeeWorkspacePolicyError(
                "WORKERBEE_WORKSPACE_ENV_INVALID",
                f"{name} must contain a JSON list of directory paths.",
            )
        return self._normalize_roots([str(item) for item in values])

    @staticmethod
    def _transition_from_row(row: Any) -> dict[str, Any]:
        data = dict(row)
        try:
            metadata = json.loads(str(data.pop("metadata_json") or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _transition_event(transition: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "transition_id": transition.get("transition_id"),
            "agent_id": transition.get("agent_id"),
            "lease_id": transition.get("lease_id"),
            "from_binding_id": transition.get("from_binding_id"),
            "to_binding_id": transition.get("to_binding_id"),
            "base_project": transition.get("base_project"),
            "runtime_project": transition.get("runtime_project"),
            "scenario_label": transition.get("scenario_label"),
            "policy": transition.get("policy"),
            "request_mode": transition.get("request_mode"),
            "status": transition.get("status"),
            "created_at": transition.get("created_at"),
        }

    @staticmethod
    def _optional_text(value: str | None, limit: int) -> str | None:
        selected = str(value or "").strip()
        return selected[:limit] if selected else None

    @staticmethod
    def _deny(message: str) -> None:
        raise WorkerBeeWorkspacePolicyError(
            "WORKERBEE_WORKSPACE_SCOPE_DENIED",
            message,
            "Choose a workspace allowed by the effective Agent PBX policy or ask a controller to update it.",
        )
