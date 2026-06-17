from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .agent import runbook_payload
from .codex_sessions import enrich_codex_session_metadata
from .config import ServerConfig
from .joplin import JoplinService
from .issues import IssueService
from .operator import OperatorService, operator_runbook_payload
from .polling import poll_commands as poll_commands_until
from .pull_requests import PullRequestService
from .schemas import (
    AgentRegisterRequest,
    CommandAckRequest,
    CommandCreateRequest,
    OperatorAssignmentCreate,
    ReportCreateRequest,
)
from .security import constant_time_equal
from .store import Store


logger = logging.getLogger("agent_pbx.mcp")


def build_mcp_server(
    store: Store,
    joplin: JoplinService | None = None,
    pull_requests: PullRequestService | None = None,
    issues: IssueService | None = None,
) -> FastMCP:
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
    operator_service = OperatorService(store)

    @mcp.tool()
    def pbx_agent_runbook() -> dict[str, Any]:
        """Return Agent PBX usage guidance for agents."""
        return runbook_payload()

    @mcp.tool()
    def pbx_operator_runbook() -> dict[str, Any]:
        """Return Agent PBX operator-agent campaign guidance."""
        return operator_runbook_payload()

    @mcp.tool()
    def pbx_operator_list_agents(agent_type: str = "caller") -> list[dict[str, Any]]:
        """List Agent PBX agents visible to operator workflows."""
        return operator_service.list_agents(agent_type=agent_type)

    @mcp.tool()
    def pbx_operator_get_thread(agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return a caller or operator thread for operator review."""
        return operator_service.get_thread(agent_id, limit=limit)

    @mcp.tool()
    def pbx_operator_list_forks(
        operator_agent_id: str | None = None,
        source_caller_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List forked operator sessions tracked for caller session coordination."""
        return operator_service.list_forks(
            operator_agent_id=operator_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            campaign_id=campaign_id,
            status=status,
            limit=limit,
        )

    @mcp.tool()
    def pbx_operator_ensure_fork(
        operator_agent_id: str,
        source_caller_agent_id: str,
        fork_agent_id: str | None = None,
        campaign_id: str | None = None,
        tmux_pane_id: str | None = None,
        fork_codex_session_id: str | None = None,
        status: str | None = None,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ensure a per-caller operator fork record exists."""
        return operator_service.ensure_fork(
            operator_agent_id=operator_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            fork_agent_id=fork_agent_id,
            campaign_id=campaign_id,
            tmux_pane_id=tmux_pane_id,
            fork_codex_session_id=fork_codex_session_id,
            status=status,
            summary=summary,
            metadata=metadata or {},
        )

    @mcp.tool()
    def pbx_operator_link_forks(
        from_fork_id: str,
        to_fork_id: str,
        edge_type: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a DAG edge between operator forks."""
        return operator_service.link_forks(
            from_fork_id=from_fork_id,
            to_fork_id=to_fork_id,
            edge_type=edge_type,
            summary=summary,
            metadata=metadata or {},
        )

    @mcp.tool()
    def pbx_register_agent(
        agent_id: str,
        project: str,
        name: str | None = None,
        agent_type: str = "caller",
        metadata: dict[str, Any] | None = None,
        pbx_active: bool = True,
    ) -> dict[str, Any]:
        logger.debug("mcp.tool.start name=pbx_register_agent agent_id=%s", agent_id)
        request_metadata = metadata or {}
        enriched_metadata = (
            enrich_codex_session_metadata(request_metadata)
            if str(agent_type).strip().lower() == "caller"
            else request_metadata
        )
        agent = store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project=project,
                name=name,
                agent_type=agent_type,  # type: ignore[arg-type]
                metadata=enriched_metadata,
                pbx_active=pbx_active,
            )
        )
        store.append_event(
            "agent_registered",
            {
                "agent_id": agent_id,
                "project": project,
                "agent_type": agent.get("agent_type", "caller"),
                "pbx_active": pbx_active,
            },
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
        plan_options: list[str | dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
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
                metadata=metadata or {},
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
        append_joplin_report_log(store, joplin, report)
        logger.debug(
            "mcp.tool.finish name=pbx_report_turn agent_id=%s report_id=%s",
            agent_id,
            report["report_id"],
        )
        return report

    @mcp.tool()
    def pbx_operator_start_campaign(
        operator_agent_id: str,
        title: str,
        objective: str,
        criteria: list[str] | None = None,
        assignments: list[dict[str, Any]] | None = None,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        """Start a tracked operator campaign and dispatch caller assignments."""
        parsed_assignments = [
            OperatorAssignmentCreate(**assignment)
            for assignment in (assignments or [])
        ]
        return operator_service.start_campaign(
            operator_agent_id=operator_agent_id,
            title=title,
            objective=objective,
            criteria=criteria or [],
            assignments=parsed_assignments,
            delivery=delivery,
        )

    @mcp.tool()
    def pbx_operator_send_followup(
        operator_agent_id: str,
        campaign_id: str,
        target_agent_id: str,
        message: str,
        assignment_id: str | None = None,
        delivery: str = "auto",
    ) -> dict[str, Any]:
        """Send a tracked campaign follow-up to a caller agent."""
        return operator_service.send_followup(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            target_agent_id=target_agent_id,
            message=message,
            assignment_id=assignment_id,
            delivery=delivery,
        )

    @mcp.tool()
    def pbx_operator_campaign_status(
        operator_agent_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return tracked operator campaign state."""
        return operator_service.campaign_status(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            status=status,
            limit=limit,
        )

    @mcp.tool()
    def pbx_operator_report_assignment(
        operator_agent_id: str,
        campaign_id: str,
        assignment_id: str,
        state: str,
        summary: str,
        detail: str,
    ) -> dict[str, Any]:
        """Record operator review state for a campaign assignment."""
        return operator_service.report_assignment(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            state=state,
            summary=summary,
            detail=detail,
        )

    @mcp.tool()
    def pbx_operator_finish_campaign(
        operator_agent_id: str,
        campaign_id: str,
        status: str,
        summary: str,
        detail: str,
    ) -> dict[str, Any]:
        """Record final state for an operator campaign."""
        return operator_service.finish_campaign(
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            status=status,
            summary=summary,
            detail=detail,
        )

    @mcp.tool()
    def pbx_joplin_status() -> dict[str, Any]:
        """Return Agent PBX Joplin integration status."""
        if joplin is None:
            return JoplinService().status()
        return joplin.status()

    @mcp.tool()
    def pbx_joplin_create_document(
        agent_id: str,
        project: str,
        title: str,
        body: str,
        session_id: str | None = None,
        mermaid_blocks: list[str] | None = None,
        assets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a scoped Markdown note in the Agent PBX Joplin notebook."""
        if joplin is None or not joplin.config.configured:
            raise ValueError("Joplin is not configured")
        agent = store.get_agent(agent_id) or {
            "agent_id": agent_id,
            "project": project,
            "metadata": {"session_id": session_id} if session_id else {},
        }
        return joplin.create_document(
            agent=agent,
            title=title,
            body=body,
            session_id=session_id,
            mermaid_blocks=mermaid_blocks or [],
            assets=assets or [],
        )

    @mcp.tool()
    def pbx_pr_context(agent_id: str, pr_number: int) -> dict[str, Any]:
        """Return read-only GitHub pull request context for an agent project."""
        if pull_requests is None:
            raise ValueError("Pull request integration is not configured")
        agent = store.get_agent(agent_id)
        if agent is None:
            raise ValueError("agent not registered")
        return pull_requests.detail_for_agent(agent, pr_number)

    @mcp.tool()
    def pbx_issue_context(agent_id: str, issue_number: int) -> dict[str, Any]:
        """Return read-only GitHub issue context for an agent project."""
        if issues is None:
            raise ValueError("Issue integration is not configured")
        agent = store.get_agent(agent_id)
        if agent is None:
            raise ValueError("agent not registered")
        return issues.detail_for_agent(agent, issue_number)

    @mcp.tool()
    async def pbx_poll_commands(
        agent_id: str,
        wait_seconds: float = 25,
        limit: int = 10,
        max_wait_seconds: float | None = None,
        interval_seconds: float = 5,
    ) -> list[dict[str, Any]]:
        logger.debug("mcp.tool.start name=pbx_poll_commands agent_id=%s", agent_id)
        if store.get_agent(agent_id) is None:
            raise ValueError("agent not registered")
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
        command_id: str, agent_id: str, result: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        logger.debug("mcp.tool.start name=pbx_ack_command command_id=%s", command_id)
        request = CommandAckRequest(agent_id=agent_id, result=result or {})
        existing = store.get_command(command_id)
        if existing is None:
            raise ValueError("command not found")
        if existing["agent_id"] != request.agent_id:
            raise ValueError("command is not owned by agent")
        if existing["status"] != "delivered":
            raise ValueError(f"command is {existing['status']}, not delivered")
        command = store.ack_command(
            command_id, request.agent_id, request.result
        )
        if command is None:
            raise ValueError("command is no longer delivered for agent")
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
        if agent_id is not None and store.get_agent(agent_id) is None:
            raise ValueError("agent not registered")
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
        append_joplin_command_log(store, joplin, command)
        logger.debug(
            "mcp.tool.finish name=pbx_queue_command command_id=%s",
            command["command_id"],
        )
        return command

    return mcp


def append_joplin_command_log(
    store: Store,
    joplin: JoplinService | None,
    command: dict[str, Any],
) -> None:
    if joplin is None or not joplin.config.configured:
        return
    try:
        joplin.append_command_log(store, command)
    except Exception as exc:  # noqa: BLE001 - MCP command queuing must not fail logs
        store.append_event(
            "joplin_log_failed",
            {
                "agent_id": command.get("agent_id"),
                "command_id": command.get("command_id"),
                "message": str(exc),
            },
            str(command.get("command_id") or ""),
        )


def append_joplin_report_log(
    store: Store,
    joplin: JoplinService | None,
    report: dict[str, Any],
) -> None:
    if joplin is None or not joplin.config.configured:
        return
    try:
        joplin.append_report_log(store, report)
    except Exception as exc:  # noqa: BLE001 - MCP reports must not fail logs
        store.append_event(
            "joplin_log_failed",
            {
                "agent_id": report.get("agent_id"),
                "report_id": report.get("report_id"),
                "message": str(exc),
            },
            str(report.get("report_id") or ""),
        )


def create_mcp_asgi_app(
    store: Store,
    config: ServerConfig,
    joplin: JoplinService | None = None,
    pull_requests: PullRequestService | None = None,
    issues: IssueService | None = None,
) -> tuple[ASGIApp, FastMCP]:
    mcp = build_mcp_server(
        store,
        joplin=joplin,
        pull_requests=pull_requests,
        issues=issues,
    )
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
