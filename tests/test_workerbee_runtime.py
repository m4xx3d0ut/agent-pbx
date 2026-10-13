from __future__ import annotations

from pathlib import Path

from agent_pbx.workerbee_runtime import (
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    WorkerBeeRuntimeRegistry,
    canonical_workerbee_backend,
)


def provenance(backend: str = "platform_debug", version: str = "1.30.0") -> dict[str, object]:
    return {
        "backend": backend,
        "endpoint": "http://127.0.0.1:8765/mcp",
        "server_name": "K1S WorkerBee",
        "server_version": version,
        "config_path": "/private/wb-mcp-switch.env",
    }


def test_runtime_registry_persists_selection_and_generation(tmp_path: Path) -> None:
    ticks = iter((10.0, 11.0, 12.0, 13.0))
    registry = WorkerBeeRuntimeRegistry(tmp_path, clock=lambda: next(ticks))

    first = registry.observe(provenance())
    same = registry.observe(provenance())
    changed = registry.observe(provenance("oss"), reason="switch")

    assert first.generation == 1
    assert same.generation == 1
    assert same.selected_at == first.selected_at
    assert changed.generation == 2
    assert changed.backend == "oss"
    assert registry.selected() == changed
    assert registry.selection_path.stat().st_mode & 0o777 == 0o600


def test_runtime_registry_force_generation_invalidates_same_backend(tmp_path: Path) -> None:
    registry = WorkerBeeRuntimeRegistry(tmp_path)
    first = registry.observe(provenance())
    restarted = registry.observe(provenance(), reason="backend_restart", force_generation=True)

    assert restarted.backend == first.backend
    assert restarted.generation == first.generation + 1


def test_backend_catalog_defers_user_appliance_without_hiding_contract(tmp_path: Path) -> None:
    registry = WorkerBeeRuntimeRegistry(tmp_path)
    registry.observe(provenance())

    catalog = {item.backend: item for item in registry.catalog()}

    assert catalog[WORKERBEE_BACKEND_PLATFORM_DEBUG].selected is True
    assert catalog[WORKERBEE_BACKEND_USER_APPLIANCE].available is False
    assert "deferred" in catalog[WORKERBEE_BACKEND_USER_APPLIANCE].unavailable_reason.lower()
    assert canonical_workerbee_backend("user-facing") == WORKERBEE_BACKEND_USER_APPLIANCE

