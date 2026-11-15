from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from ..auth import get_store, require_controller, require_token
from ..schemas import (
    FileCreateRequest,
    FileDeleteRequest,
    FileDiagnosticsRequest,
    FileDiagnosticsResponse,
    FileDocumentResponse,
    FileDocumentWriteRequest,
    FileListResponse,
    FileMoveRequest,
    FilePreviewResponse,
    FileSearchResponse,
    FileTrashPruneApplyRequest,
    FileTrashPruneUndoRequest,
    FileTrashRestoreRequest,
    TerminalEditorStartRequest,
)
from ._shared import require_agent


def register_file_editor_routes(app: FastAPI) -> None:
    @app.get(
        "/v1/agents/{agent_id}/files",
        response_model=FileListResponse,
        dependencies=[Depends(require_token)],
    )
    async def list_agent_files(
        agent_id: str,
        request: Request,
        path: str = ".",
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(files.list_for_agent, agent, path=path)

    @app.get(
        "/v1/terminal-editor/profiles",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def terminal_editor_profile_view(request: Request) -> dict[str, Any]:
        return request.app.state.terminal_editor.profile_view()

    @app.post(
        "/v1/agents/{agent_id}/terminal-editor/preview",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def preview_terminal_editor(
        agent_id: str,
        payload: TerminalEditorStartRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        try:
            return await asyncio.to_thread(
                request.app.state.terminal_editor.preview,
                agent,
                path=payload.path,
                profile_id=payload.profile_id,
                line=payload.line,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/v1/agents/{agent_id}/terminal-editor",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def start_terminal_editor(
        agent_id: str,
        payload: TerminalEditorStartRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        try:
            return await asyncio.to_thread(
                request.app.state.terminal_editor.start,
                agent,
                path=payload.path,
                profile_id=payload.profile_id,
                line=payload.line,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/agents/{agent_id}/terminal-editor",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def terminal_editor_status(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        return await asyncio.to_thread(
            request.app.state.terminal_editor.status, agent_id
        )

    @app.delete(
        "/v1/agents/{agent_id}/terminal-editor",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def stop_terminal_editor(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        return await asyncio.to_thread(request.app.state.terminal_editor.stop, agent_id)

    @app.post(
        "/v1/agents/{agent_id}/terminal-editor/forget",
        response_model=dict[str, Any],
        dependencies=[Depends(require_controller)],
    )
    async def forget_terminal_editor(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        try:
            return await asyncio.to_thread(
                request.app.state.terminal_editor.forget,
                agent_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v1/agents/{agent_id}/runtime-bundle",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def agent_runtime_bundle(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, Any]:
        if store.get_agent(agent_id) is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        editor = await asyncio.to_thread(
            request.app.state.terminal_editor.status,
            agent_id,
        )
        codex = store.get_tmux_runtime_mapping(agent_id)
        codex_ready = bool(codex and str(codex.get("state") or "") == "ready")
        editor_ready = bool(editor.get("running"))
        return {
            "api_version": "agent-pbx.runtime-bundle/v1",
            "agent_id": agent_id,
            "codex": codex,
            "editor": editor,
            "codex_ready": codex_ready,
            "editor_ready": editor_ready,
            "ready": codex_ready and editor_ready,
        }

    @app.get(
        "/v1/agents/{agent_id}/files/preview",
        response_model=FilePreviewResponse,
        dependencies=[Depends(require_token)],
    )
    async def preview_agent_file(
        agent_id: str,
        request: Request,
        path: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(files.preview_for_agent, agent, path=path)

    @app.get(
        "/v1/agents/{agent_id}/files/document",
        response_model=FileDocumentResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_agent_file_document(
        agent_id: str,
        request: Request,
        path: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(files.document_for_agent, agent, path=path)

    @app.put(
        "/v1/agents/{agent_id}/files/document",
        response_model=FileDocumentResponse,
        dependencies=[Depends(require_token)],
    )
    async def put_agent_file_document(
        agent_id: str,
        payload: FileDocumentWriteRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(
            files.write_document_for_agent,
            agent,
            path=payload.path,
            text=payload.text,
            previous_sha256=payload.previous_sha256,
            previous_mtime=payload.previous_mtime,
            create=payload.create,
        )

    @app.post(
        "/v1/agents/{agent_id}/files/create",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def create_agent_file_path(
        agent_id: str,
        payload: FileCreateRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        files = request.app.state.files
        result = await asyncio.to_thread(
            files.create_path_for_agent,
            agent,
            path=payload.path,
            kind=payload.kind,
            text=payload.text,
        )
        if not result.get("error"):
            store.append_event(
                "file_created",
                {"agent_id": agent_id, "path": payload.path, "kind": payload.kind},
                agent_id,
            )
        return result

    @app.post(
        "/v1/agents/{agent_id}/files/move",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def move_agent_file_path(
        agent_id: str,
        payload: FileMoveRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        files = request.app.state.files
        result = await asyncio.to_thread(
            files.move_path_for_agent,
            agent,
            source=payload.source,
            destination=payload.destination,
            previous_sha256=payload.previous_sha256,
            previous_mtime=payload.previous_mtime,
        )
        if not result.get("error"):
            store.append_event(
                "file_moved",
                {
                    "agent_id": agent_id,
                    "source": payload.source,
                    "destination": payload.destination,
                },
                agent_id,
            )
        return result

    @app.get(
        "/v1/agents/{agent_id}/files/delete-preview",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def preview_agent_file_delete(
        agent_id: str,
        request: Request,
        path: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        return await asyncio.to_thread(
            request.app.state.files.delete_preview_for_agent,
            agent,
            path=path,
        )

    @app.post(
        "/v1/agents/{agent_id}/files/delete",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def delete_agent_file_path(
        agent_id: str,
        payload: FileDeleteRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        result = await asyncio.to_thread(
            request.app.state.files.delete_path_for_agent,
            agent,
            path=payload.path,
            preview_token=payload.preview_token,
            actor=payload.actor,
            confirm_tracked=payload.confirm_tracked,
            retention_days=payload.retention_days,
        )
        if result.get("trashed"):
            store.record_file_trash_entry(
                agent_id=agent_id,
                project=str(agent.get("project") or ""),
                root_path=str(result.get("cwd") or ""),
                result=result,
            )
        if result.get("deleted"):
            store.append_event(
                "file_trashed" if result.get("trashed") else "file_deleted_tracked",
                {
                    "agent_id": agent_id,
                    "path": result.get("path"),
                    "trash_id": result.get("trash_id"),
                    "git_state": result.get("git_state"),
                    "actor": payload.actor,
                },
                agent_id,
            )
        return result

    @app.get(
        "/v1/agents/{agent_id}/files/trash",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def list_agent_file_trash(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        result = await asyncio.to_thread(
            request.app.state.files.list_trash_for_agent, agent
        )
        result["records"] = store.list_file_trash_entries(agent_id, limit=500)
        return result

    @app.post(
        "/v1/agents/{agent_id}/files/trash/restore",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def restore_agent_file_trash(
        agent_id: str,
        payload: FileTrashRestoreRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        result = await asyncio.to_thread(
            request.app.state.files.restore_trash_for_agent,
            agent,
            trash_id=payload.trash_id,
        )
        if result.get("restored"):
            store.mark_file_trash_restored(payload.trash_id)
            store.append_event(
                "file_trash_restored",
                {
                    "agent_id": agent_id,
                    "trash_id": payload.trash_id,
                    "path": result.get("path"),
                },
                agent_id,
            )
        return result

    @app.get(
        "/v1/agents/{agent_id}/files/trash/prune-preview",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def preview_agent_file_trash_prune(
        agent_id: str,
        request: Request,
        cutoff: float | None = None,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        return await asyncio.to_thread(
            request.app.state.files.preview_trash_prune_for_agent,
            agent,
            now=cutoff,
        )

    @app.post(
        "/v1/agents/{agent_id}/files/trash/prune",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def apply_agent_file_trash_prune(
        agent_id: str,
        payload: FileTrashPruneApplyRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        result = await asyncio.to_thread(
            request.app.state.files.apply_trash_prune_for_agent,
            agent,
            preview_token=payload.preview_token,
            now=payload.cutoff,
        )
        if result.get("status") == "applied" and not result.get("error"):
            store.record_file_trash_prune_batch(
                agent_id=agent_id,
                preview_token=payload.preview_token,
                result=result,
            )
            store.append_event(
                "file_trash_pruned",
                {
                    "agent_id": agent_id,
                    "batch_id": result.get("batch_id"),
                    "count": result.get("count"),
                },
                agent_id,
            )
        return result

    @app.post(
        "/v1/agents/{agent_id}/files/trash/prune-undo",
        response_model=dict[str, Any],
        dependencies=[Depends(require_token)],
    )
    async def undo_agent_file_trash_prune(
        agent_id: str,
        payload: FileTrashPruneUndoRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = require_agent(store, agent_id)
        result = await asyncio.to_thread(
            request.app.state.files.undo_trash_prune_for_agent,
            agent,
            batch_id=payload.batch_id,
        )
        if result.get("status") == "undone" and not result.get("error"):
            store.mark_file_trash_prune_undone(payload.batch_id)
            store.append_event(
                "file_trash_prune_undone",
                {"agent_id": agent_id, "batch_id": payload.batch_id},
                agent_id,
            )
        return result

    @app.get(
        "/v1/agents/{agent_id}/files/search",
        response_model=FileSearchResponse,
        dependencies=[Depends(require_token)],
    )
    async def search_agent_files(
        agent_id: str,
        request: Request,
        query: str,
        path: str = ".",
        limit: int = Query(default=100, ge=1, le=500),
        regex: bool = False,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(
            files.search_for_agent,
            agent,
            query=query,
            path=path,
            limit=limit,
            regex=regex,
        )

    @app.post(
        "/v1/agents/{agent_id}/files/diagnostics",
        response_model=FileDiagnosticsResponse,
        dependencies=[Depends(require_token)],
    )
    async def check_agent_file_diagnostics(
        agent_id: str,
        payload: FileDiagnosticsRequest,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        agent = store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        files = request.app.state.files
        return await asyncio.to_thread(
            files.diagnostics_for_agent,
            agent,
            path=payload.path,
            tool=payload.tool,
        )


__all__ = ("register_file_editor_routes",)
