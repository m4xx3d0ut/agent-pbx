from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from typing import Any

from fastapi import (
    BackgroundTasks,
    Body,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from . import __version__
from .auth import get_store, require_token
from .config import ServerConfig
from .codex_sessions import enrich_codex_session_metadata
from .debug_smoke import DebugSmokeConfig, run_debug_smoke_reports
from .files import AgentFileService
from .joplin import (
    JoplinApiError,
    JoplinConfig,
    JoplinGateway,
    JoplinScopeError,
    JoplinService,
    agent_session_id,
    format_copy_body,
    scoped_note_title,
)
from .issues import (
    IssueConfig,
    IssueError,
    IssueService,
)
from .mcp_tools import create_mcp_asgi_app
from .operator import OperatorService, operator_runbook_payload
from .pairing import PairRequest, PairResponse, issue_pairing_token
from .polling import poll_commands as poll_commands_until
from .pull_requests import (
    PullRequestConfig,
    PullRequestError,
    PullRequestService,
)
from .schemas import (
    AgentActiveRequest,
    AgentRegisterRequest,
    AgentResponse,
    CommandAckRequest,
    CommandCreateRequest,
    CommandResponse,
    EventResponse,
    FileListResponse,
    FilePreviewResponse,
    JoplinCopyRequest,
    JoplinDocumentRequest,
    JoplinLogAppendRequest,
    JoplinLogResponse,
    JoplinNoteCreateRequest,
    JoplinNoteResponse,
    JoplinNoteSummary,
    JoplinNoteUpdateRequest,
    JoplinStatusResponse,
    JoplinSyncJobResponse,
    JoplinSyncStatusResponse,
    OperatorCampaignAssignmentResponse,
    OperatorCampaignFinishRequest,
    OperatorCampaignListResponse,
    OperatorCampaignResponse,
    OperatorCampaignStartRequest,
    OperatorForkEdgeCreateRequest,
    OperatorForkEdgeResponse,
    OperatorForkEnsureRequest,
    OperatorForkListResponse,
    OperatorForkResponse,
    OperatorFollowupRequest,
    OperatorAssignmentReportRequest,
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
    ReportCreateRequest,
    ReportResponse,
    ThreadItemResponse,
    WorkerBeeStatusResponse,
)
from .security import generate_pairing_code
from .store import Store
from .workerbee import WorkerBeeStatusService


logger = logging.getLogger("agent_pbx.api")


def create_app(config: ServerConfig | None = None) -> FastAPI:
    resolved_config = config or ServerConfig()
    store = Store(resolved_config.db_path)
    store.init()
    joplin_config = JoplinConfig(
        api_url=resolved_config.joplin_api_url,
        token=resolved_config.joplin_token,
        notebook=resolved_config.joplin_notebook,
        joplin_bin=resolved_config.joplin_bin,
        profile=resolved_config.joplin_profile,
        timeout_seconds=resolved_config.joplin_timeout_seconds,
        sync_on_write=resolved_config.joplin_sync_on_write,
        webdav_url=resolved_config.joplin_webdav_url,
        webdav_username=resolved_config.joplin_webdav_username,
        webdav_password_configured=(
            resolved_config.joplin_webdav_password_configured
        ),
    )
    joplin = JoplinGateway(
        store,
        JoplinService(replace(joplin_config, sync_on_write=False)),
        sync_on_write=joplin_config.sync_on_write,
    )
    pull_requests = PullRequestService(
        PullRequestConfig(
            enabled=resolved_config.pull_requests_enabled,
            merge_enabled=resolved_config.pull_request_merge_enabled,
            gh_bin=resolved_config.github_bin,
            timeout_seconds=resolved_config.pull_request_timeout_seconds,
            allowed_repos=resolved_config.pull_request_allowed_repos,
            github_remote=resolved_config.github_remote,
            github_ssh_command=resolved_config.github_ssh_command,
            github_ssh_command_overrides=(
                resolved_config.github_ssh_command_overrides or {}
            ),
        )
    )
    issues = IssueService(
        IssueConfig(
            enabled=resolved_config.issues_enabled,
            close_enabled=resolved_config.issue_close_enabled,
            gh_bin=resolved_config.github_bin,
            timeout_seconds=resolved_config.pull_request_timeout_seconds,
            allowed_repos=resolved_config.pull_request_allowed_repos,
            github_remote=resolved_config.github_remote,
            github_ssh_command=resolved_config.github_ssh_command,
            github_ssh_command_overrides=(
                resolved_config.github_ssh_command_overrides or {}
            ),
        )
    )
    mcp_asgi_app, mcp_server = create_mcp_asgi_app(
        store,
        resolved_config,
        joplin,
        pull_requests=pull_requests,
        issues=issues,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_server.session_manager.run():
            smoke_task: asyncio.Task[None] | None = None
            joplin_sync_task: asyncio.Task[None] | None = None
            if resolved_config.debug_smoke:
                smoke_config = DebugSmokeConfig(
                    duration_seconds=resolved_config.debug_smoke_duration_seconds,
                    min_interval_seconds=(
                        resolved_config.debug_smoke_min_interval_seconds
                    ),
                    max_interval_seconds=(
                        resolved_config.debug_smoke_max_interval_seconds
                    ),
                )
                smoke_task = asyncio.create_task(
                    run_debug_smoke_reports(store, smoke_config),
                    name="agent-pbx-debug-smoke",
                )
            if joplin.config.configured:
                joplin_sync_task = asyncio.create_task(
                    run_joplin_sync_worker(store, joplin),
                    name="agent-pbx-joplin-sync",
                )
            try:
                yield
            finally:
                if joplin_sync_task is not None:
                    joplin_sync_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await joplin_sync_task
                if smoke_task is not None:
                    smoke_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await smoke_task

    app = FastAPI(
        title="Agent PBX",
        version=__version__,
        lifespan=lifespan,
        debug=resolved_config.debug,
    )
    app.state.config = resolved_config
    app.state.store = store
    app.state.files = AgentFileService()
    app.state.joplin = joplin
    app.state.workerbee = WorkerBeeStatusService(
        workerbee_bin=resolved_config.workerbee_bin,
        timeout_seconds=resolved_config.workerbee_timeout_seconds,
        cache_seconds=resolved_config.workerbee_cache_seconds,
    )
    app.state.pull_requests = pull_requests
    app.state.issues = issues
    app.state.operator_service = OperatorService(store)
    if resolved_config.debug:
        app.add_middleware(DebugRequestLogMiddleware)
    app.mount("/mcp", mcp_asgi_app)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx", "version": __version__}

    @app.get("/v1/auth/check", dependencies=[Depends(require_token)])
    async def auth_check() -> dict[str, object]:
        return {"ok": True}

    @app.post(
        "/v1/agents/register",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def register_agent(
        request: AgentRegisterRequest, store: Store = Depends(get_store)
    ) -> dict[str, object]:
        if request.agent_type == "caller":
            request = request.model_copy(
                update={"metadata": enrich_codex_session_metadata(request.metadata)}
            )
        agent = store.register_agent(request)
        store.append_event(
            "agent_registered",
            {
                "agent_id": request.agent_id,
                "project": request.project,
                "agent_type": request.agent_type,
            },
            request.agent_id,
        )
        return agent

    @app.get(
        "/v1/agents",
        response_model=list[AgentResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_agents(
        include_hidden: bool = False,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        return store.list_agents(include_hidden=include_hidden)

    @app.put(
        "/v1/agents/{agent_id}/pbx-active",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def set_agent_pbx_active(
        agent_id: str,
        request: AgentActiveRequest,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.set_agent_pbx_active(agent_id, request.active)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        store.append_event(
            "agent_pbx_active_changed",
            {
                "agent_id": agent_id,
                "project": agent["project"],
                "pbx_active": agent["pbx_active"],
            },
            agent_id,
        )
        return agent

    @app.put(
        "/v1/agents/{agent_id}/unhide",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def unhide_agent(
        agent_id: str,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.unhide_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        store.append_event(
            "agent_unhidden",
            {
                "agent_id": agent_id,
                "project": agent["project"],
            },
            agent_id,
        )
        return agent

    @app.delete(
        "/v1/agents/{agent_id}",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def dismiss_agent(
        agent_id: str,
        delete_thread: bool = False,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        dismissed = store.dismiss_agent(agent_id, delete_thread=delete_thread)
        if dismissed is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        store.append_event(
            "agent_dismissed",
            {
                "agent_id": agent_id,
                "project": agent["project"],
                "delete_thread": delete_thread,
            },
            agent_id,
        )
        return dismissed

    @app.post(
        "/v1/agents/{agent_id}/reports",
        response_model=ReportResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_report(
        agent_id: str,
        request: ReportCreateRequest,
        http_request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        report = store.create_report(agent_id, request)
        store.append_event(
            "report_created",
                {
                    "report_id": report["report_id"],
                    "agent_id": agent_id,
                    "status": request.status,
                    "summary": request.summary,
                    "needs_input": request.needs_input,
                    "created_at": report["created_at"],
                },
                report["report_id"],
            )
        await append_joplin_report_log(http_request, store, report)
        return report

    @app.get(
        "/v1/reports/{report_id}",
        response_model=ReportResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_report(
        report_id: str, store: Store = Depends(get_store)
    ) -> dict[str, object]:
        report = store.get_report(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return report

    @app.get(
        "/v1/agents/{agent_id}/reports",
        response_model=list[ReportResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_agent_reports(
        agent_id: str,
        limit: int = 20,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        return store.list_reports(agent_id, limit=limit)

    @app.post(
        "/v1/agents/{agent_id}/latest/seen",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def mark_agent_latest_seen(
        agent_id: str,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        agent = store.mark_latest_report_seen(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        store.append_event(
            "latest_seen",
            {
                "agent_id": agent_id,
                "project": agent["project"],
                "latest_report_seen_at": agent.get("latest_report_seen_at"),
            },
            agent_id,
        )
        return agent

    async def set_agent_star(
        agent_id: str,
        *,
        starred: bool,
        store: Store,
    ) -> dict[str, object]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        agent = store.set_agent_starred(agent_id, starred=starred)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        store.append_event(
            "agent_starred_changed",
            {
                "agent_id": agent_id,
                "project": agent["project"],
                "starred": agent["starred"],
                "starred_at": agent.get("starred_at"),
            },
            agent_id,
        )
        return agent

    @app.post(
        "/v1/agents/{agent_id}/star",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def star_agent(
        agent_id: str,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        return await set_agent_star(agent_id, starred=True, store=store)

    @app.delete(
        "/v1/agents/{agent_id}/star",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def unstar_agent(
        agent_id: str,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        return await set_agent_star(agent_id, starred=False, store=store)

    @app.get(
        "/v1/agents/{agent_id}/thread",
        response_model=list[ThreadItemResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_agent_thread(
        agent_id: str,
        limit: int = 100,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        return store.list_thread(agent_id, limit=limit)

    @app.get(
        "/v1/agents/{agent_id}/workerbee",
        response_model=WorkerBeeStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_workerbee_status(
        agent_id: str,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        workerbee = request.app.state.workerbee
        return await asyncio.to_thread(workerbee.status_for_agent, agent)

    @app.get(
        "/v1/agents/{agent_id}/pull-requests/status",
        response_model=PullRequestStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_pull_request_status(
        agent_id: str,
        request: Request,
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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
        store: Store = Depends(get_store),
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

    @app.get(
        "/v1/joplin/status",
        response_model=JoplinStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_joplin_status(request: Request) -> dict[str, object]:
        joplin = request.app.state.joplin
        return await asyncio.to_thread(joplin.status)

    @app.get(
        "/v1/joplin/sync",
        response_model=JoplinSyncStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_joplin_sync_status(request: Request) -> dict[str, object]:
        joplin = request.app.state.joplin
        return await asyncio.to_thread(joplin.sync_status)

    @app.post(
        "/v1/joplin/sync",
        response_model=JoplinSyncJobResponse,
        dependencies=[Depends(require_token)],
    )
    async def enqueue_joplin_sync(request: Request) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.request_sync, reason="manual")

    @app.get(
        "/v1/projects/{project}/joplin/notes",
        response_model=list[JoplinNoteSummary],
        dependencies=[Depends(require_token)],
    )
    async def list_project_joplin_notes(
        project: str,
        request: Request,
    ) -> list[dict[str, object]]:
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.list_notes_for_project, project)

    @app.post(
        "/v1/projects/{project}/joplin/notes",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_project_joplin_note(
        project: str,
        payload: JoplinNoteCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.create_note_for_project,
            project,
            title=payload.title or scoped_note_title(project, "NOTE"),
            body=payload.body,
        )

    @app.get(
        "/v1/projects/{project}/joplin/notes/{note_id}",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_project_joplin_note(
        project: str,
        note_id: str,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.get_note_for_project, project, note_id)

    @app.put(
        "/v1/projects/{project}/joplin/notes/{note_id}",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_project_joplin_note(
        project: str,
        note_id: str,
        payload: JoplinNoteUpdateRequest,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.update_note_for_project,
            project,
            note_id,
            title=payload.title,
            body=payload.body,
        )

    @app.delete(
        "/v1/projects/{project}/joplin/notes/{note_id}",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def delete_project_joplin_note(
        project: str,
        note_id: str,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.delete_note_for_project, project, note_id)

    @app.get(
        "/v1/agents/{agent_id}/joplin/notes",
        response_model=list[JoplinNoteSummary],
        dependencies=[Depends(require_token)],
    )
    async def list_agent_joplin_notes(
        agent_id: str,
        request: Request,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.list_notes_for_agent, agent)

    @app.post(
        "/v1/agents/{agent_id}/joplin/notes",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_agent_joplin_note(
        agent_id: str,
        payload: JoplinNoteCreateRequest,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.create_note_for_agent,
            agent,
            event_type="NOTE",
            title=payload.title or scoped_note_title(agent_session_id(agent), "NOTE"),
            body=payload.body,
        )

    @app.get(
        "/v1/agents/{agent_id}/joplin/notes/{note_id}",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_joplin_note(
        agent_id: str,
        note_id: str,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.get_note_for_agent, agent, note_id)

    @app.put(
        "/v1/agents/{agent_id}/joplin/notes/{note_id}",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_agent_joplin_note(
        agent_id: str,
        note_id: str,
        payload: JoplinNoteUpdateRequest,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.update_note_for_agent,
            agent,
            note_id,
            title=payload.title,
            body=payload.body,
        )

    @app.delete(
        "/v1/agents/{agent_id}/joplin/notes/{note_id}",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def delete_agent_joplin_note(
        agent_id: str,
        note_id: str,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        deleted = await run_joplin_call(joplin.delete_note_for_agent, agent, note_id)
        store.append_event(
            "joplin_note_deleted",
            {"agent_id": agent_id, "note_id": note_id},
            agent_id,
        )
        return deleted

    @app.post(
        "/v1/agents/{agent_id}/joplin/copy",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def copy_agent_joplin_note(
        agent_id: str,
        request: Request,
        payload: JoplinCopyRequest = Body(default_factory=JoplinCopyRequest),
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        source, title, body, metadata = joplin_copy_source(store, agent, payload)
        note_body = format_copy_body(
            agent=agent,
            source=source,
            title=title,
            body=body,
            metadata=metadata,
        )
        return await run_joplin_call(
            joplin.create_note_for_agent,
            agent,
            event_type="COPY",
            body=note_body,
            title=payload.title
            or scoped_note_title(agent_session_id(agent), "COPY"),
        )

    @app.post(
        "/v1/agents/{agent_id}/joplin/log/start",
        response_model=JoplinLogResponse,
        dependencies=[Depends(require_token)],
    )
    async def start_agent_joplin_log(
        agent_id: str,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        log = await run_joplin_call(joplin.start_log, store, agent)
        store.append_event(
            "joplin_log_started",
            {"agent_id": agent_id, "note_id": log["note_id"], "title": log["title"]},
            agent_id,
        )
        return log

    @app.post(
        "/v1/agents/{agent_id}/joplin/log/append",
        response_model=JoplinLogResponse | None,
        dependencies=[Depends(require_token)],
    )
    async def append_agent_joplin_log(
        agent_id: str,
        payload: JoplinLogAppendRequest,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object] | None:
        require_agent(store, agent_id)
        joplin = require_joplin(request)
        log = await run_joplin_call(
            joplin.append_log_section,
            store,
            agent_id,
            title=payload.title,
            body=payload.body,
        )
        if log is not None:
            store.append_event(
                "joplin_log_appended",
                {"agent_id": agent_id, "note_id": log["note_id"]},
                agent_id,
            )
        return log

    @app.post(
        "/v1/agents/{agent_id}/joplin/log/stop",
        response_model=JoplinLogResponse | None,
        dependencies=[Depends(require_token)],
    )
    async def stop_agent_joplin_log(
        agent_id: str,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object] | None:
        require_agent(store, agent_id)
        joplin = require_joplin(request)
        log = await run_joplin_call(joplin.stop_log, store, agent_id)
        if log is not None:
            store.append_event(
                "joplin_log_stopped",
                {"agent_id": agent_id, "note_id": log["note_id"]},
                agent_id,
            )
        return log

    @app.post(
        "/v1/joplin/documents",
        response_model=JoplinNoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_joplin_document(
        payload: JoplinDocumentRequest,
        request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(payload.agent_id)
        if agent is None:
            agent = {
                "agent_id": payload.agent_id,
                "project": payload.project,
                "metadata": {"session_id": payload.session_id}
                if payload.session_id
                else {},
            }
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.create_document,
            agent=agent,
            title=payload.title,
            body=payload.body,
            session_id=payload.session_id,
            mermaid_blocks=payload.mermaid_blocks,
            assets=[asset.model_dump(exclude_none=True) for asset in payload.assets],
        )

    @app.get(
        "/v1/agents/{agent_id}/files",
        response_model=FileListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_agent_files(
        agent_id: str,
        request: Request,
        path: str = ".",
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(files.list_for_agent, agent, path=path)

    @app.get(
        "/v1/agents/{agent_id}/files/preview",
        response_model=FilePreviewResponse,
        dependencies=[Depends(require_token)],
    )
    async def preview_agent_file(
        agent_id: str,
        request: Request,
        path: str,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(files.preview_for_agent, agent, path=path)

    @app.get(
        "/v1/operator/runbook",
        dependencies=[Depends(require_token)],
    )
    async def get_operator_runbook() -> dict[str, object]:
        return operator_runbook_payload()

    @app.get(
        "/v1/operator/forks",
        response_model=OperatorForkListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_forks(
        request: Request,
        operator_agent_id: str | None = None,
        source_caller_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        forks = await asyncio.to_thread(
            operator_service.list_forks,
            operator_agent_id=operator_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            campaign_id=campaign_id,
            status=status,
            limit=limit,
        )
        return {"forks": forks}

    @app.post(
        "/v1/operator/forks/ensure",
        response_model=OperatorForkResponse,
        dependencies=[Depends(require_token)],
    )
    async def ensure_operator_fork(
        payload: OperatorForkEnsureRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.ensure_fork,
            operator_agent_id=payload.operator_agent_id,
            source_caller_agent_id=payload.source_caller_agent_id,
            fork_agent_id=payload.fork_agent_id,
            campaign_id=payload.campaign_id,
            tmux_pane_id=payload.tmux_pane_id,
            fork_codex_session_id=payload.fork_codex_session_id,
            status=payload.status,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/fork-edges",
        response_model=OperatorForkEdgeResponse,
        dependencies=[Depends(require_token)],
    )
    async def link_operator_forks(
        payload: OperatorForkEdgeCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.link_forks,
            from_fork_id=payload.from_fork_id,
            to_fork_id=payload.to_fork_id,
            edge_type=payload.edge_type,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/campaigns",
        response_model=OperatorCampaignListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_campaigns(
        request: Request,
        operator_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        campaigns = await asyncio.to_thread(
            operator_service.campaign_status,
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            status=status,
            limit=limit,
        )
        return {"campaigns": campaigns}

    @app.post(
        "/v1/operator/campaigns",
        response_model=OperatorCampaignResponse,
        dependencies=[Depends(require_token)],
    )
    async def start_operator_campaign(
        payload: OperatorCampaignStartRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.start_campaign,
            operator_agent_id=payload.operator_agent_id,
            title=payload.title,
            objective=payload.objective,
            criteria=payload.criteria,
            assignments=payload.assignments,
            delivery=payload.delivery,
        )

    @app.post(
        "/v1/operator/campaigns/{campaign_id}/followups",
        response_model=CommandResponse,
        dependencies=[Depends(require_token)],
    )
    async def send_operator_campaign_followup(
        campaign_id: str,
        payload: OperatorFollowupRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.send_followup,
            operator_agent_id=payload.operator_agent_id,
            campaign_id=campaign_id,
            target_agent_id=payload.target_agent_id,
            message=payload.message,
            assignment_id=payload.assignment_id,
            delivery=payload.delivery,
        )

    @app.post(
        "/v1/operator/campaigns/{campaign_id}/assignments/{assignment_id}/report",
        response_model=OperatorCampaignAssignmentResponse,
        dependencies=[Depends(require_token)],
    )
    async def report_operator_campaign_assignment(
        campaign_id: str,
        assignment_id: str,
        payload: OperatorAssignmentReportRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.report_assignment,
            operator_agent_id=payload.operator_agent_id,
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            state=payload.state,
            summary=payload.summary,
            detail=payload.detail,
        )

    @app.post(
        "/v1/operator/campaigns/{campaign_id}/finish",
        response_model=OperatorCampaignResponse,
        dependencies=[Depends(require_token)],
    )
    async def finish_operator_campaign(
        campaign_id: str,
        payload: OperatorCampaignFinishRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.finish_campaign,
            operator_agent_id=payload.operator_agent_id,
            campaign_id=campaign_id,
            status=payload.status,
            summary=payload.summary,
            detail=payload.detail,
        )

    @app.post(
        "/v1/commands",
        response_model=CommandResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_command(
        request: CommandCreateRequest,
        http_request: Request,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        if request.agent_id is not None and store.get_agent(request.agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        command = store.create_command(request)
        store.append_event(
            "command_queued",
            {
                "command_id": command["command_id"],
                "agent_id": request.agent_id,
                "type": request.type,
            },
            command["command_id"],
        )
        await append_joplin_command_log(http_request, store, command)
        return command

    @app.delete(
        "/v1/commands/{command_id}",
        response_model=CommandResponse,
        dependencies=[Depends(require_token)],
    )
    async def delete_queued_command(
        command_id: str,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        command = store.get_command(command_id)
        if command is None:
            raise HTTPException(status_code=404, detail="command not found")
        if command["status"] != "queued":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="command is not queued",
            )
        deleted = store.delete_queued_command(command_id)
        if deleted is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="command is no longer queued",
            )
        store.append_event(
            "command_deleted",
            {
                "command_id": command_id,
                "agent_id": deleted["agent_id"],
                "type": deleted["type"],
            },
            command_id,
        )
        return deleted

    @app.get(
        "/v1/agents/{agent_id}/commands",
        response_model=list[CommandResponse],
        dependencies=[Depends(require_token)],
    )
    async def poll_commands(
        agent_id: str,
        wait_seconds: float = 25,
        max_wait_seconds: float | None = None,
        interval_seconds: float = 5,
        limit: int = 10,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        commands = await poll_commands_until(
            store,
            agent_id,
            wait_seconds=wait_seconds,
            max_wait_seconds=max_wait_seconds,
            interval_seconds=interval_seconds,
            limit=limit,
        )
        store.record_poll(agent_id, delivered_count=len(commands))
        for command in commands:
            store.append_event(
                "command_delivered",
                {
                    "command_id": command["command_id"],
                    "agent_id": agent_id,
                    "type": command["type"],
                },
                command["command_id"],
            )
        return commands

    @app.post(
        "/v1/commands/{command_id}/ack",
        response_model=CommandResponse,
        dependencies=[Depends(require_token)],
    )
    async def ack_command(
        command_id: str,
        request: CommandAckRequest,
        store: Store = Depends(get_store),
    ) -> dict[str, object]:
        existing = store.get_command(command_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="command not found")
        if existing["agent_id"] != request.agent_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="command is not owned by agent",
            )
        if existing["status"] != "delivered":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"command is {existing['status']}, not delivered",
            )
        command = store.ack_command(command_id, request.agent_id, request.result)
        if command is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="command is no longer delivered for agent",
            )
        store.append_event(
            "command_acked",
            {
                "command_id": command_id,
                "agent_id": command["agent_id"],
                "result": request.result,
            },
            command_id,
        )
        return command

    @app.get(
        "/v1/events",
        response_model=list[EventResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_events(
        after_id: int = 0,
        limit: int = 100,
        tail: bool = False,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        bounded_limit = min(500, max(1, limit))
        if tail:
            return store.list_recent_events(limit=bounded_limit)
        return store.list_events(after_id=after_id, limit=bounded_limit)

    @app.get("/v1/events/stream", dependencies=[Depends(require_token)])
    async def event_stream(
        request: Request,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
        store: Store = Depends(get_store),
    ) -> StreamingResponse:
        async def generate() -> object:
            after_id = last_event_id or 0
            while not await request.is_disconnected():
                events = await asyncio.to_thread(store.list_events, after_id=after_id)
                if events:
                    for event in events:
                        after_id = int(event["event_id"])
                        yield _format_sse(event)
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


def require_agent(store: Store, agent_id: str) -> dict[str, object]:
    agent = store.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not registered")
    return agent


def require_joplin(request: Request) -> JoplinGateway:
    joplin = request.app.state.joplin
    status_payload = joplin.status()
    if not status_payload.get("configured"):
        raise HTTPException(status_code=404, detail=status_payload["error"])
    if status_payload.get("error"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=status_payload["error"],
        )
    return joplin


async def run_joplin_call(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except JoplinScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "JOPLIN_NOTE_OUT_OF_SCOPE",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc
    except JoplinApiError as exc:
        response_status = (
            status.HTTP_404_NOT_FOUND
            if exc.status_code == status.HTTP_404_NOT_FOUND
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail={
                "code": "JOPLIN_API_ERROR",
                "message": str(exc),
                "retryable": response_status != status.HTTP_404_NOT_FOUND,
            },
        ) from exc


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


async def run_operator_call(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except ValueError as exc:
        message = str(exc)
        response_status = (
            status.HTTP_409_CONFLICT
            if "tmux" in message or "delivery" in message
            else status.HTTP_400_BAD_REQUEST
        )
        if "not registered" in message or "not found" in message:
            response_status = status.HTTP_404_NOT_FOUND
        raise HTTPException(
            status_code=response_status,
            detail={
                "code": "OPERATOR_CAMPAIGN_ERROR",
                "message": message,
                "retryable": response_status == status.HTTP_409_CONFLICT,
            },
        ) from exc


def joplin_copy_source(
    store: Store,
    agent: dict[str, object],
    payload: JoplinCopyRequest,
) -> tuple[str, str, str, dict[str, object]]:
    agent_id = str(agent["agent_id"])
    if payload.body is not None:
        title = payload.title or "Manual Copy"
        return "manual", title, payload.body, {}
    if payload.report_id:
        report = store.get_report(payload.report_id)
        if report is None or report["agent_id"] != agent_id:
            raise HTTPException(status_code=404, detail="report not found")
        return (
            f"report:{report['report_id']}",
            str(payload.title or report["summary"]),
            str(report["detail"]),
            {
                "report_id": report["report_id"],
                "status": report["status"],
                "summary": report["summary"],
                "created_at": report["created_at"],
            },
        )
    if payload.thread_item_id:
        for item in store.list_thread(agent_id, limit=200):
            if item["item_id"] != payload.thread_item_id:
                continue
            return (
                str(item["item_id"]),
                str(payload.title or item["title"]),
                str(item["body"]),
                {
                    "kind": item["kind"],
                    "status": item["status"],
                    "created_at": item["created_at"],
                    "metadata": item["metadata"],
                },
            )
        raise HTTPException(status_code=404, detail="thread item not found")
    latest = store.list_reports(agent_id, limit=1)
    if not latest:
        raise HTTPException(status_code=404, detail="latest report not found")
    report = latest[0]
    return (
        f"report:{report['report_id']}",
        str(payload.title or report["summary"]),
        str(report["detail"]),
        {
            "report_id": report["report_id"],
            "status": report["status"],
            "summary": report["summary"],
            "created_at": report["created_at"],
        },
    )


async def append_joplin_command_log(
    request: Request,
    store: Store,
    command: dict[str, object],
) -> None:
    joplin: JoplinGateway = request.app.state.joplin
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


async def append_joplin_report_log(
    request: Request,
    store: Store,
    report: dict[str, object],
) -> None:
    joplin: JoplinGateway = request.app.state.joplin
    if not joplin.config.configured:
        return
    try:
        await asyncio.to_thread(joplin.append_report_log, store, report)
    except Exception as exc:  # noqa: BLE001 - logging must not break PBX reports
        store.append_event(
            "joplin_log_failed",
            {
                "agent_id": report.get("agent_id"),
                "report_id": report.get("report_id"),
                "message": str(exc),
            },
            str(report.get("report_id") or ""),
        )


async def run_joplin_sync_worker(
    store: Store,
    joplin: JoplinGateway,
    *,
    interval_seconds: float = 2.0,
) -> None:
    while True:
        job = store.claim_next_joplin_sync_job()
        if job is None:
            await asyncio.sleep(interval_seconds)
            continue
        try:
            await asyncio.to_thread(joplin.run_sync_job, job)
        except Exception as exc:  # noqa: BLE001 - sync status should retain cause
            completed = store.complete_joplin_sync_job(
                str(job["sync_id"]),
                success=False,
                error=str(exc),
            )
            store.append_event(
                "joplin_sync_failed",
                {
                    "sync_id": job["sync_id"],
                    "reason": job["reason"],
                    "agent_id": job.get("agent_id"),
                    "note_id": job.get("note_id"),
                    "message": str(exc),
                },
                str(job["sync_id"]),
            )
            logger.warning("Joplin sync job failed: %s", completed or job)
            continue
        completed = store.complete_joplin_sync_job(
            str(job["sync_id"]),
            success=True,
        )
        store.append_event(
            "joplin_sync_succeeded",
            {
                "sync_id": job["sync_id"],
                "reason": job["reason"],
                "agent_id": job.get("agent_id"),
                "note_id": job.get("note_id"),
            },
            str(job["sync_id"]),
        )
        logger.debug("Joplin sync job succeeded: %s", completed or job)


class DebugRequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        logger.debug(
            "pbx.request.start method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "-",
        )
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "pbx.request.error method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                (time.perf_counter() - start) * 1000,
            )
            raise
        logger.debug(
            "pbx.request.finish method=%s path=%s status=%s duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            (time.perf_counter() - start) * 1000,
        )
        return response


def create_token_helper_app(
    store: Store,
    *,
    pairing_code: str | None = None,
    ttl_seconds: int = 120,
    on_issued: Callable[[], None] | None = None,
) -> tuple[FastAPI, str]:
    store.init()
    code = pairing_code or generate_pairing_code()
    store.create_pairing_code(code, ttl_seconds=ttl_seconds)
    app = FastAPI(title="Agent PBX Token Helper", version=__version__)
    app.state.store = store

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx-token-helper"}

    @app.post("/pair", response_model=PairResponse)
    async def pair(
        request: PairRequest, background_tasks: BackgroundTasks
    ) -> PairResponse:
        response = issue_pairing_token(store, request)
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid or expired pairing code",
            )
        if on_issued is not None:
            background_tasks.add_task(on_issued)
        return response

    return app, code


def _format_sse(event: dict[str, object]) -> str:
    return (
        f"id: {event['event_id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event)}\n\n"
    )
