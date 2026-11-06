from __future__ import annotations

import pytest

from agent_pbx.workerbee_modes import (
    WORKERBEE_FAMILY_EXPORT,
    WORKERBEE_HIGH_RISK_TOOLS,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    WORKERBEE_SOURCE_MANAGED_CLONE,
    WORKERBEE_SOURCE_NATIVE_DIRECT,
    WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
    WorkerBeeModeError,
    capability_families_for_profile,
    legacy_strategy_for_source_mode,
    resolve_workerbee_tool_surface,
    source_mode_from_legacy_strategy,
    tools_for_access_profile,
    tools_for_capability_families,
    validate_independent_mode_selection,
)


def test_profile_families_expand_through_fixed_registry_without_wildcards() -> None:
    local = tools_for_access_profile(WORKERBEE_PROFILE_LOCAL_DEPLOY)
    trusted = tools_for_access_profile(WORKERBEE_PROFILE_TRUSTED_WORKBENCH)

    assert "workerbee_v1_manifest_deploy_local" in local
    assert "workerbee_v1_exec" not in local
    assert "workerbee_v1_exec" in trusted
    assert "workerbee_v1_project_export" in trusted
    assert not any("*" in tool for tool in trusted)
    assert not WORKERBEE_HIGH_RISK_TOOLS.intersection(trusted)
    assert WORKERBEE_FAMILY_EXPORT in capability_families_for_profile(
        WORKERBEE_PROFILE_TRUSTED_WORKBENCH
    )


def test_review_profile_is_nonmutating() -> None:
    review = set(tools_for_access_profile(WORKERBEE_PROFILE_REVIEW))

    assert "workerbee_v1_session_start" not in review
    assert "workerbee_v1_image_build" not in review
    assert "workerbee_v1_ingress_route_apply" not in review
    assert "workerbee_v1_project_status" in review
    assert "workerbee_v1_logs" in review


def test_unknown_capability_family_fails_closed() -> None:
    with pytest.raises(WorkerBeeModeError) as failure:
        tools_for_capability_families(("build-deploy", "backend-*"))

    assert failure.value.code == "WORKERBEE_CAPABILITY_FAMILY_UNKNOWN"


def test_tool_surface_reports_missing_unknown_and_denied_tools() -> None:
    requested = tools_for_access_profile(WORKERBEE_PROFILE_LOCAL_DEPLOY)
    backend = [
        *requested[:-1],
        "workerbee_v1_backend_extension",
        "workerbee_v1_manifest_deploy_remote_k1s",
    ]

    surface = resolve_workerbee_tool_surface(WORKERBEE_PROFILE_LOCAL_DEPLOY, backend)

    assert surface.compatible is False
    assert surface.missing_tools == (requested[-1],)
    assert surface.unknown_backend_tools == ("workerbee_v1_backend_extension",)
    assert surface.denied_high_risk_tools == (
        "workerbee_v1_manifest_deploy_remote_k1s",
    )
    assert "workerbee_v1_manifest_deploy_remote_k1s" not in surface.effective_tools


def test_tool_surface_rejects_unreviewed_backend_extension_when_requested_set_is_complete() -> None:
    requested = tools_for_access_profile(WORKERBEE_PROFILE_LOCAL_DEPLOY)

    surface = resolve_workerbee_tool_surface(
        WORKERBEE_PROFILE_LOCAL_DEPLOY,
        (*requested, "workerbee_v1_backend_extension"),
    )

    assert surface.missing_tools == ()
    assert surface.unknown_backend_tools == ("workerbee_v1_backend_extension",)
    assert surface.compatible is False


@pytest.mark.parametrize(
    ("legacy", "source_mode"),
    [
        ("direct_host", WORKERBEE_SOURCE_NATIVE_DIRECT),
        ("content_addressed_import", WORKERBEE_SOURCE_SNAPSHOT_IMPORT),
        ("managed_appliance_volume", WORKERBEE_SOURCE_MANAGED_CLONE),
    ],
)
def test_legacy_workspace_strategy_migrates_deterministically(
    legacy: str,
    source_mode: str,
) -> None:
    assert source_mode_from_legacy_strategy(legacy) == source_mode
    assert legacy_strategy_for_source_mode(source_mode) == legacy


def test_four_axes_are_validated_without_implicit_coupling() -> None:
    selected = validate_independent_mode_selection(
        selected_backend="platform-debug",
        source_mode="native-direct",
        access_profile="trusted-workbench",
        execution_lane="interactive-gateway",
    )

    assert selected.public_dict() == {
        "api_version": "agent-pbx.workerbee-modes/v1",
        "selected_backend": "platform_debug",
        "source_mode": "native-direct",
        "access_profile": "trusted-workbench",
        "execution_lane": "interactive-gateway",
    }
