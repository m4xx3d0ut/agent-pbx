from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, status

from ..auth import get_store, require_token
from ..codex.profiles import (
    managed_profile_for,
    managed_profile_view,
)
from ..codex.policy import load_codex_model_policy
from ..codex.skills import PersonalityOverlay
from ..runtime_tmux import (
    OuterTmuxContext,
    RuntimeServerMode,
    TmuxServerIdentity,
    normalize_runtime_server_mode,
    recursive_attachment_reason,
    runtime_mapping_tmux_binary,
    runtime_server_id,
    validate_tmux_socket,
)
from ..schemas import (
    CodexCliUpdateRequest,
    CodexCliUpdateResponse,
    CodexConfigPatchRequest,
    CodexConfigPatchResponse,
    CodexConfigViewResponse,
    CodexRuntimeEventRequest,
    ManagedSkillApplyRequest,
    ModelElevationActivateRequest,
    ModelElevationDecisionRequest,
    ModelElevationFinishRequest,
    ModelElevationRequest,
    ModelElevationResponse,
    ModelElevationRevertRequest,
    ModelElevationTransitionRequest,
    TmuxRuntimeMappingRequest,
    TmuxRuntimeMappingResponse,
    TmuxWriterLeaseRequest,
)


def register_runtime_routes(
    app: FastAPI,
    *,
    inspect_tmux_runtime_mapping_live: Any,
    inspect_codex_model_catalog: Any,
    list_runtime_panes: Any,
    load_codex_config_view: Any,
    patch_codex_config: Any,
    process_start_ticks: Any,
    update_codex_cli_package: Any,
    _managed_skill_service: Any,
) -> None:
    @app.get(
        "/v1/codex/config",
        response_model=CodexConfigViewResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_codex_config() -> dict[str, object]:
        return load_codex_config_view()

    @app.patch(
        "/v1/codex/config",
        response_model=CodexConfigPatchResponse,
        dependencies=[Depends(require_token)],
    )
    async def patch_global_codex_config(
        payload: CodexConfigPatchRequest,
    ) -> dict[str, object]:
        try:
            result = await asyncio.to_thread(
                patch_codex_config,
                updates=payload.updates,
                remove=payload.remove,
                agent_pbx_mcp=(
                    payload.agent_pbx_mcp.model_dump(exclude_none=True)
                    if payload.agent_pbx_mcp is not None
                    else None
                ),
                secret_updates=[
                    item.model_dump(exclude_none=True)
                    for item in payload.secret_updates
                ],
                keymap_preset=payload.keymap_preset,
                tui_theme=payload.tui_theme,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {
            "ok": True,
            "changed_paths": list(result.changed_paths),
            "backup_path": result.backup_path,
            "config": result.view,
        }

    @app.post(
        "/v1/codex/cli/update",
        response_model=CodexCliUpdateResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_codex_cli(
        payload: CodexCliUpdateRequest = Body(default_factory=CodexCliUpdateRequest),
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                update_codex_cli_package,
                target=payload.target,
                timeout=payload.timeout_seconds,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

    @app.get(
        "/v2/codex/capabilities",
        dependencies=[Depends(require_token)],
    )
    async def get_codex_capabilities(refresh: bool = False) -> dict[str, object]:
        snapshot = await asyncio.to_thread(
            app.state.codex_capabilities.inspect,
            use_cache=not refresh,
        )
        return snapshot.as_dict()

    @app.get(
        "/v2/codex/event-bridge",
        dependencies=[Depends(require_token)],
    )
    async def get_codex_event_bridge() -> dict[str, object]:
        return app.state.codex_event_bridge.status()

    @app.get(
        "/v2/agents/{agent_id}/codex-runtime",
        dependencies=[Depends(require_token)],
    )
    async def get_agent_codex_runtime(
        agent_id: str,
        request: Request,
    ) -> dict[str, object]:
        try:
            return await asyncio.to_thread(
                request.app.state.codex_runtime.snapshot,
                agent_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="agent not registered") from exc

    @app.post(
        "/v2/agents/{agent_id}/codex-runtime/adopt-model",
        dependencies=[Depends(require_token)],
    )
    async def adopt_agent_codex_runtime_model(
        agent_id: str,
        request: Request,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        """Adopt one observed direct ``/model`` choice for this PBX entity.

        This updates entity-local resume metadata only. It never changes the
        committed profile registry, workstation role defaults, or global Codex
        configuration.
        """

        try:
            snapshot, current_selection = await asyncio.to_thread(
                request.app.state.codex_runtime.snapshot_with_current_model_selection,
                agent_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="agent not registered") from exc
        profile = snapshot.get("profile")
        if not isinstance(profile, dict):
            raise HTTPException(status_code=409, detail="runtime model is not observed")
        if current_selection is None:
            raise HTTPException(status_code=409, detail="runtime model is not observed")
        selection_class = current_selection.classification.value
        if selection_class not in {"managed_qualified", "catalog_observed"}:
            raise HTTPException(
                status_code=409,
                detail=(
                    "only a current managed or live-catalog Codex selection can "
                    "be adopted"
                ),
            )
        model = current_selection.model
        effort = current_selection.reasoning_effort
        if not model or not effort:
            raise HTTPException(
                status_code=409,
                detail="runtime model and reasoning effort must both be observed",
            )
        managed_profile_id = str(current_selection.managed_profile_id or "").strip()
        if not current_selection.allowed or current_selection.requires_approval:
            await asyncio.to_thread(store.expire_model_elevations)
            matching_lease = next(
                (
                    lease
                    for lease in await asyncio.to_thread(
                        store.list_model_elevations,
                        agent_id=agent_id,
                        limit=20,
                    )
                    if (
                        (
                            lease_profile := managed_profile_for(
                                lease.get("requested_profile")
                            )
                        )
                        is not None
                        and lease_profile.profile_id == managed_profile_id
                    )
                    and str(lease.get("status") or "") in {"approved", "active"}
                ),
                None,
            )
            if matching_lease is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "the observed model requires an unexpired matching "
                        "bounded elevation lease"
                    ),
                )
        adopted_profile_id = managed_profile_id or f"catalog:{model}:{effort}"
        updates = {
            "codex_model_preset": adopted_profile_id,
            "codex_model_label": f"{model} / {effort}",
            "codex_model": model,
            "codex_model_reasoning_effort": effort,
            "codex_model_verbosity": str(profile.get("verbosity") or "high"),
            "codex_model_reasoning_summary": str(
                profile.get("reasoning_summary") or "detailed"
            ),
            "codex_model_service_tier": str(profile.get("service_tier") or ""),
            "codex_model_provider": current_selection.model_provider,
            "codex_model_selection_class": selection_class,
            "codex_model_adopted_at": time.time(),
        }
        updated = await asyncio.to_thread(
            store.merge_agent_metadata,
            agent_id,
            updates,
            event_type="codex_runtime_model_adopted",
            event_payload={
                "profile_id": adopted_profile_id,
                "model": model,
                "reasoning_effort": effort,
                "selection_class": selection_class,
            },
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="agent not registered")
        await asyncio.to_thread(store.merge_operator_fork_metadata, agent_id, updates)
        return {
            "agent_id": agent_id,
            "profile_id": adopted_profile_id,
            "model": model,
            "reasoning_effort": effort,
            "selection_class": selection_class,
            "scope": "entity_local_resume",
        }

    @app.post(
        "/v2/agents/{agent_id}/codex-runtime/events",
        dependencies=[Depends(require_token)],
    )
    async def observe_agent_codex_runtime(
        agent_id: str,
        payload: CodexRuntimeEventRequest,
        request: Request,
    ) -> dict[str, object]:
        # The projection is deliberately bounded before it reaches the event
        # journal. The service itself keeps only known public protocol fields.
        if len(json.dumps(payload.params, separators=(",", ":"))) > 1_000_000:
            raise HTTPException(
                status_code=413, detail="runtime event payload is too large"
            )
        try:
            return await asyncio.to_thread(
                request.app.state.codex_runtime.observe_protocol,
                agent_id,
                thread_id=payload.thread_id,
                method=payload.method,
                params=payload.params,
                observed_at=payload.observed_at,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="agent not registered") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get(
        "/v2/codex/profiles",
        dependencies=[Depends(require_token)],
    )
    async def get_codex_profiles() -> dict[str, object]:
        catalog = await asyncio.to_thread(inspect_codex_model_catalog)
        model_policy = await asyncio.to_thread(load_codex_model_policy)
        overlay_path = Path(
            os.getenv("AGENT_PBX_PERSONALITY_OVERLAY")
            or "~/.config/agent-pbx/personality.local.md"
        ).expanduser()
        overlay = await asyncio.to_thread(PersonalityOverlay.load, overlay_path)
        return {
            "api_version": "agent-pbx.codex-profiles/v2",
            "default_profile": model_policy.caller_profile_id,
            "role_defaults": model_policy.public_dict()["role_defaults"],
            "model_policy": model_policy.public_dict(),
            "profiles": managed_profile_view(catalog),
            "personality": {
                "committed": "roses-architect",
                "local_overlay": overlay.public_dict(),
            },
        }

    @app.get(
        "/v2/agents/{agent_id}/skills/preview",
        dependencies=[Depends(require_token)],
    )
    async def preview_managed_skills(
        agent_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        service = _managed_skill_service(store, agent_id)
        previews = await asyncio.to_thread(service.preview)
        return {
            "agent_id": agent_id,
            "project_root": str(service.project_root),
            "packs": [item.__dict__ for item in previews],
            "estimated_tokens": sum(item.estimated_tokens for item in previews),
        }

    @app.post(
        "/v2/agents/{agent_id}/skills/apply",
        dependencies=[Depends(require_token)],
    )
    async def apply_managed_skills(
        agent_id: str,
        payload: ManagedSkillApplyRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        service = _managed_skill_service(store, agent_id, payload.pack_names)
        try:
            result = await asyncio.to_thread(
                service.apply,
                max_estimated_tokens=payload.max_estimated_tokens,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event(
            "managed_skills_applied",
            {
                "agent_id": agent_id,
                "project_root": str(service.project_root),
                "changed": result["changed"],
                "estimated_tokens": result["manifest"]["estimated_tokens"],
            },
            agent_id,
        )
        return {"agent_id": agent_id, **result}

    @app.post(
        "/v2/agents/{agent_id}/skills/rollback",
        dependencies=[Depends(require_token)],
    )
    async def rollback_managed_skills(
        agent_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        service = _managed_skill_service(store, agent_id)
        try:
            result = await asyncio.to_thread(service.rollback)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event(
            "managed_skills_rolled_back",
            {"agent_id": agent_id, "project_root": str(service.project_root), **result},
            agent_id,
        )
        return {"agent_id": agent_id, **result}

    @app.post(
        "/v2/codex/elevations",
        response_model=ModelElevationResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_model_elevation(
        payload: ModelElevationRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        profile = managed_profile_for(payload.requested_profile)
        if profile is None or not profile.elevated:
            raise HTTPException(
                status_code=400, detail="requested profile is not elevated"
            )
        model_policy = load_codex_model_policy()
        if (
            not profile.migration_only
            and profile.profile_id not in model_policy.elevation_profile_ids
        ):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"elevation profile {profile.profile_id!r} is not allowed by "
                    "the active Agent PBX model policy"
                ),
            )
        try:
            request_data = payload.model_dump()
            request_data["requested_profile"] = profile.profile_id
            lease, created = store.request_or_reuse_model_elevation(
                **request_data
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result = {**lease, "reused": not created}
        store.append_event(
            "model_elevation_requested" if created else "model_elevation_reused",
            result,
            payload.agent_id,
        )
        return result

    @app.post(
        "/v2/codex/elevations/{lease_id}/decision",
        response_model=ModelElevationResponse,
        dependencies=[Depends(require_token)],
    )
    async def decide_model_elevation(
        lease_id: str,
        payload: ModelElevationDecisionRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        existing = store.get_model_elevation(lease_id)
        if existing is None:
            raise HTTPException(
                status_code=404, detail="model elevation lease not found"
            )
        if payload.approved and payload.approved_by == existing["agent_id"]:
            raise HTTPException(
                status_code=409, detail="an agent cannot approve its own elevation"
            )
        try:
            lease = store.decide_model_elevation(lease_id, **payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event("model_elevation_decided", lease, str(lease["agent_id"]))
        return lease

    @app.post(
        "/v2/codex/elevations/{lease_id}/activate",
        response_model=ModelElevationResponse,
        dependencies=[Depends(require_token)],
    )
    async def activate_model_elevation(
        lease_id: str,
        payload: ModelElevationActivateRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        try:
            lease = store.activate_model_elevation(
                lease_id, transition=payload.transition
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event("model_elevation_activated", lease, str(lease["agent_id"]))
        return lease

    @app.post(
        "/v2/codex/elevations/{lease_id}/transition",
        response_model=ModelElevationResponse,
        dependencies=[Depends(require_token)],
    )
    async def update_model_elevation_transition(
        lease_id: str,
        payload: ModelElevationTransitionRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        try:
            lease = store.update_model_elevation_transition(
                lease_id,
                transition=payload.transition,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event(
            "model_elevation_transition_updated",
            lease,
            str(lease["agent_id"]),
        )
        return lease

    @app.post(
        "/v2/codex/elevations/{lease_id}/revert",
        response_model=ModelElevationResponse,
        dependencies=[Depends(require_token)],
    )
    async def request_model_elevation_revert(
        lease_id: str,
        payload: ModelElevationRevertRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        try:
            lease = store.mark_model_elevation_revert_pending(
                lease_id,
                reason=payload.reason,
                transition=payload.transition,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event(
            "model_elevation_revert_pending",
            lease,
            str(lease["agent_id"]),
        )
        return lease

    @app.post(
        "/v2/codex/elevations/{lease_id}/finish",
        response_model=ModelElevationResponse,
        dependencies=[Depends(require_token)],
    )
    async def finish_model_elevation(
        lease_id: str,
        payload: ModelElevationFinishRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        try:
            lease = store.finish_model_elevation(
                lease_id,
                status=payload.status,
                transition=payload.transition,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event("model_elevation_finished", lease, str(lease["agent_id"]))
        return lease

    @app.get(
        "/v2/codex/elevations",
        response_model=list[ModelElevationResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_model_elevations(
        agent_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
        store: Any = Depends(get_store),
    ) -> list[dict[str, object]]:
        store.expire_model_elevations()
        return store.list_model_elevations(agent_id=agent_id, limit=limit)

    @app.post(
        "/v2/tmux/runtimes/{entity_id}",
        response_model=TmuxRuntimeMappingResponse,
        dependencies=[Depends(require_token)],
    )
    async def register_tmux_runtime(
        entity_id: str,
        payload: TmuxRuntimeMappingRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        if store.get_agent(entity_id) is None:
            raise HTTPException(status_code=404, detail="agent not found")
        socket_path = Path(payload.socket_path).expanduser()
        if not socket_path.is_absolute():
            raise HTTPException(
                status_code=400, detail="tmux socket path must be absolute"
            )
        ready, message = await asyncio.to_thread(validate_tmux_socket, socket_path)
        if not ready:
            raise HTTPException(status_code=409, detail=message)
        reason = recursive_attachment_reason(
            target_session=payload.session_name,
            target_pane_id=payload.pane_id,
            outer=(
                None
                if not payload.origin_session_name
                else OuterTmuxContext(
                    payload.origin_session_name,
                    "",
                    "",
                    payload.origin_client_tty or "",
                )
            ),
        )
        if reason:
            raise HTTPException(status_code=409, detail=reason)
        mode = normalize_runtime_server_mode(payload.server_mode)
        # Runtime mappings may belong to a qualified tmux server whose client
        # protocol differs from the daemon's default tmux.  The TUI records
        # that executable in mapping metadata; use it for the registration
        # preflight as well as subsequent reconciliation and attachment.
        # Probing a newer server with the host's older client can fail before
        # the mapping is stored, leaving a valid managed pane orphaned.
        tmux_bin = runtime_mapping_tmux_binary({"metadata": payload.metadata})
        identity = TmuxServerIdentity(
            mode,
            mode,
            runtime_server_id(socket_path),
            str(socket_path.resolve(strict=True)),
            True,
            mode is not RuntimeServerMode.DEDICATED,
            tmux_bin=tmux_bin,
        )
        panes = await asyncio.to_thread(list_runtime_panes, identity)
        pane = next((item for item in panes if item.pane_id == payload.pane_id), None)
        if pane is None:
            raise HTTPException(
                status_code=409, detail="tmux pane is absent from the selected server"
            )
        if pane.session_name != payload.session_name:
            raise HTTPException(
                status_code=409, detail="tmux pane belongs to another session"
            )
        observed_ticks = await asyncio.to_thread(process_start_ticks, pane.pane_pid)
        if payload.pane_pid and pane.pane_pid and payload.pane_pid != pane.pane_pid:
            raise HTTPException(
                status_code=409, detail="tmux pane process identity changed"
            )
        if (
            payload.process_start_ticks
            and observed_ticks
            and payload.process_start_ticks != observed_ticks
        ):
            raise HTTPException(
                status_code=409, detail="tmux pane process start evidence changed"
            )
        mapping_metadata = dict(payload.metadata)
        if mapping_metadata.get("mapping_source") == "in_place_restart":
            mapping_metadata.pop("last_live_validation", None)
            mapping_metadata.pop("live_validated_at", None)
        mapping = store.upsert_tmux_runtime_mapping(
            entity_id=entity_id,
            server_mode=mode.value,
            server_id=identity.server_id,
            socket_path=identity.socket_path,
            session_name=pane.session_name,
            window_id=pane.window_id,
            window_name=pane.window_name,
            pane_id=pane.pane_id,
            pane_pid=pane.pane_pid,
            process_start_ticks=observed_ticks,
            codex_session_id=payload.codex_session_id,
            cwd=pane.cwd,
            origin_client_tty=payload.origin_client_tty,
            origin_session_name=payload.origin_session_name,
            state="ready",
            metadata=mapping_metadata,
        )
        store.append_event("tmux_runtime_registered", mapping, entity_id)
        return mapping

    @app.get(
        "/v2/tmux/runtimes",
        response_model=list[TmuxRuntimeMappingResponse],
        dependencies=[Depends(require_token)],
    )
    async def list_tmux_runtimes(
        store: Any = Depends(get_store),
    ) -> list[dict[str, object]]:
        return store.list_tmux_runtime_mappings()

    @app.get(
        "/v2/tmux/runtimes/{entity_id}",
        response_model=TmuxRuntimeMappingResponse,
        dependencies=[Depends(require_token)],
    )
    async def get_tmux_runtime(
        entity_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        mapping = store.get_tmux_runtime_mapping(entity_id)
        if mapping is None:
            raise HTTPException(
                status_code=404, detail="tmux runtime mapping not found"
            )
        assessment = await inspect_tmux_runtime_mapping_live(mapping)
        assessed_state = str(assessment["state"])
        stopping = str(mapping.get("state") or "") == "stopping"
        target_state = "stopping" if stopping else assessed_state
        if target_state != str(mapping.get("state") or "") or not assessment["safe"]:
            mapping = store.set_tmux_runtime_state(
                entity_id,
                state=target_state,
                metadata={
                    "last_live_validation": assessment,
                    "live_validated_at": time.time(),
                },
                clear_writer_lease=not bool(assessment["safe"]),
            )
            if not assessment["safe"]:
                store.append_event_if_changed(
                    "tmux_runtime_reconciliation_required",
                    assessment,
                    entity_id,
                )
        return mapping

    @app.post(
        "/v2/tmux/reconcile",
        dependencies=[Depends(require_token)],
    )
    async def reconcile_tmux_runtimes(
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        results: list[dict[str, object]] = []
        for mapping in store.list_tmux_runtime_mappings():
            assessment = await inspect_tmux_runtime_mapping_live(mapping)
            stopping = str(mapping.get("state") or "") == "stopping"
            updated = store.set_tmux_runtime_state(
                str(mapping["entity_id"]),
                state="stopping" if stopping else str(assessment["state"]),
                metadata={
                    "last_reconciliation": assessment,
                    "reconciled_at": time.time(),
                },
                clear_writer_lease=not bool(assessment["safe"]),
            )
            results.append({"mapping": updated, "assessment": assessment})
            if not assessment["safe"]:
                store.append_event_if_changed(
                    "tmux_runtime_reconciliation_required",
                    assessment,
                    str(mapping["entity_id"]),
                )
        return {"count": len(results), "results": results}

    @app.post(
        "/v2/tmux/runtimes/{entity_id}/writer/acquire",
        response_model=TmuxRuntimeMappingResponse,
        dependencies=[Depends(require_token)],
    )
    async def acquire_tmux_runtime_writer(
        entity_id: str,
        payload: TmuxWriterLeaseRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        previous = store.get_tmux_runtime_mapping(entity_id)
        if previous is None:
            raise HTTPException(
                status_code=404, detail="tmux runtime mapping not found"
            )
        if str(previous.get("state") or "") == "stopping":
            raise HTTPException(
                status_code=409,
                detail="tmux runtime is stopping; writer acquisition is disabled",
            )
        assessment = await inspect_tmux_runtime_mapping_live(previous)
        if not assessment["safe"]:
            store.set_tmux_runtime_state(
                entity_id,
                state=str(assessment["state"]),
                metadata={
                    "last_live_validation": assessment,
                    "live_validated_at": time.time(),
                },
                clear_writer_lease=True,
            )
            store.append_event_if_changed(
                "tmux_runtime_reconciliation_required",
                assessment,
                entity_id,
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"tmux runtime is {assessment['state']}: {assessment['message']}"
                ),
            )
        if str(previous.get("state") or "") != "ready":
            previous = store.set_tmux_runtime_state(entity_id, state="ready")
        was_current_owner = bool(
            previous
            and previous.get("writer_lease_active")
            and previous.get("writer_client_id") == payload.client_id
        )
        try:
            mapping = store.acquire_tmux_writer_lease(
                entity_id,
                client_id=payload.client_id,
                ttl_seconds=payload.ttl_seconds,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        # Renewals happen frequently while an embedded terminal is visible.
        # Only ownership transitions belong in the durable event stream.
        if not was_current_owner:
            store.append_event(
                "tmux_runtime_writer_acquired",
                {"entity_id": entity_id, "client_id": payload.client_id},
                entity_id,
            )
        return mapping

    @app.post(
        "/v2/tmux/runtimes/{entity_id}/writer/release",
        response_model=TmuxRuntimeMappingResponse,
        dependencies=[Depends(require_token)],
    )
    async def release_tmux_runtime_writer(
        entity_id: str,
        payload: TmuxWriterLeaseRequest,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        try:
            mapping = store.release_tmux_writer_lease(
                entity_id,
                client_id=payload.client_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.append_event(
            "tmux_runtime_writer_released",
            {"entity_id": entity_id, "client_id": payload.client_id},
            entity_id,
        )
        return mapping

    @app.delete(
        "/v2/tmux/runtimes/{entity_id}",
        dependencies=[Depends(require_token)],
    )
    async def delete_tmux_runtime(
        entity_id: str,
        store: Any = Depends(get_store),
    ) -> dict[str, object]:
        mapping = store.get_tmux_runtime_mapping(entity_id)
        if mapping is None:
            raise HTTPException(
                status_code=404, detail="tmux runtime mapping not found"
            )
        if mapping.get("writer_lease_active"):
            raise HTTPException(
                status_code=409, detail="release the active writer lease first"
            )
        deleted = store.delete_tmux_runtime_mapping(entity_id)
        store.append_event(
            "tmux_runtime_mapping_deleted", {"entity_id": entity_id}, entity_id
        )
        return {"deleted": deleted, "entity_id": entity_id}


__all__ = ("register_runtime_routes",)
