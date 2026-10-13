from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_switcher import WorkerBeeSwitchService


def status_payload(backend: str) -> str:
    endpoint = "http://127.0.0.1:8765/mcp"
    return json.dumps(
        {
            "config": {
                "api_version": "workerbee.mcp_switch_config/v1",
                "path": "/private/wb-mcp-switch.env",
            },
            "status": {
                "api_version": "workerbee.mcp_switch_status/v1",
                "backend": backend,
                "mcp_endpoint": endpoint,
                "mcp": {
                    "status": "ready",
                    "server_info": {"name": "K1S WorkerBee", "version": "1.30.0"},
                },
            },
        }
    )


class FakeSwitcher:
    def __init__(self, backend: str = "oss") -> None:
        self.backend = backend
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if command == ["status-test"]:
            return subprocess.CompletedProcess(command, 0, status_payload(self.backend), "")
        if command[0] != "switch-test":
            return subprocess.CompletedProcess(command, 2, "", "unexpected executable")
        target = "platform_debug" if command[1] == "switch-debug" else "oss"
        if "--dry-run" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                f"[dry-run] would switch {self.backend} to {target}\n",
                "",
            )
        self.backend = target
        return subprocess.CompletedProcess(command, 0, json.dumps({"backend": target}), "")


def service(
    tmp_path: Path,
    switcher: FakeSwitcher,
    *,
    capability_ready: bool = True,
    invalidated: list[str] | None = None,
    impact: dict[str, object] | None = None,
    selections: list[dict[str, object]] | None = None,
) -> WorkerBeeSwitchService:
    invalidation_sink = invalidated if invalidated is not None else []
    selection_sink = selections if selections is not None else []
    return WorkerBeeSwitchService(
        state_root=tmp_path / "switch-state",
        switch_bin="switch-test",
        status_bin="status-test",
        runner=switcher,
        capability_probe=lambda endpoint: {
            "ready": capability_ready,
            "code": "WORKERBEE_READY" if capability_ready else "WORKERBEE_FAILED",
            "endpoint": endpoint,
        },
        cache_invalidator=lambda: invalidation_sink.append("cleared"),
        impact_provider=lambda: dict(impact or {}),
        selection_recorder=lambda provenance, **kwargs: selection_sink.append(
            {"provenance": dict(provenance), **kwargs}
        ),
    )


def test_workerbee_switch_preview_apply_and_rollback(tmp_path: Path) -> None:
    switcher = FakeSwitcher()
    invalidated: list[str] = []
    selections: list[dict[str, object]] = []
    manager = service(
        tmp_path,
        switcher,
        invalidated=invalidated,
        selections=selections,
    )

    preview = manager.preview("platform_debug")
    receipt = (
        tmp_path
        / "switch-state"
        / "transactions"
        / f"{preview['transaction_id']}.json"
    )

    assert preview["phase"] == "previewed"
    assert preview["before"]["backend"] == "oss"
    assert preview["requires_privileged_confirmation"] is True
    assert preview["plan_summary"] == ["[dry-run] would switch oss to platform_debug"]
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600
    assert switcher.backend == "oss"

    applied = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )

    assert applied["phase"] == "verified"
    assert applied["after"]["backend"] == "platform_debug"
    assert applied["capability"]["ready"] is True
    assert "preview_token" not in applied
    assert switcher.backend == "platform_debug"
    assert invalidated == ["cleared"]
    assert selections[-1]["reason"] == "switch"
    assert selections[-1]["force_generation"] is True
    assert selections[-1]["provenance"]["backend"] == "platform_debug"

    rolled_back = manager.rollback(preview["transaction_id"], confirm=True)
    assert rolled_back["phase"] == "rolled_back"
    assert rolled_back["rollback"]["after"]["backend"] == "oss"
    assert switcher.backend == "oss"
    assert invalidated == ["cleared", "cleared"]
    assert selections[-1]["reason"] == "rollback"
    assert selections[-1]["provenance"]["backend"] == "oss"


def test_workerbee_switch_requires_matching_fresh_privileged_preview(tmp_path: Path) -> None:
    switcher = FakeSwitcher()
    manager = service(tmp_path, switcher)
    preview = manager.preview("platform_debug")

    with pytest.raises(ValueError, match="privileged"):
        manager.apply(
            preview["transaction_id"],
            preview_token=preview["preview_token"],
            confirm_target="platform_debug",
        )
    with pytest.raises(ValueError, match="token"):
        manager.apply(
            preview["transaction_id"],
            preview_token="wrong-token-value",
            confirm_target="platform_debug",
            confirm_privileged=True,
        )

    switcher.backend = "platform_debug"
    with pytest.raises(ValueError, match="changed after preview"):
        manager.apply(
            preview["transaction_id"],
            preview_token=preview["preview_token"],
            confirm_target="platform_debug",
            confirm_privileged=True,
        )


def test_workerbee_switch_failed_verification_automatically_rolls_back(
    tmp_path: Path,
) -> None:
    switcher = FakeSwitcher()
    manager = service(tmp_path, switcher, capability_ready=False)
    preview = manager.preview("platform_debug")

    result = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )

    assert result["phase"] == "failed_rollback_failed"
    # The fake capability probe intentionally rejects both the requested and
    # restored endpoint. Backend identity still returns to the receipt state.
    assert result["automatic_rollback"]["after"]["backend"] == "oss"
    assert switcher.backend == "oss"


def test_workerbee_switch_command_failure_records_unchanged_backend(
    tmp_path: Path,
) -> None:
    switcher = FakeSwitcher()
    manager = service(tmp_path, switcher)
    preview = manager.preview("platform_debug")
    original = switcher.__call__

    def fail_apply(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["switch-test", "switch-debug"] and "--dry-run" not in command:
            return subprocess.CompletedProcess(command, 1, "", "apply refused")
        return original(command, **kwargs)

    manager.runner = fail_apply
    result = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )

    assert result["phase"] == "apply_failed_unchanged"
    assert result["automatic_rollback"]["unchanged"] is True
    assert switcher.backend == "oss"


def test_workerbee_switch_status_requires_generation_guard(tmp_path: Path) -> None:
    manager = service(tmp_path, FakeSwitcher("platform_debug"))

    result = manager.status()

    assert result["available"] is True
    assert result["active"]["backend"] == "platform_debug"
    assert result["existing_sessions_pinned"] is False
    assert result["generation_guard_required"] is True
    assert result["targets"] == ["oss", "platform_debug"]
    user_appliance = next(
        item
        for item in result["backend_catalog"]
        if item["backend"] == "user_appliance"
    )
    assert user_appliance["available"] is False
    assert "deferred" in user_appliance["unavailable_reason"].lower()


def test_workerbee_switch_blocks_active_mutations(tmp_path: Path) -> None:
    impact = {"mutation_count": 1, "mutation_leases": ["lease-1"]}
    manager = service(tmp_path, FakeSwitcher(), impact=impact)

    with pytest.raises(ValueError, match="active mutating"):
        manager.preview("platform_debug")

    impact["mutation_count"] = 0
    preview = manager.preview("platform_debug")
    impact["mutation_count"] = 1

    with pytest.raises(ValueError, match="active mutating"):
        manager.apply(
            preview["transaction_id"],
            preview_token=preview["preview_token"],
            confirm_target="platform_debug",
            confirm_privileged=True,
        )


def test_workerbee_switch_api_preserves_preview_apply_boundary(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    calls: list[tuple[str, object]] = []

    class FakeService:
        def status(self):  # type: ignore[no-untyped-def]
            return {"available": True}

        def preview(self, target):  # type: ignore[no-untyped-def]
            calls.append(("preview", target))
            return {"transaction_id": "a" * 24, "preview_token": "p" * 24}

        def apply(self, transaction_id, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(("apply", (transaction_id, kwargs)))
            return {"phase": "verified"}

        def rollback(self, transaction_id, **kwargs):  # type: ignore[no-untyped-def]
            calls.append(("rollback", (transaction_id, kwargs)))
            return {"phase": "rolled_back"}

    app.state.workerbee_switcher = FakeService()
    with TestClient(app) as client:
        preview = client.post(
            "/v1/workerbee/switch/preview", json={"target": "platform_debug"}
        )
        applied = client.post(
            "/v1/workerbee/switch/apply",
            json={
                "transaction_id": "a" * 24,
                "preview_token": "p" * 24,
                "confirm_target": "platform_debug",
                "confirm_privileged": True,
            },
        )
        rolled_back = client.post(
            "/v1/workerbee/switch/rollback",
            json={"transaction_id": "a" * 24, "confirm": True},
        )

    assert preview.status_code == 200
    assert applied.status_code == 200 and applied.json()["phase"] == "verified"
    assert rolled_back.status_code == 200
    assert [name for name, _value in calls] == ["preview", "apply", "rollback"]
