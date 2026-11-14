from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from textual.widgets import DataTable, Static, TextArea

from ... import tmux as tmux_support
from ...client import auth_headers
from ..agent_types import OPERATOR_AGENT_TYPE
from ..screens import slugify
from ._base import TUIController


def float_value(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class CampaignController(TUIController[Any]):
    async def load_operator_campaigns(self, agent_id: str) -> None:
        generation_resource = f"operator-campaigns:{agent_id}"
        generation = self.async_generations.start(generation_resource)
        status_label = self.query_one_or_none("#campaign-status", Static)
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        agent = self.agents.get(agent_id)
        if agent is None or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            self.render_campaigns(agent_id, [])
            if status_label is not None:
                status_label.update("Campaigns: select an operator agent")
            if detail is not None:
                detail.text = "Campaigns are shown for operator agents only."
            return
        if status_label is not None:
            status_label.update(f"Campaigns: loading for {agent_id}...")
        try:
            response = await self.api_client().get(
                "/v2/operator/campaigns",
                params={"operator_agent_id": agent_id, "limit": 25, "cursor": 0},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            payload = response.json()
            campaigns = (
                payload.get("campaigns", []) if isinstance(payload, dict) else []
            )
            if not isinstance(campaigns, list):
                campaigns = []
        except Exception as exc:
            if not self.async_generations.current(generation_resource, generation):
                return
            self.render_campaigns(agent_id, [])
            if status_label is not None:
                status_label.update(f"Campaigns: unable to load ({exc})")
            if detail is not None:
                detail.text = f"Unable to load campaigns for {agent_id}: {exc}"
            return
        if not self.async_generations.current(generation_resource, generation):
            return
        self.campaigns_by_operator[agent_id] = {
            str(campaign.get("campaign_id")): campaign
            for campaign in campaigns
            if campaign.get("campaign_id")
        }
        self.render_campaigns(agent_id, campaigns)
        if status_label is not None:
            status_label.update(f"Campaigns: {len(campaigns)} for {agent_id}")
        selected = self.selected_campaign_id_by_operator.get(agent_id)
        if selected in self.campaigns_by_operator.get(agent_id, {}):
            self.select_campaign(selected)
        elif campaigns:
            self.select_campaign(str(campaigns[0]["campaign_id"]))
        elif detail is not None:
            detail.text = f"No operator campaigns for {agent_id}."

    async def load_campaign_detail(self, operator_id: str, campaign_id: str) -> None:
        generation_resource = f"operator-campaign-detail:{operator_id}"
        generation = self.async_generations.start(generation_resource)
        try:
            response = await self.api_client().get(
                f"/v2/operator/campaigns/{campaign_id}",
                params={"operator_agent_id": operator_id},
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            campaign = response.json()
            if not isinstance(campaign, dict):
                raise RuntimeError("campaign detail response was not an object")
            await self.attach_campaign_reports([campaign])
        except Exception as exc:
            if not self.async_generations.current(generation_resource, generation):
                return
            detail = self.query_one_or_none("#campaign-detail", TextArea)
            if (
                detail is not None
                and self.selected_campaign_id_by_operator.get(operator_id)
                == campaign_id
            ):
                detail.text = f"Unable to load campaign {campaign_id}: {exc}"
            return
        if not self.async_generations.current(generation_resource, generation):
            return
        self.campaigns_by_operator.setdefault(operator_id, {})[campaign_id] = campaign
        if (
            self.selected_agent_id == operator_id
            and self.selected_campaign_id_by_operator.get(operator_id) == campaign_id
        ):
            detail = self.query_one_or_none("#campaign-detail", TextArea)
            if detail is not None:
                detail.text = self.format_campaign_detail(campaign)

    def campaign_report_ids(self, campaign: dict[str, Any]) -> list[str]:
        report_ids: list[str] = []
        seen: set[str] = set()

        def add(value: Any) -> None:
            report_id = str(value or "").strip()
            if report_id and report_id not in seen:
                seen.add(report_id)
                report_ids.append(report_id)

        assignments = campaign.get("assignments") or []
        if isinstance(assignments, list):
            for assignment in assignments:
                if isinstance(assignment, dict):
                    add(assignment.get("last_report_id"))
        events = campaign.get("events") or []
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict):
                    add(event.get("report_id"))
        return report_ids

    async def fetch_report_by_id(self, report_id: str) -> dict[str, Any]:
        response = await self.api_client().get(
            f"/v1/reports/{report_id}",
            headers=auth_headers(self.token),
        )
        response.raise_for_status()
        report = response.json()
        if not isinstance(report, dict):
            raise RuntimeError(f"report {report_id} response was not an object")
        return report

    async def attach_campaign_reports(
        self,
        campaigns: list[dict[str, Any]],
    ) -> None:
        report_ids: list[str] = []
        seen: set[str] = set()
        for campaign in campaigns:
            if not isinstance(campaign, dict):
                continue
            for report_id in self.campaign_report_ids(campaign):
                if report_id not in seen:
                    seen.add(report_id)
                    report_ids.append(report_id)
        reports_by_id: dict[str, dict[str, Any]] = {}

        async def fetch(report_id: str) -> tuple[str, dict[str, Any]]:
            try:
                return report_id, await self.fetch_report_by_id(report_id)
            except Exception as exc:
                return report_id, {
                    "report_id": report_id,
                    "error": str(exc),
                }

        for report_id, report in await asyncio.gather(
            *(fetch(report_id) for report_id in report_ids)
        ):
            reports_by_id[report_id] = report
        for campaign in campaigns:
            if not isinstance(campaign, dict):
                continue
            campaign["_reports_by_id"] = {
                report_id: reports_by_id[report_id]
                for report_id in self.campaign_report_ids(campaign)
                if report_id in reports_by_id
            }

    def render_campaigns(
        self,
        agent_id: str,
        campaigns: list[dict[str, Any]],
    ) -> None:
        table = self.query_one_or_none("#campaigns", DataTable)
        if table is None:
            return
        position = self.data_table_position(table)
        table.clear()
        for campaign in campaigns:
            campaign_id = str(campaign.get("campaign_id") or "")
            if not campaign_id:
                continue
            assignments = campaign.get("assignments") or []
            states: dict[str, int] = {}
            if isinstance(assignments, list):
                for assignment in assignments:
                    if isinstance(assignment, dict):
                        state = str(assignment.get("state") or "pending")
                        states[state] = states.get(state, 0) + 1
            assignment_text = (
                ", ".join(f"{state}:{count}" for state, count in sorted(states.items()))
                or "-"
            )
            updated = float_value(campaign.get("updated_at"))
            updated_text = f"{updated:.0f}" if updated is not None else "-"
            table.add_row(
                str(campaign.get("status") or "-"),
                assignment_text,
                str(campaign.get("title") or campaign_id),
                updated_text,
                key=campaign_id,
            )
        selected = self.selected_campaign_id_by_operator.get(agent_id)
        if selected and selected in self.campaigns_by_operator.get(agent_id, {}):
            try:
                table.move_cursor(
                    row=table.get_row_index(selected),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                pass
        self.restore_data_table_position(table, position)

    def select_campaign(self, campaign_id: str) -> None:
        operator_id = self.selected_agent_id
        if not operator_id:
            return
        campaign = self.campaigns_by_operator.get(operator_id, {}).get(campaign_id)
        if campaign is None:
            return
        self.selected_campaign_id = campaign_id
        self.selected_campaign_id_by_operator[operator_id] = campaign_id
        report_ids = self.campaign_report_ids(campaign)
        selected_report_id = self.selected_campaign_report_id_by_operator.get(
            operator_id
        )
        if selected_report_id not in report_ids:
            selected_report_id = report_ids[0] if report_ids else None
        if selected_report_id:
            self.selected_campaign_report_id_by_operator[operator_id] = (
                selected_report_id
            )
        else:
            self.selected_campaign_report_id_by_operator.pop(operator_id, None)
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if detail is not None:
            detail.text = self.format_campaign_detail(campaign)
        self.run_worker(
            self.load_campaign_detail(operator_id, campaign_id),
            name=f"campaign-detail-{slugify(operator_id)}",
            group=f"campaign-detail-{slugify(operator_id)}",
            exclusive=True,
        )

    def report_summary_line(self, report: dict[str, Any]) -> str:
        if report.get("error"):
            return f"unavailable: {report.get('error')}"
        status = str(report.get("status") or "-")
        summary = str(report.get("summary") or "").strip()
        return f"{status} {summary}".strip()

    def format_report_block(self, report: dict[str, Any]) -> list[str]:
        report_id = str(report.get("report_id") or "-")
        if report.get("error"):
            return [
                f"Report: {report_id}",
                f"Error: {report.get('error')}",
            ]
        lines = [
            f"Report: {report_id}",
            f"Agent: {report.get('agent_id')}",
            f"Status: {report.get('status')}",
            f"Summary: {report.get('summary')}",
            "",
            str(report.get("detail") or ""),
        ]
        return lines

    def selected_campaign_for_operator(
        self,
        operator_id: str | None,
    ) -> dict[str, Any] | None:
        if not operator_id:
            return None
        campaign_id = self.selected_campaign_id_by_operator.get(operator_id)
        if not campaign_id:
            return None
        return self.campaigns_by_operator.get(operator_id, {}).get(campaign_id)

    def format_campaign_detail(self, campaign: dict[str, Any]) -> str:
        lines = [
            f"Campaign: {campaign.get('title')}",
            f"ID: {campaign.get('campaign_id')}",
            f"Status: {campaign.get('status')}",
            "",
            "Objective:",
            str(campaign.get("objective") or ""),
        ]
        criteria = campaign.get("criteria") or []
        if criteria:
            lines.extend(["", "Criteria:", *[f"- {item}" for item in criteria]])
        reports_by_id = (
            campaign.get("_reports_by_id")
            if isinstance(campaign.get("_reports_by_id"), dict)
            else {}
        )
        assignments = campaign.get("assignments") or []
        if assignments:
            lines.extend(["", "Assignments:"])
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    continue
                lines.append(
                    "- "
                    f"{assignment.get('target_agent_id')} "
                    f"[{assignment.get('state')}] "
                    f"{assignment.get('title')}"
                )
                if assignment.get("last_command_id"):
                    lines.append(f"  command: {assignment.get('last_command_id')}")
                if assignment.get("operator_fork_id"):
                    lines.append(f"  fork: {assignment.get('operator_fork_id')}")
                if assignment.get("last_report_id"):
                    report_id = str(assignment.get("last_report_id"))
                    report = reports_by_id.get(report_id)
                    if isinstance(report, dict):
                        lines.append(
                            f"  report: {report_id} ({self.report_summary_line(report)})"
                        )
                    else:
                        lines.append(f"  report: {report_id}")
        events = campaign.get("events") or []
        if events:
            lines.extend(["", "Recent Events:"])
            for event in events[:10]:
                if not isinstance(event, dict):
                    continue
                lines.append(f"- {event.get('event_type')} {event.get('summary')}")
                if event.get("report_id"):
                    lines.append(f"  report: {event.get('report_id')}")
        report_ids = self.campaign_report_ids(campaign)
        if report_ids:
            lines.extend(["", "Reports:"])
            for index, report_id in enumerate(report_ids, start=1):
                report = reports_by_id.get(report_id)
                if not isinstance(report, dict):
                    lines.append(f"### {index}. {report_id}")
                    lines.append("(not loaded)")
                    continue
                lines.append(f"### {index}. {report_id}")
                block = self.format_report_block(report)
                for block_line in block[1:]:
                    lines.append(block_line)
                lines.append("")
        return "\n".join(lines)

    def campaign_joplin_copy_title(self, campaign: dict[str, Any]) -> str:
        title = str(campaign.get("title") or campaign.get("campaign_id") or "").strip()
        if not title:
            title = "Campaign"
        return f"Campaign - {title}"

    def format_campaign_joplin_copy_body(
        self,
        operator_id: str,
        campaign: dict[str, Any],
    ) -> str:
        title = str(campaign.get("title") or campaign.get("campaign_id") or "Campaign")
        campaign_id = str(campaign.get("campaign_id") or "")
        status = str(campaign.get("status") or "")
        copied_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return "\n".join(
            [
                f"# Campaign - {title}",
                "",
                f"- Campaign ID: `{campaign_id}`",
                f"- Operator Agent: `{operator_id}`",
                f"- Status: `{status}`",
                f"- Copied: `{copied_at}`",
                "",
                "## Campaign Detail",
                "",
                "```text",
                self.format_campaign_detail(campaign).strip(),
                "```",
            ]
        )

    async def copy_selected_campaign_to_joplin(self) -> None:
        operator_id = self.selected_agent_id
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return
        campaign = self.selected_campaign_for_operator(operator_id)
        if campaign is None:
            if detail is not None:
                detail.text = "Select a campaign before copying to Joplin."
            self.notify(
                "Select a campaign before copying to Joplin.", severity="warning"
            )
            return
        report_ids = self.campaign_report_ids(campaign)
        reports_by_id = (
            campaign.get("_reports_by_id")
            if isinstance(campaign.get("_reports_by_id"), dict)
            else {}
        )
        if any(report_id not in reports_by_id for report_id in report_ids):
            await self.attach_campaign_reports([campaign])
        await self.create_manual_joplin_copy(
            operator_id,
            title=self.campaign_joplin_copy_title(campaign),
            body=self.format_campaign_joplin_copy_body(operator_id, campaign),
            success_message="Copied campaign to a new Joplin note.",
        )

    async def export_selected_campaign_evidence(self) -> dict[str, Any] | None:
        operator_id = self.selected_agent_id
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return None
        campaign = self.selected_campaign_for_operator(operator_id)
        campaign_id = str((campaign or {}).get("campaign_id") or "").strip()
        if not campaign_id:
            self.notify(
                "Select a campaign before exporting evidence.", severity="warning"
            )
            return None
        return await self.create_evidence_export(
            {
                "kind": "campaign",
                "subject_id": campaign_id,
                "formats": ["json", "markdown", "ndjson"],
            },
            label="campaign",
        )

    async def export_selected_agent_evidence_case(self) -> dict[str, Any] | None:
        agent_id = self.selected_agent_id
        if not agent_id:
            self.notify(
                "Select an Agent before exporting evidence.", severity="warning"
            )
            return None
        return await self.create_evidence_export(
            {
                "kind": "evidence_case",
                "subject_id": agent_id,
                "categories": [
                    "git_broker",
                    "lifecycle",
                    "runtime",
                    "handoff",
                    "workerbee_workspace",
                    "workerbee_run",
                    "workerbee_resource",
                ],
                "formats": ["json", "markdown", "ndjson"],
            },
            label="evidence case",
        )

    async def create_evidence_export(
        self,
        payload: dict[str, Any],
        *,
        label: str,
    ) -> dict[str, Any] | None:
        try:
            response = await self.api_client().post(
                "/v1/evidence/exports",
                json=payload,
                headers=auth_headers(self.token),
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:  # noqa: BLE001 - surfaced as a bounded TUI error
            self.notify(f"{label.title()} export failed: {exc}", severity="error")
            return None
        self.notify(
            f"Exported {label}: {result.get('export_id')} "
            f"({result.get('content_sha256')})"
        )
        return result

    def campaign_monitor_prompt(
        self,
        operator_id: str,
        campaign: dict[str, Any],
    ) -> str:
        campaign_id = str(campaign.get("campaign_id") or "").strip()
        title = str(campaign.get("title") or campaign_id or "Campaign").strip()
        status = str(campaign.get("status") or "unknown").strip()
        objective = str(campaign.get("objective") or "").strip()
        criteria = campaign.get("criteria") or []
        criteria_lines = [
            f"- {str(item).strip()}" for item in criteria if str(item).strip()
        ]
        if not criteria_lines:
            criteria_lines = ["- Use the campaign criteria already stored in PBX."]

        assignment_lines: list[str] = []
        assignments = campaign.get("assignments") or []
        if isinstance(assignments, list):
            for assignment in assignments:
                if not isinstance(assignment, dict):
                    continue
                assignment_id = str(assignment.get("assignment_id") or "-")
                target_agent_id = str(assignment.get("target_agent_id") or "-")
                state = str(assignment.get("state") or "-")
                fork_agent_id = str(
                    assignment.get("operator_fork_id")
                    or assignment.get("fork_agent_id")
                    or "-"
                )
                last_command_id = str(assignment.get("last_command_id") or "-")
                last_report_id = str(assignment.get("last_report_id") or "-")
                assignment_title = str(assignment.get("title") or "").strip()
                line = (
                    f"- assignment_id={assignment_id} "
                    f"target={target_agent_id} "
                    f"state={state} "
                    f"fork={fork_agent_id} "
                    f"last_command={last_command_id} "
                    f"last_report={last_report_id}"
                )
                if assignment_title:
                    line = f"{line} title={assignment_title}"
                assignment_lines.append(line)
        if not assignment_lines:
            assignment_lines = ["- no assignments loaded"]

        return "\n".join(
            [
                f"Monitor operator campaign `{campaign_id}` for `{operator_id}`.",
                "",
                f"Title: {title}",
                f"Current status: {status}",
                "",
                "Objective:",
                objective or "-",
                "",
                "Completion criteria:",
                *criteria_lines,
                "",
                "Assignments:",
                *assignment_lines,
                "",
                "Instructions:",
                "- Keep this root operator turn active until every campaign assignment is complete, blocked, failed, or canceled.",
                "- Do not return to idle after the initial dispatch/status check unless the campaign is terminal or human input is required.",
                "- Recheck with `pbx_operator_campaign_status` at natural milestones and about every 60 seconds while work is active.",
                "- Inspect assignment reports/thread state before deciding whether follow-up is needed.",
                "- Use `pbx_operator_send_followup` only for the campaign/fork targets in this campaign. Do not dispatch to regular caller tmux panes.",
                "- Native Codex children may perform bounded operator-local decomposition, but inherit session restrictions and may not become PBX identities, campaign assignees, or peer routers. Do not use legacy `multi_agent_v1`; durable caller work stays in visible Agent PBX fork panes.",
                "- Report assignment state changes with `pbx_operator_report_assignment`.",
                "- When all assignments are terminal, use `pbx_operator_finish_campaign` and report the final campaign result.",
            ]
        )

    async def send_operator_monitor_prompt(
        self,
        operator_id: str,
        prompt: str,
    ) -> bool:
        status = self.query_one_or_none("#tmux-status", Static)
        agent = self.agents.get(operator_id)
        metadata = agent.get("metadata") if isinstance(agent, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        tmux_pane_id = str(metadata.get("tmux_pane_id") or "").strip()
        if tmux_pane_id:
            try:
                panes = await asyncio.to_thread(tmux_support.list_panes)
            except Exception as exc:
                if status is not None:
                    status.update(f"Tmux: unavailable ({exc})")
            else:
                self.tmux_panes = panes
                for pane in panes:
                    if (
                        pane.pane_id != tmux_pane_id
                        and pane.target_label != tmux_pane_id
                    ):
                        continue
                    if isinstance(agent, dict) and not self.tmux_pane_allowed_for_agent(
                        agent,
                        pane,
                    ):
                        break
                    self.tmux_agent_targets[operator_id] = pane.pane_id
                    self.tmux_direct_agent_modes[operator_id] = True
                    self.tmux_detached_agent_ids.discard(operator_id)
                    self.save_settings()
                    return await self.send_text_to_tmux_pane(
                        pane.pane_id,
                        prompt,
                        status=status,
                    )
        return await self.send_text_to_tmux(operator_id, prompt)

    async def monitor_selected_campaign(self) -> None:
        operator_id = self.selected_agent_id
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return
        agent = self.agents.get(operator_id)
        if agent is None or self.agent_type(agent) != OPERATOR_AGENT_TYPE:
            if detail is not None:
                detail.text = (
                    "Campaign monitoring is available for operator agents only."
                )
            self.notify(
                "Select an operator agent before monitoring a campaign.",
                severity="warning",
            )
            return
        campaign = self.selected_campaign_for_operator(operator_id)
        if campaign is None:
            if detail is not None:
                detail.text = (
                    "Select a campaign before asking the operator to monitor it."
                )
            self.notify("Select a campaign before monitoring.", severity="warning")
            return
        prompt = self.campaign_monitor_prompt(operator_id, campaign)
        sent = await self.send_operator_monitor_prompt(operator_id, prompt)
        campaign_id = str(campaign.get("campaign_id") or "")
        if not sent:
            if detail is not None:
                detail.text = (
                    f"Unable to send monitor prompt for campaign {campaign_id}.\n\n"
                    f"{self.format_campaign_detail(campaign)}"
                )
            self.notify(
                "Unable to send monitor prompt to the operator pane.", severity="error"
            )
            return
        if detail is not None:
            detail.text = (
                f"Monitor prompt sent to {operator_id} for campaign {campaign_id}.\n\n"
                f"{self.format_campaign_detail(campaign)}"
            )
        self.notify(f"Monitor prompt sent to {operator_id}.")
        await self.refresh_events()

    async def view_selected_campaign_report(self) -> None:
        operator_id = self.selected_agent_id
        detail = self.query_one_or_none("#campaign-detail", TextArea)
        if not operator_id:
            self.notify("Select an operator campaign first.", severity="warning")
            return
        campaign = self.selected_campaign_for_operator(operator_id)
        if campaign is None:
            if detail is not None:
                detail.text = "Select a campaign before viewing a report."
            self.notify(
                "Select a campaign before viewing a report.", severity="warning"
            )
            return
        report_ids = self.campaign_report_ids(campaign)
        if not report_ids:
            if detail is not None:
                detail.text = "Selected campaign has no generated reports yet."
            self.notify(
                "Selected campaign has no generated reports yet.", severity="warning"
            )
            return
        report_id = self.selected_campaign_report_id_by_operator.get(operator_id)
        if report_id not in report_ids:
            report_id = report_ids[0]
            self.selected_campaign_report_id_by_operator[operator_id] = report_id
        reports_by_id = campaign.get("_reports_by_id")
        if not isinstance(reports_by_id, dict):
            reports_by_id = {}
            campaign["_reports_by_id"] = reports_by_id
        report = reports_by_id.get(report_id)
        if not isinstance(report, dict):
            try:
                report = await self.fetch_report_by_id(report_id)
            except Exception as exc:
                report = {"report_id": report_id, "error": str(exc)}
            reports_by_id[report_id] = report
        if detail is not None:
            lines = [
                f"Campaign: {campaign.get('title')}",
                f"Campaign ID: {campaign.get('campaign_id')}",
                "",
                *self.format_report_block(report),
            ]
            detail.text = "\n".join(lines)
