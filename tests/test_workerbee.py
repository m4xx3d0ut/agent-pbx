from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from agent_pbx.workerbee import WorkerBeeStatusService, find_project_card


def make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_workerbee_status_reports_missing_configuration(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AGENT_PBX_WORKERBEE_BIN", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    service = WorkerBeeStatusService()

    status = service.status_for_agent(
        {"agent_id": "agent-1", "metadata": {"cwd": str(repo)}}
    )

    assert status["configured"] is False
    assert status["available"] is False
    assert status["error"]["code"] == "WORKERBEE_NOT_CONFIGURED"


def test_workerbee_status_reports_missing_agent_cwd(tmp_path: Path) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    service = WorkerBeeStatusService(workerbee_bin=workerbee)

    status = service.status_for_agent({"agent_id": "agent-1", "metadata": {}})

    assert status["configured"] is True
    assert status["available"] is False
    assert status["error"]["code"] == "AGENT_CWD_MISSING"


def test_workerbee_status_parses_project_and_dashboard(tmp_path: Path) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[list[str]] = []

    def runner(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "projects":
            payload: dict[str, Any] = {
                "global_dashboard": {
                    "dashboard_url": "https://dashboard.local/",
                    "running": True,
                },
                "projects": [
                    {
                        "project": "demo-dev-123",
                        "cwd_hint": str(repo),
                        "status_kind": "stopped",
                        "exposed_route_summary": "none",
                    }
                ],
            }
        else:
            payload = {
                "project": "demo-dev-123",
                "mode": "lazy",
                "dashboard_url": None,
                "project_status": {
                    "running": False,
                    "state_dir": "/tmp/workerbee/demo",
                    "app_status": {
                        "state": "no_workload_deployed",
                        "ready": False,
                        "message": "no app workload deployed yet",
                        "declared_workload_count": 0,
                        "ready_workload_count": 0,
                        "degraded_workload_count": 0,
                        "orphaned_workload_count": 0,
                    },
                    "latest_deployment": None,
                },
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    service = WorkerBeeStatusService(workerbee_bin=workerbee, runner=runner)

    status = service.status_for_agent(
        {"agent_id": "agent-1", "metadata": {"cwd": str(repo)}}
    )

    assert status["available"] is True
    assert status["project"] == "demo-dev-123"
    assert status["mode"] == "lazy"
    assert status["running"] is False
    assert status["description"] == "no app workload deployed yet"
    assert status["project_card"]["status_kind"] == "stopped"
    assert status["global_dashboard"]["running"] is True
    assert len(calls) == 2


def test_workerbee_status_caches_results(tmp_path: Path) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = 0

    def runner(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        payload = {
            "project": "demo",
            "project_status": {"running": False, "app_status": {}},
        }
        if argv[-1] == "projects":
            payload = {"projects": [], "global_dashboard": {}}
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    service = WorkerBeeStatusService(workerbee_bin=workerbee, runner=runner)
    agent = {"agent_id": "agent-1", "metadata": {"cwd": str(repo)}}

    service.status_for_agent(agent)
    service.status_for_agent(agent)

    assert calls == 2


def test_workerbee_status_reports_invalid_json(tmp_path: Path) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    repo = tmp_path / "repo"
    repo.mkdir()

    def runner(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, "not json", "")

    service = WorkerBeeStatusService(workerbee_bin=workerbee, runner=runner)

    status = service.status_for_agent(
        {"agent_id": "agent-1", "metadata": {"cwd": str(repo)}}
    )

    assert status["available"] is False
    assert status["error"]["code"] == "WORKERBEE_INVALID_JSON"


def test_find_project_card_matches_project_then_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    payload = {
        "projects": [
            {"project": "other", "cwd_hint": "/tmp/other"},
            {"project": "demo", "cwd_hint": str(repo)},
        ]
    }

    assert find_project_card(payload, project="demo", cwd="/unused")["project"] == "demo"
    assert find_project_card(payload, project="", cwd=str(repo))["project"] == "demo"
