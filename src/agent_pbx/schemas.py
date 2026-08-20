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
AssignmentState = Literal[
    "pending",
    "sent",
    "waiting",
    "needs_followup",
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


class AgentRegisterRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=240)
    name: str | None = Field(default=None, max_length=120)
    agent_type: AgentType = "caller"
    metadata: dict[str, Any] = Field(default_factory=dict)
    pbx_active: bool = True


class AgentActiveRequest(BaseModel):
    active: bool


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
    active_campaign_count: int = 0


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


class OperatorHandoffListResponse(BaseModel):
    handoffs: list[OperatorHandoffResponse] = Field(default_factory=list)


class OperatorHandoffDeliveryResponse(BaseModel):
    handoff: OperatorHandoffResponse
    command: CommandResponse | None = None


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
    webdav_url: str | None = None
    webdav_username: str | None = None
    webdav_password_configured: bool = False
    checked_at: float
    root_notebook_id: str | None = None
    sync: dict[str, Any] | None = None
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


class JoplinNoteSummary(BaseModel):
    id: str
    parent_id: str
    title: str
    created_time: int | float | None = None
    updated_time: int | float | None = None


class JoplinNoteResponse(JoplinNoteSummary):
    body: str = ""


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
