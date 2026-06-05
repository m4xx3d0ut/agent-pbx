from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress

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
from .debug_smoke import DebugSmokeConfig, run_debug_smoke_reports
from .files import AgentFileService
from .joplin import (
    JoplinConfig,
    JoplinService,
    agent_session_id,
    format_copy_body,
    scoped_note_title,
)
from .mcp_tools import create_mcp_asgi_app
from .pairing import PairRequest, PairResponse, issue_pairing_token
from .polling import poll_commands as poll_commands_until
from .schemas import (
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
    JoplinLogResponse,
    JoplinNoteResponse,
    JoplinNoteSummary,
    JoplinNoteUpdateRequest,
    JoplinStatusResponse,
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
    joplin = JoplinService(
        JoplinConfig(
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
    )
    mcp_asgi_app, mcp_server = create_mcp_asgi_app(store, resolved_config, joplin)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_server.session_manager.run():
            smoke_task: asyncio.Task[None] | None = None
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
            try:
                yield
            finally:
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
        agent = store.register_agent(request)
        store.append_event(
            "agent_registered",
            {"agent_id": request.agent_id, "project": request.project},
            request.agent_id,
        )
        return agent

    @app.get(
        "/v1/agents",
        response_model=list[AgentResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_agents(store: Store = Depends(get_store)) -> list[dict[str, object]]:
        return store.list_agents()

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
        "/v1/joplin/status",
        response_model=JoplinStatusResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_joplin_status(request: Request) -> dict[str, object]:
        joplin = request.app.state.joplin
        return await asyncio.to_thread(joplin.status)

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
        return await asyncio.to_thread(joplin.list_notes_for_agent, agent)

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
        return await asyncio.to_thread(joplin.get_note_for_agent, agent, note_id)

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
        return await asyncio.to_thread(
            joplin.update_note_for_agent,
            agent,
            note_id,
            title=payload.title,
            body=payload.body,
        )

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
        return await asyncio.to_thread(
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
        log = await asyncio.to_thread(joplin.start_log, store, agent)
        store.append_event(
            "joplin_log_started",
            {"agent_id": agent_id, "note_id": log["note_id"], "title": log["title"]},
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
        log = await asyncio.to_thread(joplin.stop_log, store, agent_id)
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
        return await asyncio.to_thread(
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


def require_joplin(request: Request) -> JoplinService:
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
    joplin: JoplinService = request.app.state.joplin
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
    joplin: JoplinService = request.app.state.joplin
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
