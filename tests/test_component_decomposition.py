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
TUI_COMPAT = ROOT / "tests" / "fixtures" / "tui_compat_v1.json"
API_ROUTE_INVENTORY = ROOT / "tests" / "fixtures" / "api_route_inventory_v1.json"

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


def test_future_component_ownership_rules_reject_absolute_and_relative_bypasses(
    tmp_path: Path,
) -> None:
    checker = _checker()
    examples = {
        "src/agent_pbx/ui/screens/modal.py": "from ...store import Store\n",
        "src/agent_pbx/ui/controllers/runtime.py": (
            "from ...persistence.repositories.runtime import RuntimeRepository\n"
        ),
        "src/agent_pbx/api_routes/agents.py": "from ..store import Store\n",
        "src/agent_pbx/persistence/repositories/agents.py": (
            "from ...api import create_app\n"
        ),
    }
    for relative, source in examples.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    checker.ROOT = tmp_path

    errors = checker.ownership_errors()

    assert len(errors) == 4
    assert any("ui/screens/modal.py" in error and "agent_pbx.store" in error for error in errors)
    assert any(
        "ui/controllers/runtime.py" in error
        and "agent_pbx.persistence.repositories" in error
        for error in errors
    )

    controller_cycle = tmp_path / "src/agent_pbx/ui/controllers/cycle.py"
    controller_cycle.write_text("from ...tui import AgentPBXTUI\n", encoding="utf-8")
    cycle_errors = checker.ownership_errors()
    assert any(
        "ui/controllers/cycle.py" in error and "agent_pbx.tui" in error
        for error in cycle_errors
    )
    assert any("api_routes/agents.py" in error and "agent_pbx.store" in error for error in errors)
    assert any(
        "persistence/repositories/agents.py" in error and "agent_pbx.api" in error
        for error in errors
    )


def test_store_schema_and_transaction_inventory_are_versioned() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert contract["schema"] == "agent-pbx.component-decomposition-contract/v1"
    assert (
        contract["baseline_commit"]
        == "56f46179863fed13f339ab3e55481a895b994921"
    )
    assert contract["review_targets"] == _checker().REVIEW_TARGETS
    assert contract["ownership_rules"] == _checker().OWNERSHIP_RULES
    assert contract["store_contract"] == _checker().collect_store_contract()
    assert contract["store_contract"]["schema_version"] == 45
    assert contract["store_contract"]["begin_immediate_methods"]


def test_api_route_method_and_operation_id_inventory_is_versioned() -> None:
    expected = json.loads(API_ROUTE_INVENTORY.read_text(encoding="utf-8"))

    assert expected == _checker().collect_api_route_inventory()
    assert len(expected) == 280
    assert len(
        {
            (item["method"], item["path"], item["operation_id"])
            for item in expected
        }
    ) == len(expected)


def test_tui_widget_action_tab_and_keymap_ownership_is_versioned() -> None:
    expected = json.loads(TUI_COMPAT.read_text(encoding="utf-8"))
    actual = _checker().collect_tui_compatibility()

    assert expected == actual
    assert expected["schema"] == "agent-pbx.tui-compatibility-contract/v1"
    assert expected["widget_owner"] == "AgentPBXTUI.compose"
    assert len(expected["widgets"]) == 242
    assert len(expected["tabs"]) == 11
    assert expected["action_owner"] == "AgentPBXTUI"
    assert len(expected["bound_actions"]) == 32
    assert expected["keymap_owner"] == "AgentPBXTUI.BINDINGS"
    assert len(expected["bindings"]) == 35
    assert expected["terminal_focus_ownership"] == {
        "policy_owner": "AgentPBXTUI.pbx_owns_key_under_terminal_focus",
        "policy_sha256": "c97587d8268fe77a42ccf09d37881002df868451b766576ca49717654a1609fe",
        "priority_gate_owner": "AgentPBXTUI._check_bindings",
        "priority_gate_sha256": "0a5c63c989c44e150a67405cbb663a82afb6862ef17f6c5e729a674a366ff17e",
    }


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
    protected_provider_routes = {
        route.path: route.endpoint.__module__
        for route in app.routes
        if route.path.startswith("/v2/operator/protected-provider/")
    }
    assert protected_provider_routes
    assert set(protected_provider_routes.values()) == {
        "agent_pbx.api_routes.protected_provider"
    }
