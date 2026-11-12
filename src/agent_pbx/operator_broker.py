from __future__ import annotations

import os
import re
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from .operator_actions import project_handoff_action
from .operator_delegation import (
    execution_scope_digest,
    resolve_handoff_execution_scope,
)
from .security import now_ts
from .store import Store


OPERATOR_BROKER_MODE_ENV = "AGENT_PBX_OPERATOR_BROKER_MODE"
OPERATOR_BROKER_CONTROL_MODE_ENV = "AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE"
OPERATOR_BROKER_REQUEST_TTL_SECONDS_ENV = (
    "AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS"
)
OPERATOR_BROKER_MAX_IN_FLIGHT_ENV = "AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT"

OPERATOR_BROKER_MODES = ("guided", "root_operator", "disabled")
OPERATOR_BROKER_CONTROL_MODES = ("editable", "read_only", "hidden")
DEFAULT_OPERATOR_BROKER_REQUEST_TTL_SECONDS = 900
MIN_OPERATOR_BROKER_REQUEST_TTL_SECONDS = 60
MAX_OPERATOR_BROKER_REQUEST_TTL_SECONDS = 3600
DEFAULT_OPERATOR_BROKER_MAX_IN_FLIGHT = 1
MAX_OPERATOR_BROKER_MAX_IN_FLIGHT = 8
MAX_OPERATOR_BROKER_PATHS = 256
MAX_OPERATOR_BROKER_SUBJECT_LENGTH = 160
OPERATOR_BROKER_REQUEST_EXECUTABLE_STATES = {
    "ready_stop",
    "stopped_ready_broker",
    "receipt_review",
    "committed_ready_resume",
    "resumed_ready_validation",
}

_HEX_OID = re.compile(r"[0-9a-f]{40,64}")


class OperatorBrokerPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class OperatorBrokerPosture:
    mode: str
    control_mode: str
    request_ttl_seconds: int
    max_in_flight_per_root: int

    @property
    def root_operator_enabled(self) -> bool:
        return self.mode == "root_operator"

    @property
    def controls_hidden(self) -> bool:
        return self.control_mode == "hidden"

    def public_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "root_operator_enabled": self.root_operator_enabled,
            "controls_hidden": self.controls_hidden,
        }


class OperatorBrokerService:
    """Validate root-owned checkpoints and persist local TUI execution requests."""

    def __init__(
        self,
        store: Store,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.environ = dict(os.environ if environ is None else environ)

    def posture(self) -> OperatorBrokerPosture:
        return operator_broker_posture(self.environ)

    def preflight(
        self,
        *,
        root_operator_agent_id: str,
        target_fork_agent_id: str,
        handoff_id: str,
        allow_completed_validation: bool = False,
    ) -> dict[str, Any]:
        posture = self.posture()
        if posture.mode == "disabled":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_DISABLED",
                "The Operator checkpoint broker is disabled by local policy.",
            )
        root = self._require_root(root_operator_agent_id)
        fork = self._require_owned_edit_fork(
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=target_fork_agent_id,
        )
        handoff = self.store.get_operator_handoff(handoff_id)
        if handoff is None:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_HANDOFF_NOT_FOUND",
                "The exact broker handoff no longer exists.",
            )
        handoff_root_operator_id = str(
            handoff.get("logical_operator_agent_id") or ""
        ).strip()
        handoff_target_operator_id = str(
            handoff.get("target_operator_agent_id") or ""
        ).strip()
        authority_mode = "root_owned"
        source_root: dict[str, Any] | None = None
        if handoff_root_operator_id != root_operator_agent_id:
            if handoff_target_operator_id != root_operator_agent_id:
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_HANDOFF_ROOT_MISMATCH",
                    "The handoff belongs to another logical root Operator and "
                    "does not target the requesting root.",
                )
            handoff_status = str(handoff.get("status") or "").strip().lower()
            handoff_metadata = self._metadata(handoff)
            approved_by = str(
                handoff_metadata.get("approved_by_operator_agent_id") or ""
            ).strip()
            acknowledged_by = str(
                handoff_metadata.get("acknowledged_by_operator_agent_id") or ""
            ).strip()
            approved_through_tui = (
                str(handoff_metadata.get("approved_by") or "").strip()
                == "agent-pbx-tui"
            )
            delegated_approval = bool(
                handoff_metadata.get("delegated_approval")
                and handoff_metadata.get("delegation_lease_id")
            )
            if (
                handoff_status not in {"acknowledged", "running"}
                or approved_by != handoff_root_operator_id
                or acknowledged_by != root_operator_agent_id
                or not (approved_through_tui or delegated_approval)
            ):
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_DELEGATED_HANDOFF_UNAPPROVED",
                    "A cross-root broker handoff requires source-root approval "
                    "and target-root acknowledgment before execution.",
                )
            source_root = self._require_root(handoff_root_operator_id)
            authority_mode = "delegated_cross_root"
        target_fork_id = str(handoff.get("target_operator_fork_id") or "").strip()
        target_fork = self.store.get_operator_fork(target_fork_id) if target_fork_id else None
        if target_fork is None:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_HANDOFF_FORK_MISSING",
                "The handoff does not bind one durable target edit fork.",
            )
        if str(target_fork.get("fork_agent_id") or "") != target_fork_agent_id:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_HANDOFF_FORK_MISMATCH",
                "The handoff target does not match the requested edit fork.",
            )
        try:
            scope = resolve_handoff_execution_scope(handoff)
        except ValueError as exc:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_SCOPE_INVALID",
                str(exc),
            ) from exc
        if scope.get("action_class") != "exact_git_commit_broker":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_SCOPE_WRONG_CLASS",
                "The handoff is not an exact Git commit broker action.",
            )
        campaign_id = str(scope.get("campaign_id") or "").strip()
        campaign = self.store.get_operator_campaign(campaign_id) if campaign_id else None
        if (
            campaign is None
            or str(campaign.get("operator_agent_id") or "") != root_operator_agent_id
            or str(campaign.get("status") or "").lower() != "running"
        ):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_CAMPAIGN_NOT_RUNNING",
                "The exact broker handoff must belong to a running root campaign.",
            )
        assignment_id = self._resolve_campaign_assignment(
            campaign_id=campaign_id,
            handoff=handoff,
            target_fork=target_fork,
        )
        transaction = self.store.get_operator_action_transaction(handoff_id=handoff_id)
        action = project_handoff_action(
            handoff,
            target_agent=fork,
            transaction=transaction,
        )
        executable_states = set(OPERATOR_BROKER_REQUEST_EXECUTABLE_STATES)
        if allow_completed_validation:
            executable_states.add("complete")
        if action is None or action.get("state") not in executable_states:
            reason = str((action or {}).get("reason") or "")
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ACTION_NOT_EXECUTABLE",
                reason or "The exact handoff is not at an executable broker checkpoint.",
            )
        digest = str(scope.get("workspace_set_digest") or "").strip().lower()
        root_metadata = self._metadata(root)
        fork_metadata = self._metadata(fork)
        activated = {
            str(root_metadata.get("operator_authority_activated_workspace_set_digest") or ""),
            str(fork_metadata.get("operator_authority_activated_workspace_set_digest") or ""),
            digest,
        }
        if len(activated) != 1 or not digest or len(digest) != 64:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_WORKSPACE_MISMATCH",
                "Root, fork, and handoff do not share one activated workspace-set digest.",
            )
        authority_subjects = [(root_metadata, "root"), (fork_metadata, "fork")]
        if source_root is not None:
            source_root_metadata = self._metadata(source_root)
            source_digest = str(
                source_root_metadata.get(
                    "operator_authority_activated_workspace_set_digest"
                )
                or ""
            )
            if source_digest != digest:
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_SOURCE_WORKSPACE_MISMATCH",
                    "The approving source root does not share the activated "
                    "workspace-set digest.",
                )
            authority_subjects.append((source_root_metadata, "source root"))
        for metadata, label in authority_subjects:
            if str(metadata.get("operator_authority_activated_profile") or "") != "trusted-workbench":
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_AUTHORITY_REQUIRED",
                    f"The {label} Operator is not activated under trusted-workbench authority.",
                )
            if metadata.get("operator_authority_requires_restart") is True:
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_AUTHORITY_PENDING_RESTART",
                    f"The {label} Operator has an unapplied authority change.",
                )
        exact_paths = normalize_broker_paths(scope.get("exact_paths") or ())
        expected_parent = normalize_broker_expected_parent(
            scope.get("expected_git_parent")
        )
        expected_parents = normalize_broker_expected_parents(
            scope.get("expected_git_parents"),
            expected_parent=expected_parent,
        )
        expected_tree = normalize_broker_expected_tree(
            scope.get("expected_git_tree"),
            required=len(expected_parents) > 1,
        )
        subject = normalize_broker_commit_subject(scope.get("commit_subject"))
        scope_sha256 = execution_scope_digest(scope)
        authority_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "root_operator_agent_id": root_operator_agent_id,
                    "handoff_root_operator_agent_id": handoff_root_operator_id,
                    "handoff_authority_mode": authority_mode,
                    "target_fork_agent_id": target_fork_agent_id,
                    "handoff_id": handoff_id,
                    "campaign_id": campaign_id,
                    "assignment_id": assignment_id,
                    "workspace_set_digest": digest,
                    "scope_sha256": scope_sha256,
                    "expected_git_parent": expected_parent,
                    "expected_git_parents": expected_parents,
                    "expected_git_tree": expected_tree,
                    "commit_subject": subject,
                    "exact_paths": exact_paths,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "ok": True,
            "checked_at": now_ts(),
            "posture": posture.public_dict(),
            "root_operator_agent_id": root_operator_agent_id,
            "handoff_root_operator_agent_id": handoff_root_operator_id,
            "handoff_authority_mode": authority_mode,
            "target_fork_agent_id": target_fork_agent_id,
            "handoff_id": handoff_id,
            "campaign_id": campaign_id,
            "assignment_id": assignment_id,
            "workspace_set_digest": digest,
            "scope_sha256": scope_sha256,
            "authority_sha256": authority_sha256,
            "expected_git_parent": expected_parent,
            "expected_git_parents": list(expected_parents),
            "expected_git_tree": expected_tree,
            "commit_subject": subject,
            "exact_paths": list(exact_paths),
            "action": action,
            "transaction": transaction,
        }

    def request(
        self,
        *,
        root_operator_agent_id: str,
        target_fork_agent_id: str,
        handoff_id: str,
    ) -> dict[str, Any]:
        posture = self.posture()
        if not posture.root_operator_enabled:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ROOT_MODE_REQUIRED",
                "Root-supervised broker execution is not enabled; use the guided TUI workflow.",
            )
        preflight = self.preflight(
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=target_fork_agent_id,
            handoff_id=handoff_id,
        )
        request, created = self.store.create_operator_broker_request(
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=target_fork_agent_id,
            handoff_id=handoff_id,
            campaign_id=str(preflight["campaign_id"]),
            assignment_id=str(preflight["assignment_id"]),
            workspace_set_digest=str(preflight["workspace_set_digest"]),
            scope_sha256=str(preflight["scope_sha256"]),
            expected_git_parent=str(preflight["expected_git_parent"]),
            expected_git_parents=tuple(preflight["expected_git_parents"]),
            expected_git_tree=preflight["expected_git_tree"],
            commit_subject=str(preflight["commit_subject"]),
            exact_paths=tuple(preflight["exact_paths"]),
            ttl_seconds=posture.request_ttl_seconds,
            max_in_flight_per_root=posture.max_in_flight_per_root,
        )
        if not created and str(request.get("status") or "") not in {
            "pending",
            "claimed",
            "executing",
        }:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_TERMINAL_REPLAY",
                "A terminal broker request cannot be reopened or retried; create a "
                "new exact handoff under a successor campaign when authorized.",
            )
        approval_report = self.store.project_operator_broker_approval(
            str(request["request_id"])
        )
        self.store.append_event(
            "operator_broker_request_created" if created else "operator_broker_request_reused",
            {
                "request_id": request["request_id"],
                "root_operator_agent_id": root_operator_agent_id,
                "target_fork_agent_id": target_fork_agent_id,
                "handoff_id": handoff_id,
                "scope_sha256": preflight["scope_sha256"],
                "created": created,
                "approval_report_id": approval_report["report_id"],
            },
            str(request["request_id"]),
        )
        return {
            "request": with_operator_broker_receipt_summary(request),
            "created": created,
            "preflight": preflight,
            "approval_report": approval_report,
        }

    def validate(
        self,
        *,
        root_operator_agent_id: str,
        request_id: str,
        action_transaction_id: str,
    ) -> dict[str, Any]:
        """Validate one exact terminal broker receipt and close its workflow."""

        self._require_root(root_operator_agent_id)
        request = self.store.get_operator_broker_request(request_id)
        if request is None:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_REQUEST_NOT_FOUND", "The broker request was not found."
            )
        if request["root_operator_agent_id"] != root_operator_agent_id:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_REQUEST_ROOT_MISMATCH",
                "The broker request belongs to another root Operator.",
            )
        if str(request.get("status") or "") != "succeeded":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_RESULT_NOT_SUCCEEDED",
                "Only a succeeded terminal broker result can be validated.",
            )
        if str(request.get("action_transaction_id") or "") != action_transaction_id:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_TRANSACTION_MISMATCH",
                "The supplied action transaction does not match the broker result.",
            )
        if request.get("validated_at") is not None:
            transaction = self.store.get_operator_action_transaction(
                transaction_id=action_transaction_id
            )
            handoff = self.store.get_operator_handoff(str(request["handoff_id"]))
            if (
                transaction is None
                or str(transaction.get("state") or "") != "complete"
                or handoff is None
                or not str(request.get("validation_sha256") or "")
            ):
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_VALIDATION_REPLAY_MISSING",
                    "The validated broker workflow is no longer durably complete.",
                )
            if str(handoff.get("status") or "") not in {"complete", "completed"}:
                handoff = self.store.update_operator_handoff(
                    str(request["handoff_id"]),
                    status="complete",
                    complete=True,
                    summary="Exact broker transaction validated by the root Operator",
                    artifact_bundle=[
                        {
                            "kind": "operator_broker_validation",
                            "request_id": request_id,
                            "transaction_id": action_transaction_id,
                            "validation_sha256": request["validation_sha256"],
                        }
                    ],
                    metadata={
                        "broker_request_id": request_id,
                        "broker_validation_sha256": request["validation_sha256"],
                        "broker_validated_by": root_operator_agent_id,
                    },
                )
                if handoff is None:
                    raise OperatorBrokerPolicyError(
                        "OPERATOR_BROKER_HANDOFF_NOT_FOUND",
                        "The validated broker handoff disappeared before completion.",
                    )
            return {
                "request": with_operator_broker_receipt_summary(request),
                "transaction": transaction,
                "handoff": handoff,
                "validation_sha256": str(request.get("validation_sha256") or ""),
            }
        preflight = self.preflight(
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=str(request["target_fork_agent_id"]),
            handoff_id=str(request["handoff_id"]),
            allow_completed_validation=True,
        )
        for key in (
            "campaign_id",
            "assignment_id",
            "workspace_set_digest",
            "scope_sha256",
            "expected_git_parent",
            "expected_git_tree",
            "commit_subject",
        ):
            if request.get(key) != preflight.get(key):
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_VALIDATION_SCOPE_DRIFT",
                    f"Broker validation identity changed: {key}.",
                )
        if list(request.get("expected_git_parents") or []) != list(
            preflight.get("expected_git_parents") or []
        ) or list(request.get("exact_paths") or []) != list(
            preflight.get("exact_paths") or []
        ):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_VALIDATION_SCOPE_DRIFT",
                "Broker validation parents or exact paths changed.",
            )
        result = request.get("result")
        evidence = dict(result) if isinstance(result, Mapping) else {}
        new_head = str(evidence.get("new_head") or "").strip().lower()
        verified_head = str(evidence.get("verified_head") or "").strip().lower()
        committed_paths = [str(item) for item in evidence.get("committed_paths") or []]
        result_parents = [str(item) for item in evidence.get("commit_parents") or []]
        required_booleans = {
            "journal_done": True,
            "same_fork_resumed": True,
            "index_empty": True,
        }
        if (
            not _HEX_OID.fullmatch(new_head)
            or new_head != verified_head
            or str(evidence.get("old_head") or "") != request["expected_git_parent"]
            or result_parents != list(request["expected_git_parents"])
            or committed_paths != list(request["exact_paths"])
            or str(evidence.get("commit_subject") or "") != request["commit_subject"]
            or any(evidence.get(key) is not value for key, value in required_booleans.items())
        ):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_TERMINAL_RECEIPT_INVALID",
                "The terminal broker receipt does not exactly satisfy the approved request.",
            )
        transaction = self.store.get_operator_action_transaction(
            transaction_id=action_transaction_id
        )
        if transaction is None or str(transaction.get("handoff_id") or "") != request["handoff_id"]:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_TRANSACTION_MISMATCH",
                "The durable action transaction is missing or belongs to another handoff.",
            )
        validation_payload = {
            "request_id": request_id,
            "action_transaction_id": action_transaction_id,
            "campaign_id": request["campaign_id"],
            "assignment_id": request["assignment_id"],
            "scope_sha256": request["scope_sha256"],
            "old_head": evidence["old_head"],
            "new_head": new_head,
            "commit_parents": result_parents,
            "commit_subject": request["commit_subject"],
            "committed_paths": committed_paths,
            "journal_done": True,
            "same_fork_resumed": True,
            "index_empty": True,
        }
        validation_sha256 = hashlib.sha256(
            json.dumps(
                validation_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        transaction_state = str(transaction.get("state") or "")
        if transaction_state == "resumed_ready_validation":
            transaction = self.store.append_operator_action_receipt(
                handoff_id=str(request["handoff_id"]),
                action_class="exact_git_commit_broker",
                target_agent_id=str(request["target_fork_agent_id"]),
                scope_sha256=str(request["scope_sha256"]),
                initial_state="resumed_ready_validation",
                step="validate",
                outcome="succeeded",
                actor=root_operator_agent_id,
                evidence={
                    "request_id": request_id,
                    "validation_sha256": validation_sha256,
                    "verified_head": new_head,
                },
            )
        elif transaction_state != "complete":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_TRANSACTION_NOT_VALIDATABLE",
                f"Broker action transaction is not validation-ready: {transaction_state}.",
            )
        else:
            receipts = transaction.get("receipts") or []
            last = receipts[-1] if receipts else {}
            last_evidence = last.get("evidence") if isinstance(last, Mapping) else {}
            if (
                last.get("step") != "validate"
                or last.get("outcome") != "succeeded"
                or last.get("actor") != root_operator_agent_id
                or not isinstance(last_evidence, Mapping)
                or last_evidence.get("request_id") != request_id
                or last_evidence.get("validation_sha256") != validation_sha256
            ):
                raise OperatorBrokerPolicyError(
                    "OPERATOR_BROKER_VALIDATION_REPLAY_MISMATCH",
                    "The completed transaction lacks this exact root validation receipt.",
                )
        validated = self.store.validate_operator_broker_request(
            request_id=request_id,
            root_operator_agent_id=root_operator_agent_id,
            action_transaction_id=action_transaction_id,
            validation_sha256=validation_sha256,
        )
        updated_handoff = self.store.update_operator_handoff(
            str(request["handoff_id"]),
            status="complete",
            complete=True,
            summary="Exact broker transaction validated by the root Operator",
            artifact_bundle=[
                {
                    "kind": "operator_broker_validation",
                    "request_id": request_id,
                    "transaction_id": action_transaction_id,
                    "validation_sha256": validation_sha256,
                }
            ],
            metadata={
                "broker_request_id": request_id,
                "broker_validation_sha256": validation_sha256,
                "broker_validated_by": root_operator_agent_id,
            },
        )
        if updated_handoff is None:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_HANDOFF_NOT_FOUND",
                "The validated broker handoff disappeared before completion.",
            )
        return {
            "request": with_operator_broker_receipt_summary(validated),
            "transaction": transaction,
            "handoff": updated_handoff,
            "validation_sha256": validation_sha256,
        }

    def get(
        self,
        *,
        root_operator_agent_id: str,
        request_id: str,
    ) -> dict[str, Any]:
        self._require_root(root_operator_agent_id)
        request = self.store.get_operator_broker_request(request_id)
        if request is None:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_REQUEST_NOT_FOUND",
                "The broker request was not found.",
            )
        if request["root_operator_agent_id"] != root_operator_agent_id:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_REQUEST_ROOT_MISMATCH",
                "The broker request belongs to another root Operator.",
            )
        return with_operator_broker_receipt_summary(request)

    def cancel(
        self,
        *,
        root_operator_agent_id: str,
        request_id: str,
        reason: str,
    ) -> dict[str, Any]:
        self.get(
            root_operator_agent_id=root_operator_agent_id,
            request_id=request_id,
        )
        canceled = self.store.cancel_operator_broker_request(
            request_id=request_id,
            root_operator_agent_id=root_operator_agent_id,
            reason=reason,
        )
        self.store.append_event(
            "operator_broker_request_canceled",
            {
                "request_id": request_id,
                "root_operator_agent_id": root_operator_agent_id,
                "reason": str(reason or "")[:1000],
            },
            request_id,
        )
        return with_operator_broker_receipt_summary(canceled)

    def _require_root(self, agent_id: str) -> dict[str, Any]:
        agent = self.store.get_agent(agent_id)
        if agent is None or agent.get("agent_type") != "operator" or not agent.get("pbx_active"):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ROOT_INACTIVE",
                "The requesting root Operator is not registered and active.",
            )
        metadata = self._metadata(agent)
        if str(metadata.get("operator_role") or "root") != "root":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ROOT_REQUIRED",
                "Only a root Operator may request a broker checkpoint.",
            )
        logical = str(metadata.get("logical_operator_id") or agent_id)
        if logical != agent_id:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ROOT_IDENTITY_MISMATCH",
                "The requesting Operator is not its logical root identity.",
            )
        return agent

    def _require_owned_edit_fork(
        self,
        *,
        root_operator_agent_id: str,
        target_fork_agent_id: str,
    ) -> dict[str, Any]:
        fork = self.store.get_agent(target_fork_agent_id)
        if fork is None or fork.get("agent_type") != "operator":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_FORK_NOT_FOUND",
                "The target edit fork is not registered.",
            )
        metadata = self._metadata(fork)
        if (
            str(metadata.get("operator_role") or "") != "fork"
            or str(metadata.get("logical_operator_id") or "") != root_operator_agent_id
            or str(metadata.get("fork_purpose") or "edit") != "edit"
            or str(metadata.get("access_mode") or "edit") != "edit"
        ):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_FORK_BOUNDARY",
                "The target must be an edit fork owned by the requesting root Operator.",
            )
        return fork

    def _resolve_campaign_assignment(
        self,
        *,
        campaign_id: str,
        handoff: Mapping[str, Any],
        target_fork: Mapping[str, Any],
    ) -> str:
        metadata = self._metadata(handoff)
        requested = str(metadata.get("assignment_id") or "").strip()
        assignments = self.store.list_operator_campaign_assignments(
            campaign_id=campaign_id,
            limit=500,
        )
        source_caller = str(target_fork.get("source_caller_agent_id") or "").strip()
        matches = [
            assignment
            for assignment in assignments
            if str(assignment.get("target_agent_id") or "") == source_caller
            and (not requested or assignment.get("assignment_id") == requested)
        ]
        if len(matches) != 1:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ASSIGNMENT_AMBIGUOUS",
                "The exact broker handoff must resolve to one campaign assignment.",
            )
        assignment = matches[0]
        if assignment.get("completed_at") is not None:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_ASSIGNMENT_TERMINAL",
                "A terminal campaign assignment cannot create a new broker request.",
            )
        return str(assignment["assignment_id"])

    @staticmethod
    def _metadata(agent: Mapping[str, Any]) -> dict[str, Any]:
        metadata = agent.get("metadata")
        return dict(metadata) if isinstance(metadata, Mapping) else {}


def operator_broker_receipt_summary(request: Mapping[str, Any]) -> str:
    """Render a concise root-readable result without exposing hidden runtime data."""

    status = str(request.get("status") or "unknown")
    target = str(request.get("target_fork_agent_id") or "-")
    subject = str(request.get("commit_subject") or "-")
    result = request.get("result")
    evidence = dict(result) if isinstance(result, Mapping) else {}
    lines = [
        f"Broker checkpoint `{status}` for `{target}`.",
        f"Commit subject: `{subject}`",
    ]
    old_head = str(evidence.get("old_head") or "").strip()
    new_head = str(
        evidence.get("new_head") or evidence.get("verified_head") or ""
    ).strip()
    parents = evidence.get("commit_parents") or request.get("expected_git_parents")
    if isinstance(parents, (list, tuple)) and parents:
        lines.append(
            "Git parents: " + " → ".join(f"`{str(parent)}`" for parent in parents)
        )
    elif old_head:
        lines.append(f"Git parent: `{old_head}`")
    if new_head:
        lines.append(f"Verified Git HEAD: `{new_head}`")
    paths = evidence.get("committed_paths") or request.get("exact_paths")
    if isinstance(paths, (list, tuple)) and paths:
        lines.append("Paths: " + ", ".join(f"`{str(path)}`" for path in paths))
    session_id = str(evidence.get("resumed_codex_session_id") or "").strip()
    pane_id = str(evidence.get("resumed_tmux_pane_id") or "").strip()
    if session_id:
        lines.append(f"Resumed Codex session: `{session_id}`")
    if pane_id:
        lines.append(f"Managed tmux pane: `{pane_id}`")
    if evidence.get("validation_pending") is True:
        lines.append("Root validation remains pending.")
    error = str(request.get("error") or "").strip()
    if error:
        lines.append(f"Failure: {error}")
    return "\n".join(lines)


def with_operator_broker_receipt_summary(
    request: Mapping[str, Any],
) -> dict[str, Any]:
    payload = dict(request)
    payload["receipt_summary"] = operator_broker_receipt_summary(request)
    return payload


def operator_broker_posture(
    environ: Mapping[str, str] | None = None,
) -> OperatorBrokerPosture:
    selected = os.environ if environ is None else environ
    mode = str(selected.get(OPERATOR_BROKER_MODE_ENV) or "guided").strip().lower()
    if mode not in OPERATOR_BROKER_MODES:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_MODE_INVALID",
            f"Unsupported Operator broker mode: {mode or '<empty>'}",
        )
    control_mode = str(
        selected.get(OPERATOR_BROKER_CONTROL_MODE_ENV) or "editable"
    ).strip().lower()
    if control_mode not in OPERATOR_BROKER_CONTROL_MODES:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_CONTROL_MODE_INVALID",
            f"Unsupported Operator broker control mode: {control_mode or '<empty>'}",
        )
    ttl = _bounded_integer(
        selected.get(OPERATOR_BROKER_REQUEST_TTL_SECONDS_ENV),
        default=DEFAULT_OPERATOR_BROKER_REQUEST_TTL_SECONDS,
        minimum=MIN_OPERATOR_BROKER_REQUEST_TTL_SECONDS,
        maximum=MAX_OPERATOR_BROKER_REQUEST_TTL_SECONDS,
        label="Operator broker request TTL",
    )
    maximum = _bounded_integer(
        selected.get(OPERATOR_BROKER_MAX_IN_FLIGHT_ENV),
        default=DEFAULT_OPERATOR_BROKER_MAX_IN_FLIGHT,
        minimum=1,
        maximum=MAX_OPERATOR_BROKER_MAX_IN_FLIGHT,
        label="Operator broker in-flight limit",
    )
    return OperatorBrokerPosture(
        mode=mode,
        control_mode=control_mode,
        request_ttl_seconds=ttl,
        max_in_flight_per_root=maximum,
    )


def normalize_broker_commit_subject(value: object) -> str:
    subject = str(value or "").strip()
    if not subject:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_SUBJECT_REQUIRED",
            "An exact one-line commit subject is required.",
        )
    if len(subject) > MAX_OPERATOR_BROKER_SUBJECT_LENGTH:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_SUBJECT_TOO_LONG",
            "The commit subject exceeds the 160-character broker limit.",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in subject):
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_SUBJECT_INVALID",
            "The commit subject cannot contain control characters or newlines.",
        )
    return subject


def normalize_broker_expected_parent(value: object) -> str:
    parent = str(value or "").strip().lower()
    if not _HEX_OID.fullmatch(parent):
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PARENT_INVALID",
            "The expected Git parent must be one complete hexadecimal object ID.",
        )
    return parent


def normalize_broker_expected_parents(
    values: object,
    *,
    expected_parent: str,
) -> tuple[str, ...]:
    """Return one immutable ordered parent set with HEAD first."""

    if values is None:
        return (expected_parent,)
    if not isinstance(values, (list, tuple)) or not values:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PARENTS_INVALID",
            "Expected Git parents must be a non-empty ordered list.",
        )
    parents = tuple(normalize_broker_expected_parent(value) for value in values)
    if len(parents) != len(set(parents)):
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PARENTS_DUPLICATE",
            "Expected Git parents cannot contain duplicate object IDs.",
        )
    if parents[0] != expected_parent:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PARENT_ORDER_INVALID",
            "The first ordered Git parent must match the expected Git parent.",
        )
    return parents


def normalize_broker_expected_tree(
    value: object,
    *,
    required: bool,
) -> str | None:
    tree = str(value or "").strip().lower()
    if not tree:
        if required:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_TREE_REQUIRED",
                "A multi-parent checkpoint requires one exact expected Git tree.",
            )
        return None
    if not _HEX_OID.fullmatch(tree):
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_TREE_INVALID",
            "The expected Git tree must be one complete hexadecimal object ID.",
        )
    return tree


def normalize_broker_paths(values: Iterable[object]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "")
        if not value or value != value.strip():
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_INVALID",
                "Broker paths must be non-empty repository-relative paths without surrounding whitespace.",
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_INVALID",
                "Broker paths cannot contain control characters.",
            )
        path = PurePosixPath(value)
        if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_INVALID",
                f"Broker path escapes the repository root: {value}",
            )
        canonical = path.as_posix()
        if canonical.startswith(".git/") or canonical == ".git":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_PROTECTED",
                "Git administration paths cannot be committed through the broker.",
            )
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
        if len(normalized) > MAX_OPERATOR_BROKER_PATHS:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_LIMIT",
                f"A broker request may include at most {MAX_OPERATOR_BROKER_PATHS} paths.",
            )
    if not normalized:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PATHS_REQUIRED",
            "At least one exact repository-relative path is required.",
        )
    return tuple(normalized)


def _bounded_integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_INTEGER_INVALID",
            f"{label} must be an integer.",
        ) from exc
    if parsed < minimum or parsed > maximum:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_INTEGER_OUT_OF_RANGE",
            f"{label} must be between {minimum} and {maximum}.",
        )
    return parsed
