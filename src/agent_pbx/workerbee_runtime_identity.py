"""Canonical WorkerBee runtime identity shared by selection and transport admission."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import time
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

WORKERBEE_RUNTIME_API_VERSION = "agent-pbx.workerbee-runtime/v1"
WORKERBEE_BACKEND_OSS = "oss"
WORKERBEE_BACKEND_USER_APPLIANCE = "user_appliance"
WORKERBEE_BACKEND_PLATFORM_DEBUG = "platform_debug"
WORKERBEE_BACKENDS = (
    WORKERBEE_BACKEND_OSS,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
)

WORKERBEE_WORKSPACE_DIRECT_HOST = "direct_host"
WORKERBEE_WORKSPACE_MANAGED_APPLIANCE = "managed_appliance_volume"
WORKERBEE_WORKSPACE_CONTENT_IMPORT = "content_addressed_import"


def canonical_workerbee_backend(value: object) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "local": WORKERBEE_BACKEND_OSS,
        "oss_local": WORKERBEE_BACKEND_OSS,
        "workerbee_oss": WORKERBEE_BACKEND_OSS,
        "user": WORKERBEE_BACKEND_USER_APPLIANCE,
        "appliance": WORKERBEE_BACKEND_USER_APPLIANCE,
        "user_facing": WORKERBEE_BACKEND_USER_APPLIANCE,
        "debug": WORKERBEE_BACKEND_PLATFORM_DEBUG,
        "platform": WORKERBEE_BACKEND_PLATFORM_DEBUG,
        "platform_admin": WORKERBEE_BACKEND_PLATFORM_DEBUG,
    }
    return aliases.get(normalized, normalized)


def workspace_strategy_for_backend(backend: str) -> str:
    selected = canonical_workerbee_backend(backend)
    if selected == WORKERBEE_BACKEND_OSS:
        return WORKERBEE_WORKSPACE_DIRECT_HOST
    if selected == WORKERBEE_BACKEND_USER_APPLIANCE:
        return WORKERBEE_WORKSPACE_MANAGED_APPLIANCE
    if selected == WORKERBEE_BACKEND_PLATFORM_DEBUG:
        return WORKERBEE_WORKSPACE_CONTENT_IMPORT
    raise ValueError(f"unsupported WorkerBee backend: {backend}")


def _runtime_identity_fields(provenance: Mapping[str, Any]) -> dict[str, str]:
    return {
        "backend": canonical_workerbee_backend(provenance.get("backend")),
        "endpoint": str(provenance.get("endpoint") or "").strip().rstrip("/"),
        "server_name": str(provenance.get("server_name") or "").strip(),
        "server_version": str(provenance.get("server_version") or "").strip(),
        "config_path": str(provenance.get("config_path") or "").strip(),
    }


def runtime_fingerprint(provenance: Mapping[str, Any]) -> str:
    fields = _runtime_identity_fields(provenance)
    encoded = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class WorkerBeeBackendDescriptor:
    backend: str
    label: str
    workspace_strategy: str
    available: bool
    selected: bool = False
    unavailable_reason: str = ""

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WorkerBeeRuntimeSelection:
    backend: str
    endpoint: str
    server_name: str
    server_version: str
    config_path: str
    fingerprint: str
    instance_id: str
    generation: int
    workspace_strategy: str
    selected_at: float
    observed_at: float
    reason: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": WORKERBEE_RUNTIME_API_VERSION,
            **asdict(self),
        }


def decode_runtime_selection(payload: object) -> WorkerBeeRuntimeSelection:
    if not isinstance(payload, dict) or payload.get("api_version") != WORKERBEE_RUNTIME_API_VERSION:
        raise ValueError("WorkerBee selected-runtime schema is invalid")
    try:
        selection = WorkerBeeRuntimeSelection(
            **{key: value for key, value in payload.items() if key != "api_version"}
        )
        fields = _runtime_identity_fields(payload)
        endpoint = urlsplit(selection.endpoint)
        if (
            any(type(payload[key]) is not str or payload[key] != value
                for key, value in fields.items())
            or selection.backend not in WORKERBEE_BACKENDS
            or endpoint.scheme not in ("http", "https")
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.query or endpoint.fragment
            or type(selection.generation) is not int
            or selection.generation < 1
            or selection.generation >= (1 << 63) - 1
            or selection.workspace_strategy != workspace_strategy_for_backend(selection.backend)
            or selection.fingerprint != runtime_fingerprint(fields)
            or selection.instance_id != selection.fingerprint[:24]
            or type(selection.reason) is not str
            or not all(
                type(value) in (int, float) and math.isfinite(value) and value >= 0
                for value in (selection.selected_at, selection.observed_at)
            )
            or selection.observed_at < selection.selected_at
        ):
            raise ValueError("WorkerBee selected-runtime identity is invalid")
        return selection
    except (AttributeError, KeyError, OverflowError, TypeError) as exc:
        raise ValueError("WorkerBee selected-runtime fields are invalid") from exc


def make_runtime_selection(
    provenance: Mapping[str, Any], *, reason: str, generation: int = 1,
    clock: Callable[[], float] = time.time,
) -> WorkerBeeRuntimeSelection:
    fields = _runtime_identity_fields(provenance)
    now = clock()
    digest = runtime_fingerprint(fields)
    selection = WorkerBeeRuntimeSelection(
        **fields,
        fingerprint=digest,
        instance_id=digest[:24],
        generation=generation,
        workspace_strategy=workspace_strategy_for_backend(fields["backend"]),
        selected_at=now,
        observed_at=now,
        reason=reason,
    )
    return decode_runtime_selection(selection.public_dict())
