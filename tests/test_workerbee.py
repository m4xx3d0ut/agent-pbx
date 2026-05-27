from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import threading
from typing import Any

from agent_pbx.workerbee import (
    DEFAULT_WORKERBEE_TIMEOUT_SECONDS,
    WorkerBeeStatusService,
    env_workerbee_cache_seconds,
    env_workerbee_timeout_seconds,
    find_project_card,
)


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
    assert status["dashboard_error"] is None
    assert len(calls) == 2


def test_workerbee_status_prefers_agent_project_card_for_same_cwd(
    tmp_path: Path,
) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    repo = tmp_path / "repo"
    repo.mkdir()

    def runner(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if argv[-1] == "projects":
            payload: dict[str, Any] = {
                "global_dashboard": {"running": True},
                "projects": [
                    {
                        "project": "agent-pbx",
                        "cwd_hint": str(repo),
                        "explicit_project": True,
                        "running": True,
                        "status_kind": "stack",
                        "stack_running": True,
                        "dashboard_url": "https://k1s.agent-pbx/dashboard",
                        "app_status": {
                            "state": "ready",
                            "ready": True,
                            "message": "ready",
                            "declared_workload_count": 1,
                            "ready_workload_count": 1,
                        },
                        "latest_deployment": {"id": "deploy-1"},
                    },
                    {
                        "project": "agent-pbx-dev-123",
                        "cwd_hint": str(repo),
                        "explicit_project": False,
                        "running": False,
                        "status_kind": "stopped",
                        "app_status": {"state": "no_workload_deployed"},
                    },
                ],
            }
        else:
            payload = {
                "project": "agent-pbx-dev-123",
                "mode": "lazy",
                "project_status": {
                    "running": False,
                    "app_status": {
                        "state": "no_workload_deployed",
                        "ready": False,
                        "message": "no app workload deployed yet",
                    },
                    "latest_deployment": None,
                },
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    service = WorkerBeeStatusService(workerbee_bin=workerbee, runner=runner)
    status = service.status_for_agent(
        {
            "agent_id": "agent-1",
            "project": "agent-pbx",
            "metadata": {"cwd": str(repo)},
        }
    )

    assert status["project"] == "agent-pbx"
    assert status["running"] is True
    assert status["status_kind"] == "stack"
    assert status["dashboard_url"] == "https://k1s.agent-pbx/dashboard"
    assert status["app_status"]["state"] == "ready"
    assert status["latest_deployment"]["id"] == "deploy-1"


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


def test_workerbee_status_coalesces_concurrent_same_agent_requests(
    tmp_path: Path,
) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    repo = tmp_path / "repo"
    repo.mkdir()
    calls = 0
    calls_lock = threading.Lock()
    first_call_entered = threading.Event()
    release_first_call = threading.Event()

    def runner(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_call_entered.set()
            release_first_call.wait(timeout=5)
        if argv[-1] == "projects":
            payload: dict[str, Any] = {
                "projects": [{"project": "demo", "cwd_hint": str(repo)}],
                "global_dashboard": {"running": True},
            }
        else:
            payload = {
                "project": "demo",
                "project_status": {"running": True, "app_status": {}},
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    service = WorkerBeeStatusService(
        workerbee_bin=workerbee,
        runner=runner,
        cache_seconds=60,
    )
    agent = {"agent_id": "agent-1", "metadata": {"cwd": str(repo)}}

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.status_for_agent, agent)
        assert first_call_entered.wait(timeout=5)
        second = executor.submit(service.status_for_agent, agent)
        release_first_call.set()
        first_status = first.result(timeout=5)
        second_status = second.result(timeout=5)

    assert first_status["available"] is True
    assert second_status["available"] is True
    assert calls == 2


def test_workerbee_status_keeps_available_when_dashboard_lookup_fails(
    tmp_path: Path,
) -> None:
    workerbee = make_executable(tmp_path / "workerbee")
    repo = tmp_path / "repo"
    repo.mkdir()

    def runner(
        argv: list[str], cwd: Path, timeout: float
    ) -> subprocess.CompletedProcess[str]:
        if argv[-1] == "projects":
            return subprocess.CompletedProcess(
                argv,
                1,
                "",
                "workerbee: WorkerBee project demo is locked",
            )
        payload = {
            "project": "demo",
            "mode": "lazy",
            "dashboard_url": "https://k1s.demo/dashboard",
            "project_status": {
                "running": True,
                "state_dir": "/tmp/workerbee/demo",
                "app_status": {"message": "control plane running"},
            },
        }
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")

    service = WorkerBeeStatusService(workerbee_bin=workerbee, runner=runner)
    status = service.status_for_agent(
        {"agent_id": "agent-1", "metadata": {"cwd": str(repo)}}
    )

    assert status["available"] is True
    assert status["error"] is None
    assert status["dashboard_error"]["code"] == "WORKERBEE_COMMAND_FAILED"
    assert status["project"] == "demo"
    assert status["running"] is True


def test_workerbee_timeout_and_cache_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_TIMEOUT_SECONDS", "21.5")
    monkeypatch.setenv("AGENT_PBX_WORKERBEE_CACHE_SECONDS", "4.5")

    assert env_workerbee_timeout_seconds() == 21.5
    assert env_workerbee_cache_seconds() == 4.5

    monkeypatch.setenv("AGENT_PBX_WORKERBEE_TIMEOUT_SECONDS", "bad")
    assert env_workerbee_timeout_seconds() == DEFAULT_WORKERBEE_TIMEOUT_SECONDS


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
