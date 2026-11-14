from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import time
from typing import Any
from urllib.parse import quote

import httpx
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Markdown, Static, TextArea

from ...client import auth_headers
from ..screens import JoplinDeleteConfirmScreen, JoplinNoteTitleScreen
from ..widgets import ReadingMarkdown
from ._base import TUIController


def _int_value(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class JoplinController(TUIController[Any]):
    async def refresh_joplin_status(self) -> None:
        try:
            response = await self.api_client().get(
                "/v1/joplin/status",
                headers=auth_headers(self.token),
                timeout=10,
            )
            response.raise_for_status()
            status = response.json()
            if not isinstance(status, dict):
                raise ValueError("Joplin status response was not an object")
        except Exception as exc:
            previous = (
                self.joplin_status if isinstance(self.joplin_status, dict) else {}
            )
            status = {
                **previous,
                "configured": bool(previous.get("configured"))
                or self.joplin_configured,
                "available": False,
                "notebook": previous.get("notebook") or "Agent PBX",
                "error": {
                    "code": "JOPLIN_STATUS_UNAVAILABLE",
                    "message": str(exc),
                    "retryable": True,
                },
            }
        self.joplin_status = status
        self.joplin_configured = bool(status.get("configured"))
        self.joplin_available = bool(status.get("available"))
        self.apply_joplin_tab_visibility()
        label = self.query_one_or_none("#joplin-status", Static)
        if label is not None:
            label.update(self.format_joplin_status_line(status))

    def format_joplin_status_line(self, status: dict[str, Any]) -> str:
        sync = status.get("sync") if isinstance(status.get("sync"), dict) else None
        sync_running = self.joplin_sync_in_progress(status)
        if not status.get("available") and sync_running:
            transition = (
                "Data API restarting"
                if int((sync or {}).get("running") or 0)
                else "Data API restart queued"
            )
            return (
                f"Joplin: {status.get('notebook') or 'Agent PBX'}"
                f" | {self.format_joplin_sync_summary(sync)}"
                f" | {transition}"
            )
        if status.get("available"):
            sync_text = f" | {self.format_joplin_sync_summary(sync)}" if sync else ""
            encryption = (
                status.get("encryption")
                if isinstance(status.get("encryption"), dict)
                else None
            )
            profile_text = self.format_joplin_profile_summary(sync, encryption)
            profile_suffix = f" | {profile_text}" if profile_text else ""
            return (
                f"Joplin: {status.get('notebook') or 'Agent PBX'}"
                f"{sync_text}{profile_suffix}"
            )
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = error.get("code") or "JOPLIN_UNAVAILABLE"
        return f"Joplin: {code}"

    @staticmethod
    def joplin_sync_in_progress(status: dict[str, Any]) -> bool:
        sync = status.get("sync") if isinstance(status.get("sync"), dict) else {}
        return bool(int(sync.get("running") or 0) or int(sync.get("pending") or 0))

    def schedule_joplin_sync_refresh(self, agent_id: str) -> None:
        if self.joplin_sync_refresh_timer is not None:
            self.joplin_sync_refresh_timer.stop()

        def retry() -> None:
            self.joplin_sync_refresh_timer = None
            if (
                self.selected_agent_id != agent_id
                or self.active_agent_tab != "joplin-tab"
            ):
                return
            self.run_worker(
                self.load_joplin_notes(agent_id),
                name="joplin-sync-retry",
                group="joplin-refresh",
                exclusive=True,
            )

        self.joplin_sync_refresh_timer = self.set_timer(1.0, retry)

    def cancel_joplin_sync_refresh(self) -> None:
        if self.joplin_sync_refresh_timer is None:
            return
        self.joplin_sync_refresh_timer.stop()
        self.joplin_sync_refresh_timer = None

    def format_joplin_sync_summary(self, sync: dict[str, Any] | None) -> str:
        if not sync or not sync.get("enabled"):
            return "Sync: off"
        write_sync = sync.get("write_sync")
        if isinstance(write_sync, dict):
            running = int(write_sync.get("running") or 0)
            pending = int(write_sync.get("pending") or 0)
            if running:
                return f"Writes: running {running}"
            if pending:
                return f"Writes: queued {pending}"
            status = str(write_sync.get("current_status") or "ready").lower()
            if status == "failed":
                return "Writes: error"
            if status == "succeeded":
                return "Writes: ok"
            return "Writes: ready"
        running = int(sync.get("running") or 0)
        pending = int(sync.get("pending") or 0)
        if running:
            return f"Sync: running {running}"
        if pending:
            return f"Sync: queued {pending}"
        latest = sync.get("latest")
        if isinstance(latest, dict):
            latest_status = str(latest.get("status") or "").lower()
            if latest_status == "failed" and latest.get("error"):
                return "Sync: error"
            if latest_status == "succeeded":
                return "Sync: ok"
        latest_error = sync.get("latest_error")
        if isinstance(latest_error, dict) and latest_error.get("error"):
            return "Sync: error"
        if sync.get("latest_success"):
            return "Sync: ok"
        return "Sync: ready"

    @staticmethod
    def format_joplin_profile_summary(
        sync: dict[str, Any] | None,
        encryption: dict[str, Any] | None,
    ) -> str:
        locked = int((encryption or {}).get("pending_total") or 0)
        if locked:
            return f"Profile: E2EE locked {locked}"
        profile = (
            sync.get("profile_validation")
            if isinstance(sync, dict)
            and isinstance(sync.get("profile_validation"), dict)
            else None
        )
        if profile is None:
            return ""
        running = int(profile.get("running") or 0)
        pending = int(profile.get("pending") or 0)
        if running:
            return f"Profile: validating {running}"
        if pending:
            return f"Profile: queued {pending}"
        status = str(profile.get("current_status") or "ready").lower()
        if status == "failed":
            return "Profile: error"
        if status == "succeeded":
            return "Profile: ok"
        return "Profile: ready"

    async def update_joplin_reader(
        self,
        markdown: str,
        *,
        scope: str = "",
        position_id: str = "",
        revision: str = "",
    ) -> bool:
        reader = self.query_one_or_none("#joplin-reader", ReadingMarkdown)
        if reader is None:
            return False
        source_changed = str(getattr(reader, "source_markdown", "") or "") != markdown
        prior_identity = (
            str(getattr(reader, "joplin_scope", "") or ""),
            str(getattr(reader, "joplin_position_id", "") or ""),
            str(getattr(reader, "joplin_revision", "") or ""),
        )
        identity = (scope, position_id, revision)
        if not source_changed and prior_identity == identity:
            return False
        self.remember_joplin_reader_position()
        if source_changed:
            setattr(reader, "source_markdown", markdown)
            await reader.update(markdown)
        setattr(reader, "joplin_scope", scope)
        setattr(reader, "joplin_position_id", position_id)
        setattr(reader, "joplin_revision", revision)
        self.restore_joplin_reader_position(
            scope=scope,
            position_id=position_id,
            revision=revision,
            reset_if_missing=source_changed,
        )
        return source_changed

    def remember_joplin_reader_position(self) -> None:
        reader = self.query_one_or_none("#joplin-reader", ReadingMarkdown)
        if reader is None:
            return
        scope = str(getattr(reader, "joplin_scope", "") or "")
        position_id = str(getattr(reader, "joplin_position_id", "") or "")
        revision = str(getattr(reader, "joplin_revision", "") or "")
        if not scope or not position_id or not revision:
            return
        self.joplin_panel_state.remember_reader_position(
            scope,
            position_id,
            revision=revision,
            scroll_y=float(reader.scroll_y),
            scroll_target_y=float(reader.scroll_target_y),
        )

    def restore_joplin_reader_position(
        self,
        *,
        scope: str,
        position_id: str,
        revision: str,
        reset_if_missing: bool,
    ) -> None:
        reader = self.query_one_or_none("#joplin-reader", ReadingMarkdown)
        if reader is None:
            return
        position = (
            self.joplin_panel_state.reader_position(
                scope,
                position_id,
                revision=revision,
            )
            if scope and position_id and revision
            else None
        )

        def restore() -> None:
            if (
                str(getattr(reader, "joplin_scope", "") or "") != scope
                or str(getattr(reader, "joplin_position_id", "") or "") != position_id
                or str(getattr(reader, "joplin_revision", "") or "") != revision
            ):
                return
            if position is None:
                if reset_if_missing:
                    reader.scroll_home(animate=False, immediate=True)
                return
            reader.scroll_y = position.scroll_y
            reader.scroll_target_y = position.scroll_target_y

        self.call_after_refresh(restore)

    @classmethod
    def joplin_reader_revision(cls, note: Mapping[str, Any] | None) -> str:
        if not isinstance(note, Mapping):
            return ""
        explicit = str(note.get("revision") or note.get("body_hash") or "").strip()
        if explicit:
            return explicit
        return hashlib.sha256(
            repr(cls.joplin_note_cache_version(note)).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def joplin_note_cache_version(note: Mapping[str, Any] | None) -> tuple[object, ...]:
        if not isinstance(note, Mapping):
            return ()
        return (
            str(note.get("id") or ""),
            note.get("updated_time"),
            note.get("user_updated_time"),
            _int_value(note.get("encryption_applied")) or 0,
            bool(note.get("decryption_pending")),
            bool(note.get("is_conflict")),
            str(note.get("conflict_original_id") or ""),
        )

    @classmethod
    def joplin_note_has_cached_body(
        cls,
        note: Mapping[str, Any] | None,
        summary: Mapping[str, Any] | None = None,
    ) -> bool:
        if not isinstance(note, Mapping) or "body" not in note:
            return False
        if summary is None:
            return True
        return cls.joplin_note_cache_version(note) == cls.joplin_note_cache_version(
            summary
        )

    @classmethod
    def merge_joplin_note_summary(
        cls,
        summary: Mapping[str, Any],
        cached: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        merged = dict(summary)
        if not cls.joplin_note_has_cached_body(cached, summary):
            return merged
        assert cached is not None
        for key in ("body", "revision", "title_hash", "body_hash"):
            if key in cached:
                merged[key] = cached[key]
        return merged

    @staticmethod
    def joplin_list_fingerprint(
        notes: list[dict[str, Any]],
    ) -> tuple[tuple[object, ...], ...]:
        return tuple(
            (
                str(note.get("id") or ""),
                str(note.get("title") or ""),
                note.get("updated_time"),
                note.get("user_updated_time"),
                _int_value(note.get("encryption_applied")) or 0,
                bool(note.get("decryption_pending")),
                bool(note.get("is_conflict")),
                str(note.get("conflict_original_id") or ""),
            )
            for note in notes
            if str(note.get("id") or "")
        )

    @staticmethod
    def joplin_note_is_locked(note: dict[str, Any] | None) -> bool:
        if not isinstance(note, dict):
            return False
        return bool(note.get("decryption_pending") or note.get("encryption_applied"))

    @staticmethod
    def joplin_note_is_native_conflict(note: Mapping[str, Any] | None) -> bool:
        if not isinstance(note, Mapping):
            return False
        return bool(note.get("is_conflict"))

    def set_joplin_mutation_controls_locked(self, locked: bool) -> None:
        for button_id in (
            "joplin-rename",
            "joplin-delete",
            "joplin-edit",
            "joplin-preview",
            "joplin-save",
            "joplin-conflict-merge",
            "joplin-conflict-keep",
            "joplin-conflict-copy",
            "joplin-conflict-overwrite",
            "joplin-note-paste",
        ):
            button = self.query_one_or_none(f"#{button_id}", Button)
            if button is not None:
                button.disabled = locked

    def update_joplin_clipboard_controls(
        self,
        *,
        active_note: Mapping[str, Any] | None,
        mode: str,
        locked: bool,
    ) -> None:
        copy_button = self.query_one_or_none("#joplin-note-copy", Button)
        paste_button = self.query_one_or_none("#joplin-note-paste", Button)
        if copy_button is not None:
            copy_button.disabled = active_note is None or locked
        if paste_button is not None:
            paste_button.disabled = (
                active_note is None or locked or mode not in {"edit", "conflict"}
            )

    async def render_joplin_mode(
        self,
        agent_id: str,
        *,
        note: dict[str, Any] | None = None,
    ) -> None:
        scope = self.joplin_selection_key(agent_id)
        mode = self.joplin_panel_state.mode_for(scope)
        draft = self.joplin_panel_state.draft_for(scope)
        reader = self.query_one_or_none("#joplin-reader", Markdown)
        body = self.query_one_or_none("#joplin-body", TextArea)
        conflict_actions = self.query_one_or_none(
            "#joplin-conflict-actions", Horizontal
        )
        if reader is None or body is None:
            return
        active_note = note
        if active_note is None:
            note_id = self.selected_joplin_note_for_agent(agent_id)
            active_note = self.joplin_notes_by_agent.get(scope, {}).get(note_id or "")
        locked = self.joplin_note_is_locked(active_note)
        native_conflict = self.joplin_note_is_native_conflict(active_note)
        self.set_joplin_mutation_controls_locked(locked or native_conflict)
        if locked:
            self.joplin_panel_state.set_mode(scope, "read")
            mode = "locked"
            draft = None
        self.update_joplin_clipboard_controls(
            active_note=active_note,
            mode=mode,
            locked=locked,
        )
        if mode in {"edit", "conflict"} and draft is not None:
            self.remember_joplin_reader_position()
            reader.styles.display = "none"
            body.styles.display = "block"
            if body.text != draft.body:
                self.joplin_body_loading = True
                body.text = draft.body
                self.joplin_body_loading = False
            body.read_only = False
            body.focus()
        else:
            body.styles.display = "none"
            reader.styles.display = "block"
            if locked:
                markdown = (
                    "# 🔒 Encrypted Joplin note\n\n"
                    "This note is present, but the configured Joplin profile has "
                    "not decrypted it. Unlock E2EE in Joplin and run **Sync Now**. "
                    "Editing, renaming, deleting, appending, and conflict overwrite "
                    "remain disabled until decryption completes."
                )
            else:
                markdown = (
                    draft.body
                    if mode == "preview" and draft is not None
                    else str((active_note or {}).get("body") or "")
                )
                if native_conflict:
                    markdown = (
                        "# ⚠ Local Joplin conflict recovery copy\n\n"
                        "This note is readable locally but Joplin will not publish it "
                        "as an ordinary synchronized note. Use **Promote Local** to "
                        "create a guarded, synchronizable copy. The source remains "
                        "preserved.\n\n---\n\n"
                        f"{markdown}"
                    )
            note_id = str((active_note or {}).get("id") or "")
            position_id = (
                f"{note_id}:preview"
                if mode == "preview" and draft is not None
                else note_id
            )
            revision = (
                f"preview:{draft.edit_id}:{hashlib.sha256(markdown.encode('utf-8')).hexdigest()}"
                if mode == "preview" and draft is not None
                else self.joplin_reader_revision(active_note)
            )
            await self.update_joplin_reader(
                markdown or "_Empty Joplin note._",
                scope=scope,
                position_id=position_id,
                revision=revision,
            )
        native_promote = self.query_one_or_none(
            "#joplin-native-conflict-promote", Button
        )
        has_edit_conflict = bool(draft is not None and draft.conflict_id)
        if native_promote is not None:
            native_promote.disabled = locked or not native_conflict
        for button_id in (
            "joplin-conflict-merge",
            "joplin-conflict-keep",
            "joplin-conflict-copy",
            "joplin-conflict-overwrite",
        ):
            button = self.query_one_or_none(f"#{button_id}", Button)
            if button is not None:
                button.disabled = locked or native_conflict or not has_edit_conflict
        if conflict_actions is not None:
            conflict_actions.styles.display = (
                "block" if has_edit_conflict or native_conflict else "none"
            )
        status = self.query_one_or_none("#joplin-status", Static)
        if status is not None and active_note is not None:
            dirty = " · dirty draft" if draft and draft.dirty else ""
            lock_text = " · 🔒 decryption pending" if locked else ""
            native_conflict_text = (
                " · ⚠ local-only conflict; promote to sync" if native_conflict else ""
            )
            status.update(
                f"Joplin {mode}: {active_note.get('title') or active_note.get('id')}"
                f"{dirty}{lock_text}{native_conflict_text}"
            )

    async def load_joplin_notes(self, agent_id: str) -> None:
        table = self.query_one_or_none("#joplin-notes", DataTable)
        body = self.query_one_or_none("#joplin-body", TextArea)
        reader = self.query_one_or_none("#joplin-reader", Markdown)
        if table is None or body is None or reader is None:
            return
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        cached_notes = list(self.joplin_notes_by_agent.get(cache_agent_id, {}).values())
        if cached_notes and self.joplin_rendered_list_scope != cache_agent_id:
            self.render_joplin_notes(agent_id, cached_notes)
            selected = self.selected_joplin_note_for_agent(agent_id)
            cached_note = self.current_joplin_note(agent_id, selected)
            if self.joplin_note_has_cached_body(cached_note):
                await self.render_joplin_mode(agent_id, note=cached_note)
        await self.refresh_joplin_status()
        draft = self.joplin_panel_state.draft_for(cache_agent_id)
        reader_markdown_before_load = str(getattr(reader, "source_markdown", "") or "")
        generation_resource = f"joplin-notes:{cache_agent_id}"
        generation = self.async_generations.start(generation_resource)
        if not self.joplin_configured:
            table.clear()
            self.joplin_rendered_list_scope = None
            self.set_joplin_mutation_controls_locked(False)
            await self.update_joplin_reader(
                self.format_joplin_unavailable(self.joplin_status)
            )
            return
        if not self.joplin_available:
            if self.joplin_sync_in_progress(self.joplin_status):
                self.set_joplin_mutation_controls_locked(True)
                if reader_markdown_before_load:
                    await self.update_joplin_reader(reader_markdown_before_load)
                self.schedule_joplin_sync_refresh(agent_id)
                return
            table.clear()
            self.joplin_rendered_list_scope = None
            self.set_joplin_mutation_controls_locked(False)
            await self.update_joplin_reader(
                self.format_joplin_unavailable(self.joplin_status)
            )
            return
        selected_before_load = self.selected_joplin_note_for_agent(agent_id)
        selected_cached_note = self.current_joplin_note(agent_id, selected_before_load)
        if not (draft and draft.dirty) and not self.joplin_note_has_cached_body(
            selected_cached_note
        ):
            await self.update_joplin_reader(
                f"Loading Joplin notes for {self.joplin_scope_label_for_agent(agent_id)}..."
            )
        try:
            response = await self.api_client().get(
                self.joplin_notes_url_for_agent(agent_id),
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            notes = response.json()
        except Exception as exc:
            if not self.async_generations.current(generation_resource, generation):
                return
            # Managed CLI sync briefly stops and restarts the Data API server.
            # Preserve the current note list, reader, and dirty draft through
            # that expected handoff; the terminal sync event reloads the panel.
            await self.refresh_joplin_status()
            if not self.async_generations.current(generation_resource, generation):
                return
            if self.joplin_sync_in_progress(self.joplin_status):
                self.set_joplin_mutation_controls_locked(True)
                if reader_markdown_before_load:
                    await self.update_joplin_reader(reader_markdown_before_load)
                self.schedule_joplin_sync_refresh(agent_id)
                return
            table.clear()
            self.joplin_rendered_list_scope = None
            await self.update_joplin_reader(
                f"Unable to load Joplin notes for "
                f"{self.joplin_scope_label_for_agent(agent_id)}: {exc}"
            )
            return
        if not self.async_generations.current(generation_resource, generation):
            return
        self.cancel_joplin_sync_refresh()
        self.render_joplin_notes(agent_id, notes)
        if draft and draft.dirty:
            await self.render_joplin_mode(agent_id)
            return
        if notes:
            selected_note_id = self.selected_joplin_note_for_agent(agent_id)
            note_id = (
                selected_note_id
                if selected_note_id
                in self.joplin_notes_by_agent.get(cache_agent_id, {})
                else str(notes[0]["id"])
            )
            cached_note = self.current_joplin_note(agent_id, note_id)
            if self.joplin_note_has_cached_body(cached_note):
                self.set_selected_joplin_note_for_agent(agent_id, note_id)
                await self.render_joplin_mode(agent_id, note=cached_note)
            else:
                await self.select_joplin_note(note_id, agent_id=agent_id)
        else:
            self.set_selected_joplin_note_for_agent(agent_id, None)
            self.joplin_rendered_list_scope = None
            self.set_joplin_mutation_controls_locked(False)
            self.update_joplin_clipboard_controls(
                active_note=None,
                mode="read",
                locked=False,
            )
            await self.update_joplin_reader("No project-scoped Joplin notes yet.")

    def render_joplin_notes(
        self,
        agent_id: str,
        notes: list[dict[str, Any]],
    ) -> None:
        table = self.query_one("#joplin-notes", DataTable)
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        prior_notes = self.joplin_notes_by_agent.get(cache_agent_id, {})
        note_map: dict[str, dict[str, Any]] = {}
        for note in notes:
            note_id = str(note.get("id") or "")
            if not note_id:
                continue
            note_map[note_id] = self.merge_joplin_note_summary(
                note,
                prior_notes.get(note_id),
            )
        fingerprint = self.joplin_list_fingerprint(list(note_map.values()))
        list_changed = (
            self.joplin_rendered_list_scope != cache_agent_id
            or self.joplin_list_fingerprint_by_scope.get(cache_agent_id) != fingerprint
            or table.row_count != len(note_map)
        )
        position = self.data_table_position(table) if list_changed else {}
        if list_changed:
            table.clear()
        for note_id, note in note_map.items():
            if not list_changed:
                break
            table.add_row(
                self.format_joplin_time(note.get("updated_time")),
                (
                    f"🔒 {note.get('title') or note_id}"
                    if self.joplin_note_is_locked(note)
                    else (
                        f"⚠ LOCAL {note.get('title') or note_id}"
                        if self.joplin_note_is_native_conflict(note)
                        else str(note.get("title") or note_id)
                    )
                ),
                key=note_id,
            )
        self.joplin_notes_by_agent[cache_agent_id] = note_map
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent[agent_id] = note_map
        self.joplin_list_fingerprint_by_scope[cache_agent_id] = fingerprint
        self.joplin_rendered_list_scope = cache_agent_id
        selected = self.selected_joplin_note_for_agent(agent_id)
        if list_changed and selected and selected in note_map:
            try:
                table.move_cursor(
                    row=table.get_row_index(selected),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                pass
        if list_changed:
            self.restore_data_table_position(table, position)

    def joplin_selection_key(self, agent_id: str) -> str:
        return self.joplin_note_cache_agent_id(agent_id)

    def selected_joplin_note_for_agent(self, agent_id: str) -> str | None:
        key = self.joplin_selection_key(agent_id)
        self.joplin_panel_state.selected_note_id = self.selected_joplin_note_id
        return self.joplin_panel_state.selected_for(key)

    def set_selected_joplin_note_for_agent(
        self,
        agent_id: str,
        note_id: str | None,
    ) -> None:
        key = self.joplin_selection_key(agent_id)
        self.joplin_panel_state.select(key, note_id)
        self.selected_joplin_note_id = self.joplin_panel_state.selected_note_id

    async def select_joplin_note(
        self,
        note_id: str,
        *,
        agent_id: str | None = None,
        force_refresh: bool = False,
    ) -> None:
        agent_id = agent_id or self.selected_agent_id
        if not agent_id:
            return
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        draft = self.joplin_panel_state.draft_for(cache_agent_id)
        if draft is not None and draft.dirty and draft.note_id != note_id:
            self.notify(
                "Save or resolve the current Joplin draft before selecting another note.",
                severity="warning",
            )
            return
        cached_note = self.current_joplin_note(agent_id, note_id)
        if not force_refresh and self.joplin_note_has_cached_body(cached_note):
            self.set_selected_joplin_note_for_agent(agent_id, note_id)
            if draft is None or draft.note_id != note_id:
                self.joplin_panel_state.set_mode(cache_agent_id, "read")
            await self.render_joplin_mode(agent_id, note=cached_note)
            return
        generation_resource = f"joplin-note:{cache_agent_id}"
        generation = self.async_generations.start(generation_resource)
        await self.update_joplin_reader(f"Loading Joplin note {note_id}...")
        try:
            response = await self.api_client().get(
                self.joplin_notes_url_for_agent(agent_id, note_id),
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            if not self.async_generations.current(generation_resource, generation):
                return
            await self.update_joplin_reader(
                f"Unable to load Joplin note {note_id}: {exc}"
            )
            return
        if not self.async_generations.current(generation_resource, generation):
            return
        self.set_selected_joplin_note_for_agent(agent_id, note_id)
        self.joplin_notes_by_agent.setdefault(cache_agent_id, {})[note_id] = note
        if cache_agent_id != agent_id:
            self.joplin_notes_by_agent.setdefault(agent_id, {})[note_id] = note
        if draft is None or draft.note_id != note_id:
            self.joplin_panel_state.set_mode(cache_agent_id, "read")
        await self.render_joplin_mode(agent_id, note=note)
        table = self.query_one_or_none("#joplin-notes", DataTable)
        if table is not None and note_id in self.joplin_notes_by_agent.get(
            cache_agent_id, {}
        ):
            try:
                table.move_cursor(
                    row=table.get_row_index(note_id),
                    animate=False,
                    scroll=False,
                )
            except Exception:
                return

    def current_joplin_note_title(self, agent_id: str, note_id: str | None) -> str:
        if not note_id:
            return ""
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        note = self.joplin_notes_by_agent.get(cache_agent_id, {}).get(note_id, {})
        return str(note.get("title") or note_id)

    def current_joplin_note(
        self,
        agent_id: str,
        note_id: str | None = None,
    ) -> dict[str, Any] | None:
        resolved_note_id = note_id or self.selected_joplin_note_for_agent(agent_id)
        if not resolved_note_id:
            return None
        cache_agent_id = self.joplin_note_cache_agent_id(agent_id)
        return self.joplin_notes_by_agent.get(cache_agent_id, {}).get(resolved_note_id)

    def guard_joplin_note_mutation(
        self,
        agent_id: str,
        note_id: str | None = None,
    ) -> bool:
        note = self.current_joplin_note(agent_id, note_id)
        if self.joplin_note_is_native_conflict(note):
            self.notify(
                "This is a local-only Joplin conflict recovery copy. Promote it "
                "before editing, renaming, or deleting it.",
                severity="warning",
            )
            return False
        if not self.joplin_note_is_locked(note):
            return True
        self.notify(
            "This Joplin note is encrypted. Unlock E2EE and sync before changing it.",
            severity="warning",
        )
        return False

    def open_joplin_title_modal(self, agent_id: str, *, action: str) -> None:
        note_id = self.selected_joplin_note_for_agent(agent_id)
        scope = self.joplin_selection_key(agent_id)
        draft = self.joplin_panel_state.draft_for(scope)
        if action == "rename" and draft is not None and draft.dirty:
            self.notify(
                "Save or resolve the current Joplin draft before renaming.",
                severity="warning",
            )
            return
        if action == "rename" and not note_id:
            self.notify("Select a Joplin note before renaming.", severity="warning")
            return
        if action == "rename" and not self.guard_joplin_note_mutation(
            agent_id, note_id
        ):
            return
        current_title = (
            self.current_joplin_note_title(agent_id, note_id)
            if action == "rename"
            else ""
        )
        self.push_screen(
            JoplinNoteTitleScreen(
                agent_id=agent_id,
                action=action,
                note_id=note_id if action == "rename" else None,
                current_title=current_title,
            )
        )

    async def joplin_title_action_for_agent(
        self,
        agent_id: str,
        action: str,
        title: str,
        *,
        note_id: str | None = None,
    ) -> None:
        if action == "new":
            await self.create_joplin_note(agent_id, title=title)
            return
        if action == "rename":
            await self.rename_joplin_note(agent_id, title=title, note_id=note_id)
            return
        self.notify(f"Unknown Joplin action: {action}", severity="warning")

    async def create_joplin_note(self, agent_id: str, *, title: str) -> None:
        if not await self.ensure_joplin_available():
            return
        body = f"# {title.strip()}\n\n"
        try:
            response = await self.api_client().post(
                self.joplin_notes_url_for_agent(agent_id),
                json={"title": title.strip(), "body": body},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            note = response.json()
        except Exception as exc:
            self.notify(f"Joplin note create failed: {exc}", severity="error")
            return
        self.set_selected_joplin_note_for_agent(
            agent_id,
            str(note.get("id") or "").strip() or None,
        )
        self.notify("Joplin note created.")
        await self.load_joplin_notes(agent_id)

    async def rename_joplin_note(
        self,
        agent_id: str,
        *,
        title: str,
        note_id: str | None = None,
    ) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = note_id or self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before renaming.", severity="warning")
            return
        if not self.guard_joplin_note_mutation(agent_id, note_id):
            return
        try:
            edit_response = await self.api_client().post(
                self.joplin_edit_url_for_agent(agent_id, note_id),
                json={"client_id": self.tmux_terminal_client_id},
                headers=auth_headers(self.token),
                timeout=20,
            )
            edit_response.raise_for_status()
            edit_payload = edit_response.json()
            edit = edit_payload.get("edit", {})
            note = edit_payload.get("note", {})
            response = await self.api_client().put(
                self.joplin_notes_url_for_agent(agent_id, note_id),
                json={
                    "title": title.strip(),
                    "base_revision": note.get("revision"),
                    "base_title": note.get("title"),
                    "base_body": note.get("body"),
                    "edit_id": edit.get("edit_id"),
                },
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Joplin rename failed: {exc}", severity="error")
            return
        self.set_selected_joplin_note_for_agent(agent_id, note_id)
        self.notify("Joplin note renamed.")
        await self.load_joplin_notes(agent_id)

    def confirm_delete_joplin_note(self, agent_id: str) -> None:
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before deleting.", severity="warning")
            return
        if not self.guard_joplin_note_mutation(agent_id, note_id):
            return
        self.push_screen(
            JoplinDeleteConfirmScreen(
                agent_id=agent_id,
                note_id=note_id,
                title=self.current_joplin_note_title(agent_id, note_id),
            )
        )

    async def delete_joplin_note(self, agent_id: str, note_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        if not self.guard_joplin_note_mutation(agent_id, note_id):
            return
        try:
            response = await self.api_client().delete(
                self.joplin_notes_url_for_agent(agent_id, note_id),
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(f"Joplin delete failed: {exc}", severity="error")
            return
        if self.selected_joplin_note_for_agent(agent_id) == note_id:
            self.set_selected_joplin_note_for_agent(agent_id, None)
        self.notify("Joplin note deleted.")
        await self.load_joplin_notes(agent_id)

    async def begin_joplin_edit(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before editing.", severity="warning")
            return
        if not self.guard_joplin_note_mutation(agent_id, note_id):
            return
        scope = self.joplin_selection_key(agent_id)
        existing = self.joplin_panel_state.draft_for(scope)
        if existing is not None and existing.note_id == note_id:
            self.joplin_panel_state.set_mode(scope, "edit")
            await self.render_joplin_mode(agent_id)
            return
        try:
            response = await self.api_client().post(
                self.joplin_edit_url_for_agent(agent_id, note_id),
                json={"client_id": self.tmux_terminal_client_id},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            edit = payload.get("edit") if isinstance(payload, dict) else None
            note = payload.get("note") if isinstance(payload, dict) else None
            conflict = payload.get("conflict") if isinstance(payload, dict) else None
            if not isinstance(edit, dict) or not isinstance(note, dict):
                raise ValueError("invalid Joplin edit response")
        except Exception as exc:
            self.notify(f"Joplin edit start failed: {exc}", severity="error")
            return
        draft = self.joplin_panel_state.begin_edit(scope, edit=edit, note=note)
        if isinstance(conflict, dict):
            draft.conflict_id = str(conflict.get("conflict_id") or "") or None
            draft.merged_title = str(conflict.get("merged_title") or "")
            draft.merged_body = str(conflict.get("merged_body") or "")
            self.joplin_panel_state.set_mode(scope, "conflict")
        self.joplin_notes_by_agent.setdefault(scope, {})[note_id] = note
        self.joplin_body_loading = True
        self.query_one("#joplin-body", TextArea).text = draft.body
        self.joplin_body_loading = False
        await self.render_joplin_mode(agent_id, note=note)

    async def preview_joplin_draft(self, agent_id: str) -> None:
        scope = self.joplin_selection_key(agent_id)
        draft = self.joplin_panel_state.draft_for(scope)
        if draft is None:
            self.notify(
                "Start editing a Joplin note before previewing.", severity="warning"
            )
            return
        body = self.query_one("#joplin-body", TextArea)
        self.joplin_panel_state.update_draft(scope, body.text)
        mode = self.joplin_panel_state.mode_for(scope)
        self.joplin_panel_state.set_mode(
            scope, "edit" if mode == "preview" else "preview"
        )
        await self.render_joplin_mode(agent_id)

    def schedule_joplin_draft_persist(self, agent_id: str) -> None:
        if self.joplin_draft_timer is not None:
            self.joplin_draft_timer.stop()
        self.joplin_draft_timer = self.set_timer(
            0.5,
            lambda agent_id=agent_id: self.run_async_worker(
                lambda agent_id=agent_id: self.persist_joplin_draft(agent_id),
                name="joplin-draft-persist",
                exclusive=True,
            ),
        )

    async def persist_joplin_draft(self, agent_id: str) -> None:
        scope = self.joplin_selection_key(agent_id)
        draft = self.joplin_panel_state.draft_for(scope)
        if draft is None:
            return
        try:
            response = await self.api_client().put(
                f"/v1/joplin/edits/{quote(draft.edit_id, safe='')}/draft",
                json={"title": draft.title, "body": draft.body},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except Exception as exc:
            self.notify(
                f"Joplin draft remains local; daemon persistence failed: {exc}",
                severity="warning",
            )

    @staticmethod
    def joplin_native_conflict_promotion_title(note: Mapping[str, Any]) -> str:
        note_id = str(note.get("id") or "")
        base = str(note.get("title") or "Joplin-Recovery").strip()
        suffix = f"-Recovered-{note_id[:8] or 'local'}"
        return f"{base[: max(1, 240 - len(suffix))]}{suffix}"

    async def promote_joplin_native_conflict(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify(
                "Select a local Joplin conflict before promoting it.",
                severity="warning",
            )
            return
        note = self.current_joplin_note(agent_id, note_id)
        if not self.joplin_note_is_native_conflict(note):
            self.notify(
                "The selected Joplin note is not a local conflict recovery copy.",
                severity="warning",
            )
            return
        if not isinstance(note, dict) or not note.get("revision") or "body" not in note:
            await self.select_joplin_note(
                note_id,
                agent_id=agent_id,
                force_refresh=True,
            )
            note = self.current_joplin_note(agent_id, note_id)
        if not isinstance(note, dict) or not note.get("revision"):
            self.notify(
                "The local Joplin conflict could not be refreshed for promotion.",
                severity="error",
            )
            return
        target_title = self.joplin_native_conflict_promotion_title(note)
        armed_key = f"{self.joplin_selection_key(agent_id)}:{note_id}"
        armed_until = self.joplin_native_conflict_promote_armed_until.get(
            armed_key,
            0.0,
        )
        if time.monotonic() > armed_until:
            self.joplin_native_conflict_promote_armed_until[armed_key] = (
                time.monotonic() + 5.0
            )
            self.notify(
                f"Promotion will create {target_title!r} and preserve the local "
                "conflict. Select Promote Local again within 5 seconds.",
                severity="warning",
            )
            return
        self.joplin_native_conflict_promote_armed_until.pop(armed_key, None)
        try:
            response = await self.api_client().post(
                self.joplin_native_conflict_promote_url_for_agent(
                    agent_id,
                    note_id,
                ),
                json={
                    "title": target_title,
                    "expected_revision": str(note["revision"]),
                    "expected_original_id": (
                        str(note.get("conflict_original_id") or "") or None
                    ),
                },
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(f"Joplin conflict promotion failed: {exc}", severity="error")
            return
        promoted = payload.get("promoted") if isinstance(payload, dict) else None
        promoted_id = (
            str(promoted.get("id") or "") if isinstance(promoted, dict) else ""
        )
        if not promoted_id:
            self.notify(
                "Joplin conflict promotion returned no promoted note.",
                severity="error",
            )
            return
        scope = self.joplin_selection_key(agent_id)
        self.joplin_notes_by_agent.setdefault(scope, {})[promoted_id] = promoted
        self.set_selected_joplin_note_for_agent(agent_id, promoted_id)
        self.notify(
            f"Promoted local conflict to {promoted.get('title') or promoted_id}; "
            "synchronization is queued and the source remains preserved."
        )
        await self.load_joplin_notes(agent_id)

    async def resolve_joplin_conflict(
        self,
        agent_id: str,
        resolution: str,
    ) -> None:
        scope = self.joplin_selection_key(agent_id)
        draft = self.joplin_panel_state.draft_for(scope)
        if draft is None or not draft.conflict_id:
            self.notify("No open Joplin conflict is selected.", severity="warning")
            return
        if resolution == "overwrite":
            armed_until = self.joplin_overwrite_armed_until.get(draft.conflict_id, 0.0)
            if time.monotonic() > armed_until:
                self.joplin_overwrite_armed_until[draft.conflict_id] = (
                    time.monotonic() + 5.0
                )
                self.notify(
                    "Overwrite will replace the current Joplin note. Select Overwrite again within 5 seconds.",
                    severity="warning",
                )
                return
        try:
            response = await self.api_client().post(
                self.joplin_conflict_url_for_agent(agent_id, draft.conflict_id),
                json={"resolution": resolution},
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.notify(f"Joplin conflict resolution failed: {exc}", severity="error")
            return
        if resolution == "review_merge":
            edit = payload.get("edit") if isinstance(payload, dict) else None
            note = payload.get("note") if isinstance(payload, dict) else None
            merged = payload.get("draft") if isinstance(payload, dict) else None
            if (
                isinstance(edit, dict)
                and isinstance(note, dict)
                and isinstance(merged, dict)
            ):
                rebased = self.joplin_panel_state.begin_edit(
                    scope, edit=edit, note=note
                )
                rebased.title = str(merged.get("title") or rebased.title)
                rebased.body = str(merged.get("body") or rebased.body)
                rebased.dirty = True
                self.joplin_body_loading = True
                self.query_one("#joplin-body", TextArea).text = rebased.body
                self.joplin_body_loading = False
                await self.render_joplin_mode(agent_id, note=note)
                self.notify(
                    "Merged draft opened for review. Conflict markers require review."
                )
                return
        self.joplin_panel_state.finish_edit(scope)
        self.notify(f"Joplin conflict resolved: {resolution.replace('_', ' ')}.")
        await self.load_joplin_notes(agent_id)

    async def save_joplin_note(self, agent_id: str) -> None:
        if not await self.ensure_joplin_available():
            return
        note_id = self.selected_joplin_note_for_agent(agent_id)
        if not note_id:
            self.notify("Select a Joplin note before saving.", severity="warning")
            return
        if not self.guard_joplin_note_mutation(agent_id, note_id):
            return
        scope = self.joplin_selection_key(agent_id)
        draft = self.joplin_panel_state.draft_for(scope)
        if draft is None or draft.note_id != note_id:
            self.notify("Choose Edit before saving a Joplin note.", severity="warning")
            return
        body = self.query_one("#joplin-body", TextArea)
        self.joplin_panel_state.update_draft(scope, body.text)
        try:
            response = await self.api_client().put(
                self.joplin_notes_url_for_agent(agent_id, note_id),
                json={
                    "title": draft.title,
                    "body": draft.body,
                    "base_revision": draft.base_revision,
                    "base_title": draft.base_title,
                    "base_body": draft.base_body,
                    "edit_id": draft.edit_id,
                },
                headers=auth_headers(self.token),
                timeout=20,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail: dict[str, Any] = {}
            try:
                payload = exc.response.json()
                raw_detail = (
                    payload.get("detail") if isinstance(payload, dict) else None
                )
                if isinstance(raw_detail, dict):
                    detail = raw_detail
            except ValueError:
                pass
            if detail.get("code") == "JOPLIN_NOTE_CONFLICT":
                draft.conflict_id = str(detail.get("conflict_id") or "") or None
                merged = detail.get("merged")
                if isinstance(merged, dict):
                    draft.merged_title = str(merged.get("title") or "")
                    draft.merged_body = str(merged.get("body") or "")
                self.joplin_panel_state.set_mode(scope, "conflict")
                await self.render_joplin_mode(agent_id)
                self.notify(
                    "Joplin changed externally. Review merge, keep Joplin, save a conflict copy, or explicitly overwrite.",
                    severity="warning",
                )
                return
            self.notify(f"Joplin save failed: {exc}", severity="error")
            return
        except Exception as exc:
            self.notify(f"Joplin save failed: {exc}", severity="error")
            return
        self.joplin_panel_state.finish_edit(scope)
        self.notify("Joplin note saved.")
        await self.load_joplin_notes(agent_id)

    async def ensure_joplin_available(self) -> bool:
        await self.refresh_joplin_status()
        if self.joplin_configured and self.joplin_available:
            return True
        self.notify(
            self.format_joplin_unavailable_summary(self.joplin_status),
            severity="warning",
        )
        return False

    def format_joplin_unavailable_summary(self, status: dict[str, Any]) -> str:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        code = str(error.get("code") or "JOPLIN_UNAVAILABLE")
        message = str(error.get("message") or "")
        if message:
            return f"{code}: {message}"
        return code

    def format_joplin_unavailable(self, status: dict[str, Any]) -> str:
        error = status.get("error") if isinstance(status.get("error"), dict) else {}
        lines = [
            "Joplin unavailable",
            f"Code: {error.get('code', 'JOPLIN_UNAVAILABLE')}",
            f"Message: {error.get('message', '')}",
        ]
        remediation = error.get("remediation")
        if remediation:
            lines.append(f"Remediation: {remediation}")
        return "\n".join(lines)

    def format_joplin_time(self, value: Any) -> str:
        if value is None:
            return ""
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return ""
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")
