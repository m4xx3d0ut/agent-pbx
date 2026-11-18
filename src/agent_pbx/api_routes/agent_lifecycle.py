from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..agent_adoption import AgentPaneAdoptionService
from ..auth import get_store, require_token
from ..codex_sessions import enrich_codex_session_metadata
from ..lifecycle import LifecycleService
from ..managed_runtime import ManagedRuntimeService
from ..mcp_inventory import MCP_TOOL_MANIFEST_STORE_KEY
from ..schemas import (
    AgentActiveRequest,
    AgentPaneAdoptionPreviewRequest,
    AgentPruneApplyResponse,
    AgentPruneBatchListResponse,
    AgentPruneBatchResponse,
    AgentPrunePreviewResponse,
    AgentPruneRequest,
    AgentRegisterRequest,
    AgentResponse,
    LifecycleActionRequest,
    ManagedAgentLaunchRequest,
    ManagedCallerRehydrateRequest,
    ManagedRuntimeStopFinalizeRequest,
    ManagedRuntimeStopPrepareRequest,
    ReportCreateRequest,
    ReportResponse,
    RuntimeMigrationPreviewRequest,
    RuntimeMigrationResultsRequest,
    ThreadItemResponse,
)
from ._shared import append_joplin_report_log


def register_agent_lifecycle_routes(
    app: FastAPI,
    *,
    resolved_config: Any,
) -> None:
    async def set_agent_star(
        agent_id: str,
        *,
        starred: bool,
        store: Any,
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

    @app.get(
        "/v2/lifecycle/{entity_id}",
        dependencies=[Depends(require_token)],
    )
    async def inspect_lifecycle_entity(
        entity_id: str,
        request: Request,
    ) -> dict[str, object]:
        service: LifecycleService = request.app.state.lifecycle
        try:
            return await asyncio.to_thread(service.inspect, entity_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post(
        "/v2/lifecycle/{entity_id}/managed-stop/prepare",
        dependencies=[Depends(require_token)],
    )
    async def prepare_managed_runtime_stop(
        entity_id: str,
        payload: ManagedRuntimeStopPrepareRequest,
        request: Request,
    ) -> dict[str, object]:
        service: LifecycleService = request.app.state.lifecycle
        expected_identity = {
            "server_id": payload.expected_server_id,
            "socket_path": payload.expected_socket_path,
            "session_name": payload.expected_session_name,
            "window_id": payload.expected_window_id,
            "pane_id": payload.expected_pane_id,
            "pane_pid": payload.expected_pane_pid,
            "process_start_ticks": payload.expected_process_start_ticks,
            "codex_session_id": payload.expected_codex_session_id,
        }
        try:
            return await asyncio.to_thread(
                service.prepare_managed_runtime_stop,
                entity_id,
                expected_identity=expected_identity,
                client_id=payload.client_id,
                metadata=payload.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/lifecycle/{entity_id}/managed-stop/finalize",
        dependencies=[Depends(require_token)],
    )
    async def finalize_managed_runtime_stop(
        entity_id: str,
        payload: ManagedRuntimeStopFinalizeRequest,
        request: Request,
    ) -> dict[str, object]:
        service: LifecycleService = request.app.state.lifecycle
        try:
            return await asyncio.to_thread(
                service.finalize_managed_runtime_stop,
                entity_id,
                transaction_id=payload.transaction_id,
                metadata=payload.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/lifecycle/{entity_id}/actions/{action}/preview",
        dependencies=[Depends(require_token)],
    )
    async def preview_lifecycle_action(
        entity_id: str,
        action: str,
        request: Request,
    ) -> dict[str, object]:
        service: LifecycleService = request.app.state.lifecycle
        try:
            return await asyncio.to_thread(service.preview_action, entity_id, action)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/lifecycle/{entity_id}/actions/{action}/apply",
        dependencies=[Depends(require_token)],
    )
    async def apply_lifecycle_action(
        entity_id: str,
        action: str,
        payload: LifecycleActionRequest,
        request: Request,
    ) -> dict[str, object]:
        service: LifecycleService = request.app.state.lifecycle
        try:
            return await asyncio.to_thread(
                service.apply_action,
                entity_id,
                action,
                preview_token=payload.preview_token,
                metadata=payload.metadata,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v2/projects/discover",
        dependencies=[Depends(require_token)],
    )
    async def discover_managed_projects(
        request: Request,
        root: str | None = Query(default=None, max_length=4096),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        try:
            projects = await asyncio.to_thread(
                service.discover_projects,
                root=root,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"roots": service.public_roots(), "projects": projects}

    @app.post(
        "/v2/agents/managed-launch/preview",
        dependencies=[Depends(require_token)],
    )
    async def preview_managed_agent_launch(
        payload: ManagedAgentLaunchRequest,
        request: Request,
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        local_server = f"http://127.0.0.1:{resolved_config.port}"
        try:
            return await asyncio.to_thread(
                service.preview_launch,
                project_path=payload.project_path,
                agent_id=payload.agent_id,
                profile_id=payload.profile_id,
                runtime_server_mode=payload.runtime_server_mode,
                server_url=local_server,
                source_codex_session_id=payload.source_codex_session_id,
                workerbee_profile=payload.workerbee_profile,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/agents/managed-launch",
        dependencies=[Depends(require_token)],
    )
    async def launch_managed_agent(
        payload: ManagedAgentLaunchRequest,
        request: Request,
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        local_server = f"http://127.0.0.1:{resolved_config.port}"
        try:
            return await asyncio.to_thread(
                service.launch,
                project_path=payload.project_path,
                agent_id=payload.agent_id,
                profile_id=payload.profile_id,
                runtime_server_mode=payload.runtime_server_mode,
                server_url=local_server,
                token=resolved_config.token,
                source_codex_session_id=payload.source_codex_session_id,
                workerbee_profile=payload.workerbee_profile,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/agents/rehydrate/preview",
        dependencies=[Depends(require_token)],
    )
    async def preview_managed_caller_rehydration(
        payload: ManagedCallerRehydrateRequest,
        request: Request,
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        local_server = f"http://127.0.0.1:{resolved_config.port}"
        try:
            return await asyncio.to_thread(
                service.preview_rehydrate,
                agent_id=payload.agent_id,
                mode=payload.mode,
                profile_id=payload.profile_id,
                runtime_server_mode=payload.runtime_server_mode,
                server_url=local_server,
                source_codex_session_id=payload.source_codex_session_id,
                workerbee_profile=payload.workerbee_profile,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/agents/rehydrate",
        dependencies=[Depends(require_token)],
    )
    async def apply_managed_caller_rehydration(
        payload: ManagedCallerRehydrateRequest,
        request: Request,
    ) -> dict[str, object]:
        if not payload.preview_token:
            raise HTTPException(status_code=409, detail="preview_token is required")
        service: ManagedRuntimeService = request.app.state.managed_runtime
        local_server = f"http://127.0.0.1:{resolved_config.port}"
        try:
            return await asyncio.to_thread(
                service.rehydrate,
                agent_id=payload.agent_id,
                preview_token=payload.preview_token,
                mode=payload.mode,
                profile_id=payload.profile_id,
                runtime_server_mode=payload.runtime_server_mode,
                server_url=local_server,
                token=resolved_config.token,
                source_codex_session_id=payload.source_codex_session_id,
                workerbee_profile=payload.workerbee_profile,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/runtime-migrations/preview",
        dependencies=[Depends(require_token)],
    )
    async def preview_runtime_migration(
        payload: RuntimeMigrationPreviewRequest,
        request: Request,
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        try:
            return await asyncio.to_thread(
                service.preview_migration,
                agent_ids=payload.agent_ids,
                include_starred=payload.include_starred,
                include_operators=payload.include_operators,
                target_cli_version=payload.target_cli_version,
                retention_days=payload.retention_days,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v2/runtime-migrations",
        dependencies=[Depends(require_token)],
    )
    async def list_runtime_migrations(
        limit: int = Query(default=100, ge=1, le=1000),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return {"batches": store.list_runtime_migration_batches(limit=limit)}

    @app.get(
        "/v2/runtime-migrations/{batch_id}",
        dependencies=[Depends(require_token)],
    )
    async def get_runtime_migration(
        batch_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        batch = store.get_runtime_migration_batch(batch_id)
        if batch is None:
            raise HTTPException(
                status_code=404, detail="runtime migration batch not found"
            )
        return batch

    @app.post(
        "/v2/runtime-migrations/{batch_id}/results",
        dependencies=[Depends(require_token)],
    )
    async def record_runtime_migration_results(
        batch_id: str,
        payload: RuntimeMigrationResultsRequest,
        request: Request,
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        try:
            return await asyncio.to_thread(
                service.record_migration_results,
                batch_id,
                payload.results,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/runtime-migrations/{batch_id}/rollback",
        dependencies=[Depends(require_token)],
    )
    async def rollback_runtime_migration(
        batch_id: str,
        request: Request,
    ) -> dict[str, object]:
        service: ManagedRuntimeService = request.app.state.managed_runtime
        try:
            return await asyncio.to_thread(service.rollback_migration, batch_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/agent-pane-adoptions/preview",
        dependencies=[Depends(require_token)],
    )
    async def preview_agent_pane_adoption(
        payload: AgentPaneAdoptionPreviewRequest,
        request: Request,
    ) -> dict[str, object]:
        service: AgentPaneAdoptionService = request.app.state.agent_pane_adoption
        try:
            return await asyncio.to_thread(service.preview, **payload.model_dump())
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/agent-pane-adoptions/{batch_id}/apply",
        dependencies=[Depends(require_token)],
    )
    async def apply_agent_pane_adoption(
        batch_id: str,
        request: Request,
    ) -> dict[str, object]:
        service: AgentPaneAdoptionService = request.app.state.agent_pane_adoption
        try:
            return await asyncio.to_thread(service.apply, batch_id)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v2/agent-pane-adoptions/{batch_id}/rollback",
        dependencies=[Depends(require_token)],
    )
    async def rollback_agent_pane_adoption(
        batch_id: str,
        request: Request,
    ) -> dict[str, object]:
        service: AgentPaneAdoptionService = request.app.state.agent_pane_adoption
        try:
            return await asyncio.to_thread(service.rollback, batch_id)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/agents/register",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def register_agent(
        request: AgentRegisterRequest, store: Any = Depends(get_store)
    ) -> dict[str, object]:
        if request.agent_type == "caller":
            request = request.model_copy(
                update={"metadata": enrich_codex_session_metadata(request.metadata)}
            )
        violation = store.agent_registration_identity_violation_payload(request)
        if violation is not None:
            store.append_event(
                "agent_registration_identity_violation",
                violation,
                request.agent_id,
            )
            raise HTTPException(status_code=409, detail=violation["reason"])
        agent = store.register_agent(request)
        agent_metadata = (
            agent.get("metadata")
            if isinstance(agent.get("metadata"), dict)
            else {}
        )
        bound_mapping = store.bind_managed_tmux_codex_session(
            request.agent_id,
            codex_session_id=str(agent_metadata.get("codex_session_id") or ""),
            expected_pane_id=str(agent_metadata.get("tmux_pane_id") or ""),
            expected_bootstrap_id=str(
                agent_metadata.get("managed_bootstrap_id") or ""
            ),
        )
        if bound_mapping is not None:
            store.append_event(
                "managed_runtime_codex_session_bound",
                {
                    "agent_id": request.agent_id,
                    "pane_id": bound_mapping.get("pane_id"),
                    "codex_session_id": bound_mapping.get("codex_session_id"),
                },
                request.agent_id,
            )
        registered_project = str(agent.get("project") or request.project)
        registered_agent_type = str(agent.get("agent_type") or request.agent_type)
        store.append_event(
            "agent_registered",
            {
                "agent_id": request.agent_id,
                "project": registered_project,
                "agent_type": registered_agent_type,
            },
            request.agent_id,
        )
        if registered_project != request.project:
            store.append_event(
                "agent_registration_project_canonicalized",
                {
                    "agent_id": request.agent_id,
                    "requested_project": request.project,
                    "project": registered_project,
                    "agent_type": registered_agent_type,
                },
                request.agent_id,
            )
        return agent

    @app.get(
        "/v2/mcp/tool-manifest",
        dependencies=[Depends(require_token)],
    )
    async def get_mcp_tool_manifest(
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return store.get_system_metadata_json(MCP_TOOL_MANIFEST_STORE_KEY)

    @app.get(
        "/v1/agents",
        response_model=list[AgentResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_agents(
        include_hidden: bool = False,
        store: Any = Depends(get_store),
    ) -> list[dict[str, object]]:
        return store.list_agents(include_hidden=include_hidden)

    @app.post(
        "/v1/agents/prune/preview",
        response_model=AgentPrunePreviewResponse,
        dependencies=[Depends(require_token)],
    )
    async def preview_agent_prune(
        request: AgentPruneRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return store.preview_agent_prune(**request.model_dump())

    @app.post(
        "/v1/agents/prune/apply",
        response_model=AgentPruneApplyResponse,
        dependencies=[Depends(require_token)],
    )
    async def apply_agent_prune(
        request: AgentPruneRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        criteria = request.model_dump()
        preview = store.preview_agent_prune(**criteria)
        batch = store.apply_agent_prune(
            preview=preview,
            criteria=criteria,
            metadata=request.metadata,
        )
        return {"preview": preview, "batch": batch}

    @app.get(
        "/v1/agents/prune/batches",
        response_model=AgentPruneBatchListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_agent_prune_batches(
        limit: int = 20,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return {"batches": store.list_agent_prune_batches(limit=limit)}

    @app.post(
        "/v1/agents/prune/batches/{batch_id}/undo",
        response_model=AgentPruneBatchResponse,
        dependencies=[Depends(require_token)],
    )
    async def undo_agent_prune_batch(
        batch_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        batch = store.undo_agent_prune_batch(batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="agent prune batch not found")
        return batch

    @app.put(
        "/v1/agents/{agent_id}/pbx-active",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def set_agent_pbx_active(
        agent_id: str,
        request: AgentActiveRequest,
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        request = store.report_request_with_identity_metadata(request)
        requested_project = request.project
        request = store.report_request_with_canonical_project(agent, request)
        if request.project != requested_project:
            store.append_event(
                "report_project_canonicalized",
                {
                    "agent_id": agent_id,
                    "requested_project": requested_project,
                    "project": request.project,
                },
                agent_id,
            )
        violation = store.report_identity_violation_payload(agent_id, request)
        if violation is not None:
            store.append_event("report_identity_violation", violation, agent_id)
            raise HTTPException(status_code=409, detail=violation["reason"])
        report = store.create_report(agent_id, request)
        http_request.app.state.codex_runtime.observe_report(
            agent_id,
            status=request.status,
            observed_at=float(report["created_at"]),
        )
        store.append_event(
            "report_created",
            {
                "report_id": report["report_id"],
                "agent_id": agent_id,
                "status": request.status,
                "summary": request.summary,
                "needs_input": request.needs_input,
                "suppress_tui_alerts": bool(
                    isinstance(request.metadata, dict)
                    and request.metadata.get("suppress_tui_alerts")
                ),
                "created_at": report["created_at"],
            },
            report["report_id"],
        )
        if str(agent.get("agent_type") or "") == "operator":
            metadata = (
                report.get("metadata")
                if isinstance(report.get("metadata"), dict)
                else {}
            )
            if any(
                key in metadata
                for key in ("operator_kb_candidates", "kb_candidates", "kb_proposals")
            ):
                operator_service = http_request.app.state.operator_service
                try:
                    await asyncio.to_thread(
                        operator_service.compile_kb_candidates_from_report,
                        operator_agent_id=agent_id,
                        report=report,
                    )
                except Exception as exc:  # noqa: BLE001 - report storage must not depend on KB compile.
                    store.append_event(
                        "operator_kb_report_compile_failed",
                        {
                            "report_id": report["report_id"],
                            "agent_id": agent_id,
                            "error": str(exc),
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
        report_id: str, store: Any = Depends(get_store)
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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

    @app.post(
        "/v1/agents/{agent_id}/star",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def star_agent(
        agent_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return await set_agent_star(agent_id, starred=True, store=store)

    @app.delete(
        "/v1/agents/{agent_id}/star",
        response_model=AgentResponse,
        dependencies=[Depends(require_token)],
    )
    async def unstar_agent(
        agent_id: str,
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
    ) -> list[dict[str, object]]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        return store.list_thread(agent_id, limit=limit)


__all__ = ("register_agent_lifecycle_routes",)
