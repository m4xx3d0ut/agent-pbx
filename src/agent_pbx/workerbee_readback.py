"""Redacted WorkerBee selector and session diagnostics."""
from __future__ import annotations

from collections.abc import Mapping
import re
import sqlite3
import time
from typing import Any

from .workerbee_cutover import DB_NAME, WorkerBeeCutoverError, read_durable_readback
from .workerbee_modes import WORKERBEE_SOURCE_MODES, source_mode_from_legacy_strategy
from .workerbee_runtime_identity import (
    WORKERBEE_BACKENDS, canonical_workerbee_backend, workspace_strategy_for_backend,
)

READBACK_SCHEMA = "agent-pbx.workerbee-selector-readback/v1"
_IDENTITY_HEX = {"instance_id": 24, "fingerprint": 64}


def runtime_identity(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping) or value.get("backend") not in WORKERBEE_BACKENDS:
        return None
    backend = value["backend"]
    result: dict[str, Any] = {"backend": backend}
    generation = value.get("generation")
    if type(generation) is int and generation > 0:
        result["generation"] = generation
    for key, width in _IDENTITY_HEX.items():
        identity = value.get(key)
        if isinstance(identity, str) and re.fullmatch(f"[0-9a-f]{{{width}}}", identity):
            result[key] = identity
    strategy = value.get("workspace_strategy")
    if strategy == workspace_strategy_for_backend(backend):
        result["workspace_strategy"] = strategy
        result["source_mode"] = source_mode_from_legacy_strategy(strategy)
    return result


def local_readback(registry, *, controller: str | None = None) -> dict[str, Any]:
    """Return a safe local projection for either the daemon or offline Doctor."""
    path = registry.state_root / DB_NAME
    if not path.exists():
        selected = registry.selected()
        return {
            "selected": runtime_identity(selected.public_dict()) if selected else None,
            "observed": None,
            "admission": {
                "admission": "startup" if registry.cutover is not None else "legacy",
                "reason": "authority_not_attested",
            },
            "observation_state": "not_probed",
        }
    try:
        snapshot = read_durable_readback(path, controller=controller)
    except (OSError, sqlite3.Error, ValueError, WorkerBeeCutoverError):
        return {
            "selected": None, "observed": None, "observation_state": "not_probed",
            "admission": {"admission": "recovery_required", "reason": "selector_state_unreadable"},
        }
    selected = runtime_identity(snapshot["selected"])
    evidence = snapshot["observation"] if isinstance(snapshot["observation"], dict) else {}
    observed = runtime_identity(evidence)
    gate = snapshot["admission"]
    if gate["reason"] == "external_identity_drift":
        state = "mismatch"
    elif observed and selected:
        state = (
            "matches_selected" if evidence.get("against_generation") == selected.get("generation")
            and selected.get("fingerprint") == observed.get("fingerprint")
            else "historical_probe"
        )
    else:
        state = evidence.get("state", "not_probed")
    return {
        "selected": selected,
        "observed": {
            **observed, "checked_at": evidence.get("checked_at"),
            "against_generation": evidence.get("against_generation"),
        } if observed else None,
        "observation_state": state,
        "admission": gate,
        "last_transaction": switch_record(snapshot["latest"]),
        "rollback_transaction": (
            switch_record(snapshot["rollback"]) if gate["admission"] == "open" else None
        ),
    }


def daemon_readback(registry, switcher, *, controller: str | None = None) -> dict[str, Any]:
    """Probe under the daemon gate and keep the resulting projection read-only."""
    report: dict[str, Any] = {}
    if registry.cutover is not None and registry.cutover.posture()["admission"] == "open":
        try:
            report = switcher.status()
        except (OSError, ValueError, WorkerBeeCutoverError):
            # A concurrent close or invalid probe leaves the local receipt readable.
            pass
    view = local_readback(registry, controller=controller)
    open_gate = view["admission"]["admission"] == "open"
    latest = view["last_transaction"] if "last_transaction" in view else None
    observed = view["observed"]
    candidates = []
    for descriptor in report.get("backend_catalog", []):
        backend = descriptor.get("backend")
        if backend == "platform_debug" and controller is None:
            continue
        source_mode = descriptor.get("source_mode")
        qualification = descriptor.get("qualification")
        candidate = {
            "backend": backend, "source_mode": source_mode,
            "workspace_strategy": descriptor.get("workspace_strategy"),
            "available": descriptor.get("available") is True,
            "qualification_state": "unavailable",
        }
        if candidate["available"]:
            candidate["qualification_state"] = "configured_only"
        if observed and observed["backend"] == backend:
            candidate["qualification_state"] = "probed"
        if (controller is not None and latest and latest.get("phase") == "previewed"
                and latest.get("expires_at", 0) > time.time() and latest.get("source") == view["selected"]
                and (latest.get("target_runtime") or {}).get("backend") == backend):
            candidate["qualification_state"] = "reviewed_target"
        if controller is not None:
            candidate["descriptor_qualification"] = qualification
            candidate["configuration_sha256"] = descriptor.get("configuration_sha256")
        candidates.append(candidate)
    switch_targets = [
        item["backend"] for item in candidates
        if controller is not None and item["backend"] in {"oss", "platform_debug"}
        and item.get("descriptor_qualification") == "verified" and open_gate
        and callable(getattr(getattr(switcher, "adapter", None), "cutover_qualification", None))
    ]
    return {
        "api_version": READBACK_SCHEMA,
        "single_active_backend": True,
        **view,
        "bootstrap_available": open_gate and view["selected"] is None and observed is not None
        and view["observation_state"] == "probed",
        "active": observed or {},
        "available": bool(switch_targets),
        "targets": switch_targets,
        "backend_catalog": candidates,
        "backends": candidates,
        "generation_guard_required": True,
    }


def switch_record(
    record: Mapping[str, Any] | None, *, include_preview: bool = False,
) -> dict[str, Any] | None:
    """Serialize bounded reviewed evidence and keep the token in its original response."""
    if record is None:
        return None
    if "source_qualification" not in record:
        # Compatibility for the old switch API and test doubles.
        return {
            key: record[key] for key in ("transaction_id", "phase", "target", "before_backend", "rollback_backend")
            if key in record
        } | ({"preview_token": record["preview_token"]} if include_preview and "preview_token" in record else {})
    source = runtime_identity(record["source"])
    qualification = record["target_qualification"]
    backend = canonical_workerbee_backend(qualification["backend"])
    strategy = workspace_strategy_for_backend(backend)
    fingerprint = qualification["runtime_fingerprint"]
    target = runtime_identity({
        "backend": backend, "workspace_strategy": strategy,
        "instance_id": fingerprint[:24], "fingerprint": fingerprint,
        "generation": source["generation"] + int(record["source_qualification"] != qualification),
    })
    result = {key: record[key] for key in (
        "api_version", "transaction_id", "phase", "target", "created_at", "expires_at",
        "plan_digest", "plan_summary", "requires_privileged_confirmation",
        "existing_sessions_pinned", "future_sessions_only", "generation_guard_required",
    ) if key in record}
    result.update(
        source=source, before=source, target_runtime=target,
        after=runtime_identity(record.get("after")),
        rollback_after=runtime_identity(record.get("rollback_after")),
        target_qualification={
            key: qualification[key] for key in ("artifact_sha256", "effective_config_sha256", "route_sha256")
        },
    )
    if include_preview and "preview_token" in record:
        result["preview_token"] = record["preview_token"]
    return result


def binding_readback(
    selected: Mapping[str, Any] | None, bound: Mapping[str, Any] | None,
    *, override: str | None = None,
) -> dict[str, Any]:
    identity = runtime_identity(bound)
    if override:
        state = override
    elif not identity:
        state = "unbound"
    elif not selected:
        state = "selection_unavailable"
    elif any(identity.get(key) is None for key in ("backend", "generation", "instance_id", "fingerprint")):
        state = "legacy_unverified"
    elif any(identity[key] != selected.get(key) for key in ("backend", "generation", "instance_id", "fingerprint")):
        state = "rebind_required"
    else:
        state = "matches_selected"
    return {
        "state": state, "bound": identity, "selected": selected,
        "rebind_required": state in {
            "legacy_unverified", "rebind_required", "lease_missing", "lease_expired",
            "lease_revoked", "binding_mismatch",
        },
    }


def run_readback(record: Mapping[str, Any], selected: Mapping[str, Any] | None) -> dict[str, Any]:
    expired = record.get("status") == "approved" and (record.get("expires_at") or 0) <= time.time()
    return {
        **binding_readback(selected, {
            "backend": record.get("backend"), "instance_id": record.get("backend_instance_id"),
            "fingerprint": record.get("backend_fingerprint"), "generation": record.get("backend_generation"),
        }, override="approval_expired" if expired else None),
        "source_mode": "immutable-commit-export",
    }


def agent_binding_readback(app_state, agent: Mapping[str, Any], selected) -> dict[str, Any]:
    metadata = agent.get("metadata") or {}
    lease_id = metadata.get("workerbee_gateway_lease_id")
    bound = {
        "backend": metadata.get("workerbee_bound_backend") or metadata.get("workerbee_backend"),
        "generation": metadata.get("workerbee_backend_generation"),
    }
    state = None
    source_mode = None
    projection_stale = False
    if lease_id:
        try:
            lease = app_state.workerbee_gateway.load(lease_id)
        except (OSError, RuntimeError, ValueError):
            state = "lease_missing"
        else:
            if lease.agent_id != agent["agent_id"]:
                state = "binding_mismatch"
            else:
                bound = {
                    "backend": lease.backend, "generation": lease.backend_generation,
                    "instance_id": lease.backend_instance_id, "fingerprint": lease.backend_fingerprint,
                }
                source_mode = lease.source_mode if lease.source_mode in WORKERBEE_SOURCE_MODES else None
                if lease.revoked_at is not None or lease.status != "active":
                    state = "lease_revoked"
                elif lease.expires_at is not None and lease.expires_at <= time.time():
                    state = "lease_expired"
                projection_stale = metadata.get("workerbee_workspace_binding_id") != lease.workspace_binding_id
    return {
        **binding_readback(selected, bound, override=state),
        "source_mode": source_mode,
        "projection_stale": projection_stale,
    }
