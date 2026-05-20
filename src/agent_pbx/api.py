from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

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

from .auth import get_store, require_token
from .config import ServerConfig
from .pairing import PairRequest, PairResponse, issue_pairing_token
from .schemas import (
    AgentRegisterRequest,
    AgentResponse,
    CommandAckRequest,
    CommandCreateRequest,
    CommandResponse,
    EventResponse,
    ReportCreateRequest,
    ReportResponse,
)
from .security import generate_pairing_code
from .store import Store


def create_app(config: ServerConfig | None = None) -> FastAPI:
    app = FastAPI(title="Agent PBX", version="0.1.0")
    app.state.config = config or ServerConfig()
    app.state.store = Store(app.state.config.db_path)
    app.state.store.init()

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx", "version": "0.1.0"}

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

    @app.post(
        "/v1/commands",
        response_model=CommandResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_command(
        request: CommandCreateRequest, store: Store = Depends(get_store)
    ) -> dict[str, object]:
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

    @app.get(
        "/v1/agents/{agent_id}/commands",
        response_model=list[CommandResponse],
        dependencies=[Depends(require_token)],
    )
    async def poll_commands(
        agent_id: str,
        wait_seconds: float = 25,
        store: Store = Depends(get_store),
    ) -> list[dict[str, object]]:
        deadline = asyncio.get_running_loop().time() + min(max(wait_seconds, 0), 30)
        while True:
            commands = store.claim_commands(agent_id)
            if commands:
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
            if wait_seconds <= 0 or asyncio.get_running_loop().time() >= deadline:
                return []
            await asyncio.sleep(0.5)

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
        command = store.ack_command(command_id, request.result)
        if command is None:
            raise HTTPException(status_code=404, detail="command not found")
        store.append_event(
            "command_acked",
            {"command_id": command_id, "result": request.result},
            command_id,
        )
        return command

    @app.get(
        "/v1/events",
        response_model=list[EventResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_events(
        after_id: int = 0, store: Store = Depends(get_store)
    ) -> list[dict[str, object]]:
        return store.list_events(after_id=after_id)

    @app.get("/v1/events/stream", dependencies=[Depends(require_token)])
    async def event_stream(
        request: Request,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
        store: Store = Depends(get_store),
    ) -> StreamingResponse:
        async def generate() -> object:
            after_id = last_event_id or 0
            while not await request.is_disconnected():
                events = store.list_events(after_id=after_id)
                if events:
                    for event in events:
                        after_id = int(event["event_id"])
                        yield _format_sse(event)
                else:
                    yield ": keepalive\n\n"
                    await asyncio.sleep(1)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


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
    app = FastAPI(title="Agent PBX Token Helper", version="0.1.0")
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
