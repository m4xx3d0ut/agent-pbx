from __future__ import annotations

from typing import Any

from agent_pbx import __version__


DEMO_CONTRACT_VERSION = "agent-pbx.public-demo/v1"
DEMO_SCENARIO_ID = "product-tour-v1"


def public_demo_contract() -> dict[str, Any]:
    """Return the versioned contract consumed by the product-site wrapper."""

    return {
        "api_version": DEMO_CONTRACT_VERSION,
        "scenario": {
            "id": DEMO_SCENARIO_ID,
            "version": 1,
            "agent_pbx_version": __version__,
            "deterministic": True,
        },
        "transport": {
            "kind": "textual-serve",
            "index_path": "/",
            "websocket_path": "/ws",
            "health_path": "/healthz",
            "contract_path": "/demo-contract.json",
            "per_connection_runtime": True,
        },
        "terminal": {
            "minimum_columns": 80,
            "minimum_rows": 24,
            "recommended_columns": 150,
            "recommended_rows": 46,
            "color_system": "truecolor",
            "focus_required": True,
        },
        "navigation": {
            "F1": "Agents",
            "F2": "Events",
            "F3": "Right-side tabs",
            "F5": "Operators",
            "F8": "Next alert",
            "F10": "Launch project workspace",
            "Ctrl+P": "Command palette outside a native terminal",
            "Shift+F1..F12": "Pass F1..F12 to the focused native terminal",
        },
        "seeded_surfaces": [
            "managed Agent and native tmux runtime",
            "Agent and Operator reports",
            "Operator forks and campaign",
            "Codex posture",
            "scoped project files and editor",
            "WorkerBee runtime truth",
            "pull requests and issues",
            "Joplin notes",
        ],
        "policy": {
            "live_credentials": False,
            "live_repositories": False,
            "live_integrations": False,
            "persistent_visitor_state": False,
            "host_tmux_access": False,
            "host_home_access": False,
            "network_egress_required": False,
        },
        "embedding": {
            "iframe_title": "Interactive Agent PBX demo",
            "recommended_sandbox": "allow-scripts allow-same-origin allow-downloads",
            "fullscreen_supported": True,
            "mobile_note": (
                "Use the on-page keyboard controls when a mobile keyboard does not "
                "emit function keys."
            ),
        },
    }
