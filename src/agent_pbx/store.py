from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import (
    AgentRegisterRequest,
    CommandCreateRequest,
    ReportCreateRequest,
    plan_options_to_jsonable,
)
from .project_spawn import PROJECT_SPAWN_TERMINAL_STATUSES
from .security import hash_secret, now_ts


SCHEMA_VERSION = 19
TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4
POLL_BASE_TOKEN_ESTIMATE = 80
DELIVERED_COMMAND_TOKEN_ESTIMATE = 120
USAGE_WARN_TOKENS_PER_HOUR = 10_000
POLL_WARN_PER_HOUR = 24
STALE_WORKING_SECONDS = 600
STALE_WORKING_STATUSES = {"running", "working"}
OPERATOR_AGENT_TYPE = "operator"
OPERATOR_PROJECT = "agent-pbx-operator"
OPERATOR_ROLE_ROOT = "root"
OPERATOR_ROLE_FORK = "fork"
OPERATOR_TERMINAL_BASE_STATES = (
    "complete",
    "completed",
    "blocked",
    "failed",
    "canceled",
    "cancelled",
    "superseded",
)
OPERATOR_KNOWLEDGE_LINK_STATUSES = {
    "proposed",
    "active",
    "closed",
    "canceled",
    "cancelled",
}
OPERATOR_KNOWLEDGE_LINK_TERMINAL_STATUSES = {"closed", "canceled", "cancelled"}
OPERATOR_KNOWLEDGE_TURN_DELIVERY_STATUSES = {
    "pending_approval",
    "recorded",
    "queued",
    "sent",
    "failed",
}
OPERATOR_HANDOFF_STATUSES = {
    "proposed",
    "approved",
    "pending_launch",
    "sent",
    "acknowledged",
    "running",
    "complete",
    "completed",
    "blocked",
    "failed",
    "expired",
    "canceled",
    "cancelled",
}
OPERATOR_HANDOFF_TERMINAL_STATUSES = {
    "complete",
    "completed",
    "blocked",
    "failed",
    "expired",
    "canceled",
    "cancelled",
}
AGENT_PRUNE_PRESETS = {"terminal-callers", "stale-callers", "operator-forks"}
AGENT_PRUNE_TERMINAL_STATUSES = {
    "done",
    "complete",
    "completed",
    "failed",
    "blocked",
    "ready",
    "canceled",
    "cancelled",
    "planning-complete",
    "planning-completed",
}
AGENT_PRUNE_NONTERMINAL_STALE_STATUSES = {
    "running",
    "working",
    "stale-running",
    "stale-working",
    "planned",
    "planning",
    "in-progress",
    "in_progress",
}
OPERATOR_KB_SCOPES = {"global", "project", "repo", "operator", "caller"}
OPERATOR_KB_STATUSES = {"proposed", "active", "retired", "rejected"}
OPERATOR_KB_TERMINAL_STATUSES = {"retired", "rejected"}
OPERATOR_KB_REDACTION_STATUSES = {
    "unreviewed",
    "clean",
    "needs_review",
    "blocked",
}
OPERATOR_KB_SEED_RUN_STATUSES = {
    "requested",
    "queued",
    "sent",
    "failed",
    "complete",
    "completed",
    "canceled",
    "cancelled",
}
OPERATOR_KB_SEED_TERMINAL_STATUSES = {
    "failed",
    "complete",
    "completed",
    "canceled",
    "cancelled",
}
OPERATOR_KB_EXPORT_FORMAT = "agent-pbx-operator-kb-v1"
REPORTING_AGENT_ID_METADATA_KEYS = (
    "reporting_agent_id",
    "pbx_reporting_agent_id",
    "session_agent_id",
)


def _operator_active_state_sql(status_column: str, completed_column: str) -> str:
    quoted_states = ", ".join(f"'{state}'" for state in OPERATOR_TERMINAL_BASE_STATES)
    return f"""
        {completed_column} IS NULL
        AND lower({status_column}) NOT IN ({quoted_states})
        AND lower({status_column}) NOT GLOB 'complete[_-]*'
        AND lower({status_column}) NOT GLOB 'completed[_-]*'
        AND lower({status_column}) NOT GLOB 'blocked[_-]*'
        AND lower({status_column}) NOT GLOB 'failed[_-]*'
        AND lower({status_column}) NOT GLOB 'canceled[_-]*'
        AND lower({status_column}) NOT GLOB 'cancelled[_-]*'
    """


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalized_prune_status(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _agent_prune_status_is_terminal(value: Any) -> bool:
    status = _normalized_prune_status(value)
    if not status:
        return False
    return (
        status in AGENT_PRUNE_TERMINAL_STATUSES
        or status.endswith("-complete")
        or status.endswith("-completed")
        or status.startswith("complete-")
        or status.startswith("completed-")
        or status.startswith("failed-")
        or status.startswith("blocked-")
        or status.startswith("canceled-")
        or status.startswith("cancelled-")
    )


def _agent_prune_status_is_nonterminal_stale(value: Any) -> bool:
    status = _normalized_prune_status(value)
    if not status:
        return False
    return (
        status in AGENT_PRUNE_NONTERMINAL_STALE_STATUSES
        or status.startswith("stale-")
    )


@dataclass(frozen=True)
class TokenRecord:
    token_hash: str
    kind: str
    label: str | None
    created_at: float
    expires_at: float | None
    revoked_at: float | None


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT,
                    created_at REAL NOT NULL,
                    expires_at REAL,
                    revoked_at REAL
                );

                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    used_at REAL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    agent_type TEXT NOT NULL DEFAULT 'caller',
                    project TEXT NOT NULL,
                    name TEXT,
                    status TEXT NOT NULL,
                    pbx_active INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    last_poll_at REAL,
                    latest_report_seen_at REAL,
                    starred_at REAL,
                    dismissed_at REAL
                );

                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    needs_input INTEGER NOT NULL DEFAULT 0,
                    plan_options_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS commands (
                    command_id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    claimed_at REAL,
                    acked_at REAL,
                    result_json TEXT,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS poll_events (
                    poll_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    delivered_count INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    subject_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS joplin_logs (
                    log_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    project TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    note_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    active INTEGER NOT NULL,
                    started_at REAL NOT NULL,
                    stopped_at REAL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS joplin_sync_jobs (
                    sync_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    agent_id TEXT,
                    note_id TEXT,
                    error TEXT,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS operator_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    operator_agent_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    criteria_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'running',
                    summary TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS operator_campaign_assignments (
                    assignment_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    target_agent_id TEXT NOT NULL,
                    operator_fork_id TEXT,
                    fork_track_id TEXT,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    criteria_json TEXT NOT NULL DEFAULT '[]',
                    state TEXT NOT NULL DEFAULT 'pending',
                    last_report_id TEXT,
                    last_command_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(target_agent_id) REFERENCES agents(agent_id)
                );

                CREATE TABLE IF NOT EXISTS operator_campaign_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    assignment_id TEXT,
                    operator_agent_id TEXT NOT NULL,
                    target_agent_id TEXT,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    report_id TEXT,
                    command_id TEXT,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS operator_forks (
                    operator_fork_id TEXT PRIMARY KEY,
                    logical_operator_agent_id TEXT NOT NULL,
                    fork_agent_id TEXT NOT NULL UNIQUE,
                    source_caller_agent_id TEXT NOT NULL,
                    source_codex_session_id TEXT NOT NULL,
                    fork_track_id TEXT NOT NULL DEFAULT 'default',
                    fork_purpose TEXT NOT NULL DEFAULT 'edit',
                    access_mode TEXT NOT NULL DEFAULT 'edit',
                    source_cwd TEXT,
                    work_root TEXT,
                    fork_codex_session_id TEXT,
                    campaign_id TEXT,
                    cwd TEXT NOT NULL,
                    codex_home TEXT,
                    codex_host_id TEXT,
                    tmux_pane_id TEXT,
                    status TEXT NOT NULL DEFAULT 'starting',
                    summary TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_used_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(fork_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_caller_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(campaign_id) REFERENCES operator_campaigns(campaign_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_project_spawn_requests (
                    spawn_request_id TEXT PRIMARY KEY,
                    logical_operator_agent_id TEXT NOT NULL,
                    operator_agent_id TEXT NOT NULL,
                    review_fork_id TEXT NOT NULL,
                    review_fork_agent_id TEXT NOT NULL,
                    source_caller_agent_id TEXT NOT NULL,
                    source_cwd TEXT NOT NULL,
                    target_parent TEXT NOT NULL,
                    target_slug TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    launched_agent_id TEXT,
                    tmux_pane_id TEXT,
                    error TEXT,
                    campaign_id TEXT,
                    assignment_id TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(review_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(review_fork_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_caller_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(launched_agent_id) REFERENCES agents(agent_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_fork_edges (
                    edge_id TEXT PRIMARY KEY,
                    from_fork_id TEXT NOT NULL,
                    to_fork_id TEXT NOT NULL,
                    edge_type TEXT NOT NULL,
                    summary TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(from_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(to_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE CASCADE,
                    UNIQUE(from_fork_id, to_fork_id, edge_type)
                );

                CREATE TABLE IF NOT EXISTS operator_knowledge_links (
                    link_id TEXT PRIMARY KEY,
                    logical_operator_agent_id TEXT NOT NULL,
                    operator_agent_id TEXT NOT NULL,
                    source_agent_id TEXT NOT NULL,
                    target_agent_id TEXT NOT NULL,
                    source_operator_fork_id TEXT,
                    link_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    summary TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    closed_at REAL,
                    FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(target_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_knowledge_turns (
                    turn_id TEXT PRIMARY KEY,
                    link_id TEXT NOT NULL,
                    sender_agent_id TEXT NOT NULL,
                    recipient_agent_id TEXT NOT NULL,
                    turn_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    delivery_status TEXT NOT NULL DEFAULT 'recorded',
                    command_id TEXT,
                    tmux_pane_id TEXT,
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(link_id) REFERENCES operator_knowledge_links(link_id)
                        ON DELETE CASCADE,
                    FOREIGN KEY(sender_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(recipient_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(command_id) REFERENCES commands(command_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_handoffs (
                    handoff_id TEXT PRIMARY KEY,
                    logical_operator_agent_id TEXT NOT NULL,
                    source_operator_agent_id TEXT NOT NULL,
                    target_operator_agent_id TEXT NOT NULL,
                    source_agent_id TEXT NOT NULL,
                    source_operator_fork_id TEXT,
                    target_caller_agent_id TEXT,
                    target_operator_fork_id TEXT,
                    knowledge_link_id TEXT,
                    knowledge_turn_id TEXT,
                    objective TEXT NOT NULL,
                    message TEXT NOT NULL,
                    allowed_mutation_scope TEXT,
                    required_artifacts_json TEXT NOT NULL DEFAULT '[]',
                    artifact_bundle_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    needs_ack INTEGER NOT NULL DEFAULT 1,
                    expires_at REAL,
                    acknowledged_at REAL,
                    started_at REAL,
                    completed_at REAL,
                    command_id TEXT,
                    tmux_pane_id TEXT,
                    delivery_status TEXT,
                    delivery_evidence_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    summary TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(target_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(target_caller_agent_id) REFERENCES agents(agent_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(target_operator_fork_id) REFERENCES operator_forks(operator_fork_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(knowledge_link_id) REFERENCES operator_knowledge_links(link_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(knowledge_turn_id) REFERENCES operator_knowledge_turns(turn_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(command_id) REFERENCES commands(command_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS agent_prune_batches (
                    batch_id TEXT PRIMARY KEY,
                    preset TEXT NOT NULL,
                    delete_thread INTEGER NOT NULL DEFAULT 0,
                    criteria_json TEXT NOT NULL DEFAULT '{}',
                    candidate_count INTEGER NOT NULL DEFAULT 0,
                    hidden_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    results_json TEXT NOT NULL DEFAULT '[]',
                    undo_results_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    undone_at REAL
                );

                CREATE TABLE IF NOT EXISTS operator_kb_entries (
                    kb_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    project TEXT,
                    repo_root TEXT,
                    git_remote TEXT,
                    branch TEXT,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    body TEXT NOT NULL,
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'proposed',
                    redaction_status TEXT NOT NULL DEFAULT 'unreviewed',
                    created_by_operator_agent_id TEXT NOT NULL,
                    created_by_agent_id TEXT NOT NULL,
                    source_knowledge_link_id TEXT,
                    source_handoff_id TEXT,
                    source_turn_ids_json TEXT NOT NULL DEFAULT '[]',
                    stale_after REAL,
                    expires_at REAL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    promoted_at REAL,
                    retired_at REAL,
                    imported_at REAL,
                    FOREIGN KEY(created_by_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(created_by_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_knowledge_link_id) REFERENCES operator_knowledge_links(link_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(source_handoff_id) REFERENCES operator_handoffs(handoff_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_kb_sources (
                    source_row_id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS operator_kb_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kb_id TEXT,
                    event_type TEXT NOT NULL,
                    operator_agent_id TEXT,
                    summary TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(kb_id) REFERENCES operator_kb_entries(kb_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(operator_agent_id) REFERENCES agents(agent_id)
                        ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS operator_kb_seed_runs (
                    seed_run_id TEXT PRIMARY KEY,
                    logical_operator_agent_id TEXT NOT NULL,
                    source_operator_agent_id TEXT NOT NULL,
                    seed_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    project TEXT,
                    repo_root TEXT,
                    git_remote TEXT,
                    branch TEXT,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'requested',
                    command_id TEXT,
                    tmux_pane_id TEXT,
                    delivery_status TEXT,
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    FOREIGN KEY(logical_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(source_operator_agent_id) REFERENCES agents(agent_id),
                    FOREIGN KEY(command_id) REFERENCES commands(command_id)
                        ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_operator_campaigns_operator_updated
                    ON operator_campaigns(operator_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_campaigns_status_updated
                    ON operator_campaigns(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_assignments_campaign_state
                    ON operator_campaign_assignments(campaign_id, state);
                CREATE INDEX IF NOT EXISTS idx_operator_assignments_target_updated
                    ON operator_campaign_assignments(target_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_events_campaign_created
                    ON operator_campaign_events(campaign_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_events_assignment_created
                    ON operator_campaign_events(assignment_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_forks_logical_updated
                    ON operator_forks(logical_operator_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_forks_source_updated
                    ON operator_forks(source_caller_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_forks_fork_agent
                    ON operator_forks(fork_agent_id);
                CREATE INDEX IF NOT EXISTS idx_operator_project_spawns_logical_status
                    ON operator_project_spawn_requests(
                        logical_operator_agent_id,
                        status,
                        created_at ASC
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_project_spawns_review_status
                    ON operator_project_spawn_requests(review_fork_id, status, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_operator_project_spawns_launched_agent
                    ON operator_project_spawn_requests(launched_agent_id);
                CREATE INDEX IF NOT EXISTS idx_operator_fork_edges_from
                    ON operator_fork_edges(from_fork_id);
                CREATE INDEX IF NOT EXISTS idx_operator_fork_edges_to
                    ON operator_fork_edges(to_fork_id);
                CREATE INDEX IF NOT EXISTS idx_operator_knowledge_links_logical_status
                    ON operator_knowledge_links(
                        logical_operator_agent_id,
                        status,
                        updated_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_knowledge_links_source
                    ON operator_knowledge_links(source_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_knowledge_links_target
                    ON operator_knowledge_links(target_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_knowledge_turns_link_created
                    ON operator_knowledge_turns(link_id, created_at ASC);
                CREATE INDEX IF NOT EXISTS idx_operator_knowledge_turns_recipient_status
                    ON operator_knowledge_turns(
                        recipient_agent_id,
                        delivery_status,
                        created_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_handoffs_logical_status
                    ON operator_handoffs(
                        logical_operator_agent_id,
                        status,
                        updated_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_handoffs_target_status
                    ON operator_handoffs(
                        target_operator_agent_id,
                        status,
                        updated_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_handoffs_source_updated
                    ON operator_handoffs(source_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_handoffs_target_caller
                    ON operator_handoffs(target_caller_agent_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_handoffs_expires
                    ON operator_handoffs(expires_at, status);
                CREATE INDEX IF NOT EXISTS idx_agent_prune_batches_created
                    ON agent_prune_batches(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_scope_status
                    ON operator_kb_entries(scope, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_project_status
                    ON operator_kb_entries(project, status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_source_link
                    ON operator_kb_entries(source_knowledge_link_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_source_handoff
                    ON operator_kb_entries(source_handoff_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_sources_kb
                    ON operator_kb_sources(kb_id, source_type);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_events_kb_created
                    ON operator_kb_events(kb_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_operator_kb_seed_runs_logical_status
                    ON operator_kb_seed_runs(
                        logical_operator_agent_id,
                        status,
                        updated_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_operator_kb_seed_runs_source
                    ON operator_kb_seed_runs(source_operator_agent_id, updated_at DESC);
                """
            )
            previous_schema_version = self._schema_version(conn)
            conn.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = 'queued', started_at = NULL
                WHERE status = 'running'
                """
            )
            self._ensure_column(conn, "agents", "last_poll_at", "REAL")
            self._ensure_column(
                conn,
                "agents",
                "agent_type",
                "TEXT NOT NULL DEFAULT 'caller'",
            )
            self._ensure_column(conn, "agents", "latest_report_seen_at", "REAL")
            self._ensure_column(conn, "agents", "starred_at", "REAL")
            self._ensure_column(conn, "agents", "dismissed_at", "REAL")
            self._ensure_column(
                conn,
                "agents",
                "pbx_active",
                "INTEGER NOT NULL DEFAULT 1",
            )
            self._ensure_column(
                conn,
                "reports",
                "metadata_json",
                "TEXT NOT NULL DEFAULT '{}'",
            )
            self._ensure_column(
                conn,
                "operator_campaign_assignments",
                "operator_fork_id",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "operator_campaign_assignments",
                "fork_track_id",
                "TEXT",
            )
            self._ensure_column(
                conn,
                "operator_forks",
                "fork_track_id",
                "TEXT NOT NULL DEFAULT 'default'",
            )
            self._ensure_column(
                conn,
                "operator_forks",
                "fork_purpose",
                "TEXT NOT NULL DEFAULT 'edit'",
            )
            self._ensure_column(
                conn,
                "operator_forks",
                "access_mode",
                "TEXT NOT NULL DEFAULT 'edit'",
            )
            self._ensure_column(conn, "operator_forks", "source_cwd", "TEXT")
            self._ensure_column(conn, "operator_forks", "work_root", "TEXT")
            conn.execute(
                """
                UPDATE operator_forks
                SET fork_track_id = COALESCE(NULLIF(fork_track_id, ''), 'default'),
                    fork_purpose = COALESCE(NULLIF(fork_purpose, ''), 'edit'),
                    access_mode = COALESCE(NULLIF(access_mode, ''), 'edit'),
                    source_cwd = COALESCE(NULLIF(source_cwd, ''), cwd),
                    work_root = COALESCE(NULLIF(work_root, ''), cwd)
                """
            )
            conn.execute("DROP INDEX IF EXISTS idx_operator_forks_source_session")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_operator_forks_source_session
                    ON operator_forks(
                        logical_operator_agent_id,
                        source_caller_agent_id,
                        source_codex_session_id,
                        fork_track_id
                    )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_operator_forks_track_updated
                    ON operator_forks(
                        logical_operator_agent_id,
                        fork_track_id,
                        updated_at DESC
                    )
                """
            )
            self._backfill_agent_type(conn)
            if previous_schema_version < 7:
                self._backfill_latest_report_seen(conn)
            conn.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _schema_version(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            return 0
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _backfill_latest_report_seen(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            UPDATE agents
            SET latest_report_seen_at = (
                SELECT MAX(reports.created_at)
                FROM reports
                WHERE reports.agent_id = agents.agent_id
            )
            WHERE latest_report_seen_at IS NULL
              AND EXISTS (
                SELECT 1
                FROM reports
                WHERE reports.agent_id = agents.agent_id
              )
            """
        )

    @staticmethod
    def _backfill_agent_type(conn: sqlite3.Connection) -> None:
        rows = conn.execute(
            "SELECT agent_id, agent_type, metadata_json FROM agents"
        ).fetchall()
        for row in rows:
            agent_type = str(row["agent_type"] or "").strip().lower()
            metadata: dict[str, Any] = {}
            try:
                decoded = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                decoded = {}
            if isinstance(decoded, dict):
                metadata = decoded
            metadata_type = str(metadata.get("agent_type") or "").strip().lower()
            resolved = (
                metadata_type
                if metadata_type in {"caller", "operator"}
                else agent_type
            )
            if resolved not in {"caller", "operator"}:
                resolved = "caller"
            if metadata_type or resolved == "operator":
                metadata["agent_type"] = resolved
            conn.execute(
                """
                UPDATE agents
                SET agent_type = ?, metadata_json = ?
                WHERE agent_id = ?
                """,
                (resolved, json.dumps(metadata), row["agent_id"]),
            )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def has_tokens(self) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM tokens WHERE revoked_at IS NULL LIMIT 1"
            ).fetchone()
            return row is not None

    def add_token(
        self,
        raw_token: str,
        *,
        kind: str,
        label: str | None = None,
        ttl_seconds: int | None = None,
    ) -> TokenRecord:
        created_at = now_ts()
        expires_at = created_at + ttl_seconds if ttl_seconds else None
        record = TokenRecord(
            token_hash=hash_secret(raw_token),
            kind=kind,
            label=label,
            created_at=created_at,
            expires_at=expires_at,
            revoked_at=None,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tokens
                    (token_hash, kind, label, created_at, expires_at, revoked_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    record.token_hash,
                    record.kind,
                    record.label,
                    record.created_at,
                    record.expires_at,
                ),
            )
        return record

    def verify_token(self, raw_token: str) -> TokenRecord | None:
        token_hash = hash_secret(raw_token)
        current = now_ts()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT token_hash, kind, label, created_at, expires_at, revoked_at
                FROM tokens
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (token_hash,),
            ).fetchone()
        if row is None:
            return None
        record = TokenRecord(**dict(row))
        if record.expires_at is not None and record.expires_at < current:
            return None
        return record

    def create_pairing_code(self, code: str, *, ttl_seconds: int) -> None:
        created_at = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pairing_codes
                    (code_hash, created_at, expires_at, used_at)
                VALUES (?, ?, ?, NULL)
                """,
                (hash_secret(code), created_at, created_at + ttl_seconds),
            )

    def consume_pairing_code(self, code: str) -> bool:
        code_hash = hash_secret(code)
        current = now_ts()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT code_hash FROM pairing_codes
                WHERE code_hash = ? AND used_at IS NULL AND expires_at >= ?
                """,
                (code_hash, current),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                "UPDATE pairing_codes SET used_at = ? WHERE code_hash = ?",
                (current, code_hash),
            )
            return True

    def append_event(
        self, event_type: str, payload: dict[str, Any], subject_id: str | None = None
    ) -> dict[str, Any]:
        created_at = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO events(type, subject_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_type, subject_id, json.dumps(payload), created_at),
            )
            event_id = int(cursor.lastrowid)
        return {
            "event_id": event_id,
            "type": event_type,
            "subject_id": subject_id,
            "payload": payload,
            "created_at": created_at,
        }

    def list_events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, type, subject_id, payload_json, created_at
                FROM events
                WHERE event_id > ?
                ORDER BY event_id ASC
                LIMIT ?
                """,
                (after_id, limit),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_recent_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, type, subject_id, payload_json, created_at
                FROM (
                    SELECT event_id, type, subject_id, payload_json, created_at
                    FROM events
                    ORDER BY event_id DESC
                    LIMIT ?
                )
                ORDER BY event_id ASC
                """,
                (limit,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def register_agent(self, request: AgentRegisterRequest) -> dict[str, Any]:
        current = now_ts()
        with self.connect() as conn:
            existing = conn.execute(
                """
                SELECT name, agent_type, project, metadata_json
                FROM agents
                WHERE agent_id = ?
                """,
                (request.agent_id,),
            ).fetchone()
            existing_fork = self._operator_fork_row_for_agent(conn, request.agent_id)
            existing_fork_identity = (
                self._operator_fork_agent_identity(existing_fork)
                if existing_fork is not None
                else None
            )
            request_metadata = dict(request.metadata)
            existing_type = (
                self._normalized_agent_type(existing["agent_type"])
                if existing
                else "caller"
            )
            existing_metadata = self._json_object(existing["metadata_json"]) if existing else {}
            existing_operator_role = (
                str(existing_metadata.get("operator_role") or "").strip().lower()
            )
            requested_type = self._normalized_agent_type(request.agent_type)
            has_metadata_agent_type = "agent_type" in request_metadata
            metadata_type = (
                self._normalized_agent_type(request_metadata.get("agent_type"))
                if has_metadata_agent_type
                else ""
            )
            requested_operator_role = (
                str(request_metadata.get("operator_role") or "").strip().lower()
            )
            preserve_existing_operator_identity = (
                existing_type == "operator"
                and requested_type == "caller"
                and not has_metadata_agent_type
            )
            if existing_fork_identity is not None:
                agent_type = OPERATOR_AGENT_TYPE
            elif requested_type == "operator" or metadata_type == "operator":
                agent_type = "operator"
            elif existing_type == "operator" and metadata_type != "caller":
                agent_type = "operator"
            else:
                agent_type = "caller"
            if agent_type == "operator" or has_metadata_agent_type:
                request_metadata["agent_type"] = agent_type
            is_root_operator_registration = (
                agent_type == OPERATOR_AGENT_TYPE
                and existing_fork_identity is None
                and requested_operator_role != OPERATOR_ROLE_FORK
            )
            preserve_existing_root_operator_identity = (
                is_root_operator_registration
                and existing_type == OPERATOR_AGENT_TYPE
                and existing_operator_role != OPERATOR_ROLE_FORK
                and request.project != OPERATOR_PROJECT
            )
            if preserve_existing_operator_identity:
                for key in (
                    "agent_type",
                    "operator_role",
                    "logical_operator_id",
                    "source_caller_agent_id",
                    "source_codex_session_id",
                    "cwd",
                    "tmux_pane_id",
                    "tmux_session",
                    "launched_by",
                    "mcp_url",
                    "token_env",
                    "codex_command",
                    "operator_session_history",
                    "last_resume_codex_session_id",
                ):
                    request_metadata.pop(key, None)
            if preserve_existing_root_operator_identity:
                for key in (
                    "operator_role",
                    "logical_operator_id",
                    "source_caller_agent_id",
                    "source_codex_session_id",
                    "cwd",
                    "tmux_pane_id",
                    "tmux_target",
                    "tmux_session",
                    "active_tmux_pane_id",
                    "launched_by",
                    "mcp_url",
                    "token_env",
                    "codex_command",
                    "operator_session_history",
                    "last_resume_codex_session_id",
                ):
                    request_metadata.pop(key, None)
            if is_root_operator_registration:
                request_metadata["operator_role"] = OPERATOR_ROLE_ROOT
            if existing_fork_identity is not None and requested_operator_role != OPERATOR_ROLE_FORK:
                for key in (
                    "agent_type",
                    "operator_role",
                    "logical_operator_id",
                    "source_caller_agent_id",
                    "source_codex_session_id",
                    "fork_codex_session_id",
                    "cwd",
                    "tmux_pane_id",
                    "tmux_target",
                    "tmux_session",
                    "active_tmux_pane_id",
                ):
                    request_metadata.pop(key, None)
            metadata = self._merged_agent_metadata(
                existing["metadata_json"] if existing else None,
                request_metadata,
            )
            if existing_fork_identity is not None:
                metadata.update(existing_fork_identity)
            elif is_root_operator_registration:
                metadata["operator_role"] = OPERATOR_ROLE_ROOT
            metadata_json = json.dumps(metadata)
            name = request.name
            project = request.project
            if existing_fork_identity is not None and existing:
                name = name or existing["name"]
                project = existing["project"]
            elif is_root_operator_registration:
                project = OPERATOR_PROJECT
                if preserve_existing_root_operator_identity and existing:
                    name = existing["name"]
            elif preserve_existing_operator_identity and existing:
                name = existing["name"]
                project = existing["project"]
            elif name is None and existing:
                name = existing["name"]
            conn.execute(
                """
                INSERT INTO agents
                    (agent_id, agent_type, project, name, status, pbx_active, metadata_json,
                     created_at, last_seen_at, dismissed_at)
                VALUES (?, ?, ?, ?, 'online', ?, ?, ?, ?, NULL)
                ON CONFLICT(agent_id) DO UPDATE SET
                    agent_type = excluded.agent_type,
                    project = excluded.project,
                    name = excluded.name,
                    status = 'online',
                    pbx_active = excluded.pbx_active,
                    metadata_json = excluded.metadata_json,
                    last_seen_at = excluded.last_seen_at,
                    dismissed_at = NULL
                """,
                (
                    request.agent_id,
                    agent_type,
                    project,
                    name,
                    int(request.pbx_active),
                    metadata_json,
                    current,
                    current,
                ),
            )
        return self.get_agent(request.agent_id) or {}

    @staticmethod
    def _normalized_agent_type(value: Any) -> str:
        agent_type = str(value or "caller").strip().lower()
        if agent_type == "operator":
            return "operator"
        return "caller"

    @staticmethod
    def _merged_agent_metadata(
        existing_json: str | None,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        existing = Store._json_object(existing_json)

        merged = dict(existing)
        for key, value in metadata.items():
            if value is None:
                continue
            if (
                key == "cwd"
                and not _non_empty_string(value)
                and _non_empty_string(merged.get("cwd"))
            ):
                continue
            merged[key] = value
        Store._backfill_agent_cwd(merged)
        return merged

    @staticmethod
    def _json_object(value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    @staticmethod
    def _backfill_agent_cwd(metadata: dict[str, Any]) -> None:
        if _non_empty_string(metadata.get("cwd")):
            return
        repo = metadata.get("repo")
        if not _non_empty_string(repo):
            return
        repo_path = Path(str(repo)).expanduser()
        if repo_path.is_absolute() and repo_path.is_dir():
            metadata["cwd"] = str(repo_path)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_id, agent_type, project, name, status, pbx_active, metadata_json,
                       created_at, last_seen_at, last_poll_at,
                       latest_report_seen_at, starred_at, dismissed_at
                FROM agents
                WHERE agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
            fork_row = self._operator_fork_row_for_agent(conn, agent_id) if row else None
        if not row:
            return None
        agent = self._agent_from_row(row)
        if fork_row is not None:
            self._hydrate_operator_fork_agent(agent, fork_row)
        return agent

    def list_agents(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        where_clause = "" if include_hidden else "WHERE dismissed_at IS NULL"
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT agent_id, agent_type, project, name, status, pbx_active, metadata_json,
                       created_at, last_seen_at, last_poll_at,
                       latest_report_seen_at, starred_at, dismissed_at
                FROM agents
                {where_clause}
                ORDER BY last_seen_at DESC, agent_id ASC
                """
            ).fetchall()
            agents = [self._agent_from_row(row) for row in rows]
            fork_rows = self._operator_fork_rows_for_agents(
                conn,
                [str(agent["agent_id"]) for agent in agents],
            )
            for agent in agents:
                fork_row = fork_rows.get(str(agent["agent_id"]))
                if fork_row is not None:
                    self._hydrate_operator_fork_agent(agent, fork_row)
                self._add_latest_report_summary(conn, agent)
                self._add_queue_summary(conn, agent)
                self._add_campaign_summary(conn, agent)
                self._add_usage_summary(conn, agent)
        return agents

    def unhide_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (agent_id,),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def dismiss_agent(
        self, agent_id: str, *, delete_thread: bool = False
    ) -> dict[str, Any] | None:
        agent = self.get_agent(agent_id)
        if agent is None:
            return None
        current = now_ts()
        with self.connect() as conn:
            if delete_thread:
                conn.execute("DELETE FROM reports WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM commands WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM poll_events WHERE agent_id = ?", (agent_id,))
                conn.execute("DELETE FROM joplin_logs WHERE agent_id = ?", (agent_id,))
                conn.execute(
                    "DELETE FROM joplin_sync_jobs WHERE agent_id = ?",
                    (agent_id,),
                )
            cursor = conn.execute(
                """
                UPDATE agents
                SET dismissed_at = ?, last_seen_at = ?
                WHERE agent_id = ?
                """,
                (current, current, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def preview_agent_prune(
        self,
        *,
        preset: str = "terminal-callers",
        min_age_days: float = 30.0,
        include_projects: list[str] | None = None,
        exclude_projects: list[str] | None = None,
        agent_ids: list[str] | None = None,
        include_hidden: bool = False,
        include_starred: bool = False,
        require_no_tmux_pane: bool = True,
        limit: int = 500,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_preset = str(preset or "terminal-callers").strip().lower()
        if normalized_preset not in AGENT_PRUNE_PRESETS:
            raise ValueError("prune preset is invalid")
        safe_min_age_days = max(0.0, float(min_age_days))
        safe_limit = min(max(int(limit), 1), 2000)
        include_project_set = {
            project.strip()
            for project in include_projects or []
            if isinstance(project, str) and project.strip()
        }
        exclude_project_set = {
            project.strip()
            for project in exclude_projects or []
            if isinstance(project, str) and project.strip()
        }
        explicit_agent_ids = {
            agent_id.strip()
            for agent_id in agent_ids or []
            if isinstance(agent_id, str) and agent_id.strip()
        }
        agents = self.list_agents(include_hidden=include_hidden)
        protection_reasons = self.agent_prune_protection_reasons()
        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        current = now_ts()
        for agent in agents:
            agent_id = str(agent.get("agent_id") or "").strip()
            project = str(agent.get("project") or "").strip()
            if not agent_id:
                continue
            if explicit_agent_ids and agent_id not in explicit_agent_ids:
                continue
            if include_project_set and project not in include_project_set:
                continue
            if exclude_project_set and project in exclude_project_set:
                continue
            reason = self._agent_prune_match_reason(
                agent,
                preset=normalized_preset,
                min_age_days=safe_min_age_days,
                now=current,
            )
            if reason is None:
                continue
            guard_reasons: list[str] = []
            if bool(agent.get("starred")) and not include_starred:
                guard_reasons.append("starred")
            if agent.get("dismissed_at") is not None and not include_hidden:
                guard_reasons.append("hidden")
            if int(agent.get("queued_command_count") or 0) > 0:
                guard_reasons.append("queued_commands")
            if int(agent.get("active_campaign_count") or 0) > 0:
                guard_reasons.append("active_campaign")
            guard_reasons.extend(protection_reasons.get(agent_id, []))
            if (
                normalized_preset == "operator-forks"
                and require_no_tmux_pane
                and self._agent_has_recorded_tmux_pane(agent)
            ):
                guard_reasons.append("tmux_pane_recorded")
            item = self._agent_prune_candidate_from_agent(
                agent,
                reason=reason,
                guard_reasons=guard_reasons,
                now=current,
            )
            if guard_reasons:
                skipped.append(item)
            elif len(candidates) < safe_limit:
                candidates.append(item)
            else:
                skipped.append(
                    {
                        **item,
                        "guard_reasons": [*item["guard_reasons"], "limit_exceeded"],
                    }
                )
        def sort_key(item: dict[str, Any]) -> tuple[str, float, str]:
            return (
                str(item.get("project") or ""),
                float(item.get("last_seen_at") or 0.0),
                str(item.get("agent_id") or ""),
            )

        candidates.sort(key=sort_key)
        skipped.sort(key=sort_key)
        return {
            "preset": normalized_preset,
            "min_age_days": safe_min_age_days,
            "include_hidden": include_hidden,
            "include_starred": include_starred,
            "require_no_tmux_pane": require_no_tmux_pane,
            "delete_thread": False,
            "candidate_count": len(candidates),
            "skipped_count": len(skipped),
            "candidates": candidates,
            "skipped": skipped,
            "metadata": metadata or {},
        }

    def apply_agent_prune(
        self,
        *,
        preview: dict[str, Any],
        criteria: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        batch_id = str(uuid.uuid4())
        current = now_ts()
        results: list[dict[str, Any]] = []
        candidates = [
            candidate
            for candidate in preview.get("candidates", [])
            if isinstance(candidate, dict)
        ]
        with self.connect() as conn:
            for candidate in candidates:
                agent_id = str(candidate.get("agent_id") or "").strip()
                if not agent_id:
                    continue
                cursor = conn.execute(
                    """
                    UPDATE agents
                    SET dismissed_at = ?, last_seen_at = ?
                    WHERE agent_id = ? AND dismissed_at IS NULL
                    """,
                    (current, current, agent_id),
                )
                result = {
                    "agent_id": agent_id,
                    "project": candidate.get("project"),
                    "status": candidate.get("status"),
                    "reason": candidate.get("reason"),
                    "result": "hidden" if cursor.rowcount else "skipped",
                }
                if not cursor.rowcount:
                    result["skip_reason"] = "not_visible"
                results.append(result)
            hidden_count = sum(1 for item in results if item.get("result") == "hidden")
            skipped_count = len(results) - hidden_count + int(
                preview.get("skipped_count") or 0
            )
            conn.execute(
                """
                INSERT INTO agent_prune_batches
                    (batch_id, preset, delete_thread, criteria_json,
                     candidate_count, hidden_count, skipped_count, results_json,
                     undo_results_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    str(preview.get("preset") or "terminal-callers"),
                    0,
                    json.dumps(criteria),
                    int(preview.get("candidate_count") or len(candidates)),
                    hidden_count,
                    skipped_count,
                    json.dumps(results),
                    json.dumps([]),
                    json.dumps(metadata or {}),
                    current,
                ),
            )
            conn.execute(
                """
                INSERT INTO events(type, subject_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "agent_prune_applied",
                    batch_id,
                    json.dumps(
                        {
                            "batch_id": batch_id,
                            "preset": str(preview.get("preset") or "terminal-callers"),
                            "candidate_count": int(
                                preview.get("candidate_count") or len(candidates)
                            ),
                            "hidden_count": hidden_count,
                            "skipped_count": skipped_count,
                            "delete_thread": False,
                        }
                    ),
                    current,
                ),
            )
        batch = self.get_agent_prune_batch(batch_id)
        if batch is None:
            raise RuntimeError("agent prune batch insert failed")
        return batch

    def get_agent_prune_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT batch_id, preset, delete_thread, criteria_json,
                       candidate_count, hidden_count, skipped_count,
                       results_json, undo_results_json, metadata_json,
                       created_at, undone_at
                FROM agent_prune_batches
                WHERE batch_id = ?
                """,
                (batch_id,),
            ).fetchone()
        return self._agent_prune_batch_from_row(row) if row else None

    def list_agent_prune_batches(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 200)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT batch_id, preset, delete_thread, criteria_json,
                       candidate_count, hidden_count, skipped_count,
                       results_json, undo_results_json, metadata_json,
                       created_at, undone_at
                FROM agent_prune_batches
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._agent_prune_batch_from_row(row) for row in rows]

    def undo_agent_prune_batch(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.get_agent_prune_batch(batch_id)
        if batch is None:
            return None
        if batch.get("undone_at") is not None:
            return batch
        current = now_ts()
        undo_results: list[dict[str, Any]] = []
        hidden_agent_ids = [
            str(item.get("agent_id") or "").strip()
            for item in batch.get("results", [])
            if isinstance(item, dict) and item.get("result") == "hidden"
        ]
        with self.connect() as conn:
            for agent_id in hidden_agent_ids:
                if not agent_id:
                    continue
                cursor = conn.execute(
                    """
                    UPDATE agents
                    SET dismissed_at = NULL
                    WHERE agent_id = ? AND dismissed_at IS NOT NULL
                    """,
                    (agent_id,),
                )
                undo_results.append(
                    {
                        "agent_id": agent_id,
                        "result": "unhidden" if cursor.rowcount else "skipped",
                        "skip_reason": None if cursor.rowcount else "already_visible",
                    }
                )
            conn.execute(
                """
                UPDATE agent_prune_batches
                SET undone_at = ?, undo_results_json = ?
                WHERE batch_id = ?
                """,
                (current, json.dumps(undo_results), batch_id),
            )
            conn.execute(
                """
                INSERT INTO events(type, subject_id, payload_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "agent_prune_undone",
                    batch_id,
                    json.dumps(
                        {
                            "batch_id": batch_id,
                            "unhidden_count": sum(
                                1
                                for item in undo_results
                                if item.get("result") == "unhidden"
                            ),
                            "skipped_count": sum(
                                1
                                for item in undo_results
                                if item.get("result") == "skipped"
                            ),
                        }
                    ),
                    current,
                ),
            )
        return self.get_agent_prune_batch(batch_id)

    def set_agent_pbx_active(self, agent_id: str, active: bool) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET pbx_active = ?, last_seen_at = ?, dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (int(active), current, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def create_report(
        self, agent_id: str, request: ReportCreateRequest
    ) -> dict[str, Any]:
        report_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE agents
                SET status = ?, last_seen_at = ?, dismissed_at = NULL
                WHERE agent_id = ?
                """,
                (request.status, current, agent_id),
            )
            conn.execute(
                """
                INSERT INTO reports
                    (report_id, agent_id, project, status, summary, detail,
                     needs_input, plan_options_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    agent_id,
                    request.project,
                    request.status,
                    request.summary,
                    request.detail,
                    int(request.needs_input),
                    json.dumps(plan_options_to_jsonable(request.plan_options)),
                    json.dumps(request.metadata),
                    current,
                ),
            )
        report = self.get_report(report_id)
        if report is None:
            raise RuntimeError("report insert failed")
        return report

    @staticmethod
    def report_request_with_identity_metadata(
        request: ReportCreateRequest,
    ) -> ReportCreateRequest:
        reporting_agent_id = str(request.reporting_agent_id or "").strip()
        if not reporting_agent_id:
            return request
        metadata = dict(request.metadata or {})
        metadata.setdefault("reporting_agent_id", reporting_agent_id)
        return request.model_copy(update={"metadata": metadata})

    @classmethod
    def declared_reporting_agent_id(
        cls,
        request: ReportCreateRequest,
    ) -> str | None:
        reporting_agent_id = str(request.reporting_agent_id or "").strip()
        if reporting_agent_id:
            return reporting_agent_id
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        for key in REPORTING_AGENT_ID_METADATA_KEYS:
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return None

    def report_identity_violation_payload(
        self,
        agent_id: str,
        request: ReportCreateRequest,
    ) -> dict[str, Any] | None:
        reporting_agent_id = self.declared_reporting_agent_id(request)
        if not reporting_agent_id or reporting_agent_id == agent_id:
            return None
        target_agent = self.get_agent(agent_id)
        reporting_agent = self.get_agent(reporting_agent_id)
        return {
            "agent_id": agent_id,
            "reporting_agent_id": reporting_agent_id,
            "target_agent_type": (
                target_agent.get("agent_type") if isinstance(target_agent, dict) else None
            ),
            "reporting_agent_type": (
                reporting_agent.get("agent_type")
                if isinstance(reporting_agent, dict)
                else None
            ),
            "project": request.project,
            "status": request.status,
            "summary": request.summary,
            "reason": (
                "reporting_agent_id must match agent_id; use operator "
                "campaign or handoff tools instead of writing reports for "
                "another agent"
            ),
        }

    def mark_latest_report_seen(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT created_at
                FROM reports
                WHERE agent_id = ?
                ORDER BY created_at DESC, report_id DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
            if row is None:
                exists = conn.execute(
                    "SELECT 1 FROM agents WHERE agent_id = ?",
                    (agent_id,),
                ).fetchone()
                return self.get_agent(agent_id) if exists is not None else None
            seen_at = float(row["created_at"])
            cursor = conn.execute(
                """
                UPDATE agents
                SET latest_report_seen_at = MAX(
                    COALESCE(latest_report_seen_at, 0),
                    ?
                )
                WHERE agent_id = ?
                """,
                (seen_at, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def set_agent_starred(
        self, agent_id: str, *, starred: bool
    ) -> dict[str, Any] | None:
        starred_at = now_ts() if starred else None
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE agents
                SET starred_at = ?
                WHERE agent_id = ?
                """,
                (starred_at, agent_id),
            )
        return self.get_agent(agent_id) if cursor.rowcount else None

    def start_joplin_log(
        self,
        *,
        agent_id: str,
        project: str,
        session_id: str,
        note_id: str,
        title: str,
    ) -> dict[str, Any]:
        log_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE joplin_logs
                SET active = 0, stopped_at = COALESCE(stopped_at, ?), updated_at = ?
                WHERE agent_id = ? AND active = 1
                """,
                (current, current, agent_id),
            )
            conn.execute(
                """
                INSERT INTO joplin_logs
                    (log_id, agent_id, project, session_id, note_id, title,
                     active, started_at, stopped_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?)
                """,
                (
                    log_id,
                    agent_id,
                    project,
                    session_id,
                    note_id,
                    title,
                    current,
                    current,
                ),
            )
        log = self.get_joplin_log(log_id)
        if log is None:
            raise RuntimeError("joplin log insert failed")
        return log

    def get_joplin_log(self, log_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs
                WHERE log_id = ?
                """,
                (log_id,),
            ).fetchone()
        return self._joplin_log_from_row(row) if row else None

    def get_active_joplin_log(self, agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT log_id, agent_id, project, session_id, note_id, title,
                       active, started_at, stopped_at, updated_at
                FROM joplin_logs
                WHERE agent_id = ? AND active = 1
                ORDER BY started_at DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        return self._joplin_log_from_row(row) if row else None

    def stop_active_joplin_log(self, agent_id: str) -> dict[str, Any] | None:
        active = self.get_active_joplin_log(agent_id)
        if active is None:
            return None
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE joplin_logs
                SET active = 0, stopped_at = ?, updated_at = ?
                WHERE log_id = ?
                """,
                (current, current, active["log_id"]),
            )
        return self.get_joplin_log(active["log_id"])

    def touch_joplin_log(self, log_id: str) -> None:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                "UPDATE joplin_logs SET updated_at = ? WHERE log_id = ?",
                (current, log_id),
            )

    def enqueue_joplin_sync(
        self,
        *,
        reason: str,
        agent_id: str | None = None,
        note_id: str | None = None,
    ) -> dict[str, Any]:
        sync_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO joplin_sync_jobs
                    (sync_id, status, reason, agent_id, note_id, error,
                     created_at, started_at, finished_at, attempts)
                VALUES (?, 'queued', ?, ?, ?, NULL, ?, NULL, NULL, 0)
                """,
                (sync_id, reason, agent_id, note_id, current),
            )
        job = self.get_joplin_sync_job(sync_id)
        if job is None:
            raise RuntimeError("joplin sync job insert failed")
        return job

    def claim_next_joplin_sync_job(self) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT sync_id
                FROM joplin_sync_jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC, sync_id ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            sync_id = str(row["sync_id"])
            cursor = conn.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = 'running',
                    started_at = ?,
                    finished_at = NULL,
                    error = NULL,
                    attempts = attempts + 1
                WHERE sync_id = ? AND status = 'queued'
                """,
                (current, sync_id),
            )
            if not cursor.rowcount:
                return None
        return self.get_joplin_sync_job(sync_id)

    def complete_joplin_sync_job(
        self,
        sync_id: str,
        *,
        success: bool,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        current = now_ts()
        status = "succeeded" if success else "failed"
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE joplin_sync_jobs
                SET status = ?, error = ?, finished_at = ?
                WHERE sync_id = ? AND status = 'running'
                """,
                (status, error, current, sync_id),
            )
        return self.get_joplin_sync_job(sync_id) if cursor.rowcount else None

    def get_joplin_sync_job(self, sync_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                WHERE sync_id = ?
                """,
                (sync_id,),
            ).fetchone()
        return self._joplin_sync_job_from_row(row) if row else None

    def list_joplin_sync_jobs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                ORDER BY created_at DESC, sync_id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [self._joplin_sync_job_from_row(row) for row in rows]

    def joplin_sync_status(self) -> dict[str, Any]:
        with self.connect() as conn:
            count_rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM joplin_sync_jobs
                GROUP BY status
                """
            ).fetchall()
            latest = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                ORDER BY COALESCE(finished_at, started_at, created_at) DESC,
                         sync_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_success = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                WHERE status = 'succeeded'
                ORDER BY finished_at DESC, sync_id DESC
                LIMIT 1
                """
            ).fetchone()
            latest_error = conn.execute(
                """
                SELECT sync_id, status, reason, agent_id, note_id, error,
                       created_at, started_at, finished_at, attempts
                FROM joplin_sync_jobs
                WHERE status = 'failed'
                ORDER BY finished_at DESC, sync_id DESC
                LIMIT 1
                """
            ).fetchone()
        counts = {str(row["status"]): int(row["count"] or 0) for row in count_rows}
        return {
            "pending": counts.get("queued", 0),
            "running": counts.get("running", 0),
            "succeeded": counts.get("succeeded", 0),
            "failed": counts.get("failed", 0),
            "latest": self._joplin_sync_job_from_row(latest) if latest else None,
            "latest_success": (
                self._joplin_sync_job_from_row(latest_success)
                if latest_success
                else None
            ),
            "latest_error": (
                self._joplin_sync_job_from_row(latest_error)
                if latest_error
                else None
            ),
        }

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, metadata_json, created_at
                FROM reports
                WHERE report_id = ?
                """,
                (report_id,),
            ).fetchone()
        return self._report_from_row(row) if row else None

    def list_reports(self, agent_id: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 100)
        if agent_id is None:
            query = """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, metadata_json, created_at
                FROM reports
                ORDER BY created_at DESC
                LIMIT ?
            """
            params: tuple[Any, ...] = (safe_limit,)
        else:
            query = """
                SELECT report_id, agent_id, project, status, summary, detail,
                       needs_input, plan_options_json, metadata_json, created_at
                FROM reports
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """
            params = (agent_id, safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._report_from_row(row) for row in rows]

    def list_thread(self, agent_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 200)
        reports = self.list_reports(agent_id, limit=safe_limit)
        with self.connect() as conn:
            command_rows = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json, status, created_at,
                       claimed_at, acked_at, result_json
                FROM commands
                WHERE agent_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (agent_id, safe_limit),
            ).fetchall()
        commands = [self._command_from_row(row) for row in command_rows]
        items = [self._thread_report(report) for report in reports]
        items.extend(
            self._thread_command(command)
            for command in commands
            if command is not None and command["agent_id"] is not None
        )
        latest = sorted(
            items,
            key=lambda item: (float(item["created_at"]), str(item["item_id"])),
            reverse=True,
        )[:safe_limit]
        return sorted(
            latest,
            key=lambda item: (float(item["created_at"]), str(item["item_id"])),
        )

    def create_operator_campaign(
        self,
        *,
        operator_agent_id: str,
        title: str,
        objective: str,
        criteria: list[str],
        assignments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        campaign_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_campaigns
                    (campaign_id, operator_agent_id, title, objective,
                     criteria_json, status, summary, created_at, updated_at,
                     completed_at)
                VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, ?, NULL)
                """,
                (
                    campaign_id,
                    operator_agent_id,
                    title,
                    objective,
                    json.dumps(criteria),
                    current,
                    current,
                ),
            )
            for assignment in assignments:
                assignment_id = str(uuid.uuid4())
                target_agent_id = str(assignment["target_agent_id"])
                assignment_title = str(
                    assignment.get("title") or f"Assignment for {target_agent_id}"
                )
                conn.execute(
                    """
                    INSERT INTO operator_campaign_assignments
                        (assignment_id, campaign_id, target_agent_id, operator_fork_id,
                         fork_track_id, title, prompt, criteria_json, state, last_report_id,
                         last_command_id, created_at, updated_at, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, NULL)
                    """,
                    (
                        assignment_id,
                        campaign_id,
                        target_agent_id,
                        assignment.get("operator_fork_id"),
                        assignment.get("fork_track_id"),
                        assignment_title,
                        str(assignment["prompt"]),
                        json.dumps(assignment.get("criteria") or []),
                        current,
                        current,
                    ),
                )
            conn.execute(
                """
                INSERT INTO operator_campaign_events
                    (campaign_id, assignment_id, operator_agent_id,
                     target_agent_id, event_type, summary, detail_json,
                     report_id, command_id, created_at)
                VALUES (?, NULL, ?, NULL, 'campaign_started', ?, ?, NULL, NULL, ?)
                """,
                (
                    campaign_id,
                    operator_agent_id,
                    f"Campaign started: {title}",
                    json.dumps({"objective": objective, "criteria": criteria}),
                    current,
                ),
            )
        campaign = self.get_operator_campaign(campaign_id)
        if campaign is None:
            raise RuntimeError("operator campaign insert failed")
        return campaign

    def get_operator_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        campaigns = self.list_operator_campaigns(campaign_id=campaign_id, limit=1)
        return campaigns[0] if campaigns else None

    def list_operator_campaigns(
        self,
        *,
        operator_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        include_events: bool = True,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 200)
        where: list[str] = []
        params: list[Any] = []
        if operator_agent_id:
            where.append("operator_agent_id = ?")
            params.append(operator_agent_id)
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT campaign_id, operator_agent_id, title, objective,
                   criteria_json, status, summary, created_at, updated_at,
                   completed_at
            FROM operator_campaigns
            {clause}
            ORDER BY updated_at DESC, campaign_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        campaigns = [self._operator_campaign_from_row(row) for row in rows]
        for campaign in campaigns:
            campaign["assignments"] = self.list_operator_campaign_assignments(
                campaign_id=campaign["campaign_id"]
            )
            campaign["events"] = (
                self.list_operator_campaign_events(
                    campaign_id=campaign["campaign_id"],
                    limit=25,
                )
                if include_events
                else []
            )
        return campaigns

    def list_operator_campaign_assignments(
        self,
        *,
        campaign_id: str | None = None,
        target_agent_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if target_agent_id:
            where.append("target_agent_id = ?")
            params.append(target_agent_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT assignment_id, campaign_id, target_agent_id, operator_fork_id,
                   fork_track_id, title, prompt,
                   criteria_json, state, last_report_id, last_command_id,
                   created_at, updated_at, completed_at
            FROM operator_campaign_assignments
            {clause}
            ORDER BY updated_at DESC, assignment_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_assignment_from_row(row) for row in rows]

    def get_operator_campaign_assignment(
        self,
        assignment_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT assignment_id, campaign_id, target_agent_id, operator_fork_id,
                       fork_track_id, title, prompt,
                       criteria_json, state, last_report_id, last_command_id,
                       created_at, updated_at, completed_at
                FROM operator_campaign_assignments
                WHERE assignment_id = ?
                """,
                (assignment_id,),
            ).fetchone()
        return self._operator_assignment_from_row(row) if row else None

    def update_operator_assignment(
        self,
        assignment_id: str,
        *,
        state: str | None = None,
        last_report_id: str | None = None,
        last_command_id: str | None = None,
        operator_fork_id: str | None = None,
        fork_track_id: str | None = None,
        complete: bool | None = None,
    ) -> dict[str, Any] | None:
        assignment = self.get_operator_campaign_assignment(assignment_id)
        if assignment is None:
            return None
        current = now_ts()
        completed_at = (
            current
            if complete is True
            else None
            if complete is False
            else assignment.get("completed_at")
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE operator_campaign_assignments
                SET state = COALESCE(?, state),
                    last_report_id = COALESCE(?, last_report_id),
                    last_command_id = COALESCE(?, last_command_id),
                    operator_fork_id = COALESCE(?, operator_fork_id),
                    fork_track_id = COALESCE(?, fork_track_id),
                    updated_at = ?,
                    completed_at = ?
                WHERE assignment_id = ?
                """,
                (
                    state,
                    last_report_id,
                    last_command_id,
                    operator_fork_id,
                    fork_track_id,
                    current,
                    completed_at,
                    assignment_id,
                ),
            )
            conn.execute(
                """
                UPDATE operator_campaigns
                SET updated_at = ?
                WHERE campaign_id = ?
                """,
                (current, assignment["campaign_id"]),
            )
        return self.get_operator_campaign_assignment(assignment_id)

    def update_operator_campaign(
        self,
        campaign_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        complete: bool | None = None,
    ) -> dict[str, Any] | None:
        campaign = self.get_operator_campaign(campaign_id)
        if campaign is None:
            return None
        current = now_ts()
        completed_at = (
            current
            if complete is True
            else None
            if complete is False
            else campaign.get("completed_at")
        )
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE operator_campaigns
                SET status = COALESCE(?, status),
                    summary = COALESCE(?, summary),
                    updated_at = ?,
                    completed_at = ?
                WHERE campaign_id = ?
                """,
                (status, summary, current, completed_at, campaign_id),
            )
        return self.get_operator_campaign(campaign_id)

    def add_operator_campaign_event(
        self,
        *,
        campaign_id: str,
        operator_agent_id: str,
        event_type: str,
        summary: str,
        assignment_id: str | None = None,
        target_agent_id: str | None = None,
        detail: dict[str, Any] | None = None,
        report_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, Any]:
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO operator_campaign_events
                    (campaign_id, assignment_id, operator_agent_id,
                     target_agent_id, event_type, summary, detail_json,
                     report_id, command_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    campaign_id,
                    assignment_id,
                    operator_agent_id,
                    target_agent_id,
                    event_type,
                    summary,
                    json.dumps(detail or {}),
                    report_id,
                    command_id,
                    current,
                ),
            )
            event_id = int(cursor.lastrowid)
            conn.execute(
                """
                UPDATE operator_campaigns
                SET updated_at = ?
                WHERE campaign_id = ?
                """,
                (current, campaign_id),
            )
        event = self.get_operator_campaign_event(event_id)
        if event is None:
            raise RuntimeError("operator campaign event insert failed")
        operator_fork_id = None
        fork_agent_id = None
        if assignment_id:
            assignment = self.get_operator_campaign_assignment(assignment_id)
            if assignment is not None:
                operator_fork_id = str(assignment.get("operator_fork_id") or "").strip() or None
        if detail:
            operator_fork_id = (
                operator_fork_id
                or str(detail.get("operator_fork_id") or "").strip()
                or None
            )
            fork_agent_id = str(detail.get("fork_agent_id") or "").strip() or None
        if operator_fork_id and not fork_agent_id:
            fork = self.get_operator_fork(operator_fork_id)
            if fork is not None:
                fork_agent_id = str(fork.get("fork_agent_id") or "").strip() or None
        self.append_event(
            "operator_campaign_event",
            {
                "campaign_id": campaign_id,
                "assignment_id": assignment_id,
                "operator_agent_id": operator_agent_id,
                "target_agent_id": target_agent_id,
                "operator_fork_id": operator_fork_id,
                "fork_agent_id": fork_agent_id,
                "event_type": event_type,
                "summary": summary,
                "report_id": report_id,
                "command_id": command_id,
            },
            campaign_id,
        )
        return event

    def get_operator_campaign_event(self, event_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT event_id, campaign_id, assignment_id, operator_agent_id,
                       target_agent_id, event_type, summary, detail_json,
                       report_id, command_id, created_at
                FROM operator_campaign_events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        return self._operator_campaign_event_from_row(row) if row else None

    def list_operator_campaign_events(
        self,
        *,
        campaign_id: str | None = None,
        assignment_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if assignment_id:
            where.append("assignment_id = ?")
            params.append(assignment_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT event_id, campaign_id, assignment_id, operator_agent_id,
                   target_agent_id, event_type, summary, detail_json,
                   report_id, command_id, created_at
            FROM operator_campaign_events
            {clause}
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_campaign_event_from_row(row) for row in rows]

    def create_operator_fork(
        self,
        *,
        logical_operator_agent_id: str,
        fork_agent_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
        cwd: str,
        fork_track_id: str = "default",
        fork_purpose: str = "edit",
        access_mode: str = "edit",
        source_cwd: str | None = None,
        work_root: str | None = None,
        fork_codex_session_id: str | None = None,
        campaign_id: str | None = None,
        codex_home: str | None = None,
        codex_host_id: str | None = None,
        tmux_pane_id: str | None = None,
        status: str = "starting",
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self.get_operator_fork_for_source(
            logical_operator_agent_id=logical_operator_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            source_codex_session_id=source_codex_session_id,
            fork_track_id=fork_track_id,
        )
        if existing is not None:
            updated = self.update_operator_fork(
                existing["operator_fork_id"],
                fork_agent_id=fork_agent_id,
                fork_codex_session_id=fork_codex_session_id,
                campaign_id=campaign_id,
                cwd=cwd,
                fork_purpose=fork_purpose,
                access_mode=access_mode,
                source_cwd=source_cwd,
                work_root=work_root,
                codex_home=codex_home,
                codex_host_id=codex_host_id,
                tmux_pane_id=tmux_pane_id,
                status=status,
                summary=summary,
                metadata=metadata,
                touch=True,
            )
            if updated is None:
                raise RuntimeError("operator fork update failed")
            return updated

        operator_fork_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_forks
                    (operator_fork_id, logical_operator_agent_id, fork_agent_id,
                     source_caller_agent_id, source_codex_session_id,
                     fork_track_id, fork_purpose, access_mode, source_cwd,
                     work_root, fork_codex_session_id, campaign_id, cwd, codex_home,
                     codex_host_id, tmux_pane_id, status, summary,
                     metadata_json, created_at, updated_at, last_used_at,
                     completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    operator_fork_id,
                    logical_operator_agent_id,
                    fork_agent_id,
                    source_caller_agent_id,
                    source_codex_session_id,
                    fork_track_id,
                    fork_purpose,
                    access_mode,
                    source_cwd,
                    work_root,
                    fork_codex_session_id,
                    campaign_id,
                    cwd,
                    codex_home,
                    codex_host_id,
                    tmux_pane_id,
                    status,
                    summary,
                    json.dumps(metadata or {}),
                    current,
                    current,
                    current,
                ),
            )
        fork = self.get_operator_fork(operator_fork_id)
        if fork is None:
            raise RuntimeError("operator fork insert failed")
        return fork

    def get_operator_fork(self, operator_fork_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                       source_caller_agent_id, source_codex_session_id,
                       fork_track_id, fork_purpose, access_mode, source_cwd,
                       work_root, fork_codex_session_id, campaign_id, cwd, codex_home,
                       codex_host_id, tmux_pane_id, status, summary,
                       metadata_json, created_at, updated_at, last_used_at,
                       completed_at
                FROM operator_forks
                WHERE operator_fork_id = ?
                """,
                (operator_fork_id,),
            ).fetchone()
        return self._operator_fork_from_row(row) if row else None

    def get_operator_fork_for_source(
        self,
        *,
        logical_operator_agent_id: str,
        source_caller_agent_id: str,
        source_codex_session_id: str,
        fork_track_id: str = "default",
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                       source_caller_agent_id, source_codex_session_id,
                       fork_track_id, fork_purpose, access_mode, source_cwd,
                       work_root, fork_codex_session_id, campaign_id, cwd, codex_home,
                       codex_host_id, tmux_pane_id, status, summary,
                       metadata_json, created_at, updated_at, last_used_at,
                       completed_at
                FROM operator_forks
                WHERE logical_operator_agent_id = ?
                  AND source_caller_agent_id = ?
                  AND source_codex_session_id = ?
                  AND fork_track_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (
                    logical_operator_agent_id,
                    source_caller_agent_id,
                    source_codex_session_id,
                    fork_track_id,
                ),
            ).fetchone()
        return self._operator_fork_from_row(row) if row else None

    def get_operator_fork_for_agent(self, fork_agent_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                       source_caller_agent_id, source_codex_session_id,
                       fork_track_id, fork_purpose, access_mode, source_cwd,
                       work_root, fork_codex_session_id, campaign_id, cwd, codex_home,
                       codex_host_id, tmux_pane_id, status, summary,
                       metadata_json, created_at, updated_at, last_used_at,
                       completed_at
                FROM operator_forks
                WHERE fork_agent_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (fork_agent_id,),
            ).fetchone()
        return self._operator_fork_from_row(row) if row else None

    def list_operator_forks(
        self,
        *,
        logical_operator_agent_id: str | None = None,
        source_caller_agent_id: str | None = None,
        fork_track_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        include_edges: bool = True,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if logical_operator_agent_id:
            where.append("logical_operator_agent_id = ?")
            params.append(logical_operator_agent_id)
        if source_caller_agent_id:
            where.append("source_caller_agent_id = ?")
            params.append(source_caller_agent_id)
        if fork_track_id:
            where.append("fork_track_id = ?")
            params.append(fork_track_id)
        if campaign_id:
            where.append("campaign_id = ?")
            params.append(campaign_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                   source_caller_agent_id, source_codex_session_id,
                   fork_track_id, fork_purpose, access_mode, source_cwd,
                   work_root, fork_codex_session_id, campaign_id, cwd, codex_home,
                   codex_host_id, tmux_pane_id, status, summary,
                   metadata_json, created_at, updated_at, last_used_at,
                   completed_at
            FROM operator_forks
            {clause}
            ORDER BY updated_at DESC, operator_fork_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        forks = [self._operator_fork_from_row(row) for row in rows]
        if include_edges:
            for fork in forks:
                fork["edges"] = self.list_operator_fork_edges(
                    operator_fork_id=fork["operator_fork_id"]
                )
        return forks

    def update_operator_fork(
        self,
        operator_fork_id: str,
        *,
        fork_agent_id: str | None = None,
        fork_codex_session_id: str | None = None,
        campaign_id: str | None = None,
        cwd: str | None = None,
        fork_purpose: str | None = None,
        access_mode: str | None = None,
        source_cwd: str | None = None,
        work_root: str | None = None,
        codex_home: str | None = None,
        codex_host_id: str | None = None,
        tmux_pane_id: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        touch: bool = False,
        complete: bool | None = None,
    ) -> dict[str, Any] | None:
        fork = self.get_operator_fork(operator_fork_id)
        if fork is None:
            return None
        current = now_ts()
        completed_at = (
            current
            if complete is True
            else None
            if complete is False
            else fork.get("completed_at")
        )
        merged_metadata = dict(fork.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_forks
                SET fork_agent_id = COALESCE(?, fork_agent_id),
                    fork_codex_session_id = COALESCE(?, fork_codex_session_id),
                    campaign_id = COALESCE(?, campaign_id),
                    cwd = COALESCE(?, cwd),
                    fork_purpose = COALESCE(?, fork_purpose),
                    access_mode = COALESCE(?, access_mode),
                    source_cwd = COALESCE(?, source_cwd),
                    work_root = COALESCE(?, work_root),
                    codex_home = COALESCE(?, codex_home),
                    codex_host_id = COALESCE(?, codex_host_id),
                    tmux_pane_id = COALESCE(?, tmux_pane_id),
                    status = COALESCE(?, status),
                    summary = COALESCE(?, summary),
                    metadata_json = ?,
                    updated_at = ?,
                    last_used_at = CASE WHEN ? THEN ? ELSE last_used_at END,
                    completed_at = ?
                WHERE operator_fork_id = ?
                """,
                (
                    fork_agent_id,
                    fork_codex_session_id,
                    campaign_id,
                    cwd,
                    fork_purpose,
                    access_mode,
                    source_cwd,
                    work_root,
                    codex_home,
                    codex_host_id,
                    tmux_pane_id,
                    status,
                    summary,
                    json.dumps(merged_metadata),
                    current,
                    int(touch),
                    current,
                    completed_at,
                    operator_fork_id,
                ),
            )
        return self.get_operator_fork(operator_fork_id) if cursor.rowcount else None

    def update_operator_fork_source_session(
        self,
        operator_fork_id: str,
        *,
        old_source_codex_session_id: str,
        new_source_codex_session_id: str,
        source_cwd: str | None = None,
        codex_host_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        touch: bool = True,
    ) -> dict[str, Any] | None:
        fork = self.get_operator_fork(operator_fork_id)
        if fork is None:
            return None
        if fork["source_codex_session_id"] != old_source_codex_session_id:
            return None
        current = now_ts()
        merged_metadata = dict(fork.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        merged_metadata["source_codex_session_id"] = new_source_codex_session_id
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_forks
                SET source_codex_session_id = ?,
                    source_cwd = COALESCE(?, source_cwd),
                    codex_host_id = COALESCE(?, codex_host_id),
                    metadata_json = ?,
                    updated_at = ?,
                    last_used_at = CASE WHEN ? THEN ? ELSE last_used_at END
                WHERE operator_fork_id = ?
                  AND source_codex_session_id = ?
                """,
                (
                    new_source_codex_session_id,
                    source_cwd,
                    codex_host_id,
                    json.dumps(merged_metadata),
                    current,
                    int(touch),
                    current,
                    operator_fork_id,
                    old_source_codex_session_id,
                ),
            )
        return self.get_operator_fork(operator_fork_id) if cursor.rowcount else None

    def create_operator_project_spawn_request(
        self,
        *,
        logical_operator_agent_id: str,
        operator_agent_id: str,
        review_fork_id: str,
        review_fork_agent_id: str,
        source_caller_agent_id: str,
        source_cwd: str,
        target_parent: str,
        target_slug: str,
        target_path: str,
        project_name: str,
        mode: str,
        instructions: str,
        campaign_id: str | None = None,
        assignment_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        spawn_request_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_project_spawn_requests
                    (spawn_request_id, logical_operator_agent_id, operator_agent_id,
                     review_fork_id, review_fork_agent_id, source_caller_agent_id,
                     source_cwd, target_parent, target_slug, target_path,
                     project_name, mode, instructions, status, launched_agent_id,
                     tmux_pane_id, error, campaign_id, assignment_id, metadata_json,
                     created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending',
                        NULL, NULL, NULL, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    spawn_request_id,
                    logical_operator_agent_id,
                    operator_agent_id,
                    review_fork_id,
                    review_fork_agent_id,
                    source_caller_agent_id,
                    source_cwd,
                    target_parent,
                    target_slug,
                    target_path,
                    project_name,
                    mode,
                    instructions,
                    campaign_id,
                    assignment_id,
                    json.dumps(metadata or {}),
                    current,
                    current,
                ),
            )
        request = self.get_operator_project_spawn_request(spawn_request_id)
        if request is None:
            raise RuntimeError("operator project spawn request insert failed")
        return request

    def get_operator_project_spawn_request(
        self,
        spawn_request_id: str,
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                {self._operator_project_spawn_select_sql()}
                WHERE spawn_request_id = ?
                """,
                (spawn_request_id,),
            ).fetchone()
        return self._operator_project_spawn_from_row(row) if row else None

    def list_operator_project_spawn_requests(
        self,
        *,
        logical_operator_agent_id: str | None = None,
        review_fork_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if logical_operator_agent_id:
            where.append("logical_operator_agent_id = ?")
            params.append(logical_operator_agent_id)
        if review_fork_id:
            where.append("review_fork_id = ?")
            params.append(review_fork_id)
        if status:
            where.append("status = ?")
            params.append(status)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            {self._operator_project_spawn_select_sql()}
            {clause}
            ORDER BY
                CASE WHEN status = 'pending' THEN 0 ELSE 1 END ASC,
                created_at ASC,
                updated_at DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_project_spawn_from_row(row) for row in rows]

    def update_operator_project_spawn_request(
        self,
        spawn_request_id: str,
        *,
        status: str,
        launched_agent_id: str | None = None,
        tmux_pane_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        request = self.get_operator_project_spawn_request(spawn_request_id)
        if request is None:
            return None
        current = now_ts()
        normalized_status = str(status or "").strip().lower()
        completed_at = (
            current
            if normalized_status in PROJECT_SPAWN_TERMINAL_STATUSES
            else None
        )
        merged_metadata = dict(request.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_project_spawn_requests
                SET status = ?,
                    launched_agent_id = COALESCE(?, launched_agent_id),
                    tmux_pane_id = COALESCE(?, tmux_pane_id),
                    error = COALESCE(?, error),
                    metadata_json = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE spawn_request_id = ?
                """,
                (
                    normalized_status,
                    launched_agent_id,
                    tmux_pane_id,
                    error,
                    json.dumps(merged_metadata),
                    current,
                    completed_at,
                    spawn_request_id,
                ),
            )
        return (
            self.get_operator_project_spawn_request(spawn_request_id)
            if cursor.rowcount
            else None
        )

    def create_operator_fork_edge(
        self,
        *,
        from_fork_id: str,
        to_fork_id: str,
        edge_type: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(edge_type or "").strip().lower()
        if normalized_type not in {"related", "depends_on", "blocks"}:
            raise ValueError("edge_type must be related, depends_on, or blocks")
        if from_fork_id == to_fork_id:
            raise ValueError("operator fork edge cannot point to itself")
        current = now_ts()
        with self.connect() as conn:
            if self._operator_fork_edge_path_exists(conn, to_fork_id, from_fork_id):
                raise ValueError("operator fork edge would create a cycle")
            existing = conn.execute(
                """
                SELECT edge_id
                FROM operator_fork_edges
                WHERE from_fork_id = ? AND to_fork_id = ? AND edge_type = ?
                """,
                (from_fork_id, to_fork_id, normalized_type),
            ).fetchone()
            if existing is not None:
                edge_id = str(existing["edge_id"])
                conn.execute(
                    """
                    UPDATE operator_fork_edges
                    SET summary = COALESCE(?, summary),
                        metadata_json = ?
                    WHERE edge_id = ?
                    """,
                    (summary, json.dumps(metadata or {}), edge_id),
                )
            else:
                edge_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO operator_fork_edges
                        (edge_id, from_fork_id, to_fork_id, edge_type,
                         summary, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        edge_id,
                        from_fork_id,
                        to_fork_id,
                        normalized_type,
                        summary,
                        json.dumps(metadata or {}),
                        current,
                    ),
                )
        edge = self.get_operator_fork_edge(edge_id)
        if edge is None:
            raise RuntimeError("operator fork edge insert failed")
        return edge

    @staticmethod
    def _operator_fork_edge_path_exists(
        conn: sqlite3.Connection,
        start_fork_id: str,
        target_fork_id: str,
    ) -> bool:
        pending = [start_fork_id]
        seen: set[str] = set()
        while pending:
            fork_id = pending.pop()
            if fork_id == target_fork_id:
                return True
            if fork_id in seen:
                continue
            seen.add(fork_id)
            rows = conn.execute(
                """
                SELECT to_fork_id
                FROM operator_fork_edges
                WHERE from_fork_id = ?
                """,
                (fork_id,),
            ).fetchall()
            pending.extend(str(row["to_fork_id"]) for row in rows)
        return False

    def get_operator_fork_edge(self, edge_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT edge_id, from_fork_id, to_fork_id, edge_type,
                       summary, metadata_json, created_at
                FROM operator_fork_edges
                WHERE edge_id = ?
                """,
                (edge_id,),
            ).fetchone()
        return self._operator_fork_edge_from_row(row) if row else None

    def list_operator_fork_edges(
        self,
        *,
        operator_fork_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where = ""
        params: list[Any] = []
        if operator_fork_id:
            where = "WHERE from_fork_id = ? OR to_fork_id = ?"
            params.extend([operator_fork_id, operator_fork_id])
        query = f"""
            SELECT edge_id, from_fork_id, to_fork_id, edge_type,
                   summary, metadata_json, created_at
            FROM operator_fork_edges
            {where}
            ORDER BY created_at DESC, edge_id DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_fork_edge_from_row(row) for row in rows]

    def create_operator_knowledge_link(
        self,
        *,
        logical_operator_agent_id: str,
        operator_agent_id: str,
        source_agent_id: str,
        target_agent_id: str,
        link_type: str,
        status: str = "active",
        source_operator_fork_id: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(link_type or "").strip().lower()
        if normalized_type not in {"handoff", "consult", "domain_context", "review_context"}:
            raise ValueError("link_type must be handoff, consult, domain_context, or review_context")
        normalized_status = str(status or "active").strip().lower()
        if normalized_status not in OPERATOR_KNOWLEDGE_LINK_STATUSES:
            raise ValueError("status must be proposed, active, closed, canceled, or cancelled")
        current = now_ts()
        closed_at = (
            current
            if normalized_status in OPERATOR_KNOWLEDGE_LINK_TERMINAL_STATUSES
            else None
        )
        link_id = str(uuid.uuid4())
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_knowledge_links
                    (link_id, logical_operator_agent_id, operator_agent_id,
                     source_agent_id, target_agent_id, source_operator_fork_id,
                     link_type, status, summary, metadata_json, created_at,
                     updated_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    link_id,
                    logical_operator_agent_id,
                    operator_agent_id,
                    source_agent_id,
                    target_agent_id,
                    source_operator_fork_id,
                    normalized_type,
                    normalized_status,
                    summary,
                    json.dumps(metadata or {}),
                    current,
                    current,
                    closed_at,
                ),
            )
        link = self.get_operator_knowledge_link(link_id)
        if link is None:
            raise RuntimeError("operator knowledge link insert failed")
        return link

    def get_operator_knowledge_link(self, link_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                {self._operator_knowledge_link_select_sql()}
                WHERE l.link_id = ?
                """,
                (link_id,),
            ).fetchone()
        return self._operator_knowledge_link_from_row(row) if row else None

    def list_operator_knowledge_links(
        self,
        *,
        logical_operator_agent_id: str | None = None,
        source_agent_id: str | None = None,
        target_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if logical_operator_agent_id:
            where.append("l.logical_operator_agent_id = ?")
            params.append(logical_operator_agent_id)
        if source_agent_id:
            where.append("l.source_agent_id = ?")
            params.append(source_agent_id)
        if target_agent_id:
            where.append("l.target_agent_id = ?")
            params.append(target_agent_id)
        if status:
            where.append("l.status = ?")
            params.append(str(status).strip().lower())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            {self._operator_knowledge_link_select_sql()}
            {clause}
            ORDER BY
                CASE WHEN l.status = 'proposed' THEN 0
                     WHEN l.status = 'active' THEN 1
                     ELSE 2 END ASC,
                l.updated_at DESC,
                l.created_at DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_knowledge_link_from_row(row) for row in rows]

    def update_operator_knowledge_link(
        self,
        link_id: str,
        *,
        status: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        link = self.get_operator_knowledge_link(link_id)
        if link is None:
            return None
        normalized_status = (
            str(status or link.get("status") or "active").strip().lower()
        )
        if normalized_status not in OPERATOR_KNOWLEDGE_LINK_STATUSES:
            raise ValueError("status must be proposed, active, closed, canceled, or cancelled")
        current = now_ts()
        closed_at = link.get("closed_at")
        if normalized_status in OPERATOR_KNOWLEDGE_LINK_TERMINAL_STATUSES:
            closed_at = closed_at or current
        elif status is not None:
            closed_at = None
        merged_metadata = dict(link.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_knowledge_links
                SET status = ?,
                    summary = COALESCE(?, summary),
                    metadata_json = ?,
                    updated_at = ?,
                    closed_at = ?
                WHERE link_id = ?
                """,
                (
                    normalized_status,
                    summary,
                    json.dumps(merged_metadata),
                    current,
                    closed_at,
                    link_id,
                ),
            )
        return self.get_operator_knowledge_link(link_id) if cursor.rowcount else None

    def create_operator_knowledge_turn(
        self,
        *,
        link_id: str,
        sender_agent_id: str,
        recipient_agent_id: str,
        turn_type: str,
        message: str,
        delivery_status: str = "recorded",
        command_id: str | None = None,
        tmux_pane_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = str(turn_type or "note").strip().lower()
        if normalized_type not in {"handoff", "question", "answer", "note"}:
            raise ValueError("turn_type must be handoff, question, answer, or note")
        normalized_delivery = str(delivery_status or "recorded").strip().lower()
        if normalized_delivery not in OPERATOR_KNOWLEDGE_TURN_DELIVERY_STATUSES:
            raise ValueError("delivery_status must be pending_approval, recorded, queued, sent, or failed")
        turn_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_knowledge_turns
                    (turn_id, link_id, sender_agent_id, recipient_agent_id,
                     turn_type, message, delivery_status, command_id,
                     tmux_pane_id, error, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    link_id,
                    sender_agent_id,
                    recipient_agent_id,
                    normalized_type,
                    message,
                    normalized_delivery,
                    command_id,
                    tmux_pane_id,
                    error,
                    json.dumps(metadata or {}),
                    current,
                ),
            )
            conn.execute(
                """
                UPDATE operator_knowledge_links
                SET updated_at = ?
                WHERE link_id = ?
                """,
                (current, link_id),
            )
        turn = self.get_operator_knowledge_turn(turn_id)
        if turn is None:
            raise RuntimeError("operator knowledge turn insert failed")
        return turn

    def get_operator_knowledge_turn(self, turn_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT turn_id, link_id, sender_agent_id, recipient_agent_id,
                       turn_type, message, delivery_status, command_id,
                       tmux_pane_id, error, metadata_json, created_at
                FROM operator_knowledge_turns
                WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
        return self._operator_knowledge_turn_from_row(row) if row else None

    def list_operator_knowledge_turns(
        self,
        *,
        link_id: str,
        delivery_status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where = ["link_id = ?"]
        params: list[Any] = [link_id]
        if delivery_status:
            where.append("delivery_status = ?")
            params.append(str(delivery_status).strip().lower())
        query = f"""
            SELECT turn_id, link_id, sender_agent_id, recipient_agent_id,
                   turn_type, message, delivery_status, command_id,
                   tmux_pane_id, error, metadata_json, created_at
            FROM operator_knowledge_turns
            WHERE {' AND '.join(where)}
            ORDER BY created_at ASC, turn_id ASC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_knowledge_turn_from_row(row) for row in rows]

    def update_operator_knowledge_turn_delivery(
        self,
        turn_id: str,
        *,
        delivery_status: str,
        command_id: str | None = None,
        tmux_pane_id: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        turn = self.get_operator_knowledge_turn(turn_id)
        if turn is None:
            return None
        normalized_delivery = str(delivery_status or "").strip().lower()
        if normalized_delivery not in OPERATOR_KNOWLEDGE_TURN_DELIVERY_STATUSES:
            raise ValueError("delivery_status must be pending_approval, recorded, queued, sent, or failed")
        merged_metadata = dict(turn.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_knowledge_turns
                SET delivery_status = ?,
                    command_id = COALESCE(?, command_id),
                    tmux_pane_id = COALESCE(?, tmux_pane_id),
                    error = COALESCE(?, error),
                    metadata_json = ?
                WHERE turn_id = ?
                """,
                (
                    normalized_delivery,
                    command_id,
                    tmux_pane_id,
                    error,
                    json.dumps(merged_metadata),
                    turn_id,
                ),
            )
            conn.execute(
                """
                UPDATE operator_knowledge_links
                SET updated_at = ?
                WHERE link_id = ?
                """,
                (current, turn["link_id"]),
            )
        return self.get_operator_knowledge_turn(turn_id) if cursor.rowcount else None

    def create_operator_kb_entry(
        self,
        *,
        scope: str,
        title: str,
        summary: str,
        body: str,
        created_by_operator_agent_id: str,
        created_by_agent_id: str,
        project: str | None = None,
        repo_root: str | None = None,
        git_remote: str | None = None,
        branch: str | None = None,
        tags: list[str] | None = None,
        status: str = "proposed",
        redaction_status: str = "unreviewed",
        source_knowledge_link_id: str | None = None,
        source_handoff_id: str | None = None,
        source_turn_ids: list[str] | None = None,
        stale_after: float | None = None,
        expires_at: float | None = None,
        imported_at: float | None = None,
        metadata: dict[str, Any] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        normalized_scope = self._normalize_operator_kb_scope(scope)
        normalized_status = self._normalize_operator_kb_status(status)
        normalized_redaction = self._normalize_operator_kb_redaction_status(
            redaction_status
        )
        normalized_title = str(title or "").strip()
        normalized_summary = str(summary or "").strip()
        normalized_body = str(body or "").strip()
        if not normalized_title:
            raise ValueError("KB title is required")
        if not normalized_summary:
            raise ValueError("KB summary is required")
        if not normalized_body:
            raise ValueError("KB body is required")
        kb_id = str(uuid.uuid4())
        current = now_ts()
        promoted_at = current if normalized_status == "active" else None
        retired_at = current if normalized_status in OPERATOR_KB_TERMINAL_STATUSES else None
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_kb_entries
                    (kb_id, scope, project, repo_root, git_remote, branch,
                     title, summary, body, tags_json, status, redaction_status,
                     created_by_operator_agent_id, created_by_agent_id,
                     source_knowledge_link_id, source_handoff_id,
                     source_turn_ids_json, stale_after, expires_at, metadata_json,
                     created_at, updated_at, promoted_at, retired_at, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    kb_id,
                    normalized_scope,
                    self._none_if_blank(project),
                    self._none_if_blank(repo_root),
                    self._none_if_blank(git_remote),
                    self._none_if_blank(branch),
                    normalized_title,
                    normalized_summary,
                    normalized_body,
                    json.dumps(self._normalize_tags(tags or [])),
                    normalized_status,
                    normalized_redaction,
                    created_by_operator_agent_id,
                    created_by_agent_id,
                    self._none_if_blank(source_knowledge_link_id),
                    self._none_if_blank(source_handoff_id),
                    json.dumps(self._normalize_id_list(source_turn_ids or [])),
                    stale_after,
                    expires_at,
                    json.dumps(metadata or {}),
                    current,
                    current,
                    promoted_at,
                    retired_at,
                    imported_at,
                ),
            )
            for source in self._operator_kb_sources_payload(
                kb_id=kb_id,
                source_knowledge_link_id=source_knowledge_link_id,
                source_handoff_id=source_handoff_id,
                source_turn_ids=source_turn_ids or [],
                sources=sources or [],
            ):
                conn.execute(
                    """
                    INSERT INTO operator_kb_sources
                        (source_row_id, kb_id, source_type, source_id,
                         metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source["source_row_id"],
                        kb_id,
                        source["source_type"],
                        source["source_id"],
                        json.dumps(source.get("metadata") or {}),
                        current,
                    ),
                )
            conn.execute(
                """
                INSERT INTO operator_kb_events
                    (kb_id, event_type, operator_agent_id, summary,
                     detail_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    kb_id,
                    "operator_kb_imported" if imported_at is not None else "operator_kb_created",
                    created_by_operator_agent_id,
                    normalized_summary,
                    json.dumps({"status": normalized_status}),
                    current,
                ),
            )
        entry = self.get_operator_kb_entry(kb_id)
        if entry is None:
            raise RuntimeError("operator KB entry insert failed")
        return entry

    def get_operator_kb_entry(self, kb_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                {self._operator_kb_entry_select_sql()}
                WHERE kb_id = ?
                """,
                (kb_id,),
            ).fetchone()
            if row is None:
                return None
            entry = self._operator_kb_entry_from_row(row)
            entry["sources"] = self._operator_kb_sources_for_entry(conn, kb_id)
        return entry

    def search_operator_kb_entries(
        self,
        *,
        query: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        repo_root: str | None = None,
        status: str | None = "active",
        tags: list[str] | None = None,
        include_expired: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if scope:
            where.append("scope = ?")
            params.append(self._normalize_operator_kb_scope(scope))
        if project:
            where.append("project = ?")
            params.append(str(project).strip())
        if repo_root:
            where.append("repo_root = ?")
            params.append(str(repo_root).strip())
        if status:
            where.append("status = ?")
            params.append(self._normalize_operator_kb_status(status))
        if not include_expired:
            where.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(now_ts())
        terms = [
            term.strip().lower()
            for term in str(query or "").split()
            if term.strip()
        ]
        for term in terms:
            where.append(
                """
                (
                    lower(title) LIKE ?
                    OR lower(summary) LIKE ?
                    OR lower(body) LIKE ?
                    OR lower(tags_json) LIKE ?
                    OR lower(COALESCE(project, '')) LIKE ?
                )
                """
            )
            like = f"%{term}%"
            params.extend([like, like, like, like, like])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query_sql = f"""
            {self._operator_kb_entry_select_sql()}
            {clause}
            ORDER BY
                CASE WHEN status = 'active' THEN 0
                     WHEN status = 'proposed' THEN 1
                     ELSE 2 END ASC,
                updated_at DESC,
                created_at DESC
            LIMIT ?
        """
        params.append(safe_limit)
        normalized_tags = set(self._normalize_tags(tags or []))
        with self.connect() as conn:
            rows = conn.execute(query_sql, tuple(params)).fetchall()
            entries = [self._operator_kb_entry_from_row(row) for row in rows]
            if normalized_tags:
                entries = [
                    entry
                    for entry in entries
                    if normalized_tags.issubset(set(entry.get("tags") or []))
                ]
            for entry in entries:
                entry["sources"] = self._operator_kb_sources_for_entry(
                    conn,
                    str(entry["kb_id"]),
                )
        return entries

    def update_operator_kb_entry(
        self,
        kb_id: str,
        *,
        updates: dict[str, Any] | None = None,
        status: str | None = None,
        redaction_status: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        event_type: str = "operator_kb_updated",
        operator_agent_id: str | None = None,
        event_summary: str | None = None,
    ) -> dict[str, Any] | None:
        entry = self.get_operator_kb_entry(kb_id)
        if entry is None:
            return None
        requested_updates = dict(updates or {})
        if summary is not None:
            requested_updates["summary"] = summary
        normalized_status = (
            self._normalize_operator_kb_status(status)
            if status is not None
            else str(entry.get("status") or "proposed")
        )
        normalized_redaction = (
            self._normalize_operator_kb_redaction_status(redaction_status)
            if redaction_status is not None
            else str(entry.get("redaction_status") or "unreviewed")
        )
        current = now_ts()
        promoted_at = entry.get("promoted_at")
        retired_at = entry.get("retired_at")
        if normalized_status == "active":
            promoted_at = promoted_at or current
            retired_at = None
        elif normalized_status in OPERATOR_KB_TERMINAL_STATUSES:
            retired_at = retired_at or current
        elif status is not None:
            retired_at = None
        merged_metadata = dict(entry.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        column_updates: dict[str, Any] = {}
        for key in (
            "scope",
            "project",
            "repo_root",
            "git_remote",
            "branch",
            "title",
            "body",
            "stale_after",
            "expires_at",
        ):
            if key not in requested_updates:
                continue
            value = requested_updates[key]
            if key == "scope":
                column_updates[key] = self._normalize_operator_kb_scope(
                    str(value or "project")
                )
            elif key in {"project", "repo_root", "git_remote", "branch"}:
                column_updates[key] = self._none_if_blank(value)
            elif key in {"title", "body"}:
                text = str(value or "").strip()
                if not text:
                    raise ValueError(f"KB {key} is required")
                column_updates[key] = text
            else:
                column_updates[key] = value
        if "summary" in requested_updates:
            summary_text = str(requested_updates.get("summary") or "").strip()
            if not summary_text:
                raise ValueError("KB summary is required")
            column_updates["summary"] = summary_text
        if "tags" in requested_updates:
            raw_tags = requested_updates.get("tags")
            column_updates["tags_json"] = json.dumps(
                self._normalize_tags(raw_tags if isinstance(raw_tags, list) else [])
            )
        with self.connect() as conn:
            assignments = [
                "status = ?",
                "redaction_status = ?",
                "metadata_json = ?",
                "updated_at = ?",
                "promoted_at = ?",
                "retired_at = ?",
            ]
            params: list[Any] = [
                normalized_status,
                normalized_redaction,
                json.dumps(merged_metadata),
                current,
                promoted_at,
                retired_at,
            ]
            for key, value in column_updates.items():
                assignments.append(f"{key} = ?")
                params.append(value)
            params.append(kb_id)
            cursor = conn.execute(
                f"""
                UPDATE operator_kb_entries
                SET {', '.join(assignments)}
                WHERE kb_id = ?
                """,
                tuple(params),
            )
            if cursor.rowcount:
                conn.execute(
                    """
                    INSERT INTO operator_kb_events
                        (kb_id, event_type, operator_agent_id, summary,
                         detail_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        kb_id,
                        event_type,
                        operator_agent_id,
                        event_summary
                        or str(column_updates.get("summary") or "")
                        or event_type,
                        json.dumps(
                            {
                                "status": normalized_status,
                                "redaction_status": normalized_redaction,
                                "updated_fields": sorted(column_updates),
                            }
                        ),
                        current,
                    ),
                )
        return self.get_operator_kb_entry(kb_id) if cursor.rowcount else None

    def export_operator_kb_entries(
        self,
        *,
        query: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        repo_root: str | None = None,
        status: str | None = "active",
        tags: list[str] | None = None,
        include_expired: bool = False,
        limit: int = 500,
    ) -> dict[str, Any]:
        entries = self.search_operator_kb_entries(
            query=query,
            scope=scope,
            project=project,
            repo_root=repo_root,
            status=status,
            tags=tags,
            include_expired=include_expired,
            limit=limit,
        )
        return {
            "format": OPERATOR_KB_EXPORT_FORMAT,
            "version": 1,
            "exported_at": now_ts(),
            "criteria": {
                "query": query,
                "scope": scope,
                "project": project,
                "repo_root": repo_root,
                "status": status,
                "tags": tags or [],
                "include_expired": include_expired,
                "limit": limit,
            },
            "entries": entries,
        }

    def import_operator_kb_entries(
        self,
        *,
        bundle: dict[str, Any],
        operator_agent_id: str,
        import_status: str = "proposed",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if str(bundle.get("format") or "") != OPERATOR_KB_EXPORT_FORMAT:
            raise ValueError("unsupported KB export format")
        normalized_status = self._normalize_operator_kb_status(import_status)
        entries = bundle.get("entries")
        if not isinstance(entries, list):
            raise ValueError("KB import bundle entries must be a list")
        imported: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        current = now_ts()
        for index, item in enumerate(entries):
            if not isinstance(item, dict):
                skipped.append({"index": index, "reason": "entry is not an object"})
                continue
            try:
                entry = self.create_operator_kb_entry(
                    scope=str(item.get("scope") or "project"),
                    project=item.get("project"),
                    repo_root=item.get("repo_root"),
                    git_remote=item.get("git_remote"),
                    branch=item.get("branch"),
                    title=str(item.get("title") or ""),
                    summary=str(item.get("summary") or ""),
                    body=str(item.get("body") or ""),
                    tags=item.get("tags") if isinstance(item.get("tags"), list) else [],
                    status=normalized_status,
                    redaction_status=(
                        str(item.get("redaction_status") or "unreviewed")
                        if normalized_status != "active"
                        else "clean"
                    ),
                    created_by_operator_agent_id=operator_agent_id,
                    created_by_agent_id=operator_agent_id,
                    source_knowledge_link_id=item.get("source_knowledge_link_id"),
                    source_handoff_id=item.get("source_handoff_id"),
                    source_turn_ids=(
                        item.get("source_turn_ids")
                        if isinstance(item.get("source_turn_ids"), list)
                        else []
                    ),
                    stale_after=item.get("stale_after"),
                    expires_at=item.get("expires_at"),
                    imported_at=current,
                    metadata={
                        **(
                            item.get("metadata")
                            if isinstance(item.get("metadata"), dict)
                            else {}
                        ),
                        **(metadata or {}),
                        "imported_from_format": OPERATOR_KB_EXPORT_FORMAT,
                    },
                    sources=(
                        item.get("sources")
                        if isinstance(item.get("sources"), list)
                        else []
                    ),
                )
            except Exception as exc:
                skipped.append({"index": index, "reason": str(exc)})
                continue
            imported.append(entry)
        return {
            "imported_count": len(imported),
            "skipped_count": len(skipped),
            "entries": imported,
            "skipped": skipped,
        }

    def create_operator_kb_seed_run(
        self,
        *,
        seed_run_id: str | None = None,
        logical_operator_agent_id: str,
        source_operator_agent_id: str,
        seed_type: str,
        scope: str,
        prompt: str,
        project: str | None = None,
        repo_root: str | None = None,
        git_remote: str | None = None,
        branch: str | None = None,
        status: str = "requested",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_seed_type = str(seed_type or "operator_self_seed").strip().lower()
        normalized_prompt = str(prompt or "").strip()
        if not normalized_seed_type:
            raise ValueError("KB seed type is required")
        if not normalized_prompt:
            raise ValueError("KB seed prompt is required")
        normalized_status = self._normalize_operator_kb_seed_run_status(status)
        resolved_seed_run_id = str(seed_run_id or uuid.uuid4()).strip()
        if not resolved_seed_run_id:
            raise ValueError("KB seed run id is required")
        current = now_ts()
        completed_at = (
            current
            if normalized_status in OPERATOR_KB_SEED_TERMINAL_STATUSES
            else None
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_kb_seed_runs
                    (seed_run_id, logical_operator_agent_id,
                     source_operator_agent_id, seed_type, scope, project,
                     repo_root, git_remote, branch, prompt, status,
                     metadata_json, created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    resolved_seed_run_id,
                    logical_operator_agent_id,
                    source_operator_agent_id,
                    normalized_seed_type,
                    self._normalize_operator_kb_scope(scope),
                    self._none_if_blank(project),
                    self._none_if_blank(repo_root),
                    self._none_if_blank(git_remote),
                    self._none_if_blank(branch),
                    normalized_prompt,
                    normalized_status,
                    json.dumps(metadata or {}),
                    current,
                    current,
                    completed_at,
                ),
            )
        run = self.get_operator_kb_seed_run(resolved_seed_run_id)
        if run is None:
            raise RuntimeError("operator KB seed run insert failed")
        return run

    def get_operator_kb_seed_run(self, seed_run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                {self._operator_kb_seed_run_select_sql()}
                WHERE seed_run_id = ?
                """,
                (seed_run_id,),
            ).fetchone()
        return self._operator_kb_seed_run_from_row(row) if row else None

    def list_operator_kb_seed_runs(
        self,
        *,
        logical_operator_agent_id: str | None = None,
        source_operator_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(int(limit), 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if logical_operator_agent_id:
            where.append("logical_operator_agent_id = ?")
            params.append(str(logical_operator_agent_id).strip())
        if source_operator_agent_id:
            where.append("source_operator_agent_id = ?")
            params.append(str(source_operator_agent_id).strip())
        if status:
            where.append("status = ?")
            params.append(self._normalize_operator_kb_seed_run_status(status))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                {self._operator_kb_seed_run_select_sql()}
                {clause}
                ORDER BY updated_at DESC, created_at DESC, seed_run_id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._operator_kb_seed_run_from_row(row) for row in rows]

    def update_operator_kb_seed_run(
        self,
        seed_run_id: str,
        *,
        status: str | None = None,
        command_id: str | None = None,
        tmux_pane_id: str | None = None,
        delivery_status: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        existing = self.get_operator_kb_seed_run(seed_run_id)
        if existing is None:
            return None
        normalized_status = (
            self._normalize_operator_kb_seed_run_status(status)
            if status is not None
            else str(existing.get("status") or "requested")
        )
        current = now_ts()
        completed_at = existing.get("completed_at")
        if (
            completed_at is None
            and normalized_status in OPERATOR_KB_SEED_TERMINAL_STATUSES
        ):
            completed_at = current
        merged_metadata = dict(existing.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_kb_seed_runs
                SET status = ?,
                    command_id = COALESCE(?, command_id),
                    tmux_pane_id = COALESCE(?, tmux_pane_id),
                    delivery_status = COALESCE(?, delivery_status),
                    error = ?,
                    metadata_json = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE seed_run_id = ?
                """,
                (
                    normalized_status,
                    self._none_if_blank(command_id),
                    self._none_if_blank(tmux_pane_id),
                    self._none_if_blank(delivery_status),
                    self._none_if_blank(error),
                    json.dumps(merged_metadata),
                    current,
                    completed_at,
                    seed_run_id,
                ),
            )
        return self.get_operator_kb_seed_run(seed_run_id) if cursor.rowcount else None

    def create_operator_handoff(
        self,
        *,
        logical_operator_agent_id: str,
        source_operator_agent_id: str,
        target_operator_agent_id: str,
        source_agent_id: str,
        objective: str,
        message: str,
        source_operator_fork_id: str | None = None,
        target_caller_agent_id: str | None = None,
        target_operator_fork_id: str | None = None,
        knowledge_link_id: str | None = None,
        knowledge_turn_id: str | None = None,
        allowed_mutation_scope: str | None = None,
        required_artifacts: list[Any] | None = None,
        artifact_bundle: list[Any] | None = None,
        status: str = "proposed",
        needs_ack: bool = True,
        expires_at: float | None = None,
        command_id: str | None = None,
        tmux_pane_id: str | None = None,
        delivery_status: str | None = None,
        delivery_evidence: dict[str, Any] | None = None,
        error: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_status = str(status or "proposed").strip().lower()
        if normalized_status not in OPERATOR_HANDOFF_STATUSES:
            raise ValueError("handoff status is invalid")
        handoff_id = str(uuid.uuid4())
        current = now_ts()
        acknowledged_at = current if normalized_status in {"acknowledged", "running"} else None
        started_at = current if normalized_status == "running" else None
        completed_at = (
            current
            if normalized_status in OPERATOR_HANDOFF_TERMINAL_STATUSES
            else None
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO operator_handoffs
                    (handoff_id, logical_operator_agent_id,
                     source_operator_agent_id, target_operator_agent_id,
                     source_agent_id, source_operator_fork_id,
                     target_caller_agent_id, target_operator_fork_id,
                     knowledge_link_id, knowledge_turn_id, objective, message,
                     allowed_mutation_scope, required_artifacts_json,
                     artifact_bundle_json, status, needs_ack, expires_at,
                     acknowledged_at, started_at, completed_at, command_id,
                     tmux_pane_id, delivery_status, delivery_evidence_json,
                     error, summary, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    logical_operator_agent_id,
                    source_operator_agent_id,
                    target_operator_agent_id,
                    source_agent_id,
                    source_operator_fork_id,
                    target_caller_agent_id,
                    target_operator_fork_id,
                    knowledge_link_id,
                    knowledge_turn_id,
                    objective,
                    message,
                    allowed_mutation_scope,
                    json.dumps(required_artifacts or []),
                    json.dumps(artifact_bundle or []),
                    normalized_status,
                    1 if needs_ack else 0,
                    expires_at,
                    acknowledged_at,
                    started_at,
                    completed_at,
                    command_id,
                    tmux_pane_id,
                    delivery_status,
                    json.dumps(delivery_evidence or {}),
                    error,
                    summary,
                    json.dumps(metadata or {}),
                    current,
                    current,
                ),
            )
        handoff = self.get_operator_handoff(handoff_id)
        if handoff is None:
            raise RuntimeError("operator handoff insert failed")
        return handoff

    def get_operator_handoff(self, handoff_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                f"""
                {self._operator_handoff_select_sql()}
                WHERE h.handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
        return self._operator_handoff_from_row(row) if row else None

    def list_operator_handoffs(
        self,
        *,
        logical_operator_agent_id: str | None = None,
        source_agent_id: str | None = None,
        target_operator_agent_id: str | None = None,
        target_caller_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 500)
        where: list[str] = []
        params: list[Any] = []
        if logical_operator_agent_id:
            where.append("h.logical_operator_agent_id = ?")
            params.append(logical_operator_agent_id)
        if source_agent_id:
            where.append("h.source_agent_id = ?")
            params.append(source_agent_id)
        if target_operator_agent_id:
            where.append("h.target_operator_agent_id = ?")
            params.append(target_operator_agent_id)
        if target_caller_agent_id:
            where.append("h.target_caller_agent_id = ?")
            params.append(target_caller_agent_id)
        if status:
            where.append("h.status = ?")
            params.append(str(status).strip().lower())
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        query = f"""
            {self._operator_handoff_select_sql()}
            {clause}
            ORDER BY
                CASE
                    WHEN h.status = 'proposed' THEN 0
                    WHEN h.status = 'approved' THEN 1
                    WHEN h.status = 'pending_launch' THEN 2
                    WHEN h.status = 'sent' THEN 3
                    WHEN h.status = 'acknowledged' THEN 4
                    WHEN h.status = 'running' THEN 5
                    ELSE 6
                END ASC,
                h.updated_at DESC,
                h.created_at DESC
            LIMIT ?
        """
        params.append(safe_limit)
        with self.connect() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._operator_handoff_from_row(row) for row in rows]

    def update_operator_handoff(
        self,
        handoff_id: str,
        *,
        status: str | None = None,
        target_operator_fork_id: str | None = None,
        command_id: str | None = None,
        tmux_pane_id: str | None = None,
        delivery_status: str | None = None,
        delivery_evidence: dict[str, Any] | None = None,
        artifact_bundle: list[Any] | None = None,
        required_artifacts: list[Any] | None = None,
        error: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        acknowledge: bool = False,
        start: bool = False,
        complete: bool = False,
    ) -> dict[str, Any] | None:
        handoff = self.get_operator_handoff(handoff_id)
        if handoff is None:
            return None
        normalized_status = (
            str(status or handoff.get("status") or "proposed").strip().lower()
        )
        if normalized_status not in OPERATOR_HANDOFF_STATUSES:
            raise ValueError("handoff status is invalid")
        current = now_ts()
        acknowledged_at = handoff.get("acknowledged_at")
        started_at = handoff.get("started_at")
        completed_at = handoff.get("completed_at")
        if acknowledge or normalized_status in {"acknowledged", "running"}:
            acknowledged_at = acknowledged_at or current
        if start or normalized_status == "running":
            started_at = started_at or current
        terminal = normalized_status in OPERATOR_HANDOFF_TERMINAL_STATUSES
        if complete or terminal:
            completed_at = completed_at or current
        elif status is not None:
            completed_at = None
        merged_metadata = dict(handoff.get("metadata") or {})
        if metadata:
            merged_metadata.update(metadata)
        merged_evidence = dict(handoff.get("delivery_evidence") or {})
        if delivery_evidence:
            merged_evidence.update(delivery_evidence)
        next_artifacts = (
            required_artifacts
            if required_artifacts is not None
            else handoff.get("required_artifacts") or []
        )
        next_bundle = (
            artifact_bundle
            if artifact_bundle is not None
            else handoff.get("artifact_bundle") or []
        )
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operator_handoffs
                SET status = ?,
                    target_operator_fork_id = COALESCE(?, target_operator_fork_id),
                    command_id = COALESCE(?, command_id),
                    tmux_pane_id = COALESCE(?, tmux_pane_id),
                    delivery_status = COALESCE(?, delivery_status),
                    delivery_evidence_json = ?,
                    artifact_bundle_json = ?,
                    required_artifacts_json = ?,
                    error = COALESCE(?, error),
                    summary = COALESCE(?, summary),
                    metadata_json = ?,
                    acknowledged_at = ?,
                    started_at = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE handoff_id = ?
                """,
                (
                    normalized_status,
                    target_operator_fork_id,
                    command_id,
                    tmux_pane_id,
                    delivery_status,
                    json.dumps(merged_evidence),
                    json.dumps(next_bundle),
                    json.dumps(next_artifacts),
                    error,
                    summary,
                    json.dumps(merged_metadata),
                    acknowledged_at,
                    started_at,
                    completed_at,
                    current,
                    handoff_id,
                ),
            )
        return self.get_operator_handoff(handoff_id) if cursor.rowcount else None

    def create_command(
        self,
        request: CommandCreateRequest,
        *,
        status: str = "queued",
    ) -> dict[str, Any]:
        command_id = str(uuid.uuid4())
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO commands
                    (command_id, agent_id, type, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    command_id,
                    request.agent_id,
                    request.type,
                    json.dumps(request.payload),
                    status,
                    current,
                ),
            )
        command = self.get_command(command_id)
        if command is None:
            raise RuntimeError("command insert failed")
        return command

    def get_command(self, command_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json, status, created_at,
                       claimed_at, acked_at, result_json
                FROM commands
                WHERE command_id = ?
                """,
                (command_id,),
            ).fetchone()
        return self._command_from_row(row) if row else None

    def delete_queued_command(self, command_id: str) -> dict[str, Any] | None:
        command = self.get_command(command_id)
        if command is None or command["status"] != "queued":
            return None
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM commands WHERE command_id = ? AND status = 'queued'",
                (command_id,),
            )
        return command if cursor.rowcount else None

    def claim_commands(self, agent_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                "UPDATE agents SET last_poll_at = ?, dismissed_at = NULL WHERE agent_id = ?",
                (current, agent_id),
            )
            rows = conn.execute(
                """
                SELECT command_id, agent_id, type, payload_json, status, created_at,
                       claimed_at, acked_at, result_json
                FROM commands
                WHERE status = 'queued' AND (agent_id IS NULL OR agent_id = ?)
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (agent_id, limit),
            ).fetchall()
            command_ids = [row["command_id"] for row in rows]
            if command_ids:
                placeholders = ",".join("?" for _ in command_ids)
                conn.execute(
                    f"""
                    UPDATE commands
                    SET status = 'delivered',
                        claimed_at = ?,
                        agent_id = COALESCE(agent_id, ?)
                    WHERE command_id IN ({placeholders}) AND status = 'queued'
                    """,
                    (current, agent_id, *command_ids),
                )
        return [self.get_command(command_id) for command_id in command_ids if command_id]

    def record_poll(self, agent_id: str, *, delivered_count: int) -> None:
        current = now_ts()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO poll_events(agent_id, delivered_count, created_at)
                SELECT ?, ?, ?
                WHERE EXISTS (SELECT 1 FROM agents WHERE agent_id = ?)
                """,
                (agent_id, max(0, delivered_count), current, agent_id),
            )

    def ack_command(
        self, command_id: str, agent_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        current = now_ts()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE commands
                SET status = 'acked', acked_at = ?, result_json = ?
                WHERE command_id = ? AND agent_id = ? AND status = 'delivered'
                """,
                (current, json.dumps(result or {}), command_id, agent_id),
            )
        if not cursor.rowcount:
            return None
        return self.get_command(command_id)

    @staticmethod
    def _operator_fork_select_sql() -> str:
        return """
            SELECT operator_fork_id, logical_operator_agent_id, fork_agent_id,
                   source_caller_agent_id, source_codex_session_id,
                   fork_track_id, fork_purpose, access_mode, source_cwd,
                   work_root, fork_codex_session_id, campaign_id, cwd, codex_home,
                   codex_host_id, tmux_pane_id, status, summary,
                   metadata_json, created_at, updated_at, last_used_at,
                   completed_at
            FROM operator_forks
        """

    @staticmethod
    def _operator_project_spawn_select_sql() -> str:
        return """
            SELECT spawn_request_id, logical_operator_agent_id, operator_agent_id,
                   review_fork_id, review_fork_agent_id, source_caller_agent_id,
                   source_cwd, target_parent, target_slug, target_path,
                   project_name, mode, instructions, status, launched_agent_id,
                   tmux_pane_id, error, campaign_id, assignment_id,
                   metadata_json, created_at, updated_at, completed_at
            FROM operator_project_spawn_requests
        """

    @staticmethod
    def _operator_knowledge_link_select_sql() -> str:
        return """
            SELECT l.link_id, l.logical_operator_agent_id, l.operator_agent_id,
                   l.source_agent_id, l.target_agent_id,
                   l.source_operator_fork_id, l.link_type, l.status,
                   l.summary, l.metadata_json, l.created_at, l.updated_at,
                   l.closed_at,
                   COALESCE((
                       SELECT COUNT(*)
                       FROM operator_knowledge_turns t
                       WHERE t.link_id = l.link_id
                         AND t.delivery_status = 'pending_approval'
                   ), 0) AS pending_turn_count,
                   (
                       SELECT MAX(t.created_at)
                       FROM operator_knowledge_turns t
                       WHERE t.link_id = l.link_id
                   ) AS latest_turn_at
            FROM operator_knowledge_links l
        """

    @staticmethod
    def _operator_handoff_select_sql() -> str:
        return """
            SELECT h.handoff_id, h.logical_operator_agent_id,
                   h.source_operator_agent_id, h.target_operator_agent_id,
                   h.source_agent_id, h.source_operator_fork_id,
                   h.target_caller_agent_id, h.target_operator_fork_id,
                   h.knowledge_link_id, h.knowledge_turn_id, h.objective,
                   h.message, h.allowed_mutation_scope,
                   h.required_artifacts_json, h.artifact_bundle_json,
                   h.status, h.needs_ack, h.expires_at, h.acknowledged_at,
                   h.started_at, h.completed_at, h.command_id, h.tmux_pane_id,
                   h.delivery_status, h.delivery_evidence_json, h.error,
                   h.summary, h.metadata_json, h.created_at, h.updated_at
            FROM operator_handoffs h
        """

    @staticmethod
    def _operator_kb_entry_select_sql() -> str:
        return """
            SELECT kb_id, scope, project, repo_root, git_remote, branch,
                   title, summary, body, tags_json, status, redaction_status,
                   created_by_operator_agent_id, created_by_agent_id,
                   source_knowledge_link_id, source_handoff_id,
                   source_turn_ids_json, stale_after, expires_at, metadata_json,
                   created_at, updated_at, promoted_at, retired_at, imported_at
            FROM operator_kb_entries
        """

    @staticmethod
    def _operator_kb_seed_run_select_sql() -> str:
        return """
            SELECT seed_run_id, logical_operator_agent_id,
                   source_operator_agent_id, seed_type, scope, project,
                   repo_root, git_remote, branch, prompt, status, command_id,
                   tmux_pane_id, delivery_status, error, metadata_json,
                   created_at, updated_at, completed_at
            FROM operator_kb_seed_runs
        """

    @classmethod
    def _operator_fork_row_for_agent(
        cls,
        conn: sqlite3.Connection,
        agent_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            f"""
            {cls._operator_fork_select_sql()}
            WHERE fork_agent_id = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (agent_id,),
        ).fetchone()

    @classmethod
    def _operator_fork_rows_for_agents(
        cls,
        conn: sqlite3.Connection,
        agent_ids: list[str],
    ) -> dict[str, sqlite3.Row]:
        if not agent_ids:
            return {}
        placeholders = ",".join("?" for _ in agent_ids)
        rows = conn.execute(
            f"""
            {cls._operator_fork_select_sql()}
            WHERE fork_agent_id IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            tuple(agent_ids),
        ).fetchall()
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            fork_agent_id = str(row["fork_agent_id"])
            latest.setdefault(fork_agent_id, row)
        return latest

    @staticmethod
    def _operator_fork_metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            decoded = json.loads(row["metadata_json"] or "{}")
        except json.JSONDecodeError:
            decoded = {}
        return decoded if isinstance(decoded, dict) else {}

    @classmethod
    def _operator_fork_agent_identity(cls, row: sqlite3.Row) -> dict[str, Any]:
        metadata = cls._operator_fork_metadata_from_row(row)
        identity: dict[str, Any] = dict(metadata)
        identity.update(
            {
                "agent_type": OPERATOR_AGENT_TYPE,
                "operator_role": OPERATOR_ROLE_FORK,
                "operator_fork_id": str(row["operator_fork_id"]),
                "logical_operator_id": str(row["logical_operator_agent_id"]),
                "source_caller_agent_id": str(row["source_caller_agent_id"]),
                "source_codex_session_id": str(row["source_codex_session_id"]),
                "fork_track_id": str(row["fork_track_id"] or "default"),
                "fork_purpose": str(row["fork_purpose"] or "edit"),
                "access_mode": str(row["access_mode"] or "edit"),
                "cwd": str(row["cwd"]),
            }
        )
        optional_keys = (
            "source_cwd",
            "work_root",
            "fork_codex_session_id",
            "codex_home",
            "codex_host_id",
            "tmux_pane_id",
        )
        for key in optional_keys:
            value = row[key]
            if isinstance(value, str) and value.strip():
                identity[key] = value.strip()
        return identity

    @staticmethod
    def _none_if_blank(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _normalize_id_list(values: list[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @staticmethod
    def _normalize_tags(values: list[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip().lower()
            text = re.sub(r"[^a-z0-9_.:/+-]+", "-", text).strip("-")
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    @staticmethod
    def _normalize_operator_kb_scope(scope: str) -> str:
        normalized = str(scope or "project").strip().lower()
        if normalized not in OPERATOR_KB_SCOPES:
            raise ValueError("KB scope is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_status(status: str | None) -> str:
        normalized = str(status or "proposed").strip().lower()
        if normalized not in OPERATOR_KB_STATUSES:
            raise ValueError("KB status is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_redaction_status(status: str | None) -> str:
        normalized = str(status or "unreviewed").strip().lower()
        if normalized not in OPERATOR_KB_REDACTION_STATUSES:
            raise ValueError("KB redaction status is invalid")
        return normalized

    @staticmethod
    def _normalize_operator_kb_seed_run_status(status: str | None) -> str:
        normalized = str(status or "requested").strip().lower()
        if normalized not in OPERATOR_KB_SEED_RUN_STATUSES:
            raise ValueError("KB seed run status is invalid")
        return normalized

    @staticmethod
    def _operator_kb_sources_payload(
        *,
        kb_id: str,
        source_knowledge_link_id: str | None,
        source_handoff_id: str | None,
        source_turn_ids: list[Any],
        sources: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def add(
            source_type: str,
            source_id: Any,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            normalized_type = str(source_type or "").strip().lower()
            normalized_id = str(source_id or "").strip()
            if not normalized_type or not normalized_id:
                return
            key = (normalized_type, normalized_id)
            if key in seen:
                return
            seen.add(key)
            payloads.append(
                {
                    "source_row_id": str(uuid.uuid4()),
                    "kb_id": kb_id,
                    "source_type": normalized_type,
                    "source_id": normalized_id,
                    "metadata": metadata or {},
                }
            )

        add("knowledge_link", source_knowledge_link_id)
        add("handoff", source_handoff_id)
        for turn_id in source_turn_ids:
            add("knowledge_turn", turn_id)
        for source in sources:
            if isinstance(source, dict):
                add(
                    str(source.get("source_type") or ""),
                    source.get("source_id"),
                    source.get("metadata")
                    if isinstance(source.get("metadata"), dict)
                    else {},
                )
        return payloads

    @staticmethod
    def _operator_kb_source_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _operator_kb_entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key, default in (
            ("tags_json", []),
            ("source_turn_ids_json", []),
            ("metadata_json", {}),
        ):
            raw = data.pop(key)
            try:
                decoded = json.loads(raw or json.dumps(default))
            except json.JSONDecodeError:
                decoded = default
            data[key.removesuffix("_json")] = (
                decoded if isinstance(decoded, type(default)) else default
            )
        data["sources"] = []
        return data

    @staticmethod
    def _operator_kb_seed_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _operator_kb_sources_for_entry(
        conn: sqlite3.Connection,
        kb_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT source_row_id, kb_id, source_type, source_id,
                   metadata_json, created_at
            FROM operator_kb_sources
            WHERE kb_id = ?
            ORDER BY created_at ASC, source_row_id ASC
            """,
            (kb_id,),
        ).fetchall()
        return [Store._operator_kb_source_from_row(row) for row in rows]

    @staticmethod
    def _agent_prune_candidate_from_agent(
        agent: dict[str, Any],
        *,
        reason: str,
        guard_reasons: list[str],
        now: float,
    ) -> dict[str, Any]:
        last_seen_at = float(agent.get("last_seen_at") or 0.0)
        age_days = max(0.0, (now - last_seen_at) / 86_400.0) if last_seen_at else 0.0
        return {
            "agent_id": str(agent.get("agent_id") or ""),
            "agent_type": Store._normalized_agent_type(agent.get("agent_type")),
            "project": str(agent.get("project") or ""),
            "status": str(agent.get("status") or ""),
            "effective_status": str(agent.get("effective_status") or ""),
            "latest_report_status": agent.get("latest_report_status"),
            "last_seen_at": last_seen_at,
            "age_days": age_days,
            "starred": bool(agent.get("starred")),
            "queued_command_count": int(agent.get("queued_command_count") or 0),
            "active_campaign_count": int(agent.get("active_campaign_count") or 0),
            "reason": reason,
            "guard_reasons": sorted(set(guard_reasons)),
        }

    @staticmethod
    def _agent_has_recorded_tmux_pane(agent: dict[str, Any]) -> bool:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        return bool(str(metadata.get("tmux_pane_id") or "").strip())

    @staticmethod
    def _agent_is_operator_fork(agent: dict[str, Any]) -> bool:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        return (
            Store._normalized_agent_type(agent.get("agent_type")) == OPERATOR_AGENT_TYPE
            and str(metadata.get("operator_role") or "").strip().lower()
            == OPERATOR_ROLE_FORK
        )

    @staticmethod
    def _agent_prune_match_reason(
        agent: dict[str, Any],
        *,
        preset: str,
        min_age_days: float,
        now: float,
    ) -> str | None:
        last_seen_at = float(agent.get("last_seen_at") or 0.0)
        age_days = max(0.0, (now - last_seen_at) / 86_400.0) if last_seen_at else 0.0
        if age_days < min_age_days:
            return None
        effective_status = _normalized_prune_status(agent.get("effective_status"))
        status = _normalized_prune_status(agent.get("status"))
        latest_status = _normalized_prune_status(agent.get("latest_report_status"))
        terminal = (
            _agent_prune_status_is_terminal(effective_status)
            or _agent_prune_status_is_terminal(status)
            or _agent_prune_status_is_terminal(latest_status)
        )
        agent_type = Store._normalized_agent_type(agent.get("agent_type"))
        if preset == "terminal-callers":
            if agent_type != "caller" or not terminal:
                return None
            return "terminal caller older than threshold"
        if preset == "stale-callers":
            if agent_type != "caller" or terminal:
                return None
            if not (
                _agent_prune_status_is_nonterminal_stale(effective_status)
                or _agent_prune_status_is_nonterminal_stale(status)
                or _agent_prune_status_is_nonterminal_stale(latest_status)
            ):
                return None
            return "stale nonterminal caller older than threshold"
        if preset == "operator-forks":
            if not Store._agent_is_operator_fork(agent):
                return None
            if not terminal and not (
                _agent_prune_status_is_nonterminal_stale(effective_status)
                or _agent_prune_status_is_nonterminal_stale(status)
                or _agent_prune_status_is_nonterminal_stale(latest_status)
            ):
                return None
            return "operator fork older than threshold"
        return None

    @staticmethod
    def _agent_prune_batch_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key, target in (
            ("criteria_json", "criteria"),
            ("results_json", "results"),
            ("undo_results_json", "undo_results"),
            ("metadata_json", "metadata"),
        ):
            try:
                decoded = json.loads(data.pop(key) or "{}")
            except json.JSONDecodeError:
                decoded = [] if key.endswith("results_json") else {}
            data[target] = decoded
        data["delete_thread"] = bool(data.get("delete_thread"))
        return data

    def agent_prune_protection_reasons(self) -> dict[str, list[str]]:
        reasons: dict[str, set[str]] = {}

        def add(agent_id: Any, reason: str) -> None:
            value = str(agent_id or "").strip()
            if not value:
                return
            reasons.setdefault(value, set()).add(reason)

        with self.connect() as conn:
            active_campaign_filter = _operator_active_state_sql(
                "status",
                "completed_at",
            )
            for row in conn.execute(
                f"""
                SELECT operator_agent_id
                FROM operator_campaigns
                WHERE {active_campaign_filter}
                """
            ):
                add(row["operator_agent_id"], "active_operator_campaign")

            active_assignment_filter = _operator_active_state_sql(
                "state",
                "completed_at",
            )
            for row in conn.execute(
                f"""
                SELECT target_agent_id
                FROM operator_campaign_assignments
                WHERE {active_assignment_filter}
                """
            ):
                add(row["target_agent_id"], "active_campaign_assignment")

            active_fork_filter = _operator_active_state_sql(
                "status",
                "completed_at",
            )
            for row in conn.execute(
                f"""
                SELECT logical_operator_agent_id, fork_agent_id, source_caller_agent_id
                FROM operator_forks
                WHERE {active_fork_filter}
                """
            ):
                add(row["logical_operator_agent_id"], "active_operator_fork_owner")
                add(row["fork_agent_id"], "active_operator_fork")
                add(row["source_caller_agent_id"], "active_operator_fork_source")

            for row in conn.execute(
                """
                SELECT l.logical_operator_agent_id, l.operator_agent_id,
                       l.source_agent_id, l.target_agent_id,
                       f.fork_agent_id AS source_fork_agent_id
                FROM operator_knowledge_links l
                LEFT JOIN operator_forks f
                  ON f.operator_fork_id = l.source_operator_fork_id
                WHERE l.status IN ('proposed', 'active')
                """
            ):
                for key in (
                    "logical_operator_agent_id",
                    "operator_agent_id",
                    "source_agent_id",
                    "target_agent_id",
                    "source_fork_agent_id",
                ):
                    add(row[key], "active_knowledge_link")

            handoff_placeholders = ",".join(
                "?" for _ in OPERATOR_HANDOFF_TERMINAL_STATUSES
            )
            for row in conn.execute(
                f"""
                SELECT h.logical_operator_agent_id, h.source_operator_agent_id,
                       h.target_operator_agent_id, h.source_agent_id,
                       h.target_caller_agent_id,
                       sf.fork_agent_id AS source_fork_agent_id,
                       tf.fork_agent_id AS target_fork_agent_id
                FROM operator_handoffs h
                LEFT JOIN operator_forks sf
                  ON sf.operator_fork_id = h.source_operator_fork_id
                LEFT JOIN operator_forks tf
                  ON tf.operator_fork_id = h.target_operator_fork_id
                WHERE lower(h.status) NOT IN ({handoff_placeholders})
                """,
                tuple(sorted(OPERATOR_HANDOFF_TERMINAL_STATUSES)),
            ):
                for key in (
                    "logical_operator_agent_id",
                    "source_operator_agent_id",
                    "target_operator_agent_id",
                    "source_agent_id",
                    "target_caller_agent_id",
                    "source_fork_agent_id",
                    "target_fork_agent_id",
                ):
                    add(row[key], "active_operator_handoff")

            spawn_placeholders = ",".join(
                "?" for _ in PROJECT_SPAWN_TERMINAL_STATUSES
            )
            for row in conn.execute(
                f"""
                SELECT logical_operator_agent_id, operator_agent_id,
                       review_fork_agent_id, source_caller_agent_id,
                       launched_agent_id
                FROM operator_project_spawn_requests
                WHERE lower(status) NOT IN ({spawn_placeholders})
                """,
                tuple(sorted(PROJECT_SPAWN_TERMINAL_STATUSES)),
            ):
                for key in (
                    "logical_operator_agent_id",
                    "operator_agent_id",
                    "review_fork_agent_id",
                    "source_caller_agent_id",
                    "launched_agent_id",
                ):
                    add(row[key], "active_project_spawn")

        return {
            agent_id: sorted(agent_reasons)
            for agent_id, agent_reasons in reasons.items()
        }

    @classmethod
    def _hydrate_operator_fork_agent(
        cls,
        agent: dict[str, Any],
        fork_row: sqlite3.Row,
    ) -> None:
        metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
        hydrated = dict(metadata)
        hydrated.update(cls._operator_fork_agent_identity(fork_row))
        agent["agent_type"] = OPERATOR_AGENT_TYPE
        source_project = str(hydrated.get("source_caller_project") or "").strip()
        if source_project:
            agent["project"] = source_project
        agent["metadata"] = hydrated

    @staticmethod
    def _agent_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["agent_type"] = Store._normalized_agent_type(data.get("agent_type"))
        data["pbx_active"] = bool(data["pbx_active"])
        data["starred"] = data.get("starred_at") is not None
        data["metadata"] = json.loads(data.pop("metadata_json"))
        Store._add_effective_status(data)
        data["queued_command_count"] = 0
        data["oldest_queued_command_age_seconds"] = None
        data["polls_per_hour"] = 0
        data["empty_polls_per_hour"] = 0
        data["reports_per_hour"] = 0
        data["pings_per_hour"] = 0
        data["estimated_visible_tokens_per_hour"] = 0
        data["usage_warning"] = None
        data["latest_report_id"] = None
        data["latest_report_created_at"] = None
        data["latest_report_status"] = None
        data["latest_report_needs_input"] = False
        data["latest_report_plan_option_count"] = 0
        data["latest_report_action_required"] = False
        data["active_campaign_count"] = 0
        return data

    @staticmethod
    def _add_effective_status(agent: dict[str, Any]) -> None:
        status = str(agent.get("status") or "")
        last_seen_at = agent.get("last_seen_at")
        age: float | None = None
        if last_seen_at is not None:
            try:
                age = max(0.0, now_ts() - float(last_seen_at))
            except (TypeError, ValueError):
                age = None
        stale = (
            age is not None
            and age >= STALE_WORKING_SECONDS
            and status.lower() in STALE_WORKING_STATUSES
        )
        agent["status_age_seconds"] = age
        agent["status_stale"] = stale
        agent["effective_status"] = f"stale-{status}" if stale else status

    @staticmethod
    def _add_latest_report_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        row = conn.execute(
            """
            SELECT report_id, status, needs_input, plan_options_json, created_at
            FROM reports
            WHERE agent_id = ?
            ORDER BY created_at DESC, report_id DESC
            LIMIT 1
            """,
            (agent["agent_id"],),
        ).fetchone()
        if row is None:
            return
        try:
            plan_options = json.loads(row["plan_options_json"])
        except json.JSONDecodeError:
            plan_options = []
        option_count = len(plan_options) if isinstance(plan_options, list) else 0
        needs_input = bool(row["needs_input"])
        agent["latest_report_id"] = row["report_id"]
        agent["latest_report_created_at"] = row["created_at"]
        agent["latest_report_status"] = row["status"]
        agent["latest_report_needs_input"] = needs_input
        agent["latest_report_plan_option_count"] = option_count
        agent["latest_report_action_required"] = needs_input or option_count > 0

    @staticmethod
    def _add_queue_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        current = now_ts()
        row = conn.execute(
            """
            SELECT COUNT(*) AS queued_count, MIN(created_at) AS oldest_created_at
            FROM commands
            WHERE status = 'queued' AND (agent_id IS NULL OR agent_id = ?)
            """,
            (agent["agent_id"],),
        ).fetchone()
        queued_count = int(row["queued_count"] or 0) if row else 0
        oldest = row["oldest_created_at"] if row else None
        agent["queued_command_count"] = queued_count
        agent["oldest_queued_command_age_seconds"] = (
            max(0.0, current - float(oldest)) if oldest is not None else None
        )

    @staticmethod
    def _add_campaign_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        agent_id = agent["agent_id"]
        if agent.get("agent_type") == "operator":
            active_status_filter = _operator_active_state_sql(
                "status",
                "completed_at",
            )
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS active_count
                FROM operator_campaigns
                WHERE operator_agent_id = ?
                  AND {active_status_filter}
                """,
                (agent_id,),
            ).fetchone()
        else:
            active_state_filter = _operator_active_state_sql(
                "state",
                "completed_at",
            )
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS active_count
                FROM operator_campaign_assignments
                WHERE target_agent_id = ?
                  AND {active_state_filter}
                """,
                (agent_id,),
            ).fetchone()
        agent["active_campaign_count"] = int(row["active_count"] or 0) if row else 0

    @staticmethod
    def _add_usage_summary(
        conn: sqlite3.Connection, agent: dict[str, Any]
    ) -> None:
        since = now_ts() - 3600
        agent_id = agent["agent_id"]
        poll_row = conn.execute(
            """
            SELECT COUNT(*) AS poll_count,
                   SUM(CASE WHEN delivered_count = 0 THEN 1 ELSE 0 END) AS empty_count,
                   SUM(delivered_count) AS delivered_count
            FROM poll_events
            WHERE agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
        report_row = conn.execute(
            """
            SELECT COUNT(*) AS report_count,
                   COALESCE(SUM(LENGTH(summary) + LENGTH(detail)), 0) AS report_chars
            FROM reports
            WHERE agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
        command_row = conn.execute(
            """
            SELECT COUNT(*) AS command_count,
                   SUM(CASE WHEN type = 'ping' THEN 1 ELSE 0 END) AS ping_count,
                   COALESCE(SUM(LENGTH(payload_json) + COALESCE(LENGTH(result_json), 0)), 0) AS command_chars
            FROM commands
            WHERE (agent_id = ? OR agent_id IS NULL) AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
        poll_count = int(poll_row["poll_count"] or 0) if poll_row else 0
        empty_count = int(poll_row["empty_count"] or 0) if poll_row else 0
        delivered_count = int(poll_row["delivered_count"] or 0) if poll_row else 0
        report_count = int(report_row["report_count"] or 0) if report_row else 0
        report_chars = int(report_row["report_chars"] or 0) if report_row else 0
        ping_count = int(command_row["ping_count"] or 0) if command_row else 0
        command_chars = int(command_row["command_chars"] or 0) if command_row else 0
        estimated_tokens = (
            poll_count * POLL_BASE_TOKEN_ESTIMATE
            + delivered_count * DELIVERED_COMMAND_TOKEN_ESTIMATE
            + (report_chars + command_chars) // TOKEN_ESTIMATE_CHARS_PER_TOKEN
        )
        warning = None
        if estimated_tokens >= USAGE_WARN_TOKENS_PER_HOUR:
            warning = "high estimated token use"
        elif poll_count >= POLL_WARN_PER_HOUR and empty_count == poll_count:
            warning = "idle polling"
        agent["polls_per_hour"] = poll_count
        agent["empty_polls_per_hour"] = empty_count
        agent["reports_per_hour"] = report_count
        agent["pings_per_hour"] = ping_count
        agent["estimated_visible_tokens_per_hour"] = estimated_tokens
        agent["usage_warning"] = warning

    @staticmethod
    def _report_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["needs_input"] = bool(data["needs_input"])
        data["plan_options"] = json.loads(data.pop("plan_options_json"))
        metadata_json = data.pop("metadata_json", "{}")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _command_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        result_json = data.pop("result_json")
        data["result"] = json.loads(result_json) if result_json else None
        return data

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return data

    @staticmethod
    def _joplin_log_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["active"] = bool(data["active"])
        return data

    @staticmethod
    def _joplin_sync_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    @staticmethod
    def _operator_campaign_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["criteria"] = json.loads(data.pop("criteria_json"))
        data["assignments"] = []
        data["events"] = []
        return data

    @staticmethod
    def _operator_assignment_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["criteria"] = json.loads(data.pop("criteria_json"))
        return data

    @staticmethod
    def _operator_campaign_event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        detail_json = data.pop("detail_json")
        try:
            detail = json.loads(detail_json)
        except json.JSONDecodeError:
            detail = {}
        data["detail"] = detail if isinstance(detail, dict) else {}
        return data

    @staticmethod
    def _operator_fork_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        data["edges"] = []
        return data

    @staticmethod
    def _operator_project_spawn_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _operator_knowledge_link_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        data["pending_turn_count"] = int(data.get("pending_turn_count") or 0)
        latest_turn_at = data.get("latest_turn_at")
        data["latest_turn_at"] = latest_turn_at if latest_turn_at is not None else None
        return data

    @staticmethod
    def _operator_knowledge_turn_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _operator_handoff_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key, default in (
            ("required_artifacts_json", []),
            ("artifact_bundle_json", []),
            ("delivery_evidence_json", {}),
            ("metadata_json", {}),
        ):
            raw = data.pop(key)
            try:
                decoded = json.loads(raw or json.dumps(default))
            except json.JSONDecodeError:
                decoded = default
            if key.endswith("_json"):
                target_key = key.removesuffix("_json")
            else:
                target_key = key
            data[target_key] = decoded if isinstance(decoded, type(default)) else default
        data["needs_ack"] = bool(data["needs_ack"])
        expires_at = data.get("expires_at")
        status = str(data.get("status") or "").lower()
        now = now_ts()
        time_remaining: float | None = None
        if expires_at is not None:
            try:
                time_remaining = float(expires_at) - now
            except (TypeError, ValueError):
                time_remaining = None
        terminal = status in OPERATOR_HANDOFF_TERMINAL_STATUSES
        expired = bool(
            status == "expired"
            or (
                time_remaining is not None
                and time_remaining <= 0
                and data.get("started_at") is None
                and not terminal
            )
        )
        data["time_remaining_seconds"] = (
            max(0.0, time_remaining) if time_remaining is not None else None
        )
        data["expired"] = expired
        data["safe_to_start"] = not expired
        return data

    @staticmethod
    def _operator_fork_edge_from_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        metadata_json = data.pop("metadata_json")
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError:
            metadata = {}
        data["metadata"] = metadata if isinstance(metadata, dict) else {}
        return data

    @staticmethod
    def _thread_report(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": f"report:{report['report_id']}",
            "kind": "report",
            "agent_id": report["agent_id"],
            "created_at": report["created_at"],
            "status": report["status"],
            "title": report["summary"],
            "body": report["detail"],
            "metadata": {
                "report_id": report["report_id"],
                "project": report["project"],
                "needs_input": report["needs_input"],
                "plan_options": report["plan_options"],
                "report_metadata": report.get("metadata", {}),
            },
        }

    @staticmethod
    def _thread_command(command: dict[str, Any]) -> dict[str, Any]:
        payload = command["payload"]
        if command["type"] == "send_input":
            title = "Follow-up input"
            body = str(payload.get("message") or payload)
        elif command["type"] == "send_key":
            key = str(payload.get("key") or "").strip() or "key"
            title = f"Send key: {key}"
            body = str(payload.get("request") or payload)
        elif command["type"] == "request_detail":
            title = "Detail request"
            body = str(payload.get("request") or payload)
        elif command["type"] == "ping":
            title = "Ping"
            body = str(payload.get("request") or "Ping agent and extend polling.")
        else:
            title = command["type"].replace("_", " ").title()
            body = json.dumps(payload, indent=2, sort_keys=True)
        return {
            "item_id": f"command:{command['command_id']}",
            "kind": "command",
            "agent_id": command["agent_id"],
            "created_at": command["created_at"],
            "status": command["status"],
            "title": title,
            "body": body,
            "metadata": {
                "command_id": command["command_id"],
                "type": command["type"],
                "payload": payload,
                "claimed_at": command["claimed_at"],
                "acked_at": command["acked_at"],
                "result": command["result"],
            },
        }
