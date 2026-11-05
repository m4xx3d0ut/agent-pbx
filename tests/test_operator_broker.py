from __future__ import annotations

import pytest

from agent_pbx.operator import OperatorService
from agent_pbx.operator_broker import (
    OperatorBrokerPolicyError,
    OperatorBrokerService,
    normalize_broker_commit_subject,
    normalize_broker_expected_parent,
    normalize_broker_paths,
    operator_broker_receipt_summary,
    operator_broker_posture,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import SCHEMA_VERSION, Store


def test_operator_broker_posture_defaults_to_guided_safe_path() -> None:
    posture = operator_broker_posture({})

    assert posture.mode == "guided"
    assert posture.control_mode == "editable"
    assert posture.request_ttl_seconds == 900
    assert posture.max_in_flight_per_root == 1
    assert posture.root_operator_enabled is False


def test_operator_broker_posture_accepts_bounded_root_operator_mode() -> None:
    posture = operator_broker_posture(
        {
            "AGENT_PBX_OPERATOR_BROKER_MODE": "root_operator",
            "AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE": "hidden",
            "AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS": "1200",
            "AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT": "2",
        }
    )

    assert posture.root_operator_enabled is True
    assert posture.controls_hidden is True
    assert posture.request_ttl_seconds == 1200
    assert posture.max_in_flight_per_root == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AGENT_PBX_OPERATOR_BROKER_MODE", "automatic"),
        ("AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE", "open"),
        ("AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS", "59"),
        ("AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT", "9"),
    ],
)
def test_operator_broker_posture_fails_closed_on_invalid_values(
    key: str,
    value: str,
) -> None:
    with pytest.raises(OperatorBrokerPolicyError):
        operator_broker_posture({key: value})


def test_operator_broker_normalizes_exact_immutable_scope() -> None:
    assert normalize_broker_commit_subject(" fix: exact broker receipt ") == (
        "fix: exact broker receipt"
    )
    assert normalize_broker_expected_parent("A" * 40) == "a" * 40
    assert normalize_broker_paths(
        ["src/app.py", "tests/test_app.py", "src/app.py"]
    ) == ("src/app.py", "tests/test_app.py")


@pytest.mark.parametrize(
    "paths",
    [[], ["../secret"], ["/etc/passwd"], [".git/config"], ["bad\npath"]],
)
def test_operator_broker_rejects_empty_escaping_or_protected_paths(
    paths: list[str],
) -> None:
    with pytest.raises(OperatorBrokerPolicyError):
        normalize_broker_paths(paths)


def test_operator_broker_rejects_multiline_subject() -> None:
    with pytest.raises(OperatorBrokerPolicyError):
        normalize_broker_commit_subject("fix: one\nbody")


def _broker_store(tmp_path) -> tuple[Store, str, str]:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id, role in (("operator-0", "root"), ("operator-7-fork", "fork")):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={
                    "cwd": str(tmp_path),
                    "operator_role": role,
                    "logical_operator_id": "operator-0",
                },
            )
        )
    handoff = OperatorService(store).create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-0",
        message="Commit the reviewed exact scope.",
    )
    return store, str(handoff["handoff_id"]), "a" * 64


def _qualified_broker_service(tmp_path):
    digest = "a" * 64
    store = Store(tmp_path / "qualified.sqlite")
    store.init()
    root_metadata = {
        "cwd": str(tmp_path),
        "operator_role": "root",
        "logical_operator_id": "operator-0",
        "operator_authority_activated_profile": "trusted-workbench",
        "operator_authority_activated_workspace_set_digest": digest,
    }
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata=root_metadata,
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-7",
            project="demo",
            metadata={
                "cwd": str(tmp_path),
                "codex_session_id": "session-caller-7",
            },
        )
    )
    operator = OperatorService(store)
    fork = operator.ensure_fork(
        operator_agent_id="operator-0",
        source_caller_agent_id="caller-7",
        fork_agent_id="operator-7-fork",
        fork_track_id="edit",
        fork_purpose="edit",
        access_mode="edit",
        source_cwd=str(tmp_path),
        work_root=str(tmp_path),
        fork_codex_session_id="session-fork-7",
        status="running",
        metadata={
            "operator_authority_activated_profile": "trusted-workbench",
            "operator_authority_activated_workspace_set_digest": digest,
        },
    )
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-0",
        title="Exact broker test",
        objective="Create one reviewed commit",
        criteria=["exact paths only"],
        assignments=[
            {
                "target_agent_id": "caller-7",
                "title": "Prepare change",
                "prompt": "Prepare the exact change.",
                "criteria": [],
            }
        ],
    )
    handoff = operator.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-0",
        target_operator_fork_id=str(fork["operator_fork_id"]),
        message="Create the reviewed commit.",
        execution_scope={
            "action_class": "exact_git_commit_broker",
            "campaign_id": campaign["campaign_id"],
            "workspace_set_digest": digest,
            "mutation_mode": "exact_paths",
            "allowed_roots": [str(tmp_path)],
            "exact_paths": ["src/app.py", "tests/test_app.py"],
            "expected_git_parent": "b" * 40,
            "commit_subject": "fix: exact root checkpoint",
        },
    )
    operator.approve_handoff(
        operator_agent_id="operator-0",
        handoff_id=handoff["handoff_id"],
        delivery="record_only",
    )
    operator.ack_handoff(
        operator_agent_id="operator-0",
        handoff_id=handoff["handoff_id"],
        status="running",
        summary="Ready for exact checkpoint",
    )
    service = OperatorBrokerService(
        store,
        environ={"AGENT_PBX_OPERATOR_BROKER_MODE": "root_operator"},
    )
    return store, service, handoff, digest


def _create_request(store: Store, handoff_id: str, digest: str):
    return store.create_operator_broker_request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff_id,
        workspace_set_digest=digest,
        scope_sha256="b" * 64,
        expected_git_parent="c" * 40,
        commit_subject="fix: durable broker request",
        exact_paths=["src/app.py"],
        ttl_seconds=900,
        max_in_flight_per_root=1,
    )


def test_store_installs_durable_operator_broker_request_schema(tmp_path) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)

    request, created = _create_request(store, handoff_id, digest)

    assert store.schema_version() == SCHEMA_VERSION
    assert created is True
    assert request["status"] == "pending"
    assert request["exact_paths"] == ["src/app.py"]


def test_store_reuses_identical_open_request_and_limits_root_concurrency(
    tmp_path,
) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)
    first, created = _create_request(store, handoff_id, digest)
    reused, reused_created = _create_request(store, handoff_id, digest)

    assert created is True
    assert reused_created is False
    assert reused["request_id"] == first["request_id"]

    second_handoff = OperatorService(store).create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-0",
        message="Another exact request.",
    )
    with pytest.raises(ValueError, match="maximum"):
        _create_request(store, str(second_handoff["handoff_id"]), digest)


def test_store_claims_with_compare_and_swap_and_rejects_other_executor(
    tmp_path,
) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)
    request, _created = _create_request(store, handoff_id, digest)

    claimed = store.claim_operator_broker_request(
        request_id=request["request_id"],
        executor_id="tui-a",
    )

    assert claimed["status"] == "claimed"
    assert claimed["executor_id"] == "tui-a"
    with pytest.raises(ValueError, match="not claimable"):
        store.claim_operator_broker_request(
            request_id=request["request_id"],
            executor_id="tui-b",
        )
    with pytest.raises(PermissionError, match="another executor"):
        store.transition_operator_broker_request(
            request_id=request["request_id"],
            executor_id="tui-b",
            status="executing",
            expected_statuses=("claimed",),
        )


def test_store_persists_terminal_result_and_prevents_cancel_after_claim(
    tmp_path,
) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)
    request, _created = _create_request(store, handoff_id, digest)
    store.claim_operator_broker_request(
        request_id=request["request_id"],
        executor_id="tui-a",
    )

    with pytest.raises(ValueError, match="allowed to finish"):
        store.cancel_operator_broker_request(
            request_id=request["request_id"],
            root_operator_agent_id="operator-0",
            reason="too late",
        )

    executing = store.transition_operator_broker_request(
        request_id=request["request_id"],
        executor_id="tui-a",
        status="executing",
        expected_statuses=("claimed",),
        action_transaction_id=None,
        result={"phase": "stop"},
    )
    completed = store.transition_operator_broker_request(
        request_id=request["request_id"],
        executor_id="tui-a",
        status="succeeded",
        expected_statuses=("executing",),
        result={"new_head": "d" * 40, "validation_pending": True},
    )

    assert executing["status"] == "executing"
    assert completed["status"] == "succeeded"
    assert completed["result"]["validation_pending"] is True
    assert completed["completed_at"] is not None
    events = store.list_events(limit=20)
    event_types = {event["type"] for event in events}
    assert "operator_broker_request_claimed" in event_types
    assert "operator_broker_request_transitioned" in event_types


def test_operator_broker_receipt_summary_is_root_readable() -> None:
    summary = operator_broker_receipt_summary(
        {
            "status": "succeeded",
            "target_fork_agent_id": "operator-7-fork",
            "commit_subject": "fix: exact checkpoint",
            "exact_paths": ["src/app.py"],
            "result": {
                "old_head": "a" * 40,
                "new_head": "b" * 40,
                "committed_paths": ["src/app.py"],
                "resumed_codex_session_id": "session-7",
                "resumed_tmux_pane_id": "%77",
                "validation_pending": True,
            },
        }
    )

    assert "succeeded" in summary
    assert "fix: exact checkpoint" in summary
    assert "session-7" in summary
    assert "%77" in summary
    assert "validation remains pending" in summary


def test_store_recovers_expired_executing_claim_for_a_new_local_tui(
    tmp_path,
) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)
    request, _created = _create_request(store, handoff_id, digest)
    store.claim_operator_broker_request(
        request_id=request["request_id"],
        executor_id="old-tui",
        lease_seconds=10,
    )
    store.transition_operator_broker_request(
        request_id=request["request_id"],
        executor_id="old-tui",
        status="executing",
        expected_statuses=("claimed",),
        result={"phase": "stop"},
        lease_seconds=10,
    )
    with store.connect() as conn:
        conn.execute(
            "UPDATE operator_broker_requests SET claim_expires_at = 1 WHERE request_id = ?",
            (request["request_id"],),
        )

    recovered = store.claim_operator_broker_request(
        request_id=request["request_id"],
        executor_id="new-tui",
    )

    assert recovered["status"] == "claimed"
    assert recovered["executor_id"] == "new-tui"


def test_broker_service_preflight_binds_root_fork_campaign_and_scope(tmp_path) -> None:
    _store, service, handoff, digest = _qualified_broker_service(tmp_path)

    result = service.preflight(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )

    assert result["ok"] is True
    assert result["workspace_set_digest"] == digest
    assert result["exact_paths"] == ["src/app.py", "tests/test_app.py"]
    assert result["action"]["state"] == "ready_stop"
    assert len(result["authority_sha256"]) == 64


def test_broker_service_root_mode_creates_durable_request(tmp_path) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)

    result = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )

    assert result["created"] is True
    assert result["request"]["status"] == "pending"
    assert "receipt_summary" in result["request"]
    events = store.list_events(limit=20)
    assert any(event["type"] == "operator_broker_request_created" for event in events)


def test_broker_service_guided_mode_refuses_root_automation(tmp_path) -> None:
    store, _service, handoff, _digest = _qualified_broker_service(tmp_path)
    guided = OperatorBrokerService(store, environ={})

    with pytest.raises(OperatorBrokerPolicyError, match="guided TUI"):
        guided.request(
            root_operator_agent_id="operator-0",
            target_fork_agent_id="operator-7-fork",
            handoff_id=handoff["handoff_id"],
        )


def test_broker_service_rejects_edit_fork_as_requesting_root(tmp_path) -> None:
    _store, service, handoff, _digest = _qualified_broker_service(tmp_path)

    with pytest.raises(OperatorBrokerPolicyError, match="Only a root"):
        service.preflight(
            root_operator_agent_id="operator-7-fork",
            target_fork_agent_id="operator-7-fork",
            handoff_id=handoff["handoff_id"],
        )
