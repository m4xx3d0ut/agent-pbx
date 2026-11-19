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
        target = self.transaction.get("target_runtime") or {"backend": self.transaction.get("target")}
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
                        f"Transaction: {self.transaction.get('transaction_id') or '-'}",
                        f"Current: {before.get('backend') or '-'} g{before.get('generation', '-')} "
                        f"instance {before.get('instance_id', '-')}",
                        f"Target: {target.get('backend') or '-'} g{target.get('generation', '-')} "
                        f"instance {target.get('instance_id', '-')} · {target.get('source_mode', '-')}",
                        f"Target fingerprint: {target.get('fingerprint', '-')}",
                        "Existing leases for an older generation require rebind.",
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


class WorkerBeeSnapshotScreen(ModalScreen[None]):
    """Edit the content copied into one appliance-backed workspace."""

    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, policy_view: dict[str, Any]) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.policy_view = policy_view

    def compose(self) -> ComposeResult:
        effective = self.policy_view.get("effective") or {}
        admin = self.policy_view.get("admin") or {}
        applicable = bool(self.policy_view.get("applicable", True))
        read_only = (
            str(admin.get("control_mode") or "editable") != "editable" or not applicable
        )
        includes = ", ".join(str(item) for item in effective.get("include_paths") or ())
        backend = str(self.policy_view.get("backend") or "unselected")
        with Vertical(id="workerbee-snapshot-panel"):
            yield Static("WorkerBee Appliance Content", id="workerbee-snapshot-title")
            yield Static(
                (
                    f"{self.policy_view.get('workflow_label') or effective.get('profile_label') or 'Snapshot policy'} · "
                    f"backend={backend} · control={admin.get('control_mode') or '-'} · "
                    f"ceiling={admin.get('policy_ceiling') or '-'}"
                ),
                id="workerbee-snapshot-posture",
            )
            if not applicable:
                yield Static(
                    "The selected OSS backend uses the host checkout directly; no appliance snapshot is created.",
                    id="workerbee-snapshot-direct-host",
                )
            yield Select(
                [
                    ("Tracked content", "tracked"),
                    ("Tracked + approved generated paths", "approved-paths"),
                    ("Safe working tree", "safe-working-tree"),
                ],
                value=str(effective.get("policy") or "tracked"),
                id="workerbee-snapshot-policy",
                disabled=read_only,
            )
            yield Input(
                value=includes,
                placeholder="Approved source-relative generated paths, comma-separated",
                id="workerbee-snapshot-includes",
                disabled=read_only,
            )
            yield Input(
                value=str(effective.get("max_bytes") or ""),
                placeholder="Maximum total bytes",
                id="workerbee-snapshot-max-bytes",
                disabled=read_only,
            )
            yield Input(
                value=str(effective.get("max_files") or ""),
                placeholder="Maximum file count",
                id="workerbee-snapshot-max-files",
                disabled=read_only,
            )
            yield Input(
                value=str(effective.get("max_file_bytes") or ""),
                placeholder="Maximum bytes per file",
                id="workerbee-snapshot-max-file-bytes",
                disabled=read_only,
            )
            yield Input(
                value=str(effective.get("profile_label") or ""),
                placeholder="Policy label",
                id="workerbee-snapshot-label",
                disabled=read_only,
            )
            yield Static(
                "Preview resolves generated roots without changing the active lease or workspace.",
                id="workerbee-snapshot-summary",
            )
            with Horizontal(id="workerbee-snapshot-actions"):
                yield Button(
                    "Save Agent", id="workerbee-snapshot-save", disabled=read_only
                )
                yield Button(
                    "Save Root Default",
                    id="workerbee-snapshot-save-global",
                    disabled=read_only,
                )
                yield Button(
                    "Inherit", id="workerbee-snapshot-inherit", disabled=read_only
                )
                yield Button(
                    "Preview",
                    id="workerbee-snapshot-preview",
                    disabled=not applicable,
                )
                yield Button("Close", id="workerbee-snapshot-close")

    @staticmethod
    def _optional_int(value: str) -> int | None:
        selected = value.strip()
        return int(selected) if selected else None

    def policy_payload(self) -> dict[str, Any]:
        includes = self.query_one("#workerbee-snapshot-includes", Input).value
        effective = self.policy_view.get("effective") or {}
        return {
            "source_root": effective.get("source_root"),
            "policy": str(self.query_one("#workerbee-snapshot-policy", Select).value),
            "include_paths": [
                part.strip() for part in includes.split(",") if part.strip()
            ],
            "max_bytes": self._optional_int(
                self.query_one("#workerbee-snapshot-max-bytes", Input).value
            ),
            "max_files": self._optional_int(
                self.query_one("#workerbee-snapshot-max-files", Input).value
            ),
            "max_file_bytes": self._optional_int(
                self.query_one("#workerbee-snapshot-max-file-bytes", Input).value
            ),
            "profile_label": self.query_one(
                "#workerbee-snapshot-label", Input
            ).value.strip()
            or None,
        }

    def show_preview(self, payload: dict[str, Any]) -> None:
        selection = (
            payload.get("selection")
            if isinstance(payload.get("selection"), dict)
            else {}
        )
        excluded = selection.get("excluded_paths") or ()
        self.query_one("#workerbee-snapshot-summary", Static).update(
            f"Preview: {selection.get('policy') or '-'} · "
            f"{selection.get('file_count') or 0} files · "
            f"{selection.get('total_bytes') or 0} bytes · "
            f"{len(selection.get('include_paths') or ())} generated roots · "
            f"{len(excluded)} excluded"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button_id = event.button.id
        if button_id == "workerbee-snapshot-close":
            self.dismiss()
        elif button_id == "workerbee-snapshot-save":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_workerbee_snapshot_policy(self),  # type: ignore[attr-defined]
                name="workerbee-snapshot-save",
                exclusive=True,
            )
        elif button_id == "workerbee-snapshot-save-global":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_workerbee_snapshot_policy(  # type: ignore[attr-defined]
                    self, global_policy=True
                ),
                name="workerbee-snapshot-save-global",
                exclusive=True,
            )
        elif button_id == "workerbee-snapshot-inherit":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.inherit_workerbee_snapshot_policy(self),  # type: ignore[attr-defined]
                name="workerbee-snapshot-inherit",
                exclusive=True,
            )
        elif button_id == "workerbee-snapshot-preview":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.preview_workerbee_snapshot_policy(self),  # type: ignore[attr-defined]
                name="workerbee-snapshot-preview",
                exclusive=True,
            )


class WorkerBeeAccessScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, *, agent_id: str, access_view: dict[str, Any]) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.access_view = access_view

    def compose(self) -> ComposeResult:
        review_readonly = bool(self.access_view.get("review_readonly"))
        access_control = str(self.access_view.get("control_mode") or "editable")
        source_control = str(self.access_view.get("source_mode_control") or "read_only")
        lane_control = str(
            self.access_view.get("execution_lane_control") or "read_only"
        )
        access_read_only = access_control != "editable" or review_readonly
        source_read_only = source_control != "editable" or review_readonly
        access_rank = {
            "observe-local": 0,
            "local-deploy": 1,
            "trusted-workbench": 2,
        }
        ceiling = str(self.access_view.get("profile_ceiling") or "observe-local")
        ceiling_rank = access_rank.get(ceiling, 0)
        trusted_label = str(
            self.access_view.get("trusted_workbench_label") or "Trusted Workbench"
        )
        access_options = [
            ("Inspect runtime state", "observe-local"),
            ("Build and deploy in this workspace", "local-deploy"),
            (trusted_label, "trusted-workbench"),
        ]
        access_options = [
            option
            for option in access_options
            if access_rank.get(str(option[1]), 99) <= ceiling_rank
        ]
        if review_readonly:
            access_options = [("Review read-only", "review-readonly")]
        source_labels = {
            "native-direct": "Native direct checkout",
            "snapshot-import": "Content-addressed snapshot import",
            "trusted-direct-bind": "Trusted direct bind",
            "managed-clone": "Managed appliance clone",
        }
        source_ceiling = self.access_view.get("source_mode_ceiling")
        if not isinstance(source_ceiling, list):
            source_ceiling = []
        source_options = [
            (source_labels.get(str(mode), str(mode)), str(mode))
            for mode in source_ceiling
        ]
        selected_source = str(
            self.access_view.get("source_mode")
            or self.access_view.get("source_mode_preference")
            or "snapshot-import"
        )
        if selected_source not in {str(value) for _label, value in source_options}:
            source_options.append(
                (source_labels.get(selected_source, selected_source), selected_source)
            )
        selected_profile = str(
            self.access_view.get("effective_profile") or "observe-local"
        )
        if review_readonly:
            selected_profile = "review-readonly"
        with Vertical(id="workerbee-access-panel"):
            yield Static("WorkerBee Runtime Policy", id="workerbee-access-title")
            yield Static(
                f"backend={self.access_view.get('selected_backend') or 'unselected'} · "
                f"access ceiling={ceiling} · access={access_control} · "
                f"source={source_control} · lane={lane_control}",
                id="workerbee-access-posture",
            )
            tool_contract = self.access_view.get("tool_surface_contract")
            if isinstance(tool_contract, dict):
                requested = tool_contract.get("required_tools")
                optional = tool_contract.get("reviewed_optional_tools")
                yield Static(
                    f"requested={len(requested) if isinstance(requested, list) else 0} · "
                    f"backend optional={len(optional) if isinstance(optional, list) else 0} · "
                    "unknown=quarantined/denied · effective catalog: run doctor",
                    id="workerbee-tool-surface-summary",
                )
            if access_control != "hidden":
                yield Select(
                    access_options,
                    value=selected_profile,
                    id="workerbee-access-profile",
                    disabled=access_read_only,
                )
            if source_control != "hidden":
                yield Select(
                    source_options,
                    value=selected_source,
                    id="workerbee-source-mode",
                    disabled=source_read_only,
                )
            if lane_control != "hidden":
                yield Select(
                    [
                        ("Ordinary generation-bound gateway", "interactive-gateway"),
                        ("Controlled run (root-owned approval)", "controlled-run"),
                    ],
                    value=str(
                        self.access_view.get("execution_lane") or "interactive-gateway"
                    ),
                    id="workerbee-execution-lane",
                    disabled=True,
                )
            yield Static(
                (
                    "Review forks remain read-only."
                    if review_readonly
                    else "Access and source changes apply after a safe Agent/Operator restart or resume. Controlled runs require separate root approval."
                ),
                id="workerbee-access-summary",
            )
            with Horizontal(id="workerbee-access-actions"):
                yield Button(
                    "Save",
                    id="workerbee-access-save",
                    disabled=access_read_only and source_read_only,
                    variant="primary",
                )
                yield Button("Close", id="workerbee-access-close")

    def selected_profile(self) -> str:
        if str(self.access_view.get("control_mode") or "editable") == "hidden":
            return str(self.access_view.get("effective_profile") or "observe-local")
        selected = self.query_one("#workerbee-access-profile", Select)
        return str(selected.value)

    def policy_payload(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        if str(
            self.access_view.get("control_mode") or "editable"
        ) == "editable" and not self.access_view.get("review_readonly"):
            access = self.query_one("#workerbee-access-profile", Select)
            payload["access_profile"] = str(access.value)
        if str(
            self.access_view.get("source_mode_control") or "read_only"
        ) == "editable" and not self.access_view.get("review_readonly"):
            source = self.query_one("#workerbee-source-mode", Select)
            payload["source_mode"] = str(source.value)
        return payload

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "workerbee-access-close":
            self.dismiss()
        elif event.button.id == "workerbee-access-save":
            self.app.run_worker(  # type: ignore[attr-defined]
                self.app.save_workerbee_access_profile(self),  # type: ignore[attr-defined]
                name="workerbee-access-save",
                exclusive=True,
            )
