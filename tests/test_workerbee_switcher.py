from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_backend_adapter import WorkerBeeSwitchBackend
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_switcher import WorkerBeeSwitchService


def status_payload(backend: str, posture: str = "loopback") -> str:
    endpoint = "http://127.0.0.1:8765/mcp"
    reported_posture = posture.replace("_", "-")
    return json.dumps(
        {
            "config": {
                "api_version": "workerbee.mcp_switch_config/v1",
                "path": "/private/wb-mcp-switch.env",
            },
            "status": {
                "api_version": "workerbee.mcp_switch_status/v1",
                "backend": backend,
                "configured_posture": {
                    "posture": reported_posture,
                    "explicit": posture == "native_lan",
                },
                "expected_posture": {"posture": reported_posture},
                "observed_posture": {"posture": reported_posture},
                "mcp_endpoint": endpoint,
                "mcp": {
                    "status": "ready",
                    "server_info": {"name": "K1S WorkerBee", "version": "1.30.0"},
                },
            },
        }
    )


class FakeSwitcher:
    def __init__(
        self,
        backend: str = "oss",
        posture: str = "loopback",
        configured_posture: str = "loopback",
        fail_posture_mismatch: bool = False,
    ) -> None:
        self.backend = backend
        self.posture = posture
        self.configured_posture = configured_posture
        self.fail_posture_mismatch = fail_posture_mismatch
        self.fail_apply = False
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(command))
        if command == ["status-test"]:
            return subprocess.CompletedProcess(
                command,
                (
                    3
                    if self.fail_posture_mismatch
                    and self.backend == "platform_debug"
                    and self.posture != self.configured_posture
                    else 0
                ),
                status_payload(self.backend, self.posture),
                "",
            )
        if command[0] != "switch-test":
            return subprocess.CompletedProcess(command, 2, "", "unexpected executable")
        target = (
            "platform_debug"
            if command[1] in {"switch-debug", "switch-debug-lan"}
            else "oss"
        )
        target_posture = self.configured_posture
        if "--dry-run" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                f"[dry-run] would switch {self.backend} to {target}\n",
                "",
            )
        if self.fail_apply:
            return subprocess.CompletedProcess(command, 1, "", "apply refused")
        self.backend = target
        if target == "platform_debug":
            self.posture = target_posture
        return subprocess.CompletedProcess(command, 0, json.dumps({"backend": target}), "")


def service(
    tmp_path: Path,
    switcher: FakeSwitcher,
    *,
    capability_ready: bool = True,
    invalidated: list[str] | None = None,
    impact: dict[str, object] | None = None,
    selections: list[dict[str, object]] | None = None,
    platform_debug_posture: str = "loopback",
) -> WorkerBeeSwitchService:
    invalidation_sink = invalidated if invalidated is not None else []
    selection_sink = selections if selections is not None else []
    return WorkerBeeSwitchService(
        state_root=tmp_path / "switch-state",
        switch_bin="switch-test",
        status_bin="status-test",
        platform_debug_posture=platform_debug_posture,
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
    assert switcher.commands[-1] == [
        "switch-test", "switch-debug", "--dry-run", "--json", "--confirm-platform-debug"
    ]
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
    assert ["switch-test", "switch-debug", "--json", "--confirm-platform-debug"] in switcher.commands
    assert invalidated == ["cleared"]
    assert selections[-1]["reason"] == "switch"
    assert selections[-1]["force_generation"] is True
    assert selections[-1]["provenance"]["backend"] == "platform_debug"

    rolled_back = manager.rollback(preview["transaction_id"], confirm=True)
    assert rolled_back["phase"] == "rolled_back"
    assert rolled_back["rollback"]["after"]["backend"] == "oss"
    assert switcher.backend == "oss"
    assert ["switch-test", "switch-oss", "--json"] in switcher.commands
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


def test_legacy_switch_preview_without_descriptor_still_applies(tmp_path: Path) -> None:
    switcher = FakeSwitcher()
    manager = service(tmp_path, switcher)
    preview = manager.preview("platform_debug")
    transaction_id = preview["transaction_id"]
    path = tmp_path / "switch-state" / "transactions" / f"{transaction_id}.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted.pop("target_descriptor")
    path.write_text(json.dumps(persisted), encoding="utf-8")
    restarted = service(tmp_path, switcher)
    result = restarted.apply(
        transaction_id,
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )
    assert result["phase"] == "verified"
    assert result["after"]["backend"] == "platform_debug"


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
    switcher.fail_apply = True
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
    switcher = FakeSwitcher("platform_debug")
    manager = service(tmp_path, switcher)

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
    catalog = {item["backend"]: item for item in result["backend_catalog"]}
    assert {key: item["source_mode"] for key, item in catalog.items()} == {
        "oss": "native-direct",
        "platform_debug": "snapshot-import",
        "user_appliance": "managed-clone",
    }
    assert catalog["oss"]["qualification"] == "legacy_switcher"
    assert catalog["oss"]["instance_id"] is None
    assert len(catalog["oss"]["configuration_sha256"]) == 64
    assert catalog["oss"]["configuration_sha256"] != catalog["platform_debug"]["configuration_sha256"]
    registry = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    observed = registry.observe(result["active"])
    assert catalog["platform_debug"]["instance_id"] == observed.instance_id
    assert user_appliance["configuration_sha256"] is None
    assert switcher.commands == [["status-test"]]

    with pytest.raises(ValueError, match="qualification is deferred"):
        manager.preview("user-appliance")
    assert switcher.commands == [["status-test"]]


def test_workerbee_switch_discovery_reports_missing_host_commands(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_SWITCHER_BIN", raising=False)
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_SWITCHER_STATUS_BIN", raising=False)

    def unexpected_command(*_args, **_kwargs):
        raise AssertionError("missing tools must not invoke a host command")

    manager = WorkerBeeSwitchService(
        state_root=tmp_path,
        which=lambda _: None,
        runner=unexpected_command,
    )
    report = manager.status()
    assert report["available"] is False
    assert report["targets"] == []
    assert report["active"]["ready"] is False
    with pytest.raises(ValueError, match="wb-mcp-switch is not configured"):
        manager.preview("oss")


class IsolatedApplianceAdapter:
    platform_debug_posture = "loopback"

    def __init__(self) -> None:
        self.backend = "oss"
        self.digest = "b" * 64
        self.calls: list[tuple[str, str]] = []

    def descriptors(self) -> tuple[WorkerBeeSwitchBackend, ...]:
        return (
            WorkerBeeSwitchBackend("oss", "verified", "a" * 64),
            WorkerBeeSwitchBackend("user_appliance", "verified", self.digest),
        )

    def discover(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "endpoint": f"http://127.0.0.1/{self.backend}/mcp",
            "server_name": self.backend,
            "server_version": "fixture",
            "ready": True,
        }

    def preflight(self, target: str) -> str:
        self.calls.append(("preflight", target))
        return f"fixture plan for {target}"

    def activate(self, target: str) -> None:
        self.calls.append(("activate", target))
        self.backend = target

    def verify(self, target: str, **_kwargs) -> dict[str, object]:
        self.calls.append(("verify", target))
        return {
            "verified": self.backend == target,
            "after": self.discover(),
            "capability": {"ready": True},
            "platform_debug_posture": {},
        }

    def restore(self, target: str, **kwargs) -> dict[str, object]:
        self.calls.append(("restore", target))
        self.backend = target
        return self.verify(target, **kwargs)


def test_workerbee_switch_uses_qualified_isolated_adapter_for_appliance(tmp_path: Path) -> None:
    adapter = IsolatedApplianceAdapter()
    manager = WorkerBeeSwitchService(state_root=tmp_path, adapter=adapter)

    preview = manager.preview("user-appliance")
    assert preview["target_descriptor"]["source_mode"] == "managed-clone"
    assert preview["target_descriptor"]["qualification"] == "verified"
    args = {
        "preview_token": preview["preview_token"],
        "confirm_target": "user-appliance",
    }
    adapter.digest = "c" * 64
    with pytest.raises(ValueError, match="target configuration changed"):
        manager.apply(preview["transaction_id"], **args)
    assert adapter.calls == [("preflight", "user_appliance")]
    adapter.digest = "b" * 64
    assert manager.apply(preview["transaction_id"], **args)["phase"] == "verified"
    assert manager.rollback(preview["transaction_id"], confirm=True)["phase"] == "rolled_back"
    assert adapter.calls == [
        ("preflight", "user_appliance"),
        ("activate", "user_appliance"),
        ("verify", "user_appliance"),
        ("restore", "oss"),
        ("verify", "oss"),
    ]


def test_workerbee_appliance_never_qualifies_through_legacy_switcher() -> None:
    with pytest.raises(ValueError, match="supports only OSS and platform debug"):
        WorkerBeeSwitchBackend("user_appliance", "legacy_switcher", "a" * 64)


def test_workerbee_switch_rejects_inconsistent_adapter_verification(tmp_path: Path) -> None:
    class MismatchedAdapter(IsolatedApplianceAdapter):
        def verify(self, target: str, **kwargs) -> dict[str, object]:
            result = super().verify(target, **kwargs)
            if target == "user_appliance":
                result["after"] = {**self.discover(), "backend": "oss"}
            return result

    adapter = MismatchedAdapter()
    selections = []
    manager = WorkerBeeSwitchService(
        state_root=tmp_path,
        adapter=adapter,
        selection_recorder=lambda *args, **kwargs: selections.append((args, kwargs)),
    )
    preview = manager.preview("appliance")
    result = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="appliance",
    )
    assert result["phase"] == "failed_rolled_back"
    assert "mismatched verification evidence" in result["failure"]
    assert result["automatic_rollback"]["after"]["backend"] == "oss"
    assert selections == []


def test_workerbee_native_lan_posture_is_previewed_applied_and_verified(
    tmp_path: Path,
) -> None:
    switcher = FakeSwitcher(configured_posture="native_lan")
    manager = service(
        tmp_path,
        switcher,
        platform_debug_posture="native_lan",
    )

    preview = manager.preview("platform_debug")
    assert preview["target_platform_debug_posture"] == "native_lan"
    assert ["switch-test", "switch-debug", "--dry-run"] == switcher.commands[-1][
        :3
    ]

    applied = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )

    assert applied["phase"] == "verified"
    assert applied["after"]["platform_debug_posture"] == "native_lan"
    assert applied["platform_debug_posture"] == {
        "expected": "native_lan",
        "observed": "native_lan",
        "ready": True,
    }
    assert any(
        len(command) > 1 and command[1] == "switch-debug"
        for command in switcher.commands
    )


def test_workerbee_same_backend_posture_drift_is_not_a_noop(tmp_path: Path) -> None:
    switcher = FakeSwitcher(
        "platform_debug",
        posture="loopback",
        configured_posture="native_lan",
    )
    manager = service(
        tmp_path,
        switcher,
        platform_debug_posture="native_lan",
    )

    status = manager.status()
    assert status["platform_debug_posture"] == {
        "expected": "native_lan",
        "observed": "loopback",
        "ready": False,
    }
    preview = manager.preview("platform_debug")
    applied = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )

    assert applied["phase"] == "verified"
    assert switcher.posture == "native_lan"
    assert any(
        len(command) > 1
        and command[1] == "switch-debug"
        and "--dry-run" not in command
        for command in switcher.commands
    )


def test_workerbee_native_lan_drift_can_repair_failed_doctor_status(
    tmp_path: Path,
) -> None:
    switcher = FakeSwitcher(
        "platform_debug",
        posture="loopback",
        configured_posture="native_lan",
        fail_posture_mismatch=True,
    )
    manager = service(
        tmp_path,
        switcher,
        platform_debug_posture="native_lan",
    )

    preview = manager.preview("platform_debug")
    assert preview["repairs_posture_drift"] is True

    applied = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="platform_debug",
        confirm_privileged=True,
    )

    assert applied["phase"] == "verified"
    assert applied["after"]["ready"] is True
    assert applied["after"]["platform_debug_posture"] == "native_lan"


def test_workerbee_rollback_restores_configured_debug_posture(tmp_path: Path) -> None:
    switcher = FakeSwitcher(
        "platform_debug",
        posture="native_lan",
        configured_posture="native_lan",
    )
    manager = service(
        tmp_path,
        switcher,
        platform_debug_posture="native_lan",
    )

    preview = manager.preview("oss")
    applied = manager.apply(
        preview["transaction_id"],
        preview_token=preview["preview_token"],
        confirm_target="oss",
    )
    assert applied["phase"] == "verified"

    rolled_back = manager.rollback(preview["transaction_id"], confirm=True)

    assert rolled_back["phase"] == "rolled_back"
    assert switcher.backend == "platform_debug"
    assert switcher.posture == "native_lan"
    assert any(
        len(command) > 1
        and command[1] == "switch-debug"
        and "--dry-run" not in command
        for command in switcher.commands
    )


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

        def preview(self, target, *, controller):  # type: ignore[no-untyped-def]
            assert controller == "local"
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
