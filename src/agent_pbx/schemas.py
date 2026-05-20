from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


CommandType = Literal[
    "request_detail", "send_input", "start_task", "cancel_task", "acknowledge"
]


class AgentRegisterRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    project: str = Field(min_length=1, max_length=240)
    name: str | None = Field(default=None, max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    agent_id: str
    project: str
    name: str | None
    status: str
    metadata: dict[str, Any]
    created_at: float
    last_seen_at: float


class ReportCreateRequest(BaseModel):
    project: str = Field(min_length=1, max_length=240)
    status: str = Field(default="done", max_length=40)
    summary: str = Field(min_length=1, max_length=4000)
    detail: str = Field(min_length=1)
    needs_input: bool = False
    plan_options: list[str] = Field(default_factory=list)


class ReportResponse(BaseModel):
    report_id: str
    agent_id: str
    project: str
    status: str
    summary: str
    detail: str
    needs_input: bool
    plan_options: list[str]
    created_at: float


class CommandCreateRequest(BaseModel):
    agent_id: str | None = Field(default=None, max_length=120)
    type: CommandType
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandAckRequest(BaseModel):
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
