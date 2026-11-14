from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static


class WorkerBeeSwitchConfirmScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        transaction: dict[str, Any],
        action: str = "apply",
    ) -> None:
        super().__init__()
        self.transaction = transaction
        self.action = action

    def expected_confirmation(self) -> str:
        if self.action == "rollback":
            return f"rollback {self.transaction.get('transaction_id')}"
        return f"switch {self.transaction.get('target')}"

    def compose(self) -> ComposeResult:
        before = self.transaction.get("before") or {}
        target = str(self.transaction.get("target") or "-")
        expected = self.expected_confirmation()
        title = (
            "Rollback WorkerBee Backend"
            if self.action == "rollback"
            else "Switch WorkerBee Backend"
        )
        with Vertical(id="workerbee-switch-panel"):
            yield Static(title, id="workerbee-switch-title")
            yield Static(
                "\n".join(
                    (
                        f"Current: {before.get('backend') or '-'}",
                        f"Target: {target}",
                        "Existing Codex sessions remain pinned; this affects future sessions.",
                        f'Type "{expected}" to confirm.',
                    )
                ),
                id="workerbee-switch-summary",
            )
            yield Input(placeholder=expected, id="workerbee-switch-confirm")
            with Horizontal(id="workerbee-switch-actions"):
                yield Button(
                    "Rollback" if self.action == "rollback" else "Apply Switch",
                    id="workerbee-switch-run",
                    variant="error",
                )
                yield Button("Cancel", id="workerbee-switch-cancel")

    def submit(self) -> None:
        confirmation = self.query_one("#workerbee-switch-confirm", Input).value
        if self.action == "rollback":
            work = self.app.rollback_workerbee_switch(  # type: ignore[attr-defined]
                self.transaction,
                confirmation=confirmation,
            )
        else:
            work = self.app.apply_workerbee_switch(  # type: ignore[attr-defined]
                self.transaction,
                confirmation=confirmation,
            )
        self.app.run_worker(  # type: ignore[attr-defined]
            work,
            name=f"workerbee-switch-{self.action}",
            exclusive=True,
        )
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "workerbee-switch-cancel":
            event.stop()
            self.dismiss()
            return
        if event.button.id == "workerbee-switch-run":
            event.stop()
            self.submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "workerbee-switch-confirm":
            event.stop()
            self.submit()


class WorkerBeeWorkspaceScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(
        self,
        *,
        agent_id: str,
        policy_view: dict[str, Any],
        transitions: list[dict[str, Any]],
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.policy_view = policy_view
        self.transitions = transitions
        self.preview_payload: dict[str, Any] | None = None

    def compose(self) -> ComposeResult:
        effective = self.policy_view.get("effective") or {}
        admin = self.policy_view.get("admin") or {}
        read_only = str(admin.get("control_mode") or "editable") != "editable"
        roots = ", ".join(str(item) for item in effective.get("allowed_roots") or ())
        with Vertical(id="workerbee-workspace-panel"):
            yield Static("WorkerBee Workspace", id="workerbee-workspace-title")
            yield Static(
                f"{effective.get('profile_label') or 'Workspace policy'} · "
                f"control={admin.get('control_mode') or '-'} · "
                f"ceiling={admin.get('policy_ceiling') or '-'}",
                id="workerbee-workspace-posture",
            )
            yield Select(
                [
                    ("Pinned checkout", "pinned"),
                    ("Same Git repository", "same-repository"),
                    ("Scoped workbench", "scoped-workbench"),
                ],
                value=str(effective.get("policy") or "pinned"),
                id="workerbee-workspace-policy",
                disabled=read_only,
            )
            yield Select(
                [
                    ("Approval required", "approval"),
                    ("Automatic", "automatic"),
                    ("Disabled", "disabled"),
                ],
                value=str(effective.get("request_mode") or "approval"),
                id="workerbee-workspace-request-mode",
                disabled=read_only,
            )
            yield Input(
                value=roots,
                placeholder="Allowed roots, comma-separated",
                id="workerbee-workspace-roots",
                disabled=read_only,
            )
            yield Input(
                value=str(effective.get("managed_scratch_root") or ""),
                placeholder="Managed scratch root (optional)",
                id="workerbee-workspace-scratch",
                disabled=read_only,
            )
            yield Input(
                value=str(effective.get("profile_label") or ""),
                placeholder="Profile label",
                id="workerbee-workspace-label",
                disabled=read_only,
            )
            yield Input(
                placeholder="Git checkout or subdirectory to bind",
                id="workerbee-workspace-target",
            )
            yield Input(
                placeholder="Scenario label (optional)",
                id="workerbee-workspace-scenario",
            )
            yield Static(self.transition_summary(), id="workerbee-workspace-summary")
            with Horizontal(id="workerbee-workspace-policy-actions"):
                yield Button(
                    "Save Agent", id="workerbee-workspace-save", disabled=read_only
                )
                yield Button(
                    "Save Global",
                    id="workerbee-workspace-save-global",
                    disabled=read_only,
                )
                yield Button(
                    "Inherit", id="workerbee-workspace-inherit", disabled=read_only
                )
                yield Button("Preview", id="workerbee-workspace-preview")
                yield Button(
                    "Bind",
                    id="workerbee-workspace-bind",
                    disabled=True,
                    variant="primary",
                )
            with Horizontal(id="workerbee-workspace-transition-actions"):
                yield Button("Approve Pending", id="workerbee-workspace-approve")
                yield Button("Deny Pending", id="workerbee-workspace-deny")
                yield Button("Revert Latest", id="workerbee-workspace-revert")
                yield Button("Close", id="workerbee-workspace-close")

    def transition_summary(self) -> str:
        latest = self.transitions[0] if self.transitions else None
        if not latest:
            return "No workspace transitions recorded."
        return (
            f"Latest: {latest.get('status') or '-'} · "
            f"{latest.get('runtime_project') or '-'} · "
            f"{latest.get('working_subpath') or '.'}"
        )

    def policy_payload(self) -> dict[str, Any]:
        policy = self.query_one("#workerbee-workspace-policy", Select).value
        request_mode = self.query_one("#workerbee-workspace-request-mode", Select).value
        roots_text = self.query_one("#workerbee-workspace-roots", Input).value
        return {
            "policy": str(policy),
            "request_mode": str(request_mode),
            "allowed_roots": [
                part.strip() for part in roots_text.split(",") if part.strip()
            ],
            "managed_scratch_root": self.query_one(
                "#workerbee-workspace-scratch", Input
            ).value.strip()
            or None,
            "profile_label": self.query_one(
                "#workerbee-workspace-label", Input
            ).value.strip()
            or None,
        }

    def target_payload(self) -> dict[str, Any]:
        return {
            "cwd": self.query_one("#workerbee-workspace-target", Input).value.strip(),
            "project": self.query_one(
                "#workerbee-workspace-scenario", Input
            ).value.strip()
            or None,
        }

    def show_preview(self, payload: dict[str, Any]) -> None:
        self.preview_payload = {"request": self.target_payload(), "preview": payload}
        target = (
            payload.get("target") if isinstance(payload.get("target"), dict) else {}
        )
        self.query_one("#workerbee-workspace-summary", Static).update(
            f"Preview: {target.get('source_root') or '-'} · "
            f"{target.get('working_subpath') or '.'} · "
            f"{target.get('runtime_project') or '-'} · "
            f"{target.get('request_mode') or '-'}"
        )
        self.query_one("#workerbee-workspace-bind", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "workerbee-workspace-close":
            self.dismiss()
        elif button_id == "workerbee-workspace-save":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_workerbee_workspace_policy(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-policy-save",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-save-global":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_workerbee_workspace_policy(  # type: ignore[attr-defined]
                    self, global_policy=True
                ),
                name="workerbee-workspace-global-policy-save",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-inherit":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.inherit_workerbee_workspace_policy(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-policy-inherit",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-preview":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.preview_workerbee_workspace_target(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-preview",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-bind":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.apply_workerbee_workspace_target(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-bind",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-approve":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.approve_workerbee_workspace_transition(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-approve",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-deny":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.deny_workerbee_workspace_transition(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-deny",
                exclusive=True,
            )
        elif button_id == "workerbee-workspace-revert":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.revert_workerbee_workspace_transition(self),  # type: ignore[attr-defined]
                name="workerbee-workspace-revert",
                exclusive=True,
            )
