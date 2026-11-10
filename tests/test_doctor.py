from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from agent_pbx.cli import build_parser
from agent_pbx.doctor import (
    DoctorCheck,
    DoctorReport,
    _database_check,
    _launcher_permission_check,
    _managed_tmux_command_check,
    _operator_broker_policy_check,
    _codex_theme_check,
    _security_check,
    _tmux_server_diagnostic,
    _compatibility_check,
    _workerbee_runtime_binding_check,
    _workerbee_tool_surface_check,
    _workerbee_tool_surface_runtime_check,
    _workerbee_trusted_direct_bind_check,
    _workerbee_controlled_run_check,
    _workerbee_workspace_policy_check,
    doctor_json,
    doctor_markdown,
    run_platform_doctor,
)
from agent_pbx.mcp_daemon import MCPDaemonConfig
from agent_pbx.store import SCHEMA_VERSION, Store
from agent_pbx.workerbee_mcp import WorkerBeeBackendProvenance, WorkerBeeMcpPreflight
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_policy import WorkerBeeWorkspacePolicyService


def test_doctor_report_formats_machine_and_human_output() -> None:
    report = DoctorReport(
        checks=(
            DoctorCheck("ready", "pass", "available"),
            DoctorCheck("optional", "warn", "not configured", remediation="Configure it."),
        ),
        platform="Linux",
        architecture="x86_64",
        python_version="3.10.12",
        agent_pbx_version="2.0.0",
    )

    payload = json.loads(doctor_json(report))
    rendered = doctor_markdown(report)

    assert payload["api_version"] == "agent-pbx.doctor/v2"
    assert payload["ok"] is True
    assert payload["warning_count"] == 1
    assert "[PASS] ready: available" in rendered
    assert "[WARN] optional: not configured" in rendered


def test_doctor_database_check_handles_new_current_and_legacy_state(tmp_path: Path) -> None:
    db_path = tmp_path / "pbx.sqlite"
    missing = _database_check(db_path)
    Store(db_path).init()
    current = _database_check(db_path)

    assert missing.status == "warn"
    assert current.status == "pass"
    assert f"schema {SCHEMA_VERSION}" in current.summary


def test_doctor_reports_effective_workerbee_workspace_policy(tmp_path: Path) -> None:
    config = MCPDaemonConfig(state_root=tmp_path)
    store = Store(config.resolved_db_path)
    store.init()
    root = tmp_path / "git"
    root.mkdir()
    WorkerBeeWorkspacePolicyService(store, environ={}).set_policy(
        agent_id=None,
        policy="scoped-workbench",
        request_mode="automatic",
        allowed_roots=[str(root)],
        managed_scratch_root=str(root / ".scratch"),
        profile_label="Rosie Workbench",
        updated_by="test",
    )

    check = _workerbee_workspace_policy_check(config)

    assert check.status == "pass"
    assert "Rosie Workbench" in check.summary
    assert "allowed_roots=1" in check.detail


def test_doctor_reports_trusted_direct_bind_disabled_incomplete_and_ready(
    tmp_path: Path,
) -> None:
    disabled = _workerbee_trusted_direct_bind_check(
        MCPDaemonConfig(state_root=tmp_path / "disabled")
    )
    incomplete = _workerbee_trusted_direct_bind_check(
        MCPDaemonConfig(
            state_root=tmp_path / "incomplete",
            workerbee_trusted_direct_helper_bin=tmp_path / "helper",
        )
    )
    ready = _workerbee_trusted_direct_bind_check(
        MCPDaemonConfig(
            state_root=tmp_path / "ready",
            workerbee_trusted_direct_helper_bin=tmp_path / "helper",
            workerbee_trusted_direct_receipt_root=tmp_path / "receipts",
            workerbee_trusted_direct_approved_roots=(tmp_path / "git",),
            workerbee_trusted_direct_administrator_ceiling=True,
        )
    )

    assert disabled.status == "pass"
    assert "disabled by default" in disabled.summary
    assert incomplete.status == "warn"
    assert "remains disabled" in incomplete.summary
    assert ready.status == "pass"
    assert "host admission is configured" in ready.summary
    assert "approved_roots=1" in ready.detail


def test_doctor_reports_safe_controlled_run_default_and_missing_root_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_root = tmp_path / "profiles"
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_CONTROLLED_RUN_PROFILE_ROOT", str(profile_root))
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MODE", raising=False)
    guided = _workerbee_controlled_run_check()
    assert guided.status == "pass"
    assert "guided" in guided.summary

    monkeypatch.setenv("AGENT_PBX_WORKERBEE_CONTROLLED_RUN_MODE", "root_operator")
    root = _workerbee_controlled_run_check()
    assert root.status == "warn"
    assert "no enabled private profiles" in root.summary


def test_doctor_security_rejects_unprotected_lan_and_warns_on_override(
    tmp_path: Path,
) -> None:
    blocked = _security_check(MCPDaemonConfig(state_root=tmp_path, host="0.0.0.0"))
    overridden = _security_check(
        MCPDaemonConfig(
            state_root=tmp_path,
            host="0.0.0.0",
            allow_insecure_lan=True,
        )
    )
    secure = _security_check(
        MCPDaemonConfig(
            state_root=tmp_path,
            host="0.0.0.0",
            token="secret",
            tls_certfile=tmp_path / "cert.pem",
            tls_keyfile=tmp_path / "key.pem",
        )
    )

    assert blocked.status == "fail"
    assert overridden.status == "warn"
    assert secure.status == "pass"


def test_doctor_reports_v2_compatibility_posture(tmp_path: Path) -> None:
    retained = _compatibility_check(MCPDaemonConfig(state_root=tmp_path))
    disabled = _compatibility_check(
        MCPDaemonConfig(state_root=tmp_path, legacy_polling_enabled=False)
    )

    assert retained.status == "pass"
    assert "polling" in retained.summary
    assert disabled.status == "warn"
    assert "polling" in disabled.remediation


def test_platform_doctor_reports_missing_host_executables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_PBX_TMUX_BIN", raising=False)
    report = run_platform_doctor(
        MCPDaemonConfig(state_root=tmp_path),
        codex_command="missing-codex",
        probe_services=False,
        which=lambda _name: None,
    )
    by_id = {item.check_id: item for item in report.checks}

    assert by_id["git"].status == "fail"
    assert by_id["ssh"].status == "fail"
    assert by_id["tmux"].status == "fail"
    assert by_id["codex"].status == "fail"
    assert by_id["database"].status == "warn"
    assert report.ok is False


def test_doctor_warns_on_shared_or_world_accessible_local_launcher(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "apbx"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o777)

    unsafe = _launcher_permission_check(launcher)
    launcher.chmod(0o700)
    safe = _launcher_permission_check(launcher)

    assert unsafe.status == "warn"
    assert "chmod 700" in unsafe.remediation
    assert safe.status == "pass"


def test_doctor_reports_root_operator_broker_policy(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_OPERATOR_BROKER_MODE", "root_operator")
    monkeypatch.setenv("AGENT_PBX_OPERATOR_BROKER_CONTROL_MODE", "hidden")

    check = _operator_broker_policy_check()

    assert check.status == "pass"
    assert "mode=root_operator" in check.summary
    assert "controls=hidden" in check.summary


def test_doctor_cli_parser_exposes_release_gate_options() -> None:
    args = build_parser().parse_args(
        ["doctor", "--json", "--strict", "--no-service-probes", "--codex-bin", "codex-x"]
    )

    assert args.command == "doctor"
    assert args.json is True
    assert args.strict is True
    assert args.no_service_probes is True
    assert args.codex_bin == "codex-x"

    theme = build_parser().parse_args(
        ["codex", "theme", "install", "--codex-home", "/tmp/codex", "--force"]
    )
    assert theme.command == "codex"
    assert theme.codex_command == "theme"
    assert theme.codex_theme_command == "install"
    assert theme.codex_home == Path("/tmp/codex")
    assert theme.force is True


def test_doctor_reports_managed_codex_theme_posture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / ".codex"))
    missing = _codex_theme_check()
    assert missing.status == "warn"
    assert "theme install" in missing.remediation

    from agent_pbx.codex_theme import install_managed_codex_theme

    install_managed_codex_theme(codex_home=tmp_path / ".codex")
    ready = _codex_theme_check()
    assert ready.status == "pass"
    assert "installed and selected" in ready.summary


def test_tmux_server_diagnostic_reports_server_binary_and_plugin_contamination(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "display-message" in command:
            return subprocess.CompletedProcess(command, 0, "3.2a\t4242\n", "")
        return subprocess.CompletedProcess(
            command,
            0,
            "@continuum-restore on\n@plugin tmux-plugins/tmux-resurrect\n",
            "",
        )

    monkeypatch.setattr("agent_pbx.doctor.subprocess.run", run)
    monkeypatch.setattr("agent_pbx.doctor.os.readlink", lambda _path: "/usr/bin/tmux")
    monkeypatch.setattr("agent_pbx.doctor.sys.platform", "linux")

    result = _tmux_server_diagnostic(
        "/opt/tmux-3.7c",
        Path("/tmp/tmux.sock"),
        2.0,
    )

    assert result is not None
    assert result["version"] == "3.2a"
    assert result["executable"] == "/usr/bin/tmux"
    assert result["plugin_contaminated"] is True
    assert calls[0][:3] == ["/opt/tmux-3.7c", "-S", "/tmp/tmux.sock"]


def test_doctor_reports_managed_tmux_command_state(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "tmux-3.7c"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    target.chmod(0o755)
    monkeypatch.setenv("AGENT_PBX_TMUX_BIN", str(target))

    missing = _managed_tmux_command_check(tmp_path / "state", lambda _: str(target))
    from agent_pbx.tmux_binary import ensure_managed_tmux_shim

    ensure_managed_tmux_shim(state_root=tmp_path / "state")
    ready = _managed_tmux_command_check(tmp_path / "state", lambda _: str(target))

    assert missing.status == "warn"
    assert "Restart the Agent PBX daemon" in missing.remediation
    assert ready.status == "pass"


def test_doctor_reports_platform_debug_runtime_generation_and_helper(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path / "state")
    helper = tmp_path / "agent-pbx-workerbee-workspace"
    stage_python = tmp_path / "python"
    stage_script = tmp_path / "stage.py"
    for executable in (helper, stage_python):
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
    stage_script.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_WORKSPACE_HELPER_BIN", str(helper))
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_DEBUG_STAGE_PYTHON", str(stage_python))
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_DEBUG_STAGE_SCRIPT", str(stage_script))
    provenance = WorkerBeeBackendProvenance(
        code="WORKERBEE_SWITCHER_READY",
        configured=True,
        ready=True,
        backend="platform_debug",
        endpoint="http://127.0.0.1:8765/mcp",
        server_name="K1S WorkerBee Debug",
        server_version="1.30.0",
    )
    monkeypatch.setattr(
        "agent_pbx.doctor.inspect_workerbee_switcher",
        lambda **_kwargs: provenance,
    )
    WorkerBeeRuntimeRegistry(
        config.resolved_db_path.parent / "workerbee-runtime"
    ).observe(provenance.public_dict())

    result = _workerbee_runtime_binding_check(config, lambda _name: None, 2.0)

    assert result.status == "pass"
    assert result.summary == "platform_debug generation 1 ready"
    assert f"helper={helper}" in result.detail


def test_doctor_warns_when_debug_materializer_is_incomplete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = MCPDaemonConfig(state_root=tmp_path / "state")
    provenance = WorkerBeeBackendProvenance(
        code="WORKERBEE_SWITCHER_READY",
        configured=True,
        ready=True,
        backend="platform_debug",
        endpoint="http://127.0.0.1:8765/mcp",
    )
    monkeypatch.setattr(
        "agent_pbx.doctor.inspect_workerbee_switcher",
        lambda **_kwargs: provenance,
    )
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_WORKSPACE_HELPER_BIN", raising=False)
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_DEBUG_STAGE_PYTHON", raising=False)
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_DEBUG_STAGE_SCRIPT", raising=False)
    WorkerBeeRuntimeRegistry(
        config.resolved_db_path.parent / "workerbee-runtime"
    ).observe(provenance.public_dict())

    result = _workerbee_runtime_binding_check(config, lambda _name: None, 2.0)

    assert result.status == "warn"
    assert "materializer prerequisites" in result.summary
    assert "AGENT_PBX_WORKERBEE_WORKSPACE_HELPER_BIN" in result.detail


def test_doctor_reports_quarantined_workerbee_catalog_with_remediation() -> None:
    from agent_pbx.workerbee_modes import tools_for_access_profile

    requested = tools_for_access_profile("local-deploy")
    result = _workerbee_tool_surface_check(
        profile="local-deploy",
        backend="oss",
        advertised_tools=[*requested, "workerbee_v1_unreviewed_extension"],
    )

    assert result.status == "warn"
    assert "quarantined" in result.summary
    assert "workerbee_v1_unreviewed_extension" in result.detail
    assert "backend-specific optional capability registry" in result.remediation


def test_doctor_reports_missing_k1s_profile_evidence_separately() -> None:
    from agent_pbx.workerbee_modes import (
        WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE,
        tools_for_access_profile,
    )

    advertised = [
        tool
        for tool in tools_for_access_profile("local-deploy")
        if tool != WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE
    ]
    result = _workerbee_tool_surface_check(
        profile="local-deploy",
        backend="oss",
        advertised_tools=advertised,
    )

    assert result.status == "warn"
    assert "missing required profile tools" in result.summary
    assert WORKERBEE_TOOL_K1S_PROFILE_EVIDENCE in result.detail
    assert "install a WorkerBee build" in result.remediation


def test_doctor_reachable_runtime_catalog_check_uses_selected_backend(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_pbx.workerbee_modes import tools_for_access_profile

    config = MCPDaemonConfig(state_root=tmp_path / "state")
    provenance = WorkerBeeBackendProvenance(
        code="WORKERBEE_SWITCHER_READY",
        configured=True,
        ready=True,
        backend="oss",
        endpoint="http://127.0.0.1:8765/mcp",
        server_name="WorkerBee OSS",
        server_version="2.3.0rc2",
    )
    WorkerBeeRuntimeRegistry(
        config.resolved_db_path.parent / "workerbee-runtime"
    ).observe(provenance.public_dict())

    async def inspect(_endpoint, **_kwargs):
        return WorkerBeeMcpPreflight(
            code="WORKERBEE_INVENTORY_READY",
            ready=True,
            message="ready",
            remediation="",
            advertised_tools=(
                *tools_for_access_profile("local-deploy"),
                "workerbee_v1_unreviewed_extension",
            ),
        )

    monkeypatch.setattr("agent_pbx.doctor.inspect_workerbee_mcp", inspect)
    result = _workerbee_tool_surface_runtime_check(config, 2.0)

    assert result.check_id == "workerbee-tool-surface"
    assert result.status == "warn"
    assert "quarantined" in result.summary
    assert "workerbee_v1_unreviewed_extension" in result.detail
