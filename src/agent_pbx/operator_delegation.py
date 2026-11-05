from __future__ import annotations

from pathlib import Path, PurePosixPath
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
    if action_class in ALWAYS_MANUAL_ACTION_CLASSES:
        flags[
            {
                "credential_access": "credential_access",
                "provider_action": "provider_action",
                "deployment": "deployment",
                "publication": "publication",
                "workspace_expansion": "workspace_expansion",
                "authority_elevation": "authority_elevation",
                "destructive_mutation": "destructive",
            }[action_class]
        ] = True

    return {
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
