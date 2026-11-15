from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse

from ..auth import (
    ClientPrincipal,
    get_store,
    require_client_principal,
    require_controller,
    require_global_controller,
    require_token,
)
from ..remote import (
    REMOTE_API_VERSION,
    RemoteAuthError,
    authenticate_websocket,
    normalize_remote_client_id,
    principal_allows_agent,
    validate_view_state,
    websocket_token_is_current,
)
from ..schemas import (
    EventResponse,
    EventStreamEnvelope,
    RemoteClientStateRequest,
    RemoteControlRequest,
    RemoteTokenIssueRequest,
)
from ..security import generate_token


def _format_sse(event: dict[str, object]) -> str:
    return (
        f"id: {event['event_id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event)}\n\n"
    )


def register_remote_event_routes(
    app: FastAPI,
    *,
    resolved_config: Any,
    store: Any,
    capture_terminal_snapshot: Any,
) -> None:
    @app.post("/v2/remote/tokens")
    async def issue_remote_token(
        payload: RemoteTokenIssueRequest,
        controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        audience = payload.audience or resolved_config.remote_audience
        if audience != resolved_config.remote_audience:
            raise HTTPException(
                status_code=400, detail="token audience must match this server"
            )
        missing_agents = [
            agent_id
            for agent_id in payload.allowed_agent_ids
            if store.get_agent(agent_id) is None
        ]
        if missing_agents:
            raise HTTPException(
                status_code=404,
                detail=f"unknown allowed Agent ids: {', '.join(missing_agents)}",
            )
        raw_token = generate_token()
        record = store.add_token(
            raw_token,
            kind="remote",
            role=payload.role,
            label=payload.label,
            ttl_seconds=payload.ttl_seconds,
            audience=audience,
            client_id=payload.client_id,
            scopes=payload.scopes,
            allowed_agent_ids=payload.allowed_agent_ids,
        )
        public = next(
            item for item in store.list_tokens() if item["token_id"] == record.token_id
        )
        store.append_remote_audit(
            client_id=controller.client_id,
            token_id=controller.token_id,
            role=controller.role,
            action="remote_token_issue",
            outcome="allowed",
            metadata={
                "issued_token_id": record.token_id,
                "issued_role": record.role,
                "allowed_agent_count": len(record.allowed_agent_ids),
            },
        )
        return {"api_version": REMOTE_API_VERSION, "token": raw_token, "record": public}

    @app.get("/v2/remote/tokens")
    async def list_remote_tokens(
        include_revoked: bool = False,
        _controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return {
            "api_version": REMOTE_API_VERSION,
            "tokens": store.list_tokens(include_revoked=include_revoked),
        }

    @app.post("/v2/remote/tokens/{token_id}/revoke")
    async def revoke_remote_token(
        token_id: str,
        controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        if token_id == "runtime":
            raise HTTPException(
                status_code=409, detail="runtime token is managed by daemon config"
            )
        record = store.revoke_token(token_id)
        if record is None:
            raise HTTPException(status_code=404, detail="remote token not found")
        store.append_remote_audit(
            client_id=controller.client_id,
            token_id=controller.token_id,
            role=controller.role,
            action="remote_token_revoke",
            outcome="allowed",
            metadata={"revoked_token_id": token_id},
        )
        return {"api_version": REMOTE_API_VERSION, "record": record}

    @app.get("/v2/remote/me")
    async def remote_client_identity(
        principal: ClientPrincipal = Depends(require_client_principal),
    ) -> dict[str, object]:
        return {"api_version": REMOTE_API_VERSION, **principal.public_dict()}

    @app.get("/v2/remote/clients/{client_id}/view-state")
    async def get_remote_client_view_state(
        client_id: str,
        principal: ClientPrincipal = Depends(require_client_principal),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        session = store.get_remote_client_session(client_id)
        if session is None:
            session = store.upsert_remote_client_session(
                client_id=client_id,
                token_id=principal.token_id,
                role=principal.role,
                audience=principal.audience,
                allowed_agent_ids=principal.allowed_agent_ids,
                cursor=0,
                connected=False,
            )
        if principal.role != "local" and session.get("token_id") != principal.token_id:
            raise HTTPException(
                status_code=403, detail="remote client state belongs to another token"
            )
        return {"api_version": REMOTE_API_VERSION, "client": session}

    @app.put("/v2/remote/clients/{client_id}/view-state")
    async def update_remote_client_view_state(
        client_id: str,
        payload: RemoteClientStateRequest,
        principal: ClientPrincipal = Depends(require_client_principal),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        if principal.client_id and principal.client_id != client_id:
            raise HTTPException(
                status_code=403, detail="token is bound to another client id"
            )
        session = store.get_remote_client_session(client_id)
        if session is None:
            session = store.upsert_remote_client_session(
                client_id=client_id,
                token_id=principal.token_id,
                role=principal.role,
                audience=principal.audience,
                allowed_agent_ids=principal.allowed_agent_ids,
                cursor=payload.cursor or 0,
                connected=False,
            )
        if principal.role != "local" and session.get("token_id") != principal.token_id:
            raise HTTPException(
                status_code=403, detail="remote client state belongs to another token"
            )
        try:
            view_state = validate_view_state(payload.view_state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        updated = store.update_remote_client_view_state(
            client_id,
            token_id=principal.token_id,
            view_state=view_state,
            cursor=payload.cursor,
        )
        if updated is None:
            raise HTTPException(
                status_code=409, detail="remote client state ownership changed"
            )
        return {"api_version": REMOTE_API_VERSION, "client": updated}

    @app.get("/v2/remote/terminal/{entity_id}/snapshot")
    async def remote_terminal_snapshot(
        entity_id: str,
        lines: int = Query(default=80, ge=1, le=200),
        principal: ClientPrincipal = Depends(require_client_principal),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        if not resolved_config.remote_terminal_snapshots_enabled:
            raise HTTPException(
                status_code=404, detail="remote terminal snapshots are disabled"
            )
        if not principal_allows_agent(principal, entity_id):
            raise HTTPException(
                status_code=403, detail="token is not scoped to this Agent"
            )
        mapping = store.get_tmux_runtime_mapping(entity_id)
        if mapping is None:
            raise HTTPException(status_code=404, detail="runtime mapping not found")
        try:
            snapshot = await asyncio.to_thread(
                capture_terminal_snapshot,
                mapping,
                lines=lines,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_remote_audit(
            client_id=principal.client_id,
            token_id=principal.token_id,
            role=principal.role,
            action="terminal_snapshot",
            target_agent_id=entity_id,
            outcome="allowed",
            metadata={"lines": lines},
        )
        return snapshot

    @app.post("/v2/remote/control/{entity_id}/{action}")
    async def remote_lifecycle_control(
        entity_id: str,
        action: str,
        payload: RemoteControlRequest,
        controller: ClientPrincipal = Depends(require_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        if not principal_allows_agent(controller, entity_id):
            raise HTTPException(
                status_code=403, detail="token is not scoped to this Agent"
            )
        try:
            metadata = validate_view_state(payload.metadata)
            if payload.preview_token:
                result = await asyncio.to_thread(
                    app.state.lifecycle.apply_action,
                    entity_id,
                    action,
                    preview_token=payload.preview_token,
                    metadata={
                        **metadata,
                        "source": "remote_client_v2",
                        "remote_client_id": controller.client_id,
                    },
                )
                operation = "apply"
            else:
                result = await asyncio.to_thread(
                    app.state.lifecycle.preview_action,
                    entity_id,
                    action,
                )
                operation = "preview"
        except ValueError as exc:
            store.append_remote_audit(
                client_id=controller.client_id,
                token_id=controller.token_id,
                role=controller.role,
                action=f"lifecycle_{action}",
                target_agent_id=entity_id,
                outcome="denied",
                metadata={"error": str(exc)},
            )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_remote_audit(
            client_id=controller.client_id,
            token_id=controller.token_id,
            role=controller.role,
            action=f"lifecycle_{action}_{operation}",
            target_agent_id=entity_id,
            outcome="allowed",
        )
        return {"api_version": REMOTE_API_VERSION, "result": result}

    @app.get("/v2/remote/audit")
    async def list_remote_audit(
        limit: int = Query(default=100, ge=1, le=500),
        _controller: ClientPrincipal = Depends(require_global_controller),
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        return {
            "api_version": REMOTE_API_VERSION,
            "events": store.list_remote_audit(limit=limit),
        }

    @app.get(
        "/v1/events",
        response_model=list[EventResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_events(
        after_id: int = 0,
        limit: int = 100,
        tail: bool = False,
        store: Any = Depends(get_store),
    ) -> list[dict[str, object]]:
        bounded_limit = min(500, max(1, limit))
        if tail:
            return store.list_recent_events(limit=bounded_limit)
        return store.list_events(after_id=after_id, limit=bounded_limit)

    @app.get("/v1/events/stream", dependencies=[Depends(require_token)])
    async def event_stream(
        request: Request,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
        store: Any = Depends(get_store),
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

    @app.get(
        "/v2/events/snapshot",
        response_model=EventStreamEnvelope,
    )
    async def event_snapshot_v2(
        after_id: int = 0,
        limit: int = 100,
        include_state: bool = True,
        principal: ClientPrincipal = Depends(require_client_principal),
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            app.state.event_stream.envelope,
            after_id=after_id,
            limit=limit,
            include_state=include_state,
            role=None if principal.role == "local" else principal.role,
            allowed_agent_ids=principal.allowed_agent_ids,
        )

    @app.websocket("/v2/events/ws")
    async def event_websocket_v2(websocket: WebSocket) -> None:
        try:
            principal, raw_token = authenticate_websocket(
                authorization=websocket.headers.get("authorization", ""),
                origin=websocket.headers.get("origin"),
                config=resolved_config,
                store=store,
            )
        except RemoteAuthError as exc:
            await websocket.close(code=exc.code, reason=exc.reason)
            return
        try:
            after_id = max(0, int(websocket.query_params.get("after_id", "0")))
        except ValueError:
            await websocket.close(code=4400, reason="after_id must be an integer")
            return
        try:
            limit = min(
                500,
                max(1, int(websocket.query_params.get("limit", "100"))),
            )
        except ValueError:
            await websocket.close(code=4400, reason="limit must be an integer")
            return
        requested_client_id = websocket.query_params.get("client_id") or None
        if requested_client_id is not None:
            try:
                requested_client_id = normalize_remote_client_id(requested_client_id)
            except ValueError as exc:
                await websocket.close(code=4400, reason=str(exc))
                return
        if principal.client_id and requested_client_id not in {
            None,
            principal.client_id,
        }:
            await websocket.close(
                code=4403, reason="token is bound to another client id"
            )
            return
        try:
            client = app.state.event_clients.register(
                role=principal.role,
                cursor=after_id,
                client_id=principal.client_id or requested_client_id,
            )
        except RuntimeError:
            await websocket.close(code=4429, reason="remote client limit reached")
            return
        existing_session = await asyncio.to_thread(
            store.get_remote_client_session,
            client.client_id,
        )
        if (
            existing_session is not None
            and principal.role != "local"
            and existing_session.get("token_id") != principal.token_id
        ):
            app.state.event_clients.unregister(client.client_id)
            await websocket.close(
                code=4403, reason="client id belongs to another token"
            )
            return
        await asyncio.to_thread(
            store.upsert_remote_client_session,
            client_id=client.client_id,
            token_id=principal.token_id,
            role=principal.role,
            audience=principal.audience,
            allowed_agent_ids=principal.allowed_agent_ids,
            cursor=after_id,
            connected=True,
        )
        await asyncio.to_thread(
            store.append_remote_audit,
            client_id=client.client_id,
            token_id=principal.token_id,
            role=principal.role,
            action="event_stream_connect",
            outcome="allowed",
            metadata={"cursor": after_id},
        )
        await websocket.accept()
        cursor = after_id
        try:
            initial = await asyncio.to_thread(
                app.state.event_stream.envelope,
                after_id=after_id,
                limit=limit,
                include_state=True,
                role=None if principal.role == "local" else principal.role,
                allowed_agent_ids=principal.allowed_agent_ids,
            )
            initial["client_id"] = client.client_id
            initial["role"] = principal.role
            await websocket.send_json(initial)
            cursor = int(initial["cursor"])
            app.state.event_clients.update(client.client_id, cursor)
            while True:
                if not await asyncio.to_thread(
                    websocket_token_is_current,
                    principal,
                    raw_token,
                    config=resolved_config,
                    store=store,
                ):
                    await websocket.close(code=4403, reason="token revoked or expired")
                    break
                message: dict[str, Any] | None = None
                try:
                    incoming = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=0.5,
                    )
                    if isinstance(incoming, dict):
                        message = incoming
                except asyncio.TimeoutError:
                    pass
                if message is not None:
                    if not app.state.event_clients.allow_message(client.client_id):
                        await websocket.close(code=4429, reason="message rate exceeded")
                        break
                    message_type = str(message.get("type") or "").strip().lower()
                    if message_type == "ping":
                        await websocket.send_json(
                            {
                                "api_version": "agent-pbx.events/v2",
                                "kind": "pong",
                                "cursor": cursor,
                                "server_time": time.time(),
                            }
                        )
                    elif message_type in {"resume", "resync"}:
                        try:
                            cursor = max(0, int(message.get("after_id", cursor)))
                        except (TypeError, ValueError):
                            await websocket.send_json(
                                {
                                    "api_version": "agent-pbx.events/v2",
                                    "kind": "error",
                                    "code": "INVALID_CURSOR",
                                    "cursor": cursor,
                                }
                            )
                            continue
                envelope = await asyncio.to_thread(
                    app.state.event_stream.envelope,
                    after_id=cursor,
                    limit=limit,
                    include_state=bool(
                        message is not None
                        and str(message.get("type") or "").strip().lower() == "resync"
                    ),
                    role=None if principal.role == "local" else principal.role,
                    allowed_agent_ids=principal.allowed_agent_ids,
                )
                if envelope["events"] or envelope["reset_required"]:
                    envelope["client_id"] = client.client_id
                    envelope["role"] = principal.role
                    await websocket.send_json(envelope)
                    cursor = int(envelope["cursor"])
                app.state.event_clients.update(client.client_id, cursor)
        except WebSocketDisconnect:
            pass
        finally:
            app.state.event_clients.unregister(client.client_id)
            await asyncio.to_thread(
                store.disconnect_remote_client_session,
                client.client_id,
                cursor=cursor,
            )
            await asyncio.to_thread(
                store.append_remote_audit,
                client_id=client.client_id,
                token_id=principal.token_id,
                role=principal.role,
                action="event_stream_disconnect",
                outcome="complete",
                metadata={"cursor": cursor},
            )


__all__ = ("register_remote_event_routes",)
