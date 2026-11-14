from __future__ import annotations

from datetime import datetime
from typing import Any

from textual.widgets import DataTable, Input, Static, TextArea

from ...client import auth_headers
from ..github import github_ref_number
from ..screens import IssueClearConfirmScreen, PullRequestMergeConfirmScreen
from ._base import TUIController


class GitHubController(TUIController[Any]):
    async def fetch_pull_request_summaries(
        self,
        agent_id: str,
    ) -> dict[int, dict[str, Any]]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/pull-requests",
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("pull request list response was not an object")
        self.pull_request_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "pull_requests"
        }
        if repo_agent_id != agent_id:
            self.pull_request_status_by_agent[repo_agent_id] = (
                self.pull_request_status_by_agent[agent_id]
            )
        pulls = (
            payload.get("pull_requests")
            if isinstance(payload.get("pull_requests"), list)
            else []
        )
        pull_map: dict[int, dict[str, Any]] = {}
        for item in pulls:
            if not isinstance(item, dict):
                continue
            number = github_ref_number(item.get("number"))
            if number is None:
                continue
            pull_map[number] = item
        self.pull_requests_by_agent[agent_id] = pull_map
        if repo_agent_id != agent_id:
            self.pull_requests_by_agent[repo_agent_id] = pull_map
        return pull_map

    async def fetch_pull_request_for_reference(
        self,
        agent_id: str,
        number: int,
    ) -> dict[str, Any]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/pull-requests/{number}",
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        pull = response.json()
        if not isinstance(pull, dict):
            raise ValueError(f"pull request #{number} response was not an object")
        self.pull_requests_by_agent.setdefault(agent_id, {})[number] = pull
        if repo_agent_id != agent_id:
            self.pull_requests_by_agent.setdefault(repo_agent_id, {})[number] = pull
        return pull

    async def pull_request_action_for_agent(
        self,
        agent_id: str,
        action: str,
    ) -> None:
        previous_agent_id = self.selected_agent_id
        if previous_agent_id != agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        self.selected_agent_id = agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        if previous_agent_id != agent_id:
            self.restore_agent_drafts(agent_id)
        self.activate_pull_requests_tab()
        self.update_agent_title()
        if action in {"open", "refresh"}:
            await self.load_pull_requests(agent_id)
        elif action == "review":
            await self.request_pull_request_review(agent_id)
        elif action == "validate":
            await self.request_pull_request_validation(agent_id)
        elif action == "url":
            self.show_selected_pull_request_url(agent_id)
        elif action == "merge":
            self.confirm_merge_pull_request(agent_id)
        else:
            self.notify(f"Unknown PR action: {action}", severity="warning")

    async def load_pull_requests(self, agent_id: str) -> None:
        table = self.query_one_or_none("#pull-requests", DataTable)
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        status_label = self.query_one_or_none("#pull-request-status", Static)
        if table is None or detail is None:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        scope_note = (
            f" via repo context {repo_agent_id}" if repo_agent_id != agent_id else ""
        )
        detail.text = f"Loading pull requests for {agent_id}{scope_note}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/pull-requests",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            detail.text = (
                f"Unable to load pull requests for {agent_id}{scope_note}: {exc}"
            )
            if status_label is not None:
                status_label.update("Pull Requests: unavailable")
            return
        if status_label is not None:
            status_label.update(self.format_pull_request_status_line(payload))
        self.pull_request_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "pull_requests"
        }
        if repo_agent_id != agent_id:
            self.pull_request_status_by_agent[repo_agent_id] = (
                self.pull_request_status_by_agent[agent_id]
            )
        pulls = payload.get("pull_requests") if isinstance(payload, dict) else []
        if not isinstance(pulls, list):
            pulls = []
        self.render_pull_requests(agent_id, pulls)
        if not payload.get("available"):
            detail.text = self.format_pull_request_unavailable(payload)
            return
        if not pulls:
            self.selected_pull_request_number = None
            detail.text = "No open pull requests for this agent repository."
            return
        number = self.selected_pull_request_number
        numbers = {
            int(item.get("number") or 0) for item in pulls if isinstance(item, dict)
        }
        if number not in numbers:
            number = int(pulls[0].get("number") or 0)
        if number:
            await self.select_pull_request(str(number))

    def render_pull_requests(
        self,
        agent_id: str,
        pulls: list[dict[str, Any]],
    ) -> None:
        table = self.query_one("#pull-requests", DataTable)
        position = self.data_table_position(table)
        table.clear()
        pull_map: dict[int, dict[str, Any]] = {}
        for item in pulls:
            number = int(item.get("number") or 0)
            if number <= 0:
                continue
            pull_map[number] = item
            state = "draft" if item.get("is_draft") else str(item.get("state") or "-")
            table.add_row(
                f"#{number}",
                state,
                self.format_pull_request_checks(item.get("checks")),
                str(item.get("title") or ""),
                key=str(number),
            )
        self.pull_requests_by_agent[agent_id] = pull_map
        selected = self.selected_pull_request_number_by_agent.get(agent_id)
        if selected in pull_map:
            try:
                table.move_cursor(
                    row=table.get_row_index(str(selected)),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                pass
        self.restore_data_table_position(table, position)

    async def select_pull_request(self, number_text: str) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            number = int(number_text)
        except ValueError:
            return
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        if detail is None:
            return
        detail.text = f"Loading PR #{number}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            pull = response.json()
        except Exception as exc:
            detail.text = f"Unable to load PR #{number}: {exc}"
            return
        self.selected_pull_request_number = number
        self.selected_pull_request_number_by_agent[agent_id] = number
        self.pull_requests_by_agent.setdefault(agent_id, {})[number] = pull
        if repo_agent_id != agent_id:
            self.selected_pull_request_number_by_agent[repo_agent_id] = number
            self.pull_requests_by_agent.setdefault(repo_agent_id, {})[number] = pull
        detail.text = self.format_pull_request_detail(pull)
        table = self.query_one_or_none("#pull-requests", DataTable)
        if table is not None:
            try:
                table.move_cursor(
                    row=table.get_row_index(str(number)),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    def selected_pull_request_number_for_agent(self, agent_id: str) -> int | None:
        pulls = self.pull_requests_by_agent.get(agent_id, {})
        if self.selected_pull_request_number in pulls:
            return self.selected_pull_request_number
        table = self.query_one_or_none("#pull-requests", DataTable)
        if (
            table is not None
            and table.row_count
            and table.is_valid_row_index(table.cursor_row)
        ):
            try:
                return int(
                    table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
                )
            except Exception:
                return None
        if pulls:
            return next(iter(pulls))
        return None

    def current_pull_request(self, agent_id: str) -> dict[str, Any] | None:
        number = self.selected_pull_request_number_for_agent(agent_id)
        if number is None:
            return None
        return self.pull_requests_by_agent.get(agent_id, {}).get(number)

    async def request_pull_request_review(self, agent_id: str) -> None:
        number = self.selected_pull_request_number_for_agent(agent_id)
        if number is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        use_tmux = self.is_tmux_direct_enabled(agent_id)
        generate_only = use_tmux or repo_agent_id != agent_id
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}/review-request",
                json={"queue": not generate_only},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(f"PR review request failed: {exc}", severity="error")
            return
        if repo_agent_id != agent_id and not use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("PR review prompt was empty.", severity="error")
                return
            command = await self.queue_command(
                agent_id,
                "send_input",
                {
                    "message": prompt,
                    "source": "pull_request_review",
                    "pr_number": number,
                    "repo": payload.get("repo"),
                    "repo_source_agent_id": repo_agent_id,
                },
            )
            self.update_pull_request_action_detail(
                f"Queued PR #{number} review for {agent_id} using repo context "
                f"{repo_agent_id}.\nCommand: {command['command_id']}\n\n"
                f"{self.command_delivery_note(agent_id)}"
            )
            self.notify(f"Queued PR #{number} review for {agent_id}.")
            await self.refresh_events()
            await self.load_thread(agent_id)
            return
        if use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("PR review prompt was empty.", severity="error")
                return
            sent = await self.send_text_to_tmux(agent_id, prompt)
            if not sent:
                return
            self.record_sent_message(agent_id, f"/pr review #{number}")
            await self.record_tmux_joplin_interaction(agent_id, prompt)
            await self.load_tmux_capture(agent_id)
            scope_line = (
                f" using repo context {repo_agent_id}"
                if repo_agent_id != agent_id
                else ""
            )
            self.update_pull_request_action_detail(
                f"Sent PR #{number} review prompt to tmux for {agent_id}"
                f"{scope_line}.\n\n"
                "Watch the tmux stream for Codex output.",
            )
            self.notify(f"Sent PR #{number} review to tmux for {agent_id}.")
            return
        command = payload.get("command") if isinstance(payload, dict) else None
        command_id = "-"
        if isinstance(command, dict):
            command_id = str(command.get("command_id") or "-")
        self.update_pull_request_action_detail(
            f"Queued PR #{number} review for {agent_id}.\n"
            f"Command: {command_id}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify(f"Queued PR #{number} review for {agent_id}.")
        await self.refresh_events()
        await self.load_thread(agent_id)

    async def request_pull_request_validation(self, agent_id: str) -> None:
        number = self.selected_pull_request_number_for_agent(agent_id)
        if number is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        use_tmux = self.is_tmux_direct_enabled(agent_id)
        generate_only = use_tmux or repo_agent_id != agent_id
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}/workerbee-validation-request",
                json={"queue": not generate_only},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(
                f"PR WorkerBee validation request failed: {exc}",
                severity="error",
            )
            return
        if repo_agent_id != agent_id and not use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify(
                    "PR WorkerBee validation prompt was empty.", severity="error"
                )
                return
            command = await self.queue_command(
                agent_id,
                "send_input",
                {
                    "message": prompt,
                    "source": "pull_request_workerbee_validation",
                    "pr_number": number,
                    "repo": payload.get("repo"),
                    "repo_source_agent_id": repo_agent_id,
                },
            )
            self.update_pull_request_action_detail(
                f"Queued PR #{number} WorkerBee validation for {agent_id} using "
                f"repo context {repo_agent_id}.\nCommand: {command['command_id']}\n\n"
                f"{self.command_delivery_note(agent_id)}"
            )
            self.notify(f"Queued PR #{number} validation for {agent_id}.")
            await self.refresh_events()
            await self.load_thread(agent_id)
            return
        if use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify(
                    "PR WorkerBee validation prompt was empty.", severity="error"
                )
                return
            sent = await self.send_text_to_tmux(agent_id, prompt)
            if not sent:
                return
            self.record_sent_message(agent_id, f"/pr validate #{number}")
            await self.record_tmux_joplin_interaction(agent_id, prompt)
            await self.load_tmux_capture(agent_id)
            scope_line = (
                f" using repo context {repo_agent_id}"
                if repo_agent_id != agent_id
                else ""
            )
            self.update_pull_request_action_detail(
                f"Sent PR #{number} WorkerBee validation prompt to tmux for "
                f"{agent_id}{scope_line}.\n\nWatch the tmux stream for Codex output."
            )
            self.notify(f"Sent PR #{number} validation to tmux for {agent_id}.")
            return
        command = payload.get("command") if isinstance(payload, dict) else None
        command_id = "-"
        if isinstance(command, dict):
            command_id = str(command.get("command_id") or "-")
        self.update_pull_request_action_detail(
            f"Queued PR #{number} WorkerBee validation for {agent_id}.\n"
            f"Command: {command_id}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify(f"Queued PR #{number} WorkerBee validation for {agent_id}.")
        await self.refresh_events()
        await self.load_thread(agent_id)

    def update_pull_request_action_detail(self, message: str) -> None:
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        if detail is not None:
            detail.text = message

    async def fetch_issue_summaries(
        self,
        agent_id: str,
    ) -> dict[int, dict[str, Any]]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/issues",
            params={"state": "open", "limit": 30},
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("issue list response was not an object")
        self.issue_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "issues"
        }
        if repo_agent_id != agent_id:
            self.issue_status_by_agent[repo_agent_id] = self.issue_status_by_agent[
                agent_id
            ]
        issues = (
            payload.get("issues") if isinstance(payload.get("issues"), list) else []
        )
        issue_map: dict[int, dict[str, Any]] = {}
        for item in issues:
            if not isinstance(item, dict):
                continue
            number = github_ref_number(item.get("number"))
            if number is None:
                continue
            issue_map[number] = item
        self.issues_by_agent[agent_id] = issue_map
        if repo_agent_id != agent_id:
            self.issues_by_agent[repo_agent_id] = issue_map
        return issue_map

    async def fetch_issue_for_reference(
        self,
        agent_id: str,
        number: int,
    ) -> dict[str, Any]:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        response = await self.api_client().get(
            f"/v1/agents/{repo_agent_id}/issues/{number}",
            headers=auth_headers(self.token),
            timeout=20,
        )
        response.raise_for_status()
        issue = response.json()
        if not isinstance(issue, dict):
            raise ValueError(f"issue #{number} response was not an object")
        self.issues_by_agent.setdefault(agent_id, {})[number] = issue
        if repo_agent_id != agent_id:
            self.issues_by_agent.setdefault(repo_agent_id, {})[number] = issue
        return issue

    async def issue_action_for_agent(
        self,
        agent_id: str,
        action: str,
    ) -> None:
        previous_agent_id = self.selected_agent_id
        if previous_agent_id != agent_id:
            self.save_current_agent_pane_state(previous_agent_id)
        self.selected_agent_id = agent_id
        agent_input = self.query_one_or_none("#agent-id", Input)
        if agent_input is not None:
            agent_input.value = agent_id
        if previous_agent_id != agent_id:
            self.restore_agent_drafts(agent_id)
        self.activate_issues_tab()
        self.update_agent_title()
        if action in {"open", "refresh"}:
            await self.load_issues(agent_id)
        elif action == "mitigate":
            await self.request_issue_mitigation(agent_id)
        elif action == "url":
            self.show_selected_issue_url(agent_id)
        elif action == "clear":
            self.confirm_clear_issue(agent_id)
        else:
            self.notify(f"Unknown issue action: {action}", severity="warning")

    async def load_issues(self, agent_id: str) -> None:
        table = self.query_one_or_none("#issues", DataTable)
        detail = self.query_one_or_none("#issue-detail", TextArea)
        status_label = self.query_one_or_none("#issue-status", Static)
        if table is None or detail is None:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        scope_note = (
            f" via repo context {repo_agent_id}" if repo_agent_id != agent_id else ""
        )
        detail.text = f"Loading GitHub issues for {agent_id}{scope_note}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/issues",
                params={"state": "open", "limit": 30},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            detail.text = (
                f"Unable to load GitHub issues for {agent_id}{scope_note}: {exc}"
            )
            if status_label is not None:
                status_label.update("Issues: unavailable")
            return
        if status_label is not None:
            status_label.update(self.format_issue_status_line(payload))
        self.issue_status_by_agent[agent_id] = {
            key: value for key, value in payload.items() if key != "issues"
        }
        if repo_agent_id != agent_id:
            self.issue_status_by_agent[repo_agent_id] = self.issue_status_by_agent[
                agent_id
            ]
        issues = payload.get("issues") if isinstance(payload, dict) else []
        if not isinstance(issues, list):
            issues = []
        self.render_issues(agent_id, issues)
        if not payload.get("available"):
            detail.text = self.format_issue_unavailable(payload)
            return
        if not issues:
            self.selected_issue_number = None
            self.selected_issue_number_by_agent.pop(agent_id, None)
            detail.text = "No open GitHub issues for this agent repository."
            return
        number = self.selected_issue_number
        numbers = {
            int(item.get("number") or 0) for item in issues if isinstance(item, dict)
        }
        if number not in numbers:
            number = next(iter(numbers))
        if number:
            await self.select_issue(str(number))

    def render_issues(
        self,
        agent_id: str,
        issues: list[dict[str, Any]],
    ) -> None:
        table = self.query_one("#issues", DataTable)
        position = self.data_table_position(table)
        table.clear()
        issue_map: dict[int, dict[str, Any]] = {}
        for item in issues:
            number = int(item.get("number") or 0)
            if number <= 0:
                continue
            issue_map[number] = item
            labels = ", ".join(item.get("labels") or [])
            table.add_row(
                f"#{number}",
                str(item.get("state") or "-"),
                labels or "-",
                str(item.get("title") or ""),
                self.format_issue_time(item.get("updated_at")),
                key=str(number),
            )
        self.issues_by_agent[agent_id] = issue_map
        selected = self.selected_issue_number_by_agent.get(agent_id)
        if selected in issue_map:
            try:
                table.move_cursor(
                    row=table.get_row_index(str(selected)),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                pass
        self.restore_data_table_position(table, position)

    async def select_issue(self, number_text: str) -> None:
        agent_id = self.selected_agent_id
        if not agent_id:
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            number = int(number_text)
        except ValueError:
            return
        detail = self.query_one_or_none("#issue-detail", TextArea)
        if detail is None:
            return
        detail.text = f"Loading issue #{number}..."
        try:
            response = await self.api_client().get(
                f"/v1/agents/{repo_agent_id}/issues/{number}",
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            issue = response.json()
        except Exception as exc:
            detail.text = f"Unable to load issue #{number}: {exc}"
            return
        self.selected_issue_number = number
        self.selected_issue_number_by_agent[agent_id] = number
        self.issues_by_agent.setdefault(agent_id, {})[number] = issue
        if repo_agent_id != agent_id:
            self.selected_issue_number_by_agent[repo_agent_id] = number
            self.issues_by_agent.setdefault(repo_agent_id, {})[number] = issue
        detail.text = self.format_issue_detail(issue)
        table = self.query_one_or_none("#issues", DataTable)
        if table is not None:
            try:
                table.move_cursor(
                    row=table.get_row_index(str(number)),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    def selected_issue_number_for_agent(self, agent_id: str) -> int | None:
        issues = self.issues_by_agent.get(agent_id, {})
        if self.selected_issue_number in issues:
            return self.selected_issue_number
        table = self.query_one_or_none("#issues", DataTable)
        if (
            table is not None
            and table.row_count
            and table.is_valid_row_index(table.cursor_row)
        ):
            try:
                return int(
                    table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value
                )
            except Exception:
                return None
        if issues:
            return next(iter(issues))
        return None

    def current_issue(self, agent_id: str) -> dict[str, Any] | None:
        number = self.selected_issue_number_for_agent(agent_id)
        if number is None:
            return None
        return self.issues_by_agent.get(agent_id, {}).get(number)

    async def request_issue_mitigation(self, agent_id: str) -> None:
        number = self.selected_issue_number_for_agent(agent_id)
        if number is None:
            self.notify("Select an issue first.", severity="warning")
            return
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        use_tmux = self.is_tmux_direct_enabled(agent_id)
        generate_only = use_tmux or repo_agent_id != agent_id
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/issues/{number}/mitigation-request",
                json={"queue": not generate_only},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(f"Issue mitigation request failed: {exc}", severity="error")
            return
        if repo_agent_id != agent_id and not use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("Issue mitigation prompt was empty.", severity="error")
                return
            command = await self.queue_command(
                agent_id,
                "send_input",
                {
                    "message": prompt,
                    "source": "issue_mitigation",
                    "issue_number": number,
                    "repo": payload.get("repo"),
                    "repo_source_agent_id": repo_agent_id,
                },
            )
            self.update_issue_action_detail(
                f"Queued issue #{number} mitigation for {agent_id} using repo "
                f"context {repo_agent_id}.\nCommand: {command['command_id']}\n\n"
                f"{self.command_delivery_note(agent_id)}"
            )
            self.notify(f"Queued issue #{number} mitigation for {agent_id}.")
            await self.refresh_events()
            await self.load_thread(agent_id)
            return
        if use_tmux:
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                self.notify("Issue mitigation prompt was empty.", severity="error")
                return
            sent = await self.send_text_to_tmux(agent_id, prompt)
            if not sent:
                return
            self.record_sent_message(agent_id, f"/issue mitigate #{number}")
            await self.record_tmux_joplin_interaction(agent_id, prompt)
            await self.load_tmux_capture(agent_id)
            scope_line = (
                f" using repo context {repo_agent_id}"
                if repo_agent_id != agent_id
                else ""
            )
            self.update_issue_action_detail(
                f"Sent issue #{number} mitigation prompt to tmux for {agent_id}"
                f"{scope_line}.\n\n"
                "Watch the tmux stream for Codex output."
            )
            self.notify(f"Sent issue #{number} mitigation to tmux for {agent_id}.")
            return
        command = payload.get("command") if isinstance(payload, dict) else None
        command_id = "-"
        if isinstance(command, dict):
            command_id = str(command.get("command_id") or "-")
        self.update_issue_action_detail(
            f"Queued issue #{number} mitigation for {agent_id}.\n"
            f"Command: {command_id}\n\n"
            f"{self.command_delivery_note(agent_id)}"
        )
        self.notify(f"Queued issue #{number} mitigation for {agent_id}.")
        await self.refresh_events()
        await self.load_thread(agent_id)

    def update_issue_action_detail(self, message: str) -> None:
        detail = self.query_one_or_none("#issue-detail", TextArea)
        if detail is not None:
            detail.text = message

    def show_selected_issue_url(self, agent_id: str) -> None:
        issue = self.current_issue(agent_id)
        if issue is None:
            self.notify("Select an issue first.", severity="warning")
            return
        url = str(issue.get("url") or "")
        if not url:
            self.notify("Selected issue has no URL.", severity="warning")
            return
        self.notify(url)
        detail = self.query_one_or_none("#issue-detail", TextArea)
        if detail is not None:
            detail.text = self.format_issue_detail(issue)

    def confirm_clear_issue(self, agent_id: str) -> None:
        issue = self.current_issue(agent_id)
        if issue is None:
            self.notify("Select an issue first.", severity="warning")
            return
        number = int(issue.get("number") or 0)
        if number <= 0:
            self.notify("Selected issue number is invalid.", severity="warning")
            return
        self.push_screen(
            IssueClearConfirmScreen(
                agent_id=agent_id,
                number=number,
                title=str(issue.get("title") or ""),
            )
        )

    async def clear_issue(
        self,
        agent_id: str,
        number: int,
        *,
        comment: str,
        confirm: str,
    ) -> None:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/issues/{number}/clear",
                json={"comment": comment, "confirm": confirm},
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Issue clear failed: {exc}", severity="error")
            self.update_issue_action_detail(f"Issue #{number} clear failed:\n{exc}")
            return
        self.notify(f"Cleared issue #{number}.")
        await self.load_issues(agent_id)

    def format_issue_status_line(self, status: dict[str, Any]) -> str:
        if not status.get("configured"):
            return "Issues: disabled"
        if not status.get("available"):
            return "Issues: unavailable"
        repo = status.get("repo") or "-"
        close = "close on" if status.get("close_enabled") else "close off"
        return f"Issues: {repo} ({close})"

    def format_issue_unavailable(self, status: dict[str, Any]) -> str:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        lines = [
            "GitHub Issues unavailable",
            f"Code: {error.get('code', 'ISSUES_UNAVAILABLE')}",
            f"Message: {error.get('message', '')}",
        ]
        details = error.get("details")
        if isinstance(details, dict) and details.get("repo"):
            lines.append(f"Repo: {details.get('repo')}")
        return "\n".join(lines)

    def format_issue_detail(self, issue: dict[str, Any]) -> str:
        labels = ", ".join(issue.get("labels") or []) or "-"
        assignees = ", ".join(issue.get("assignees") or []) or "-"
        comments = (
            issue.get("comments") if isinstance(issue.get("comments"), list) else []
        )
        lines = [
            f"Issue #{issue.get('number')} - {issue.get('title')}",
            f"Repo: {issue.get('repo') or '-'}",
            f"State: {issue.get('state') or '-'}",
            f"Author: {issue.get('author') or '-'}",
            f"Labels: {labels}",
            f"Assignees: {assignees}",
            f"Milestone: {issue.get('milestone') or '-'}",
            f"Updated: {self.format_issue_time(issue.get('updated_at')) or '-'}",
            f"URL: {issue.get('url') or '-'}",
            "",
            str(issue.get("body") or "").strip() or "(No issue body.)",
        ]
        if comments:
            lines.extend(["", "Comments:"])
            for comment in comments[-5:]:
                if not isinstance(comment, dict):
                    continue
                body = str(comment.get("body") or "").strip()
                if len(body) > 600:
                    body = f"{body[:600]}..."
                lines.extend(
                    [
                        "",
                        f"- {comment.get('author') or '-'} at {self.format_issue_time(comment.get('created_at')) or '-'}",
                        body or "(empty comment)",
                    ]
                )
        return "\n".join(lines)

    def format_issue_time(self, value: Any) -> str:
        if isinstance(value, str) and value.strip():
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value[:10]
            return parsed.strftime("%Y-%m-%d")
        return self.format_joplin_time(value)

    def show_selected_pull_request_url(self, agent_id: str) -> None:
        pull = self.current_pull_request(agent_id)
        if pull is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        url = str(pull.get("url") or "")
        if not url:
            self.notify("Selected PR has no URL.", severity="warning")
            return
        self.notify(url)
        detail = self.query_one_or_none("#pull-request-detail", TextArea)
        if detail is not None:
            detail.text = self.format_pull_request_detail(pull)

    def confirm_merge_pull_request(self, agent_id: str) -> None:
        pull = self.current_pull_request(agent_id)
        if pull is None:
            self.notify("Select a pull request first.", severity="warning")
            return
        number = int(pull.get("number") or 0)
        if number <= 0:
            self.notify("Selected PR number is invalid.", severity="warning")
            return
        self.push_screen(
            PullRequestMergeConfirmScreen(
                agent_id=agent_id,
                number=number,
                title=str(pull.get("title") or ""),
            )
        )

    async def merge_pull_request(
        self,
        agent_id: str,
        number: int,
        *,
        method: str,
        confirm: str,
    ) -> None:
        repo_agent_id = self.repo_scope_agent_id(agent_id)
        try:
            response = await self.api_client().post(
                f"/v1/agents/{repo_agent_id}/pull-requests/{number}/merge",
                json={"method": method, "confirm": confirm},
                headers=auth_headers(self.token),
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            self.notify(f"PR merge failed: {exc}", severity="error")
            return
        self.notify(f"Merged PR #{number} with {result.get('method') or method}.")
        await self.load_pull_requests(agent_id)

    def format_pull_request_status_line(self, status: dict[str, Any]) -> str:
        if status.get("available"):
            repo = status.get("repo") or "-"
            merge = "on" if status.get("merge_enabled") else "off"
            return f"Pull Requests: {repo} | Merge: {merge}"
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = error.get("code") or "PR_UNAVAILABLE"
        return f"Pull Requests: {code}"

    def format_pull_request_unavailable(self, status: dict[str, Any]) -> str:
        lines = ["Pull request integration is unavailable."]
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        if error:
            lines.extend(
                [
                    "",
                    f"Code: {error.get('code', 'PR_UNAVAILABLE')}",
                    f"Message: {error.get('message', '')}",
                ]
            )
        lines.extend(
            [
                "",
                f"GitHub CLI: {status.get('gh_bin') or '-'}",
                f"Cwd: {status.get('cwd') or '-'}",
            ]
        )
        return "\n".join(lines)

    def format_pull_request_detail(self, pull: dict[str, Any]) -> str:
        files = pull.get("files") if isinstance(pull.get("files"), list) else []
        file_lines = []
        for item in files[:40]:
            if isinstance(item, dict):
                additions = item.get("additions")
                deletions = item.get("deletions")
                changed = ""
                if additions is not None or deletions is not None:
                    changed = f" (+{additions or 0}/-{deletions or 0})"
                file_lines.append(
                    f"- {item.get('path') or item.get('filename')}{changed}"
                )
        commits = pull.get("commits") if isinstance(pull.get("commits"), list) else []
        draft = " (draft)" if pull.get("is_draft") else ""
        mergeable = pull.get("mergeable") if pull.get("mergeable") is not None else "-"
        lines = [
            f"PR #{pull.get('number')} - {pull.get('title')}",
            f"Repo: {pull.get('repo') or '-'}",
            f"URL: {pull.get('url') or '-'}",
            f"State: {pull.get('state') or '-'}{draft}",
            f"Author: {pull.get('author') or '-'}",
            f"Branch: {pull.get('head_ref') or '-'} -> {pull.get('base_ref') or '-'}",
            f"Updated: {pull.get('updated_at') or '-'}",
            f"Review: {pull.get('review_decision') or '-'}",
            f"Mergeable: {mergeable}",
            f"Merge State: {pull.get('merge_state_status') or '-'}",
            f"Checks: {self.format_pull_request_checks(pull.get('checks'))}",
            f"Labels: {', '.join(pull.get('labels') or []) or '-'}",
            "",
            "Files",
            "\n".join(file_lines) if file_lines else "-",
            "",
            f"Commits: {len(commits)}",
            "",
            "Body",
            str(pull.get("body") or "-"),
        ]
        return "\n".join(lines)

    def format_pull_request_checks(self, checks: object) -> str:
        if not isinstance(checks, dict):
            return "-"
        total = int(checks.get("total") or 0)
        if total <= 0:
            return "-"
        failed = int(checks.get("failed") or 0)
        pending = int(checks.get("pending") or 0)
        success = int(checks.get("success") or 0)
        unknown = int(checks.get("unknown") or 0)
        if failed:
            return f"{failed} fail/{total}"
        if pending:
            return f"{pending} pending/{total}"
        if unknown:
            return f"{success} ok/{unknown} unk/{total}"
        return f"{success} ok/{total}"
