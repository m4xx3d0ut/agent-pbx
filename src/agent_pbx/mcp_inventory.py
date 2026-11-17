from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping


MCP_TOOL_MANIFEST_SCHEMA_VERSION = 1
MCP_TOOL_MANIFEST_STORE_KEY = "agent_pbx_mcp_tool_manifest_v1"
MCP_TOOL_MANIFEST_METADATA_KEY = "mcp_tool_manifest_sha256"
MCP_TOOL_MANIFEST_ENV = "AGENT_PBX_MCP_TOOL_MANIFEST_SHA256"


def _json_mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def build_mcp_tool_manifest(tools: Iterable[object]) -> dict[str, Any]:
    """Return a deterministic description and digest of the exposed tools."""

    entries = [
        {
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
            "parameters": _json_mapping(getattr(tool, "parameters", {})),
            "output_schema": _json_mapping(getattr(tool, "output_schema", {})),
        }
        for tool in tools
    ]
    entries.sort(key=lambda entry: entry["name"])
    canonical = {
        "schema_version": MCP_TOOL_MANIFEST_SCHEMA_VERSION,
        "tools": entries,
    }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return {
        **canonical,
        "tool_count": len(entries),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def publish_mcp_tool_manifest(mcp: object, store: object) -> dict[str, Any]:
    """Persist the exact manifest produced by one FastMCP server build."""

    manager = getattr(mcp, "_tool_manager", None)
    if manager is None or not hasattr(manager, "list_tools"):
        raise RuntimeError("FastMCP tool manager is unavailable")
    manifest = build_mcp_tool_manifest(manager.list_tools())
    setter = getattr(store, "set_system_metadata_json", None)
    if not callable(setter):
        raise RuntimeError("Agent PBX store cannot persist MCP tool manifests")
    setter(MCP_TOOL_MANIFEST_STORE_KEY, manifest)
    setattr(mcp, "agent_pbx_tool_manifest", manifest)
    return manifest


def manifest_sha256(value: object) -> str:
    if not isinstance(value, Mapping):
        return ""
    digest = str(value.get("sha256") or "").strip().lower()
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        return ""
    return digest
