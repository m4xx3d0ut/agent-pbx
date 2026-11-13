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
AST_TARGETS = (
    "src/agent_pbx/api.py",
    "src/agent_pbx/store.py",
    "src/agent_pbx/tui.py",
)
AST_METRICS = ("lines", "bytes", "ast_nodes", "functions", "classes", "imports")
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


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


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
    package = "agent_pbx"
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                suffix = node.module or ""
                imports.add(f"{package}.{suffix}".rstrip("."))
            elif node.module:
                imports.add(node.module)
    return imports


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
    return errors


def _store_transaction_methods() -> list[str]:
    tree = _tree(ROOT / "src" / "agent_pbx" / "store.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Store":
            methods: list[str] = []
            for child in node.body:
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if any(
                    isinstance(item, ast.Constant)
                    and item.value == "BEGIN IMMEDIATE"
                    for item in ast.walk(child)
                ):
                    methods.append(child.name)
            return sorted(methods)
    raise RuntimeError("Store class not found")


def collect_store_contract() -> dict[str, Any]:
    sys.path.insert(0, str(SRC))
    from agent_pbx.store import SCHEMA_VERSION, Store

    with tempfile.TemporaryDirectory(prefix="agent-pbx-component-schema-") as raw:
        path = Path(raw) / "pbx.sqlite"
        store = Store(path)
        store.init()
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
        "begin_immediate_methods": _store_transaction_methods(),
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
        "baseline_commit": "9d62240f7c4370763d442d65549f7e950111028b",
        "ast_maxima": collect_ast_inventory(),
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


def check() -> list[str]:
    errors = dependency_errors()
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if contract.get("schema") != "agent-pbx.component-decomposition-contract/v1":
        errors.append("component decomposition contract schema changed")

    current_inventory = collect_ast_inventory()
    for relative, maxima in contract["ast_maxima"].items():
        for metric in AST_METRICS:
            current = int(current_inventory[relative][metric])
            maximum = int(maxima[metric])
            if current > maximum:
                errors.append(
                    f"{relative} {metric} grew from ceiling {maximum} to {current}"
                )

    current_store = collect_store_contract()
    if current_store != contract["store_contract"]:
        errors.append("Store schema or transaction inventory drifted")

    expected_openapi = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    current_openapi = collect_openapi()
    if canonical_json(current_openapi) != canonical_json(expected_openapi):
        errors.append("public OpenAPI contract drifted")
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
