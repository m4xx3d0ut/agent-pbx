from pathlib import Path
import json
import subprocess

import pytest

from agent_pbx.operator import OperatorService
from agent_pbx.operator_authority import (
    OPERATOR_AUTHORITY_PROFILE_CEILING_ENV,
    OPERATOR_AUTHORITY_ROOT_CEILING_ENV,
    OperatorAuthorityService,
)
from agent_pbx.operator_delegation import (
    execution_scope_requires_manual_approval,
    normalize_execution_scope,
)
from agent_pbx.schemas import AgentRegisterRequest
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
