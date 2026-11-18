from __future__ import annotations

from pathlib import Path

import pytest

from agent_pbx.codex.policy import (
    CODEX_MODEL_CALLER_PROFILE_ENV,
    CODEX_MODEL_ELEVATION_PROFILES_ENV,
    CODEX_MODEL_OPERATOR_PROFILE_ENV,
    CodexModelControlMode,
    CodexModelSelectionClass,
    PUBLIC_CODEX_MODEL_POLICY,
    classify_codex_model_selection,
    load_codex_model_policy,
)
from agent_pbx.codex_cli import CodexModelOption


def model(slug: str, *efforts: str) -> CodexModelOption:
    return CodexModelOption(
        slug=slug,
        display_name=slug,
        default_reasoning_level=efforts[0] if efforts else "",
        supported_reasoning_levels=tuple(efforts),
        service_tiers=(),
        default_service_tier="",
        visibility="list",
        shell_type="shell",
        supported_in_api=True,
        supports_verbosity=True,
        default_verbosity="high",
    )


def test_public_model_policy_uses_safe_6_2_defaults(tmp_path: Path) -> None:
    policy = load_codex_model_policy(
        environ={}, path=tmp_path / "missing-model-policy.toml"
    )

    assert policy.control_mode is CodexModelControlMode.GUARD_ELEVATED
    assert policy.profile_id_for_role("caller") == "sol-6.2-medium"
    assert policy.profile_id_for_role("operator") == "sol-6.2-high"
    assert policy.profile_id_for_role("edit_fork") == "sol-6.2-high"
    assert policy.efficient_profile_id == "luna-6-high"
    assert policy.visual_profile_id == "terra-6.1-high"


def test_owner_local_policy_overrides_roles_and_elevations(tmp_path: Path) -> None:
    policy_path = tmp_path / "model-policy.toml"
    policy_path.write_text(
        """\
control_mode = "guard_elevated"

[defaults]
caller = "sol-6.2-high"
operator = "sol-6.2-xhigh"
edit_fork = "sol-6.2-xhigh"
review_fork = "sol-6.2-xhigh"

[tasks]
efficient = "luna-6-high"
visual = "terra-6.1-high"

[elevations]
allowed = ["sol-6.2-max", "sol-6.2-ultra", "astra-6.2-xhigh"]
""",
        encoding="utf-8",
    )
    policy_path.chmod(0o600)

    policy = load_codex_model_policy(environ={}, path=policy_path, strict=True)

    assert policy.caller_profile_id == "sol-6.2-high"
    assert policy.operator_profile_id == "sol-6.2-xhigh"
    assert policy.edit_fork_profile_id == "sol-6.2-xhigh"
    assert policy.elevation_profile_ids == (
        "sol-6.2-max",
        "sol-6.2-ultra",
        "astra-6.2-xhigh",
    )
    assert policy.source == str(policy_path.resolve())
    assert len(policy.source_sha256) == 64
    assert policy.warnings == ()


def test_environment_overrides_policy_file(tmp_path: Path) -> None:
    policy_path = tmp_path / "model-policy.toml"
    policy_path.write_text(
        "[defaults]\ncaller = \"sol-6.2-medium\"\noperator = \"sol-6.2-high\"\n",
        encoding="utf-8",
    )
    policy_path.chmod(0o600)

    policy = load_codex_model_policy(
        environ={
            CODEX_MODEL_CALLER_PROFILE_ENV: "sol-6.2-high",
            CODEX_MODEL_OPERATOR_PROFILE_ENV: "sol-6.2-xhigh",
            CODEX_MODEL_ELEVATION_PROFILES_ENV: "sol-6.2-max,astra-6.2-max",
        },
        path=policy_path,
        strict=True,
    )

    assert policy.caller_profile_id == "sol-6.2-high"
    assert policy.operator_profile_id == "sol-6.2-xhigh"
    assert policy.elevation_profile_ids == ("sol-6.2-max", "astra-6.2-max")


def test_invalid_local_policy_degrades_without_breaking_startup(tmp_path: Path) -> None:
    policy_path = tmp_path / "model-policy.toml"
    policy_path.write_text(
        "[defaults]\ncaller = \"missing\"\noperator = \"sol-6.2-max\"\n",
        encoding="utf-8",
    )
    policy_path.chmod(0o644)

    policy = load_codex_model_policy(environ={}, path=policy_path)

    assert policy.caller_profile_id == PUBLIC_CODEX_MODEL_POLICY.caller_profile_id
    assert policy.operator_profile_id == PUBLIC_CODEX_MODEL_POLICY.operator_profile_id
    assert any("readable by group" in warning for warning in policy.warnings)
    assert any("Invalid non-elevated" in warning for warning in policy.warnings)


def test_invalid_local_policy_is_rejected_in_strict_mode(tmp_path: Path) -> None:
    policy_path = tmp_path / "model-policy.toml"
    policy_path.write_text(
        "[defaults]\ncaller = \"sol-6.2-max\"\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid non-elevated"):
        load_codex_model_policy(environ={}, path=policy_path, strict=True)


def test_direct_model_selection_accepts_catalog_choice_without_adopting_it() -> None:
    selection = classify_codex_model_selection(
        model="gpt-6.2-sol",
        reasoning_effort="low",
        catalog=(model("gpt-6.2-sol", "low", "medium", "high", "xhigh"),),
    )

    assert selection.classification is CodexModelSelectionClass.CATALOG_OBSERVED
    assert selection.managed_profile_id is None
    assert selection.allowed is True
    assert selection.requires_approval is False


def test_direct_managed_max_selection_requires_lease() -> None:
    selection = classify_codex_model_selection(
        model="gpt-6.2-sol",
        reasoning_effort="max",
        catalog=(model("gpt-6.2-sol", "high", "xhigh", "max", "ultra"),),
    )

    assert selection.classification is CodexModelSelectionClass.MANAGED_QUALIFIED
    assert selection.managed_profile_id == "sol-6.2-max"
    assert selection.requires_approval is True
    assert selection.allowed is False


def test_direct_custom_provider_model_remains_observable() -> None:
    selection = classify_codex_model_selection(
        model="local-coder",
        reasoning_effort="high",
        model_provider="ollama",
        catalog=(),
    )

    assert (
        selection.classification
        is CodexModelSelectionClass.CUSTOM_PROVIDER_OBSERVED
    )
    assert selection.allowed is True
