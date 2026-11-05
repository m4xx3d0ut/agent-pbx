from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping


OPERATOR_BROKER_MODE_ENV = "AGENT_PBX_OPERATOR_BROKER_MODE"
OPERATOR_BROKER_CONTROL_MODE_ENV = "AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE"
OPERATOR_BROKER_REQUEST_TTL_SECONDS_ENV = (
    "AGENT_PBX_OPERATOR_BROKER_REQUEST_TTL_SECONDS"
)
OPERATOR_BROKER_MAX_IN_FLIGHT_ENV = "AGENT_PBX_OPERATOR_BROKER_MAX_IN_FLIGHT"

OPERATOR_BROKER_MODES = ("guided", "root_operator", "disabled")
OPERATOR_BROKER_CONTROL_MODES = ("editable", "read_only", "hidden")
DEFAULT_OPERATOR_BROKER_REQUEST_TTL_SECONDS = 900
MIN_OPERATOR_BROKER_REQUEST_TTL_SECONDS = 60
MAX_OPERATOR_BROKER_REQUEST_TTL_SECONDS = 3600
DEFAULT_OPERATOR_BROKER_MAX_IN_FLIGHT = 1
MAX_OPERATOR_BROKER_MAX_IN_FLIGHT = 8
MAX_OPERATOR_BROKER_PATHS = 256
MAX_OPERATOR_BROKER_SUBJECT_LENGTH = 160

_HEX_OID = re.compile(r"[0-9a-f]{40,64}")


class OperatorBrokerPolicyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def public_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class OperatorBrokerPosture:
    mode: str
    control_mode: str
    request_ttl_seconds: int
    max_in_flight_per_root: int

    @property
    def root_operator_enabled(self) -> bool:
        return self.mode == "root_operator"

    @property
    def controls_hidden(self) -> bool:
        return self.control_mode == "hidden"

    def public_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "root_operator_enabled": self.root_operator_enabled,
            "controls_hidden": self.controls_hidden,
        }


def operator_broker_posture(
    environ: Mapping[str, str] | None = None,
) -> OperatorBrokerPosture:
    selected = os.environ if environ is None else environ
    mode = str(selected.get(OPERATOR_BROKER_MODE_ENV) or "guided").strip().lower()
    if mode not in OPERATOR_BROKER_MODES:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_MODE_INVALID",
            f"Unsupported Operator broker mode: {mode or '<empty>'}",
        )
    control_mode = str(
        selected.get(OPERATOR_BROKER_CONTROL_MODE_ENV) or "editable"
    ).strip().lower()
    if control_mode not in OPERATOR_BROKER_CONTROL_MODES:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_CONTROL_MODE_INVALID",
            f"Unsupported Operator broker control mode: {control_mode or '<empty>'}",
        )
    ttl = _bounded_integer(
        selected.get(OPERATOR_BROKER_REQUEST_TTL_SECONDS_ENV),
        default=DEFAULT_OPERATOR_BROKER_REQUEST_TTL_SECONDS,
        minimum=MIN_OPERATOR_BROKER_REQUEST_TTL_SECONDS,
        maximum=MAX_OPERATOR_BROKER_REQUEST_TTL_SECONDS,
        label="Operator broker request TTL",
    )
    maximum = _bounded_integer(
        selected.get(OPERATOR_BROKER_MAX_IN_FLIGHT_ENV),
        default=DEFAULT_OPERATOR_BROKER_MAX_IN_FLIGHT,
        minimum=1,
        maximum=MAX_OPERATOR_BROKER_MAX_IN_FLIGHT,
        label="Operator broker in-flight limit",
    )
    return OperatorBrokerPosture(
        mode=mode,
        control_mode=control_mode,
        request_ttl_seconds=ttl,
        max_in_flight_per_root=maximum,
    )


def normalize_broker_commit_subject(value: object) -> str:
    subject = str(value or "").strip()
    if not subject:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_SUBJECT_REQUIRED",
            "An exact one-line commit subject is required.",
        )
    if len(subject) > MAX_OPERATOR_BROKER_SUBJECT_LENGTH:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_SUBJECT_TOO_LONG",
            "The commit subject exceeds the 160-character broker limit.",
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in subject):
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_SUBJECT_INVALID",
            "The commit subject cannot contain control characters or newlines.",
        )
    return subject


def normalize_broker_expected_parent(value: object) -> str:
    parent = str(value or "").strip().lower()
    if not _HEX_OID.fullmatch(parent):
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PARENT_INVALID",
            "The expected Git parent must be one complete hexadecimal object ID.",
        )
    return parent


def normalize_broker_paths(values: Iterable[object]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "")
        if not value or value != value.strip():
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_INVALID",
                "Broker paths must be non-empty repository-relative paths without surrounding whitespace.",
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_INVALID",
                "Broker paths cannot contain control characters.",
            )
        path = PurePosixPath(value)
        if path.is_absolute() or value in {".", ".."} or ".." in path.parts:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_INVALID",
                f"Broker path escapes the repository root: {value}",
            )
        canonical = path.as_posix()
        if canonical.startswith(".git/") or canonical == ".git":
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_PROTECTED",
                "Git administration paths cannot be committed through the broker.",
            )
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
        if len(normalized) > MAX_OPERATOR_BROKER_PATHS:
            raise OperatorBrokerPolicyError(
                "OPERATOR_BROKER_PATH_LIMIT",
                f"A broker request may include at most {MAX_OPERATOR_BROKER_PATHS} paths.",
            )
    if not normalized:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_PATHS_REQUIRED",
            "At least one exact repository-relative path is required.",
        )
    return tuple(normalized)


def _bounded_integer(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_INTEGER_INVALID",
            f"{label} must be an integer.",
        ) from exc
    if parsed < minimum or parsed > maximum:
        raise OperatorBrokerPolicyError(
            "OPERATOR_BROKER_INTEGER_OUT_OF_RANGE",
            f"{label} must be between {minimum} and {maximum}.",
        )
    return parsed
