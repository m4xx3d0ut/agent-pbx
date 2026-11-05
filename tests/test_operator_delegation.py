from pathlib import Path
import json
import subprocess

import pytest

from agent_pbx.operator import OperatorService
from agent_pbx.operator_actions import project_handoff_action
from agent_pbx.operator_authority import (
    OPERATOR_AUTHORITY_PROFILE_CEILING_ENV,
    OPERATOR_AUTHORITY_ROOT_CEILING_ENV,
    OperatorAuthorityService,
)
from agent_pbx.operator_delegation import (
    execution_scope_digest,
    execution_scope_requires_manual_approval,
    normalize_execution_scope,
    resolve_handoff_execution_scope,
)
from agent_pbx.schemas import AgentRegisterRequest, ReportCreateRequest
from agent_pbx.store import SCHEMA_VERSION, Store


def register_operators(store: Store, tmp_path: Path) -> None:
    for agent_id in ("operator-0", "operator-7"):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={"cwd": str(tmp_path / agent_id)},
            )
        )


def test_exact_git_scope_is_normalized_and_persisted(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operators(store, tmp_path)
    service = OperatorService(store)

    handoff = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        message="Create only the reviewed commit.",
        execution_scope={
            "action_class": "exact_git_commit_broker",
            "campaign_id": "campaign-1",
            "workspace_set_digest": "a" * 64,
            "mutation_mode": "exact_paths",
            "allowed_roots": [str(tmp_path)],
            "exact_paths": ["src/app.py", "tests/test_app.py", "src/app.py"],
            "expected_git_parent": "b" * 40,
            "commit_subject": "fix: preserve exact scope",
        },
    )

    assert store.schema_version() == SCHEMA_VERSION
    assert handoff["execution_scope"]["version"] == 1
    assert handoff["execution_scope"]["exact_paths"] == [
        "src/app.py",
        "tests/test_app.py",
    ]
    assert handoff["execution_scope"]["denied_actions"]
    assert execution_scope_requires_manual_approval(handoff["execution_scope"]) is False


def test_handoff_rejects_execution_scope_misplaced_in_metadata(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operators(store, tmp_path)
    service = OperatorService(store)

    with pytest.raises(ValueError, match="top-level execution_scope"):
        service.create_handoff(
            operator_agent_id="operator-0",
            source_agent_id="operator-0",
            target_operator_agent_id="operator-7",
            message="Verify the reviewed checkpoint.",
            metadata={
                "execution_scope": {
                    "action_class": "custom_read_only_check",
                    "mutation_mode": "custom_read_only_mode",
                }
            },
        )

    assert store.list_operator_handoffs(logical_operator_agent_id="operator-0") == []


def test_legacy_managed_broker_scope_is_reduced_without_guessing(tmp_path: Path) -> None:
    handoff = {
        "execution_scope": {},
        "metadata": {
            "execution_scope": {
                "action_class": "oss_checkpoint_managed_broker_then_validation",
                "campaign_id": "campaign-1",
                "workspace_set_digest": "a" * 64,
                "allowed_roots": [{"path": str(tmp_path), "access": "exact"}],
                "exact_paths": [
                    {"path": "src/app.py", "access": "managed_broker_commit_only"},
                    {"path": "ci.yml", "access": "read_only_must_remain_identical"},
                ],
                "expected_parent": "b" * 40,
                "commit_subject": "Agent PBX approved commit",
            }
        },
    }

    scope = resolve_handoff_execution_scope(handoff)

    assert scope["action_class"] == "exact_git_commit_broker"
    assert scope["exact_paths"] == ["src/app.py"]
    assert scope["expected_git_parent"] == "b" * 40
    assert len(execution_scope_digest(scope)) == 64


def test_ready_action_transaction_records_guarded_stop_broker_resume_validate(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operators(store, tmp_path)
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    service = OperatorService(store)
    fork = service.ensure_fork(
        operator_agent_id="operator-7",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-7-fork-caller-1",
        status="running",
        metadata={"cwd": str(tmp_path), "pbx_mode": "report"},
    )
    scope = {
        "action_class": "exact_git_commit_broker",
        "campaign_id": "campaign-1",
        "workspace_set_digest": "a" * 64,
        "mutation_mode": "exact_paths",
        "allowed_roots": [str(tmp_path)],
        "exact_paths": ["src/app.py", "tests/test_app.py"],
        "expected_git_parent": "b" * 40,
        "commit_subject": "Agent PBX approved commit",
    }
    handoff = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        target_operator_fork_id=fork["operator_fork_id"],
        message="Use the exact guarded broker path.",
        execution_scope=scope,
    )
    handoff = store.update_operator_handoff(
        handoff["handoff_id"],
        status="acknowledged",
    )
    assert handoff is not None

    preflight = service.preflight_handoff_execution(
        operator_agent_id="operator-7",
        handoff_id=handoff["handoff_id"],
    )
    assert preflight["action"]["state"] == "ready_stop"
    assert preflight["transaction"] is None
    with pytest.raises(ValueError, match="different operator"):
        service.preflight_handoff_delivery(
            operator_agent_id="operator-7",
            handoff_id=handoff["handoff_id"],
        )

    service.ensure_fork(
        operator_agent_id="operator-7",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-7-fork-caller-1",
        status="canceled",
        metadata={"cwd": str(tmp_path), "pbx_mode": "report"},
    )
    store.create_report(
        "operator-7-fork-caller-1",
        ReportCreateRequest(
            project="agent-pbx-operator",
            status="canceled",
            summary="Stopped",
            detail="Stopped for broker",
        ),
    )
    store.set_agent_pbx_active("operator-7-fork-caller-1", False)
    stopped = service.record_handoff_action_receipt(
        operator_agent_id="operator-7",
        handoff_id=handoff["handoff_id"],
        step="stop",
        outcome="succeeded",
        actor="test",
    )
    assert stopped["transaction"]["state"] == "stopped_ready_broker"

    reviewed = service.record_handoff_action_receipt(
        operator_agent_id="operator-7",
        handoff_id=handoff["handoff_id"],
        step="broker_review",
        outcome="succeeded",
        actor="test",
        evidence={
            "head": "b" * 40,
            "commit_subject": "Agent PBX approved commit",
            "approved_stage_paths": ["src/app.py", "tests/test_app.py"],
        },
    )
    assert reviewed["transaction"]["state"] == "receipt_review"
    committed = service.record_handoff_action_receipt(
        operator_agent_id="operator-7",
        handoff_id=handoff["handoff_id"],
        step="broker_commit",
        outcome="succeeded",
        actor="test",
        evidence={
            "old_head": "b" * 40,
            "new_head": "c" * 40,
            "committed_paths": ["tests/test_app.py", "src/app.py"],
        },
    )
    assert committed["transaction"]["state"] == "committed_ready_resume"

    service.ensure_fork(
        operator_agent_id="operator-7",
        source_caller_agent_id="caller-1",
        fork_agent_id="operator-7-fork-caller-1",
        status="running",
        metadata={"cwd": str(tmp_path), "pbx_mode": "report"},
    )
    store.set_agent_pbx_active("operator-7-fork-caller-1", True)
    resumed = service.record_handoff_action_receipt(
        operator_agent_id="operator-7",
        handoff_id=handoff["handoff_id"],
        step="resume",
        outcome="succeeded",
        actor="test",
    )
    assert resumed["transaction"]["state"] == "resumed_ready_validation"
    complete = service.record_handoff_action_receipt(
        operator_agent_id="operator-7",
        handoff_id=handoff["handoff_id"],
        step="validate",
        outcome="succeeded",
        actor="test",
        evidence={"result": "green"},
    )
    assert complete["transaction"]["state"] == "complete"
    assert [receipt["step"] for receipt in complete["transaction"]["receipts"]] == [
        "stop",
        "broker_review",
        "broker_commit",
        "resume",
        "validate",
    ]


def test_ready_action_scope_drift_requires_reapproval(tmp_path: Path) -> None:
    handoff = {
        "handoff_id": "handoff-1",
        "logical_operator_agent_id": "operator-0",
        "target_operator_agent_id": "operator-7",
        "target_operator_fork_id": "fork-1",
        "status": "acknowledged",
        "updated_at": 100.0,
        "execution_scope": {
            "action_class": "exact_git_commit_broker",
            "campaign_id": "campaign-1",
            "workspace_set_digest": "a" * 64,
            "mutation_mode": "exact_paths",
            "allowed_roots": [str(tmp_path)],
            "exact_paths": ["src/app.py"],
            "expected_git_parent": "b" * 40,
            "commit_subject": "Agent PBX approved commit",
        },
        "metadata": {},
    }
    target = {
        "agent_id": "operator-7-fork",
        "status": "working",
        "pbx_active": True,
    }
    action = project_handoff_action(
        handoff,
        target_agent=target,
        transaction={
            "transaction_id": "tx-1",
            "scope_sha256": "f" * 64,
            "state": "receipt_review",
            "updated_at": 101.0,
        },
    )

    assert action is not None
    assert action["state"] == "blocked_reapproval_required"
    assert action["next_action"] is None
    assert "scope changed" in action["reason"]


def test_completed_handoff_requires_an_existing_action_transaction(
    tmp_path: Path,
) -> None:
    handoff = {
        "handoff_id": "handoff-1",
        "logical_operator_agent_id": "operator-0",
        "target_operator_agent_id": "operator-8",
        "target_operator_fork_id": "fork-1",
        "status": "complete",
        "updated_at": 100.0,
        "execution_scope": {
            "action_class": "exact_git_commit_broker",
            "campaign_id": "campaign-1",
            "workspace_set_digest": "a" * 64,
            "mutation_mode": "exact_paths",
            "allowed_roots": [str(tmp_path)],
            "exact_paths": ["src/app.py"],
            "expected_git_parent": "b" * 40,
            "commit_subject": "Agent PBX approved commit",
        },
        "metadata": {},
    }
    target = {
        "agent_id": "operator-8-fork",
        "status": "canceled",
        "pbx_active": False,
    }

    assert project_handoff_action(
        handoff,
        target_agent=target,
        transaction=None,
    ) is None

    action = project_handoff_action(
        handoff,
        target_agent=target,
        transaction={
            "transaction_id": "tx-1",
            "scope_sha256": execution_scope_digest(handoff["execution_scope"]),
            "state": "receipt_review",
            "updated_at": 101.0,
        },
    )
    assert action is not None
    assert action["state"] == "receipt_review"
    assert action["next_action"] == "broker_commit"


@pytest.mark.parametrize(
    "scope, message",
    [
        (
            {
                "action_class": "exact_git_commit_broker",
                "mutation_mode": "exact_paths",
                "exact_paths": ["../outside"],
                "expected_git_parent": "a" * 40,
                "commit_subject": "fix: nope",
            },
            "clean relative paths",
        ),
        (
            {
                "action_class": "exact_git_commit_broker",
                "mutation_mode": "exact_paths",
                "exact_paths": ["src/app.py"],
                "commit_subject": "fix: missing parent",
            },
            "expected Git parent",
        ),
        (
            {
                "action_class": "read_only_verification",
                "mutation_mode": "workspace",
            },
            "workspace-wide mutation",
        ),
    ],
)
def test_structured_handoff_scope_rejects_ambiguous_or_broad_mutation(
    scope: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_execution_scope(scope)


def test_sensitive_action_classes_are_always_manual() -> None:
    scope = normalize_execution_scope(
        {
            "action_class": "deployment",
            "mutation_mode": "workspace",
            "allowed_roots": ["/tmp/demo"],
        }
    )

    assert scope["deployment"] is True
    assert execution_scope_requires_manual_approval(scope) is True


def test_host_process_attestation_scope_is_canonical_and_always_manual() -> None:
    scope = normalize_execution_scope(
        {
            "action_class": "host_process_identity_attestation",
            "campaign_id": "campaign-1",
            "workspace_set_digest": "a" * 64,
            "mutation_mode": "read_only",
            "allowed_roots": ["/proc"],
            "host_process_evidence": {
                "candidate_pids": [321, 321],
                "expected_executable": "/usr/bin/sleep",
                "expected_argv0": "keeper",
                "expected_user_namespace": "user:[4026531837]",
                "expected_mount_namespace": "mnt:[4026531841]",
            },
        }
    )

    assert scope["host_process_evidence"]["candidate_pids"] == [321]
    assert execution_scope_requires_manual_approval(scope) is True


def test_host_process_attestation_f8_approval_mints_exact_evidence(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operators(store, tmp_path)
    service = OperatorService(store)
    handoff = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        message="Attest the exact approved host process identity.",
        metadata={"campaign_id": "campaign-1"},
        execution_scope={
            "action_class": "host_process_identity_attestation",
            "campaign_id": "campaign-1",
            "workspace_set_digest": "a" * 64,
            "mutation_mode": "read_only",
            "allowed_roots": ["/proc"],
            "host_process_evidence": {
                "candidate_pids": [321],
                "expected_executable": "/usr/bin/sleep",
                "expected_argv0": "keeper",
                "expected_user_namespace": "user:[4026531837]",
                "expected_mount_namespace": "mnt:[4026531841]",
            },
        },
    )

    approved = service.approve_handoff(
        operator_agent_id="operator-0",
        handoff_id=handoff["handoff_id"],
        delivery="record_only",
        metadata={"approved_by": "agent-pbx-tui"},
    )["handoff"]

    approval = approved["metadata"]["manual_approval"]
    assert approved["metadata"]["requires_f8_approval"] is True
    assert approved["metadata"]["approved_by_operator_agent_id"] == "operator-0"
    assert approval["method"] == "f8"
    assert approval["handoff_id"] == handoff["handoff_id"]
    assert approval["campaign_id"] == "campaign-1"
    assert approval["workspace_set_digest"] == "a" * 64

    with pytest.raises(ValueError, match="exact Agent PBX TUI approval"):
        service.approve_handoff(
            operator_agent_id="operator-0",
            handoff_id=handoff["handoff_id"],
            delivery="record_only",
        )


def test_trusted_campaign_lease_auto_approves_only_matching_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    monkeypatch.setenv(OPERATOR_AUTHORITY_ROOT_CEILING_ENV, json.dumps([str(tmp_path)]))
    monkeypatch.setenv(OPERATOR_AUTHORITY_PROFILE_CEILING_ENV, "trusted-workbench")

    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id in ("operator-0", "operator-7"):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={"cwd": str(repo), "pbx_mode": "nohup"},
            )
        )
    authority = OperatorAuthorityService(store)
    workspace = authority.save_workspace_set(
        set_id="rosie-stackops",
        label="Rosie StackOps",
        members=[str(repo)],
        updated_by="test",
    )
    for agent_id in ("operator-0", "operator-7"):
        authority.set_assignment(
            agent_id,
            profile="trusted-workbench",
            workspace_set_id=workspace["set_id"],
            profile_label="Rosie Workbench",
            updated_by="test",
        )
        authority.mark_activated(agent_id)
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-0",
        title="OSS qualification",
        objective="Qualify the exact checkpoint",
        criteria=[],
        assignments=[],
    )
    service = OperatorService(store)
    lease = service.request_delegation_lease(
        source_operator_agent_id="operator-0",
        target_operator_agent_ids=["operator-7"],
        campaign_ids=[campaign["campaign_id"]],
        workspace_set_digest=workspace["member_digest"],
        allowed_action_classes=["read_only_verification"],
        allowed_roots=[str(repo)],
        exact_paths=[],
        denied_actions=[],
        max_uses=2,
        ttl_seconds=3600,
        requested_by="test",
    )
    service.decide_delegation_lease(
        lease_id=lease["lease_id"],
        approve=True,
        decided_by="test-user",
    )

    delivered = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        message="Verify the reviewed checkpoint without mutation.",
        execution_scope={
            "action_class": "read_only_verification",
            "campaign_id": campaign["campaign_id"],
            "workspace_set_digest": workspace["member_digest"],
            "mutation_mode": "read_only",
            "allowed_roots": [str(repo)],
        },
    )

    assert delivered["status"] == "sent"
    assert delivered["metadata"]["delegated_approval"] is True
    assert delivered["metadata"]["delegation_lease_id"] == lease["lease_id"]
    used = store.get_operator_delegation_lease(lease["lease_id"])
    assert used is not None
    assert used["use_count"] == 1
    assert used["uses_remaining"] == 1

    manual = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-7",
        message="Deploy the artifact.",
        execution_scope={
            "action_class": "deployment",
            "campaign_id": campaign["campaign_id"],
            "workspace_set_digest": workspace["member_digest"],
            "mutation_mode": "workspace",
            "allowed_roots": [str(repo)],
        },
    )
    assert manual["status"] == "proposed"
    assert store.get_operator_delegation_lease(lease["lease_id"])["use_count"] == 1  # type: ignore[index]
