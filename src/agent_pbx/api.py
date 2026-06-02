from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress

from fastapi import (
    BackgroundTasks,
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
    mcp_asgi_app, mcp_server = create_mcp_asgi_app(store, resolved_config)

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
        request: CommandCreateRequest, store: Store = Depends(get_store)
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
