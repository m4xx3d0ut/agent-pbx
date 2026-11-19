from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ...workerbee_mcp import (
    WorkerBeeBackendProvenance,
    WorkerBeeMcpPreflight,
    WorkerBeeProjectVisibilityDiagnostic,
)
from ._base import TUIController


def _list_value(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


class WorkerBeeProjectionController(TUIController[Any]):
    def format_workerbee_backend_provenance(
        self,
        provenance: WorkerBeeBackendProvenance,
    ) -> str:
        lines = [
            "WorkerBee Backend",
            f"Code: {provenance.code}",
            f"Switcher: {'configured' if provenance.configured else 'not configured'}",
            f"Backend: {provenance.backend or '-'}",
            f"Endpoint: {provenance.endpoint or '-'}",
            f"Server: {provenance.server_name or '-'} {provenance.server_version}".rstrip(),
            f"Detail: {provenance.message}",
        ]
        if provenance.config_path:
            lines.append(f"Config: {provenance.config_path}")
        if provenance.remediation:
            lines.append(f"Remediation: {provenance.remediation}")
        return "\n".join(lines)

    def format_workerbee_project_visibility(
        self,
        result: WorkerBeeProjectVisibilityDiagnostic,
    ) -> str:
        lines = [
            "WorkerBee Project Visibility",
            f"Code: {result.code}",
            f"Ready: {'yes' if result.ready else 'no'}",
            f"Host cwd: {result.host_cwd}",
            f"Host Git root: {result.host_git_root or '-'}",
            f"WorkerBee Git root: {result.workerbee_git_root or '-'}",
            f"WorkerBee repo exists: {result.workerbee_repo_exists}",
            f"Detail: {result.message}",
        ]
        if result.remediation:
            lines.append(f"Remediation: {result.remediation}")
        return "\n".join(lines)

    def format_workerbee_mcp_preflight(self, result: WorkerBeeMcpPreflight) -> str:
        parsed = urlparse(result.endpoint)
        safe_endpoint = (
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.scheme
            else result.endpoint
        )
        lines = [
            "WorkerBee MCP Preflight",
            f"Code: {result.code}",
            f"Ready: {'yes' if result.ready else 'no'}",
            f"Endpoint: {safe_endpoint or '-'}",
            f"Version: {result.workerbee_version or '-'}",
            f"Detail: {result.message}",
        ]
        if result.missing_tools:
            lines.append("Missing: " + ", ".join(result.missing_tools))
        if result.remediation:
            lines.append(f"Remediation: {result.remediation}")
        return "\n".join(lines)

    def format_workerbee_status(self, status: dict[str, Any]) -> str:
        agent_id = str(status.get("agent_id") or "-")
        cwd = str(status.get("cwd") or "-")
        workerbee_bin = str(status.get("workerbee_bin") or "-")
        lines = [
            f"Agent: {agent_id}",
            f"Cwd: {cwd}",
            f"WorkerBee: {workerbee_bin}",
            f"Status source: {status.get('status_source') or 'host_cli'}",
        ]
        selected_runtime = (
            status.get("selected_runtime")
            if isinstance(status.get("selected_runtime"), dict)
            else None
        )
        if selected_runtime:
            observed = selected_runtime.get("observed") or {}
            gate = selected_runtime.get("admission") or {}
            lease = selected_runtime.get("gateway") or {}
            bound = lease.get("bound") or {}
            lines.extend([
                f"Selected backend: {selected_runtime.get('backend') or '-'}",
                f"Backend generation: {selected_runtime.get('generation') or '-'}",
                f"Selected instance: {selected_runtime.get('instance_id') or '-'}",
                f"Selected source mode: {selected_runtime.get('source_mode') or '-'}",
            ])
            if gate:
                lines.extend([
                    f"Selector admission: {gate.get('admission', '-')} {gate.get('reason', '')}".rstrip(),
                    f"Observed backend: {observed.get('backend', '-')} instance {observed.get('instance_id', '-')} "
                    f"({selected_runtime.get('observation_state', 'not_probed')})",
                    f"Gateway binding: {lease.get('state', '-')} · "
                    f"{bound.get('backend', '-')} g{bound.get('generation', '-')} "
                    f"instance {bound.get('instance_id', '-')} · {lease.get('source_mode') or '-'}",
                ])
                if lease.get("rebind_required"):
                    lines.append("Review the observed identity and rebind through an approved launch.")
                elif lease.get("projection_stale"):
                    lines.append("Agent metadata is behind the recorded gateway lease.")
        workspace_binding = (
            status.get("workspace_binding")
            if isinstance(status.get("workspace_binding"), dict)
            else None
        )
        if workspace_binding:
            lines.extend(
                [
                    f"Workspace binding: {workspace_binding.get('binding_id') or '-'}",
                    f"Runtime workspace: {workspace_binding.get('runtime_workspace_path') or '-'}",
                ]
            )
        workspace_policy = (
            status.get("workspace_policy")
            if isinstance(status.get("workspace_policy"), dict)
            else {}
        )
        snapshot_policy = (
            workspace_policy.get("snapshot")
            if isinstance(workspace_policy.get("snapshot"), dict)
            else {}
        )
        access_policy = (
            workspace_policy.get("access")
            if isinstance(workspace_policy.get("access"), dict)
            else {}
        )
        if access_policy.get("profile"):
            lines.append(f"WorkerBee access: {access_policy.get('profile')}")
        if workspace_policy:
            lines.append(
                "Workspace mobility: "
                f"{workspace_policy.get('policy') or '-'} / "
                f"{workspace_policy.get('request_mode') or '-'}"
            )
        if snapshot_policy.get("applicable"):
            lines.append(
                "Appliance content: "
                f"{snapshot_policy.get('policy') or '-'}"
                + (
                    f" ({snapshot_policy.get('workflow_label')})"
                    if snapshot_policy.get("workflow_label")
                    else ""
                )
            )
        error = status.get("error") if isinstance(status.get("error"), dict) else None
        if error:
            lines.extend(
                [
                    "",
                    "Status: unavailable",
                    f"Code: {error.get('code', 'WORKERBEE_ERROR')}",
                    f"Message: {error.get('message', '')}",
                ]
            )
            remediation = error.get("remediation")
            if remediation:
                lines.append(f"Remediation: {remediation}")
            return "\n".join(lines)

        if not status.get("available"):
            lines.extend(["", "Status: unavailable"])
            return "\n".join(lines)

        project = str(status.get("project") or "-")
        mode = str(status.get("mode") or "-")
        status_kind = str(status.get("status_kind") or "-")
        running = status.get("running")
        dashboard_url = str(status.get("dashboard_url") or "-")
        state_dir = str(status.get("state_dir") or "-")
        description = str(status.get("description") or "-")
        app_status = status.get("app_status")
        latest_deployment = status.get("latest_deployment")
        project_card = status.get("project_card")
        global_dashboard = status.get("global_dashboard")
        dashboard_error = (
            status.get("dashboard_error")
            if isinstance(status.get("dashboard_error"), dict)
            else None
        )
        lines.extend(
            [
                "",
                "Project",
                f"Name: {project}",
                f"Mode: {mode}",
                f"Running: {running if running is not None else '-'}",
                f"Status: {status_kind}",
                f"Dashboard: {dashboard_url}",
                f"State Dir: {state_dir}",
                f"Description: {description}",
            ]
        )
        lines.extend(self.format_workerbee_app_status(app_status))
        lines.extend(self.format_workerbee_deployment(latest_deployment))
        lines.extend(self.format_workerbee_project_card(project_card))
        lines.extend(self.format_workerbee_global_dashboard(global_dashboard))
        lines.extend(self.format_workerbee_dashboard_error(dashboard_error))
        return "\n".join(lines)

    def format_workerbee_app_status(self, app_status: object) -> list[str]:
        if not isinstance(app_status, dict):
            return ["", "App", "No app status reported."]
        lines = [
            "",
            "App",
            f"State: {app_status.get('state') or '-'}",
            f"Ready: {app_status.get('ready')}",
            f"Message: {app_status.get('message') or '-'}",
            (
                "Workloads: "
                f"{app_status.get('declared_workload_count', 0)} declared, "
                f"{app_status.get('ready_workload_count', 0)} ready, "
                f"{app_status.get('degraded_workload_count', 0)} degraded, "
                f"{app_status.get('orphaned_workload_count', 0)} orphaned"
            ),
        ]
        urls = _list_value(app_status.get("ingress_urls"))
        if urls:
            lines.append("Ingress URLs:")
            lines.extend(f"- {url}" for url in urls)
        workloads = _list_value(app_status.get("declared_workloads"))
        if workloads:
            lines.append("Declared Workloads:")
            lines.extend(
                f"- {self.format_workerbee_workload(workload)}"
                for workload in workloads
            )
        return lines

    def format_workerbee_deployment(self, deployment: object) -> list[str]:
        lines = ["", "Latest Deployment"]
        if not isinstance(deployment, dict):
            lines.append("No deployment recorded yet.")
            return lines
        lines.extend(
            [
                f"ID: {deployment.get('id') or deployment.get('deployment_id') or '-'}",
                f"Target: {deployment.get('target') or '-'}",
                f"Namespace: {deployment.get('namespace') or '-'}",
                f"Stage: {deployment.get('stage') or deployment.get('stage_dir') or '-'}",
                f"Created: {deployment.get('created_at') or '-'}",
                f"Updated: {deployment.get('updated_at') or '-'}",
            ]
        )
        urls = _list_value(deployment.get("ingress_urls"))
        if urls:
            lines.append("Ingress URLs:")
            lines.extend(f"- {url}" for url in urls)
        validation = deployment.get("validation")
        if isinstance(validation, dict):
            findings = _list_value(validation.get("findings"))
            if findings:
                lines.append("Validation Findings:")
                lines.extend(
                    f"- {finding.get('level', 'info')} {finding.get('code', '')}: "
                    f"{finding.get('message', '')}"
                    for finding in findings
                    if isinstance(finding, dict)
                )
        return lines

    def format_workerbee_project_card(self, project_card: object) -> list[str]:
        if not isinstance(project_card, dict):
            return []
        lines = ["", "Dashboard Project"]
        for label, key in (
            ("Kind", "status_kind"),
            ("Stack", "stack_running"),
            ("Profile", "profile_running"),
            ("Ingress", "ingress_status"),
            ("Exposed Routes", "exposed_route_summary"),
        ):
            value = project_card.get(key)
            if value is not None:
                lines.append(f"{label}: {value}")
        return lines

    def format_workerbee_global_dashboard(self, dashboard: object) -> list[str]:
        if not isinstance(dashboard, dict):
            return []
        lines = ["", "Global Dashboard"]
        for label, key in (
            ("URL", "dashboard_url"),
            ("Running", "running"),
            ("Exposure", "exposure"),
            ("Runtime", "runtime"),
            ("CA Ready", "ca_ready"),
        ):
            value = dashboard.get(key)
            if value is not None:
                lines.append(f"{label}: {value}")
        return lines

    def format_workerbee_dashboard_error(
        self,
        error: dict[str, Any] | None,
    ) -> list[str]:
        if not error:
            return []
        lines = [
            "",
            "Dashboard Warning",
            f"Code: {error.get('code', 'WORKERBEE_DASHBOARD_ERROR')}",
            f"Message: {error.get('message', '')}",
        ]
        if "retryable" in error:
            lines.append(f"Retryable: {error.get('retryable')}")
        return lines

    def format_workerbee_workload(self, workload: object) -> str:
        if not isinstance(workload, dict):
            return str(workload)
        kind = workload.get("kind") or workload.get("input_kind") or "workload"
        name = workload.get("name") or "-"
        namespace = workload.get("namespace") or "-"
        return f"{kind} {namespace}/{name}"
