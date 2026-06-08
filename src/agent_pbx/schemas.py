from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    metadata: dict[str, Any] = Field(default_factory=dict)
    pbx_active: bool = True


class AgentResponse(BaseModel):
    agent_id: str
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


class ReportResponse(BaseModel):
    report_id: str
    agent_id: str
    project: str
    status: str
    summary: str
    detail: str
    needs_input: bool
    plan_options: list[PlanOption]
    created_at: float


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
