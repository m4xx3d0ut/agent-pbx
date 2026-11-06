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


def test_access_profile_defaults_to_public_local_deploy_and_persists_explicit_observe(
    tmp_path: Path,
) -> None:
    service = WorkerBeeAccessPolicyService(registered_store(tmp_path), environ={})

    initial = service.get_profile("agent-1")
    assert initial["effective_profile"] == "local-deploy"
    assert initial["source_mode"] == "snapshot-import"
    assert initial["execution_lane"] == "interactive-gateway"
    updated = service.set_profile(
        "agent-1", profile="observe-local", updated_by="test"
    )

    assert updated["effective_profile"] == "observe-local"
    assert updated["changed"] is True
    assert service.store.get_agent("agent-1")["metadata"]["workerbee_mcp_profile"] == "observe-local"


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
    with pytest.raises(WorkerBeeAccessPolicyError) as source_readonly:
        review.set_policy(
            "agent-1", source_mode="native-direct", updated_by="test"
        )
    assert source_readonly.value.code == "WORKERBEE_ACCESS_REVIEW_READONLY"


def test_access_profile_controls_can_be_hidden(tmp_path: Path) -> None:
    service = WorkerBeeAccessPolicyService(
        registered_store(tmp_path),
        environ={"AGENT_PBX_WORKERBEE_ACCESS_CONTROL_MODE": "hidden"},
    )

    assert service.get_profile("agent-1")["control_mode"] == "hidden"
    with pytest.raises(WorkerBeeAccessPolicyError) as locked:
        service.set_profile("agent-1", profile="observe-local", updated_by="test")
    assert locked.value.code == "WORKERBEE_ACCESS_POLICY_LOCKED"


def test_policy_axes_update_independently_and_controlled_run_is_separate(
    tmp_path: Path,
) -> None:
    service = WorkerBeeAccessPolicyService(
        registered_store(tmp_path),
        environ={"AGENT_PBX_WORKERBEE_EXECUTION_LANE_CONTROL_MODE": "editable"},
    )

    updated = service.set_policy(
        "agent-1",
        source_mode="native-direct",
        updated_by="test",
    )

    assert updated["source_mode"] == "native-direct"
    assert updated["effective_profile"] == "local-deploy"
    assert updated["execution_lane"] == "interactive-gateway"
    with pytest.raises(WorkerBeeAccessPolicyError) as controlled:
        service.set_policy(
            "agent-1",
            execution_lane="controlled-run",
            updated_by="test",
        )
    assert controlled.value.code == "WORKERBEE_CONTROLLED_RUN_AUTHORITY_SEPARATE"


def test_trusted_workbench_requires_raised_ceiling_and_supports_local_label(
    tmp_path: Path,
) -> None:
    default = WorkerBeeAccessPolicyService(registered_store(tmp_path), environ={})
    with pytest.raises(WorkerBeeAccessPolicyError) as ceiling:
        default.set_profile(
            "agent-1", profile="trusted-workbench", updated_by="test"
        )
    assert ceiling.value.code == "WORKERBEE_ACCESS_PROFILE_CEILING"

    raised_root = tmp_path / "raised"
    raised = WorkerBeeAccessPolicyService(
        registered_store(raised_root),
        environ={
            "AGENT_PBX_WORKERBEE_ACCESS_PROFILE_CEILING": "trusted-workbench",
            "AGENT_PBX_WORKERBEE_TRUSTED_WORKBENCH_LABEL": "Rosie Workbench",
        },
    )
    result = raised.set_profile(
        "agent-1", profile="trusted-workbench", updated_by="test"
    )
    assert result["effective_profile"] == "trusted-workbench"
    assert result["access_profile_label"] == "Rosie Workbench"
    assert "local-exec" in result["requested_capability_families"]


def test_trusted_direct_bind_is_excluded_from_default_source_ceiling(
    tmp_path: Path,
) -> None:
    service = WorkerBeeAccessPolicyService(registered_store(tmp_path), environ={})

    with pytest.raises(WorkerBeeAccessPolicyError) as ceiling:
        service.set_policy(
            "agent-1",
            source_mode="trusted-direct-bind",
            updated_by="test",
        )

    assert ceiling.value.code == "WORKERBEE_SOURCE_MODE_CEILING"
