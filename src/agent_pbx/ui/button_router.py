from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class ButtonRouteDescriptor:
    button_id: str
    family: str


REVIEWED_BUTTON_ROUTES = (
    ButtonRouteDescriptor('agents-prune', 'agents'),
    ButtonRouteDescriptor('campaign-copy-joplin', 'campaign'),
    ButtonRouteDescriptor('campaign-monitor', 'campaign'),
    ButtonRouteDescriptor('campaign-refresh', 'campaign'),
    ButtonRouteDescriptor('campaign-report', 'campaign'),
    ButtonRouteDescriptor('clear-marks', 'clear'),
    ButtonRouteDescriptor('codex-config-load', 'codex'),
    ButtonRouteDescriptor('codex-config-refresh', 'codex'),
    ButtonRouteDescriptor('codex-config-remove', 'codex'),
    ButtonRouteDescriptor('codex-config-save', 'codex'),
    ButtonRouteDescriptor('codex-config-wire-pbx', 'codex'),
    ButtonRouteDescriptor('codex-keymap-portable', 'codex'),
    ButtonRouteDescriptor('codex-keymap-reset', 'codex'),
    ButtonRouteDescriptor('codex-model-cleanup-plan', 'codex'),
    ButtonRouteDescriptor('codex-model-plan', 'codex'),
    ButtonRouteDescriptor('codex-model-restart', 'codex'),
    ButtonRouteDescriptor('codex-model-save-default', 'codex'),
    ButtonRouteDescriptor('codex-secret-remove', 'codex'),
    ButtonRouteDescriptor('codex-secret-replace', 'codex'),
    ButtonRouteDescriptor('codex-update', 'codex'),
    ButtonRouteDescriptor('delete-queued', 'delete'),
    ButtonRouteDescriptor('editor-check', 'editor'),
    ButtonRouteDescriptor('editor-close', 'editor'),
    ButtonRouteDescriptor('editor-copy-path', 'editor'),
    ButtonRouteDescriptor('editor-copy-text', 'editor'),
    ButtonRouteDescriptor('editor-fullscreen', 'editor'),
    ButtonRouteDescriptor('editor-revert', 'editor'),
    ButtonRouteDescriptor('editor-save', 'editor'),
    ButtonRouteDescriptor('editor-terminal', 'editor'),
    ButtonRouteDescriptor('editor-terminal-stop', 'editor'),
    ButtonRouteDescriptor('editor-text', 'editor'),
    ButtonRouteDescriptor('export-all', 'export'),
    ButtonRouteDescriptor('export-item', 'export'),
    ButtonRouteDescriptor('export-marked', 'export'),
    ButtonRouteDescriptor('files-copy-path', 'files'),
    ButtonRouteDescriptor('files-copy-text', 'files'),
    ButtonRouteDescriptor('files-create-directory', 'files'),
    ButtonRouteDescriptor('files-create-file', 'files'),
    ButtonRouteDescriptor('files-delete', 'files'),
    ButtonRouteDescriptor('files-move', 'files'),
    ButtonRouteDescriptor('files-open-editor', 'files'),
    ButtonRouteDescriptor('files-open-terminal', 'files'),
    ButtonRouteDescriptor('files-open-text', 'files'),
    ButtonRouteDescriptor('files-refresh', 'files'),
    ButtonRouteDescriptor('files-search', 'files'),
    ButtonRouteDescriptor('files-search-clear', 'files'),
    ButtonRouteDescriptor('files-trash', 'files'),
    ButtonRouteDescriptor('files-up', 'files'),
    ButtonRouteDescriptor('hide-agent', 'hide'),
    ButtonRouteDescriptor('issue-clear', 'issue'),
    ButtonRouteDescriptor('issue-mitigate', 'issue'),
    ButtonRouteDescriptor('issue-refresh', 'issue'),
    ButtonRouteDescriptor('issue-url', 'issue'),
    ButtonRouteDescriptor('joplin-conflict-copy', 'joplin'),
    ButtonRouteDescriptor('joplin-conflict-keep', 'joplin'),
    ButtonRouteDescriptor('joplin-conflict-merge', 'joplin'),
    ButtonRouteDescriptor('joplin-conflict-overwrite', 'joplin'),
    ButtonRouteDescriptor('joplin-copy-latest', 'joplin'),
    ButtonRouteDescriptor('joplin-delete', 'joplin'),
    ButtonRouteDescriptor('joplin-edit', 'joplin'),
    ButtonRouteDescriptor('joplin-log-start', 'joplin'),
    ButtonRouteDescriptor('joplin-log-stop', 'joplin'),
    ButtonRouteDescriptor('joplin-native-conflict-promote', 'joplin'),
    ButtonRouteDescriptor('joplin-new', 'joplin'),
    ButtonRouteDescriptor('joplin-note-copy', 'joplin'),
    ButtonRouteDescriptor('joplin-note-paste', 'joplin'),
    ButtonRouteDescriptor('joplin-preview', 'joplin'),
    ButtonRouteDescriptor('joplin-refresh', 'joplin'),
    ButtonRouteDescriptor('joplin-rename', 'joplin'),
    ButtonRouteDescriptor('joplin-save', 'joplin'),
    ButtonRouteDescriptor('joplin-sync', 'joplin'),
    ButtonRouteDescriptor('mark-canceled', 'mark'),
    ButtonRouteDescriptor('mark-working', 'mark'),
    ButtonRouteDescriptor('operator-fork-next', 'operator'),
    ButtonRouteDescriptor('operator-fork-prev', 'operator'),
    ButtonRouteDescriptor('operator-fork-review', 'operator'),
    ButtonRouteDescriptor('operator-hide', 'operator'),
    ButtonRouteDescriptor('operator-history', 'operator'),
    ButtonRouteDescriptor('operator-kb-active', 'operator'),
    ButtonRouteDescriptor('operator-kb-feedback-accepted', 'operator'),
    ButtonRouteDescriptor('operator-kb-feedback-miss', 'operator'),
    ButtonRouteDescriptor('operator-kb-feedback-rejected', 'operator'),
    ButtonRouteDescriptor('operator-kb-history', 'operator'),
    ButtonRouteDescriptor('operator-kb-misses', 'operator'),
    ButtonRouteDescriptor('operator-kb-promote', 'operator'),
    ButtonRouteDescriptor('operator-kb-proposed', 'operator'),
    ButtonRouteDescriptor('operator-kb-refresh', 'operator'),
    ButtonRouteDescriptor('operator-kb-reject', 'operator'),
    ButtonRouteDescriptor('operator-kb-rejected', 'operator'),
    ButtonRouteDescriptor('operator-kb-retire', 'operator'),
    ButtonRouteDescriptor('operator-kb-retired', 'operator'),
    ButtonRouteDescriptor('operator-kb-search', 'operator'),
    ButtonRouteDescriptor('operator-project-spawn', 'operator'),
    ButtonRouteDescriptor('operator-purge', 'operator'),
    ButtonRouteDescriptor('operator-restart', 'operator'),
    ButtonRouteDescriptor('operator-resume', 'operator'),
    ButtonRouteDescriptor('operator-start', 'operator'),
    ButtonRouteDescriptor('operator-stop', 'operator'),
    ButtonRouteDescriptor('ping-agent', 'ping'),
    ButtonRouteDescriptor('pr-merge', 'pr'),
    ButtonRouteDescriptor('pr-refresh', 'pr'),
    ButtonRouteDescriptor('pr-review', 'pr'),
    ButtonRouteDescriptor('pr-url', 'pr'),
    ButtonRouteDescriptor('pr-validate', 'pr'),
    ButtonRouteDescriptor('purge-agent', 'purge'),
    ButtonRouteDescriptor('request-detail', 'request'),
    ButtonRouteDescriptor('send', 'send'),
    ButtonRouteDescriptor('star-agent', 'star'),
    ButtonRouteDescriptor('start-operator', 'start'),
    ButtonRouteDescriptor('tmux-auto', 'tmux'),
    ButtonRouteDescriptor('tmux-detach', 'tmux'),
    ButtonRouteDescriptor('tmux-restart', 'tmux'),
    ButtonRouteDescriptor('tmux-select', 'tmux'),
    ButtonRouteDescriptor('tmux-send', 'tmux'),
    ButtonRouteDescriptor('toggle-hidden-agents', 'toggle'),
    ButtonRouteDescriptor('unhide-agent', 'unhide'),
    ButtonRouteDescriptor('workerbee-access', 'workerbee'),
    ButtonRouteDescriptor('workerbee-refresh', 'workerbee'),
    ButtonRouteDescriptor('workerbee-snapshot', 'workerbee'),
    ButtonRouteDescriptor('workerbee-switch-debug', 'workerbee'),
    ButtonRouteDescriptor('workerbee-switch-oss', 'workerbee'),
    ButtonRouteDescriptor('workerbee-switch-rollback', 'workerbee'),
    ButtonRouteDescriptor('workerbee-workspace', 'workerbee'),
)


def validate_button_routes(
    descriptors: Iterable[ButtonRouteDescriptor],
) -> frozenset[str]:
    materialized = tuple(descriptors)
    identifiers = tuple(descriptor.button_id for descriptor in materialized)
    if any(not identifier.strip() for identifier in identifiers):
        raise ValueError("button route IDs must not be blank")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("button route IDs must be unique")
    return frozenset(identifiers)


KNOWN_BUTTON_IDS = validate_button_routes(REVIEWED_BUTTON_ROUTES)


async def route_button_pressed(app: Any, button_id: str) -> bool:
    button_id = str(button_id or "")
    if button_id not in KNOWN_BUTTON_IDS:
        return False
    if button_id == "send":
        await app.send_input()
        return True
    if button_id == "tmux-send":
        await app.send_tmux_input()
        return True
    if button_id == "tmux-auto":
        await app.set_tmux_auto()
        return True
    if button_id == "tmux-select":
        await app.select_next_tmux_pane()
        return True
    if button_id == "tmux-detach":
        await app.detach_tmux_pane()
        return True
    if button_id == "tmux-restart":
        await app.restart_tmux_codex_session()
        return True
    if button_id == "request-detail":
        await app.request_detail()
        return True
    if button_id == "ping-agent":
        await app.ping_agent()
        return True
    if button_id == "mark-working":
        await app.mark_agent_working()
        return True
    if button_id == "mark-canceled":
        await app.mark_agent_canceled()
        return True
    if button_id == "export-item":
        await app.export_thread_scope("item")
        return True
    if button_id == "export-marked":
        await app.export_thread_scope("marked")
        return True
    if button_id == "export-all":
        await app.export_thread_scope("all")
        return True
    if button_id == "clear-marks":
        app.clear_thread_marks()
        return True
    if button_id == "delete-queued":
        await app.delete_queued_thread_commands()
        return True
    if button_id == "files-refresh":
        if app.selected_agent_id:
            await app.load_agent_files(app.selected_agent_id)
        return True
    if button_id == "files-up":
        if app.selected_agent_id:
            await app.load_parent_agent_files(app.selected_agent_id)
        return True
    if button_id == "files-search":
        if app.selected_agent_id:
            await app.search_agent_files(app.selected_agent_id)
        return True
    if button_id == "files-search-clear":
        app.clear_file_search(app.selected_agent_id)
        return True
    if button_id == "files-copy-path":
        await app.copy_selected_file_path(app.selected_agent_id)
        return True
    if button_id == "files-copy-text":
        await app.copy_selected_file_text(app.selected_agent_id)
        return True
    if button_id == "files-open-editor":
        await app.open_selected_file_in_editor(app.selected_agent_id)
        return True
    if button_id == "files-open-text":
        await app.open_selected_file_in_text_editor(app.selected_agent_id)
        return True
    if button_id == "files-open-terminal":
        await app.open_selected_file_in_terminal_editor(app.selected_agent_id)
        return True
    if button_id == "files-create-file":
        app.open_file_path_action("create_file")
        return True
    if button_id == "files-create-directory":
        app.open_file_path_action("create_directory")
        return True
    if button_id == "files-move":
        app.open_file_path_action("move")
        return True
    if button_id == "files-delete":
        await app.request_file_delete(app.selected_agent_id)
        return True
    if button_id == "files-trash":
        await app.open_file_trash(app.selected_agent_id)
        return True
    if button_id == "editor-save":
        await app.save_editor_document(app.selected_agent_id)
        return True
    if button_id == "editor-revert":
        await app.revert_editor_document(app.selected_agent_id)
        return True
    if button_id == "editor-copy-path":
        await app.copy_editor_path(app.selected_agent_id)
        return True
    if button_id == "editor-copy-text":
        await app.copy_editor_text()
        return True
    if button_id == "editor-fullscreen":
        app.toggle_right_pane_fullscreen()
        return True
    if button_id == "editor-check":
        await app.check_editor_document(app.selected_agent_id)
        return True
    if button_id == "editor-terminal":
        await app.start_terminal_editor(app.selected_agent_id)
        return True
    if button_id == "editor-text":
        await app.show_text_editor(app.selected_agent_id)
        return True
    if button_id == "editor-terminal-stop":
        await app.stop_terminal_editor(app.selected_agent_id)
        return True
    if button_id == "editor-close":
        await app.request_close_editor_document(app.selected_agent_id)
        return True
    if button_id == "workerbee-refresh":
        if app.selected_agent_id:
            await app.load_workerbee_status(app.selected_agent_id)
        return True
    if button_id == "workerbee-access":
        if app.selected_agent_id:
            await app.open_workerbee_access_manager(app.selected_agent_id)
        return True
    if button_id == "workerbee-workspace":
        if app.selected_agent_id:
            await app.open_workerbee_workspace_manager(app.selected_agent_id)
        return True
    if button_id == "workerbee-snapshot":
        if app.selected_agent_id:
            await app.open_workerbee_snapshot_manager(app.selected_agent_id)
        return True
    if button_id == "workerbee-switch-oss":
        await app.preview_workerbee_switch("oss")
        return True
    if button_id == "workerbee-switch-debug":
        await app.preview_workerbee_switch("platform_debug")
        return True
    if button_id == "workerbee-switch-rollback":
        await app.preview_workerbee_switch_rollback()
        return True
    if button_id == "pr-refresh":
        if app.selected_agent_id:
            await app.load_pull_requests(app.selected_agent_id)
        return True
    if button_id == "pr-review":
        if app.selected_agent_id:
            await app.request_pull_request_review(app.selected_agent_id)
        return True
    if button_id == "pr-validate":
        if app.selected_agent_id:
            await app.request_pull_request_validation(app.selected_agent_id)
        return True
    if button_id == "pr-url":
        if app.selected_agent_id:
            app.show_selected_pull_request_url(app.selected_agent_id)
        return True
    if button_id == "pr-merge":
        if app.selected_agent_id:
            app.confirm_merge_pull_request(app.selected_agent_id)
        return True
    if button_id == "issue-refresh":
        if app.selected_agent_id:
            await app.load_issues(app.selected_agent_id)
        return True
    if button_id == "issue-mitigate":
        if app.selected_agent_id:
            await app.request_issue_mitigation(app.selected_agent_id)
        return True
    if button_id == "issue-url":
        if app.selected_agent_id:
            app.show_selected_issue_url(app.selected_agent_id)
        return True
    if button_id == "issue-clear":
        if app.selected_agent_id:
            app.confirm_clear_issue(app.selected_agent_id)
        return True
    joplin_button_actions = {
        "joplin-new": "new",
        "joplin-rename": "rename",
        "joplin-delete": "delete",
        "joplin-refresh": "refresh",
        "joplin-copy-latest": "copy",
        "joplin-log-start": "log-start",
        "joplin-log-stop": "log-stop",
        "joplin-sync": "sync",
        "joplin-save": "save",
        "joplin-edit": "edit",
        "joplin-preview": "preview",
        "joplin-note-copy": "note-copy",
        "joplin-note-paste": "note-paste",
        "joplin-native-conflict-promote": "promote-conflict",
    }
    joplin_action = joplin_button_actions.get(str(button_id or ""))
    if joplin_action is not None:
        agent_id = app.joplin_target_agent_id()
        if agent_id:
            await app.joplin_action_for_agent(agent_id, joplin_action)
        return True
    joplin_conflict_actions = {
        "joplin-conflict-merge": "review_merge",
        "joplin-conflict-keep": "keep_joplin",
        "joplin-conflict-copy": "save_conflict_copy",
        "joplin-conflict-overwrite": "overwrite",
    }
    conflict_resolution = joplin_conflict_actions.get(str(button_id or ""))
    if conflict_resolution is not None:
        agent_id = app.joplin_target_agent_id()
        if agent_id:
            await app.resolve_joplin_conflict(agent_id, conflict_resolution)
        return True
    if button_id == "campaign-refresh":
        if app.selected_agent_id:
            await app.load_operator_campaigns(app.selected_agent_id)
        return True
    if button_id == "campaign-monitor":
        await app.monitor_selected_campaign()
        return True
    if button_id == "campaign-report":
        await app.view_selected_campaign_report()
        return True
    if button_id == "campaign-copy-joplin":
        await app.copy_selected_campaign_to_joplin()
        return True
    if button_id == "operator-kb-refresh":
        if app.selected_agent_id:
            logical_operator_id = app.operator_kb_operator_id_for_agent(
                app.selected_agent_id
            )
            mode = (
                app.operator_kb_mode_by_operator.get(logical_operator_id or "")
                if logical_operator_id
                else None
            )
            if mode == "history":
                await app.load_operator_kb_queries(app.selected_agent_id)
            elif mode == "misses":
                await app.load_operator_kb_queries(app.selected_agent_id, misses=True)
            elif mode == "search":
                await app.search_operator_kb()
            else:
                await app.load_operator_kb(app.selected_agent_id)
        return True
    if button_id == "operator-kb-search":
        await app.search_operator_kb()
        return True
    if button_id == "operator-kb-history":
        await app.load_operator_kb_queries_for_selected()
        return True
    if button_id == "operator-kb-misses":
        await app.load_operator_kb_queries_for_selected(misses=True)
        return True
    operator_kb_status_buttons = {
        "operator-kb-active": "active",
        "operator-kb-proposed": "proposed",
        "operator-kb-rejected": "rejected",
        "operator-kb-retired": "retired",
    }
    operator_kb_status = operator_kb_status_buttons.get(str(button_id or ""))
    if operator_kb_status is not None:
        if app.selected_agent_id:
            await app.load_operator_kb(
                app.selected_agent_id,
                status=operator_kb_status,
            )
        return True
    if button_id == "operator-kb-promote":
        await app.promote_oldest_operator_kb_proposal()
        return True
    if button_id == "operator-kb-reject":
        await app.reject_oldest_operator_kb_proposal()
        return True
    if button_id == "operator-kb-retire":
        await app.retire_selected_operator_kb_entry()
        return True
    feedback_buttons = {
        "operator-kb-feedback-accepted": "accepted",
        "operator-kb-feedback-rejected": "rejected",
        "operator-kb-feedback-miss": "miss",
    }
    feedback = feedback_buttons.get(str(button_id or ""))
    if feedback is not None:
        await app.feedback_selected_operator_kb(feedback)
        return True
    if button_id == "start-operator":
        await app.start_operator_agent()
        return True
    if button_id == "operator-start":
        await app.start_operator_agent()
        return True
    if button_id == "operator-history":
        await app.show_selected_operator_history()
        return True
    if button_id == "operator-resume":
        await app.resume_selected_operator()
        return True
    if button_id == "operator-restart":
        await app.restart_selected_operator()
        return True
    if button_id == "codex-update":
        await app.update_codex_cli_from_host()
        return True
    if button_id == "codex-model-plan":
        await app.show_selected_codex_model_plan()
        return True
    if button_id == "codex-model-restart":
        await app.restart_selected_with_codex_model_preset()
        return True
    if button_id == "codex-model-save-default":
        await app.save_selected_codex_model_preset_as_default()
        return True
    if button_id == "codex-model-cleanup-plan":
        await app.show_codex_model_cleanup_plan()
        return True
    if button_id == "codex-config-refresh":
        await app.load_codex_config()
        return True
    if button_id == "codex-config-load":
        key = app.selected_codex_config_key
        if key:
            app.select_codex_config_field(key)
        else:
            app.notify("Select a Codex config row first.", severity="warning")
        return True
    if button_id == "codex-config-save":
        await app.save_codex_config_field()
        return True
    if button_id == "codex-config-remove":
        await app.remove_codex_config_field()
        return True
    if button_id == "codex-config-wire-pbx":
        await app.wire_agent_pbx_mcp_config()
        return True
    if button_id == "codex-keymap-portable":
        await app.apply_codex_keymap_preset("portable")
        return True
    if button_id == "codex-keymap-reset":
        await app.apply_codex_keymap_preset("reset")
        return True
    if button_id == "codex-secret-replace":
        await app.replace_codex_secret(remove=False)
        return True
    if button_id == "codex-secret-remove":
        await app.replace_codex_secret(remove=True)
        return True
    if button_id == "operator-stop":
        await app.stop_selected_operator()
        return True
    if button_id == "operator-fork-prev":
        await app.cycle_selected_operator_fork(-1)
        return True
    if button_id == "operator-fork-next":
        await app.cycle_selected_operator_fork(1)
        return True
    if button_id == "operator-fork-review":
        await app.start_review_operator_fork()
        return True
    if button_id == "operator-project-spawn":
        await app.launch_pending_project_spawn_request()
        return True
    if button_id == "operator-hide":
        agent_id = app.selected_operator_agent_id()
        if agent_id:
            await app.dismiss_selected_agent(agent_id=agent_id, delete_thread=False)
        return True
    if button_id == "operator-purge":
        agent_id = app.selected_operator_agent_id()
        if agent_id:
            await app.dismiss_selected_agent(agent_id=agent_id, delete_thread=True)
        return True
    if button_id == "star-agent":
        app.toggle_selected_agent_star()
        return True
    if button_id == "toggle-hidden-agents":
        app.action_toggle_hidden_agents()
        return True
    if button_id == "unhide-agent":
        await app.unhide_selected_agent()
        return True
    if button_id == "hide-agent":
        await app.dismiss_selected_agent(delete_thread=False)
        return True
    if button_id == "agents-prune":
        await app.preview_agent_prune("terminal-callers")
        return True
    if button_id == "purge-agent":
        await app.dismiss_selected_agent(delete_thread=True)
        return True
    return False

__all__ = (
    "ButtonRouteDescriptor",
    "KNOWN_BUTTON_IDS",
    "REVIEWED_BUTTON_ROUTES",
    "route_button_pressed",
    "validate_button_routes",
)
