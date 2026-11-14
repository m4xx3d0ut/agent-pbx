from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from agent_pbx import terminal_delivery
from agent_pbx.api import run_operator_call
from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.operator import OperatorService
from agent_pbx.schemas import AgentRegisterRequest, CommandCreateRequest
from agent_pbx.store import Store
from agent_pbx.terminal_delivery import (
    IdleTargetDeliveryError,
    IdleTargetDeliveryOutcome,
    IdleTargetDeliveryRequest,
    IdleTargetDeliveryResult,
    IdleTargetDeliveryService,
    classify_codex_terminal_capture,
)
from agent_pbx.tmux import TmuxPane


def _register_target(
    store: Store,
    *,
    pbx_mode: str = "report",
    codex_session_id: str = "session-1",
    workspace: str = "/srv/work/project",
    workspace_digest: str = "a" * 64,
    authority_profile: str = "scoped-edit",
) -> None:
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-6-fork-caller-1",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "pbx_mode": pbx_mode,
                "operator_role": "fork",
                "fork_purpose": "edit",
                "access_mode": "edit",
                "fork_codex_session_id": codex_session_id,
                "work_root": workspace,
                "cwd": workspace,
                "operator_workspace_set_digest": workspace_digest,
                "operator_authority_profile": authority_profile,
            },
        )
    )


def _register_mapping(
    store: Store,
    *,
    codex_session_id: str = "session-1",
    workspace: str = "/srv/work/project",
    pane_id: str = "%1",
    pane_pid: int = 123,
    process_start_ticks: int = 456,
) -> None:
    store.upsert_tmux_runtime_mapping(
        entity_id="operator-6-fork-caller-1",
        server_mode="dedicated",
        server_id="server-1",
        socket_path="/run/user/1000/agent-pbx/runtime.sock",
        session_name="agent-pbx-runtime-operator-6-fork-caller-1",
        window_id="@1",
        window_name="operator-6-fork-caller-1",
        pane_id=pane_id,
        pane_pid=pane_pid,
        process_start_ticks=process_start_ticks,
        codex_session_id=codex_session_id,
        cwd=workspace,
        state="ready",
    )


def _pane(
    *,
    workspace: str = "/srv/work/project",
    pane_id: str = "%1",
) -> TmuxPane:
    return TmuxPane(
        session_name="agent-pbx-runtime-operator-6-fork-caller-1",
        window_index="0",
        pane_index="0",
        pane_id=pane_id,
        active=True,
        current_command="codex",
        title="operator-6-fork-caller-1",
        cwd=workspace,
        width=120,
        height=40,
        history_size=100,
        window_name="operator-6-fork-caller-1",
        window_id="@1",
    )


def _request(
    *,
    request_id: str = "delivery-1",
    message: str = "Continue the exact task.",
    delivery: str = "tmux",
) -> IdleTargetDeliveryRequest:
    return IdleTargetDeliveryRequest(
        request_id=request_id,
        entity_id="operator-6-fork-caller-1",
        message=message,
        requested_delivery=delivery,
        source="operator_campaign_followup",
        payload={
            "campaign_id": "campaign-1",
            "assignment_id": "assignment-1",
            "operator_fork_id": "fork-1",
        },
    )


def _service(
    store: Store,
    *,
    state: str = "ready",
    session_id: str = "session-1",
) -> IdleTargetDeliveryService:
    return IdleTargetDeliveryService(
        store,
        runtime_snapshot=lambda _agent_id: {
            "session_id": session_id,
            "state": state,
        },
    )


def _patch_ready_terminal(
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture: str = "Recent output\n› ",
    sent: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        terminal_delivery.tmux_support,
        "list_panes",
        lambda *_args, **_kwargs: [_pane()],
    )
    monkeypatch.setattr(
        terminal_delivery.tmux_support,
        "pane_root_pid",
        lambda *_args, **_kwargs: 123,
    )
    monkeypatch.setattr(terminal_delivery, "process_start_ticks", lambda _pid: 456)
    monkeypatch.setattr(
        terminal_delivery.tmux_support,
        "capture_pane",
        lambda *_args, **_kwargs: capture,
    )

    def send_text(_target: str, message: str, **_kwargs: object) -> bool:
        if sent is not None:
            sent.append(message)
        return False

    monkeypatch.setattr(terminal_delivery.tmux_support, "send_text", send_text)


def test_idle_delivery_binds_exact_target_and_audits_without_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    sent: list[str] = []
    _patch_ready_terminal(monkeypatch, sent=sent)

    service = _service(store)
    result = service.deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.DELIVERED
    assert result.target_identity_sha256
    assert result.target_identity == {
        "entity_id": "operator-6-fork-caller-1",
        "codex_session_id": "session-1",
        "server_mode": "dedicated",
        "server_id": "server-1",
        "socket_path": "/run/user/1000/agent-pbx/runtime.sock",
        "session_name": "agent-pbx-runtime-operator-6-fork-caller-1",
        "window_id": "@1",
        "pane_id": "%1",
        "pane_pid": 123,
        "process_start_ticks": 456,
        "workspace_path": "/srv/work/project",
        "workspace_set_digest": "a" * 64,
        "authority_profile": "scoped-edit",
    }
    assert sent == ["Continue the exact task."]
    mapping = store.get_tmux_runtime_mapping("operator-6-fork-caller-1")
    assert mapping is not None
    assert mapping["writer_lease_active"] is False
    assert mapping["writer_client_id"] is None
    command = service.command_for_result(result)
    assert command["status"] == "sent"
    assert "message" not in command["payload"]
    assert command["payload"]["message_sha256"]
    events = store.list_subject_events(
        "delivery-1",
        event_type="idle_target_delivery",
    )
    assert len(events) == 1
    serialized = json.dumps(events)
    assert "Continue the exact task." not in serialized
    assert "Recent output" not in serialized


def test_idle_delivery_is_idempotent_across_service_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    sent: list[str] = []
    _patch_ready_terminal(monkeypatch, sent=sent)

    first = _service(store).deliver(_request())
    second = _service(store).deliver(_request())

    assert first.outcome == IdleTargetDeliveryOutcome.DELIVERED
    assert second.outcome == IdleTargetDeliveryOutcome.DELIVERED
    assert second.replayed is True
    assert second.command_id == first.command_id
    assert sent == ["Continue the exact task."]
    assert len(
        store.list_subject_events(
            "delivery-1",
            event_type="idle_target_delivery",
        )
    ) == 1


def test_idle_delivery_rejects_idempotency_key_reuse_with_changed_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(monkeypatch)
    service = _service(store)
    service.deliver(_request())

    with pytest.raises(ValueError, match="different request"):
        service.deliver(_request(message="A different prompt."))


def test_distinct_request_ids_deliver_identical_messages_twice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    sent: list[str] = []
    _patch_ready_terminal(monkeypatch, sent=sent)
    service = _service(store)

    first = service.deliver(_request(request_id="delivery-occurrence-1"))
    second = service.deliver(_request(request_id="delivery-occurrence-2"))

    assert first.command_id != second.command_id
    assert first.replayed is False
    assert second.replayed is False
    assert sent == ["Continue the exact task.", "Continue the exact task."]


def test_idle_delivery_queues_only_explicit_nohup_targets(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store, pbx_mode="nohup")
    service = _service(store)

    result = service.deliver(_request(delivery="auto"))

    assert result.outcome == IdleTargetDeliveryOutcome.QUEUED
    command = service.command_for_result(result)
    assert command["status"] == "queued"
    assert command["payload"]["message"] == "Continue the exact task."


def test_idle_delivery_never_queues_report_targets(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store, pbx_mode="report")
    service = _service(store)

    result = service.deliver(_request(delivery="queue"))

    assert result.outcome == IdleTargetDeliveryOutcome.BLOCKED
    assert result.reason_code == "queue_requires_explicit_nohup"
    command = service.command_for_result(result)
    assert "message" not in command["payload"]


@pytest.mark.asyncio
async def test_operator_api_surfaces_exact_typed_delivery_outcome() -> None:
    result = IdleTargetDeliveryResult(
        request_id="delivery-1",
        command_id="command-1",
        entity_id="operator-6-fork-caller-1",
        outcome=IdleTargetDeliveryOutcome.OVERLAY_ACTIVE,
        reason_code="update_overlay_active",
        reason="Codex terminal is not at an idle input prompt",
        target_identity_sha256=None,
        target_identity=None,
        replayed=False,
        writer_client_id=None,
    )

    def rejected() -> None:
        raise IdleTargetDeliveryError(result)

    with pytest.raises(HTTPException) as captured:
        await run_operator_call(rejected)

    assert captured.value.status_code == 409
    assert captured.value.detail == {
        "code": "IDLE_TARGET_DELIVERY_REJECTED",
        "message": "overlay_active: Codex terminal is not at an idle input prompt",
        "outcome": "overlay_active",
        "reason_code": "update_overlay_active",
        "request_id": "delivery-1",
        "command_id": "command-1",
        "retryable": True,
    }


@pytest.mark.parametrize(
    ("service_method", "tool_name", "arguments"),
    [
        (
            "start_campaign",
            "pbx_operator_start_campaign",
            {
                "operator_agent_id": "operator-6",
                "title": "Typed rejection",
                "objective": "Retain delivery details.",
                "assignments": [],
            },
        ),
        (
            "send_followup",
            "pbx_operator_send_followup",
            {
                "operator_agent_id": "operator-6",
                "campaign_id": "campaign-1",
                "target_agent_id": "caller-1",
                "message": "Continue.",
            },
        ),
        (
            "route_review_escalation",
            "pbx_operator_route_review_escalation",
            {
                "operator_agent_id": "operator-6",
                "review_fork_id": "fork-review-1",
                "message": "Apply the review correction.",
            },
        ),
        (
            "approve_handoff",
            "pbx_operator_approve_handoff",
            {
                "operator_agent_id": "operator-6",
                "handoff_id": "handoff-1",
            },
        ),
        (
            "update_handoff",
            "pbx_operator_update_handoff",
            {
                "operator_agent_id": "operator-6",
                "handoff_id": "handoff-1",
                "status": "complete",
                "summary": "Done.",
            },
        ),
        (
            "retry_handoff_completion_delivery",
            "pbx_operator_retry_handoff_completion",
            {
                "operator_agent_id": "operator-6",
                "completion_delivery_id": "completion-1",
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_operator_mcp_surfaces_exact_typed_delivery_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_method: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    typed = IdleTargetDeliveryResult(
        request_id="delivery-mcp-1",
        command_id="command-mcp-1",
        entity_id="operator-6-fork-caller-1",
        outcome=IdleTargetDeliveryOutcome.OVERLAY_ACTIVE,
        reason_code="model_overlay_active",
        reason="Codex terminal is not at an idle input prompt",
        target_identity_sha256=None,
        target_identity=None,
        replayed=False,
        writer_client_id=None,
    )

    def reject(_self: OperatorService, **_kwargs: object) -> dict[str, Any]:
        raise IdleTargetDeliveryError(typed)

    monkeypatch.setattr(OperatorService, service_method, reject)
    mcp = build_mcp_server(store)
    raw = await mcp.call_tool(tool_name, arguments)
    content, structured = raw if isinstance(raw, tuple) else (raw, None)
    payload = structured
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    if payload is None:
        payload = json.loads(content[0].text)
    assert payload["ok"] is False
    assert payload["error"]["outcome"] == "overlay_active"
    assert payload["error"]["reason_code"] == "model_overlay_active"
    assert payload["error"]["request_id"] == "delivery-mcp-1"
    assert payload["error"]["command_id"] == "command-mcp-1"


@pytest.mark.parametrize(
    ("capture", "reason_code"),
    [
        ("Starting Codex…", "startup_overlay_active"),
        ("Select a model", "model_overlay_active"),
        ("Update available", "update_overlay_active"),
    ],
)
def test_idle_delivery_classifies_bounded_codex_overlays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture: str,
    reason_code: str,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(monkeypatch, capture=capture)

    result = _service(store).deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.OVERLAY_ACTIVE
    assert result.reason_code == reason_code


def test_idle_delivery_does_not_suppress_security_prompts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(monkeypatch, capture="Approval required\n› ")

    result = _service(store).deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.BLOCKED
    assert result.reason_code == "security_prompt_active"


def test_idle_delivery_requires_ready_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(monkeypatch)

    result = _service(store, state="thinking").deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.TARGET_NOT_READY
    assert result.reason_code == "runtime_state_not_ready"


def test_idle_delivery_fails_closed_on_session_process_and_workspace_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store, codex_session_id="session-other")
    _patch_ready_terminal(monkeypatch)

    session = _service(store).deliver(_request(request_id="session-drift"))
    assert session.outcome == IdleTargetDeliveryOutcome.STALE_MAPPING
    assert session.reason_code == "codex_session_mismatch"

    _register_mapping(store, workspace="/srv/work/other")
    workspace = _service(store).deliver(_request(request_id="workspace-drift"))
    assert workspace.outcome == IdleTargetDeliveryOutcome.STALE_MAPPING
    assert workspace.reason_code == "workspace_mismatch"

    _register_mapping(store)
    monkeypatch.setattr(terminal_delivery, "process_start_ticks", lambda _pid: 999)
    process = _service(store).deliver(_request(request_id="process-drift"))
    assert process.outcome == IdleTargetDeliveryOutcome.STALE_MAPPING
    assert process.reason_code == "process_start_changed"


def test_idle_delivery_fails_closed_when_capture_is_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(monkeypatch)

    def fail_capture(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("capture unavailable")

    monkeypatch.setattr(terminal_delivery.tmux_support, "capture_pane", fail_capture)
    result = _service(store).deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.AMBIGUOUS
    assert result.reason_code == "terminal_readiness_unavailable"


def test_idle_delivery_blocks_foreign_writer_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    store.acquire_tmux_writer_lease(
        "operator-6-fork-caller-1",
        client_id="agent-pbx-tui:embedded-client-1",
        ttl_seconds=30,
    )
    _patch_ready_terminal(monkeypatch)

    result = _service(store).deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.BLOCKED
    assert result.reason_code == "writer_lease_unavailable"
    mapping = store.get_tmux_runtime_mapping("operator-6-fork-caller-1")
    assert mapping is not None
    assert mapping["writer_lease_active"] is True
    assert mapping["writer_client_id"] == "agent-pbx-tui:embedded-client-1"


def test_idle_delivery_rejects_metadata_change_after_writer_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    sent: list[str] = []
    _patch_ready_terminal(monkeypatch, sent=sent)
    original_acquire = store.acquire_tmux_writer_lease

    def acquire_and_change(
        entity_id: str,
        *,
        client_id: str,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        leased = original_acquire(
            entity_id,
            client_id=client_id,
            ttl_seconds=ttl_seconds,
        )
        _register_target(store, authority_profile="different-authority")
        return leased

    monkeypatch.setattr(store, "acquire_tmux_writer_lease", acquire_and_change)
    result = _service(store).deliver(_request(request_id="metadata-drift"))

    assert result.outcome == IdleTargetDeliveryOutcome.STALE_MAPPING
    assert result.reason_code == "mapping_changed_after_writer_lease"
    assert sent == []


def test_idle_delivery_rejects_changed_mapping_after_writer_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(monkeypatch)
    original_acquire = store.acquire_tmux_writer_lease

    def acquire_and_change(
        entity_id: str,
        *,
        client_id: str,
        ttl_seconds: float,
    ) -> dict[str, Any]:
        leased = original_acquire(
            entity_id,
            client_id=client_id,
            ttl_seconds=ttl_seconds,
        )
        _register_mapping(store, pane_id="%2")
        return leased

    monkeypatch.setattr(store, "acquire_tmux_writer_lease", acquire_and_change)
    result = _service(store).deliver(_request())

    assert result.outcome == IdleTargetDeliveryOutcome.STALE_MAPPING
    assert result.reason_code == "mapping_changed_after_writer_lease"


def test_terminal_capture_classifier_requires_current_prompt() -> None:
    assert classify_codex_terminal_capture("response\n› ").ready is True
    assert (
        classify_codex_terminal_capture("response still streaming").outcome
        == IdleTargetDeliveryOutcome.TARGET_NOT_READY
    )


@pytest.mark.parametrize(
    "capture",
    [
        "Earlier transcript\n› old submitted prompt\nassistant still streaming",
        "Earlier transcript\n› typed draft",
        "› Ask Codex to do anything about this typed draft",
    ],
)
def test_terminal_capture_classifier_rejects_menu_transcript_and_draft(
    capture: str,
) -> None:
    readiness = classify_codex_terminal_capture(capture)
    assert readiness.ready is False
    assert readiness.outcome == IdleTargetDeliveryOutcome.TARGET_NOT_READY


def test_terminal_capture_classifier_marks_extra_thought_menu_as_overlay() -> None:
    readiness = classify_codex_terminal_capture(
        "response taking extra thought\n"
        "\x1b[1m› Retry with a faster model\x1b[0m\n  Keep waiting"
    )
    assert readiness.ready is False
    assert readiness.outcome == IdleTargetDeliveryOutcome.OVERLAY_ACTIVE
    assert readiness.reason_code == "thinking_overlay_active"


def test_terminal_capture_classifier_accepts_bottom_composer_band_and_ansi() -> None:
    capture = (
        "completed response\n"
        "\x1b[1m› Ask Codex to do anything\x1b[0m\n\n"
        "  GPT-5.6 · /srv/work/project · scoped edit\n"
        "  ← for agents · f2 to view"
    )
    assert classify_codex_terminal_capture(capture).ready is True
    assert classify_codex_terminal_capture("response\n\x1b[2m› \x1b[0m").ready is True


def test_runtime_thinking_rejects_bottom_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    _patch_ready_terminal(
        monkeypatch,
        capture="› Ask Codex to do anything\n  GPT-5.6 · cwd · scope\n  ← for agents",
    )
    result = _service(store, state="thinking").deliver(
        _request(request_id="thinking-placeholder")
    )
    assert result.outcome == IdleTargetDeliveryOutcome.TARGET_NOT_READY
    assert result.reason_code == "runtime_state_not_ready"


def test_crash_left_preparing_replays_ambiguous_without_reinjection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    _register_target(store)
    _register_mapping(store)
    sent: list[str] = []
    _patch_ready_terminal(monkeypatch, sent=sent)
    service = _service(store)
    request = _request(request_id="crash-left-preparing")
    message_sha256 = hashlib.sha256(request.message.encode("utf-8")).hexdigest()
    request_sha256 = service._request_sha256(
        request=request,
        resolved_delivery="tmux",
        message_sha256=message_sha256,
    )
    audit_payload = {
        **dict(request.payload),
        "source": request.source,
        "requested_delivery": request.requested_delivery,
        "resolved_delivery": "tmux",
        "delivery_mode": "tmux",
        "message_sha256": message_sha256,
        "message_bytes": len(request.message.encode("utf-8")),
    }
    command, created = service.repository.create_idempotent_command(
        CommandCreateRequest(
            agent_id=request.entity_id,
            type="send_input",
            payload=audit_payload,
        ),
        delivery_request_id=request.request_id,
        delivery_request_sha256=request_sha256,
        status="preparing",
    )
    assert created is True

    replay = service.deliver(request)

    assert replay.outcome == IdleTargetDeliveryOutcome.AMBIGUOUS
    assert replay.reason_code == "delivery_replay_ambiguous"
    assert replay.replayed is True
    assert sent == []
    persisted = store.get_command(str(command["command_id"]))
    assert persisted is not None
    assert persisted["status"] == "ambiguous"
    assert persisted["result"]["reason_code"] == "delivery_replay_ambiguous"
    events = store.list_subject_events(
        request.request_id,
        event_type="idle_target_delivery_replay_ambiguous",
    )
    assert len(events) == 1
    assert events[0]["payload"]["repair_required"] is True
    assert events[0]["payload"]["repair_action"] == (
        "inspect_exact_target_before_manual_resolution"
    )
