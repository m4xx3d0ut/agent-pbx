from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .paths import default_state_root


UAT_MANIFEST_FORMAT = "agent-pbx-operator-kb-flow-uat-v1"
DEFAULT_TMUX_SESSION_CANDIDATES = ("agent-pbx", "agent-pbx-operators")


def uat_manifest_dir(state_root: Path | None = None) -> Path:
    return (state_root or default_state_root()) / "uat"


def uat_manifest_path(run_id: str, state_root: Path | None = None) -> Path:
    safe_run = str(run_id or "").strip()
    if not safe_run:
        raise ValueError("run id is required")
    if "/" in safe_run or "\\" in safe_run:
        raise ValueError("run id must not contain path separators")
    return uat_manifest_dir(state_root) / f"{safe_run}.json"


def read_uat_manifest(run_id: str, state_root: Path | None = None) -> dict[str, Any]:
    path = uat_manifest_path(run_id, state_root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"UAT manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"UAT manifest is invalid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"UAT manifest is not an object: {path}")
    if payload.get("format") != UAT_MANIFEST_FORMAT:
        raise ValueError(f"UAT manifest has unsupported format: {path}")
    return payload


def write_uat_manifest(payload: dict[str, Any], state_root: Path | None = None) -> Path:
    run_id = str(payload.get("run") or "").strip()
    path = uat_manifest_path(run_id, state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def list_tmux_sessions(tmux_bin: str = "tmux") -> list[str]:
    result = subprocess.run(
        [tmux_bin, "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_tmux_session(
    requested: str | None,
    *,
    tmux_bin: str = "tmux",
) -> tuple[str, list[str], list[str]]:
    env_session = (
        os.getenv("AGENT_PBX_TUI_OPERATOR_TMUX_SESSION", "").strip()
        or os.getenv("AGENT_PBX_OPERATOR_TMUX_SESSION", "").strip()
    )
    normalized = str(requested or "").strip()
    if normalized and normalized.lower() != "auto":
        return normalized, list_tmux_sessions(tmux_bin), []
    sessions = list_tmux_sessions(tmux_bin)
    candidates = [
        item
        for item in (env_session, *DEFAULT_TMUX_SESSION_CANDIDATES)
        if item
    ]
    for candidate in candidates:
        if candidate in sessions:
            return candidate, sessions, []
    agent_pbx_sessions = [
        session for session in sessions if session.startswith("agent-pbx")
    ]
    if agent_pbx_sessions:
        return agent_pbx_sessions[0], sessions, [
            "auto tmux session used first agent-pbx* session"
        ]
    fallback = env_session or DEFAULT_TMUX_SESSION_CANDIDATES[0]
    return fallback, sessions, ["auto tmux session found no existing Agent PBX session"]


def json_text_contains_private_markers(payload: Any) -> bool:
    text = json.dumps(payload, sort_keys=True).lower()
    markers = [
        "test_placeholder_do_not_use",
        "api_key",
        "password",
        "secret",
        "token",
        "ssn",
        "social security",
    ]
    return any(marker in text for marker in markers)


def cleanup_tmux_pane(tmux_bin: str, tmux_pane_id: str) -> dict[str, Any]:
    killed = subprocess.run(
        [tmux_bin, "kill-pane", "-t", tmux_pane_id],
        check=False,
        capture_output=True,
        text=True,
    )
    stderr = killed.stderr.strip()
    if killed.returncode == 0:
        return {
            "kind": "tmux_pane_killed",
            "tmux_pane_id": tmux_pane_id,
            "returncode": 0,
            "stderr": stderr,
        }
    if "can't find pane" in stderr.lower():
        return {
            "kind": "tmux_pane_already_absent",
            "tmux_pane_id": tmux_pane_id,
            "returncode": 0,
            "raw_returncode": killed.returncode,
            "stderr": stderr,
        }
    return {
        "kind": "tmux_pane_kill_failed",
        "tmux_pane_id": tmux_pane_id,
        "returncode": killed.returncode,
        "stderr": stderr,
    }


def cleanup_operator_kb_flow_uat(
    *,
    server: str | None,
    token: str | None,
    run: str,
    state_root: Path | None = None,
    timeout: float = 20.0,
    tmux_bin: str = "tmux",
) -> dict[str, Any]:
    manifest = read_uat_manifest(run, state_root)
    resolved_server = str(server or manifest.get("base") or "").strip()
    if not resolved_server:
        raise ValueError("server is required when manifest has no base URL")
    client = UATClient(server=resolved_server, token=token, timeout=timeout)
    sim_a = str(manifest.get("cleanup_operator_agent_id") or manifest.get("sim_a") or "")
    cleanup: list[dict[str, Any]] = []
    for kb_id in list(manifest.get("active_kb_ids") or []):
        try:
            payload = client.request(
                "POST",
                f"/v1/operator/kb/{OperatorKbFlowUAT.quote(str(kb_id))}/retire",
                {
                    "operator_agent_id": sim_a,
                    "summary": f"Retry cleanup for UAT KB entry {run}",
                    "metadata": {"uat_cleanup_retry": True, "uat_run": run},
                },
            )
            cleanup.append(
                {"kind": "kb_retired", "kb_id": kb_id, "status": payload.get("status")}
            )
        except Exception as exc:
            cleanup.append({"kind": "kb_retire_failed", "kb_id": kb_id, "error": str(exc)})
    for kb_id in list(manifest.get("secret_kb_ids") or []):
        try:
            payload = client.request(
                "POST",
                f"/v1/operator/kb/{OperatorKbFlowUAT.quote(str(kb_id))}/reject",
                {
                    "operator_agent_id": sim_a,
                    "summary": f"Retry cleanup for UAT redaction guard entry {run}",
                    "metadata": {"uat_cleanup_retry": True, "uat_run": run},
                },
            )
            cleanup.append(
                {"kind": "kb_rejected", "kb_id": kb_id, "status": payload.get("status")}
            )
        except Exception as exc:
            cleanup.append({"kind": "kb_reject_failed", "kb_id": kb_id, "error": str(exc)})
    seen: set[str] = set()
    for agent_id in list(manifest.get("synthetic_agents") or []):
        agent_id = str(agent_id or "").strip()
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        try:
            payload = client.request("DELETE", f"/v1/agents/{OperatorKbFlowUAT.quote(agent_id)}")
            cleanup.append(
                {
                    "kind": "agent_dismissed",
                    "agent_id": agent_id,
                    "dismissed_at": payload.get("dismissed_at"),
                }
            )
        except Exception as exc:
            cleanup.append(
                {"kind": "agent_dismiss_failed", "agent_id": agent_id, "error": str(exc)}
            )
    tmux_pane_id = str(manifest.get("tmux_pane_id") or "").strip()
    if tmux_pane_id:
        cleanup.append(cleanup_tmux_pane(tmux_bin, tmux_pane_id))
    failed_count = sum(
        1
        for item in cleanup
        if str(item.get("kind", "")).endswith("_failed")
        or int(item.get("returncode") or 0) != 0
    )
    result = {
        "run": run,
        "base": client.server,
        "manifest_path": str(uat_manifest_path(run, state_root)),
        "cleanup": cleanup,
        "failed_count": failed_count,
    }
    manifest["last_cleanup_retry"] = result
    manifest["status"] = "cleanup_retry_complete"
    manifest["updated_at"] = time.time()
    write_uat_manifest(manifest, state_root)
    return result


class UATClient:
    def __init__(self, *, server: str, token: str | None, timeout: float = 20.0) -> None:
        self.server = server.rstrip("/")
        self.token = token or ""
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        query: dict[str, Any] | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> Any:
        url = self.server + path
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url += "?" + urllib.parse.urlencode(filtered, doseq=True)
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("utf-8")
                payload = json.loads(text) if text else None
                if response.status not in expect:
                    raise AssertionError(
                        f"{method} {path} expected {expect}, got {response.status}: {payload}"
                    )
                return payload
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = text
            if exc.code in expect:
                return {"http_status": exc.code, "payload": payload}
            raise AssertionError(
                f"{method} {path} expected {expect}, got {exc.code}: {payload}"
            ) from exc


class OperatorKbFlowUAT:
    def __init__(
        self,
        *,
        server: str,
        token: str | None,
        project: str = "agent-pbx-kb-sim",
        tmux_sink: bool = False,
        tmux_session: str | None = None,
        controlled_cwd: Path | None = None,
        cleanup: bool = True,
        timeout: float = 20.0,
        state_root: Path | None = None,
        tmux_bin: str = "tmux",
    ) -> None:
        self.client = UATClient(server=server, token=token, timeout=timeout)
        self.project = project
        self.tmux_sink = tmux_sink
        self.tmux_bin = tmux_bin
        resolved_session, sessions, warnings = resolve_tmux_session(
            tmux_session,
            tmux_bin=tmux_bin,
        )
        self.tmux_session = resolved_session
        self.tmux_sessions = sessions
        self.tmux_resolution_warnings = warnings
        self.controlled_cwd = str((controlled_cwd or Path.cwd()).resolve())
        self.cleanup_enabled = cleanup
        self.state_root = state_root
        suffix = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
        self.tag = f"kb-sim-{suffix}"
        self.sim_a = f"operator-kb-sim-a-{suffix}"
        self.sim_b = f"operator-kb-sim-b-{suffix}"
        self.sim_fork = f"operator-kb-sim-a-fork-{suffix}"
        self.sim_tmux = f"operator-kb-sim-tmux-{suffix}"
        self.created_agents = [self.sim_a, self.sim_b, self.sim_fork]
        self.active_kb_ids: list[str] = []
        self.secret_kb_ids: list[str] = []
        self.created_kb_ids: list[str] = []
        self.handoff_ids: list[str] = []
        self.tmux_pane_id: str | None = None
        self.results: list[dict[str, Any]] = []
        self.failures: list[str] = []
        self.cleanup: list[dict[str, Any]] = []
        self.unexpected_error: str | None = None
        self.before_reports: dict[str, str | None] = {}
        self.non_sim_report_changes: list[dict[str, str | None]] = []
        self.started_at = time.time()
        self.manifest_path = uat_manifest_path(self.tag, state_root)

    def run(self) -> dict[str, Any]:
        self.write_manifest("starting")
        try:
            self.stage_0_preflight()
            self.before_reports = self.latest_reports_snapshot()
            self.stage_1_register()
            kb_id = self.stage_2_compile_and_promote()
            self.stage_3_context_and_record_only(kb_id)
            self.stage_4_guards_and_reindex()
            if self.tmux_sink:
                self.stage_5_tmux_sink()
            else:
                self.check(
                    "5",
                    "tmux sink stage skipped",
                    True,
                    {"tmux_sink": False},
                )
        except BaseException as exc:  # noqa: BLE001 - UAT output must include failures.
            self.unexpected_error = "".join(
                traceback.format_exception_only(type(exc), exc)
            ).strip()
            self.check("runtime", "unexpected exception", False, self.unexpected_error)
        finally:
            after_reports = self.latest_reports_snapshot()
            self.non_sim_report_changes = self.changed_reports(
                self.before_reports,
                after_reports,
            )
            if self.cleanup_enabled:
                self.try_cleanup()
                cleanup_failed_count = self.cleanup_failure_count()
                if cleanup_failed_count:
                    self.check(
                        "cleanup",
                        "cleanup completed without failures",
                        False,
                        {
                            "failed_count": cleanup_failed_count,
                            "cleanup": self.cleanup,
                        },
                    )
            self.write_manifest("complete" if not self.failures else "failed")
        return {
            "run": self.tag,
            "base": self.client.server,
            "manifest_path": str(self.manifest_path),
            "tmux_session": self.tmux_session,
            "project": self.project,
            "synthetic_agents": list(self.created_agents),
            "kb_ids": self.created_kb_ids,
            "handoff_ids": self.handoff_ids,
            "results": self.results,
            "failures": self.failures,
            "unexpected_error": self.unexpected_error,
            "non_sim_report_changes": self.non_sim_report_changes[:20],
            "non_sim_report_change_count": len(self.non_sim_report_changes),
            "cleanup": self.cleanup,
        }

    def check(self, stage: str, name: str, ok: bool, evidence: Any = None) -> None:
        self.results.append(
            {"stage": stage, "name": name, "ok": bool(ok), "evidence": evidence}
        )
        if not ok:
            self.failures.append(f"{stage}: {name}")
        self.write_manifest("running")

    def manifest_payload(self, status: str) -> dict[str, Any]:
        return {
            "format": UAT_MANIFEST_FORMAT,
            "run": self.tag,
            "status": status,
            "base": self.client.server,
            "project": self.project,
            "cleanup_enabled": self.cleanup_enabled,
            "cleanup_operator_agent_id": self.sim_a,
            "sim_a": self.sim_a,
            "sim_b": self.sim_b,
            "sim_fork": self.sim_fork,
            "sim_tmux": self.sim_tmux,
            "synthetic_agents": list(self.created_agents),
            "active_kb_ids": self.active_kb_ids,
            "secret_kb_ids": self.secret_kb_ids,
            "created_kb_ids": self.created_kb_ids,
            "handoff_ids": self.handoff_ids,
            "tmux_sink": self.tmux_sink,
            "tmux_session": self.tmux_session,
            "tmux_pane_id": self.tmux_pane_id,
            "controlled_cwd": self.controlled_cwd,
            "failures": self.failures,
            "unexpected_error": self.unexpected_error,
            "cleanup": self.cleanup,
            "created_at": self.started_at,
            "updated_at": time.time(),
        }

    def write_manifest(self, status: str) -> None:
        write_uat_manifest(self.manifest_payload(status), self.state_root)

    def stage_0_preflight(self) -> None:
        health = self.client.request("GET", "/healthz")
        self.check(
            "0",
            "server health endpoint is reachable",
            bool(health.get("ok")) if isinstance(health, dict) else False,
            {"server": self.client.server, "health": health},
        )
        if not (isinstance(health, dict) and health.get("ok")):
            raise AssertionError("server health endpoint is not ok")
        cwd_path = Path(self.controlled_cwd)
        self.check(
            "0",
            "controlled cwd exists",
            cwd_path.is_dir(),
            {"controlled_cwd": self.controlled_cwd},
        )
        if not cwd_path.is_dir():
            raise AssertionError(f"controlled cwd does not exist: {self.controlled_cwd}")
        if self.tmux_resolution_warnings:
            self.check(
                "0",
                "tmux auto session resolved with warning",
                True,
                {
                    "tmux_session": self.tmux_session,
                    "available_sessions": self.tmux_sessions,
                    "warnings": self.tmux_resolution_warnings,
                },
            )
        if not self.tmux_sink:
            self.check(
                "0",
                "tmux sink preflight skipped",
                True,
                {"tmux_sink": False},
            )
            return
        tmux_bin = shutil.which(self.tmux_bin)
        self.check(
            "0",
            "tmux binary is available",
            bool(tmux_bin),
            {"tmux_bin": self.tmux_bin, "resolved": tmux_bin},
        )
        if not tmux_bin:
            raise AssertionError("tmux binary is unavailable")
        sessions = list_tmux_sessions(self.tmux_bin)
        self.tmux_sessions = sessions
        self.check(
            "0",
            "tmux target session exists",
            self.tmux_session in sessions,
            {"tmux_session": self.tmux_session, "available_sessions": sessions},
        )
        if self.tmux_session not in sessions:
            raise AssertionError(f"tmux session not found: {self.tmux_session}")

    def stage_1_register(self) -> None:
        for agent_id, metadata in [
            (
                self.sim_a,
                {
                    "pbx_mode": "report",
                    "cwd": f"/tmp/agent-pbx-kb-sim/{self.tag}/{self.sim_a}",
                    "operator_role": "root",
                    "uat_run": self.tag,
                    "suppress_tui_alerts": True,
                },
            ),
            (
                self.sim_b,
                {
                    "pbx_mode": "report",
                    "cwd": f"/tmp/agent-pbx-kb-sim/{self.tag}/{self.sim_b}",
                    "operator_role": "root",
                    "uat_run": self.tag,
                    "suppress_tui_alerts": True,
                },
            ),
            (
                self.sim_fork,
                {
                    "pbx_mode": "report",
                    "cwd": f"/tmp/agent-pbx-kb-sim/{self.tag}/{self.sim_fork}",
                    "operator_role": "fork",
                    "logical_operator_id": self.sim_a,
                    "fork_purpose": "review",
                    "access_mode": "review_readonly",
                    "uat_run": self.tag,
                    "suppress_tui_alerts": True,
                },
            ),
        ]:
            payload = self.client.request(
                "POST",
                "/v1/agents/register",
                {
                    "agent_id": agent_id,
                    "project": "agent-pbx-operator",
                    "agent_type": "operator",
                    "metadata": metadata,
                },
            )
            self.check(
                "1",
                f"registered {agent_id}",
                payload.get("agent_type") == "operator",
                {
                    "project": payload.get("project"),
                    "metadata": {
                        key: payload.get("metadata", {}).get(key)
                        for key in (
                            "operator_role",
                            "logical_operator_id",
                            "pbx_mode",
                        )
                    },
                },
            )

    def stage_2_compile_and_promote(self) -> str:
        report = self.client.request(
            "POST",
            f"/v1/agents/{self.quote(self.sim_a)}/reports",
            {
                "project": "agent-pbx-operator",
                "status": "working",
                "summary": f"UAT KB candidate report {self.tag}",
                "detail": "Synthetic report for operator KB compile validation.",
                "metadata": {
                    "operator_kb_candidates": [
                        {
                            "scope": "project",
                            "project": self.project,
                            "title": f"Durable handoff routing guidance {self.tag}",
                            "summary": (
                                "Use project-scoped KB context when target operators "
                                "have different repo roots."
                            ),
                            "body": (
                                f"For UAT run {self.tag}, target operators working "
                                "from temp dirs should request KB context by project "
                                "and tags. Only pass repo_root when the handoff depends "
                                "on path-specific behavior."
                            ),
                            "tags": ["kb-sim", self.tag, "handoff", "routing"],
                        }
                    ],
                    "uat_run": self.tag,
                    "suppress_tui_alerts": True,
                },
            },
        )
        report_id = str(report["report_id"])
        proposed = self.client.request(
            "GET",
            "/v1/operator/kb",
            query={
                "operator_agent_id": self.sim_a,
                "status": "proposed",
                "query": self.tag,
                "project": self.project,
                "limit": 10,
            },
        )["kb_entries"]
        matching = [entry for entry in proposed if self.tag in str(entry.get("title") or "")]
        kb_entry = matching[0] if matching else None
        self.check(
            "2",
            "auto-compiled proposed KB entry from report metadata",
            kb_entry is not None,
            {
                "report_id": report_id,
                "proposed_count": len(proposed),
                "matched_kb_id": kb_entry.get("kb_id") if kb_entry else None,
            },
        )
        if kb_entry is None:
            raise AssertionError("No matching proposed KB entry found")
        kb_id = str(kb_entry["kb_id"])
        self.created_kb_ids.append(kb_id)
        self.check(
            "2",
            "compiled KB retains report provenance",
            kb_entry.get("metadata", {}).get("compiled_from_report_id") == report_id
            and kb_entry.get("sources", [{}])[0].get("source_type") == "report",
            {
                "metadata": kb_entry.get("metadata"),
                "sources": kb_entry.get("sources"),
            },
        )
        duplicate = self.client.request(
            "POST",
            "/v1/operator/kb/compile-report",
            {"operator_agent_id": self.sim_a, "report_id": report_id},
        )
        self.check(
            "2",
            "manual compile retries as duplicate skip",
            duplicate.get("proposed_count") == 0
            and duplicate.get("skipped", [{}])[0].get("reason")
            == "duplicate_content_hash",
            duplicate,
        )
        promoted = self.client.request(
            "POST",
            f"/v1/operator/kb/{self.quote(kb_id)}/promote",
            {"operator_agent_id": self.sim_a, "metadata": {"uat_run": self.tag}},
        )
        self.active_kb_ids.append(kb_id)
        self.check(
            "2",
            "root operator promoted clean KB entry",
            promoted.get("status") == "active"
            and promoted.get("redaction_status") == "clean",
            {
                "kb_id": kb_id,
                "status": promoted.get("status"),
                "redaction_status": promoted.get("redaction_status"),
            },
        )
        return kb_id

    def stage_3_context_and_record_only(self, kb_id: str) -> None:
        context = self.client.request(
            "POST",
            "/v1/operator/kb/context",
            {
                "operator_agent_id": self.sim_b,
                "query": "project-scoped KB context repo roots",
                "project": self.project,
                "tags": ["kb-sim"],
                "limit": 5,
            },
        )
        context_ids = [entry.get("kb_id") for entry in context.get("kb_entries", [])]
        self.check(
            "3",
            "target operator resolves active KB context across repo roots",
            context.get("satisfied_by_kb") is True
            and kb_id in context_ids
            and context.get("repo_root") is None,
            {
                "match_count": context.get("match_count"),
                "context_ids": context_ids,
                "repo_root": context.get("repo_root"),
            },
        )
        narrowed = self.client.request(
            "POST",
            "/v1/operator/kb/context",
            {
                "operator_agent_id": self.sim_b,
                "query": "project-scoped KB context repo roots",
                "project": self.project,
                "repo_root": f"/tmp/nonmatching/{self.tag}",
                "tags": ["kb-sim"],
                "limit": 5,
            },
        )
        self.check(
            "3",
            "explicit repo_root narrows context lookup",
            narrowed.get("satisfied_by_kb") is False,
            {
                "match_count": narrowed.get("match_count"),
                "repo_root": narrowed.get("repo_root"),
            },
        )
        handoff = self.client.request(
            "POST",
            "/v1/operator/handoffs",
            {
                "operator_agent_id": self.sim_a,
                "source_agent_id": self.sim_a,
                "target_operator_agent_id": self.sim_b,
                "objective": "Validate KB context handoff attachment without tmux delivery.",
                "message": (
                    "Please acknowledge the UAT handoff and confirm the attached "
                    "KB context is visible."
                ),
                "allowed_mutation_scope": "read-only UAT; do not mutate source repos",
                "required_artifacts": [{"name": "uat-summary", "required": True}],
                "needs_ack": True,
                "summary": f"UAT record-only handoff {self.tag}",
                "metadata": {
                    "kb_query": "project-scoped KB context repo roots",
                    "kb_project": self.project,
                    "kb_tags": ["kb-sim"],
                    "kb_limit": 5,
                    "uat_run": self.tag,
                },
            },
        )
        handoff_id = str(handoff["handoff_id"])
        self.handoff_ids.append(handoff_id)
        preflight = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/preflight",
            {"operator_agent_id": self.sim_a, "delivery": "record_only"},
        )
        self.check(
            "3",
            "record-only preflight is ready and non-delivering",
            preflight.get("ok") is True
            and preflight.get("resolved_delivery") == "record_only"
            and preflight.get("pane") is None,
            {
                "status": preflight.get("status"),
                "resolved_delivery": preflight.get("resolved_delivery"),
                "pane": preflight.get("pane"),
            },
        )
        handoff_context = handoff.get("metadata", {}).get("kb_context", {})
        handoff_context_ids = [
            entry.get("kb_id") for entry in handoff_context.get("kb_entries", [])
        ]
        self.check(
            "3",
            "handoff creation attaches KB context metadata",
            handoff.get("metadata", {}).get("kb_context_satisfied") is True
            and kb_id in handoff_context_ids,
            {"handoff_id": handoff_id, "context_ids": handoff_context_ids},
        )
        approved = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/approve",
            {
                "operator_agent_id": self.sim_a,
                "delivery": "record_only",
                "metadata": {"uat_run": self.tag},
            },
        )
        evidence = approved.get("handoff", {}).get("delivery_evidence", {})
        self.check(
            "3",
            "record-only approval does not create command or tmux delivery",
            approved.get("command") is None
            and approved.get("handoff", {}).get("delivery_status") == "recorded"
            and evidence.get("text_pasted") is False,
            {
                "handoff_status": approved.get("handoff", {}).get("status"),
                "delivery_status": approved.get("handoff", {}).get("delivery_status"),
                "text_pasted": evidence.get("text_pasted"),
            },
        )
        acked = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/ack",
            {
                "operator_agent_id": self.sim_b,
                "status": "acknowledged",
                "summary": "UAT receiver acknowledged KB context.",
                "detail": "Synthetic ack for record-only validation.",
                "metadata": {"uat_run": self.tag},
            },
        )
        running = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/status",
            {
                "operator_agent_id": self.sim_b,
                "status": "running",
                "summary": "UAT receiver started simulated validation.",
                "metadata": {"uat_run": self.tag},
            },
        )
        completed = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/status",
            {
                "operator_agent_id": self.sim_b,
                "status": "complete",
                "summary": "UAT receiver completed simulated validation.",
                "artifact_bundle": [{"name": "uat-result", "status": "pass"}],
                "metadata": {"uat_run": self.tag},
            },
        )
        self.check(
            "3",
            "receiver ack/running/complete transitions work",
            acked.get("status") == "acknowledged"
            and running.get("status") == "running"
            and completed.get("status") == "complete"
            and completed.get("completed_at") is not None,
            {
                "ack_status": acked.get("status"),
                "running_status": running.get("status"),
                "complete_status": completed.get("status"),
            },
        )

    def stage_4_guards_and_reindex(self) -> None:
        kb_id = self.created_kb_ids[0]
        unauthorized_promote = self.client.request(
            "POST",
            f"/v1/operator/kb/{self.quote(kb_id)}/promote",
            {"operator_agent_id": self.sim_b},
            expect=(400, 403, 409, 422),
        )
        self.check(
            "4",
            "non-owner root cannot promote another operator's KB",
            unauthorized_promote.get("http_status") in {400, 403, 409, 422},
            unauthorized_promote,
        )
        secret_report = self.client.request(
            "POST",
            f"/v1/agents/{self.quote(self.sim_fork)}/reports",
            {
                "project": "agent-pbx-operator",
                "status": "working",
                "summary": f"UAT secret guard candidate {self.tag}",
                "detail": "Synthetic review fork report for redaction validation.",
                "metadata": {
                    "kb_candidates": [
                        {
                            "scope": "project",
                            "project": self.project,
                            "title": f"Redaction guard candidate {self.tag}",
                            "summary": (
                                "Synthetic candidate that must remain unpromoted until redacted."
                            ),
                            "body": (
                                "Synthetic placeholder for scanner validation: "
                                "api_key = TEST_PLACEHOLDER_DO_NOT_USE"
                            ),
                            "tags": ["kb-sim", self.tag, "redaction"],
                        }
                    ],
                    "uat_run": self.tag,
                    "suppress_tui_alerts": True,
                },
            },
        )
        secret_entries = self.client.request(
            "GET",
            "/v1/operator/kb",
            query={
                "operator_agent_id": self.sim_a,
                "status": "proposed",
                "query": "Redaction guard",
                "project": self.project,
                "limit": 10,
            },
        )["kb_entries"]
        secret_matches = [
            entry for entry in secret_entries if self.tag in str(entry.get("title") or "")
        ]
        secret_entry = secret_matches[0] if secret_matches else None
        self.check(
            "4",
            "review fork report compiles proposed KB candidate",
            secret_entry is not None
            and secret_entry.get("created_by_operator_agent_id") == self.sim_a,
            {
                "report_id": secret_report.get("report_id"),
                "secret_kb_id": secret_entry.get("kb_id") if secret_entry else None,
            },
        )
        if secret_entry is None:
            raise AssertionError("No secret guard KB entry found")
        secret_kb_id = str(secret_entry["kb_id"])
        self.secret_kb_ids.append(secret_kb_id)
        self.check(
            "4",
            "secret-like candidate is marked needs_review",
            secret_entry.get("redaction_status") == "needs_review",
            {"redaction_status": secret_entry.get("redaction_status")},
        )
        fork_promote = self.client.request(
            "POST",
            f"/v1/operator/kb/{self.quote(secret_kb_id)}/promote",
            {"operator_agent_id": self.sim_fork},
            expect=(400, 403, 409, 422),
        )
        root_secret_promote = self.client.request(
            "POST",
            f"/v1/operator/kb/{self.quote(secret_kb_id)}/promote",
            {"operator_agent_id": self.sim_a},
            expect=(400, 403, 409, 422),
        )
        self.check(
            "4",
            "review fork cannot promote durable KB",
            fork_promote.get("http_status") in {400, 403, 409, 422},
            fork_promote,
        )
        self.check(
            "4",
            "root cannot promote secret-like KB without override",
            root_secret_promote.get("http_status") in {400, 403, 409, 422},
            root_secret_promote,
        )
        exported = self.client.request(
            "GET",
            "/v1/operator/kb/export",
            query={
                "operator_agent_id": self.sim_a,
                "query": "Durable handoff routing guidance",
                "project": self.project,
            },
        )
        export_entries = exported.get("entries") if isinstance(exported, dict) else []
        exported_ids = [
            str(entry.get("kb_id") or "")
            for entry in export_entries
            if isinstance(entry, dict)
        ]
        self.check(
            "4",
            "clean active KB export includes promoted entry only",
            self.created_kb_ids[0] in exported_ids
            and secret_kb_id not in exported_ids,
            {
                "exported_ids": exported_ids,
                "secret_kb_id": secret_kb_id,
            },
        )
        self.check(
            "4",
            "clean KB export does not include secret-like markers",
            not json_text_contains_private_markers(exported),
            {"exported_count": len(export_entries) if isinstance(export_entries, list) else 0},
        )
        rebuild_job = self.client.request(
            "POST",
            "/v1/operator/kb/index-jobs",
            {"operation": "rebuild", "metadata": {"uat_run": self.tag}},
        )
        run_index = self.client.request(
            "POST",
            "/v1/operator/kb/index-jobs/run",
            query={"limit": 20},
        )
        post_reindex_context = self.client.request(
            "POST",
            "/v1/operator/kb/context",
            {
                "operator_agent_id": self.sim_b,
                "query": "project-scoped KB context repo roots",
                "project": self.project,
                "tags": ["kb-sim"],
                "limit": 5,
            },
        )
        self.check(
            "4",
            "TUI-equivalent reindex path preserves KB search",
            rebuild_job.get("operation") == "rebuild"
            and run_index.get("failed_count") == 0
            and post_reindex_context.get("satisfied_by_kb") is True,
            {
                "job_id": rebuild_job.get("job_id"),
                "processed_count": run_index.get("processed_count"),
                "failed_count": run_index.get("failed_count"),
                "post_reindex_match_count": post_reindex_context.get("match_count"),
            },
        )

    def stage_5_tmux_sink(self) -> None:
        window_name = f"kb-sim-{uuid.uuid4().hex[:6]}"
        new_window = subprocess.run(
            [
                self.tmux_bin,
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                f"{self.tmux_session}:",
                "-n",
                window_name,
                "-c",
                self.controlled_cwd,
                "cat >/dev/null",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if new_window.returncode != 0:
            self.check(
                "5",
                "controlled tmux pane unavailable",
                False,
                {"session": self.tmux_session, "stderr": new_window.stderr.strip()},
            )
            return
        self.tmux_pane_id = new_window.stdout.strip()
        if self.sim_tmux not in self.created_agents:
            self.created_agents.append(self.sim_tmux)
        tmux_agent = self.client.request(
            "POST",
            "/v1/agents/register",
            {
                "agent_id": self.sim_tmux,
                "project": "agent-pbx-operator",
                "agent_type": "operator",
                "metadata": {
                    "pbx_mode": "report",
                    "cwd": self.controlled_cwd,
                    "operator_role": "root",
                    "tmux_pane_id": self.tmux_pane_id,
                    "uat_run": self.tag,
                    "suppress_tui_alerts": True,
                },
            },
        )
        self.check(
            "5",
            "registered controlled tmux-backed synthetic operator",
            tmux_agent.get("metadata", {}).get("tmux_pane_id") == self.tmux_pane_id
            and tmux_agent.get("metadata", {}).get("cwd") == self.controlled_cwd,
            {
                "agent_id": self.sim_tmux,
                "tmux_pane_id": self.tmux_pane_id,
                "cwd": tmux_agent.get("metadata", {}).get("cwd"),
            },
        )
        tmux_handoff = self.client.request(
            "POST",
            "/v1/operator/handoffs",
            {
                "operator_agent_id": self.sim_a,
                "source_agent_id": self.sim_a,
                "target_operator_agent_id": self.sim_tmux,
                "objective": "Validate tmux delivery evidence with attached KB context.",
                "message": "Synthetic tmux delivery validation. This pane is a controlled sink.",
                "allowed_mutation_scope": "controlled tmux sink only",
                "needs_ack": True,
                "summary": f"UAT tmux handoff {self.tag}",
                "metadata": {
                    "kb_query": "project-scoped KB context repo roots",
                    "kb_project": self.project,
                    "kb_tags": ["kb-sim"],
                    "uat_run": self.tag,
                },
            },
        )
        handoff_id = str(tmux_handoff["handoff_id"])
        self.handoff_ids.append(handoff_id)
        preflight = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/preflight",
            {"operator_agent_id": self.sim_a, "delivery": "tmux"},
        )
        self.check(
            "5",
            "tmux preflight resolves controlled pane and warns non-Codex sink",
            preflight.get("ok") is True
            and preflight.get("pane", {}).get("pane_id") == self.tmux_pane_id
            and "tmux target command does not look like Codex"
            in (preflight.get("warnings") or []),
            {
                "status": preflight.get("status"),
                "pane": preflight.get("pane"),
                "warnings": preflight.get("warnings"),
            },
        )
        sent = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/approve",
            {
                "operator_agent_id": self.sim_a,
                "delivery": "tmux",
                "metadata": {"uat_run": self.tag},
            },
        )
        evidence = sent.get("handoff", {}).get("delivery_evidence", {})
        command = sent.get("command") or {}
        self.check(
            "5",
            "tmux approval records sent command and precise pane evidence",
            sent.get("handoff", {}).get("status") == "sent"
            and command.get("status") == "sent"
            and evidence.get("delivered_to_pane") is True
            and evidence.get("text_pasted") is True
            and evidence.get("enter_sent") is True
            and evidence.get("submitted_to_codex") is False
            and evidence.get("tmux_pane_id") == self.tmux_pane_id,
            {
                "handoff_id": handoff_id,
                "command_id": command.get("command_id"),
                "tmux_pane_id": evidence.get("tmux_pane_id"),
                "target_command": evidence.get("target_command"),
                "submitted_to_codex": evidence.get("submitted_to_codex"),
                "delivery_status": sent.get("handoff", {}).get("delivery_status"),
            },
        )
        tmux_ack = self.client.request(
            "POST",
            f"/v1/operator/handoffs/{self.quote(handoff_id)}/ack",
            {
                "operator_agent_id": self.sim_tmux,
                "status": "acknowledged",
                "summary": "Controlled tmux receiver acknowledged synthetic delivery.",
                "metadata": {"uat_run": self.tag},
            },
        )
        self.check(
            "5",
            "tmux-backed target can ack handoff state",
            tmux_ack.get("status") == "acknowledged"
            and tmux_ack.get("delivery_evidence", {}).get("agent_acknowledged") is True,
            {
                "status": tmux_ack.get("status"),
                "agent_acknowledged": tmux_ack.get("delivery_evidence", {}).get(
                    "agent_acknowledged"
                ),
            },
        )

    def latest_reports_snapshot(self) -> dict[str, str | None]:
        snapshot: dict[str, str | None] = {}
        try:
            agents = self.client.request("GET", "/v1/agents", query={"include_hidden": True})
        except Exception:
            return snapshot
        for agent in agents:
            agent_id = str(agent.get("agent_id") or "")
            if not agent_id:
                continue
            try:
                reports = self.client.request(
                    "GET",
                    f"/v1/agents/{self.quote(agent_id)}/reports",
                    query={"limit": 1},
                )
            except Exception:
                snapshot[agent_id] = None
                continue
            snapshot[agent_id] = (
                str(reports[0].get("report_id")) if reports else None
            )
        return snapshot

    def changed_reports(
        self,
        before: dict[str, str | None],
        after: dict[str, str | None],
    ) -> list[dict[str, str | None]]:
        sim_ids = {
            self.sim_a,
            self.sim_b,
            self.sim_fork,
            self.sim_tmux,
            "codex-agent-pbx",
        }
        changed: list[dict[str, str | None]] = []
        for agent_id, report_id in after.items():
            if agent_id in sim_ids or agent_id.startswith("operator-kb-sim-"):
                continue
            if before.get(agent_id) != report_id:
                changed.append(
                    {
                        "agent_id": agent_id,
                        "before": before.get(agent_id),
                        "after": report_id,
                    }
                )
        return changed

    def try_cleanup(self) -> None:
        for kb_id in self.active_kb_ids:
            try:
                payload = self.client.request(
                    "POST",
                    f"/v1/operator/kb/{self.quote(kb_id)}/retire",
                    {
                        "operator_agent_id": self.sim_a,
                        "summary": f"Retire UAT KB entry {self.tag}",
                        "metadata": {"uat_cleanup": True, "uat_run": self.tag},
                    },
                )
                self.cleanup.append(
                    {"kind": "kb_retired", "kb_id": kb_id, "status": payload.get("status")}
                )
            except Exception as exc:
                self.cleanup.append(
                    {"kind": "kb_retire_failed", "kb_id": kb_id, "error": str(exc)}
                )
        for kb_id in self.secret_kb_ids:
            try:
                payload = self.client.request(
                    "POST",
                    f"/v1/operator/kb/{self.quote(kb_id)}/reject",
                    {
                        "operator_agent_id": self.sim_a,
                        "summary": f"Reject UAT redaction guard entry {self.tag}",
                        "metadata": {"uat_cleanup": True, "uat_run": self.tag},
                    },
                )
                self.cleanup.append(
                    {"kind": "kb_rejected", "kb_id": kb_id, "status": payload.get("status")}
                )
            except Exception as exc:
                self.cleanup.append(
                    {"kind": "kb_reject_failed", "kb_id": kb_id, "error": str(exc)}
                )
        seen: set[str] = set()
        for agent_id in self.created_agents:
            if agent_id in seen:
                continue
            seen.add(agent_id)
            try:
                payload = self.client.request(
                    "DELETE",
                    f"/v1/agents/{self.quote(agent_id)}",
                )
                self.cleanup.append(
                    {
                        "kind": "agent_dismissed",
                        "agent_id": agent_id,
                        "dismissed_at": payload.get("dismissed_at"),
                    }
                )
            except Exception as exc:
                self.cleanup.append(
                    {
                        "kind": "agent_dismiss_failed",
                        "agent_id": agent_id,
                        "error": str(exc),
                    }
                )
        if self.tmux_pane_id:
            self.cleanup.append(cleanup_tmux_pane(self.tmux_bin, self.tmux_pane_id))
        self.write_manifest("cleanup_complete")

    def cleanup_failure_count(self) -> int:
        failed_count = 0
        for item in self.cleanup:
            if str(item.get("kind") or "").endswith("_failed"):
                failed_count += 1
                continue
            try:
                returncode = int(item.get("returncode") or 0)
            except (TypeError, ValueError):
                returncode = 0
            if returncode != 0:
                failed_count += 1
        return failed_count

    @staticmethod
    def quote(value: str) -> str:
        return urllib.parse.quote(value, safe="")


def run_operator_kb_flow_uat(
    *,
    server: str,
    token: str | None,
    project: str = "agent-pbx-kb-sim",
    tmux_sink: bool = False,
    tmux_session: str | None = None,
    controlled_cwd: Path | None = None,
    cleanup: bool = True,
    timeout: float = 20.0,
    state_root: Path | None = None,
    tmux_bin: str = "tmux",
) -> dict[str, Any]:
    return OperatorKbFlowUAT(
        server=server,
        token=token,
        project=project,
        tmux_sink=tmux_sink,
        tmux_session=tmux_session,
        controlled_cwd=controlled_cwd,
        cleanup=cleanup,
        timeout=timeout,
        state_root=state_root,
        tmux_bin=tmux_bin,
    ).run()


def operator_kb_flow_markdown(result: dict[str, Any]) -> str:
    failures = result.get("failures") if isinstance(result.get("failures"), list) else []
    results = result.get("results") if isinstance(result.get("results"), list) else []
    pass_count = sum(1 for item in results if isinstance(item, dict) and item.get("ok"))
    lines = [
        f"# Operator KB Flow UAT {result.get('run')}",
        "",
        f"- Base: `{result.get('base')}`",
        f"- Project: `{result.get('project')}`",
        f"- Manifest: `{result.get('manifest_path') or '-'}`",
        f"- Tmux session: `{result.get('tmux_session') or '-'}`",
        f"- Checks: {pass_count}/{len(results)} passed",
        f"- Failures: {len(failures)}",
        f"- Non-synthetic report changes: {result.get('non_sim_report_change_count')}",
        "",
        "## Results",
        "",
    ]
    for item in results:
        if not isinstance(item, dict):
            continue
        mark = "PASS" if item.get("ok") else "FAIL"
        lines.append(f"- [{mark}] Stage {item.get('stage')}: {item.get('name')}")
    lines.extend(["", "## Cleanup", ""])
    for item in result.get("cleanup", []):
        if isinstance(item, dict):
            lines.append(f"- {item.get('kind')}: {item}")
    if failures:
        lines.extend(["", "## Failures", ""])
        for failure in failures:
            lines.append(f"- {failure}")
    return "\n".join(lines)
