from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.operator import OperatorService
from agent_pbx.operator_broker import (
    OperatorBrokerPolicyError,
    OperatorBrokerService,
    normalize_broker_commit_subject,
    normalize_broker_expected_parent,
    normalize_broker_expected_parents,
    normalize_broker_paths,
    operator_broker_receipt_summary,
    operator_broker_posture,
)
from agent_pbx.schemas import (
    AgentRegisterRequest,
    OperatorBrokerCheckpointResponse,
    ReportCreateRequest,
)
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
    assert normalize_broker_expected_parents(
        ["A" * 40, "B" * 40],
        expected_parent="a" * 40,
    ) == ("a" * 40, "b" * 40)
    assert normalize_broker_paths(
        ["src/app.py", "tests/test_app.py", "src/app.py"]
    ) == ("src/app.py", "tests/test_app.py")


def test_operator_broker_response_preserves_ordered_parents_and_tree() -> None:
    response = OperatorBrokerCheckpointResponse(
        request_id="request-1",
        root_operator_agent_id="operator-8",
        target_fork_agent_id="operator-8-fork",
        handoff_id="handoff-1",
        workspace_set_digest="a" * 64,
        scope_sha256="b" * 64,
        expected_git_parent="c" * 40,
        expected_git_parents=["c" * 40, "d" * 40],
        expected_git_tree="e" * 40,
        commit_subject="Merge exact candidate lineage",
        exact_paths=["src/app.py"],
        status="pending",
        created_at=1.0,
        updated_at=1.0,
        expires_at=901.0,
    ).model_dump()

    assert response["expected_git_parents"] == ["c" * 40, "d" * 40]
    assert response["expected_git_tree"] == "e" * 40


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


def test_operator_broker_rejects_invalid_ordered_parent_sets() -> None:
    with pytest.raises(OperatorBrokerPolicyError, match="first ordered"):
        normalize_broker_expected_parents(
            ["b" * 40, "a" * 40],
            expected_parent="a" * 40,
        )
    with pytest.raises(OperatorBrokerPolicyError, match="duplicate"):
        normalize_broker_expected_parents(
            ["a" * 40, "a" * 40],
            expected_parent="a" * 40,
        )


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


def _qualified_broker_service(tmp_path, *, broker_qualified: bool = True):
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
            **(
                {"linked_git_admin": _broker_qualified_linked_metadata(tmp_path)}
                if broker_qualified
                else {}
            ),
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
            "expected_git_parents": ["b" * 40, "c" * 40],
            "expected_git_tree": "d" * 40,
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


def _broker_qualified_linked_metadata(tmp_path) -> dict[str, object]:
    git_dir = tmp_path / ".git" / "worktrees" / "operator-7-fork"
    common_dir = tmp_path / ".git"
    qualified = [
        str(git_dir / "index"),
        str(common_dir / "objects"),
        str(common_dir / "refs" / "heads" / "pbx-operator-7"),
        str(common_dir / "logs" / "refs" / "heads" / "pbx-operator-7"),
    ]
    return {
        "kind": "linked_worktree_edit",
        "worktree_root": str(tmp_path),
        "git_dir": str(git_dir),
        "common_dir": str(common_dir),
        "object_dir": str(common_dir / "objects"),
        "branch_ref": "refs/heads/pbx-operator-7/test",
        "base_permission_profile": ":workspace",
        "permission_profile": "agent-pbx-linked-edit",
        "write_strategy": "tui_root_commit_broker",
        "grant_scope": "tui_root_commit_broker",
        "additional_workspace_roots": [],
        "active_write_grants": [],
        "qualified_git_admin_paths": qualified,
        "writable_paths": [],
    }


def _create_request(store: Store, handoff_id: str, digest: str):
    return store.create_operator_broker_request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff_id,
        campaign_id="campaign-1",
        assignment_id="assignment-1",
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
    assert request["expected_git_parents"] == ["c" * 40]


def test_store_round_trips_ordered_broker_parents_and_tree(tmp_path) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)

    request, created = store.create_operator_broker_request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff_id,
        campaign_id="campaign-1",
        assignment_id="assignment-1",
        workspace_set_digest=digest,
        scope_sha256="b" * 64,
        expected_git_parent="c" * 40,
        expected_git_parents=("c" * 40, "d" * 40),
        expected_git_tree="e" * 40,
        commit_subject="Merge exact candidate lineage",
        exact_paths=["src/app.py"],
        ttl_seconds=900,
        max_in_flight_per_root=1,
    )

    assert created is True
    assert request["expected_git_parents"] == ["c" * 40, "d" * 40]
    assert request["expected_git_tree"] == "e" * 40


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


def test_failed_broker_action_receipt_is_terminal_and_records_reason(tmp_path) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    preflight = service.preflight(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )
    transaction = store.append_operator_action_receipt(
        handoff_id=handoff["handoff_id"],
        action_class="exact_git_commit_broker",
        target_agent_id="operator-7-fork",
        scope_sha256=preflight["scope_sha256"],
        initial_state="ready_stop",
        step="stop",
        outcome="succeeded",
        actor="operator-0",
    )

    transaction = store.append_operator_action_receipt(
        handoff_id=handoff["handoff_id"],
        action_class="exact_git_commit_broker",
        target_agent_id="operator-7-fork",
        scope_sha256=preflight["scope_sha256"],
        initial_state=transaction["state"],
        step="broker_review",
        outcome="failed",
        actor="agent-pbx-tui",
        evidence={"reason": "linked worktree qualification failed"},
    )

    assert transaction["state"] == "blocked_reapproval_required"
    assert transaction["reason"] == "linked worktree qualification failed"
    assert transaction["completed_at"] is not None


def test_broker_service_rejects_standalone_fork_before_request(tmp_path) -> None:
    _store, service, handoff, _digest = _qualified_broker_service(
        tmp_path,
        broker_qualified=False,
    )

    with pytest.raises(OperatorBrokerPolicyError) as exc_info:
        service.preflight(
            root_operator_agent_id="operator-0",
            target_fork_agent_id="operator-7-fork",
            handoff_id=handoff["handoff_id"],
        )

    assert exc_info.value.code == "OPERATOR_BROKER_FORK_TOPOLOGY_REQUIRED"
    assert "standalone repository" in exc_info.value.message


def test_broker_service_accepts_tui_approved_cross_root_handoff(tmp_path) -> None:
    digest = "a" * 64
    store = Store(tmp_path / "cross-root.sqlite")
    store.init()
    for agent_id in ("operator-0", "operator-8"):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={
                    "cwd": str(tmp_path),
                    "operator_role": "root",
                    "logical_operator_id": agent_id,
                    "operator_authority_activated_profile": "trusted-workbench",
                    "operator_authority_activated_workspace_set_digest": digest,
                },
            )
        )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-8",
            project="demo",
            metadata={
                "cwd": str(tmp_path),
                "codex_session_id": "session-caller-8",
            },
        )
    )
    operator = OperatorService(store)
    fork = operator.ensure_fork(
        operator_agent_id="operator-8",
        source_caller_agent_id="caller-8",
        fork_agent_id="operator-8-fork",
        fork_track_id="edit",
        fork_purpose="edit",
        access_mode="edit",
        source_cwd=str(tmp_path),
        work_root=str(tmp_path),
        fork_codex_session_id="session-fork-8",
        status="running",
        metadata={
            "operator_authority_activated_profile": "trusted-workbench",
            "operator_authority_activated_workspace_set_digest": digest,
            "linked_git_admin": _broker_qualified_linked_metadata(tmp_path),
        },
    )
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-8",
        title="Delegated exact checkpoint",
        objective="Commit the target root's reviewed checkpoint",
        criteria=["exact paths only"],
        assignments=[
            {
                "target_agent_id": "caller-8",
                "title": "Prepare exact change",
                "prompt": "Prepare the reviewed change.",
                "criteria": [],
            }
        ],
    )
    handoff = operator.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-8",
        target_operator_fork_id=str(fork["operator_fork_id"]),
        message="Execute the reviewed delegated checkpoint.",
        execution_scope={
            "action_class": "exact_git_commit_broker",
            "campaign_id": campaign["campaign_id"],
            "workspace_set_digest": digest,
            "mutation_mode": "exact_paths",
            "allowed_roots": [str(tmp_path)],
            "exact_paths": ["src/app.py"],
            "expected_git_parent": "b" * 40,
            "commit_subject": "fix: delegated exact checkpoint",
        },
    )
    handoff = store.update_operator_handoff(
        str(handoff["handoff_id"]),
        status="acknowledged",
        metadata={
            "approved_by": "agent-pbx-tui",
            "approved_by_operator_agent_id": "operator-0",
            "acknowledged_by_operator_agent_id": "operator-8",
        },
    )
    assert handoff is not None
    service = OperatorBrokerService(
        store,
        environ={"AGENT_PBX_OPERATOR_BROKER_MODE": "root_operator"},
    )

    result = service.preflight(
        root_operator_agent_id="operator-8",
        target_fork_agent_id="operator-8-fork",
        handoff_id=str(handoff["handoff_id"]),
    )

    assert result["ok"] is True
    assert result["handoff_root_operator_agent_id"] == "operator-0"
    assert result["handoff_authority_mode"] == "delegated_cross_root"
    assert result["campaign_id"] == campaign["campaign_id"]
    assert result["exact_paths"] == ["src/app.py"]


def test_broker_service_rejects_unacknowledged_cross_root_handoff(tmp_path) -> None:
    _store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    service.store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-other",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "cwd": str(tmp_path),
                "operator_role": "root",
                "logical_operator_id": "operator-other",
            },
        )
    )

    with service.store.connect() as conn:
        conn.execute(
            "UPDATE operator_handoffs SET logical_operator_agent_id = ? WHERE handoff_id = ?",
            ("operator-other", str(handoff["handoff_id"])),
        )

    with pytest.raises(
        OperatorBrokerPolicyError,
        match="requires source-root approval",
    ):
        service.preflight(
            root_operator_agent_id="operator-0",
            target_fork_agent_id="operator-7-fork",
            handoff_id=str(handoff["handoff_id"]),
        )


def test_broker_service_root_mode_creates_durable_request(tmp_path) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)

    result = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )

    assert result["created"] is True
    assert result["request"]["status"] == "pending"
    assert result["request"]["expected_git_parents"] == ["b" * 40, "c" * 40]
    assert result["request"]["expected_git_tree"] == "d" * 40
    assert "receipt_summary" in result["request"]
    assert result["approval_report"]["status"] == "awaiting_broker_approval"
    assert result["approval_report"]["needs_input"] is True
    assignment = store.get_operator_campaign_assignment(
        result["request"]["assignment_id"]
    )
    assert assignment is not None
    assert assignment["state"] == "awaiting_broker_approval"
    assert assignment["last_report_id"] == result["approval_report"]["report_id"]
    events = store.list_events(limit=20)
    assert any(event["type"] == "operator_broker_request_created" for event in events)


def test_broker_request_and_needs_input_projection_are_restart_idempotent(
    tmp_path,
) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    first = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )

    restarted = OperatorBrokerService(
        Store(store.path),
        environ={"AGENT_PBX_OPERATOR_BROKER_MODE": "root_operator"},
    )
    second = restarted.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )

    assert first["created"] is True
    assert second["created"] is False
    assert second["request"]["request_id"] == first["request"]["request_id"]
    assert second["approval_report"]["report_id"] == first["approval_report"]["report_id"]
    reports = [
        report
        for report in restarted.store.list_reports("operator-0", limit=50)
        if report["metadata"].get("source") == "operator_broker"
    ]
    assert len(reports) == 1


def test_terminal_caller_report_is_preserved_but_deferred_by_open_broker(
    tmp_path,
) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    requested = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )
    request = requested["request"]
    report = store.create_report(
        "caller-7",
        ReportCreateRequest(
            project="demo",
            status="complete",
            summary="Work is locally complete",
            detail="The root broker validation is deliberately still pending.",
            metadata={
                "campaign_id": request["campaign_id"],
                "assignment_id": request["assignment_id"],
            },
        ),
    )

    assignment = store.get_operator_campaign_assignment(request["assignment_id"])
    projection = store.reconcile_operator_terminal_report(report["report_id"])
    assert assignment is not None
    assert assignment["state"] == "awaiting_broker_approval"
    assert assignment["completed_at"] is None
    assert projection["status"] == "deferred_workflow"
    assert any(item["kind"] == "broker_request" for item in projection["blockers"])


def test_store_deterministic_broker_request_does_not_reopen_terminal_result(
    tmp_path,
) -> None:
    store, handoff_id, digest = _broker_store(tmp_path)
    first, created = _create_request(store, handoff_id, digest)
    canceled = store.cancel_operator_broker_request(
        request_id=first["request_id"],
        root_operator_agent_id="operator-0",
        reason="root canceled before claim",
    )
    replayed, replay_created = _create_request(store, handoff_id, digest)

    assert created is True
    assert canceled["status"] == "canceled"
    assert replay_created is False
    assert replayed["request_id"] == first["request_id"]
    assert replayed["status"] == "canceled"


def test_broker_service_rejects_terminal_request_replay(tmp_path) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    requested = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )
    store.cancel_operator_broker_request(
        request_id=requested["request"]["request_id"],
        root_operator_agent_id="operator-0",
        reason="cancel before claim",
    )

    with pytest.raises(OperatorBrokerPolicyError, match="cannot be reopened"):
        service.request(
            root_operator_agent_id="operator-0",
            target_fork_agent_id="operator-7-fork",
            handoff_id=handoff["handoff_id"],
        )


def test_root_validates_exact_terminal_broker_receipt_before_completion(
    tmp_path,
) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    requested = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )
    request = requested["request"]
    transaction = None
    for step in ("stop", "broker_review", "broker_commit", "resume"):
        transaction = store.append_operator_action_receipt(
            handoff_id=handoff["handoff_id"],
            action_class="exact_git_commit_broker",
            target_agent_id="operator-7-fork",
            scope_sha256=request["scope_sha256"],
            initial_state="ready_stop",
            step=step,
            outcome="succeeded",
            actor="operator-0",
            evidence={"request_id": request["request_id"]},
        )
    assert transaction is not None
    transaction_id = transaction["transaction_id"]
    store.claim_operator_broker_request(
        request_id=request["request_id"], executor_id="tui-a"
    )
    store.transition_operator_broker_request(
        request_id=request["request_id"],
        executor_id="tui-a",
        status="executing",
        expected_statuses=("claimed",),
        action_transaction_id=transaction_id,
        result={"phase": "validation"},
    )
    store.transition_operator_broker_request(
        request_id=request["request_id"],
        executor_id="tui-a",
        status="succeeded",
        expected_statuses=("executing",),
        action_transaction_id=transaction_id,
        result={
            "old_head": request["expected_git_parent"],
            "new_head": "e" * 40,
            "verified_head": "e" * 40,
            "commit_parents": request["expected_git_parents"],
            "commit_subject": request["commit_subject"],
            "committed_paths": request["exact_paths"],
            "journal_done": True,
            "same_fork_resumed": True,
            "index_empty": True,
            "validation_pending": True,
        },
    )
    assignment_id = request["assignment_id"]
    campaign_id = request["campaign_id"]
    operator = OperatorService(store)
    with pytest.raises(ValueError, match="broker_request"):
        operator.report_assignment(
            operator_agent_id="operator-0",
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            state="complete",
            summary="Too early",
            detail="Root validation is still pending.",
        )

    validated = service.validate(
        root_operator_agent_id="operator-0",
        request_id=request["request_id"],
        action_transaction_id=transaction_id,
    )
    replayed = service.validate(
        root_operator_agent_id="operator-0",
        request_id=request["request_id"],
        action_transaction_id=transaction_id,
    )

    assert validated["request"]["validated_at"] is not None
    assert validated["request"]["result"]["validation_pending"] is False
    assert validated["transaction"]["state"] == "complete"
    assert replayed["validation_sha256"] == validated["validation_sha256"]
    delivery = validated["handoff"]["completion_delivery"]
    with pytest.raises(ValueError, match="completion_receipt"):
        operator.report_assignment(
            operator_agent_id="operator-0",
            campaign_id=campaign_id,
            assignment_id=assignment_id,
            state="complete",
            summary="Receipt unreviewed",
            detail="The source receipt still awaits acknowledgment.",
        )
    acknowledged = store.acknowledge_operator_handoff_completion_delivery(
        delivery["completion_delivery_id"],
        acknowledged_by=delivery["recipient_agent_id"],
    )
    assert acknowledged is not None
    operator.report_assignment(
        operator_agent_id="operator-0",
        campaign_id=campaign_id,
        assignment_id=assignment_id,
        state="complete",
        summary="Validated",
        detail="Exact terminal receipt and handoff completion were reviewed.",
    )
    campaign = operator.finish_campaign(
        operator_agent_id="operator-0",
        campaign_id=campaign_id,
        status="complete",
        summary="Complete",
        detail="All durable workflow records are closed.",
    )
    assert campaign["status"] == "complete"


def test_root_validation_rejects_foreign_transaction_and_receipt_drift(
    tmp_path,
) -> None:
    _store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    requested = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )

    with pytest.raises(OperatorBrokerPolicyError, match="succeeded terminal"):
        service.validate(
            root_operator_agent_id="operator-0",
            request_id=requested["request"]["request_id"],
            action_transaction_id="foreign-transaction",
        )


def test_broker_validation_api_is_authenticated_and_fail_closed(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, service, handoff, _digest = _qualified_broker_service(tmp_path)
    requested = service.request(
        root_operator_agent_id="operator-0",
        target_fork_agent_id="operator-7-fork",
        handoff_id=handoff["handoff_id"],
    )
    monkeypatch.setenv("AGENT_PBX_OPERATOR_BROKER_MODE", "root_operator")
    app = create_app(ServerConfig(db_path=store.path, token="secret"))
    client = TestClient(app)
    route = f"/v1/operator/broker-checkpoints/{requested['request']['request_id']}/validate"

    assert client.post(
        route,
        json={
            "root_operator_agent_id": "operator-0",
            "action_transaction_id": "foreign-transaction",
        },
    ).status_code == 401
    rejected = client.post(
        route,
        headers={"Authorization": "Bearer secret"},
        json={
            "root_operator_agent_id": "operator-0",
            "action_transaction_id": "foreign-transaction",
        },
    )
    assert rejected.status_code == 400
    assert "succeeded terminal" in rejected.json()["detail"]["message"]


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
