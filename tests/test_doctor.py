from __future__ import annotations

import json
from pathlib import Path

from agent_pbx.cli import build_parser
from agent_pbx.doctor import (
    DoctorCheck,
    DoctorReport,
    _database_check,
    _security_check,
    _compatibility_check,
    doctor_json,
    doctor_markdown,
    run_platform_doctor,
)
from agent_pbx.mcp_daemon import MCPDaemonConfig
from agent_pbx.store import SCHEMA_VERSION, Store


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


def test_platform_doctor_reports_missing_host_executables(tmp_path: Path) -> None:
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


def test_doctor_cli_parser_exposes_release_gate_options() -> None:
    args = build_parser().parse_args(
        ["doctor", "--json", "--strict", "--no-service-probes", "--codex-bin", "codex-x"]
    )

    assert args.command == "doctor"
    assert args.json is True
    assert args.strict is True
    assert args.no_service_probes is True
    assert args.codex_bin == "codex-x"
