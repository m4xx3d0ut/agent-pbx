from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentType = Literal["caller", "operator"]
AgentPrunePreset = Literal["terminal-callers", "stale-callers", "operator-forks"]
CampaignDelivery = Literal["auto", "queue", "tmux"]
CampaignStatus = Literal["running", "complete", "completed", "blocked", "failed", "canceled"]
OperatorReviewEscalationRoute = Literal[
    "primary_idle_edit_fork",
    "root_operator",
    "new_write_operator",
]
OperatorProjectSpawnMode = Literal["empty", "clone_source"]
OperatorProjectSpawnStatus = Literal[
    "pending",
    "launching",
    "launched",
    "failed",
    "canceled",
    "cancelled",
]
OperatorKnowledgeLinkType = Literal[
    "handoff",
    "consult",
    "domain_context",
    "review_context",
]
OperatorKnowledgeLinkStatus = Literal[
    "proposed",
    "active",
    "closed",
    "canceled",
    "cancelled",
]
OperatorKnowledgeTurnType = Literal[
    "handoff",
    "question",
    "answer",
    "note",
]
OperatorKnowledgeDeliveryStatus = Literal[
    "pending_approval",
    "recorded",
    "queued",
    "sent",
    "failed",
]
OperatorHandoffStatus = Literal[
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
]
OperatorHandoffCancelReason = Literal[
    "superseded",
    "no_longer_needed",
    "duplicate",
    "test_or_uat_artifact",
    "created_in_error",
    "other",
]
OperatorKbScope = Literal["global", "project", "repo", "operator", "caller"]
OperatorKbStatus = Literal["proposed", "active", "retired", "rejected"]
OperatorKbRedactionStatus = Literal[
    "unreviewed",
    "clean",
    "needs_review",
    "blocked",
]
OperatorKbSeedRunStatus = Literal[
    "requested",
    "queued",
    "sent",
    "failed",
    "complete",
    "completed",
    "canceled",
    "cancelled",
]
OperatorKbFeedbackType = Literal[
    "accepted",
    "rejected",
    "stale",
    "wrong_scope",
    "unsafe",
    "miss",
]
OperatorKbIndexJobOperation = Literal["upsert", "delete", "rebuild"]
OperatorKbIndexJobStatus = Literal["queued", "running", "complete", "failed"]
AssignmentState = Literal[
    "pending",
    "sent",
    "waiting",
    "needs_followup",
    "awaiting_broker_approval",
    "complete",
    "completed",
    "blocked",
    "failed",
    "canceled",
]


CommandType = Literal[
    "request_detail",
    "send_input",
    "send_key",
    "start_task",
    "cancel_task",
    "acknowledge",
    "ping",
]


class CodexCliUpdateRequest(BaseModel):
    target: str = Field(default="latest", min_length=1, max_length=80)
    timeout_seconds: float = Field(default=180.0, ge=30.0, le=600.0)


class CodexCliUpdateResponse(BaseModel):
    ok: bool
    target: str
    package_spec: str
    command: list[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_seconds: float
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)


class ModelElevationRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    requested_profile: str = Field(min_length=1, max_length=80)
    prior_profile: str | None = Field(default=None, max_length=80)
    justification: str = Field(min_length=8, max_length=4000)
    scope: str = Field(default="one_task", min_length=1, max_length=120)


class ModelElevationDecisionRequest(BaseModel):
    approved: bool
    approved_by: str = Field(min_length=1, max_length=120)
    duration_seconds: float = Field(default=3600.0, ge=60.0, le=86400.0)


class ModelElevationActivateRequest(BaseModel):
    transition: dict[str, Any] = Field(default_factory=dict)


class ModelElevationTransitionRequest(BaseModel):
    transition: dict[str, Any] = Field(default_factory=dict)


class ModelElevationRevertRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=240)
    transition: dict[str, Any] = Field(default_factory=dict)


class ModelElevationFinishRequest(BaseModel):
    status: Literal["expired", "failed", "reverted"]
    transition: dict[str, Any] = Field(default_factory=dict)


class ModelElevationResponse(BaseModel):
    lease_id: str
    agent_id: str
    requested_profile: str
    prior_profile: str | None = None
    justification: str
    scope: str
    status: str
    requested_at: float
    approved_at: float | None = None
    activated_at: float | None = None
    expires_at: float | None = None
    finished_at: float | None = None
    approved_by: str | None = None
    transition: dict[str, Any] = Field(default_factory=dict)
    expired: bool = False
    reused: bool = False


class ManagedSkillApplyRequest(BaseModel):
    pack_names: list[str] = Field(default_factory=list, max_length=20)
    max_estimated_tokens: int = Field(default=12_000, ge=256, le=100_000)


class CodexRuntimeEventRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=240)
    method: str = Field(min_length=1, max_length=160)
    params: dict[str, Any] = Field(default_factory=dict)
    observed_at: float | None = Field(default=None, ge=0)


class TmuxRuntimeMappingRequest(BaseModel):
    server_mode: Literal["dedicated", "outer_if_present", "outer_required"]
    socket_path: str = Field(min_length=1, max_length=4096)
    session_name: str = Field(min_length=1, max_length=240)
    window_id: str | None = Field(default=None, max_length=120)
    window_name: str | None = Field(default=None, max_length=240)
    pane_id: str = Field(min_length=1, max_length=120)
    pane_pid: int | None = Field(default=None, ge=1)
    process_start_ticks: int | None = Field(default=None, ge=1)
    codex_session_id: str | None = Field(default=None, max_length=240)
    cwd: str | None = Field(default=None, max_length=4096)
    origin_client_tty: str | None = Field(default=None, max_length=4096)
    origin_session_name: str | None = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TmuxWriterLeaseRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=240)
    ttl_seconds: float = Field(default=30.0, ge=5.0, le=300.0)


class ManagedRuntimeStopPrepareRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=240)
    expected_server_id: str = Field(min_length=1, max_length=240)
    expected_socket_path: str = Field(min_length=1, max_length=4096)
    expected_session_name: str = Field(min_length=1, max_length=240)
    expected_window_id: str = Field(min_length=1, max_length=120)
    expected_pane_id: str = Field(min_length=1, max_length=120)
    expected_pane_pid: int | None = Field(default=None, ge=1)
    expected_process_start_ticks: int | None = Field(default=None, ge=1)
    expected_codex_session_id: str | None = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ManagedRuntimeStopFinalizeRequest(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class TmuxRuntimeMappingResponse(BaseModel):
    entity_id: str
    server_mode: str
    server_id: str
    socket_path: str
    session_name: str
    window_id: str | None = None
    window_name: str | None = None
    pane_id: str
    pane_pid: int | None = None
    process_start_ticks: int | None = None
    codex_session_id: str | None = None
    cwd: str | None = None
    origin_client_tty: str | None = None
    origin_session_name: str | None = None
    state: str
    writer_client_id: str | None = None
    writer_lease_expires_at: float | None = None
    writer_lease_active: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class ManagedAgentLaunchRequest(BaseModel):
    project_path: str = Field(min_length=1, max_length=4096)
    agent_id: str = Field(default="", max_length=120)
    profile_id: str = Field(default="sol-high", min_length=1, max_length=120)
    runtime_server_mode: Literal[
        "dedicated", "outer_if_present", "outer_required"
    ] = "dedicated"
    source_codex_session_id: str | None = Field(default=None, max_length=200)


class RuntimeMigrationPreviewRequest(BaseModel):
    agent_ids: list[str] = Field(default_factory=list, max_length=500)
    include_starred: bool = True
    include_operators: bool = True
    target_cli_version: str = Field(default="", max_length=120)
    retention_days: float = Field(default=7.0, ge=1.0, le=90.0)


class RuntimeMigrationResultsRequest(BaseModel):
    results: list[dict[str, Any]] = Field(default_factory=list, max_length=500)


class AgentPaneAdoptionPreviewRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    pane_id: str = Field(default="", max_length=120)
    runtime_server_mode: Literal[
        "dedicated", "outer_if_present", "outer_required"
    ] = "outer_if_present"
    destination_session: str = Field(
        default="agent-pbx-agents", min_length=1, max_length=240
    )
    origin_session_name: str | None = Field(default=None, max_length=240)
    origin_client_tty: str | None = Field(default=None, max_length=4096)
    retention_days: float = Field(default=7.0, ge=1.0, le=90.0)


class CodexConfigMcpPatchRequest(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    bearer_token_env_var: str | None = Field(default=None, max_length=120)
    default_tools_approval_mode: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None
    required: bool | None = None
    startup_timeout_sec: float | None = Field(default=None, ge=0.0, le=600.0)
    tool_timeout_sec: float | None = Field(default=None, ge=0.0, le=3600.0)


class CodexConfigSecretPatchRequest(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    value: str | None = Field(default=None, max_length=20000)
    remove: bool = False


class CodexConfigPatchRequest(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    agent_pbx_mcp: CodexConfigMcpPatchRequest | None = None
    secret_updates: list[CodexConfigSecretPatchRequest] = Field(default_factory=list)
    keymap_preset: Literal["portable", "reset"] | None = None
    tui_theme: str | None = Field(default=None, max_length=120)


class CodexConfigViewResponse(BaseModel):
    path: str
    exists: bool
    mtime: float | None = None
    size_bytes: int = 0
    parse_error: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    hidden_items: list[dict[str, Any]] = Field(default_factory=list)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    editable_paths: list[str] = Field(default_factory=list)
    secret_update_paths: list[str] = Field(default_factory=list)
    keymap: dict[str, Any] = Field(default_factory=dict)
    keymap_presets: list[str] = Field(default_factory=list)
    theme: dict[str, Any] = Field(default_factory=dict)


class CodexConfigPatchResponse(BaseModel):
    ok: bool
    changed_paths: list[str] = Field(default_factory=list)
    backup_path: str | None = None
    config: CodexConfigViewResponse


class AgentRegisterRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=240)
    name: str | None = Field(default=None, max_length=120)
    agent_type: AgentType = "caller"
    metadata: dict[str, Any] = Field(default_factory=dict)
    pbx_active: bool = True


class AgentActiveRequest(BaseModel):
    active: bool


class LifecycleActionRequest(BaseModel):
    preview_token: str = Field(min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    agent_id: str
    agent_type: AgentType = "caller"
    project: str
    name: str | None
    status: str
    effective_status: str | None = None
    status_age_seconds: float | None = None
    status_stale: bool = False
    pbx_active: bool = True
    metadata: dict[str, Any]
    created_at: float
    last_seen_at: float
    last_poll_at: float | None = None
    starred: bool = False
    starred_at: float | None = None
    dismissed_at: float | None = None
    queued_command_count: int = 0
    oldest_queued_command_age_seconds: float | None = None
    polls_per_hour: int = 0
    empty_polls_per_hour: int = 0
    reports_per_hour: int = 0
    pings_per_hour: int = 0
    estimated_visible_tokens_per_hour: int = 0
    usage_warning: str | None = None
    latest_report_id: str | None = None
    latest_report_created_at: float | None = None
    latest_report_seen_at: float | None = None
    latest_report_status: str | None = None
    latest_report_needs_input: bool = False
    latest_report_plan_option_count: int = 0
    latest_report_action_required: bool = False
    latest_report_suppress_tui_alerts: bool = False
    active_campaign_count: int = 0
    pending_handoff_approval_count: int = 0
    oldest_pending_handoff_created_at: float | None = None
    ready_handoff_action_count: int = 0
    oldest_ready_handoff_action_at: float | None = None
    pending_completion_delivery_count: int = 0
    failed_completion_delivery_count: int = 0
    oldest_completion_delivery_at: float | None = None
    latest_completion_delivery_id: str | None = None
    latest_completion_target_operator_agent_id: str | None = None
    workflow_state: str = "idle"
    workflow_label: str = "◇ IDLE"
    workflow_reason: str = "No active Operator workflow"
    workflow_source: str = "agent_status"
    workflow_subject_id: str | None = None
    workflow_flow: str = ""


class OperatorAlertAcknowledgementItem(BaseModel):
    agent_id: str
    latest_report_seen_at: float
    reason: Literal["root", "linked_edit_fork"]
    changed: bool = False


class OperatorAlertPreservedItem(BaseModel):
    agent_id: str
    reason: Literal[
        "review_fork",
        "newer_than_root_cutoff",
        "missing_agent",
        "no_report",
    ]


class OperatorAlertAcknowledgementResponse(BaseModel):
    root_operator_id: str
    cutoff: float | None = None
    acknowledged: list[OperatorAlertAcknowledgementItem] = Field(default_factory=list)
    preserved: list[OperatorAlertPreservedItem] = Field(default_factory=list)


class AgentPruneRequest(BaseModel):
    preset: AgentPrunePreset = "terminal-callers"
    min_age_days: float = Field(default=30.0, ge=0.0, le=3650.0)
    include_projects: list[str] = Field(default_factory=list)
    exclude_projects: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    include_hidden: bool = False
    include_starred: bool = False
    require_no_tmux_pane: bool = True
    limit: int = Field(default=500, ge=1, le=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPruneCandidateResponse(BaseModel):
    agent_id: str
    agent_type: AgentType
    project: str
    status: str
    effective_status: str | None = None
    latest_report_status: str | None = None
    last_seen_at: float
    age_days: float
    starred: bool = False
    queued_command_count: int = 0
    active_campaign_count: int = 0
    reason: str
    guard_reasons: list[str] = Field(default_factory=list)


class AgentPrunePreviewResponse(BaseModel):
    preset: str
    min_age_days: float
    include_hidden: bool = False
    include_starred: bool = False
    require_no_tmux_pane: bool = True
    delete_thread: bool = False
    candidate_count: int
    skipped_count: int
    candidates: list[AgentPruneCandidateResponse] = Field(default_factory=list)
    skipped: list[AgentPruneCandidateResponse] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentPruneBatchResponse(BaseModel):
    batch_id: str
    preset: str
    delete_thread: bool = False
    criteria: dict[str, Any]
    candidate_count: int
    hidden_count: int
    skipped_count: int
    results: list[dict[str, Any]] = Field(default_factory=list)
    undo_results: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    undone_at: float | None = None


class AgentPruneApplyResponse(BaseModel):
    preview: AgentPrunePreviewResponse
    batch: AgentPruneBatchResponse


class AgentPruneBatchListResponse(BaseModel):
    batches: list[AgentPruneBatchResponse] = Field(default_factory=list)


class StructuredPlanOption(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=400)
    description: str | None = Field(default=None, max_length=2000)


PlanOption = str | StructuredPlanOption


def plan_option_to_jsonable(option: PlanOption) -> str | dict[str, str]:
    if isinstance(option, StructuredPlanOption):
        return option.model_dump(exclude_none=True)
    return option


def plan_options_to_jsonable(options: list[PlanOption]) -> list[str | dict[str, str]]:
    return [plan_option_to_jsonable(option) for option in options]


class ReportCreateRequest(BaseModel):
    project: str = Field(min_length=1, max_length=240)
    status: str = Field(default="done", max_length=40)
    summary: str = Field(min_length=1, max_length=4000)
    detail: str = Field(min_length=1)
    needs_input: bool = False
    plan_options: list[PlanOption] = Field(default_factory=list)
    reporting_agent_id: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportResponse(BaseModel):
    report_id: str
    agent_id: str
    project: str
    status: str
    summary: str
    detail: str
    needs_input: bool
    plan_options: list[PlanOption]
    metadata: dict[str, Any]
    created_at: float


class OperatorAssignmentCreate(BaseModel):
    target_agent_id: str = Field(min_length=1, max_length=120)
    operator_fork_id: str | None = Field(default=None, max_length=120)
    fork_track_id: str | None = Field(default=None, max_length=80)
    title: str | None = Field(default=None, max_length=400)
    prompt: str = Field(min_length=1)
    criteria: list[str] = Field(default_factory=list)


class OperatorCampaignStartRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=400)
    objective: str = Field(min_length=1)
    criteria: list[str] = Field(default_factory=list)
    assignments: list[OperatorAssignmentCreate] = Field(default_factory=list)
    delivery: CampaignDelivery = "auto"


class OperatorFollowupRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    target_agent_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1)
    operator_fork_id: str | None = Field(default=None, max_length=120)
    fork_track_id: str | None = Field(default=None, max_length=80)
    assignment_id: str | None = Field(default=None, max_length=120)
    delivery: CampaignDelivery = "auto"


class OperatorAssignmentReportRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    state: AssignmentState
    summary: str = Field(min_length=1, max_length=4000)
    detail: str = Field(min_length=1)


class OperatorCampaignFinishRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    status: CampaignStatus
    summary: str = Field(min_length=1, max_length=4000)
    detail: str = Field(min_length=1)


class OperatorCampaignSuccessorRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)


class OperatorForkEnsureRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    source_caller_agent_id: str = Field(min_length=1, max_length=120)
    fork_agent_id: str | None = Field(default=None, max_length=120)
    fork_track_id: str | None = Field(default=None, max_length=80)
    fork_purpose: str | None = Field(default=None, max_length=80)
    access_mode: str | None = Field(default=None, max_length=80)
    source_cwd: str | None = Field(default=None, max_length=1000)
    work_root: str | None = Field(default=None, max_length=1000)
    campaign_id: str | None = Field(default=None, max_length=120)
    tmux_pane_id: str | None = Field(default=None, max_length=120)
    fork_codex_session_id: str | None = Field(default=None, max_length=120)
    clear_fork_codex_session_id: bool = False
    status: str | None = Field(default=None, max_length=40)
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorForkRebindSourceSessionRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    operator_fork_id: str = Field(min_length=1, max_length=120)
    source_caller_agent_id: str = Field(min_length=1, max_length=120)
    old_source_codex_session_id: str = Field(min_length=1, max_length=120)
    new_source_codex_session_id: str = Field(min_length=1, max_length=120)
    source_cwd: str | None = Field(default=None, max_length=1000)
    codex_host_id: str | None = Field(default=None, max_length=120)
    reason: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorReviewEscalationRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    review_fork_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1)
    route: OperatorReviewEscalationRoute = "primary_idle_edit_fork"
    campaign_id: str | None = Field(default=None, max_length=120)
    assignment_id: str | None = Field(default=None, max_length=120)
    delivery: CampaignDelivery = "auto"


class OperatorProjectSpawnRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    review_fork_id: str = Field(min_length=1, max_length=120)
    project_name: str = Field(min_length=1, max_length=240)
    instructions: str = Field(min_length=1)
    mode: OperatorProjectSpawnMode = "empty"
    target_slug: str | None = Field(default=None, max_length=160)
    campaign_id: str | None = Field(default=None, max_length=120)
    assignment_id: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorProjectSpawnUpdateRequest(BaseModel):
    status: OperatorProjectSpawnStatus
    launched_agent_id: str | None = Field(default=None, max_length=120)
    tmux_pane_id: str | None = Field(default=None, max_length=120)
    error: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorProjectSpawnCancelRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=1000)


class OperatorForkEdgeCreateRequest(BaseModel):
    from_fork_id: str = Field(min_length=1, max_length=120)
    to_fork_id: str = Field(min_length=1, max_length=120)
    edge_type: str = Field(min_length=1, max_length=40)
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorForkEdgeResponse(BaseModel):
    edge_id: str
    from_fork_id: str
    to_fork_id: str
    edge_type: str
    summary: str | None = None
    metadata: dict[str, Any]
    created_at: float


class OperatorKnowledgeLinkCreateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    source_agent_id: str = Field(min_length=1, max_length=120)
    target_agent_id: str = Field(min_length=1, max_length=120)
    link_type: OperatorKnowledgeLinkType = "domain_context"
    status: OperatorKnowledgeLinkStatus = "active"
    source_operator_fork_id: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKnowledgeHandoffProposalRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    source_agent_id: str = Field(min_length=1, max_length=120)
    target_agent_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1)
    link_type: OperatorKnowledgeLinkType = "domain_context"
    source_operator_fork_id: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKnowledgeTurnCreateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    sender_agent_id: str = Field(min_length=1, max_length=120)
    recipient_agent_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1)
    turn_type: OperatorKnowledgeTurnType = "note"
    delivery: CampaignDelivery | Literal["record_only"] = "auto"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKnowledgeTurnApproveRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    delivery: CampaignDelivery | Literal["record_only"] = "auto"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKnowledgeLinkCloseRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    status: OperatorKnowledgeLinkStatus = "closed"
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKnowledgeTurnResponse(BaseModel):
    turn_id: str
    link_id: str
    sender_agent_id: str
    recipient_agent_id: str
    turn_type: str
    message: str
    delivery_status: str
    command_id: str | None = None
    tmux_pane_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any]
    created_at: float


class OperatorKnowledgeLinkResponse(BaseModel):
    link_id: str
    logical_operator_agent_id: str
    operator_agent_id: str
    source_agent_id: str
    target_agent_id: str
    source_operator_fork_id: str | None = None
    link_type: str
    status: str
    summary: str | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    closed_at: float | None = None
    pending_turn_count: int = 0
    latest_turn_at: float | None = None


class OperatorKnowledgeLinkListResponse(BaseModel):
    knowledge_links: list[OperatorKnowledgeLinkResponse] = Field(default_factory=list)


class OperatorKnowledgeContextResponse(BaseModel):
    link: OperatorKnowledgeLinkResponse
    turns: list[OperatorKnowledgeTurnResponse] = Field(default_factory=list)


class OperatorKnowledgeProposalResponse(BaseModel):
    link: OperatorKnowledgeLinkResponse
    turn: OperatorKnowledgeTurnResponse
    handoff: OperatorHandoffResponse | None = None


class OperatorKnowledgeDeliveryResponse(BaseModel):
    link: OperatorKnowledgeLinkResponse
    turn: OperatorKnowledgeTurnResponse
    command: CommandResponse | None = None


class OperatorKbEntryCreateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    scope: OperatorKbScope = "project"
    project: str | None = Field(default=None, max_length=240)
    repo_root: str | None = Field(default=None, max_length=1000)
    git_remote: str | None = Field(default=None, max_length=1000)
    branch: str | None = Field(default=None, max_length=240)
    title: str = Field(min_length=1, max_length=400)
    summary: str = Field(min_length=1, max_length=4000)
    body: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    source_knowledge_link_id: str | None = Field(default=None, max_length=120)
    source_handoff_id: str | None = Field(default=None, max_length=120)
    source_turn_ids: list[str] = Field(default_factory=list)
    stale_after: float | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbFromLinkRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    link_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=400)
    summary: str | None = Field(default=None, max_length=4000)
    scope: OperatorKbScope = "project"
    project: str | None = Field(default=None, max_length=240)
    repo_root: str | None = Field(default=None, max_length=1000)
    git_remote: str | None = Field(default=None, max_length=1000)
    branch: str | None = Field(default=None, max_length=240)
    tags: list[str] = Field(default_factory=list)
    include_turn_ids: list[str] = Field(default_factory=list)
    stale_after: float | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbPromoteRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    redaction_status: OperatorKbRedactionStatus = "clean"
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbUpdateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    scope: OperatorKbScope | None = None
    project: str | None = Field(default=None, max_length=240)
    repo_root: str | None = Field(default=None, max_length=1000)
    git_remote: str | None = Field(default=None, max_length=1000)
    branch: str | None = Field(default=None, max_length=240)
    title: str | None = Field(default=None, max_length=400)
    summary: str | None = Field(default=None, max_length=4000)
    body: str | None = None
    tags: list[str] | None = None
    redaction_status: OperatorKbRedactionStatus | None = None
    stale_after: float | None = None
    expires_at: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbRetireRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbRejectRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbImportRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    bundle: dict[str, Any]
    import_status: OperatorKbStatus = "proposed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbBundleV2ExportRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=240)
    repo_root: str | None = Field(default=None, max_length=1000)
    git_remote: str | None = Field(default=None, max_length=1000)
    parent_bundle_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    limit: int = Field(default=1000, ge=1, le=1000)


class OperatorKbBundleV2Request(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    bundle: dict[str, Any]


class DurableHelperOperatorRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)


class DurableHelperProposalRequest(DurableHelperOperatorRequest):
    source_path: str = Field(min_length=1, max_length=2000)
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str] = Field(min_length=1, max_length=32)
    kb_refs: list[str] = Field(default_factory=list, max_length=64)


class DurableHelperTransitionRequest(DurableHelperOperatorRequest):
    target_status: Literal[
        "reviewed",
        "active",
        "deprecated",
        "retired",
        "rejected",
        "quarantined",
    ]


class DurableHelperImportRequest(DurableHelperOperatorRequest):
    bundle: dict[str, Any]


class DurableHelperInvocationRequest(DurableHelperOperatorRequest):
    scope: str = Field(min_length=1, max_length=120)


class DynamicResourceOperatorRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)


class DynamicResourceCreateRequest(DynamicResourceOperatorRequest):
    resource_class: str = Field(min_length=1, max_length=80)
    requester_agent_id: str = Field(min_length=1, max_length=120)
    campaign_id: str | None = Field(default=None, max_length=120)
    handoff_id: str | None = Field(default=None, max_length=120)
    workspace_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    spec: dict[str, Any]
    workerbee_binding_id: str | None = Field(default=None, max_length=120)
    workerbee_backend_generation: str | None = Field(default=None, max_length=120)
    provider_capability_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    ttl_seconds: int = Field(default=3600, ge=60, le=86400, strict=True)
    quota: int = Field(default=1, ge=1, le=1_000_000_000, strict=True)
    max_uses: int = Field(default=1, ge=1, le=100, strict=True)
    approval_mode: Literal["manual", "campaign_lease", "auto"] = "manual"
    delegation_lease_id: str | None = Field(default=None, max_length=120)
    policy: Literal["public", "trusted_rosie"] = "public"
    runtime_state: Literal["stopped", "running"] = "stopped"


class DynamicResourceApprovalRequest(DynamicResourceOperatorRequest):
    approval_method: Literal["agent-pbx-tui-f8"]
    confirmation: str = Field(min_length=1, max_length=200)
    approve: bool = Field(default=True, strict=True)


class DynamicResourceUseRequest(DynamicResourceOperatorRequest):
    requester_agent_id: str = Field(min_length=1, max_length=120)


class DynamicResourceReleaseRequest(DynamicResourceOperatorRequest):
    revoke: bool = Field(default=False, strict=True)


class OperatorKbSourceResponse(BaseModel):
    source_row_id: str
    kb_id: str
    source_type: str
    source_id: str
    metadata: dict[str, Any]
    created_at: float


class OperatorKbEntryResponse(BaseModel):
    kb_id: str
    scope: str
    project: str | None = None
    repo_root: str | None = None
    git_remote: str | None = None
    branch: str | None = None
    title: str
    summary: str
    body: str
    tags: list[str]
    status: str
    redaction_status: str
    created_by_operator_agent_id: str
    created_by_agent_id: str
    source_knowledge_link_id: str | None = None
    source_handoff_id: str | None = None
    source_turn_ids: list[str]
    stale_after: float | None = None
    expires_at: float | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    promoted_at: float | None = None
    retired_at: float | None = None
    imported_at: float | None = None
    sources: list[OperatorKbSourceResponse] = Field(default_factory=list)


class OperatorKbListResponse(BaseModel):
    kb_entries: list[OperatorKbEntryResponse] = Field(default_factory=list)


class OperatorKbContextRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=4000)
    target_operator_agent_id: str | None = Field(default=None, max_length=120)
    scope: OperatorKbScope | None = None
    project: str | None = Field(default=None, max_length=240)
    repo_root: str | None = Field(default=None, max_length=1000)
    tags: list[str] = Field(default_factory=list)
    include_expired: bool = False
    include_proposed: bool = False
    semantic: bool = True
    limit: int = Field(default=5, ge=1, le=50)


class OperatorKbContextResponse(BaseModel):
    operator_agent_id: str
    logical_operator_agent_id: str
    target_operator_agent_id: str | None = None
    query: str
    scope: str | None = None
    project: str | None = None
    repo_root: str | None = None
    tags: list[str]
    include_expired: bool
    include_proposed: bool
    retrieval_mode: str = "keyword"
    semantic: bool = False
    semantic_match_count: int = 0
    query_id: str | None = None
    satisfied_by_kb: bool
    match_count: int
    kb_entries: list[OperatorKbEntryResponse] = Field(default_factory=list)


class OperatorKbQueryMatchResponse(BaseModel):
    match_id: str
    query_id: str
    kb_id: str | None = None
    rank: int
    retrieval_mode: str
    retrieval_sources: list[str] = Field(default_factory=list)
    score: float | None = None
    keyword_rank: int | None = None
    semantic_score: float | None = None
    semantic_chunks: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class OperatorKbFeedbackResponse(BaseModel):
    feedback_id: str
    query_id: str | None = None
    match_id: str | None = None
    kb_id: str | None = None
    logical_operator_agent_id: str
    operator_agent_id: str
    feedback: str
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float


class OperatorKbQueryResponse(BaseModel):
    query_id: str
    logical_operator_agent_id: str
    operator_agent_id: str
    target_operator_agent_id: str | None = None
    query: str
    scope: str | None = None
    project: str | None = None
    repo_root: str | None = None
    tags: list[str] = Field(default_factory=list)
    include_expired: bool
    include_proposed: bool
    semantic: bool
    retrieval_mode: str
    retrieval_provider: str
    retrieval_model: str
    index_version: str
    source: str
    limit_count: int
    match_count: int
    semantic_match_count: int
    satisfied_by_kb: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    matches: list[OperatorKbQueryMatchResponse] = Field(default_factory=list)
    feedback: list[OperatorKbFeedbackResponse] = Field(default_factory=list)
    feedback_summary: dict[str, int] = Field(default_factory=dict)


class OperatorKbQueryListResponse(BaseModel):
    queries: list[OperatorKbQueryResponse] = Field(default_factory=list)


class OperatorKbFeedbackRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    match_id: str | None = Field(default=None, max_length=120)
    kb_id: str | None = Field(default=None, max_length=120)
    feedback: OperatorKbFeedbackType
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbCompileReportRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    report_id: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=20, ge=1, le=100)


class OperatorKbCompileReportResponse(BaseModel):
    operator_agent_id: str
    logical_operator_agent_id: str
    report_id: str
    proposed_count: int
    skipped_count: int
    entries: list[OperatorKbEntryResponse] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class OperatorKbIndexJobRequest(BaseModel):
    operation: OperatorKbIndexJobOperation = "rebuild"
    kb_id: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbIndexJobResponse(BaseModel):
    job_id: str
    kb_id: str | None = None
    operation: str
    status: str
    error: str | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    completed_at: float | None = None


class OperatorKbIndexJobListResponse(BaseModel):
    index_jobs: list[OperatorKbIndexJobResponse] = Field(default_factory=list)


class OperatorKbIndexRunResponse(BaseModel):
    processed_count: int
    failed_count: int
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    failed: list[dict[str, Any]] = Field(default_factory=list)


class OperatorKbExportResponse(BaseModel):
    format: str
    version: int
    exported_at: float
    criteria: dict[str, Any]
    entries: list[OperatorKbEntryResponse] = Field(default_factory=list)


class OperatorKbImportResponse(BaseModel):
    imported_count: int
    skipped_count: int
    entries: list[OperatorKbEntryResponse] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class OperatorKbSeedRunCreateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    seed_type: str = Field(default="operator_self_seed", min_length=1, max_length=80)
    scope: OperatorKbScope = "project"
    project: str | None = Field(default=None, max_length=240)
    repo_root: str | None = Field(default=None, max_length=1000)
    git_remote: str | None = Field(default=None, max_length=1000)
    branch: str | None = Field(default=None, max_length=240)
    prompt: str | None = None
    delivery: CampaignDelivery = "auto"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbSeedRunUpdateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    status: OperatorKbSeedRunStatus
    summary: str | None = Field(default=None, max_length=4000)
    error: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorKbSeedRunResponse(BaseModel):
    seed_run_id: str
    logical_operator_agent_id: str
    source_operator_agent_id: str
    seed_type: str
    scope: str
    project: str | None = None
    repo_root: str | None = None
    git_remote: str | None = None
    branch: str | None = None
    prompt: str
    status: str
    command_id: str | None = None
    tmux_pane_id: str | None = None
    delivery_status: str | None = None
    error: str | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    completed_at: float | None = None


class OperatorKbSeedRunListResponse(BaseModel):
    seed_runs: list[OperatorKbSeedRunResponse] = Field(default_factory=list)


class OperatorKbSeedRunDeliveryResponse(BaseModel):
    seed_run: OperatorKbSeedRunResponse
    command: CommandResponse | None = None


class OperatorHandoffCreateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    source_agent_id: str = Field(min_length=1, max_length=120)
    target_operator_agent_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1)
    objective: str | None = Field(default=None, max_length=4000)
    source_operator_fork_id: str | None = Field(default=None, max_length=120)
    target_caller_agent_id: str | None = Field(default=None, max_length=120)
    target_operator_fork_id: str | None = Field(default=None, max_length=120)
    knowledge_link_id: str | None = Field(default=None, max_length=120)
    knowledge_turn_id: str | None = Field(default=None, max_length=120)
    allowed_mutation_scope: str | None = Field(default=None, max_length=4000)
    execution_scope: dict[str, Any] = Field(default_factory=dict)
    required_artifacts: list[Any] = Field(default_factory=list)
    artifact_bundle: list[Any] = Field(default_factory=list)
    expires_at: float | None = None
    needs_ack: bool = True
    summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorHandoffApproveRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    delivery: CampaignDelivery | Literal["record_only"] = "auto"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorHandoffPreflightRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    delivery: CampaignDelivery | Literal["record_only"] = "auto"


class OperatorHandoffCancelRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    reason: OperatorHandoffCancelReason
    detail: str | None = Field(default=None, max_length=4000)
    superseded_by_handoff_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorHandoffAckRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    status: OperatorHandoffStatus = "acknowledged"
    summary: str = Field(min_length=1, max_length=4000)
    detail: str | None = None
    artifact_bundle: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorHandoffUpdateRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    status: OperatorHandoffStatus
    summary: str = Field(min_length=1, max_length=4000)
    detail: str | None = None
    artifact_bundle: list[Any] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorHandoffCompletionAckRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)


class OperatorHandoffCompletionDeliveryResponse(BaseModel):
    completion_delivery_id: str
    handoff_id: str
    handoff_version: str
    source_operator_agent_id: str
    source_operator_fork_id: str | None = None
    recipient_agent_id: str
    target_operator_agent_id: str
    terminal_status: str
    summary: str
    artifact_kinds: list[str] = Field(default_factory=list)
    artifact_bundle_digest: str
    delivery_mode: str | None = None
    delivery_status: str
    command_id: str | None = None
    attempt_count: int = 0
    last_attempt_at: float | None = None
    last_error: str | None = None
    acknowledged_at: float | None = None
    acknowledged_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    updated_at: float


class OperatorHandoffCompletionListResponse(BaseModel):
    completion_deliveries: list[OperatorHandoffCompletionDeliveryResponse] = Field(
        default_factory=list
    )


class OperatorHandoffResponse(BaseModel):
    handoff_id: str
    logical_operator_agent_id: str
    source_operator_agent_id: str
    target_operator_agent_id: str
    source_agent_id: str
    source_operator_fork_id: str | None = None
    target_caller_agent_id: str | None = None
    target_operator_fork_id: str | None = None
    knowledge_link_id: str | None = None
    knowledge_turn_id: str | None = None
    objective: str
    message: str
    allowed_mutation_scope: str | None = None
    execution_scope: dict[str, Any] = Field(default_factory=dict)
    required_artifacts: list[Any]
    artifact_bundle: list[Any]
    status: str
    needs_ack: bool
    expires_at: float | None = None
    time_remaining_seconds: float | None = None
    expired: bool = False
    safe_to_start: bool = True
    acknowledged_at: float | None = None
    started_at: float | None = None
    completed_at: float | None = None
    command_id: str | None = None
    tmux_pane_id: str | None = None
    delivery_status: str | None = None
    delivery_evidence: dict[str, Any]
    error: str | None = None
    summary: str | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    completion_delivery: OperatorHandoffCompletionDeliveryResponse | None = None


class OperatorHandoffCancelResponse(BaseModel):
    handoff: OperatorHandoffResponse
    previous_status: str
    idempotent: bool = False


class OperatorHandoffListResponse(BaseModel):
    handoffs: list[OperatorHandoffResponse] = Field(default_factory=list)


class OperatorHandoffDeliveryResponse(BaseModel):
    handoff: OperatorHandoffResponse
    command: CommandResponse | None = None


class OperatorHandoffActionResponse(BaseModel):
    handoff_id: str
    logical_operator_agent_id: str
    target_operator_agent_id: str
    target_agent_id: str
    target_operator_fork_id: str
    campaign_id: str
    action_class: str
    state: str
    next_action: str | None = None
    scope_sha256: str
    execution_scope: dict[str, Any] = Field(default_factory=dict)
    transaction_id: str | None = None
    reason: str | None = None
    updated_at: float | None = None


class OperatorHandoffActionListResponse(BaseModel):
    actions: list[OperatorHandoffActionResponse] = Field(default_factory=list)


class OperatorHandoffExecutionPreflightResponse(BaseModel):
    ok: bool
    handoff: OperatorHandoffResponse
    action: OperatorHandoffActionResponse | None = None
    transaction: dict[str, Any] | None = None
    participant_operator_agent_id: str


class OperatorHandoffActionReceiptRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    step: Literal["stop", "broker_review", "broker_commit", "resume", "validate"]
    outcome: Literal["started", "succeeded", "failed", "restored"]
    actor: str = Field(min_length=1, max_length=240)
    evidence: dict[str, Any] = Field(default_factory=dict)


class OperatorHandoffActionTransactionResponse(BaseModel):
    action: OperatorHandoffActionResponse | None = None
    transaction: dict[str, Any]


class ProtectedProviderDiagnosticRequest(BaseModel):
    operator_agent_id: str = Field(min_length=1, max_length=120)
    handoff_id: str = Field(min_length=1, max_length=120)
    approval_id: str = Field(min_length=1, max_length=160)
    envelope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_generation: str = Field(min_length=1, max_length=160)
    provider_generation: str = Field(min_length=1, max_length=160)
    provider_receipt_id: str = Field(min_length=1, max_length=160)
    provider_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class OperatorBrokerCheckpointRequest(BaseModel):
    root_operator_agent_id: str = Field(min_length=1, max_length=120)
    target_fork_agent_id: str = Field(min_length=1, max_length=240)
    handoff_id: str = Field(min_length=1, max_length=120)


class OperatorBrokerCheckpointCancelRequest(BaseModel):
    root_operator_agent_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class OperatorBrokerCheckpointValidateRequest(BaseModel):
    root_operator_agent_id: str = Field(min_length=1, max_length=120)
    action_transaction_id: str = Field(min_length=1, max_length=120)


class OperatorBrokerCheckpointClaimRequest(BaseModel):
    executor_id: str = Field(min_length=1, max_length=240)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class OperatorBrokerCheckpointTransitionRequest(BaseModel):
    executor_id: str = Field(min_length=1, max_length=240)
    status: Literal["executing", "succeeded", "failed"]
    expected_statuses: list[Literal["claimed", "executing"]] = Field(
        min_length=1,
        max_length=2,
    )
    action_transaction_id: str | None = Field(default=None, max_length=120)
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=120)
    error: str | None = Field(default=None, max_length=4000)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class OperatorBrokerCheckpointResponse(BaseModel):
    request_id: str
    root_operator_agent_id: str
    target_fork_agent_id: str
    handoff_id: str
    campaign_id: str | None = None
    assignment_id: str | None = None
    action_transaction_id: str | None = None
    workspace_set_digest: str
    scope_sha256: str
    expected_git_parent: str
    expected_git_parents: list[str] = Field(default_factory=list)
    expected_git_tree: str | None = None
    commit_subject: str
    exact_paths: list[str]
    status: str
    executor_id: str | None = None
    claim_expires_at: float | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error: str | None = None
    created_at: float
    updated_at: float
    expires_at: float
    completed_at: float | None = None
    canceled_at: float | None = None
    approval_report_id: str | None = None
    validated_by_operator_agent_id: str | None = None
    validated_at: float | None = None
    validation_sha256: str | None = None
    receipt_summary: str | None = None


class OperatorBrokerCheckpointListResponse(BaseModel):
    requests: list[OperatorBrokerCheckpointResponse] = Field(default_factory=list)


class OperatorHandoffPreflightResponse(BaseModel):
    handoff_id: str
    operator_agent_id: str
    target_operator_agent_id: str
    target_caller_agent_id: str | None = None
    checked_at: float
    requested_delivery: str
    resolved_delivery: str
    ok: bool
    delivery_possible: bool
    status: str
    reason: str | None = None
    retryable: bool = False
    target: dict[str, Any] = Field(default_factory=dict)
    target_fork: dict[str, Any] | None = None
    pane: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    kb_context: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorDelegationLeaseCreateRequest(BaseModel):
    source_operator_agent_id: str = Field(min_length=1, max_length=120)
    target_operator_agent_ids: list[str] = Field(min_length=1, max_length=32)
    campaign_ids: list[str] = Field(min_length=1, max_length=32)
    workspace_set_digest: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    allowed_action_classes: list[str] = Field(min_length=1, max_length=16)
    allowed_roots: list[str] = Field(default_factory=list, max_length=64)
    exact_paths: list[str] = Field(default_factory=list, max_length=256)
    denied_actions: list[str] = Field(default_factory=list, max_length=64)
    max_uses: int = Field(default=8, ge=1, le=100)
    ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    requested_by: str = Field(default="agent-pbx-tui", min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorDelegationLeaseDecisionRequest(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=1, max_length=255)


class OperatorDelegationLeaseRevokeRequest(BaseModel):
    revoked_by: str = Field(min_length=1, max_length=255)


class OperatorDelegationLeaseResponse(BaseModel):
    lease_id: str
    source_operator_agent_id: str
    target_operator_agent_ids: list[str]
    campaign_ids: list[str]
    workspace_set_digest: str
    allowed_action_classes: list[str]
    allowed_roots: list[str]
    exact_paths: list[str]
    denied_actions: list[str]
    max_uses: int
    use_count: int
    uses_remaining: int
    status: str
    requested_by: str
    approved_by: str | None = None
    created_at: float
    approved_at: float | None = None
    expires_at: float
    revoked_at: float | None = None
    expired: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class OperatorDelegationLeaseListResponse(BaseModel):
    leases: list[OperatorDelegationLeaseResponse] = Field(default_factory=list)


class OperatorForkResponse(BaseModel):
    operator_fork_id: str
    logical_operator_agent_id: str
    fork_agent_id: str
    source_caller_agent_id: str
    source_codex_session_id: str
    fork_track_id: str = "default"
    fork_purpose: str = "edit"
    access_mode: str = "edit"
    source_cwd: str | None = None
    work_root: str | None = None
    fork_codex_session_id: str | None = None
    campaign_id: str | None = None
    cwd: str
    codex_home: str | None = None
    codex_host_id: str | None = None
    tmux_pane_id: str | None = None
    status: str
    summary: str | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    last_used_at: float
    completed_at: float | None = None
    edges: list[OperatorForkEdgeResponse] = Field(default_factory=list)


class OperatorForkListResponse(BaseModel):
    forks: list[OperatorForkResponse] = Field(default_factory=list)


class OperatorProjectSpawnResponse(BaseModel):
    spawn_request_id: str
    logical_operator_agent_id: str
    operator_agent_id: str
    review_fork_id: str
    review_fork_agent_id: str
    source_caller_agent_id: str
    source_cwd: str
    target_parent: str
    target_slug: str
    target_path: str
    project_name: str
    mode: OperatorProjectSpawnMode
    instructions: str
    status: str
    launched_agent_id: str | None = None
    tmux_pane_id: str | None = None
    error: str | None = None
    campaign_id: str | None = None
    assignment_id: str | None = None
    metadata: dict[str, Any]
    created_at: float
    updated_at: float
    completed_at: float | None = None


class OperatorProjectSpawnListResponse(BaseModel):
    project_spawns: list[OperatorProjectSpawnResponse] = Field(default_factory=list)


class OperatorCampaignEventResponse(BaseModel):
    event_id: int
    campaign_id: str
    assignment_id: str | None = None
    operator_agent_id: str
    target_agent_id: str | None = None
    event_type: str
    summary: str
    detail: dict[str, Any]
    report_id: str | None = None
    command_id: str | None = None
    created_at: float


class OperatorCampaignAssignmentResponse(BaseModel):
    assignment_id: str
    campaign_id: str
    target_agent_id: str
    operator_fork_id: str | None = None
    fork_track_id: str | None = None
    title: str
    prompt: str
    criteria: list[str]
    state: str
    last_report_id: str | None = None
    last_command_id: str | None = None
    created_at: float
    updated_at: float
    completed_at: float | None = None


class OperatorCampaignResponse(BaseModel):
    campaign_id: str
    predecessor_campaign_id: str | None = None
    operator_agent_id: str
    title: str
    objective: str
    criteria: list[str]
    status: str
    summary: str | None = None
    created_at: float
    updated_at: float
    completed_at: float | None = None
    assignments: list[OperatorCampaignAssignmentResponse] = Field(default_factory=list)
    events: list[OperatorCampaignEventResponse] = Field(default_factory=list)


class OperatorCampaignListResponse(BaseModel):
    campaigns: list[OperatorCampaignResponse] = Field(default_factory=list)


class CommandCreateRequest(BaseModel):
    agent_id: str | None = Field(default=None, max_length=120)
    type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandAckRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    result: dict[str, Any] = Field(default_factory=dict)


class CommandResponse(BaseModel):
    command_id: str
    agent_id: str | None
    type: CommandType
    payload: dict[str, Any]
    status: str
    created_at: float
    claimed_at: float | None
    acked_at: float | None
    result: dict[str, Any] | None


class EventResponse(BaseModel):
    event_id: int
    type: str
    subject_id: str | None
    payload: dict[str, Any]
    created_at: float


class EventStreamEnvelope(BaseModel):
    api_version: Literal["agent-pbx.events/v2"]
    kind: Literal["snapshot", "events"]
    cursor: int
    oldest_event_id: int | None = None
    latest_event_id: int | None = None
    reset_required: bool = False
    more_available: bool = False
    events: list[EventResponse] = Field(default_factory=list)
    state: dict[str, Any] | None = None
    server_time: float


class RemoteTokenIssueRequest(BaseModel):
    role: Literal["observer", "controller"] = "observer"
    label: str = Field(default="remote-tui", min_length=1, max_length=80)
    ttl_seconds: int = Field(default=3600, ge=60, le=2_592_000)
    audience: str | None = Field(default=None, min_length=1, max_length=120)
    client_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    scopes: list[str] = Field(default_factory=list, max_length=32)
    allowed_agent_ids: list[str] = Field(default_factory=list, max_length=200)


class RemoteClientStateRequest(BaseModel):
    view_state: dict[str, Any] = Field(default_factory=dict)
    cursor: int | None = Field(default=None, ge=0)


class RemoteControlRequest(BaseModel):
    preview_token: str | None = Field(default=None, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ThreadItemResponse(BaseModel):
    item_id: str
    kind: Literal["report", "command"]
    agent_id: str
    created_at: float
    status: str
    title: str
    body: str
    metadata: dict[str, Any]


class WorkerBeeStatusResponse(BaseModel):
    configured: bool
    available: bool
    agent_id: str
    agent_project: str | None = None
    cwd: str | None = None
    workerbee_bin: str | None = None
    checked_at: float
    project: str | None = None
    mode: str | None = None
    running: bool | None = None
    status_kind: str | None = None
    dashboard_url: str | None = None
    state_dir: str | None = None
    description: str | None = None
    error: dict[str, Any] | None = None
    project_status: dict[str, Any] | None = None
    project_card: dict[str, Any] | None = None
    global_dashboard: dict[str, Any] | None = None
    dashboard_error: dict[str, Any] | None = None
    app_status: dict[str, Any] | None = None
    latest_deployment: dict[str, Any] | None = None
    status_source: str = "host_cli"
    selected_runtime: dict[str, Any] | None = None
    workspace_binding: dict[str, Any] | None = None
    workspace_policy: dict[str, Any] | None = None


class PullRequestStatusResponse(BaseModel):
    configured: bool
    available: bool
    agent_id: str | None = None
    agent_project: str | None = None
    cwd: str | None = None
    gh_bin: str | None = None
    merge_enabled: bool = False
    allowed_repos: list[str] = Field(default_factory=list)
    checked_at: float
    repo: str | None = None
    repo_url: str | None = None
    error: dict[str, Any] | None = None


class PullRequestSummary(BaseModel):
    number: int
    title: str
    state: str
    is_draft: bool = False
    author: str | None = None
    head_ref: str
    base_ref: str
    updated_at: str | int | float | None = None
    url: str
    labels: list[str] = Field(default_factory=list)
    review_decision: str | None = None
    checks: dict[str, Any] = Field(default_factory=dict)


class PullRequestListResponse(PullRequestStatusResponse):
    pull_requests: list[PullRequestSummary] = Field(default_factory=list)


class PullRequestDetailResponse(PullRequestSummary):
    repo: str | None = None
    repo_url: str | None = None
    agent_id: str | None = None
    cwd: str | None = None
    created_at: str | int | float | None = None
    body: str = ""
    merge_state_status: str | None = None
    mergeable: str | bool | None = None
    files: list[dict[str, Any]] = Field(default_factory=list)
    commits: list[dict[str, Any]] = Field(default_factory=list)


class PullRequestActionRequest(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    queue: bool = True


class PullRequestActionResponse(BaseModel):
    ok: bool = True
    action: str
    agent_id: str
    number: int
    repo: str | None = None
    command: CommandResponse | None = None
    message: str | None = None
    prompt: str | None = None


class PullRequestMergeRequest(BaseModel):
    method: Literal["squash", "merge", "rebase"] = "squash"
    confirm: str = Field(min_length=1, max_length=120)


class PullRequestMergeResponse(BaseModel):
    ok: bool = True
    merged: bool = False
    number: int
    method: str
    repo: str | None = None
    url: str | None = None
    output: str = ""


class IssueStatusResponse(BaseModel):
    configured: bool
    available: bool
    agent_id: str
    agent_project: str | None = None
    cwd: str | None = None
    gh_bin: str = "gh"
    close_enabled: bool = False
    allowed_repos: list[str] = Field(default_factory=list)
    checked_at: float
    repo: str | None = None
    repo_url: str | None = None
    error: dict[str, Any] | None = None


class IssueSummary(BaseModel):
    number: int
    title: str
    state: str
    author: str | None = None
    url: str = ""
    labels: list[str] = Field(default_factory=list)
    assignees: list[str] = Field(default_factory=list)
    milestone: str | None = None
    updated_at: str | int | float | None = None
    created_at: str | int | float | None = None
    closed: bool = False
    closed_at: str | int | float | None = None


class IssueListResponse(IssueStatusResponse):
    state: str = "open"
    issues: list[IssueSummary] = Field(default_factory=list)


class IssueDetailResponse(IssueSummary):
    repo: str | None = None
    repo_url: str | None = None
    agent_id: str | None = None
    cwd: str | None = None
    body: str = ""
    comments: list[dict[str, Any]] = Field(default_factory=list)


class IssueActionRequest(BaseModel):
    message: str | None = Field(default=None, max_length=2000)
    queue: bool = True


class IssueActionResponse(BaseModel):
    ok: bool = True
    action: str
    agent_id: str
    number: int
    repo: str | None = None
    command: CommandResponse | None = None
    message: str | None = None
    prompt: str | None = None


class IssueClearRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=12000)
    confirm: str = Field(min_length=1, max_length=120)


class IssueClearResponse(BaseModel):
    ok: bool = True
    closed: bool = False
    number: int
    repo: str | None = None
    url: str | None = None
    output: str = ""


class JoplinStatusResponse(BaseModel):
    configured: bool
    available: bool
    api_url: str | None = None
    notebook: str
    joplin_bin: str | None = None
    profile: str | None = None
    sync_on_write: bool = False
    profile_owner_mode: str = "external"
    webdav_url: str | None = None
    webdav_username: str | None = None
    webdav_password_configured: bool = False
    checked_at: float
    root_notebook_id: str | None = None
    sync: dict[str, Any] | None = None
    encryption: dict[str, Any] | None = None
    encryption_error: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class JoplinSyncJobResponse(BaseModel):
    sync_id: str
    status: str
    reason: str
    agent_id: str | None = None
    note_id: str | None = None
    error: str | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0
    profile_key: str = "default"
    note_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    generation: int = 1
    progress: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    coalesced: bool = False


class JoplinSyncLaneResponse(BaseModel):
    current_status: str = "ready"
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    latest: JoplinSyncJobResponse | None = None
    latest_success: JoplinSyncJobResponse | None = None
    latest_error: JoplinSyncJobResponse | None = None


class JoplinSyncStatusResponse(BaseModel):
    enabled: bool
    sync_on_write: bool = False
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    latest: JoplinSyncJobResponse | None = None
    latest_success: JoplinSyncJobResponse | None = None
    latest_error: JoplinSyncJobResponse | None = None
    write_sync: JoplinSyncLaneResponse = Field(
        default_factory=JoplinSyncLaneResponse
    )
    profile_validation: JoplinSyncLaneResponse = Field(
        default_factory=JoplinSyncLaneResponse
    )


class JoplinNoteSummary(BaseModel):
    id: str
    parent_id: str
    title: str
    created_time: int | float | None = None
    updated_time: int | float | None = None
    user_updated_time: int | float | None = None
    encryption_applied: int = 0
    decryption_pending: bool = False
    is_conflict: bool = False
    conflict_original_id: str | None = None


class JoplinNoteResponse(JoplinNoteSummary):
    body: str = ""
    revision: str = ""
    title_hash: str = ""
    body_hash: str = ""


class JoplinCopyRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    body: str | None = None
    thread_item_id: str | None = Field(default=None, max_length=160)
    report_id: str | None = Field(default=None, max_length=120)


class JoplinNoteCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    body: str = ""


class JoplinNoteUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    body: str | None = None
    base_revision: str | None = Field(default=None, max_length=128)
    base_title: str | None = Field(default=None, max_length=240)
    base_body: str | None = None
    edit_id: str | None = Field(default=None, max_length=120)
    force: bool = False


class JoplinEditStartRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=160)


class JoplinEditDraftRequest(BaseModel):
    title: str = Field(max_length=240)
    body: str


class JoplinEditSessionResponse(BaseModel):
    edit: dict[str, Any]
    note: JoplinNoteResponse
    conflict: dict[str, Any] | None = None


class JoplinConflictResolveRequest(BaseModel):
    resolution: Literal[
        "review_merge",
        "keep_joplin",
        "save_conflict_copy",
        "overwrite",
    ]


class JoplinNativeConflictPromoteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    expected_revision: str = Field(min_length=1, max_length=128)
    expected_original_id: str | None = Field(default=None, max_length=120)


class JoplinNativeConflictPromoteResponse(BaseModel):
    source: JoplinNoteResponse
    promoted: JoplinNoteResponse


class JoplinLogAppendRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)


class JoplinLogResponse(BaseModel):
    log_id: str
    agent_id: str
    project: str
    session_id: str
    note_id: str
    title: str
    active: bool
    started_at: float
    stopped_at: float | None = None
    updated_at: float
    already_active: bool = False


class JoplinAsset(BaseModel):
    name: str = Field(min_length=1, max_length=240)
    path: str | None = None
    content_type: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class JoplinDocumentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=240)
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1)
    session_id: str | None = Field(default=None, max_length=160)
    mermaid_blocks: list[str] = Field(default_factory=list)
    assets: list[JoplinAsset] = Field(default_factory=list)


class FileEntry(BaseModel):
    name: str
    path: str
    kind: Literal["directory", "file", "other"]
    size: int | None = None
    mtime: float | None = None
    extension: str | None = None
    mime_type: str | None = None
    is_text: bool = False
    is_image: bool = False
    is_gif: bool = False


class FileListResponse(BaseModel):
    agent_id: str
    cwd: str | None = None
    path: str
    parent: str | None = None
    entries: list[FileEntry] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class FilePreviewResponse(BaseModel):
    agent_id: str
    cwd: str | None = None
    path: str
    kind: Literal["file", "directory", "error"]
    size: int | None = None
    mtime: float | None = None
    extension: str | None = None
    mime_type: str | None = None
    is_text: bool = False
    is_image: bool = False
    is_gif: bool = False
    image_width: int | None = None
    image_height: int | None = None
    image_preview: str | None = None
    image_preview_ansi: str | None = None
    image_preview_format: str | None = None
    text: str | None = None
    truncated: bool = False
    error: dict[str, Any] | None = None


class FileDocumentResponse(BaseModel):
    agent_id: str
    cwd: str | None = None
    path: str
    kind: Literal["file", "directory", "error"]
    size: int | None = None
    mtime: float | None = None
    extension: str | None = None
    mime_type: str | None = None
    is_text: bool = False
    text: str | None = None
    sha256: str | None = None
    encoding: str | None = None
    newline: str | None = None
    language: str | None = None
    line_count: int | None = None
    read_only: bool = True
    saved: bool = False
    error: dict[str, Any] | None = None


class FileDocumentWriteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    text: str
    previous_sha256: str | None = Field(default=None, max_length=128)
    previous_mtime: float | None = None
    create: bool = False


class FileCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    kind: Literal["file", "directory"]
    text: str = ""


class FileMoveRequest(BaseModel):
    source: str = Field(min_length=1, max_length=1000)
    destination: str = Field(min_length=1, max_length=1000)
    previous_sha256: str | None = Field(default=None, max_length=128)
    previous_mtime: float | None = None


class FileDeleteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    preview_token: str = Field(min_length=1, max_length=128)
    actor: str = Field(default="operator", min_length=1, max_length=160)
    confirm_tracked: bool = False
    retention_days: float = Field(default=7.0, ge=0.0, le=3650.0)


class FileTrashRestoreRequest(BaseModel):
    trash_id: str = Field(min_length=1, max_length=120)


class FileTrashPruneApplyRequest(BaseModel):
    preview_token: str = Field(min_length=1, max_length=128)
    cutoff: float


class FileTrashPruneUndoRequest(BaseModel):
    batch_id: str = Field(min_length=1, max_length=120)


class FileSearchResult(BaseModel):
    path: str
    line: int
    column: int = 1
    snippet: str


class FileSearchResponse(BaseModel):
    agent_id: str
    cwd: str | None = None
    path: str
    query: str
    results: list[FileSearchResult] = Field(default_factory=list)
    truncated: bool = False
    backend: str = "none"
    error: dict[str, Any] | None = None


class FileDiagnosticsRequest(BaseModel):
    path: str = Field(default=".", min_length=1, max_length=1000)
    tool: Literal["auto", "ruff", "mypy"] = "auto"


class TerminalEditorStartRequest(BaseModel):
    path: str | None = Field(default=None, min_length=1, max_length=1000)
    profile_id: str | None = Field(default=None, max_length=64)
    line: int | None = Field(default=None, ge=1, le=10_000_000)


class WorkerBeeSwitchPreviewRequest(BaseModel):
    target: Literal["oss", "platform_debug"]


class EvidenceExportRequest(BaseModel):
    kind: Literal["thread", "campaign", "evidence_case"]
    subject_id: str = Field(min_length=1, max_length=240)
    scope: Literal["all", "item", "marked"] = "all"
    item_ids: list[str] = Field(default_factory=list, max_length=200)
    categories: list[
        Literal[
            "event_journal",
            "remote_audit",
            "model_elevation",
            "git_broker",
            "lifecycle",
            "runtime",
            "handoff",
            "workerbee_workspace",
            "workerbee_run",
            "workerbee_resource",
        ]
    ] = Field(default_factory=list, max_length=10)
    formats: list[Literal["json", "markdown", "ndjson"]] = Field(
        default_factory=lambda: ["json", "markdown", "ndjson"],
        min_length=1,
        max_length=3,
    )


class EvidenceVerifyRequest(BaseModel):
    export_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class EvidenceRetentionPreviewRequest(BaseModel):
    older_than: float = Field(ge=0.0)


class WorkerBeeSwitchApplyRequest(BaseModel):
    transaction_id: str = Field(min_length=24, max_length=24)
    preview_token: str = Field(min_length=16, max_length=256)
    confirm_target: Literal["oss", "platform_debug"]
    confirm_privileged: bool = False


class WorkerBeeSwitchRollbackRequest(BaseModel):
    transaction_id: str = Field(min_length=24, max_length=24)
    confirm: bool = False


class WorkerBeeWorkspacePrepareRequest(BaseModel):
    project: str | None = Field(default=None, min_length=1, max_length=128)
    source_root: str | None = Field(default=None, min_length=1, max_length=4096)
    include_paths: list[str] = Field(default_factory=list, max_length=64)
    source_mode: Literal[
        "native-direct", "snapshot-import", "trusted-direct-bind", "managed-clone"
    ] | None = None
    trusted_direct_bind_receipt_id: str = Field(default="", max_length=120)
    trusted_direct_bind_receipt_sha256: str = Field(
        default="", pattern=r"^(?:|[0-9a-f]{64})$"
    )


class WorkerBeeGatewayLeaseRequest(BaseModel):
    codex_session_id: str = Field(default="", max_length=240)
    profile: Literal[
        "observe-local", "review-readonly", "local-deploy", "trusted-workbench"
    ]
    workspace_binding_id: str = Field(min_length=1, max_length=160)
    ttl_seconds: float | None = Field(default=None, ge=30, le=86_400)


class WorkerBeeWorkspacePolicyRequest(BaseModel):
    policy: Literal["pinned", "same-repository", "scoped-workbench"]
    request_mode: Literal["approval", "automatic", "disabled"]
    allowed_roots: list[str] = Field(default_factory=list, max_length=32)
    managed_scratch_root: str | None = Field(default=None, max_length=4096)
    profile_label: str | None = Field(default=None, max_length=120)


class WorkerBeeSnapshotPolicyRequest(BaseModel):
    source_root: str | None = Field(default=None, max_length=4096)
    policy: Literal["tracked", "approved-paths", "safe-working-tree"]
    include_paths: list[str] = Field(default_factory=list, max_length=64)
    max_bytes: int | None = Field(default=None, ge=1)
    max_files: int | None = Field(default=None, ge=1)
    max_file_bytes: int | None = Field(default=None, ge=1)
    profile_label: str | None = Field(default=None, max_length=120)


class WorkerBeeAccessProfileRequest(BaseModel):
    profile: Literal["observe-local", "local-deploy", "trusted-workbench"] | None = None
    access_profile: Literal[
        "observe-local", "local-deploy", "trusted-workbench"
    ] | None = None
    source_mode: Literal[
        "native-direct", "snapshot-import", "trusted-direct-bind", "managed-clone"
    ] | None = None
    execution_lane: Literal["interactive-gateway", "controlled-run"] | None = None


class WorkerBeeControlledRunPreflightRequest(BaseModel):
    root_operator_agent_id: str = Field(min_length=1, max_length=120)
    target_fork_agent_id: str = Field(min_length=1, max_length=240)
    campaign_id: str = Field(min_length=1, max_length=120)
    profile_id: str = Field(min_length=1, max_length=120)
    baseline_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")
    candidate_commit: str = Field(pattern=r"^[0-9a-fA-F]{40,64}$")


class WorkerBeeControlledRunCreateRequest(WorkerBeeControlledRunPreflightRequest):
    reason: str = Field(default="", max_length=4000)
    requested_by: str = Field(default="agent-pbx-tui", min_length=1, max_length=255)


class WorkerBeeControlledRunDecisionRequest(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=1, max_length=255)
    lease_id: str | None = Field(default=None, max_length=120)


class WorkerBeeControlledRunCancelRequest(BaseModel):
    canceled_by: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=4000)


class WorkerBeeControlledRunLeaseCreateRequest(BaseModel):
    requested_by: str = Field(default="agent-pbx-tui", min_length=1, max_length=255)


class WorkerBeeControlledRunLeaseDecisionRequest(BaseModel):
    approve: bool
    decided_by: str = Field(min_length=1, max_length=255)


class WorkerBeeControlledRunLeaseRevokeRequest(BaseModel):
    revoked_by: str = Field(min_length=1, max_length=255)


class WorkerBeeControlledRunClaimRequest(BaseModel):
    executor_id: str = Field(min_length=1, max_length=240)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class WorkerBeeControlledRunTransitionRequest(BaseModel):
    executor_id: str = Field(min_length=1, max_length=240)
    status: Literal[
        "provisioning",
        "canary",
        "running",
        "collecting",
        "cleaning",
        "recovery_required",
        "succeeded",
        "failed",
    ]
    expected_statuses: list[str] = Field(min_length=1, max_length=3)
    workerbee_run_id: str | None = Field(default=None, max_length=240)
    workerbee_workspace_id: str | None = Field(default=None, max_length=240)
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, max_length=120)
    error: str | None = Field(default=None, max_length=4000)
    lease_seconds: int = Field(default=60, ge=10, le=300)


class WorkerBeeControlledRunReceiptRequest(BaseModel):
    outcome: Literal["succeeded", "failed", "canceled"]
    backend_run_id: str | None = Field(default=None, max_length=240)
    workspace_id: str | None = Field(default=None, max_length=240)
    evidence: dict[str, Any] = Field(default_factory=dict)
    cleanup: dict[str, Any] = Field(default_factory=dict)


class OperatorWorkspaceSetRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    members: list[str] = Field(default_factory=list, max_length=128)
    collections: list[str] = Field(default_factory=list, max_length=32)
    collection_depth: int = Field(default=1, ge=1, le=4)


class OperatorAuthorityAssignmentRequest(BaseModel):
    profile: Literal[
        "scoped-edit", "trusted-workbench", "full-access", "review-readonly"
    ]
    workspace_set_id: str | None = Field(default=None, max_length=120)
    profile_label: str | None = Field(default=None, max_length=120)


class WorkerBeeWorkspaceTransitionRequest(BaseModel):
    cwd: str = Field(min_length=1, max_length=4096)
    project: str | None = Field(default=None, min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=2000)


class WorkerBeeWorkspaceTransitionDecisionRequest(BaseModel):
    approve: bool = True


class FileDiagnostic(BaseModel):
    path: str
    line: int
    column: int = 1
    severity: str = "error"
    code: str | None = None
    message: str
    source: str


class FileDiagnosticsResponse(BaseModel):
    agent_id: str
    cwd: str | None = None
    path: str
    tool: str = "auto"
    available_tools: list[str] = Field(default_factory=list)
    diagnostics: list[FileDiagnostic] = Field(default_factory=list)
    backend: str = "none"
    error: dict[str, Any] | None = None
