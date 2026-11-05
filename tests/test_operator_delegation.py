from pathlib import Path

import pytest

from agent_pbx.operator import OperatorService
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
