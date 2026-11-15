"""GitHub pull-request and issue HTTP routes."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request

from ..auth import get_store, require_token
from ..schemas import (
    CommandCreateRequest,
    IssueActionRequest,
    IssueActionResponse,
    IssueClearRequest,
    IssueClearResponse,
    IssueDetailResponse,
    IssueListResponse,
    IssueStatusResponse,
    PullRequestActionRequest,
    PullRequestActionResponse,
    PullRequestDetailResponse,
    PullRequestListResponse,
    PullRequestMergeRequest,
    PullRequestMergeResponse,
    PullRequestStatusResponse,
)
from ._shared import (
    append_joplin_command_log,
    require_agent,
    run_issue_call,
    run_pull_request_call,
)


def register_github_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/agents/{agent_id}/pull-requests/status",
        response_model=PullRequestStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_pull_request_status(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        pull_requests = request.app.state.pull_requests
        return await asyncio.to_thread(pull_requests.status_for_agent, agent)

    @app.get(
        "/v1/agents/{agent_id}/pull-requests",
        response_model=PullRequestListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_agent_pull_requests(
        agent_id: str,
        request: Request,
        limit: int = 30,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        pull_requests = request.app.state.pull_requests
        return await asyncio.to_thread(pull_requests.list_for_agent, agent, limit=limit)

    @app.get(
        "/v1/agents/{agent_id}/pull-requests/{number}",
        response_model=PullRequestDetailResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_pull_request_detail(
        agent_id: str,
        number: int,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        pull_requests = request.app.state.pull_requests
        return await run_pull_request_call(
            pull_requests.detail_for_agent,
            agent,
            number,
        )

    @app.post(
        "/v1/agents/{agent_id}/pull-requests/{number}/review-request",
        response_model=PullRequestActionResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_pull_request_review(
        agent_id: str,
        number: int,
        request: Request,
        payload: PullRequestActionRequest = Body(
            default_factory=PullRequestActionRequest
        ),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        pull_requests = request.app.state.pull_requests
        detail = await run_pull_request_call(
            pull_requests.detail_for_agent,
            agent,
            number,
        )
        prompt = pull_requests.review_prompt(detail)
        if payload.message:
            prompt = f"{prompt}\n\nOperator note: {payload.message.strip()}"
        if not payload.queue:
            return {
                "ok": True,
                "action": "review-request",
                "agent_id": agent_id,
                "number": number,
                "repo": detail.get("repo"),
                "command": None,
                "message": "Review prompt generated.",
                "prompt": prompt,
            }
        command = store.create_command(
            CommandCreateRequest(
                agent_id=agent_id,
                type="send_input",
                payload={
                    "message": prompt,
                    "source": "pull_request_review",
                    "pr_number": number,
                    "repo": detail.get("repo"),
                },
            )
        )
        store.append_event(
            "pr_review_requested",
            {
                "agent_id": agent_id,
                "command_id": command["command_id"],
                "number": number,
                "repo": detail.get("repo"),
                "url": detail.get("url"),
            },
            command["command_id"],
        )
        await append_joplin_command_log(request, store, command)
        return {
            "ok": True,
            "action": "review-request",
            "agent_id": agent_id,
            "number": number,
            "repo": detail.get("repo"),
            "command": command,
            "message": "Review request queued for agent.",
            "prompt": prompt,
        }

    @app.post(
        "/v1/agents/{agent_id}/pull-requests/{number}/workerbee-validation-request",
        response_model=PullRequestActionResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_pull_request_workerbee_validation(
        agent_id: str,
        number: int,
        request: Request,
        payload: PullRequestActionRequest = Body(
            default_factory=PullRequestActionRequest
        ),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        pull_requests = request.app.state.pull_requests
        detail = await run_pull_request_call(
            pull_requests.detail_for_agent,
            agent,
            number,
        )
        prompt = pull_requests.workerbee_validation_prompt(detail)
        if payload.message:
            prompt = f"{prompt}\n\nOperator note: {payload.message.strip()}"
        if not payload.queue:
            return {
                "ok": True,
                "action": "workerbee-validation-request",
                "agent_id": agent_id,
                "number": number,
                "repo": detail.get("repo"),
                "command": None,
                "message": "WorkerBee validation prompt generated.",
                "prompt": prompt,
            }
        command = store.create_command(
            CommandCreateRequest(
                agent_id=agent_id,
                type="send_input",
                payload={
                    "message": prompt,
                    "source": "pull_request_workerbee_validation",
                    "pr_number": number,
                    "repo": detail.get("repo"),
                },
            )
        )
        store.append_event(
            "pr_workerbee_validation_requested",
            {
                "agent_id": agent_id,
                "command_id": command["command_id"],
                "number": number,
                "repo": detail.get("repo"),
                "url": detail.get("url"),
            },
            command["command_id"],
        )
        await append_joplin_command_log(request, store, command)
        return {
            "ok": True,
            "action": "workerbee-validation-request",
            "agent_id": agent_id,
            "number": number,
            "repo": detail.get("repo"),
            "command": command,
            "message": "WorkerBee validation request queued for agent.",
            "prompt": prompt,
        }

    @app.post(
        "/v1/agents/{agent_id}/pull-requests/{number}/merge",
        response_model=PullRequestMergeResponse,
        dependencies=[Depends(require_token)],
    )
    async def merge_pull_request(
        agent_id: str,
        number: int,
        payload: PullRequestMergeRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        pull_requests = request.app.state.pull_requests
        store.append_event(
            "pr_merge_requested",
            {
                "agent_id": agent_id,
                "number": number,
                "method": payload.method,
            },
            agent_id,
        )
        try:
            merged = await run_pull_request_call(
                pull_requests.merge_for_agent,
                agent,
                number,
                method=payload.method,
                confirm=payload.confirm,
            )
        except HTTPException as exc:
            store.append_event(
                "pr_merge_failed",
                {
                    "agent_id": agent_id,
                    "number": number,
                    "method": payload.method,
                    "detail": exc.detail,
                },
                agent_id,
            )
            raise
        store.append_event(
            "pr_merged",
            {
                "agent_id": agent_id,
                "number": number,
                "method": payload.method,
                "repo": merged.get("repo"),
                "url": merged.get("url"),
            },
            agent_id,
        )
        return merged

    @app.get(
        "/v1/agents/{agent_id}/issues/status",
        response_model=IssueStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_issue_status(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        issues = request.app.state.issues
        return await asyncio.to_thread(issues.status_for_agent, agent)

    @app.get(
        "/v1/agents/{agent_id}/issues",
        response_model=IssueListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_agent_issues(
        agent_id: str,
        request: Request,
        state: str = "open",
        limit: int = 30,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        issues = request.app.state.issues
        return await asyncio.to_thread(
            issues.list_for_agent,
            agent,
            state=state,
            limit=limit,
        )

    @app.get(
        "/v1/agents/{agent_id}/issues/{number}",
        response_model=IssueDetailResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_issue_detail(
        agent_id: str,
        number: int,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        issues = request.app.state.issues
        return await run_issue_call(issues.detail_for_agent, agent, number)

    @app.post(
        "/v1/agents/{agent_id}/issues/{number}/mitigation-request",
        response_model=IssueActionResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_issue_mitigation(
        agent_id: str,
        number: int,
        request: Request,
        payload: IssueActionRequest = Body(default_factory=IssueActionRequest),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        issues = request.app.state.issues
        detail = await run_issue_call(issues.detail_for_agent, agent, number)
        prompt = issues.mitigation_prompt(detail)
        if payload.message:
            prompt = f"{prompt}\n\nOperator note: {payload.message.strip()}"
        if not payload.queue:
            return {
                "ok": True,
                "action": "mitigation-request",
                "agent_id": agent_id,
                "number": number,
                "repo": detail.get("repo"),
                "command": None,
                "message": "Issue mitigation prompt generated.",
                "prompt": prompt,
            }
        command = store.create_command(
            CommandCreateRequest(
                agent_id=agent_id,
                type="send_input",
                payload={
                    "message": prompt,
                    "source": "issue_mitigation",
                    "issue_number": number,
                    "repo": detail.get("repo"),
                },
            )
        )
        store.append_event(
            "issue_mitigation_requested",
            {
                "agent_id": agent_id,
                "command_id": command["command_id"],
                "number": number,
                "repo": detail.get("repo"),
                "url": detail.get("url"),
            },
            command["command_id"],
        )
        await append_joplin_command_log(request, store, command)
        return {
            "ok": True,
            "action": "mitigation-request",
            "agent_id": agent_id,
            "number": number,
            "repo": detail.get("repo"),
            "command": command,
            "message": "Issue mitigation request queued for agent.",
            "prompt": prompt,
        }

    @app.post(
        "/v1/agents/{agent_id}/issues/{number}/clear",
        response_model=IssueClearResponse,
        dependencies=[Depends(require_token)],
    )
    async def clear_issue(
        agent_id: str,
        number: int,
        payload: IssueClearRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        issues = request.app.state.issues
        store.append_event(
            "issue_clear_requested",
            {"agent_id": agent_id, "number": number},
            agent_id,
        )
        try:
            cleared = await run_issue_call(
                issues.clear_for_agent,
                agent,
                number,
                comment=payload.comment,
                confirm=payload.confirm,
            )
        except HTTPException as exc:
            store.append_event(
                "issue_clear_failed",
                {
                    "agent_id": agent_id,
                    "number": number,
                    "detail": exc.detail,
                },
                agent_id,
            )
            raise
        store.append_event(
            "issue_cleared",
            {
                "agent_id": agent_id,
                "number": number,
                "repo": cleared.get("repo"),
                "url": cleared.get("url"),
            },
            agent_id,
        )
        return cleared
