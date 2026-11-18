"""Managed project launch and runtime migration modal presentation."""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Select, Static

from ...codex.profiles import (
    DEFAULT_CALLER_PROFILE_ID,
    MANAGED_CODEX_PROFILE_ORDER,
)
from ..preferences import TMUX_RUNTIME_SERVER_MODE_CHOICES


class ManagedProjectPickerScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, projects: list[dict[str, Any]], roots: list[str]) -> None:
        super().__init__()
        self.projects = projects
        self.roots = roots
        self.selected_path = ""
        self.selected_owners: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="managed-project-picker"):
            yield Static("Launch Project Workspace", id="managed-project-picker-title")
            yield Static(
                "Approved roots: " + (", ".join(self.roots) or "none"),
                id="managed-project-roots",
            )
            yield DataTable(
                id="managed-projects", cursor_type="row", show_row_labels=False
            )
            yield Input(placeholder="Optional Agent ID", id="managed-agent-id")
            yield Select(
                (
                    ("Agent", "agent"),
                    ("Agent + root Operator", "agent_operator"),
                ),
                value="agent",
                allow_blank=False,
                id="managed-launch-mode",
            )
            yield Select(
                tuple(
                    (profile.label, profile.profile_id)
                    for profile in MANAGED_CODEX_PROFILE_ORDER
                    if not profile.elevated and not profile.migration_only
                ),
                value=DEFAULT_CALLER_PROFILE_ID,
                allow_blank=False,
                id="managed-agent-profile",
            )
            yield Select(
                TMUX_RUNTIME_SERVER_MODE_CHOICES,
                value=self.app.tmux_runtime_server_mode,  # type: ignore[attr-defined]
                allow_blank=False,
                id="managed-agent-runtime-mode",
            )
            with Horizontal(id="managed-project-actions"):
                yield Button("Launch", id="managed-project-launch", variant="primary")
                yield Button("Close", id="managed-project-close")

    def on_mount(self) -> None:
        table = self.query_one("#managed-projects", DataTable)
        table.add_columns("Available", "Project", "Branch", "Path", "Owner")
        for item in self.projects:
            path = str(item.get("path") or "")
            owners = (
                item.get("owned_by") if isinstance(item.get("owned_by"), list) else []
            )
            table.add_row(
                "yes" if item.get("available") else "no",
                str(item.get("name") or "-"),
                str(item.get("branch") or "-"),
                path,
                ", ".join(str(owner) for owner in owners) or "-",
                key=path,
            )
        if self.projects:
            first_item = self.projects[0]
            first = str(first_item.get("path") or "")
            if first:
                self.selected_path = first
                owners = first_item.get("owned_by")
                self.selected_owners = (
                    [str(owner) for owner in owners] if isinstance(owners, list) else []
                )
                table.move_cursor(row=0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "managed-projects":
            self.selected_path = str(event.row_key.value)
            selected = next(
                (
                    item
                    for item in self.projects
                    if str(item.get("path") or "") == self.selected_path
                ),
                {},
            )
            owners = selected.get("owned_by") if isinstance(selected, dict) else []
            self.selected_owners = (
                [str(owner) for owner in owners] if isinstance(owners, list) else []
            )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "managed-project-close":
            self.dismiss()
            return
        if event.button.id != "managed-project-launch":
            return
        if not self.selected_path:
            self.app.notify("Select a Git project first.", severity="warning")
            return
        launch_request = {
            "project_path": self.selected_path,
            "agent_id": self.query_one("#managed-agent-id", Input).value,
            "profile_id": str(self.query_one("#managed-agent-profile", Select).value),
            "runtime_server_mode": str(
                self.query_one("#managed-agent-runtime-mode", Select).value
            ),
            "launch_mode": str(self.query_one("#managed-launch-mode", Select).value),
            "existing_owner_ids": self.selected_owners,
        }
        # Managed launch selects the new Agent and attaches its terminal. Pop
        # the modal first so app-level widget queries resolve against the main
        # PBX screen instead of this temporary picker.
        self.dismiss()
        await asyncio.sleep(0)
        await self.app.launch_managed_workspace_from_picker(  # type: ignore[attr-defined]
            **launch_request,
        )


class RuntimeMigrationScreen(ModalScreen[None]):
    BINDINGS = [("escape", "dismiss", "Close")]

    def __init__(self, batch: dict[str, Any]) -> None:
        super().__init__()
        self.batch = batch

    def compose(self) -> ComposeResult:
        with Vertical(id="runtime-migration-screen"):
            yield Static(
                f"Runtime Migration {self.batch.get('batch_id', '-')}",
                id="runtime-migration-title",
            )
            yield Static(
                f"Eligible {self.batch.get('eligible_count', 0)} · "
                f"Blocked {self.batch.get('blocked_count', 0)} · "
                f"Rollback retained until {self.batch.get('retention_until', '-')}",
                id="runtime-migration-summary",
            )
            yield DataTable(id="runtime-migration-candidates", cursor_type="row")
            with Horizontal(id="runtime-migration-actions"):
                yield Button(
                    "Apply Eligible", id="runtime-migration-apply", variant="primary"
                )
                yield Button("Restore Mappings", id="runtime-migration-rollback")
                yield Button("Close", id="runtime-migration-close")

    def on_mount(self) -> None:
        table = self.query_one("#runtime-migration-candidates", DataTable)
        table.add_columns("Agent", "Type", "Session", "Eligible", "Blockers")
        candidates = self.batch.get("candidates")
        for item in candidates if isinstance(candidates, list) else []:
            if not isinstance(item, dict):
                continue
            table.add_row(
                str(item.get("agent_id") or "-"),
                str(item.get("agent_type") or "-"),
                str(item.get("session_id") or "-"),
                "yes" if item.get("eligible") else "no",
                "; ".join(str(value) for value in item.get("blockers", [])) or "-",
            )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "runtime-migration-close":
            self.dismiss()
        elif event.button.id == "runtime-migration-apply":
            await self.app.apply_runtime_migration_batch(self.batch)  # type: ignore[attr-defined]
            self.dismiss()
        elif event.button.id == "runtime-migration-rollback":
            await self.app.rollback_runtime_migration_batch(  # type: ignore[attr-defined]
                str(self.batch.get("batch_id") or "")
            )
            self.dismiss()


__all__ = ["ManagedProjectPickerScreen", "RuntimeMigrationScreen"]
