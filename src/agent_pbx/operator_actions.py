from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .operator_delegation import execution_scope_digest, resolve_handoff_execution_scope


ACTION_STATES = frozenset(
    {
        "ready_stop",
        "stopped_ready_broker",
        "receipt_review",
        "committed_ready_resume",
        "resumed_ready_validation",
        "complete",
        "blocked_reapproval_required",
    }
)
ACTION_STEPS = frozenset({"stop", "broker_review", "broker_commit", "resume", "validate"})
ACTION_OUTCOMES = frozenset({"started", "succeeded", "failed", "restored"})
TERMINAL_ACTION_STATES = frozenset({"complete", "blocked_reapproval_required"})


def project_handoff_action(
    handoff: Mapping[str, Any],
    *,
    target_agent: Mapping[str, Any] | None,
    transaction: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Derive the next guarded local action for one acknowledged handoff."""

    handoff_status = str(handoff.get("status") or "").lower()
    if handoff_status not in {
        "sent",
        "acknowledged",
        "running",
        "complete",
        "completed",
    }:
        return None
    # A terminal handoff with no action transaction has no local work left to
    # execute. Filter it before resolving legacy execution scopes so canceled,
    # failed, expired, or completed historical records cannot reappear as
    # blocked actions merely because their old scope format is unsupported.
    if handoff_status in {"complete", "completed"} and not transaction:
        return None

    try:
        scope = resolve_handoff_execution_scope(handoff)
    except ValueError as exc:
        return _blocked_projection(
            handoff,
            str(exc),
            transaction=transaction,
            target_agent=target_agent,
        )
    if not scope or scope.get("action_class") != "exact_git_commit_broker":
        return None

    digest = execution_scope_digest(scope)
    transaction_digest = str((transaction or {}).get("scope_sha256") or "")
    if transaction_digest and transaction_digest != digest:
        return _blocked_projection(
            handoff,
            "immutable execution scope changed after action execution began",
            transaction=transaction,
            scope=scope,
            digest=digest,
            target_agent=target_agent,
        )
    if target_agent is None:
        return _blocked_projection(
            handoff,
            "target edit fork is unavailable",
            transaction=transaction,
            scope=scope,
            digest=digest,
            target_agent=target_agent,
        )

    state = str((transaction or {}).get("state") or "")
    if state in ACTION_STATES:
        projected_state = state
    else:
        stopped = (
            str(target_agent.get("status") or "").casefold() == "canceled"
            and not bool(target_agent.get("pbx_active", True))
        )
        projected_state = "stopped_ready_broker" if stopped else "ready_stop"
    return {
        "handoff_id": str(handoff.get("handoff_id") or ""),
        "logical_operator_agent_id": str(
            handoff.get("logical_operator_agent_id") or ""
        ),
        "target_operator_agent_id": str(
            handoff.get("target_operator_agent_id") or ""
        ),
        "target_agent_id": str(target_agent.get("agent_id") or ""),
        "target_operator_fork_id": str(
            handoff.get("target_operator_fork_id") or ""
        ),
        "campaign_id": str(scope.get("campaign_id") or ""),
        "action_class": str(scope.get("action_class") or ""),
        "state": projected_state,
        "next_action": next_action_for_state(projected_state),
        "scope_sha256": digest,
        "execution_scope": scope,
        "transaction_id": str((transaction or {}).get("transaction_id") or "") or None,
        "reason": str((transaction or {}).get("reason") or "") or None,
        "updated_at": (transaction or {}).get("updated_at") or handoff.get("updated_at"),
    }


def next_action_for_state(state: str) -> str | None:
    return {
        "ready_stop": "stop",
        "stopped_ready_broker": "broker_review",
        "receipt_review": "broker_commit",
        "committed_ready_resume": "resume",
        "resumed_ready_validation": "validate",
    }.get(state)


def transition_action_state(state: str, step: str, outcome: str) -> str:
    if step not in ACTION_STEPS or outcome not in ACTION_OUTCOMES:
        raise ValueError("operator action receipt step or outcome is invalid")
    if outcome == "failed":
        return state
    if outcome == "restored":
        return "stopped_ready_broker"
    expected = {
        ("ready_stop", "stop", "succeeded"): "stopped_ready_broker",
        ("stopped_ready_broker", "stop", "succeeded"): "stopped_ready_broker",
        ("stopped_ready_broker", "broker_review", "started"): "receipt_review",
        ("stopped_ready_broker", "broker_review", "succeeded"): "receipt_review",
        ("receipt_review", "broker_review", "succeeded"): "receipt_review",
        ("receipt_review", "broker_commit", "started"): "receipt_review",
        ("receipt_review", "broker_commit", "succeeded"): "committed_ready_resume",
        ("stopped_ready_broker", "broker_commit", "succeeded"): "committed_ready_resume",
        ("committed_ready_resume", "resume", "succeeded"): "resumed_ready_validation",
        ("resumed_ready_validation", "validate", "succeeded"): "complete",
    }
    next_state = expected.get((state, step, outcome))
    if next_state is None:
        raise ValueError(
            f"operator action receipt {step}/{outcome} is invalid from {state}"
        )
    return next_state


def _blocked_projection(
    handoff: Mapping[str, Any],
    reason: str,
    *,
    transaction: Mapping[str, Any] | None,
    scope: Mapping[str, Any] | None = None,
    digest: str = "",
    target_agent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "handoff_id": str(handoff.get("handoff_id") or ""),
        "logical_operator_agent_id": str(
            handoff.get("logical_operator_agent_id") or ""
        ),
        "target_operator_agent_id": str(
            handoff.get("target_operator_agent_id") or ""
        ),
        "target_agent_id": str((target_agent or {}).get("agent_id") or ""),
        "target_operator_fork_id": str(
            handoff.get("target_operator_fork_id") or ""
        ),
        "campaign_id": str((scope or {}).get("campaign_id") or ""),
        "action_class": str((scope or {}).get("action_class") or ""),
        "state": "blocked_reapproval_required",
        "next_action": None,
        "scope_sha256": digest,
        "execution_scope": dict(scope or {}),
        "transaction_id": str((transaction or {}).get("transaction_id") or "") or None,
        "reason": reason,
        "updated_at": (transaction or {}).get("updated_at") or handoff.get("updated_at"),
    }
