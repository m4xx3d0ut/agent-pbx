from __future__ import annotations

from pathlib import Path

import pytest

from agent_pbx.store import Store
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.workerbee_access import (
    WorkerBeeAccessPolicyError,
    WorkerBeeAccessPolicyService,
)


def registered_store(tmp_path: Path, *, review: bool = False) -> Store:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            name="agent-1",
            metadata={
                "cwd": str(tmp_path),
                **({"access_mode": "review_readonly"} if review else {}),
            },
        )
    )
    return store


def test_access_profile_defaults_to_observe_and_persists_explicit_deploy(
    tmp_path: Path,
) -> None:
    service = WorkerBeeAccessPolicyService(registered_store(tmp_path), environ={})

    assert service.get_profile("agent-1")["effective_profile"] == "observe-local"
    updated = service.set_profile(
        "agent-1", profile="local-deploy", updated_by="test"
    )

    assert updated["effective_profile"] == "local-deploy"
    assert updated["changed"] is True
    assert service.store.get_agent("agent-1")["metadata"]["workerbee_mcp_profile"] == "local-deploy"


def test_access_profile_ceiling_and_review_fork_fail_closed(tmp_path: Path) -> None:
    constrained = WorkerBeeAccessPolicyService(
        registered_store(tmp_path),
        environ={"AGENT_PBX_WORKERBEE_ACCESS_PROFILE_CEILING": "observe-local"},
    )
    with pytest.raises(WorkerBeeAccessPolicyError) as ceiling:
        constrained.set_profile("agent-1", profile="local-deploy", updated_by="test")
    assert ceiling.value.code == "WORKERBEE_ACCESS_PROFILE_CEILING"

    review_root = tmp_path / "review"
    review = WorkerBeeAccessPolicyService(
        registered_store(review_root, review=True), environ={}
    )
    assert review.get_profile("agent-1")["effective_profile"] == "review-readonly"
    with pytest.raises(WorkerBeeAccessPolicyError) as readonly:
        review.set_profile("agent-1", profile="local-deploy", updated_by="test")
    assert readonly.value.code == "WORKERBEE_ACCESS_REVIEW_READONLY"


def test_access_profile_controls_can_be_hidden(tmp_path: Path) -> None:
    service = WorkerBeeAccessPolicyService(
        registered_store(tmp_path),
        environ={"AGENT_PBX_WORKERBEE_ACCESS_CONTROL_MODE": "hidden"},
    )

    assert service.get_profile("agent-1")["control_mode"] == "hidden"
    with pytest.raises(WorkerBeeAccessPolicyError) as locked:
        service.set_profile("agent-1", profile="observe-local", updated_by="test")
    assert locked.value.code == "WORKERBEE_ACCESS_POLICY_LOCKED"
