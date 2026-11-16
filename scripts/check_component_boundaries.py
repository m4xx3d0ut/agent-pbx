#!/usr/bin/env python3
"""Check the post-v2.4 decomposition compatibility boundary.

The checked fixtures intentionally describe public behavior and structural
ceilings rather than implementation details.  Updating either fixture is a
reviewed compatibility event; ordinary component extraction must pass without
rewriting them.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
CONTRACT_PATH = ROOT / "tests" / "fixtures" / "component_decomposition_v1.json"
OPENAPI_PATH = ROOT / "tests" / "fixtures" / "openapi_compat_v1.json"
API_ROUTE_INVENTORY_PATH = ROOT / "tests" / "fixtures" / "api_route_inventory_v1.json"
TUI_COMPAT_PATH = ROOT / "tests" / "fixtures" / "tui_compat_v1.json"
ACCEPTED_BASELINE_COMMIT = "56f46179863fed13f339ab3e55481a895b994921"
AST_TARGETS = (
    "src/agent_pbx/api.py",
    "src/agent_pbx/store.py",
    "src/agent_pbx/tui.py",
)
AST_METRICS = ("lines", "bytes", "ast_nodes", "functions", "classes", "imports")
REVIEW_TARGETS = {
    "src/agent_pbx/api.py": {
        "lines": 1100,
        "owner": "FastAPI composition root",
    },
    "src/agent_pbx/store.py": {
        "lines": 12000,
        "owner": "persistence compatibility facade",
    },
    "src/agent_pbx/tui.py": {
        "lines": 33000,
        "owner": "Textual composition root and event router",
    },
}
FORBIDDEN_DEPENDENCIES = {
    "src/agent_pbx/store.py": {
        "agent_pbx.api",
        "agent_pbx.api_routes",
        "agent_pbx.api_services",
        "agent_pbx.tui",
    },
    "src/agent_pbx/tui.py": {
        "agent_pbx.api",
        "agent_pbx.api_routes",
        "agent_pbx.api_services",
        "agent_pbx.store",
    },
}
OWNERSHIP_RULES = {
    "src/agent_pbx/ui/screens": {
        "owner": "typed Textual modal presentation",
        "forbidden_imports": [
            "agent_pbx.api",
            "agent_pbx.api_routes",
            "agent_pbx.api_services",
            "agent_pbx.persistence",
            "agent_pbx.store",
            "fastapi",
            "httpx",
            "sqlite3",
            "subprocess",
        ],
    },
    "src/agent_pbx/ui/controllers": {
        "owner": "TUI orchestration and view-model coordination",
        "forbidden_imports": [
            "agent_pbx.api",
            "agent_pbx.api_routes",
            "agent_pbx.api_services",
            "agent_pbx.persistence.repositories",
            "agent_pbx.store",
            "agent_pbx.tui",
            "fastapi",
            "sqlite3",
        ],
    },
    "src/agent_pbx/api_routes": {
        "owner": "HTTP translation over typed application services",
        "forbidden_imports": [
            "agent_pbx.persistence.repositories",
            "agent_pbx.store",
            "agent_pbx.tui",
            "agent_pbx.ui",
        ],
    },
    "src/agent_pbx/persistence/repositories": {
        "owner": "SQLite domain persistence behind the Store facade",
        "forbidden_imports": [
            "agent_pbx.api",
            "agent_pbx.api_routes",
            "agent_pbx.api_services",
            "agent_pbx.tui",
            "agent_pbx.ui",
            "fastapi",
            "httpx",
            "textual",
        ],
    },
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def collect_api_route_inventory() -> list[dict[str, str]]:
    """Return the public path/method/operation-ID contract in stable order."""

    openapi = collect_openapi()
    inventory: list[dict[str, str]] = []
    for path, path_item in openapi.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            inventory.append(
                {
                    "method": str(method).upper(),
                    "operation_id": str(operation["operationId"]),
                    "path": str(path),
                }
            )
    return sorted(
        inventory,
        key=lambda item: (item["path"], item["method"], item["operation_id"]),
    )


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def collect_ast_inventory() -> dict[str, dict[str, int]]:
    inventory: dict[str, dict[str, int]] = {}
    for relative in AST_TARGETS:
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        inventory[relative] = {
            "lines": len(source.splitlines()),
            "bytes": len(source.encode("utf-8")),
            "ast_nodes": sum(1 for _ in ast.walk(tree)),
            "functions": sum(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                for node in ast.walk(tree)
            ),
            "classes": sum(isinstance(node, ast.ClassDef) for node in ast.walk(tree)),
            "imports": sum(
                isinstance(node, (ast.Import, ast.ImportFrom))
                for node in ast.walk(tree)
            ),
        }
    return inventory


def _absolute_imports(relative: str) -> set[str]:
    tree = _tree(ROOT / relative)
    module_parts = list(Path(relative).with_suffix("").parts)
    try:
        src_index = module_parts.index("src")
    except ValueError:
        package_parts = ["agent_pbx"]
    else:
        module_parts = module_parts[src_index + 1 :]
        package_parts = (
            module_parts
            if module_parts and module_parts[-1] == "__init__"
            else module_parts[:-1]
        )
        if package_parts and package_parts[-1] == "__init__":
            package_parts = package_parts[:-1]
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                retained = max(0, len(package_parts) - node.level + 1)
                base = package_parts[:retained]
                suffix = node.module or ""
                imports.add(".".join([*base, suffix]).rstrip("."))
            elif node.module:
                imports.add(node.module)
    return imports


def _matches_prefix(candidate: str, prefix: str) -> bool:
    return candidate == prefix or candidate.startswith(prefix + ".")


def _python_sources(relative_root: str) -> list[Path]:
    path = ROOT / relative_root
    if path.is_file() and path.suffix == ".py":
        return [path]
    if path.is_dir():
        return sorted(item for item in path.rglob("*.py") if item.is_file())
    return []


def dependency_errors() -> list[str]:
    errors: list[str] = []
    for relative, forbidden in FORBIDDEN_DEPENDENCIES.items():
        imports = _absolute_imports(relative)
        for candidate in sorted(imports):
            if any(
                candidate == prefix or candidate.startswith(prefix + ".")
                for prefix in forbidden
            ):
                errors.append(f"{relative} imports forbidden dependency {candidate}")

    routes_root = ROOT / "src" / "agent_pbx" / "api_routes"
    if routes_root.exists():
        for path in sorted(routes_root.glob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            imports = _absolute_imports(relative)
            for candidate in sorted(imports):
                if candidate in {"agent_pbx.store", "agent_pbx.tui"} or candidate.startswith(
                    ("agent_pbx.store.", "agent_pbx.tui.")
                ):
                    errors.append(
                        f"{relative} bypasses the typed service boundary via {candidate}"
                    )
    errors.extend(ownership_errors())
    return errors


def ownership_errors() -> list[str]:
    errors: list[str] = []
    for relative_root, rule in OWNERSHIP_RULES.items():
        owner = str(rule["owner"])
        forbidden = tuple(str(item) for item in rule["forbidden_imports"])
        for path in _python_sources(relative_root):
            relative = path.relative_to(ROOT).as_posix()
            for candidate in sorted(_absolute_imports(relative)):
                if any(_matches_prefix(candidate, prefix) for prefix in forbidden):
                    errors.append(
                        f"{relative} violates {owner} ownership via {candidate}"
                    )
    return errors


def _static_constants(tree: ast.Module) -> dict[str, Any]:
    constants: dict[str, Any] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                constants[node.targets[0].id] = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                continue
    return constants


def _static_value(node: ast.AST, constants: dict[str, Any]) -> Any:
    if isinstance(node, ast.Name) and node.id in constants:
        return constants[node.id]
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return ast.unparse(node)


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise RuntimeError(f"{name} class not found")


def _class_method(node: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for child in node.body:
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
            return child
    raise RuntimeError(f"{node.name}.{name} method not found")


def _method_sha256(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    normalized = ast.dump(node, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def collect_tui_compatibility() -> dict[str, Any]:
    relative = "src/agent_pbx/tui.py"
    tree = _tree(ROOT / relative)
    constants = _static_constants(tree)
    app = _class_node(tree, "AgentPBXTUI")
    compose = _class_method(app, "compose")
    assignments = [
        node
        for node in app.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BINDINGS" for target in node.targets)
    ]
    if len(assignments) != 1 or not isinstance(assignments[0].value, (ast.List, ast.Tuple)):
        raise RuntimeError("AgentPBXTUI.BINDINGS must be one static list")

    bindings: list[dict[str, Any]] = []
    for item in assignments[0].value.elts:
        if isinstance(item, (ast.List, ast.Tuple)):
            values = [_static_value(value, constants) for value in item.elts]
            if len(values) != 3:
                raise RuntimeError("legacy TUI bindings must contain key, action, description")
            bindings.append(
                {
                    "key": values[0],
                    "action": values[1],
                    "description": values[2],
                    "key_display": None,
                    "priority": False,
                    "show": True,
                }
            )
            continue
        if not isinstance(item, ast.Call) or len(item.args) < 3:
            raise RuntimeError("AgentPBXTUI.BINDINGS contains a non-static binding")
        values = [_static_value(value, constants) for value in item.args]
        keywords = {
            keyword.arg: _static_value(keyword.value, constants)
            for keyword in item.keywords
            if keyword.arg
        }
        bindings.append(
            {
                "key": values[0],
                "action": values[1],
                "description": values[2],
                "key_display": keywords.get("key_display"),
                "priority": bool(keywords.get("priority", False)),
                "show": bool(keywords.get("show", True)),
            }
        )

    calls = sorted(
        (node for node in ast.walk(compose) if isinstance(node, ast.Call)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    widgets: list[str] = []
    tabs: list[dict[str, str]] = []
    for call in calls:
        call_name = ast.unparse(call.func)
        keywords = {
            keyword.arg: _static_value(keyword.value, constants)
            for keyword in call.keywords
            if keyword.arg
        }
        widget_id = keywords.get("id")
        if isinstance(widget_id, str):
            widgets.append(f"{widget_id}:{call_name}")
        if call_name.endswith("TabPane"):
            if not isinstance(widget_id, str) or not call.args:
                raise RuntimeError("TUI tab panes require a static label and id")
            label = _static_value(call.args[0], constants)
            if not isinstance(label, str):
                raise RuntimeError("TUI tab pane label must be static")
            tabs.append({"id": widget_id, "label": label})

    action_methods = sorted(
        child.name.removeprefix("action_")
        for child in app.body
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        and child.name.startswith("action_")
    )
    bound_actions = sorted({str(binding["action"]).split("(", 1)[0] for binding in bindings})
    missing_actions = sorted(set(bound_actions) - set(action_methods))
    if missing_actions:
        raise RuntimeError(f"bound TUI actions have no AgentPBXTUI owner: {missing_actions}")

    return {
        "schema": "agent-pbx.tui-compatibility-contract/v1",
        "source": relative,
        "app_class": "AgentPBXTUI",
        "widget_owner": "AgentPBXTUI.compose",
        "widgets": widgets,
        "tab_owner": "AgentPBXTUI.compose",
        "tabs": tabs,
        "keymap_owner": "AgentPBXTUI.BINDINGS",
        "bindings": bindings,
        "action_owner": "AgentPBXTUI",
        "bound_actions": bound_actions,
        "terminal_focus_ownership": {
            "policy_owner": "AgentPBXTUI.pbx_owns_key_under_terminal_focus",
            "policy_sha256": _method_sha256(
                _class_method(app, "pbx_owns_key_under_terminal_focus")
            ),
            "priority_gate_owner": "AgentPBXTUI._check_bindings",
            "priority_gate_sha256": _method_sha256(
                _class_method(app, "_check_bindings")
            ),
        },
    }


def _store_transaction_methods() -> list[str]:
    tree = _tree(ROOT / "src" / "agent_pbx" / "store.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Store":
            methods: list[str] = []
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(
                    isinstance(item, ast.Call)
                    and isinstance(item.func, ast.Name)
                    and item.func.id == "begin_immediate"
                    for item in ast.walk(child)
                ):
                    methods.append(child.name)
            return sorted(methods)
    raise RuntimeError("Store class not found")


def _store_method_contract() -> list[dict[str, Any]]:
    """Return the declared Store surface without importing implementation details.

    The compatibility facade intentionally retains private helpers while the
    persistence implementation is decomposed.  Freezing every declaration
    catches accidental signature, decorator, or async-boundary drift during
    that extraction, including helpers that are consumed by tests or adjacent
    services even though they are not a documented public API.
    """

    tree = _tree(ROOT / "src" / "agent_pbx" / "store.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Store":
            methods: list[dict[str, Any]] = []
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                methods.append(
                    {
                        "name": child.name,
                        "signature": ast.unparse(child.args),
                        "decorators": [
                            ast.unparse(decorator)
                            for decorator in child.decorator_list
                        ],
                        "async": isinstance(child, ast.AsyncFunctionDef),
                    }
                )
            return methods
    raise RuntimeError("Store class not found")


def _store_connection_contract(store: Any) -> dict[str, Any]:
    with store.connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        return {
            "row_factory": (
                f"{conn.row_factory.__module__}.{conn.row_factory.__qualname__}"
                if conn.row_factory is not None
                else None
            ),
            "journal_mode": str(journal_mode).lower(),
            "foreign_keys": int(foreign_keys),
            "isolation_level": conn.isolation_level,
            "in_transaction_on_open": conn.in_transaction,
        }


def collect_store_contract() -> dict[str, Any]:
    sys.path.insert(0, str(SRC))
    from agent_pbx.persistence.transactions import ATOMIC_STORE_OPERATIONS
    from agent_pbx.store import SCHEMA_VERSION, Store

    observed_atomic_operations = _store_transaction_methods()
    atomic_operation_manifest = sorted(ATOMIC_STORE_OPERATIONS)
    if observed_atomic_operations != atomic_operation_manifest:
        raise RuntimeError(
            "Store atomic transaction calls do not match the reviewed manifest: "
            f"observed={observed_atomic_operations!r} "
            f"manifest={atomic_operation_manifest!r}"
        )

    with tempfile.TemporaryDirectory(prefix="agent-pbx-component-schema-") as raw:
        path = Path(raw) / "pbx.sqlite"
        store = Store(path)
        store.init()
        connection = _store_connection_contract(store)
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL
                ORDER BY type, name
                """
            ).fetchall()
        objects = [
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "table": str(row[2]),
                "sql": " ".join(str(row[3]).split()),
            }
            for row in rows
        ]
    return {
        "schema_version": SCHEMA_VERSION,
        "object_count": len(objects),
        "object_names": [f"{item['type']}:{item['name']}" for item in objects],
        "schema_sha256": hashlib.sha256(canonical_json(objects)).hexdigest(),
        "begin_immediate_methods": atomic_operation_manifest,
        "connection": connection,
        "method_declarations": _store_method_contract(),
    }


def collect_openapi() -> dict[str, Any]:
    sys.path.insert(0, str(SRC))
    from agent_pbx.api import create_app
    from agent_pbx.config import ServerConfig

    with tempfile.TemporaryDirectory(prefix="agent-pbx-component-openapi-") as raw:
        app = create_app(ServerConfig(db_path=Path(raw) / "pbx.sqlite"))
        return app.openapi()


def write_fixtures() -> None:
    contract = {
        "schema": "agent-pbx.component-decomposition-contract/v1",
        "baseline_commit": ACCEPTED_BASELINE_COMMIT,
        "ast_maxima": collect_ast_inventory(),
        "review_targets": REVIEW_TARGETS,
        "ownership_rules": OWNERSHIP_RULES,
        "store_contract": collect_store_contract(),
    }
    CONTRACT_PATH.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    OPENAPI_PATH.write_text(
        json.dumps(collect_openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    TUI_COMPAT_PATH.write_text(
        json.dumps(collect_tui_compatibility(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def check() -> list[str]:
    errors = dependency_errors()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("schema") != "agent-pbx.component-decomposition-contract/v1":
        errors.append("component decomposition contract schema changed")
    if contract.get("baseline_commit") != ACCEPTED_BASELINE_COMMIT:
        errors.append("component decomposition baseline provenance changed")
    if contract.get("review_targets") != REVIEW_TARGETS:
        errors.append("component decomposition review targets changed")
    if contract.get("ownership_rules") != OWNERSHIP_RULES:
        errors.append("component ownership rules changed")

    current_inventory = collect_ast_inventory()
    for relative, maxima in contract["ast_maxima"].items():
        for metric in AST_METRICS:
            current = int(current_inventory[relative][metric])
            maximum = int(maxima[metric])
            if current > maximum:
                errors.append(
                    f"{relative} {metric} grew from ceiling {maximum} to {current}"
                )
    for relative, target in REVIEW_TARGETS.items():
        maximum = int(contract["ast_maxima"][relative]["lines"])
        if int(target["lines"]) >= maximum:
            errors.append(
                f"{relative} review target {target['lines']} must reduce ceiling {maximum}"
            )

    current_store = collect_store_contract()
    if current_store != contract["store_contract"]:
        errors.append("Store schema or transaction inventory drifted")

    expected_openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    current_openapi = collect_openapi()
    if canonical_json(current_openapi) != canonical_json(expected_openapi):
        errors.append("public OpenAPI contract drifted")
    expected_routes = json.loads(
        API_ROUTE_INVENTORY_PATH.read_text(encoding="utf-8")
    )
    current_routes = collect_api_route_inventory()
    if canonical_json(current_routes) != canonical_json(expected_routes):
        errors.append("public API route inventory drifted")
    expected_tui = json.loads(TUI_COMPAT_PATH.read_text(encoding="utf-8"))
    current_tui = collect_tui_compatibility()
    if canonical_json(current_tui) != canonical_json(expected_tui):
        errors.append("TUI widget, action, tab, or keymap ownership drifted")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write-fixtures",
        action="store_true",
        help="Write the reviewed baseline fixtures instead of checking them.",
    )
    args = parser.parse_args(argv)
    if args.write_fixtures:
        write_fixtures()
        return 0
    errors = check()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("Component decomposition boundaries passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
