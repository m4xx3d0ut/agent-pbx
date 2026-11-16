from __future__ import annotations

import sqlite3
from typing import Final


ATOMIC_STORE_OPERATIONS: Final[frozenset[str]] = frozenset(
    {
        "acquire_tmux_writer_lease",
        "append_event_if_changed",
        "append_operator_action_receipt",
        "cancel_operator_broker_request",
        "cancel_operator_handoff",
        "claim_operator_broker_request",
        "claim_workerbee_controlled_run_request",
        "consume_dynamic_resource_request",
        "consume_operator_delegation_lease",
        "create_operator_broker_request",
        "create_operator_campaign_successor",
        "create_workerbee_controlled_run_request",
        "decide_workerbee_controlled_run_request",
        "finalize_managed_runtime_stop",
        "mark_model_elevation_revert_pending",
        "merge_agent_metadata",
        "merge_operator_fork_metadata",
        "prepare_managed_runtime_stop",
        "project_operator_broker_approval",
        "reconcile_operator_terminal_report",
        "request_or_reuse_dynamic_resource",
        "request_or_reuse_model_elevation",
        "transition_operator_broker_request",
        "transition_workerbee_controlled_run_request",
        "update_model_elevation_transition",
        "validate_operator_broker_request",
    }
)

# Store retains the stable operation name while a repository may use a shorter
# domain-local method name. Keeping this mapping explicit makes delegation part
# of the reviewed atomicity contract rather than an AST naming heuristic.
ATOMIC_STORE_DELEGATES: Final[dict[str, str]] = {
    "append_event_if_changed": "append_if_changed",
}


def begin_immediate(connection: sqlite3.Connection) -> None:
    """Begin the reviewed SQLite writer transaction used by atomic operations."""

    connection.execute("BEGIN IMMEDIATE")
