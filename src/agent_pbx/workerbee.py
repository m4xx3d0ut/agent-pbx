from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable


WORKERBEE_BIN_ENV = "AGENT_PBX_WORKERBEE_BIN"
WORKERBEE_TIMEOUT_ENV = "AGENT_PBX_WORKERBEE_TIMEOUT_SECONDS"
WORKERBEE_CACHE_ENV = "AGENT_PBX_WORKERBEE_CACHE_SECONDS"
DEFAULT_WORKERBEE_TIMEOUT_SECONDS = 20.0
DEFAULT_WORKERBEE_CACHE_SECONDS = 10.0

Runner = Callable[
    [list[str], Path, float],
    subprocess.CompletedProcess[str],
]


class WorkerBeeStatusService:
    def __init__(
        self,
        *,
        workerbee_bin: Path | str | None = None,
        timeout_seconds: float = DEFAULT_WORKERBEE_TIMEOUT_SECONDS,
        cache_seconds: float = DEFAULT_WORKERBEE_CACHE_SECONDS,
        runner: Runner | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.workerbee_bin = Path(workerbee_bin).expanduser() if workerbee_bin else None
        self.timeout_seconds = timeout_seconds
        self.cache_seconds = cache_seconds
        self.runner = runner or run_workerbee_command
        self.clock = clock
        self._cache: dict[tuple[str, str, str, str], tuple[float, dict[str, Any]]] = {}
        self._lock_guard = threading.Lock()
        self._locks: dict[tuple[str, str, str], threading.Lock] = {}

    def status_for_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(agent.get("agent_id") or "")
        agent_project = str(agent.get("project") or "")
        cwd = agent_cwd(agent)
        bin_result = self.resolve_workerbee_bin()
        cache_key = (
            agent_id,
            agent_project,
            cwd or "",
            bin_result.get("workerbee_bin") or "",
        )
        cached = self._cache.get(cache_key)
        now = self.clock()
        if cached is not None and now - cached[0] <= self.cache_seconds:
            return dict(cached[1])

        with self.cache_key_lock(cache_key):
            cached = self._cache.get(cache_key)
            now = self.clock()
            if cached is not None and now - cached[0] <= self.cache_seconds:
                return dict(cached[1])
            status = self._status_for_agent_uncached(
                agent_id,
                agent_project,
                cwd,
                bin_result,
                checked_at=now,
            )
            self._cache[cache_key] = (now, status)
        return dict(status)

    def cache_key_lock(self, cache_key: tuple[str, str, str, str]) -> threading.Lock:
        with self._lock_guard:
            lock = self._locks.get(cache_key)
            if lock is None:
                lock = threading.Lock()
                self._locks[cache_key] = lock
            return lock

    def _status_for_agent_uncached(
        self,
        agent_id: str,
        agent_project: str,
        cwd: str | None,
        bin_result: dict[str, Any],
        *,
        checked_at: float,
    ) -> dict[str, Any]:
        base = {
            "configured": bool(bin_result.get("configured")),
            "available": False,
            "agent_id": agent_id,
            "agent_project": agent_project or None,
            "cwd": cwd,
            "workerbee_bin": bin_result.get("workerbee_bin"),
            "checked_at": checked_at,
            "project": None,
            "mode": None,
            "running": None,
            "status_kind": None,
            "dashboard_url": None,
            "state_dir": None,
            "description": None,
            "error": bin_result.get("error"),
            "project_status": None,
            "project_card": None,
            "global_dashboard": None,
            "dashboard_error": None,
            "app_status": None,
            "latest_deployment": None,
        }
        if base["error"] is not None:
            return base
        workerbee_bin = bin_result.get("workerbee_bin")
        if not workerbee_bin:
            return base
        if not cwd:
            return {
                **base,
                "configured": True,
                "workerbee_bin": workerbee_bin,
                "error": {
                    "code": "AGENT_CWD_MISSING",
                    "message": "agent metadata does not include an absolute cwd",
                    "retryable": True,
                    "remediation": "Register the agent with metadata.cwd set to its project directory.",
                },
            }
        cwd_path = Path(cwd).expanduser()
        if not cwd_path.is_dir():
            return {
                **base,
                "configured": True,
                "workerbee_bin": workerbee_bin,
                "error": {
                    "code": "AGENT_CWD_NOT_FOUND",
                    "message": f"agent cwd does not exist or is not a directory: {cwd}",
                    "retryable": True,
                },
            }

        project_payload = self._run_project_status(Path(workerbee_bin), cwd_path)
        if project_payload.get("error") is not None:
            return {
                **base,
                "configured": True,
                "workerbee_bin": workerbee_bin,
                "error": project_payload["error"],
            }

        data = project_payload["data"]
        projects_payload = self._run_projects(Path(workerbee_bin), cwd_path)
        project_card = None
        global_dashboard = None
        dashboard_error = None
        if projects_payload.get("error") is None:
            projects_data = projects_payload["data"]
            global_dashboard = dict_value(projects_data, "global_dashboard")
            project_card = find_project_card(
                projects_data,
                project=str(data.get("project") or ""),
                cwd=str(cwd_path),
                preferred_project=agent_project,
            )
        else:
            dashboard_error = projects_payload["error"]

        status_project = str(data.get("project") or "")
        card_project = str(value_from(project_card, "project") or "")
        use_project_card = bool(project_card) and (
            bool(agent_project and card_project == agent_project)
            or bool(card_project and card_project != status_project)
        )
        project_status = dict_value(data, "project_status")
        primary_status = project_card if use_project_card else project_status
        fallback_status = project_status if use_project_card else project_card
        app_status = (
            dict_value(primary_status, "app_status")
            or dict_value(fallback_status, "app_status")
            or None
        )
        latest_deployment = (
            dict_value(primary_status, "latest_deployment")
            or dict_value(fallback_status, "latest_deployment")
            or None
        )
        project = value_from(primary_status, "project") or data.get("project")
        mode = (
            value_from(primary_status, "mode")
            or data.get("mode")
            or project_status.get("mode")
            or value_from(fallback_status, "mode")
        )
        running = bool_value(
            value_from(primary_status, "running"),
            project_status.get("running"),
            value_from(fallback_status, "running"),
        )
        dashboard_url = (
            value_from(primary_status, "dashboard_url")
            or value_from(primary_status, "stack_dashboard_url")
            or value_from(primary_status, "profile_dashboard_url")
            or data.get("dashboard_url")
            or value_from(fallback_status, "dashboard_url")
            or value_from(fallback_status, "stack_dashboard_url")
            or value_from(fallback_status, "profile_dashboard_url")
        )
        state_dir = (
            value_from(primary_status, "state_dir")
            or data.get("state_dir")
            or project_status.get("state_dir")
            or value_from(fallback_status, "state_dir")
        )
        return {
            **base,
            "configured": True,
            "available": True,
            "workerbee_bin": workerbee_bin,
            "project": project,
            "mode": mode,
            "running": running,
            "status_kind": value_from(project_card, "status_kind"),
            "dashboard_url": dashboard_url,
            "state_dir": state_dir,
            "description": workerbee_description(app_status, latest_deployment),
            "error": None,
            "project_status": project_status or data,
            "project_card": project_card,
            "global_dashboard": global_dashboard,
            "dashboard_error": dashboard_error,
            "app_status": app_status,
            "latest_deployment": latest_deployment,
        }

    def resolve_workerbee_bin(self) -> dict[str, Any]:
        configured = self.workerbee_bin or env_workerbee_bin()
        if configured is None:
            return {
                "configured": False,
                "workerbee_bin": None,
                "error": {
                    "code": "WORKERBEE_NOT_CONFIGURED",
                    "message": f"{WORKERBEE_BIN_ENV} is not set",
                    "retryable": True,
                    "remediation": f"Start Agent PBX with {WORKERBEE_BIN_ENV}=/path/to/workerbee.",
                },
            }
        path = configured.expanduser()
        if not path.is_file():
            return {
                "configured": True,
                "workerbee_bin": str(path),
                "error": {
                    "code": "WORKERBEE_BIN_NOT_FOUND",
                    "message": f"WorkerBee executable not found: {path}",
                    "retryable": True,
                },
            }
        if not os.access(path, os.X_OK):
            return {
                "configured": True,
                "workerbee_bin": str(path),
                "error": {
                    "code": "WORKERBEE_BIN_NOT_EXECUTABLE",
                    "message": f"WorkerBee path is not executable: {path}",
                    "retryable": True,
                },
            }
        return {"configured": True, "workerbee_bin": str(path), "error": None}

    def _run_project_status(self, workerbee_bin: Path, cwd: Path) -> dict[str, Any]:
        return self._run_json(
            [
                str(workerbee_bin),
                "--json",
                "--cwd",
                str(cwd),
                "project",
                "status",
                "--cwd",
                str(cwd),
            ],
            cwd,
        )

    def _run_projects(self, workerbee_bin: Path, cwd: Path) -> dict[str, Any]:
        return self._run_json(
            [str(workerbee_bin), "--json", "--cwd", str(cwd), "projects"],
            cwd,
        )

    def _run_json(self, argv: list[str], cwd: Path) -> dict[str, Any]:
        try:
            result = self.runner(argv, cwd, self.timeout_seconds)
        except subprocess.TimeoutExpired:
            return {
                "error": {
                    "code": "WORKERBEE_TIMEOUT",
                    "message": f"WorkerBee command timed out after {self.timeout_seconds:g}s",
                    "retryable": True,
                }
            }
        except OSError as exc:
            return {
                "error": {
                    "code": "WORKERBEE_EXEC_FAILED",
                    "message": str(exc),
                    "retryable": True,
                }
            }
        if result.returncode != 0:
            return {
                "error": {
                    "code": "WORKERBEE_COMMAND_FAILED",
                    "message": (result.stderr or result.stdout or "WorkerBee command failed").strip(),
                    "details": {"returncode": result.returncode},
                    "retryable": True,
                }
            }
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            return {
                "error": {
                    "code": "WORKERBEE_INVALID_JSON",
                    "message": f"WorkerBee returned invalid JSON: {exc}",
                    "retryable": True,
                }
            }
        if not isinstance(data, dict):
            return {
                "error": {
                    "code": "WORKERBEE_UNEXPECTED_JSON",
                    "message": "WorkerBee returned JSON that was not an object",
                    "retryable": True,
                }
            }
        return {"data": data}


def run_workerbee_command(
    argv: list[str],
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        timeout=timeout,
        capture_output=True,
        text=True,
        check=False,
    )


def env_workerbee_bin() -> Path | None:
    value = os.getenv(WORKERBEE_BIN_ENV, "").strip()
    return Path(value).expanduser() if value else None


def env_workerbee_timeout_seconds(
    default: float = DEFAULT_WORKERBEE_TIMEOUT_SECONDS,
) -> float:
    return env_float(WORKERBEE_TIMEOUT_ENV, default)


def env_workerbee_cache_seconds(
    default: float = DEFAULT_WORKERBEE_CACHE_SECONDS,
) -> float:
    return env_float(WORKERBEE_CACHE_ENV, default)


def env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def agent_cwd(agent: dict[str, Any]) -> str | None:
    metadata = agent.get("metadata")
    if not isinstance(metadata, dict):
        return None
    cwd = metadata.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    return None


def find_project_card(
    projects_payload: dict[str, Any],
    *,
    project: str,
    cwd: str,
    preferred_project: str = "",
) -> dict[str, Any] | None:
    projects = projects_payload.get("projects")
    if not isinstance(projects, list):
        return None
    cwd_path = str(Path(cwd).expanduser())
    for candidate_project in (preferred_project, project):
        if not candidate_project:
            continue
        for item in projects:
            if not isinstance(item, dict):
                continue
            if str(item.get("project") or "") == candidate_project:
                return item
    for item in projects:
        if not isinstance(item, dict):
            continue
        if str(item.get("cwd_hint") or "") == cwd_path and bool(item.get("running")):
            return item
    for item in projects:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("cwd_hint") or "") == cwd_path
            and bool(item.get("explicit_project"))
        ):
            return item
    for item in projects:
        if not isinstance(item, dict):
            continue
        if str(item.get("cwd_hint") or "") == cwd_path:
            return item
    return None


def workerbee_description(
    app_status: dict[str, Any] | None,
    latest_deployment: dict[str, Any] | None,
) -> str | None:
    if app_status:
        message = app_status.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        state = app_status.get("state")
        if isinstance(state, str) and state.strip():
            return state.strip()
    if latest_deployment:
        deployment_id = latest_deployment.get("id") or latest_deployment.get("deployment_id")
        if deployment_id:
            return f"latest deployment {deployment_id}"
    return None


def dict_value(data: object, key: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def value_from(data: object, key: str) -> object:
    if not isinstance(data, dict):
        return None
    return data.get(key)


def bool_value(*values: object) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
    return None
