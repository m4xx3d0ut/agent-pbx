from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status

from ..auth import (
    ClientPrincipal,
    get_store,
    require_global_controller,
    require_token,
)
from ..operator import IdleTargetDeliveryError, operator_runbook_payload
from ..operator_authority import OperatorAuthorityError
from ..schemas import (
    CommandResponse,
    OperatorAlertAcknowledgementResponse,
    OperatorAssignmentReportRequest,
    OperatorBrokerCheckpointCancelRequest,
    OperatorBrokerCheckpointClaimRequest,
    OperatorBrokerCheckpointListResponse,
    OperatorBrokerCheckpointRequest,
    OperatorBrokerCheckpointResponse,
    OperatorBrokerCheckpointTransitionRequest,
    OperatorBrokerCheckpointValidateRequest,
    OperatorCampaignAssignmentResponse,
    OperatorCampaignFinishRequest,
    OperatorCampaignListResponse,
    OperatorCampaignResponse,
    OperatorCampaignStartRequest,
    OperatorCampaignSuccessorRequest,
    OperatorDelegationLeaseCreateRequest,
    OperatorDelegationLeaseDecisionRequest,
    OperatorDelegationLeaseListResponse,
    OperatorDelegationLeaseResponse,
    OperatorDelegationLeaseRevokeRequest,
    OperatorFollowupRequest,
    OperatorForkEdgeCreateRequest,
    OperatorForkEdgeResponse,
    OperatorForkEnsureRequest,
    OperatorForkListResponse,
    OperatorForkRebindSourceSessionRequest,
    OperatorForkResponse,
    OperatorHandoffAckRequest,
    OperatorHandoffActionListResponse,
    OperatorHandoffActionReceiptRequest,
    OperatorHandoffActionTransactionResponse,
    OperatorHandoffApproveRequest,
    OperatorHandoffCancelRequest,
    OperatorHandoffCancelResponse,
    OperatorHandoffCompletionAckRequest,
    OperatorHandoffCompletionDeliveryResponse,
    OperatorHandoffCompletionListResponse,
    OperatorHandoffCreateRequest,
    OperatorHandoffDeliveryResponse,
    OperatorHandoffExecutionPreflightResponse,
    OperatorHandoffListResponse,
    OperatorHandoffPreflightRequest,
    OperatorHandoffPreflightResponse,
    OperatorHandoffResponse,
    OperatorHandoffUpdateRequest,
    OperatorKbCompileReportRequest,
    OperatorKbCompileReportResponse,
    OperatorKbContextRequest,
    OperatorKbContextResponse,
    OperatorKbEntryCreateRequest,
    OperatorKbEntryResponse,
    OperatorKbExportResponse,
    OperatorKbFeedbackRequest,
    OperatorKbFeedbackResponse,
    OperatorKbFromLinkRequest,
    OperatorKbImportRequest,
    OperatorKbImportResponse,
    OperatorKbIndexJobListResponse,
    OperatorKbIndexJobRequest,
    OperatorKbIndexJobResponse,
    OperatorKbIndexRunResponse,
    OperatorKbListResponse,
    OperatorKbPromoteRequest,
    OperatorKbQueryListResponse,
    OperatorKbQueryResponse,
    OperatorKbRejectRequest,
    OperatorKbRetireRequest,
    OperatorKbSeedRunCreateRequest,
    OperatorKbSeedRunDeliveryResponse,
    OperatorKbSeedRunListResponse,
    OperatorKbSeedRunResponse,
    OperatorKbSeedRunUpdateRequest,
    OperatorKbUpdateRequest,
    OperatorKnowledgeContextResponse,
    OperatorKnowledgeDeliveryResponse,
    OperatorKnowledgeHandoffProposalRequest,
    OperatorKnowledgeLinkCloseRequest,
    OperatorKnowledgeLinkCreateRequest,
    OperatorKnowledgeLinkListResponse,
    OperatorKnowledgeLinkResponse,
    OperatorKnowledgeProposalResponse,
    OperatorKnowledgeTurnApproveRequest,
    OperatorKnowledgeTurnCreateRequest,
    OperatorProjectSpawnCancelRequest,
    OperatorProjectSpawnListResponse,
    OperatorProjectSpawnRequest,
    OperatorProjectSpawnResponse,
    OperatorProjectSpawnUpdateRequest,
    OperatorReviewEscalationRequest,
    OperatorWorkspaceSetRequest,
)


async def run_operator_call(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except IdleTargetDeliveryError as exc:
        result = exc.result
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "IDLE_TARGET_DELIVERY_REJECTED",
                "message": str(exc),
                "outcome": result.outcome.value,
                "reason_code": result.reason_code,
                "request_id": result.request_id,
                "command_id": result.command_id,
                "retryable": result.outcome.value
                in {"target_not_ready", "overlay_active"},
            },
        ) from exc
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


def require_local_broker_executor(request: Request) -> None:
    """Keep privileged broker execution claims on the daemon host."""

    host = str(request.client.host if request.client else "").strip()
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host in {"localhost", "testclient"}
    if not loopback:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "OPERATOR_BROKER_LOCAL_EXECUTOR_REQUIRED",
                "message": "Broker execution claims are restricted to a local TUI executor.",
            },
        )


def register_operator_routes(app: FastAPI) -> None:
    @app.post(
        "/v2/operators/{root_operator_id}/alerts/acknowledge",
        response_model=OperatorAlertAcknowledgementResponse,
        dependencies=[Depends(require_token)],
    )
    async def acknowledge_operator_alert_family(
        root_operator_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        try:
            result = store.acknowledge_operator_alert_family(root_operator_id)
        except ValueError as exc:
            detail = str(exc)
            code = 404 if "not registered" in detail else 409
            raise HTTPException(status_code=code, detail=detail) from exc

        for item in result["acknowledged"]:
            if not item.get("changed"):
                continue
            agent_id = str(item["agent_id"])
            agent = store.get_agent(agent_id) or {}
            store.append_event(
                "latest_seen",
                {
                    "agent_id": agent_id,
                    "project": agent.get("project"),
                    "latest_report_seen_at": item["latest_report_seen_at"],
                    "root_operator_id": root_operator_id,
                    "reason": item["reason"],
                },
                agent_id,
            )
        store.append_event(
            "operator_alerts_acknowledged",
            {
                "root_operator_id": root_operator_id,
                "cutoff": result["cutoff"],
                "acknowledged": [item["agent_id"] for item in result["acknowledged"]],
                "preserved": list(result["preserved"]),
            },
            root_operator_id,
        )
        return result

    @app.get(
        "/v2/operator/authority/posture",
        response_model=dict[str, Any],
    )
    async def get_operator_authority_posture(
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            posture = await asyncio.to_thread(
                request.app.state.operator_authority.posture
            )
            return posture.public_dict()
        except OperatorAuthorityError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.post(
        "/v2/operator/workspace-sets/preview",
        response_model=dict[str, Any],
    )
    async def preview_operator_workspace_set(
        payload: OperatorWorkspaceSetRequest,
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                request.app.state.operator_authority.preview_workspace_set,
                label=payload.label,
                members=payload.members,
                collections=payload.collections,
                collection_depth=payload.collection_depth,
            )
        except OperatorAuthorityError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

    @app.get(
        "/v2/operator/workspace-sets",
        response_model=list[dict[str, Any]],
    )
    async def list_operator_workspace_sets(
        request: Request,
        _controller: ClientPrincipal = Depends(require_global_controller),
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            request.app.state.operator_authority.list_workspace_sets
        )

    @app.put(
        "/v2/operator/workspace-sets/{set_id}",
        response_model=dict[str, Any],
    )
    async def put_operator_workspace_set(
        set_id: str,
        payload: OperatorWorkspaceSetRequest,
        request: Request,
        controller: ClientPrincipal = Depends(require_global_controller),
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                request.app.state.operator_authority.save_workspace_set,
                set_id=set_id,
                label=payload.label,
                members=payload.members,
                collections=payload.collections,
                collection_depth=payload.collection_depth,
                updated_by=controller.token_id or controller.client_id or "local",
            )
        except OperatorAuthorityError as exc:
            raise HTTPException(status_code=409, detail=exc.public_dict()) from exc

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
        fork_track_id: str | None = None,
        campaign_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        forks = await asyncio.to_thread(
            operator_service.list_forks,
            operator_agent_id=operator_agent_id,
            source_caller_agent_id=source_caller_agent_id,
            fork_track_id=fork_track_id,
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
            fork_track_id=payload.fork_track_id,
            fork_purpose=payload.fork_purpose,
            access_mode=payload.access_mode,
            source_cwd=payload.source_cwd,
            work_root=payload.work_root,
            campaign_id=payload.campaign_id,
            tmux_pane_id=payload.tmux_pane_id,
            fork_codex_session_id=payload.fork_codex_session_id,
            clear_fork_codex_session_id=payload.clear_fork_codex_session_id,
            status=payload.status,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/forks/rebind-source-session",
        response_model=OperatorForkResponse,
        dependencies=[Depends(require_token)],
    )
    async def rebind_operator_fork_source_session(
        payload: OperatorForkRebindSourceSessionRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.rebind_fork_source_session,
            operator_agent_id=payload.operator_agent_id,
            operator_fork_id=payload.operator_fork_id,
            source_caller_agent_id=payload.source_caller_agent_id,
            old_source_codex_session_id=payload.old_source_codex_session_id,
            new_source_codex_session_id=payload.new_source_codex_session_id,
            source_cwd=payload.source_cwd,
            codex_host_id=payload.codex_host_id,
            reason=payload.reason,
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
        "/v1/operator/knowledge-links",
        response_model=OperatorKnowledgeLinkListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_knowledge_links(
        request: Request,
        operator_agent_id: str | None = None,
        source_agent_id: str | None = None,
        target_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        links = await asyncio.to_thread(
            operator_service.list_knowledge_links,
            operator_agent_id=operator_agent_id,
            source_agent_id=source_agent_id,
            target_agent_id=target_agent_id,
            status=status,
            limit=limit,
        )
        return {"knowledge_links": links}

    @app.post(
        "/v1/operator/knowledge-links",
        response_model=OperatorKnowledgeLinkResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_operator_knowledge_link(
        payload: OperatorKnowledgeLinkCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.create_knowledge_link,
            operator_agent_id=payload.operator_agent_id,
            source_agent_id=payload.source_agent_id,
            target_agent_id=payload.target_agent_id,
            link_type=payload.link_type,
            status=payload.status,
            source_operator_fork_id=payload.source_operator_fork_id,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/knowledge-links/proposals",
        response_model=OperatorKnowledgeProposalResponse,
        dependencies=[Depends(require_token)],
    )
    async def propose_operator_knowledge_handoff(
        payload: OperatorKnowledgeHandoffProposalRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.propose_knowledge_handoff,
            operator_agent_id=payload.operator_agent_id,
            source_agent_id=payload.source_agent_id,
            target_agent_id=payload.target_agent_id,
            message=payload.message,
            link_type=payload.link_type,
            source_operator_fork_id=payload.source_operator_fork_id,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/knowledge-links/{link_id}/context",
        response_model=OperatorKnowledgeContextResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_knowledge_context(
        link_id: str,
        request: Request,
        operator_agent_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.knowledge_context,
            operator_agent_id=operator_agent_id,
            link_id=link_id,
            limit=limit,
        )

    @app.post(
        "/v1/operator/knowledge-links/{link_id}/turns",
        response_model=OperatorKnowledgeDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def send_operator_knowledge_turn(
        link_id: str,
        payload: OperatorKnowledgeTurnCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.send_knowledge_turn,
            operator_agent_id=payload.operator_agent_id,
            link_id=link_id,
            sender_agent_id=payload.sender_agent_id,
            recipient_agent_id=payload.recipient_agent_id,
            message=payload.message,
            turn_type=payload.turn_type,
            delivery=payload.delivery,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/knowledge-links/{link_id}/turns/{turn_id}/approve",
        response_model=OperatorKnowledgeDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def approve_operator_knowledge_turn(
        link_id: str,
        turn_id: str,
        payload: OperatorKnowledgeTurnApproveRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.approve_knowledge_turn,
            operator_agent_id=payload.operator_agent_id,
            link_id=link_id,
            turn_id=turn_id,
            delivery=payload.delivery,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/knowledge-links/{link_id}/close",
        response_model=OperatorKnowledgeLinkResponse,
        dependencies=[Depends(require_token)],
    )
    async def close_operator_knowledge_link(
        link_id: str,
        payload: OperatorKnowledgeLinkCloseRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.close_knowledge_link,
            operator_agent_id=payload.operator_agent_id,
            link_id=link_id,
            status=payload.status,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/kb",
        response_model=OperatorKbListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_kb_entries(
        request: Request,
        operator_agent_id: str,
        query: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        repo_root: str | None = None,
        status: str | None = "active",
        tags: str | None = None,
        include_expired: bool = False,
        semantic: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        parsed_tags = [tag.strip() for tag in str(tags or "").split(",") if tag.strip()]
        entries = await asyncio.to_thread(
            operator_service.search_kb_entries,
            operator_agent_id=operator_agent_id,
            query=query,
            scope=scope,
            project=project,
            repo_root=repo_root,
            status=status,
            tags=parsed_tags,
            include_expired=include_expired,
            semantic=semantic,
            limit=limit,
            record_query=bool(str(query or "").strip()),
            query_source="search",
            query_metadata={"requested_by": "list_operator_kb_entries"},
        )
        return {"kb_entries": entries}

    @app.post(
        "/v1/operator/kb/context",
        response_model=OperatorKbContextResponse,
        dependencies=[Depends(require_token)],
    )
    async def operator_kb_context(
        payload: OperatorKbContextRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.kb_context_for_request,
            operator_agent_id=payload.operator_agent_id,
            query=payload.query,
            target_operator_agent_id=payload.target_operator_agent_id,
            scope=payload.scope,
            project=payload.project,
            repo_root=payload.repo_root,
            tags=payload.tags,
            include_expired=payload.include_expired,
            include_proposed=payload.include_proposed,
            semantic=payload.semantic,
            limit=payload.limit,
        )

    @app.get(
        "/v1/operator/kb/queries",
        response_model=OperatorKbQueryListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_kb_queries(
        request: Request,
        operator_agent_id: str,
        misses: bool = False,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        queries = await asyncio.to_thread(
            operator_service.list_kb_queries,
            operator_agent_id=operator_agent_id,
            misses=misses,
            limit=limit,
        )
        return {"queries": queries}

    @app.get(
        "/v1/operator/kb/queries/{query_id}",
        response_model=OperatorKbQueryResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_kb_query(
        query_id: str,
        operator_agent_id: str,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.get_kb_query,
            operator_agent_id=operator_agent_id,
            query_id=query_id,
        )

    @app.post(
        "/v1/operator/kb/queries/{query_id}/feedback",
        response_model=OperatorKbFeedbackResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_operator_kb_query_feedback(
        query_id: str,
        payload: OperatorKbFeedbackRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.create_kb_feedback,
            operator_agent_id=payload.operator_agent_id,
            query_id=query_id,
            match_id=payload.match_id,
            kb_id=payload.kb_id,
            feedback=payload.feedback,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/kb/compile-report",
        response_model=OperatorKbCompileReportResponse,
        dependencies=[Depends(require_token)],
    )
    async def compile_operator_kb_report(
        payload: OperatorKbCompileReportRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.compile_kb_candidates_from_report,
            operator_agent_id=payload.operator_agent_id,
            report_id=payload.report_id,
            limit=payload.limit,
        )

    @app.get(
        "/v1/operator/kb/index-jobs",
        response_model=OperatorKbIndexJobListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_kb_index_jobs(
        job_status: str | None = Query(default=None, alias="status"),
        limit: int = 50,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return {
            "index_jobs": store.list_operator_kb_index_jobs(
                status=job_status,
                limit=limit,
            )
        }

    @app.post(
        "/v1/operator/kb/index-jobs",
        response_model=OperatorKbIndexJobResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_operator_kb_index_job(
        payload: OperatorKbIndexJobRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return store.create_operator_kb_index_job(
            operation=payload.operation,
            kb_id=payload.kb_id,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/kb/index-jobs/run",
        response_model=OperatorKbIndexRunResponse,
        dependencies=[Depends(require_token)],
    )
    async def run_operator_kb_index_jobs(
        limit: int = 100,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return store.process_operator_kb_index_jobs(limit=limit)

    @app.post(
        "/v1/operator/kb",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def propose_operator_kb_entry(
        payload: OperatorKbEntryCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.propose_kb_entry,
            operator_agent_id=payload.operator_agent_id,
            scope=payload.scope,
            project=payload.project,
            repo_root=payload.repo_root,
            git_remote=payload.git_remote,
            branch=payload.branch,
            title=payload.title,
            summary=payload.summary,
            body=payload.body,
            tags=payload.tags,
            source_knowledge_link_id=payload.source_knowledge_link_id,
            source_handoff_id=payload.source_handoff_id,
            source_turn_ids=payload.source_turn_ids,
            stale_after=payload.stale_after,
            expires_at=payload.expires_at,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/kb/from-link",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def propose_operator_kb_from_link(
        payload: OperatorKbFromLinkRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.propose_kb_from_link,
            operator_agent_id=payload.operator_agent_id,
            link_id=payload.link_id,
            title=payload.title,
            summary=payload.summary,
            scope=payload.scope,
            project=payload.project,
            repo_root=payload.repo_root,
            git_remote=payload.git_remote,
            branch=payload.branch,
            tags=payload.tags,
            include_turn_ids=payload.include_turn_ids,
            stale_after=payload.stale_after,
            expires_at=payload.expires_at,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/kb/export",
        response_model=OperatorKbExportResponse,
        dependencies=[Depends(require_token)],
    )
    async def export_operator_kb_entries(
        request: Request,
        operator_agent_id: str,
        query: str | None = None,
        scope: str | None = None,
        project: str | None = None,
        repo_root: str | None = None,
        status: str | None = "active",
        tags: str | None = None,
        include_expired: bool = False,
        limit: int = 500,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        parsed_tags = [tag.strip() for tag in str(tags or "").split(",") if tag.strip()]
        return await run_operator_call(
            operator_service.export_kb_entries,
            operator_agent_id=operator_agent_id,
            query=query,
            scope=scope,
            project=project,
            repo_root=repo_root,
            status=status,
            tags=parsed_tags,
            include_expired=include_expired,
            limit=limit,
        )

    @app.post(
        "/v1/operator/kb/import",
        response_model=OperatorKbImportResponse,
        dependencies=[Depends(require_token)],
    )
    async def import_operator_kb_entries(
        payload: OperatorKbImportRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.import_kb_entries,
            operator_agent_id=payload.operator_agent_id,
            bundle=payload.bundle,
            import_status=payload.import_status,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/kb/seed-runs",
        response_model=OperatorKbSeedRunListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_kb_seed_runs(
        request: Request,
        operator_agent_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        seed_runs = await asyncio.to_thread(
            operator_service.list_kb_seed_runs,
            operator_agent_id=operator_agent_id,
            status=status,
            limit=limit,
        )
        return {"seed_runs": seed_runs}

    @app.post(
        "/v1/operator/kb/seed-runs",
        response_model=OperatorKbSeedRunDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def start_operator_kb_seed_run(
        payload: OperatorKbSeedRunCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.start_kb_seed_run,
            operator_agent_id=payload.operator_agent_id,
            seed_type=payload.seed_type,
            scope=payload.scope,
            project=payload.project,
            repo_root=payload.repo_root,
            git_remote=payload.git_remote,
            branch=payload.branch,
            prompt=payload.prompt,
            delivery=payload.delivery,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/kb/seed-runs/{seed_run_id}",
        response_model=OperatorKbSeedRunResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_kb_seed_run(
        seed_run_id: str,
        request: Request,
        operator_agent_id: str,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.get_kb_seed_run,
            operator_agent_id=operator_agent_id,
            seed_run_id=seed_run_id,
        )

    @app.patch(
        "/v1/operator/kb/seed-runs/{seed_run_id}",
        response_model=OperatorKbSeedRunResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_operator_kb_seed_run(
        seed_run_id: str,
        payload: OperatorKbSeedRunUpdateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.update_kb_seed_run,
            operator_agent_id=payload.operator_agent_id,
            seed_run_id=seed_run_id,
            status=payload.status,
            summary=payload.summary,
            error=payload.error,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/kb/{kb_id}",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_kb_entry(
        kb_id: str,
        request: Request,
        operator_agent_id: str,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.get_kb_entry,
            operator_agent_id=operator_agent_id,
            kb_id=kb_id,
        )

    @app.patch(
        "/v1/operator/kb/{kb_id}",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_operator_kb_entry(
        kb_id: str,
        payload: OperatorKbUpdateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        update_payload = payload.model_dump(exclude_unset=True)
        operator_agent_id = str(update_payload.pop("operator_agent_id"))
        metadata = update_payload.pop("metadata", {})
        redaction_status = update_payload.pop("redaction_status", None)
        return await run_operator_call(
            operator_service.update_kb_entry,
            operator_agent_id=operator_agent_id,
            kb_id=kb_id,
            updates=update_payload,
            redaction_status=redaction_status,
            metadata=metadata,
        )

    @app.post(
        "/v1/operator/kb/{kb_id}/promote",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def promote_operator_kb_entry(
        kb_id: str,
        payload: OperatorKbPromoteRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.promote_kb_entry,
            operator_agent_id=payload.operator_agent_id,
            kb_id=kb_id,
            redaction_status=payload.redaction_status,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/kb/{kb_id}/reject",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def reject_operator_kb_entry(
        kb_id: str,
        payload: OperatorKbRejectRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.reject_kb_entry,
            operator_agent_id=payload.operator_agent_id,
            kb_id=kb_id,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/kb/{kb_id}/retire",
        response_model=OperatorKbEntryResponse,
        dependencies=[Depends(require_token)],
    )
    async def retire_operator_kb_entry(
        kb_id: str,
        payload: OperatorKbRetireRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.retire_kb_entry,
            operator_agent_id=payload.operator_agent_id,
            kb_id=kb_id,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/delegation-leases",
        response_model=OperatorDelegationLeaseListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_delegation_leases(
        request: Request,
        source_operator_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        leases = await run_operator_call(
            request.app.state.operator_service.list_delegation_leases,
            source_operator_agent_id=source_operator_agent_id,
            status=status,
            limit=limit,
        )
        return {"leases": leases}

    @app.post(
        "/v1/operator/delegation-leases",
        response_model=OperatorDelegationLeaseResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_operator_delegation_lease(
        payload: OperatorDelegationLeaseCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.request_delegation_lease,
            source_operator_agent_id=payload.source_operator_agent_id,
            target_operator_agent_ids=payload.target_operator_agent_ids,
            campaign_ids=payload.campaign_ids,
            workspace_set_digest=payload.workspace_set_digest,
            allowed_action_classes=payload.allowed_action_classes,
            allowed_roots=payload.allowed_roots,
            exact_paths=payload.exact_paths,
            denied_actions=payload.denied_actions,
            max_uses=payload.max_uses,
            ttl_seconds=payload.ttl_seconds,
            requested_by=payload.requested_by,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/delegation-leases/{lease_id}/decision",
        response_model=OperatorDelegationLeaseResponse,
        dependencies=[Depends(require_token)],
    )
    async def decide_operator_delegation_lease(
        lease_id: str,
        payload: OperatorDelegationLeaseDecisionRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.decide_delegation_lease,
            lease_id=lease_id,
            approve=payload.approve,
            decided_by=payload.decided_by,
        )

    @app.post(
        "/v1/operator/delegation-leases/{lease_id}/revoke",
        response_model=OperatorDelegationLeaseResponse,
        dependencies=[Depends(require_token)],
    )
    async def revoke_operator_delegation_lease(
        lease_id: str,
        payload: OperatorDelegationLeaseRevokeRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.revoke_delegation_lease,
            lease_id=lease_id,
            revoked_by=payload.revoked_by,
        )

    @app.post(
        "/v1/operator/delegation-leases/{lease_id}/apply/{handoff_id}",
        response_model=OperatorHandoffDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def apply_operator_delegation_lease(
        lease_id: str,
        handoff_id: str,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.apply_delegation_lease,
            lease_id=lease_id,
            handoff_id=handoff_id,
        )

    @app.get(
        "/v1/operator/handoffs",
        response_model=OperatorHandoffListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_handoffs(
        request: Request,
        operator_agent_id: str | None = None,
        source_agent_id: str | None = None,
        target_operator_agent_id: str | None = None,
        target_caller_agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        handoffs = await asyncio.to_thread(
            operator_service.list_handoffs,
            operator_agent_id=operator_agent_id,
            source_agent_id=source_agent_id,
            target_operator_agent_id=target_operator_agent_id,
            target_caller_agent_id=target_caller_agent_id,
            status=status,
            limit=limit,
        )
        return {"handoffs": handoffs}

    @app.get(
        "/v1/operator/handoff-completions",
        response_model=OperatorHandoffCompletionListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_handoff_completions(
        request: Request,
        recipient_agent_id: str | None = None,
        source_operator_agent_id: str | None = None,
        delivery_status: str | None = None,
        unacknowledged_only: bool = False,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        deliveries = await asyncio.to_thread(
            request.app.state.operator_service.list_handoff_completion_deliveries,
            recipient_agent_id=recipient_agent_id,
            source_operator_agent_id=source_operator_agent_id,
            delivery_status=delivery_status,
            unacknowledged_only=unacknowledged_only,
            limit=limit,
        )
        return {"completion_deliveries": deliveries}

    @app.post(
        "/v1/operator/handoff-completions/{completion_delivery_id}/ack",
        response_model=OperatorHandoffCompletionDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def acknowledge_operator_handoff_completion(
        completion_delivery_id: str,
        payload: OperatorHandoffCompletionAckRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.acknowledge_handoff_completion,
            operator_agent_id=payload.operator_agent_id,
            completion_delivery_id=completion_delivery_id,
        )

    @app.post(
        "/v1/operator/handoff-completions/{completion_delivery_id}/retry",
        response_model=OperatorHandoffCompletionDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def retry_operator_handoff_completion(
        completion_delivery_id: str,
        payload: OperatorHandoffCompletionAckRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.retry_handoff_completion_delivery,
            operator_agent_id=payload.operator_agent_id,
            completion_delivery_id=completion_delivery_id,
        )

    @app.post(
        "/v1/operator/handoffs",
        response_model=OperatorHandoffResponse,
        dependencies=[Depends(require_token)],
    )
    async def create_operator_handoff(
        payload: OperatorHandoffCreateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.create_handoff,
            operator_agent_id=payload.operator_agent_id,
            source_agent_id=payload.source_agent_id,
            target_operator_agent_id=payload.target_operator_agent_id,
            message=payload.message,
            objective=payload.objective,
            source_operator_fork_id=payload.source_operator_fork_id,
            target_caller_agent_id=payload.target_caller_agent_id,
            target_operator_fork_id=payload.target_operator_fork_id,
            knowledge_link_id=payload.knowledge_link_id,
            knowledge_turn_id=payload.knowledge_turn_id,
            allowed_mutation_scope=payload.allowed_mutation_scope,
            execution_scope=payload.execution_scope,
            required_artifacts=payload.required_artifacts,
            artifact_bundle=payload.artifact_bundle,
            expires_at=payload.expires_at,
            needs_ack=payload.needs_ack,
            summary=payload.summary,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/handoffs/{handoff_id}",
        response_model=OperatorHandoffResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_handoff(
        handoff_id: str,
        request: Request,
        operator_agent_id: str | None = None,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.get_handoff,
            handoff_id=handoff_id,
            operator_agent_id=operator_agent_id,
        )

    @app.post(
        "/v1/operator/handoffs/{handoff_id}/preflight",
        response_model=OperatorHandoffPreflightResponse,
        dependencies=[Depends(require_token)],
    )
    async def preflight_operator_handoff(
        handoff_id: str,
        payload: OperatorHandoffPreflightRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.preflight_handoff_delivery,
            operator_agent_id=payload.operator_agent_id,
            handoff_id=handoff_id,
            delivery=payload.delivery,
        )

    @app.get(
        "/v1/operator/handoff-actions",
        response_model=OperatorHandoffActionListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_handoff_actions(
        request: Request,
        target_agent_id: str | None = None,
        limit: int = 100,
        include_complete: bool = False,
    ) -> dict[str, object]:
        actions = await asyncio.to_thread(
            request.app.state.operator_service.list_handoff_actions,
            target_agent_id=target_agent_id,
            limit=limit,
            include_complete=include_complete,
        )
        return {"actions": actions}

    @app.post(
        "/v1/operator/broker-checkpoints/preflight",
        dependencies=[Depends(require_token)],
    )
    async def preflight_operator_broker_checkpoint(
        payload: OperatorBrokerCheckpointRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_broker_service.preflight,
            root_operator_agent_id=payload.root_operator_agent_id,
            target_fork_agent_id=payload.target_fork_agent_id,
            handoff_id=payload.handoff_id,
        )

    @app.post(
        "/v1/operator/broker-checkpoints",
        dependencies=[Depends(require_token)],
    )
    async def request_operator_broker_checkpoint(
        payload: OperatorBrokerCheckpointRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_broker_service.request,
            root_operator_agent_id=payload.root_operator_agent_id,
            target_fork_agent_id=payload.target_fork_agent_id,
            handoff_id=payload.handoff_id,
        )

    @app.get(
        "/v1/operator/broker-checkpoints",
        response_model=OperatorBrokerCheckpointListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_broker_checkpoints(
        request: Request,
        root_operator_agent_id: str | None = None,
        target_fork_agent_id: str | None = None,
        status_filter: str | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        statuses = tuple(
            item.strip().lower()
            for item in str(status_filter or "").split(",")
            if item.strip()
        )
        requests = await asyncio.to_thread(
            request.app.state.store.list_operator_broker_requests,
            root_operator_agent_id=root_operator_agent_id,
            target_fork_agent_id=target_fork_agent_id,
            statuses=statuses,
            limit=limit,
        )
        return {"requests": requests}

    @app.get(
        "/v1/operator/broker-checkpoints/{request_id}",
        response_model=OperatorBrokerCheckpointResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_broker_checkpoint(
        request_id: str,
        request: Request,
    ) -> dict[str, object]:
        result = await asyncio.to_thread(
            request.app.state.store.get_operator_broker_request,
            request_id,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="broker request not found")
        return result

    @app.post(
        "/v1/operator/broker-checkpoints/{request_id}/claim",
        response_model=OperatorBrokerCheckpointResponse,
        dependencies=[Depends(require_token)],
    )
    async def claim_operator_broker_checkpoint(
        request_id: str,
        payload: OperatorBrokerCheckpointClaimRequest,
        request: Request,
    ) -> dict[str, object]:
        require_local_broker_executor(request)
        return await run_operator_call(
            request.app.state.store.claim_operator_broker_request,
            request_id=request_id,
            executor_id=payload.executor_id,
            lease_seconds=payload.lease_seconds,
        )

    @app.post(
        "/v1/operator/broker-checkpoints/{request_id}/transition",
        response_model=OperatorBrokerCheckpointResponse,
        dependencies=[Depends(require_token)],
    )
    async def transition_operator_broker_checkpoint(
        request_id: str,
        payload: OperatorBrokerCheckpointTransitionRequest,
        request: Request,
    ) -> dict[str, object]:
        require_local_broker_executor(request)
        return await run_operator_call(
            request.app.state.store.transition_operator_broker_request,
            request_id=request_id,
            executor_id=payload.executor_id,
            status=payload.status,
            expected_statuses=payload.expected_statuses,
            action_transaction_id=payload.action_transaction_id,
            result=payload.result,
            error_code=payload.error_code,
            error=payload.error,
            lease_seconds=payload.lease_seconds,
        )

    @app.post(
        "/v1/operator/broker-checkpoints/{request_id}/cancel",
        response_model=OperatorBrokerCheckpointResponse,
        dependencies=[Depends(require_token)],
    )
    async def cancel_operator_broker_checkpoint(
        request_id: str,
        payload: OperatorBrokerCheckpointCancelRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_broker_service.cancel,
            root_operator_agent_id=payload.root_operator_agent_id,
            request_id=request_id,
            reason=payload.reason,
        )

    @app.post(
        "/v1/operator/broker-checkpoints/{request_id}/validate",
        dependencies=[Depends(require_token)],
    )
    async def validate_operator_broker_checkpoint(
        request_id: str,
        payload: OperatorBrokerCheckpointValidateRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_broker_service.validate,
            root_operator_agent_id=payload.root_operator_agent_id,
            request_id=request_id,
            action_transaction_id=payload.action_transaction_id,
        )

    @app.get(
        "/v1/operator/handoffs/{handoff_id}/execution-preflight",
        response_model=OperatorHandoffExecutionPreflightResponse,
        dependencies=[Depends(require_token)],
    )
    async def preflight_operator_handoff_execution(
        handoff_id: str,
        operator_agent_id: str,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.preflight_handoff_execution,
            operator_agent_id=operator_agent_id,
            handoff_id=handoff_id,
        )

    @app.post(
        "/v1/operator/handoffs/{handoff_id}/action-receipts",
        response_model=OperatorHandoffActionTransactionResponse,
        dependencies=[Depends(require_token)],
    )
    async def record_operator_handoff_action_receipt(
        handoff_id: str,
        payload: OperatorHandoffActionReceiptRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.record_handoff_action_receipt,
            operator_agent_id=payload.operator_agent_id,
            handoff_id=handoff_id,
            step=payload.step,
            outcome=payload.outcome,
            actor=payload.actor,
            evidence=payload.evidence,
        )

    @app.post(
        "/v1/operator/handoffs/{handoff_id}/approve",
        response_model=OperatorHandoffDeliveryResponse,
        dependencies=[Depends(require_token)],
    )
    async def approve_operator_handoff(
        handoff_id: str,
        payload: OperatorHandoffApproveRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.approve_handoff,
            operator_agent_id=payload.operator_agent_id,
            handoff_id=handoff_id,
            delivery=payload.delivery,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/handoffs/{handoff_id}/cancel",
        response_model=OperatorHandoffCancelResponse,
        dependencies=[Depends(require_token)],
    )
    async def cancel_operator_handoff(
        handoff_id: str,
        payload: OperatorHandoffCancelRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.cancel_handoff,
            operator_agent_id=payload.operator_agent_id,
            handoff_id=handoff_id,
            reason=payload.reason,
            detail=payload.detail,
            superseded_by_handoff_ids=payload.superseded_by_handoff_ids,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/handoffs/{handoff_id}/ack",
        response_model=OperatorHandoffResponse,
        dependencies=[Depends(require_token)],
    )
    async def ack_operator_handoff(
        handoff_id: str,
        payload: OperatorHandoffAckRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.ack_handoff,
            operator_agent_id=payload.operator_agent_id,
            handoff_id=handoff_id,
            status=payload.status,
            summary=payload.summary,
            detail=payload.detail,
            artifact_bundle=payload.artifact_bundle,
            metadata=payload.metadata,
        )

    @app.post(
        "/v1/operator/handoffs/{handoff_id}/status",
        response_model=OperatorHandoffResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_operator_handoff(
        handoff_id: str,
        payload: OperatorHandoffUpdateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.update_handoff,
            operator_agent_id=payload.operator_agent_id,
            handoff_id=handoff_id,
            status=payload.status,
            summary=payload.summary,
            detail=payload.detail,
            artifact_bundle=payload.artifact_bundle,
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
        include_events: bool = True,
        offset: int = 0,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        campaigns = await asyncio.to_thread(
            operator_service.campaign_status,
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            status=status,
            limit=limit,
            include_events=include_events,
            offset=offset,
        )
        return {"campaigns": campaigns}

    @app.get(
        "/v2/operator/campaigns",
        dependencies=[Depends(require_token)],
    )
    async def list_operator_campaigns_paginated(
        request: Request,
        operator_agent_id: str | None = None,
        status: str | None = None,
        cursor: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        campaigns = await asyncio.to_thread(
            operator_service.campaign_status,
            operator_agent_id=operator_agent_id,
            status=status,
            limit=limit + 1,
            include_events=False,
            offset=cursor,
        )
        has_more = len(campaigns) > limit
        page = campaigns[:limit]
        return {
            "campaigns": page,
            "cursor": cursor,
            "next_cursor": cursor + limit if has_more else None,
            "has_more": has_more,
        }

    @app.get(
        "/v2/operator/campaigns/{campaign_id}",
        response_model=OperatorCampaignResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_operator_campaign_detail(
        campaign_id: str,
        request: Request,
        operator_agent_id: str | None = None,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        campaigns = await asyncio.to_thread(
            operator_service.campaign_status,
            operator_agent_id=operator_agent_id,
            campaign_id=campaign_id,
            limit=1,
            include_events=True,
            offset=0,
        )
        if not campaigns:
            raise HTTPException(status_code=404, detail="operator campaign not found")
        return campaigns[0]

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
            operator_fork_id=payload.operator_fork_id,
            fork_track_id=payload.fork_track_id,
            delivery=payload.delivery,
            delivery_request_id=payload.delivery_request_id,
        )

    @app.post(
        "/v1/operator/review/escalations",
        response_model=CommandResponse,
        dependencies=[Depends(require_token)],
    )
    async def route_operator_review_escalation(
        payload: OperatorReviewEscalationRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.route_review_escalation,
            operator_agent_id=payload.operator_agent_id,
            review_fork_id=payload.review_fork_id,
            message=payload.message,
            route=payload.route,
            campaign_id=payload.campaign_id,
            assignment_id=payload.assignment_id,
            delivery=payload.delivery,
        )

    @app.get(
        "/v1/operator/project-spawns",
        response_model=OperatorProjectSpawnListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_operator_project_spawns(
        request: Request,
        operator_agent_id: str | None = None,
        review_fork_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        project_spawns = await asyncio.to_thread(
            operator_service.list_project_spawn_requests,
            operator_agent_id=operator_agent_id,
            review_fork_id=review_fork_id,
            status=status,
            limit=limit,
        )
        return {"project_spawns": project_spawns}

    @app.post(
        "/v1/operator/project-spawns",
        response_model=OperatorProjectSpawnResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_operator_project_spawn(
        payload: OperatorProjectSpawnRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.request_project_spawn,
            operator_agent_id=payload.operator_agent_id,
            review_fork_id=payload.review_fork_id,
            project_name=payload.project_name,
            instructions=payload.instructions,
            mode=payload.mode,
            target_slug=payload.target_slug,
            campaign_id=payload.campaign_id,
            assignment_id=payload.assignment_id,
            metadata=payload.metadata,
        )

    @app.get(
        "/v1/operator/project-spawns/{spawn_request_id}",
        response_model=OperatorProjectSpawnResponse,
        dependencies=[Depends(require_token)],
    )
    async def inspect_operator_project_spawn(
        spawn_request_id: str,
        request: Request,
        operator_agent_id: str | None = None,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.get_project_spawn_request,
            spawn_request_id=spawn_request_id,
            operator_agent_id=operator_agent_id,
        )

    @app.post(
        "/v1/operator/project-spawns/{spawn_request_id}/cancel",
        response_model=OperatorProjectSpawnResponse,
        dependencies=[Depends(require_token)],
    )
    async def cancel_operator_project_spawn(
        spawn_request_id: str,
        payload: OperatorProjectSpawnCancelRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.cancel_project_spawn_request,
            spawn_request_id=spawn_request_id,
            operator_agent_id=payload.operator_agent_id,
            reason=payload.reason,
        )

    @app.post(
        "/v1/operator/project-spawns/{spawn_request_id}/status",
        response_model=OperatorProjectSpawnResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_operator_project_spawn(
        spawn_request_id: str,
        payload: OperatorProjectSpawnUpdateRequest,
        request: Request,
    ) -> dict[str, object]:
        operator_service = request.app.state.operator_service
        return await run_operator_call(
            operator_service.update_project_spawn_request,
            spawn_request_id=spawn_request_id,
            status=payload.status,
            launched_agent_id=payload.launched_agent_id,
            tmux_pane_id=payload.tmux_pane_id,
            error=payload.error,
            metadata=payload.metadata,
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
        "/v1/operator/campaigns/{campaign_id}/successor",
        dependencies=[Depends(require_token)],
    )
    async def create_operator_campaign_successor(
        campaign_id: str,
        payload: OperatorCampaignSuccessorRequest,
        request: Request,
    ) -> dict[str, object]:
        return await run_operator_call(
            request.app.state.operator_service.create_campaign_successor,
            operator_agent_id=payload.operator_agent_id,
            predecessor_campaign_id=campaign_id,
        )


__all__ = ("register_operator_routes", "run_operator_call")
