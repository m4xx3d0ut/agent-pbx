from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .git_worktree import (
    LinkedWorktreeQualificationError,
    qualify_linked_worktree_git_admin,
)
from .managed_runtime import default_managed_project_roots
from .security import now_ts
from .store import Store
from .workerbee_access import WorkerBeeAccessPolicyService
from .workerbee_gateway import (
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_OBSERVE,
)
from .workerbee_policy import WorkerBeeWorkspacePolicyService


OPERATOR_AUTHORITY_SCOPED_EDIT = "scoped-edit"
OPERATOR_AUTHORITY_TRUSTED_WORKBENCH = "trusted-workbench"
OPERATOR_AUTHORITY_FULL_ACCESS = "full-access"
OPERATOR_AUTHORITY_REVIEW_READONLY = "review-readonly"
OPERATOR_AUTHORITY_PROFILES = (
    OPERATOR_AUTHORITY_SCOPED_EDIT,
    OPERATOR_AUTHORITY_TRUSTED_WORKBENCH,
    OPERATOR_AUTHORITY_FULL_ACCESS,
    OPERATOR_AUTHORITY_REVIEW_READONLY,
)

OPERATOR_AUTHORITY_CONTROL_MODE_ENV = "AGENT_PBX_OPERATOR_AUTHORITY_CONTROL_MODE"
OPERATOR_AUTHORITY_PROFILE_CEILING_ENV = "AGENT_PBX_OPERATOR_AUTHORITY_PROFILE_CEILING"
OPERATOR_AUTHORITY_DEFAULT_PROFILE_ENV = "AGENT_PBX_OPERATOR_AUTHORITY_DEFAULT_PROFILE"
OPERATOR_AUTHORITY_ROOT_CEILING_ENV = "AGENT_PBX_OPERATOR_AUTHORITY_ROOT_CEILING_JSON"
OPERATOR_AUTHORITY_WORKFLOW_LABEL_ENV = "AGENT_PBX_OPERATOR_AUTHORITY_WORKFLOW_LABEL"

OPERATOR_AUTHORITY_CONTROL_MODES = ("editable", "read_only", "hidden")
_PROFILE_RANK = {
    OPERATOR_AUTHORITY_REVIEW_READONLY: 0,
    OPERATOR_AUTHORITY_SCOPED_EDIT: 1,
    OPERATOR_AUTHORITY_TRUSTED_WORKBENCH: 2,
    OPERATOR_AUTHORITY_FULL_ACCESS: 3,
}
_DEFAULT_COLLECTION_EXCLUDES = {
    ".agent-pbx-review",
    ".agent-pbx-trash",
    ".git",
    ".venv",
    "node_modules",
    "temp",
    "tmp",
}
_TRUSTED_WORKBENCH_SECRET_NAMES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".envrc",
)


class OperatorAuthorityError(RuntimeError):
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
class OperatorAuthorityPosture:
    control_mode: str
    profile_ceiling: str
    default_profile: str
    root_ceiling: tuple[str, ...]
    workflow_label: str

    def public_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["root_ceiling"] = list(self.root_ceiling)
        return result


@dataclass(frozen=True, slots=True)
class OperatorWorkspaceMember:
    path: str
    git_common_dir: str
    branch: str
    head: str
    linked_worktree: bool
    collection: str | None = None
    git_admin_paths: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class OperatorAuthorityService:
    """Manage bounded execution authority independently from Operator role."""

    def __init__(
        self,
        store: Store,
        *,
        environ: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        workerbee_access: WorkerBeeAccessPolicyService | None = None,
        workerbee_workspace: WorkerBeeWorkspacePolicyService | None = None,
    ) -> None:
        self.store = store
        self.environ = dict(os.environ if environ is None else environ)
        self.runner = runner
        self.workerbee_access = workerbee_access
        self.workerbee_workspace = workerbee_workspace

    def posture(self) -> OperatorAuthorityPosture:
        control = str(
            self.environ.get(OPERATOR_AUTHORITY_CONTROL_MODE_ENV) or "editable"
        ).strip().lower()
        if control not in OPERATOR_AUTHORITY_CONTROL_MODES:
            self._invalid("control mode", control)
        ceiling = self._profile(
            self.environ.get(OPERATOR_AUTHORITY_PROFILE_CEILING_ENV)
            or OPERATOR_AUTHORITY_TRUSTED_WORKBENCH
        )
        if ceiling == OPERATOR_AUTHORITY_REVIEW_READONLY:
            ceiling = OPERATOR_AUTHORITY_SCOPED_EDIT
        default = self._profile(
            self.environ.get(OPERATOR_AUTHORITY_DEFAULT_PROFILE_ENV)
            or OPERATOR_AUTHORITY_SCOPED_EDIT
        )
        if default == OPERATOR_AUTHORITY_REVIEW_READONLY:
            default = OPERATOR_AUTHORITY_SCOPED_EDIT
        if _PROFILE_RANK[default] > _PROFILE_RANK[ceiling]:
            default = ceiling
        roots = self._environment_roots()
        if not roots:
            roots = tuple(
                str(path) for path in default_managed_project_roots(environ=self.environ)
            )
        roots = self._collapse_roots(roots)
        label = str(
            self.environ.get(OPERATOR_AUTHORITY_WORKFLOW_LABEL_ENV) or "Safe Operator"
        ).strip()[:120]
        return OperatorAuthorityPosture(
            control_mode=control,
            profile_ceiling=ceiling,
            default_profile=default,
            root_ceiling=roots,
            workflow_label=label or "Safe Operator",
        )

    def discover_collection(
        self,
        collection: str,
        *,
        max_depth: int = 1,
    ) -> dict[str, Any]:
        root = self._canonical_directory(collection, label="collection")
        self._require_under_ceiling(root)
        if root.stat().st_uid != os.getuid():
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_OWNER_MISMATCH",
                f"Collection is not owned by the Agent PBX user: {root}",
            )
        depth_limit = max(1, min(int(max_depth), 4))
        members: dict[str, OperatorWorkspaceMember] = {}
        queue: list[tuple[Path, int]] = [(root, 0)]
        while queue:
            parent, depth = queue.pop(0)
            if depth >= depth_limit:
                continue
            try:
                children = sorted(parent.iterdir(), key=lambda item: item.name.casefold())
            except OSError:
                continue
            for child in children:
                if child.name in _DEFAULT_COLLECTION_EXCLUDES or child.name.startswith("."):
                    continue
                if child.is_symlink() or not child.is_dir():
                    continue
                member = self._inspect_git_member(child, collection=str(root), strict=False)
                if member is not None:
                    members[member.path] = member
                else:
                    queue.append((child, depth + 1))
        return {
            "api_version": "agent-pbx.operator-workspace-set/v1",
            "collection": str(root),
            "max_depth": depth_limit,
            "members": [item.public_dict() for item in members.values()],
            "excluded_names": sorted(_DEFAULT_COLLECTION_EXCLUDES),
        }

    def preview_workspace_set(
        self,
        *,
        label: str,
        members: Iterable[str],
        collections: Iterable[str] = (),
        collection_depth: int = 1,
    ) -> dict[str, Any]:
        selected: dict[str, OperatorWorkspaceMember] = {}
        normalized_collections: list[str] = []
        for raw in collections:
            text = str(raw or "").strip()
            if not text:
                continue
            discovered = self.discover_collection(text, max_depth=collection_depth)
            normalized_collections.append(str(discovered["collection"]))
            for item in discovered["members"]:
                member = OperatorWorkspaceMember(**item)
                selected[member.path] = member
        for raw in members:
            text = str(raw or "").strip()
            if not text:
                continue
            member = self._inspect_git_member(
                self._canonical_directory(text, label="workspace member"),
                collection=None,
                strict=True,
            )
            assert member is not None
            selected[member.path] = member
        if not selected:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_SET_EMPTY",
                "A workspace set must contain at least one explicit Git checkout.",
            )
        ordered = tuple(sorted(selected.values(), key=lambda item: item.path.casefold()))
        self._validate_git_collisions(ordered)
        return {
            "api_version": "agent-pbx.operator-workspace-set/v1",
            "label": self._label(label),
            "collections": sorted(set(normalized_collections)),
            "members": [item.public_dict() for item in ordered],
            "member_digest": self._member_digest(ordered),
            "member_count": len(ordered),
            "authority_ceiling": self.posture().public_dict(),
        }

    def save_workspace_set(
        self,
        *,
        set_id: str | None,
        label: str,
        members: Iterable[str],
        collections: Iterable[str] = (),
        collection_depth: int = 1,
        updated_by: str,
    ) -> dict[str, Any]:
        if self.posture().control_mode != "editable":
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_POLICY_LOCKED",
                "Operator authority is administrator-managed.",
            )
        preview = self.preview_workspace_set(
            label=label,
            members=members,
            collections=collections,
            collection_depth=collection_depth,
        )
        selected_id = self._set_id(set_id or preview["label"])
        current = now_ts()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT MAX(version) AS version FROM operator_workspace_set_versions WHERE set_id = ?",
                (selected_id,),
            ).fetchone()
            version = int(row["version"] or 0) + 1
            conn.execute(
                """
                INSERT INTO operator_workspace_set_versions(
                    set_id, version, label, status, collections_json,
                    members_json, member_digest, updated_by, created_at
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
                """,
                (
                    selected_id,
                    version,
                    preview["label"],
                    json.dumps(preview["collections"]),
                    json.dumps(preview["members"]),
                    preview["member_digest"],
                    str(updated_by or "local")[:255],
                    current,
                ),
            )
        result = self.get_workspace_set(selected_id, version=version)
        assert result is not None
        self.store.append_event(
            "operator_workspace_set_saved",
            {
                "set_id": selected_id,
                "version": version,
                "member_count": len(preview["members"]),
                "member_digest": preview["member_digest"],
                "updated_by": str(updated_by or "local")[:255],
            },
            selected_id,
        )
        return result

    def list_workspace_sets(self) -> list[dict[str, Any]]:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT versions.*
                FROM operator_workspace_set_versions AS versions
                JOIN (
                    SELECT set_id, MAX(version) AS version
                    FROM operator_workspace_set_versions GROUP BY set_id
                ) AS latest
                ON versions.set_id = latest.set_id AND versions.version = latest.version
                WHERE versions.status = 'active'
                ORDER BY versions.label COLLATE NOCASE, versions.set_id
                """
            ).fetchall()
        return [self._workspace_set_from_row(row) for row in rows]

    def get_workspace_set(
        self,
        set_id: str,
        *,
        version: int | None = None,
    ) -> dict[str, Any] | None:
        selected_id = self._set_id(set_id)
        with self.store.connect() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT * FROM operator_workspace_set_versions WHERE set_id = ? ORDER BY version DESC LIMIT 1",
                    (selected_id,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM operator_workspace_set_versions WHERE set_id = ? AND version = ?",
                    (selected_id, int(version)),
                ).fetchone()
        return self._workspace_set_from_row(row) if row is not None else None

    def get_assignment(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_AGENT_NOT_FOUND", "Operator is not registered."
            )
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        review = self._is_review(metadata)
        posture = self.posture()
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM operator_authority_assignments WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
        metadata_profile = str(metadata.get("operator_authority_profile") or "").strip()
        requested = str(row["profile"]) if row is not None else (
            OPERATOR_AUTHORITY_REVIEW_READONLY
            if review
            else self._profile(metadata_profile) if metadata_profile else posture.default_profile
        )
        effective = OPERATOR_AUTHORITY_REVIEW_READONLY if review else requested
        constrained = False
        if not review and _PROFILE_RANK[effective] > _PROFILE_RANK[posture.profile_ceiling]:
            effective = posture.profile_ceiling
            constrained = True
        workspace_set = None
        if row is not None and row["workspace_set_id"]:
            workspace_set = self.get_workspace_set(
                str(row["workspace_set_id"]), version=int(row["workspace_set_version"])
            )
        elif metadata.get("operator_workspace_set_id"):
            raw_version = metadata.get("operator_workspace_set_version")
            workspace_set = self.get_workspace_set(
                str(metadata["operator_workspace_set_id"]),
                version=int(raw_version) if raw_version is not None else None,
            )
        return {
            "api_version": "agent-pbx.operator-authority/v1",
            "agent_id": agent_id,
            "operator_role": str(metadata.get("operator_role") or ""),
            "access_mode": str(metadata.get("access_mode") or ""),
            "requested_profile": requested,
            "effective_profile": effective,
            "profile_label": str(row["profile_label"]) if row is not None else str(
                metadata.get("operator_authority_profile_label")
                or ("Review read-only" if review else posture.workflow_label)
            ),
            "workspace_set": workspace_set,
            "review_readonly": review,
            "constrained": constrained,
            "admin": posture.public_dict(),
            "requires_restart_on_change": True,
            "pending_activation": bool(
                metadata.get("operator_authority_requires_restart")
            ),
            "activated_profile": metadata.get(
                "operator_authority_activated_profile"
            ),
            "activated_workspace_set_digest": metadata.get(
                "operator_authority_activated_workspace_set_digest"
            ),
            "activated_at": metadata.get("operator_authority_activated_at"),
            "updated_at": float(row["updated_at"]) if row is not None else None,
        }

    def set_assignment(
        self,
        agent_id: str,
        *,
        profile: str,
        workspace_set_id: str | None,
        updated_by: str,
        profile_label: str | None = None,
    ) -> dict[str, Any]:
        posture = self.posture()
        if posture.control_mode != "editable":
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_POLICY_LOCKED",
                "Operator authority is administrator-managed.",
            )
        agent = self.store.get_agent(agent_id)
        if agent is None:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_AGENT_NOT_FOUND", "Operator is not registered."
            )
        if str(agent.get("agent_type") or "") != "operator":
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_ROLE_REQUIRED",
                "Execution authority profiles apply only to Operators and their forks.",
            )
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        selected = self._profile(profile)
        if self._is_review(metadata):
            if selected != OPERATOR_AUTHORITY_REVIEW_READONLY:
                raise OperatorAuthorityError(
                    "OPERATOR_AUTHORITY_REVIEW_READONLY",
                    "Review forks cannot receive edit or deployment authority.",
                )
        elif selected == OPERATOR_AUTHORITY_REVIEW_READONLY:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_PROFILE_INVALID",
                "Review read-only authority is reserved for review forks.",
            )
        elif _PROFILE_RANK[selected] > _PROFILE_RANK[posture.profile_ceiling]:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_PROFILE_CEILING",
                f"Profile {selected!r} exceeds the administrator ceiling.",
            )
        workspace_set = None
        if selected == OPERATOR_AUTHORITY_TRUSTED_WORKBENCH:
            if not str(workspace_set_id or "").strip():
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_SET_REQUIRED",
                    "Trusted Workbench authority requires a versioned workspace set.",
                )
            workspace_set = self.get_workspace_set(str(workspace_set_id))
            if workspace_set is None or workspace_set["status"] != "active":
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_SET_NOT_FOUND",
                    "The selected workspace set is unavailable.",
                )
            self.validate_workspace_set(workspace_set)
        elif workspace_set_id:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_SET_UNUSED",
                "Workspace sets apply only to Trusted Workbench authority.",
            )
        label = str(
            profile_label
            or ("Rosie Workbench" if selected == OPERATOR_AUTHORITY_TRUSTED_WORKBENCH else selected)
        ).strip()[:120]
        roots = [item["path"] for item in (workspace_set or {}).get("members", [])]
        git_admin_paths = sorted(
            {
                str(path)
                for item in (workspace_set or {}).get("members", [])
                for path in item.get("git_admin_paths", [])
            }
        )
        git_common_dirs = sorted(
            {
                str(item.get("git_common_dir"))
                for item in (workspace_set or {}).get("members", [])
                if str(item.get("git_common_dir") or "").strip()
            }
        )
        current = now_ts()
        with self.store.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_authority_assignments(
                    agent_id, profile, profile_label, workspace_set_id,
                    workspace_set_version, workspace_set_digest, updated_by,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    profile = excluded.profile,
                    profile_label = excluded.profile_label,
                    workspace_set_id = excluded.workspace_set_id,
                    workspace_set_version = excluded.workspace_set_version,
                    workspace_set_digest = excluded.workspace_set_digest,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    agent_id,
                    selected,
                    label,
                    workspace_set["set_id"] if workspace_set else None,
                    workspace_set["version"] if workspace_set else None,
                    workspace_set["member_digest"] if workspace_set else None,
                    str(updated_by or "local")[:255],
                    current,
                    current,
                ),
            )
        patch: dict[str, Any] = {
            "operator_authority_profile": selected,
            "operator_authority_profile_label": label,
            "operator_authority_workspace_roots": roots,
            "operator_authority_git_admin_paths": git_admin_paths,
            "operator_authority_git_common_dirs": git_common_dirs,
            "operator_authority_requires_restart": True,
            "operator_authority_updated_by": str(updated_by or "local")[:255],
            "operator_workspace_set_id": workspace_set["set_id"] if workspace_set else None,
            "operator_workspace_set_version": workspace_set["version"] if workspace_set else None,
            "operator_workspace_set_digest": workspace_set["member_digest"] if workspace_set else None,
        }
        self.store.merge_agent_metadata(
            agent_id,
            patch,
            event_type="operator_authority_assignment_updated",
            event_payload={
                "agent_id": agent_id,
                "profile": selected,
                "profile_label": label,
                "workspace_set_id": workspace_set["set_id"] if workspace_set else None,
                "workspace_set_version": workspace_set["version"] if workspace_set else None,
                "workspace_set_digest": workspace_set["member_digest"] if workspace_set else None,
                "updated_by": str(updated_by or "local")[:255],
            },
        )
        self.store.merge_operator_fork_metadata(agent_id, patch)
        self._coordinate_workerbee(agent_id, selected, roots, label, updated_by)
        return self.get_assignment(agent_id)

    def mark_activated(self, agent_id: str) -> dict[str, Any]:
        view = self.get_assignment(agent_id)
        workspace_set = view.get("workspace_set") or {}
        if view["effective_profile"] == OPERATOR_AUTHORITY_TRUSTED_WORKBENCH:
            if not workspace_set:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_SET_NOT_FOUND",
                    "Trusted Workbench activation requires its pinned workspace set.",
                )
            self.validate_workspace_set(workspace_set)
        activation = {
            "operator_authority_requires_restart": False,
            "operator_authority_activated_at": now_ts(),
            "operator_authority_activated_profile": view["effective_profile"],
            "operator_authority_activated_workspace_set_digest": workspace_set.get(
                "member_digest"
            ),
        }
        updated = self.store.merge_agent_metadata(
            agent_id,
            activation,
            event_type="operator_authority_activated",
            event_payload={
                "agent_id": agent_id,
                "profile": view["effective_profile"],
                "workspace_set_id": workspace_set.get("set_id"),
                "workspace_set_version": workspace_set.get("version"),
                "workspace_set_digest": workspace_set.get("member_digest"),
            },
        )
        if updated is None:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_AGENT_NOT_FOUND", "Operator is not registered."
            )
        # Operator fork identity and policy are hydrated from the durable fork
        # record over the Agent row. Keep the activation marker aligned there
        # as well, otherwise a successful edit-fork restart still appears
        # pending as soon as the Agent is read back.
        self.store.merge_operator_fork_metadata(agent_id, activation)
        return self.get_assignment(agent_id)

    def validate_workspace_set(self, workspace_set: Mapping[str, Any]) -> dict[str, Any]:
        """Requalify pinned members before a launch is recorded as active."""

        current: list[OperatorWorkspaceMember] = []
        for raw in workspace_set.get("members") or ():
            if not isinstance(raw, Mapping) or not str(raw.get("path") or "").strip():
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_SET_INVALID",
                    "The pinned workspace set contains an invalid member.",
                )
            path = self._canonical_directory(
                str(raw["path"]), label="workspace member"
            )
            member = self._inspect_git_member(
                path,
                collection=(
                    str(raw.get("collection"))
                    if str(raw.get("collection") or "").strip()
                    else None
                ),
                strict=True,
            )
            assert member is not None
            current.append(member)
        ordered = tuple(sorted(current, key=lambda item: item.path.casefold()))
        self._validate_git_collisions(ordered)
        current_digest = self._member_digest(ordered)
        expected_digest = str(workspace_set.get("member_digest") or "")
        if current_digest != expected_digest:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_SET_DRIFT",
                "The pinned workspace topology changed after review; save a new set "
                "version before restart/resume.",
            )
        return {
            "member_digest": current_digest,
            "members": [item.public_dict() for item in ordered],
        }

    def _coordinate_workerbee(
        self,
        agent_id: str,
        profile: str,
        roots: list[str],
        label: str,
        updated_by: str,
    ) -> None:
        if self.workerbee_access is None or self.workerbee_workspace is None:
            return
        deploy = profile in {
            OPERATOR_AUTHORITY_TRUSTED_WORKBENCH,
            OPERATOR_AUTHORITY_FULL_ACCESS,
        }
        selected_workerbee_profile = (
            WORKERBEE_PROFILE_LOCAL_DEPLOY if deploy else WORKERBEE_PROFILE_OBSERVE
        )
        self.workerbee_access.set_profile(
            agent_id,
            profile=selected_workerbee_profile,
            updated_by=updated_by,
        )
        self.store.merge_operator_fork_metadata(
            agent_id,
            {
                "workerbee_mcp_profile": selected_workerbee_profile,
                "workerbee_mcp_profile_updated_by": str(updated_by or "local"),
            },
        )
        if profile == OPERATOR_AUTHORITY_TRUSTED_WORKBENCH:
            self.workerbee_workspace.set_policy(
                agent_id=agent_id,
                policy="scoped-workbench",
                request_mode="automatic",
                allowed_roots=roots,
                managed_scratch_root=None,
                profile_label=label,
                updated_by=updated_by,
            )
        elif profile in {
            OPERATOR_AUTHORITY_SCOPED_EDIT,
            OPERATOR_AUTHORITY_FULL_ACCESS,
        }:
            self.workerbee_workspace.set_policy(
                agent_id=agent_id,
                policy="same-repository",
                request_mode="approval",
                allowed_roots=[],
                managed_scratch_root=None,
                profile_label=label,
                updated_by=updated_by,
            )

    def _inspect_git_member(
        self,
        path: Path,
        *,
        collection: str | None,
        strict: bool,
    ) -> OperatorWorkspaceMember | None:
        self._require_under_ceiling(path)
        if path.stat().st_uid != os.getuid():
            if strict:
                raise OperatorAuthorityError(
                    "OPERATOR_AUTHORITY_OWNER_MISMATCH",
                    f"Workspace member is not owned by the Agent PBX user: {path}",
                )
            return None
        top = self._git_optional(path, "--show-toplevel")
        if not top:
            if strict:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_MEMBER_GIT_REQUIRED",
                    f"Workspace member is not an accessible Git checkout: {path}",
                )
            return None
        resolved_top = Path(top).resolve(strict=True)
        if resolved_top != path:
            if strict:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_MEMBER_TOPLEVEL_REQUIRED",
                    f"Workspace member must be its canonical Git toplevel: {path}",
                )
            return None
        common_raw = self._git(path, "--git-common-dir")
        common = Path(common_raw)
        if not common.is_absolute():
            common = path / common
        common = common.resolve(strict=True)
        branch = self._git_optional(path, "--abbrev-ref", "HEAD") or "HEAD"
        head = self._git_optional(path, "HEAD") or ""
        git_admin_paths: tuple[str, ...] = ()
        if (path / ".git").is_file():
            try:
                plan = qualify_linked_worktree_git_admin(
                    path,
                    approved_roots=[Path(root) for root in self.posture().root_ceiling],
                    fork_purpose="edit",
                    access_mode="edit",
                    codex_home=Path(
                        self.environ.get("CODEX_HOME") or Path.home() / ".codex"
                    ),
                )
            except LinkedWorktreeQualificationError as exc:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_GIT_QUALIFICATION_FAILED",
                    f"Linked worktree Git administration did not qualify: {path}: {exc}",
                ) from exc
            if plan is None:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_GIT_QUALIFICATION_FAILED",
                    f"Linked worktree did not produce a Git administration plan: {path}",
                )
            git_admin_paths = tuple(str(item) for item in plan.writable_paths)
        return OperatorWorkspaceMember(
            path=str(path),
            git_common_dir=str(common),
            branch=branch,
            head=head,
            linked_worktree=(path / ".git").is_file(),
            collection=collection,
            git_admin_paths=git_admin_paths,
        )

    def _validate_git_collisions(self, members: tuple[OperatorWorkspaceMember, ...]) -> None:
        branches: dict[tuple[str, str], str] = {}
        paths: set[str] = set()
        for member in members:
            if member.path in paths:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_SET_DUPLICATE",
                    f"Workspace member is duplicated: {member.path}",
                )
            paths.add(member.path)
            if member.branch == "HEAD":
                continue
            key = (member.git_common_dir, member.branch)
            existing = branches.get(key)
            if existing and existing != member.path:
                raise OperatorAuthorityError(
                    "OPERATOR_WORKSPACE_BRANCH_COLLISION",
                    "Two selected worktrees share the same branch namespace: "
                    f"{member.branch} in {existing} and {member.path}",
                )
            branches[key] = member.path

    def _require_under_ceiling(self, path: Path) -> None:
        roots = self.posture().root_ceiling
        if roots and not any(path.is_relative_to(Path(root)) for root in roots):
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_ROOT_CEILING",
                f"Path is outside the administrator authority ceiling: {path}",
            )

    def _canonical_directory(self, raw: str, *, label: str) -> Path:
        supplied = Path(str(raw)).expanduser()
        try:
            resolved = supplied.resolve(strict=True)
        except OSError as exc:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_PATH_INVALID",
                f"Unable to inspect {label}: {supplied}: {exc}",
            ) from exc
        if not resolved.is_dir():
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_PATH_INVALID",
                f"{label.title()} is not a directory: {resolved}",
            )
        if supplied.absolute() != resolved:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_SYMLINK_REFUSED",
                f"{label.title()} must be a canonical path, not a symlink: {supplied}",
            )
        return resolved

    def _git(self, cwd: Path, *args: str) -> str:
        value = self._git_optional(cwd, *args)
        if not value:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_GIT_INSPECTION_FAILED",
                f"Git inspection failed for {cwd}: {' '.join(args)}",
            )
        return value

    def _git_optional(self, cwd: Path, *args: str) -> str:
        result = self.runner(
            ["git", "-C", str(cwd), "rev-parse", *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            return ""
        return str(result.stdout or "").strip()

    def _environment_roots(self) -> tuple[str, ...]:
        raw = str(self.environ.get(OPERATOR_AUTHORITY_ROOT_CEILING_ENV) or "").strip()
        if not raw:
            return ()
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_ENV_INVALID",
                f"{OPERATOR_AUTHORITY_ROOT_CEILING_ENV} must be a JSON list.",
            ) from exc
        if not isinstance(values, list):
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_ENV_INVALID",
                f"{OPERATOR_AUTHORITY_ROOT_CEILING_ENV} must be a JSON list.",
            )
        roots: list[str] = []
        for raw_root in values:
            path = self._canonical_directory(str(raw_root), label="authority root")
            if path.stat().st_uid != os.getuid():
                raise OperatorAuthorityError(
                    "OPERATOR_AUTHORITY_OWNER_MISMATCH",
                    f"Authority root is not owned by the Agent PBX user: {path}",
                )
            roots.append(str(path))
        return tuple(roots)

    @staticmethod
    def _collapse_roots(roots: Iterable[str]) -> tuple[str, ...]:
        canonical = sorted(
            {str(Path(root).expanduser().resolve()) for root in roots},
            key=lambda value: (len(Path(value).parts), value.casefold()),
        )
        selected: list[Path] = []
        for raw in canonical:
            path = Path(raw)
            if any(path.is_relative_to(parent) for parent in selected):
                continue
            selected.append(path)
        return tuple(str(path) for path in selected)

    @staticmethod
    def _is_review(metadata: Mapping[str, Any]) -> bool:
        purpose = str(metadata.get("fork_purpose") or "").strip().lower()
        access = str(metadata.get("access_mode") or "").strip().lower()
        return purpose == "review" or access in {"review", "review_readonly"}

    @staticmethod
    def _label(value: str) -> str:
        label = str(value or "").strip()
        if not label or len(label) > 120:
            raise OperatorAuthorityError(
                "OPERATOR_WORKSPACE_LABEL_INVALID",
                "Workspace set label must contain 1 to 120 characters.",
            )
        return label

    @staticmethod
    def _set_id(value: str) -> str:
        raw = str(value or "").strip().lower()
        normalized = "".join(
            char if char.isalnum() or char in "._-" else "-" for char in raw
        ).strip(".-")
        return (normalized or uuid.uuid4().hex)[:120]

    @staticmethod
    def _profile(value: str) -> str:
        selected = str(value or "").strip().lower().replace("_", "-")
        if selected not in OPERATOR_AUTHORITY_PROFILES:
            raise OperatorAuthorityError(
                "OPERATOR_AUTHORITY_PROFILE_INVALID",
                f"Unsupported Operator authority profile: {value!r}",
            )
        return selected

    @staticmethod
    def _invalid(label: str, value: str) -> None:
        raise OperatorAuthorityError(
            "OPERATOR_AUTHORITY_CONFIG_INVALID",
            f"Unsupported Operator authority {label}: {value!r}",
        )

    @staticmethod
    def _member_digest(members: tuple[OperatorWorkspaceMember, ...]) -> str:
        # HEAD is retained as audit evidence, but ordinary commits must not make
        # a previously reviewed topology unusable. Membership, branches, Git
        # common directories, and qualified administration paths define the
        # authority boundary.
        payload = json.dumps(
            [
                {
                    key: value
                    for key, value in item.public_dict().items()
                    if key != "head"
                }
                for item in members
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _workspace_set_from_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["collections"] = json.loads(result.pop("collections_json") or "[]")
        result["members"] = json.loads(result.pop("members_json") or "[]")
        result["member_count"] = len(result["members"])
        result["version"] = int(result["version"])
        result["created_at"] = float(result["created_at"])
        return result


def operator_authority_launch_overrides(
    metadata: Mapping[str, Any],
) -> tuple[tuple[str, object], ...]:
    """Return launch-local Codex permission overrides for an assigned profile."""

    profile = str(metadata.get("operator_authority_profile") or "").strip()
    access = str(metadata.get("access_mode") or "").strip().lower()
    purpose = str(metadata.get("fork_purpose") or "").strip().lower()
    if purpose == "review" or access in {"review", "review_readonly"}:
        return ()
    if profile != OPERATOR_AUTHORITY_TRUSTED_WORKBENCH:
        return ()
    raw_roots = metadata.get("operator_authority_workspace_roots")
    roots = tuple(
        dict.fromkeys(
            str(Path(str(item)).expanduser().resolve(strict=False))
            for item in raw_roots or ()
            if str(item or "").strip()
        )
    )
    if not roots:
        return ()
    raw_git_common_dirs = metadata.get("operator_authority_git_common_dirs")
    git_common_dirs = tuple(
        dict.fromkeys(
            str(Path(str(item)).expanduser().resolve(strict=False))
            for item in raw_git_common_dirs or ()
            if str(item or "").strip()
        )
    )
    permission_roots = tuple(dict.fromkeys((*roots, *git_common_dirs)))
    permission_name = "agent-pbx-trusted-workbench"
    definition = {
        "description": "Agent PBX explicit multi-project Trusted Workbench",
        "extends": ":workspace",
        "workspace_roots": {root: True for root in permission_roots},
        "filesystem": {
            ":workspace_roots": {
                ".": "write",
                ".git": "write",
            },
        },
        "network": {
            "enabled": True,
            "allow_local_binding": True,
            "domains": {"*": "allow"},
        },
    }
    # Codex expands filesystem glob rules with ripgrep while building its
    # bubblewrap command. A legitimate project may contain unreadable runtime
    # artifacts (for example a root-owned crash-report directory); recursive
    # ``**/.env*`` rules then make the entire session fail before the first
    # turn. Deny existing common project-root secret files through literal
    # paths. Do not add absent paths: bubblewrap creates read-only mount targets
    # for them in the host checkout. The profile still extends Codex's managed
    # ``:workspace`` baseline for its built-in protections.
    for root in roots:
        for name in _TRUSTED_WORKBENCH_SECRET_NAMES:
            candidate = Path(root) / name
            if candidate.exists() or candidate.is_symlink():
                definition["filesystem"][str(candidate)] = "deny"
    # Trusted Workbench grants the selected checkout roots and retains every
    # authority-qualified common Git directory as an explicit sandbox root.
    # A nested read-only bind can override a writable parent, so nesting alone
    # must not remove the common Git directory. Exact canonical duplicates are
    # collapsed. Do not mount the narrow writable ref/reflog namespaces used
    # by the stricter edit-fork profile:
    # Codex protects metadata-like children below each writable mount and can
    # leave those mount targets inside refs/heads, invalidating Git namespace
    # qualification on the next launch.
    return (
        (f"permissions.{permission_name}", definition),
        ("default_permissions", permission_name),
    )


def operator_authority_sandbox(metadata: Mapping[str, Any]) -> str | None:
    """Return the explicit legacy sandbox switch for exceptional full access."""

    access = str(metadata.get("access_mode") or "").strip().lower()
    purpose = str(metadata.get("fork_purpose") or "").strip().lower()
    if purpose == "review" or access in {"review", "review_readonly"}:
        return "workspace-write"
    if str(metadata.get("operator_authority_profile") or "").strip() == OPERATOR_AUTHORITY_FULL_ACCESS:
        return "danger-full-access"
    return None
