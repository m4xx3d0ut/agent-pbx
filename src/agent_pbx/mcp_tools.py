from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .agent import runbook_payload
from .config import ServerConfig
from .polling import poll_commands as poll_commands_until
from .schemas import (
    AgentRegisterRequest,
    CommandAckRequest,
    CommandCreateRequest,
    ReportCreateRequest,
)
from .security import constant_time_equal
from .store import Store


logger = logging.getLogger("agent_pbx.mcp")


def build_mcp_server(store: Store) -> FastMCP:
    mcp = FastMCP(
        "Agent PBX",
        instructions=(
            "Report agent turn status in report mode, poll queued follow-up "
            "commands only in explicit nohup mode, and use pbx_agent_runbook "
            "for Agent PBX session guidance."
        ),
        streamable_http_path="/",
        stateless_http=True,
    )

    @mcp.tool()
    def pbx_agent_runbook() -> dict[str, Any]:
        """Return Agent PBX usage guidance for agents."""
        return runbook_payload()

    @mcp.tool()
    def pbx_register_agent(
        agent_id: str,
        project: str,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        pbx_active: bool = True,
    ) -> dict[str, Any]:
        logger.debug("mcp.tool.start name=pbx_register_agent agent_id=%s", agent_id)
        agent = store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project=project,
                name=name,
                metadata=metadata or {},
                pbx_active=pbx_active,
            )
        )
        store.append_event(
            "agent_registered",
            {"agent_id": agent_id, "project": project, "pbx_active": pbx_active},
            agent_id,
        )
        logger.debug("mcp.tool.finish name=pbx_register_agent agent_id=%s", agent_id)
        return agent

    @mcp.tool()
    def pbx_set_active(agent_id: str, active: bool) -> dict[str, Any]:
        """Mark whether this agent session is actively using Agent PBX."""
        logger.debug(
            "mcp.tool.start name=pbx_set_active agent_id=%s active=%s",
            agent_id,
            active,
        )
        agent = store.set_agent_pbx_active(agent_id, active)
        if agent is None:
            raise ValueError("agent not registered")
        store.append_event(
            "agent_pbx_active_changed",
            {"agent_id": agent_id, "pbx_active": active},
            agent_id,
        )
        logger.debug(
            "mcp.tool.finish name=pbx_set_active agent_id=%s active=%s",
            agent_id,
            active,
        )
        return agent

    @mcp.tool()
    def pbx_report_turn(
        agent_id: str,
        project: str,
        summary: str,
        detail: str,
        status: str = "done",
        needs_input: bool = False,
        plan_options: list[str] | None = None,
    ) -> dict[str, Any]:
        logger.debug("mcp.tool.start name=pbx_report_turn agent_id=%s status=%s", agent_id, status)
        if store.get_agent(agent_id) is None:
            raise ValueError("agent not registered")
        report = store.create_report(
            agent_id,
            ReportCreateRequest(
                project=project,
                status=status,
                summary=summary,
                detail=detail,
                needs_input=needs_input,
                plan_options=plan_options or [],
            ),
        )
        store.append_event(
            "report_created",
            {
                "report_id": report["report_id"],
                "agent_id": agent_id,
                "status": status,
                "summary": summary,
                "needs_input": needs_input,
            },
            report["report_id"],
        )
        logger.debug(
            "mcp.tool.finish name=pbx_report_turn agent_id=%s report_id=%s",
            agent_id,
            report["report_id"],
        )
        return report

    @mcp.tool()
    async def pbx_poll_commands(
        agent_id: str,
        wait_seconds: float = 25,
        limit: int = 10,
        max_wait_seconds: float | None = None,
        interval_seconds: float = 5,
    ) -> list[dict[str, Any]]:
        logger.debug("mcp.tool.start name=pbx_poll_commands agent_id=%s", agent_id)
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
        logger.debug(
            "mcp.tool.finish name=pbx_poll_commands agent_id=%s delivered=%s",
            agent_id,
            len(commands),
        )
        return commands

    @mcp.tool()
    def pbx_ack_command(
        command_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        logger.debug("mcp.tool.start name=pbx_ack_command command_id=%s", command_id)
        command = store.ack_command(
            command_id, CommandAckRequest(result=result or {}).result
        )
        if command is None:
            raise ValueError("command not found")
        store.append_event(
            "command_acked",
            {
                "command_id": command_id,
                "agent_id": command["agent_id"],
                "result": result or {},
            },
            command_id,
        )
        logger.debug("mcp.tool.finish name=pbx_ack_command command_id=%s", command_id)
        return command

    @mcp.tool()
    def pbx_queue_command(
        command_type: str,
        payload: dict[str, Any] | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        logger.debug(
            "mcp.tool.start name=pbx_queue_command agent_id=%s type=%s",
            agent_id,
            command_type,
        )
        command = store.create_command(
            CommandCreateRequest(
                agent_id=agent_id,
                type=command_type,  # type: ignore[arg-type]
                payload=payload or {},
            )
        )
        store.append_event(
            "command_queued",
            {
                "command_id": command["command_id"],
                "agent_id": agent_id,
                "type": command_type,
            },
            command["command_id"],
        )
        logger.debug(
            "mcp.tool.finish name=pbx_queue_command command_id=%s",
            command["command_id"],
        )
        return command

    return mcp


def create_mcp_asgi_app(store: Store, config: ServerConfig) -> tuple[ASGIApp, FastMCP]:
    mcp = build_mcp_server(store)
    return BearerAuthASGIMiddleware(mcp.streamable_http_app(), store, config), mcp


class BearerAuthASGIMiddleware:
    def __init__(self, app: ASGIApp, store: Store, config: ServerConfig) -> None:
        self.app = app
        self.store = store
        self.config = config

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._auth_required():
            await self.app(scope, receive, send)
            return

        token = self._bearer_token(scope)
        if token is None:
            await JSONResponse({"detail": "bearer token required"}, status_code=401)(
                scope, receive, send
            )
            return
        if self.config.token and constant_time_equal(token, self.config.token):
            await self.app(scope, receive, send)
            return
        if self.store.verify_token(token) is None:
            await JSONResponse({"detail": "invalid bearer token"}, status_code=403)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)

    def _auth_required(self) -> bool:
        if self.config.lan_bound and not self.config.allow_insecure_lan:
            return True
        if self.config.token:
            return True
        return self.store.has_tokens()

    @staticmethod
    def _bearer_token(scope: Scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key.lower() == b"authorization":
                text = value.decode("latin1")
                prefix = "Bearer "
                if text.startswith(prefix):
                    return text[len(prefix) :]
        return None
