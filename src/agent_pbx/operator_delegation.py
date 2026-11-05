from __future__ import annotations

from pathlib import Path, PurePosixPath
import hashlib
import json
import re
from typing import Any, Mapping


HANDOFF_ACTION_CLASSES = frozenset(
    {
        "read_only_verification",
        "test_validation",
        "evidence_review",
        "same_workspace_handoff",
        "exact_git_commit_broker",
        "same_fork_reactivation",
        "host_process_identity_attestation",
        "deployment",
        "publication",
        "provider_action",
        "credential_access",
        "workspace_expansion",
        "authority_elevation",
        "destructive_mutation",
    }
)
HANDOFF_MUTATION_MODES = frozenset({"none", "read_only", "exact_paths", "workspace"})
LEASE_ELIGIBLE_ACTION_CLASSES = frozenset(
    {
        "read_only_verification",
        "test_validation",
        "evidence_review",
        "same_workspace_handoff",
        "exact_git_commit_broker",
        "same_fork_reactivation",
    }
)
ALWAYS_MANUAL_ACTION_CLASSES = HANDOFF_ACTION_CLASSES - LEASE_ELIGIBLE_ACTION_CLASSES
DEFAULT_DENIED_ACTIONS = (
    "credentials",
    "provider_or_cloud",
    "deployment",
    "publication",
    "push",
    "tag",
    "release",
    "repository_creation",
    "worktree_creation",
    "fork_creation",
    "workspace_expansion",
    "authority_elevation",
    "destructive_mutation",
)


def normalize_execution_scope(scope: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and canonicalize a machine-readable Operator handoff scope."""

    if not scope:
        return {}
    action_class = str(scope.get("action_class") or "").strip().lower()
    if action_class not in HANDOFF_ACTION_CLASSES:
        raise ValueError("handoff execution_scope action_class is invalid")
    mutation_mode = str(scope.get("mutation_mode") or "none").strip().lower()
    if mutation_mode not in HANDOFF_MUTATION_MODES:
        raise ValueError("handoff execution_scope mutation_mode is invalid")

    campaign_id = _optional_identifier(scope.get("campaign_id"), "campaign_id")
    workspace_digest = _optional_digest(scope.get("workspace_set_digest"))
    allowed_roots = _absolute_roots(scope.get("allowed_roots"))
    exact_paths = _relative_paths(scope.get("exact_paths"))
    expected_parent = str(scope.get("expected_git_parent") or "").strip() or None
    commit_subject = str(scope.get("commit_subject") or "").strip() or None
    denied_actions = tuple(
        dict.fromkeys(
            (*DEFAULT_DENIED_ACTIONS, *_string_tuple(scope.get("denied_actions") or (), label="denied_actions"))
        )
    )

    if mutation_mode == "exact_paths" and not exact_paths:
        raise ValueError("exact_paths mutation mode requires at least one exact path")
    if action_class == "exact_git_commit_broker":
        if mutation_mode != "exact_paths":
            raise ValueError("exact_git_commit_broker requires exact_paths mutation mode")
        if not expected_parent or not re.fullmatch(r"[0-9a-fA-F]{7,64}", expected_parent):
            raise ValueError("exact_git_commit_broker requires an expected Git parent")
        if not commit_subject:
            raise ValueError("exact_git_commit_broker requires an exact commit subject")
    if mutation_mode == "workspace" and action_class in LEASE_ELIGIBLE_ACTION_CLASSES:
        raise ValueError("lease-eligible action classes may not request workspace-wide mutation")
    if action_class in {"read_only_verification", "evidence_review"} and mutation_mode not in {
        "none",
        "read_only",
    }:
        raise ValueError(f"{action_class} must remain read-only")
    host_process_evidence = None
    if action_class == "host_process_identity_attestation":
        if mutation_mode not in {"none", "read_only"}:
            raise ValueError("host_process_identity_attestation must remain read-only")
        host_process_evidence = _host_process_evidence(
            scope.get("host_process_evidence")
        )

    flags = {
        key: bool(scope.get(key, False))
        for key in (
            "credential_access",
            "provider_action",
            "deployment",
            "publication",
            "destructive",
            "workspace_expansion",
            "authority_elevation",
        )
    }
    manual_flag = {
        "credential_access": "credential_access",
        "provider_action": "provider_action",
        "deployment": "deployment",
        "publication": "publication",
        "workspace_expansion": "workspace_expansion",
        "authority_elevation": "authority_elevation",
        "destructive_mutation": "destructive",
    }.get(action_class)
    if manual_flag is not None:
        flags[manual_flag] = True

    normalized = {
        "version": 1,
        "action_class": action_class,
        "campaign_id": campaign_id,
        "workspace_set_digest": workspace_digest,
        "mutation_mode": mutation_mode,
        "allowed_roots": list(allowed_roots),
        "exact_paths": list(exact_paths),
        "expected_git_parent": expected_parent,
        "commit_subject": commit_subject,
        "denied_actions": list(denied_actions),
        **flags,
    }
    if host_process_evidence is not None:
        normalized["host_process_evidence"] = host_process_evidence
    return normalized


def resolve_handoff_execution_scope(handoff: Mapping[str, Any]) -> dict[str, Any]:
    """Return one canonical handoff scope, including validated legacy records.

    Structured handoffs created by current Agent PBX releases store the scope in
    the top-level ``execution_scope`` column.  A small number of handoffs created
    during the rollout of the feature stored an equivalent, richer description
    under ``metadata.execution_scope``.  Those records are accepted only when
    they can be reduced to the same narrow schema without guessing.

    If both representations are present, their immutable execution fields must
    agree.  A mismatch fails closed instead of silently selecting one source.
    """

    top_level_raw = handoff.get("execution_scope")
    top_level = (
        normalize_execution_scope(top_level_raw)
        if isinstance(top_level_raw, Mapping) and top_level_raw
        else {}
    )
    metadata = handoff.get("metadata")
    legacy_raw = metadata.get("execution_scope") if isinstance(metadata, Mapping) else None
    legacy = _normalize_legacy_execution_scope(legacy_raw)
    if top_level and legacy and execution_scope_digest(top_level) != execution_scope_digest(legacy):
        raise ValueError(
            "handoff top-level and legacy execution scopes do not match"
        )
    return top_level or legacy


def execution_scope_digest(scope: Mapping[str, Any] | None) -> str:
    """Hash a canonical scope for drift detection and action receipts."""

    normalized = normalize_execution_scope(scope)
    if not normalized:
        return ""
    return hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalize_legacy_execution_scope(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        return {}
    action_class = str(value.get("action_class") or "").strip().lower()
    if action_class in HANDOFF_ACTION_CLASSES:
        return normalize_execution_scope(value)
    if "managed_broker" not in action_class:
        raise ValueError("legacy handoff execution_scope action_class is unsupported")

    roots: list[str] = []
    for raw in value.get("allowed_roots") or ():
        if isinstance(raw, Mapping):
            path = str(raw.get("path") or "").strip()
        else:
            path = str(raw or "").strip()
        if path:
            roots.append(path)

    exact_paths: list[str] = []
    for raw in value.get("exact_paths") or ():
        if isinstance(raw, Mapping):
            access = str(raw.get("access") or "").strip().lower()
            path = str(raw.get("path") or "").strip()
            if "broker_commit" not in access:
                continue
        else:
            path = str(raw or "").strip()
        if path:
            exact_paths.append(path)

    expected_parent = str(
        value.get("expected_git_parent") or value.get("expected_parent") or ""
    ).strip()
    commit_subject = str(value.get("commit_subject") or "").strip()
    if not roots or not exact_paths or not expected_parent or not commit_subject:
        raise ValueError("legacy managed-broker scope is incomplete")
    return normalize_execution_scope(
        {
            "action_class": "exact_git_commit_broker",
            "campaign_id": value.get("campaign_id"),
            "workspace_set_digest": value.get("workspace_set_digest"),
            "mutation_mode": "exact_paths",
            "allowed_roots": roots,
            "exact_paths": exact_paths,
            "expected_git_parent": expected_parent,
            "commit_subject": commit_subject,
            "denied_actions": value.get("denied_actions") or (),
        }
    )


def execution_scope_requires_manual_approval(scope: Mapping[str, Any] | None) -> bool:
    if not scope:
        return True
    if str(scope.get("action_class") or "") in ALWAYS_MANUAL_ACTION_CLASSES:
        return True
    return any(
        bool(scope.get(key))
        for key in (
            "credential_access",
            "provider_action",
            "deployment",
            "publication",
            "destructive",
            "workspace_expansion",
            "authority_elevation",
        )
    )


def normalize_delegation_lease_scope(
    *,
    allowed_action_classes: Any,
    allowed_roots: Any,
    exact_paths: Any,
    denied_actions: Any,
) -> dict[str, list[str]]:
    actions = tuple(
        dict.fromkeys(
            item.lower()
            for item in _string_tuple(
                allowed_action_classes,
                label="allowed_action_classes",
            )
        )
    )
    if not actions or any(item not in LEASE_ELIGIBLE_ACTION_CLASSES for item in actions):
        raise ValueError("delegation lease contains an ineligible action class")
    denied = tuple(
        dict.fromkeys(
            (*DEFAULT_DENIED_ACTIONS, *_string_tuple(denied_actions or (), label="denied_actions"))
        )
    )
    return {
        "allowed_action_classes": list(actions),
        "allowed_roots": list(_absolute_roots(allowed_roots or ())),
        "exact_paths": list(_relative_paths(exact_paths or ())),
        "denied_actions": list(denied),
    }


def delegation_lease_matches_handoff(
    lease: Mapping[str, Any],
    handoff: Mapping[str, Any],
    *,
    source_workspace_digest: str,
    target_workspace_digest: str,
) -> tuple[bool, str]:
    scope = handoff.get("execution_scope")
    if not isinstance(scope, Mapping) or not scope:
        return False, "handoff has no structured execution scope"
    if execution_scope_requires_manual_approval(scope):
        return False, "handoff action always requires manual approval"
    if str(lease.get("status") or "") != "approved" or bool(lease.get("expired")):
        return False, "delegation lease is not active"
    if int(lease.get("uses_remaining") or 0) <= 0:
        return False, "delegation lease is exhausted"
    target = str(handoff.get("target_operator_agent_id") or "")
    if target not in set(lease.get("target_operator_agent_ids") or ()):
        return False, "target Operator is outside the lease"
    campaign_id = str(scope.get("campaign_id") or "")
    if not campaign_id or campaign_id not in set(lease.get("campaign_ids") or ()):
        return False, "campaign is outside the lease"
    action_class = str(scope.get("action_class") or "")
    if action_class not in set(lease.get("allowed_action_classes") or ()):
        return False, "action class is outside the lease"
    lease_digest = str(lease.get("workspace_set_digest") or "")
    scope_digest = str(scope.get("workspace_set_digest") or "")
    if not scope_digest or len({lease_digest, scope_digest, source_workspace_digest, target_workspace_digest}) != 1:
        return False, "workspace-set identity does not match the lease"
    if not set(scope.get("allowed_roots") or ()).issubset(
        set(lease.get("allowed_roots") or ())
    ):
        return False, "handoff roots exceed the lease"
    if not set(scope.get("exact_paths") or ()).issubset(
        set(lease.get("exact_paths") or ())
    ):
        return False, "handoff paths exceed the lease"
    if not set(DEFAULT_DENIED_ACTIONS).issubset(set(lease.get("denied_actions") or ())):
        return False, "delegation lease does not preserve mandatory denials"
    return True, "matched"


def _optional_identifier(value: Any, label: str) -> str | None:
    normalized = str(value or "").strip() or None
    if normalized and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", normalized):
        raise ValueError(f"handoff execution_scope {label} is invalid")
    return normalized


def _optional_digest(value: Any) -> str | None:
    normalized = str(value or "").strip().lower() or None
    if normalized and not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValueError("handoff execution_scope workspace_set_digest must be SHA-256")
    return normalized


def _host_process_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(
            "host_process_identity_attestation requires host_process_evidence"
        )
    required = {
        "candidate_pids",
        "expected_executable",
        "expected_argv0",
        "expected_user_namespace",
        "expected_mount_namespace",
    }
    if set(value) != required:
        raise ValueError("host_process_evidence fields are invalid")
    raw_pids = value.get("candidate_pids")
    if not isinstance(raw_pids, (list, tuple)) or not 1 <= len(raw_pids) <= 32:
        raise ValueError(
            "host_process_evidence candidate_pids must contain 1 to 32 PIDs"
        )
    candidate_pids: list[int] = []
    for raw_pid in raw_pids:
        if isinstance(raw_pid, bool) or not isinstance(raw_pid, int) or raw_pid <= 0:
            raise ValueError(
                "host_process_evidence candidate PIDs must be positive integers"
            )
        if raw_pid not in candidate_pids:
            candidate_pids.append(raw_pid)
    if not candidate_pids:
        raise ValueError("host_process_evidence requires at least one unique PID")

    evidence: dict[str, Any] = {"candidate_pids": candidate_pids}
    for key in required - {"candidate_pids"}:
        text = str(value.get(key) or "").strip()
        if not text or len(text) > 1000 or any(
            ord(character) < 32 or ord(character) == 127 for character in text
        ):
            raise ValueError(f"host_process_evidence {key} is invalid")
        evidence[key] = text
    return evidence


def _absolute_roots(value: Any) -> tuple[str, ...]:
    roots = _string_tuple(value or (), label="allowed_roots")
    normalized: list[str] = []
    for root in roots:
        path = Path(root).expanduser()
        if not path.is_absolute():
            raise ValueError("handoff execution_scope allowed_roots must be absolute")
        normalized.append(str(path.resolve(strict=False)))
    return tuple(dict.fromkeys(normalized))


def _relative_paths(value: Any) -> tuple[str, ...]:
    paths = _string_tuple(value or (), label="exact_paths")
    normalized: list[str] = []
    for raw in paths:
        path = PurePosixPath(raw.replace("\\", "/"))
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("handoff execution_scope exact_paths must be clean relative paths")
        normalized.append(path.as_posix())
    return tuple(dict.fromkeys(normalized))


def _string_tuple(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"handoff execution_scope {label} must be a list")
    values = tuple(str(item or "").strip() for item in value)
    if any(not item for item in values):
        raise ValueError(f"handoff execution_scope {label} contains an empty value")
    return values
