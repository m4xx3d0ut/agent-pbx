from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_component_boundaries.py"
CONTRACT = ROOT / "tests" / "fixtures" / "component_decomposition_v1.json"

ROUTE_OWNERSHIP = {
    "evidence_audit": {
        ("POST", "/v1/evidence/preview"),
        ("POST", "/v1/evidence/exports"),
        ("POST", "/v1/evidence/verify"),
        ("POST", "/v1/evidence/retention/preview"),
        ("POST", "/v1/evidence/{export_id}/joplin-render"),
    },
    "operator_kb_v2": {
        ("POST", "/v1/operator/kb/bundle-v2/export"),
        ("POST", "/v1/operator/kb/bundle-v2/preview"),
        ("POST", "/v1/operator/kb/bundle-v2/import"),
        ("POST", "/v1/operator/kb/bundle-v2/git-tree"),
    },
    "durable_helpers": {
        ("GET", "/v1/operator/helpers"),
        ("POST", "/v1/operator/helpers/propose"),
        ("POST", "/v1/operator/helpers/{helper_id}/validate"),
        ("POST", "/v1/operator/helpers/{helper_id}/transition"),
        ("POST", "/v1/operator/helpers/{helper_id}/export"),
        ("POST", "/v1/operator/helpers/import"),
        ("POST", "/v1/operator/helpers/{helper_id}/invocation"),
        ("GET", "/v1/operator/helpers/measurements"),
    },
    "dynamic_resources": {
        ("GET", "/v1/operator/resources"),
        ("POST", "/v1/operator/resources/request"),
        ("POST", "/v1/operator/resources/{request_id}/approve"),
        ("POST", "/v1/operator/resources/{request_id}/acquire"),
        ("POST", "/v1/operator/resources/{request_id}/use"),
        ("POST", "/v1/operator/resources/{request_id}/release"),
        ("POST", "/v1/operator/resources/{request_id}/recover"),
    },
    "protected_provider_diagnostics": {
        ("POST", "/v2/operator/protected-provider/diagnose"),
    },
}


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("component_boundary_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_component_boundary_checker_passes() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "Component decomposition boundaries passed."


def test_dependency_direction_has_no_forbidden_edges() -> None:
    assert _checker().dependency_errors() == []


def test_store_schema_and_transaction_inventory_are_versioned() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert contract["schema"] == "agent-pbx.component-decomposition-contract/v1"
    assert contract["store_contract"] == _checker().collect_store_contract()
    assert contract["store_contract"]["schema_version"] == 45
    assert contract["store_contract"]["begin_immediate_methods"]


def test_owned_route_families_remain_complete(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    actual = {
        (method, route.path)
        for route in app.routes
        if hasattr(route, "methods")
        for method in route.methods
    }

    for family, expected in ROUTE_OWNERSHIP.items():
        missing = expected - actual
        assert not missing, f"{family} lost routes: {sorted(missing)}"


def test_typed_service_container_preserves_legacy_state_and_route_owner(
    tmp_path: Path,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    assert app.state.api_services.config is app.state.config
    assert app.state.api_services.evidence_exports is app.state.evidence_exports
    assert app.state.api_services.operator_service is app.state.operator_service
    assert app.state.api_services.durable_helpers is app.state.durable_helpers
    assert app.state.api_services.dynamic_resources is app.state.dynamic_resources
    assert (
        app.state.api_services.protected_provider_diagnostics
        is app.state.protected_provider_diagnostics
    )
    evidence_routes = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if route.path.startswith("/v1/evidence/")
    }
    assert evidence_routes
    assert set(evidence_routes.values()) == {"agent_pbx.api_routes.evidence"}
    helper_routes = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if route.path.startswith("/v1/operator/helpers")
    }
    assert helper_routes
    assert set(helper_routes.values()) == {
        "agent_pbx.api_routes.durable_helpers"
    }
    resource_routes = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if route.path.startswith("/v1/operator/resources")
    }
    assert resource_routes
    assert set(resource_routes.values()) == {
        "agent_pbx.api_routes.dynamic_resources"
    }
    kb_v2_routes = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if route.path.startswith("/v1/operator/kb/bundle-v2/")
    }
    assert kb_v2_routes
    assert set(kb_v2_routes.values()) == {
        "agent_pbx.api_routes.operator_kb_v2"
    }
