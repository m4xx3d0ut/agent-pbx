from __future__ import annotations

from copy import deepcopy

import pytest

from agent_pbx.workerbee_profile import (
    WORKERBEE_PROFILE_INTENT_METADATA_KEY,
    WorkerBeeProfileIntentError,
    build_workerbee_profile_intent,
    configured_operator_workerbee_profile,
    resolve_workerbee_profile_intent,
)


def trusted_operator_metadata() -> dict[str, object]:
    return {
        "agent_type": "operator",
        "operator_role": "root",
        "access_mode": "edit",
        "operator_authority_profile": "scoped-edit",
        "operator_authority_activated_profile": "scoped-edit",
        "operator_workspace_set_digest": "git-authority-digest-is-independent",
        "workerbee_mcp_profile": "trusted-workbench",
        "workerbee_access_profile_ceiling": "trusted-workbench",
        "workerbee_workspace_policy_digest": "a" * 64,
        "broker_policy": {"mode": "root-supervised-exact"},
    }


def test_caller_legacy_default_remains_local_deploy() -> None:
    intent = resolve_workerbee_profile_intent(
        "caller-1",
        {"agent_type": "caller"},
    )

    assert intent.profile == "local-deploy"
    assert intent.profile_ceiling == "local-deploy"


def test_scoped_edit_operator_can_use_independently_authorized_trusted_profile() -> None:
    metadata = trusted_operator_metadata()
    original = deepcopy(metadata)
    intent = build_workerbee_profile_intent("operator-6", metadata)
    metadata[WORKERBEE_PROFILE_INTENT_METADATA_KEY] = intent.public_dict()

    resolved = resolve_workerbee_profile_intent("operator-6", metadata)

    assert resolved.profile == "trusted-workbench"
    assert resolved.workspace_policy_digest == "a" * 64
    assert metadata["operator_authority_profile"] == "scoped-edit"
    assert metadata["operator_workspace_set_digest"] == (
        "git-authority-digest-is-independent"
    )
    assert metadata["broker_policy"] == original["broker_policy"]


def test_review_fork_cannot_inherit_trusted_workbench() -> None:
    metadata = trusted_operator_metadata()
    metadata.update(
        {
            "operator_role": "fork",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
        }
    )

    with pytest.raises(WorkerBeeProfileIntentError, match="review-readonly"):
        build_workerbee_profile_intent("operator-6-fork-review", metadata)


def test_trusted_profile_rejects_stale_workspace_policy_digest() -> None:
    metadata = trusted_operator_metadata()
    intent = build_workerbee_profile_intent("operator-6", metadata)
    metadata[WORKERBEE_PROFILE_INTENT_METADATA_KEY] = intent.public_dict()
    metadata["workerbee_workspace_policy_digest"] = "b" * 64

    with pytest.raises(WorkerBeeProfileIntentError, match="no longer matches"):
        resolve_workerbee_profile_intent("operator-6", metadata)


def test_explicit_trusted_profile_requires_durable_intent() -> None:
    with pytest.raises(WorkerBeeProfileIntentError, match="missing its durable"):
        resolve_workerbee_profile_intent(
            "operator-6",
            trusted_operator_metadata(),
        )


def test_profile_above_independent_ceiling_fails_closed() -> None:
    metadata = trusted_operator_metadata()
    metadata["workerbee_access_profile_ceiling"] = "local-deploy"

    with pytest.raises(WorkerBeeProfileIntentError, match="independent"):
        build_workerbee_profile_intent("operator-6", metadata)


def test_configured_operator_default_applies_only_to_new_root() -> None:
    metadata = {
        "agent_type": "operator",
        "operator_role": "root",
        "access_mode": "edit",
        "workerbee_access_profile_ceiling": "trusted-workbench",
        "workerbee_workspace_policy_digest": "a" * 64,
    }

    assert configured_operator_workerbee_profile(
        "operator-6",
        metadata,
        configured_default="trusted-workbench",
        existing_entity=False,
    ) == "trusted-workbench"
    assert configured_operator_workerbee_profile(
        "operator-6",
        metadata,
        configured_default="observe-local",
        existing_entity=False,
    ) == "observe-local"
    assert configured_operator_workerbee_profile(
        "operator-6",
        metadata,
        configured_default="trusted-workbench",
        existing_entity=True,
    ) == "local-deploy"


def test_explicit_entity_profile_overrides_configured_operator_default() -> None:
    metadata = trusted_operator_metadata()
    metadata["workerbee_mcp_profile"] = "local-deploy"
    intent = build_workerbee_profile_intent(
        "operator-6",
        metadata,
        profile="local-deploy",
    )
    metadata[WORKERBEE_PROFILE_INTENT_METADATA_KEY] = intent.public_dict()

    assert configured_operator_workerbee_profile(
        "operator-6",
        metadata,
        configured_default="trusted-workbench",
        existing_entity=False,
    ) == "local-deploy"


def test_configured_operator_default_never_broadens_callers_or_reviews() -> None:
    assert configured_operator_workerbee_profile(
        "caller-1",
        {"agent_type": "caller"},
        configured_default="trusted-workbench",
        existing_entity=False,
    ) == "local-deploy"
    assert configured_operator_workerbee_profile(
        "operator-6-review",
        {
            "agent_type": "operator",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
        },
        configured_default="trusted-workbench",
        existing_entity=False,
    ) == "review-readonly"


def test_trusted_profile_rejects_malformed_workspace_policy_digest() -> None:
    metadata = trusted_operator_metadata()
    metadata["workerbee_workspace_policy_digest"] = "not-a-digest"

    with pytest.raises(WorkerBeeProfileIntentError, match="valid.*digest"):
        build_workerbee_profile_intent("operator-6", metadata)
