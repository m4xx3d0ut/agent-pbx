from __future__ import annotations

import asyncio
from typing import Any, Callable

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status

from ..auth import get_store, require_token
from ..joplin import (
    JoplinApiError,
    JoplinConflictError,
    JoplinGateway,
    JoplinScopeError,
    agent_session_id,
    format_copy_body,
    scoped_note_title,
)
from ..schemas import (
    JoplinConflictResolveRequest,
    JoplinCopyRequest,
    JoplinDocumentRequest,
    JoplinEditDraftRequest,
    JoplinEditSessionResponse,
    JoplinEditStartRequest,
    JoplinLogAppendRequest,
    JoplinLogResponse,
    JoplinNativeConflictPromoteRequest,
    JoplinNativeConflictPromoteResponse,
    JoplinNoteCreateRequest,
    JoplinNoteResponse,
    JoplinNoteSummary,
    JoplinNoteUpdateRequest,
    JoplinStatusResponse,
    JoplinSyncJobResponse,
    JoplinSyncStatusResponse,
)
from ._shared import require_agent


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
    except JoplinConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.as_error(),
        ) from exc
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
            else status.HTTP_409_CONFLICT
            if exc.status_code == status.HTTP_409_CONFLICT
            else status.HTTP_401_UNAUTHORIZED
            if exc.code == "JOPLIN_AUTH_ERROR"
            else status.HTTP_429_TOO_MANY_REQUESTS
            if exc.code == "JOPLIN_RATE_LIMITED"
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        detail = {
            "code": exc.code,
            "message": str(exc),
            "retryable": exc.retryable,
        }
        encryption = getattr(exc, "encryption", None)
        if isinstance(encryption, dict):
            detail["encryption"] = encryption
        raise HTTPException(
            status_code=response_status,
            detail=detail,
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JOPLIN_EDIT_INVALID",
                "message": str(exc),
                "retryable": False,
            },
        ) from exc


def joplin_copy_source(
    store: Any,
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


def register_joplin_routes(app: FastAPI) -> None:
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

    @app.post(
        "/v1/agents/{agent_id}/joplin/sync/retry",
        response_model=JoplinSyncJobResponse,
        dependencies=[Depends(require_token)],
    )
    async def retry_agent_joplin_write_sync(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        require_agent(store, agent_id)
        joplin = request.app.state.joplin
        return await run_joplin_call(joplin.retry_latest_failed_write, agent_id)

    @app.put(
        "/v1/joplin/edits/{edit_id}/draft",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def update_joplin_edit_draft(
        edit_id: str,
        payload: JoplinEditDraftRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        edit = await asyncio.to_thread(
            store.update_joplin_edit_draft,
            edit_id,
            title=payload.title,
            body=payload.body,
        )
        if edit is None:
            raise HTTPException(status_code=404, detail="Joplin edit session not found")
        return edit

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

    @app.post(
        "/v1/projects/{project}/joplin/notes/{note_id}/edit",
        response_model=JoplinEditSessionResponse,
        dependencies=[Depends(require_token)],
    )
    async def begin_project_joplin_edit(
        project: str,
        note_id: str,
        payload: JoplinEditStartRequest,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.begin_edit_for_project,
            project,
            note_id,
            client_id=payload.client_id,
        )

    @app.post(
        "/v1/projects/{project}/joplin/notes/{note_id}/promote-conflict",
        response_model=JoplinNativeConflictPromoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def promote_project_joplin_native_conflict(
        project: str,
        note_id: str,
        payload: JoplinNativeConflictPromoteRequest,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.promote_native_conflict_for_project,
            project,
            note_id,
            title=payload.title,
            expected_revision=payload.expected_revision,
            expected_original_id=payload.expected_original_id,
        )

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
            base_revision=payload.base_revision,
            base_title=payload.base_title,
            base_body=payload.base_body,
            edit_id=payload.edit_id,
            force=payload.force,
        )

    @app.post(
        "/v1/projects/{project}/joplin/conflicts/{conflict_id}/resolve",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def resolve_project_joplin_conflict(
        project: str,
        conflict_id: str,
        payload: JoplinConflictResolveRequest,
        request: Request,
    ) -> dict[str, object]:
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.resolve_edit_conflict,
            conflict_id,
            resolution=payload.resolution,
            project=project,
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(joplin.get_note_for_agent, agent, note_id)

    @app.post(
        "/v1/agents/{agent_id}/joplin/notes/{note_id}/edit",
        response_model=JoplinEditSessionResponse,
        dependencies=[Depends(require_token)],
    )
    async def begin_agent_joplin_edit(
        agent_id: str,
        note_id: str,
        payload: JoplinEditStartRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.begin_edit_for_agent,
            agent,
            note_id,
            client_id=payload.client_id,
        )

    @app.post(
        "/v1/agents/{agent_id}/joplin/notes/{note_id}/promote-conflict",
        response_model=JoplinNativeConflictPromoteResponse,
        dependencies=[Depends(require_token)],
    )
    async def promote_agent_joplin_native_conflict(
        agent_id: str,
        note_id: str,
        payload: JoplinNativeConflictPromoteRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.promote_native_conflict_for_agent,
            agent,
            note_id,
            title=payload.title,
            expected_revision=payload.expected_revision,
            expected_original_id=payload.expected_original_id,
        )

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
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.update_note_for_agent,
            agent,
            note_id,
            title=payload.title,
            body=payload.body,
            base_revision=payload.base_revision,
            base_title=payload.base_title,
            base_body=payload.base_body,
            edit_id=payload.edit_id,
            force=payload.force,
        )

    @app.post(
        "/v1/agents/{agent_id}/joplin/conflicts/{conflict_id}/resolve",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def resolve_agent_joplin_conflict(
        agent_id: str,
        conflict_id: str,
        payload: JoplinConflictResolveRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        joplin = require_joplin(request)
        return await run_joplin_call(
            joplin.resolve_edit_conflict,
            conflict_id,
            resolution=payload.resolution,
            agent=agent,
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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
            title=payload.title or scoped_note_title(agent_session_id(agent), "COPY"),
        )

    @app.post(
        "/v1/agents/{agent_id}/joplin/log/start",
        response_model=JoplinLogResponse,
        dependencies=[Depends(require_token)],
    )
    async def start_agent_joplin_log(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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
        store: Any = Depends(get_store),
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


__all__ = ("register_joplin_routes",)
