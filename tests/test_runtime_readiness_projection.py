from __future__ import annotations

import json
from pathlib import Path
import uuid

import pytest

from agent_pbx import terminal_delivery
from agent_pbx.schemas import AgentRegisterRequest, ReportCreateRequest
from agent_pbx.store import Store
from agent_pbx.terminal_delivery import (
    IdleTargetDeliveryOutcome,
    IdleTargetDeliveryRequest,
    IdleTargetDeliveryService,
)
from agent_pbx.tmux import TmuxPane


FIXTURE_PATH = Path(__file__).parent / "fixtures/runtime_readiness_projection_rc2.json"


def _fixture() -> dict[str, object]:
    value = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert value["schema"] == "agent-pbx.runtime-readiness-projection-fixture/v1"
    assert value["planning_report_id"] == "ac03d9bb-2c38-46ce-881e-46c66237d229"
    assert value["ledger_note_id"] == "3a03a4f648534a388f9c5ebca03c85c2"
    return value


def _register_exact_target(store: Store, workspace: Path) -> None:
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-fixture-fork",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "pbx_mode": "report",
                "operator_role": "fork",
                "fork_purpose": "edit",
                "access_mode": "edit",
                "fork_codex_session_id": "fixture-session",
                "work_root": str(workspace),
                "cwd": str(workspace),
                "operator_workspace_set_digest": "a" * 64,
                "operator_authority_profile": "trusted-workbench",
            },
        )
    )
    store.upsert_tmux_runtime_mapping(
        entity_id="operator-fixture-fork",
        server_mode="dedicated",
        server_id="fixture-server",
        socket_path="/tmp/fixture-runtime.sock",
        session_name="agent-pbx-runtime-operator-fixture-fork",
        window_id="@88",
        window_name="operator-fixture-fork",
        pane_id="%88",
        pane_pid=4242,
        process_start_ticks=99,
        codex_session_id="fixture-session",
        cwd=str(workspace),
        state="ready",
        metadata={"runtime_generation": "fixture-generation-1"},
    )


def _patch_exact_idle_terminal(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
    sent: list[str],
) -> None:
    pane = TmuxPane(
        session_name="agent-pbx-runtime-operator-fixture-fork",
        window_index="0",
        pane_index="0",
        pane_id="%88",
        active=True,
        current_command="codex",
        title="operator-fixture-fork",
        cwd=str(workspace),
        width=120,
        height=40,
        history_size=100,
        window_name="operator-fixture-fork",
        window_id="@88",
    )
    monkeypatch.setattr(
        terminal_delivery.tmux_support,
        "list_panes",
        lambda *_args, **_kwargs: [pane],
    )
    monkeypatch.setattr(
        terminal_delivery.tmux_support,
        "pane_root_pid",
        lambda *_args, **_kwargs: 4242,
    )
    monkeypatch.setattr(terminal_delivery, "process_start_ticks", lambda _pid: 99)
    monkeypatch.setattr(
        terminal_delivery.tmux_support,
        "capture_pane",
        lambda *_args, **_kwargs: "completed response\n› ",
    )

    def send_text(_target: str, message: str, **_kwargs: object) -> bool:
        sent.append(message)
        return True

    monkeypatch.setattr(terminal_delivery.tmux_support, "send_text", send_text)


def _delivery(request_id: str) -> IdleTargetDeliveryRequest:
    return IdleTargetDeliveryRequest(
        request_id=request_id,
        entity_id="operator-fixture-fork",
        message="Continue the isolated runtime-readiness fixture.",
        requested_delivery="tmux",
        source="operator_campaign_followup",
        payload={"fixture": "runtime-readiness-rc2"},
    )


@pytest.mark.parametrize("case_name", ["operator_0", "operator_6"])
def test_isolated_terminal_history_and_runtime_readiness_harness(
    case_name: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture()
    case = fixture["cases"][case_name]
    for identifier in case.values():
        uuid.UUID(str(identifier))

    database = tmp_path / "pbx.sqlite"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = Store(database)
    store.init()
    _register_exact_target(store, workspace)
    sent: list[str] = []
    _patch_exact_idle_terminal(monkeypatch, workspace, sent)

    terminal = store.create_report(
        "operator-fixture-fork",
        ReportCreateRequest(
            project="agent-pbx-operator",
            status="done",
            summary="Historical campaign terminal report",
            detail="Terminal history must remain immutable.",
            metadata={"fixture_report_id": case["terminal_report_id"]},
        ),
    )
    readiness = store.create_report(
        "operator-fixture-fork",
        ReportCreateRequest(
            project="agent-pbx-operator",
            status="needs_input",
            summary="Current runtime is input-ready",
            detail="This is projection metadata, not runtime authorization.",
            metadata={"fixture_report_id": case["readiness_report_id"]},
        ),
    )
    request = _delivery(f"{case_name}-successor-delivery")
    first = IdleTargetDeliveryService(store).deliver(request)

    restarted_store = Store(database)
    restarted_store.init()
    replay = IdleTargetDeliveryService(restarted_store).deliver(request)
    late = restarted_store.create_report(
        "operator-fixture-fork",
        ReportCreateRequest(
            project="agent-pbx-operator",
            status="blocked",
            summary="Late historical report",
            detail="The exact live target remains independently input-ready.",
            metadata={"fixture_report_id": case["rejected_delivery_id"]},
        ),
    )
    after_late_report = IdleTargetDeliveryService(restarted_store).deliver(
        _delivery(f"{case_name}-late-report-delivery")
    )
    interrupted = IdleTargetDeliveryService(
        restarted_store,
        runtime_snapshot=lambda _agent_id: {
            "session_id": "fixture-session",
            "state": "interrupted",
            "updated_at": 200.0,
            "evidence": {"source": "app_server", "observed_at": 200.0},
        },
    ).deliver(_delivery(f"{case_name}-interrupted-delivery"))

    assert terminal["status"] == "done"
    assert readiness["status"] == "needs_input"
    assert late["status"] == "blocked"
    assert first.outcome == IdleTargetDeliveryOutcome.DELIVERED
    assert first.readiness_provenance is not None
    assert first.readiness_provenance["runtime"]["projection_only"] is True
    assert first.readiness_provenance["terminal"]["ready"] is True
    assert replay.outcome == IdleTargetDeliveryOutcome.DELIVERED
    assert replay.replayed is True
    assert replay.command_id == first.command_id
    assert after_late_report.outcome == IdleTargetDeliveryOutcome.DELIVERED
    assert interrupted.outcome == IdleTargetDeliveryOutcome.TARGET_NOT_READY
    assert interrupted.reason_code == "runtime_state_not_ready"
    assert first.target_identity_sha256 == after_late_report.target_identity_sha256
    assert sent == [
        "Continue the isolated runtime-readiness fixture.",
        "Continue the isolated runtime-readiness fixture.",
    ]
    serialized = json.dumps(
        restarted_store.list_subject_events(
            request.request_id,
            event_type="idle_target_delivery",
        )
    )
    assert "Continue the isolated runtime-readiness fixture." not in serialized
