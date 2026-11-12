from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Callable

from .git_worktree import (
    LinkedWorktreeQualificationError,
    _git,
    qualify_linked_worktree_git_admin,
)
from .operator_delegation import resolve_handoff_execution_scope
from .store import Store


DIAGNOSTIC_SCHEMA = "agent-pbx.protected-provider-diagnostics/v1"
ENVELOPE_SCHEMA = "agent-pbx.protected-provider-merge-envelope/v1"
TOPOLOGY_SCHEMA = "agent-pbx.linked-worktree-topology/v1"
ACTION_CLASS = "protected_provider_merge_reconciliation"


class ProtectedProviderDiagnosticError(ValueError):
    """A protected-provider preflight failed without provider-side action."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def protected_path_names_sha256(paths: list[str]) -> str:
    return hashlib.sha256(
        json.dumps(paths, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class ProtectedProviderDiagnosticService:
    """Reconcile approved metadata before any protected-provider operation.

    The service has no provider adapter and exposes no mutation method. It reads
    only linked-worktree Git administration metadata and caller-supplied opaque
    generation/receipt identities already bound by a manually approved handoff.
    """

    def __init__(
        self,
        store: Store,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.clock = clock

    def diagnose(
        self,
        *,
        operator_agent_id: str,
        handoff_id: str,
        approval_id: str,
        envelope_sha256: str,
        workspace_generation: str,
        provider_generation: str,
        provider_receipt_id: str,
        provider_receipt_sha256: str,
    ) -> dict[str, Any]:
        now = float(self.clock())
        handoff = self.store.get_operator_handoff(handoff_id)
        if handoff is None:
            self._fail("handoff_not_found", "protected-provider handoff was not found")
        assert handoff is not None
        if str(handoff.get("status") or "") not in {
            "approved",
            "sent",
            "acknowledged",
            "running",
        }:
            self._fail(
                "handoff_not_approved",
                "protected-provider handoff is not manually approved and active",
            )
        logical_operator_id = str(handoff.get("logical_operator_agent_id") or "")
        if operator_agent_id != logical_operator_id:
            self._fail(
                "operator_mismatch",
                "protected-provider diagnostics require the exact source root Operator",
            )
        operator = self.store.get_agent(operator_agent_id)
        metadata = (operator or {}).get("metadata") or {}
        if (
            operator is None
            or operator.get("agent_type") != "operator"
            or metadata.get("operator_role") != "root"
            or not operator.get("pbx_active")
        ):
            self._fail(
                "root_operator_required",
                "protected-provider diagnostics require an active root Operator",
            )

        try:
            scope = resolve_handoff_execution_scope(handoff)
        except ValueError as exc:
            self._fail("execution_scope_invalid", str(exc))
        if scope.get("action_class") != ACTION_CLASS:
            self._fail(
                "action_class_mismatch",
                "handoff is not approved for protected-provider reconciliation",
            )
        if scope.get("mutation_mode") not in {"none", "read_only"}:
            self._fail("mutation_not_read_only", "diagnostic authority is not read-only")
        if any(
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
        ):
            self._fail(
                "authority_broadened",
                "diagnostic handoff contains provider, credential, or mutation authority",
            )
        envelope = scope.get("protected_provider_merge_envelope")
        if not isinstance(envelope, Mapping) or envelope.get("schema") != ENVELOPE_SCHEMA:
            self._fail("envelope_invalid", "approved merge envelope is unavailable")

        campaign_id = str(scope.get("campaign_id") or "")
        campaign = self.store.get_operator_campaign(campaign_id)
        if (
            campaign is None
            or campaign.get("status") != "running"
            or campaign.get("operator_agent_id") != logical_operator_id
        ):
            self._fail(
                "campaign_not_running",
                "protected-provider campaign identity is not active",
            )
        workspace_digest = str(scope.get("workspace_set_digest") or "")
        if metadata.get("operator_authority_activated_workspace_set_digest") != workspace_digest:
            self._fail(
                "workspace_identity_mismatch",
                "activated workspace-set identity does not match the approval",
            )

        handoff_metadata = handoff.get("metadata") or {}
        manual = handoff_metadata.get("manual_approval")
        required_approval_keys = {
            "approved",
            "method",
            "approval_id",
            "approved_at",
            "campaign_id",
            "handoff_id",
            "workspace_set_digest",
            "action_class",
            "envelope_sha256",
        }
        if (
            not isinstance(manual, Mapping)
            or set(manual) != required_approval_keys
            or manual.get("approved") is not True
            or manual.get("method") != "f8"
            or manual.get("approval_id") != approval_id
            or manual.get("campaign_id") != campaign_id
            or manual.get("handoff_id") != handoff_id
            or manual.get("workspace_set_digest") != workspace_digest
            or manual.get("action_class") != ACTION_CLASS
            or manual.get("envelope_sha256") != envelope.get("envelope_sha256")
            or handoff_metadata.get("requires_f8_approval") is not True
            or handoff_metadata.get("approved_by") != "agent-pbx-tui"
            or handoff_metadata.get("approved_by_operator_agent_id")
            != logical_operator_id
            or isinstance(manual.get("approved_at"), bool)
            or not isinstance(manual.get("approved_at"), (int, float))
            or not math.isfinite(float(manual.get("approved_at")))
            or float(manual.get("approved_at")) <= 0
            or float(manual.get("approved_at")) > now
            or float(manual.get("approved_at")) < float(envelope["issued_at"])
            or float(manual.get("approved_at")) >= float(envelope["expires_at"])
        ):
            self._fail(
                "manual_approval_invalid",
                "exact receipt-bound F8 approval is missing or mismatched",
            )
        if envelope_sha256 != envelope.get("envelope_sha256"):
            self._fail("envelope_mismatch", "reviewed merge envelope digest drifted")
        if now >= float(envelope["expires_at"]):
            self._fail("envelope_expired", "reviewed merge envelope expired")
        if envelope.get("reviewed_by_operator_agent_id") != logical_operator_id:
            self._fail("reviewer_mismatch", "merge envelope reviewer identity drifted")

        observed = {
            "workspace_generation": workspace_generation,
            "provider_generation": provider_generation,
            "provider_receipt_id": provider_receipt_id,
            "provider_receipt_sha256": provider_receipt_sha256,
        }
        for key, value in observed.items():
            if value != envelope.get(key):
                self._fail(f"{key}_mismatch", f"protected-provider {key} drifted")

        repository_root = Path(str(envelope["repository_root"]))
        allowed_roots = tuple(Path(str(path)) for path in scope.get("allowed_roots") or ())
        try:
            plan = qualify_linked_worktree_git_admin(
                repository_root,
                approved_roots=allowed_roots,
                fork_purpose="edit",
                access_mode="edit",
                codex_home=None,
                expected_uid=int(envelope["owner_uid"]),
            )
        except (LinkedWorktreeQualificationError, OSError, ValueError) as exc:
            self._fail("topology_unqualified", f"linked-worktree topology rejected: {exc}")
        if plan is None:
            self._fail("topology_unqualified", "repository is not a linked worktree")
        assert plan is not None

        topology = {
            "schema": TOPOLOGY_SCHEMA,
            "worktree_root": str(plan.worktree_root),
            "git_dir": str(plan.git_dir),
            "common_dir": str(plan.common_dir),
            "object_dir": str(plan.object_dir),
            "branch_ref": plan.branch_ref,
            "ref_namespace": str(plan.ref_namespace),
            "reflog_namespace": str(plan.reflog_namespace),
        }
        try:
            current = {
                "repository_root": str(plan.worktree_root),
                "git_dir": str(plan.git_dir),
                "common_dir": str(plan.common_dir),
                "object_dir": str(plan.object_dir),
                "branch_ref": plan.branch_ref,
                "head_oid": _git(plan.worktree_root, "rev-parse", "HEAD"),
                "upstream_ref": _git(
                    plan.worktree_root,
                    "rev-parse",
                    "--abbrev-ref",
                    "@{upstream}",
                ),
                "upstream_oid": _git(
                    plan.worktree_root, "rev-parse", "@{upstream}"
                ),
                "topology_sha256": canonical_sha256(topology),
                "owner_uid": plan.worktree_root.stat(follow_symlinks=False).st_uid,
            }
        except (LinkedWorktreeQualificationError, OSError) as exc:
            self._fail(
                "git_metadata_unavailable",
                f"linked-worktree metadata could not be read: {exc}",
            )
        for key, value in current.items():
            if value != envelope.get(key):
                self._fail(f"{key}_mismatch", f"protected-provider {key} drifted")

        try:
            candidate_oid = _git(
                plan.worktree_root,
                "rev-parse",
                f"{envelope['candidate_oid']}^{{commit}}",
            )
            expected_tree_type = _git(
                plan.worktree_root,
                "cat-file",
                "-t",
                str(envelope["expected_tree_oid"]),
            )
            merge_base_oid = _git(
                plan.worktree_root,
                "merge-base",
                str(envelope["head_oid"]),
                str(envelope["candidate_oid"]),
            )
        except LinkedWorktreeQualificationError as exc:
            self._fail(
                "merge_identity_unavailable",
                f"reviewed merge identities could not be resolved: {exc}",
            )
        if candidate_oid != envelope["candidate_oid"]:
            self._fail("candidate_oid_mismatch", "merge candidate identity drifted")
        if expected_tree_type != "tree":
            self._fail("expected_tree_oid_mismatch", "expected tree identity drifted")
        if merge_base_oid != envelope["merge_base_oid"]:
            self._fail("merge_base_oid_mismatch", "merge-base identity drifted")

        protected_paths = list(envelope["protected_path_names"])
        return {
            "schema": DIAGNOSTIC_SCHEMA,
            "status": "ready",
            "ready": True,
            "campaign_id": campaign_id,
            "handoff_id": handoff_id,
            "workspace_set_digest": workspace_digest,
            "envelope_id": envelope["envelope_id"],
            "envelope_sha256": envelope["envelope_sha256"],
            "repository_root": str(plan.worktree_root),
            "branch_ref": plan.branch_ref,
            "head_oid": current["head_oid"],
            "upstream_ref": current["upstream_ref"],
            "upstream_oid": current["upstream_oid"],
            "topology_sha256": current["topology_sha256"],
            "owner_uid": current["owner_uid"],
            "workspace_generation_match": True,
            "provider_generation_match": True,
            "provider_receipt_match": True,
            "merge_envelope_match": True,
            "protected_path_count": len(protected_paths),
            "protected_path_names_sha256": protected_path_names_sha256(
                protected_paths
            ),
            "checked_at": now,
            "authority": "metadata_only_read_only",
        }

    @staticmethod
    def _fail(code: str, message: str) -> None:
        raise ProtectedProviderDiagnosticError(code, message)
