"""Shared HTTP translation helpers for extracted route families."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import HTTPException, Request, status

from ..issues import IssueError
from ..pull_requests import PullRequestError


class AgentLookup(Protocol):
    def get_agent(self, agent_id: str) -> dict[str, object] | None: ...


class CommandLogStore(AgentLookup, Protocol):
    def append_event(
        self,
        event_type: str,
        payload: dict[str, object],
        agent_id: str,
    ) -> Any: ...


def require_agent(store: AgentLookup, agent_id: str) -> dict[str, object]:
    agent = store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not registered")
    return agent


async def run_pull_request_call(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except PullRequestError as exc:
        response_status = (
            status.HTTP_403_FORBIDDEN
            if exc.code in {"PR_MERGE_DISABLED", "PR_REPO_NOT_ALLOWED"}
            else status.HTTP_409_CONFLICT
            if exc.code in {"PR_MERGE_CONFIRMATION_REQUIRED", "PR_MERGE_METHOD_INVALID"}
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=exc.as_error(),
        ) from exc


async def run_issue_call(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except IssueError as exc:
        response_status = (
            status.HTTP_403_FORBIDDEN
            if exc.code in {"ISSUE_CLOSE_DISABLED", "ISSUE_REPO_NOT_ALLOWED"}
            else status.HTTP_409_CONFLICT
            if exc.code
            in {"ISSUE_CLEAR_CONFIRMATION_REQUIRED", "ISSUE_CLEAR_COMMENT_REQUIRED"}
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=exc.as_error(),
        ) from exc


async def append_joplin_command_log(
    request: Request,
    store: CommandLogStore,
    command: dict[str, object],
) -> None:
    joplin = request.app.state.joplin
    if not joplin.config.configured:
        return
    try:
        await asyncio.to_thread(joplin.append_command_log, store, command)
    except Exception as exc:  # noqa: BLE001 - logging must not break PBX commands
        store.append_event(
            "joplin_log_failed",
            {
                "agent_id": command.get("agent_id"),
                "command_id": command.get("command_id"),
                "message": str(exc),
            },
            str(command.get("command_id") or ""),
        )


__all__ = (
    "AgentLookup",
    "CommandLogStore",
    "append_joplin_command_log",
    "require_agent",
    "run_issue_call",
    "run_pull_request_call",
)
