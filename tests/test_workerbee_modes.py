from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_pbx.workerbee_modes import (
    WORKERBEE_CAPABILITY_FAMILY_TOOLS,
    WORKERBEE_FAMILY_EXPORT,
    WORKERBEE_FAMILY_RUNTIME_OBSERVE,
    WORKERBEE_HIGH_RISK_TOOLS,
    WORKERBEE_K1S_PROFILE_EVIDENCE_CAPABILITY_CONTRACT,
    WORKERBEE_K1S_PROFILE_EVIDENCE_OPERATIONS,
    WORKERBEE_PROFILE_LOCAL_DEPLOY,
    WORKERBEE_PROFILE_REVIEW,
    WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
    WORKERBEE_SOURCE_MANAGED_CLONE,
    WORKERBEE_SOURCE_NATIVE_DIRECT,
    WORKERBEE_SOURCE_SNAPSHOT_IMPORT,
    WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,
    WorkerBeeModeError,
    capability_families_for_profile,
    legacy_strategy_for_source_mode,
    resolve_workerbee_tool_surface,
    reviewed_tools_for_access_profile,
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
    assert "workerbee_v1_bundle_export" in trusted
    assert "workerbee_v1_project_runbook_export" in trusted
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
    assert WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE in review


def test_k1s_profile_evidence_contract_is_locked_read_only_local_state() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "workerbee_tool_catalogs_rc2.json").read_text(
            encoding="utf-8"
        )
    )
    contract = fixture["k1s_profile_evidence_contract"]

    assert contract == {
        "tool": WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,
        "arguments": {
            "operation": "str",
            "profile": "str | None",
            "component": "str | None",
        },
        "public_operations": list(WORKERBEE_K1S_PROFILE_EVIDENCE_OPERATIONS),
        "read_only": True,
        "local_state": True,
        "networked": False,
        "bootstrap": "internal-pure-projection-not-exposed",
        "capability_contract": WORKERBEE_K1S_PROFILE_EVIDENCE_CAPABILITY_CONTRACT,
        "k1s_core_contracts": {"adapter": "v1", "evidence": "v1"},
        "qualification": "source-validated/unqualified",
    }
    assert WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE in (
        WORKERBEE_CAPABILITY_FAMILY_TOOLS[WORKERBEE_FAMILY_RUNTIME_OBSERVE]
    )
    assert WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE not in WORKERBEE_HIGH_RISK_TOOLS


def test_k1s_profile_evidence_missing_is_a_distinct_surface_diagnostic() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "workerbee_tool_catalogs_rc2.json").read_text(
            encoding="utf-8"
        )
    )
    advertised = [
        tool
        for tool in fixture["oss_tools"]
        if tool != WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE
    ]

    surface = resolve_workerbee_tool_surface(
        WORKERBEE_PROFILE_LOCAL_DEPLOY,
        advertised,
        backend="oss",
    )

    assert surface.compatible is False
    assert surface.missing_tools == (WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,)
    assert WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE not in surface.effective_tools


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

    surface = resolve_workerbee_tool_surface(
        WORKERBEE_PROFILE_LOCAL_DEPLOY, backend, backend="oss"
    )

    assert surface.compatible is False
    assert surface.missing_tools == (requested[-1],)
    assert surface.unknown_backend_tools == ("workerbee_v1_backend_extension",)
    assert surface.denied_high_risk_tools == (
        "workerbee_v1_manifest_deploy_remote_k1s",
    )
    assert "workerbee_v1_manifest_deploy_remote_k1s" not in surface.effective_tools


def test_tool_surface_quarantines_unreviewed_backend_extension_when_requested_set_is_complete() -> None:
    requested = tools_for_access_profile(WORKERBEE_PROFILE_LOCAL_DEPLOY)

    surface = resolve_workerbee_tool_surface(
        WORKERBEE_PROFILE_LOCAL_DEPLOY,
        (*requested, "workerbee_v1_backend_extension"),
        backend="oss",
    )

    assert surface.missing_tools == ()
    assert surface.unknown_backend_tools == ("workerbee_v1_backend_extension",)
    assert "workerbee_v1_backend_extension" in surface.quarantined_backend_tools
    assert "workerbee_v1_backend_extension" not in surface.effective_tools
    assert surface.compatible is True
    assert surface.catalog_fully_reviewed is False
    assert surface.public_dict()["posture"] == (
        "warning-unreviewed-backend-tools-quarantined"
    )


def test_exact_oss_catalog_supports_trusted_local_surface_and_quarantines_risk() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "workerbee_tool_catalogs_rc2.json").read_text(
            encoding="utf-8"
        )
    )

    surface = resolve_workerbee_tool_surface(
        WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
        fixture["oss_tools"],
        backend="oss",
    )

    assert surface.compatible is True
    assert surface.missing_tools == ()
    assert set(surface.effective_tools) == set(
        tools_for_access_profile(WORKERBEE_PROFILE_TRUSTED_WORKBENCH)
    )
    assert not set(surface.effective_tools).intersection(WORKERBEE_HIGH_RISK_TOOLS)
    assert set(surface.denied_high_risk_tools) == {
        "workerbee_v1_cleanup",
        "workerbee_v1_ingress_ca_regenerate",
        "workerbee_v1_manifest_deploy_remote_k1s",
        "workerbee_v1_project_reset",
        "workerbee_v1_trust_install",
        "workerbee_v1_trust_uninstall",
    }


def test_platform_debug_optional_surface_is_reviewed_without_changing_public_required_tools() -> None:
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "workerbee_tool_catalogs_rc2.json").read_text(
            encoding="utf-8"
        )
    )
    catalog = [
        *fixture["oss_tools"],
        *fixture["platform_debug_reviewed_optional_tools"],
        *fixture["platform_debug_quarantined_extensions"],
    ]

    surface = resolve_workerbee_tool_surface(
        WORKERBEE_PROFILE_TRUSTED_WORKBENCH,
        catalog,
        backend="platform_debug",
    )

    assert surface.compatible is True
    assert surface.effective_optional_tools == ("workerbee_v1_ingress_route_apply",)
    assert set(surface.effective_tools) == set(
        reviewed_tools_for_access_profile(
            WORKERBEE_PROFILE_TRUSTED_WORKBENCH, "platform_debug"
        )
    )
    assert set(fixture["platform_debug_quarantined_extensions"]).issubset(
        surface.quarantined_backend_tools
    )
    assert not set(fixture["platform_debug_quarantined_extensions"]).intersection(
        surface.effective_tools
    )


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
