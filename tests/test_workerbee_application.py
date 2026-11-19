from __future__ import annotations

from pathlib import Path

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_application import durable_workerbee_profile


class _Switcher:
    def status(self) -> dict[str, object]:
        return {
            "active": {
                "ready": True,
                "backend": "platform_debug",
                "endpoint": "http://127.0.0.1:8765/mcp",
            }
        }


class _Runtime:
    def __init__(self) -> None:
        self.observed: list[tuple[dict[str, object], str]] = []

    def observe_status(self, discover):
        status = discover()
        self.observe(status["active"], reason="selector_status")
        return status

    def observe(
        self,
        payload: dict[str, object],
        *,
        reason: str,
    ) -> None:
        self.observed.append((payload, reason))

    def posture(self) -> dict[str, object]:
        return {"selected": {"backend": "platform_debug"}}


class _Gateway:
    def impact(self) -> dict[str, object]:
        return {"mutation_count": 0}


def test_durable_profile_prefers_explicit_launch_intent() -> None:
    assert durable_workerbee_profile(
        {
            "workerbee_mcp_profile": "observe",
            "workerbee_profile_intent": {"profile": "trusted-workbench"},
        }
    ) == "trusted-workbench"
    assert durable_workerbee_profile(
        {"workerbee_mcp_profile": "local-deploy"}
    ) == "local-deploy"


def test_application_service_resolves_replaceable_app_state_providers(
    tmp_path: Path,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    runtime = _Runtime()
    app.state.workerbee_switcher = _Switcher()
    app.state.workerbee_runtime = runtime
    app.state.workerbee_gateway = _Gateway()

    posture = app.state.workerbee_application.refresh_runtime_selection()

    assert posture == {
        "selected": {"backend": "platform_debug"},
        "selector": {
            "active": {
                "ready": True,
                "backend": "platform_debug",
                "endpoint": "http://127.0.0.1:8765/mcp",
            }
        },
        "gateway_impact": {"mutation_count": 0},
    }
    assert runtime.observed == [
        (
            {
                "ready": True,
                "backend": "platform_debug",
                "endpoint": "http://127.0.0.1:8765/mcp",
            },
            "selector_status",
        )
    ]
