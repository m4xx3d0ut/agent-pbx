from __future__ import annotations

import pytest

from agent_pbx.config import (
    OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV,
    WORKERBEE_ACCESS_PROFILE_CEILING_ENV,
    ServerConfig,
    env_operator_workerbee_default_profile,
)


def test_operator_workerbee_default_is_public_local_deploy() -> None:
    assert env_operator_workerbee_default_profile({}) == "local-deploy"


def test_operator_workerbee_default_requires_independent_ceiling() -> None:
    environment = {
        OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV: "trusted-workbench",
        WORKERBEE_ACCESS_PROFILE_CEILING_ENV: "trusted-workbench",
    }

    assert env_operator_workerbee_default_profile(environment) == "trusted-workbench"

    with pytest.raises(ValueError, match="exceeds"):
        env_operator_workerbee_default_profile(
            {OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV: "trusted-workbench"}
        )


def test_server_config_captures_operator_workerbee_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        OPERATOR_WORKERBEE_DEFAULT_PROFILE_ENV,
        "trusted-workbench",
    )
    monkeypatch.setenv(
        WORKERBEE_ACCESS_PROFILE_CEILING_ENV,
        "trusted-workbench",
    )

    assert ServerConfig().operator_workerbee_default_profile == "trusted-workbench"
