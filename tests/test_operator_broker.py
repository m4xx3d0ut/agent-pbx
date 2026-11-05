from __future__ import annotations

import pytest

from agent_pbx.operator_broker import (
    OperatorBrokerPolicyError,
    normalize_broker_commit_subject,
    normalize_broker_expected_parent,
    normalize_broker_paths,
    operator_broker_posture,
)


def test_operator_broker_posture_defaults_to_guided_safe_path() -> None:
    posture = operator_broker_posture({})

    assert posture.mode == "guided"
    assert posture.control_mode == "editable"
    assert posture.request_ttl_seconds == 900
    assert posture.max_in_flight_per_root == 1
    assert posture.root_operator_enabled is False


def test_operator_broker_posture_accepts_bounded_root_operator_mode() -> None:
    posture = operator_broker_posture(
        {
            "AGENT_PBX_OPERATOR_BROKER_MODE": "root_operator",
            "AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE": "hidden",
            "AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS": "1200",
            "AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT": "2",
        }
    )

    assert posture.root_operator_enabled is True
    assert posture.controls_hidden is True
    assert posture.request_ttl_seconds == 1200
    assert posture.max_in_flight_per_root == 2


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("AGENT_PBX_OPERATOR_BROKER_MODE", "automatic"),
        ("AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE", "open"),
        ("AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS", "59"),
        ("AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT", "9"),
    ],
)
def test_operator_broker_posture_fails_closed_on_invalid_values(
    key: str,
    value: str,
) -> None:
    with pytest.raises(OperatorBrokerPolicyError):
        operator_broker_posture({key: value})


def test_operator_broker_normalizes_exact_immutable_scope() -> None:
    assert normalize_broker_commit_subject(" fix: exact broker receipt ") == (
        "fix: exact broker receipt"
    )
    assert normalize_broker_expected_parent("A" * 40) == "a" * 40
    assert normalize_broker_paths(
        ["src/app.py", "tests/test_app.py", "src/app.py"]
    ) == ("src/app.py", "tests/test_app.py")


@pytest.mark.parametrize(
    "paths",
    [[], ["../secret"], ["/etc/passwd"], [".git/config"], ["bad\npath"]],
)
def test_operator_broker_rejects_empty_escaping_or_protected_paths(
    paths: list[str],
) -> None:
    with pytest.raises(OperatorBrokerPolicyError):
        normalize_broker_paths(paths)


def test_operator_broker_rejects_multiline_subject() -> None:
    with pytest.raises(OperatorBrokerPolicyError):
        normalize_broker_commit_subject("fix: one\nbody")
