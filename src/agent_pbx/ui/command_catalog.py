from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from textual.app import SystemCommand

from ..terminal import EMBEDDED_SCROLL_MODE_CHILD, EMBEDDED_SCROLL_MODE_TMUX
from . import model_commands, preferences


ACCESSIBLE_TUI_THEME = preferences.ACCESSIBLE_TUI_THEME
ADAPTIVE_TUI_LAYOUT = preferences.ADAPTIVE_TUI_LAYOUT
COMPACT_TUI_LAYOUT = preferences.COMPACT_TUI_LAYOUT
DEFAULT_TUI_THEME = preferences.DEFAULT_TUI_THEME
JOPLIN_COPY_MODE_COPY_FIRST = preferences.JOPLIN_COPY_MODE_COPY_FIRST
JOPLIN_COPY_MODE_TMUX_CAPTURE = preferences.JOPLIN_COPY_MODE_TMUX_CAPTURE
JOPLIN_COPY_MODE_TRANSCRIPT_FIRST = preferences.JOPLIN_COPY_MODE_TRANSCRIPT_FIRST
MINIMAL_TUI_THEME = preferences.MINIMAL_TUI_THEME
SPLIT_TUI_LAYOUT = preferences.SPLIT_TUI_LAYOUT
TINY_TUI_LAYOUT = preferences.TINY_TUI_LAYOUT


@dataclass(frozen=True)
class CodexTerminalModes:
    scrollback: str
    raw: str
    default: str


@dataclass(frozen=True)
class CommandDescriptor:
    title: str
    help: str
    callback: Callable[[], object]

    def to_system_command(self) -> SystemCommand:
        return SystemCommand(self.title, self.help, self.callback)


REVIEWED_COMMAND_TITLES = (
    '/agent inspect',
    '/agent launch',
    '/agent native adopt',
    '/agent native preview',
    '/agent native rollback',
    '/agent repair apply',
    '/agent repair preview',
    '/agents migrate apply',
    '/agents migrate preview',
    '/agents migrate rollback',
    '/agents prune',
    '/agents prune apply',
    '/agents prune forks',
    '/agents prune stale',
    '/agents prune undo',
    '/campaign copy',
    '/campaign export',
    '/campaign monitor',
    '/campaign report',
    '/campaigns',
    '/cancel',
    '/codex',
    '/codex config',
    '/codex config refresh',
    '/codex config save',
    '/codex keymap',
    '/codex keymap portable',
    '/codex keymap reset',
    '/codex mcp wire',
    '/codex model',
    '/codex model adopt',
    '/codex model astra-max',
    '/codex model astra-ultra',
    '/codex model astra-xhigh',
    '/codex model cleanup plan',
    '/codex model default luna-high',
    '/codex model default sol-high',
    '/codex model default sol-max',
    '/codex model default sol-medium',
    '/codex model default sol-xhigh',
    '/codex model default terra-xhigh',
    '/codex model legacy-5.5',
    '/codex model luna-high',
    '/codex model inspect',
    '/codex model native',
    '/codex model plan',
    '/codex model return',
    '/codex model sol-high',
    '/codex model sol-max',
    '/codex model sol-medium',
    '/codex model sol-ultra',
    '/codex model sol-xhigh',
    '/codex model terra-xhigh',
    '/codex restart',
    '/codex terminal default',
    '/codex terminal raw',
    '/codex terminal scrollback',
    '/codex update',
    '/commands reload',
    '/ctrlc',
    '/detail',
    '/editor',
    '/editor check',
    '/editor close',
    '/editor copy path',
    '/editor copy text',
    '/editor focus',
    '/editor fullscreen',
    '/editor open',
    '/editor reveal',
    '/editor revert',
    '/editor save',
    '/editor terminal',
    '/editor terminal close',
    '/editor terminal forget',
    '/editor terminal nano',
    '/editor terminal nvim',
    '/editor terminal open',
    '/editor terminal vim',
    '/editor text',
    '/editor workspace',
    '/esc',
    '/evidence export',
    '/files',
    '/files copy path',
    '/files copy text',
    '/files open editor',
    '/files open terminal',
    '/files open text',
    '/files refresh',
    '/files search',
    '/files search clear',
    '/files up',
    '/gitdiff',
    '/gitpush',
    '/gitstageandcommit',
    '/gitstatus',
    '/hide agent',
    '/issue',
    '/issue clear',
    '/issue mitigate',
    '/issue refresh',
    '/issue url',
    '/joplin',
    '/joplin copy',
    '/joplin copy force',
    '/joplin copy mode copy-first',
    '/joplin copy mode tmux-capture',
    '/joplin copy mode transcript-first',
    '/joplin copy report',
    '/joplin delete',
    '/joplin edit',
    '/joplin log start',
    '/joplin log stop',
    '/joplin new',
    '/joplin note copy',
    '/joplin note copy source',
    '/joplin note paste',
    '/joplin preview',
    '/joplin promote conflict',
    '/joplin prompt',
    '/joplin refresh',
    '/joplin rename',
    '/joplin retry write',
    '/joplin save',
    '/joplin sync',
    '/joplin validate profile',
    '/latest',
    '/layout adaptive',
    '/layout compact',
    '/layout split',
    '/layout tiny',
    '/navigation back',
    '/navigation forward',
    '/operator action validate',
    '/operator authority',
    '/operator focus',
    '/operator fork next',
    '/operator fork prev',
    '/operator fork review',
    '/operator git commit',
    '/operator git recover',
    '/operator handoff approve',
    '/operator handoff launch',
    '/operator handoff preflight',
    '/operator handoffs',
    '/operator helpers',
    '/operator hide',
    '/operator history',
    '/operator kb',
    '/operator kb compile',
    '/operator kb detail',
    '/operator kb history',
    '/operator kb misses',
    '/operator kb promote',
    '/operator kb proposed',
    '/operator kb proposed detail',
    '/operator kb reindex',
    '/operator kb reject',
    '/operator kb retire',
    '/operator kb search',
    '/operator kb seed',
    '/operator knowledge links',
    '/operator knowledge send',
    '/operator project spawn',
    '/operator project spawn cancel',
    '/operator project spawn inspect',
    '/operator purge',
    '/operator ready actions',
    '/operator resources',
    '/operator restart',
    '/operator resume',
    '/operator start',
    '/operator stop',
    '/palette history',
    '/palette history clear',
    '/palette history persistence off',
    '/palette history persistence on',
    '/pbx keymap',
    '/ping',
    '/plan',
    '/plan latest',
    '/plan thread',
    '/pr',
    '/pr merge',
    '/pr refresh',
    '/pr review',
    '/pr url',
    '/pr validate',
    '/purge agent',
    '/refresh',
    '/restart',
    '/show hidden agents',
    '/subagents',
    '/theme cyberpunk',
    '/theme cyberpunk-accessible',
    '/theme minimal',
    '/thread',
    '/thread export',
    '/tmux',
    '/tmux pop in',
    '/tmux pop out',
    '/tmux scroll mode child',
    '/tmux scroll mode history',
    '/tmux scrollback',
    '/tmux terminal',
    '/unblock',
    '/unhide agent',
    '/view fullscreen',
    '/workerbee',
    '/workerbee access',
    '/workerbee content',
    '/workerbee policy',
    '/workerbee preflight',
    '/workerbee run',
    '/workerbee run cancel',
    '/workerbee run leases',
    '/workerbee run status',
    '/workerbee switch debug',
    '/workerbee switch oss',
    '/workerbee switch rollback',
    '/workerbee workspace',
    '/working',
    '/workspace launch',
)


def validate_command_titles(titles: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(str(title) for title in titles)
    if any(not title.strip() for title in normalized):
        raise ValueError("command titles must not be blank")
    if len(normalized) != len(set(normalized)):
        raise ValueError("command titles must be unique")
    return normalized


validate_command_titles(REVIEWED_COMMAND_TITLES)


class CommandCatalog:
    def __init__(self) -> None:
        self._titles: set[str] = set()

    def command(
        self,
        title: str,
        help: str,
        callback: Callable[[], object],
    ) -> SystemCommand:
        descriptor = CommandDescriptor(str(title), str(help), callback)
        if not descriptor.title.strip():
            raise ValueError("command title must not be blank")
        if descriptor.title in self._titles:
            raise ValueError(f"duplicate system command title: {descriptor.title}")
        self._titles.add(descriptor.title)
        return descriptor.to_system_command()

    def adopt(self, command: SystemCommand) -> SystemCommand:
        """Register a dynamic command under the same duplicate guard."""

        return self.command(command.title, command.help, command.callback)


def iter_system_commands(
    app: Any,
    *,
    terminal_modes: CodexTerminalModes,
) -> Iterable[SystemCommand]:
    catalog = CommandCatalog()
    yield catalog.command("/refresh", "Refresh agents, events, and selected agent", app.palette_refresh)
    yield catalog.command("/detail", "Request detail for the selected agent", app.palette_request_detail)
    yield catalog.command("/ping", "Ping the selected nohup-mode agent", app.palette_ping)
    yield catalog.command("/unblock", "Mark the selected agent working", app.palette_mark_working)
    yield catalog.command("/working", "Mark the selected agent working", app.palette_mark_working)
    yield catalog.command("/cancel", "Mark the selected agent canceled", app.palette_mark_canceled)
    yield catalog.command(
        "/agent inspect",
        "Inspect reconciled Agent/Operator lifecycle state",
        app.palette_lifecycle_inspect,
    )
    yield catalog.command(
        "/agent repair preview",
        "Preview a stale runtime mapping repair",
        app.palette_lifecycle_repair_preview,
    )
    yield catalog.command(
        "/agent repair apply",
        "Apply the latest reviewed runtime mapping repair",
        app.palette_lifecycle_repair_apply,
    )
    yield catalog.command(
        "/agent native preview",
        "Preview pane-preserving native tmux adoption for the selected Agent",
        app.palette_agent_pane_adoption_preview,
    )
    yield catalog.command(
        "/agent native adopt",
        "Apply the latest reviewed native tmux adoption",
        app.palette_agent_pane_adoption_apply,
    )
    yield catalog.command(
        "/agent native rollback",
        "Restore the latest adopted Agent pane to its source layout",
        app.palette_agent_pane_adoption_rollback,
    )
    yield catalog.command("/esc", "Send Escape to the selected agent", app.palette_escape)
    yield catalog.command("/ctrlc", "Send Ctrl+C to the selected tmux pane", app.palette_ctrl_c)
    yield catalog.command(
        "/restart",
        "Restart the selected tmux Codex pane using its persisted model",
        app.palette_tmux_restart,
    )
    yield catalog.command("/codex restart", "Restart the selected tmux Codex pane", app.palette_tmux_restart)
    yield catalog.command("/codex", "Open global Codex config", app.palette_codex_config)
    yield catalog.command("/codex config", "Open global Codex config", app.palette_codex_config)
    yield catalog.command("/codex config refresh", "Refresh global Codex config", app.palette_codex_config_refresh)
    yield catalog.command("/codex config save", "Save the Codex config key/value editor", app.palette_codex_config_save)
    yield catalog.command("/pbx keymap", "Show PBX and Codex key ownership", app.palette_pbx_keymap)
    yield catalog.command("/codex keymap", "Open the native Codex /keymap viewer", app.palette_codex_keymap)
    yield catalog.command(
        "/subagents",
        "Open the native Codex subagent inspector for the selected runtime",
        app.palette_codex_subagents,
    )
    yield catalog.command("/codex keymap portable", "Save the portable Codex newline keymap", app.palette_codex_keymap_portable)
    yield catalog.command("/codex keymap reset", "Remove the Agent PBX keymap overrides", app.palette_codex_keymap_reset)
    yield catalog.command("/codex mcp wire", "Write Agent PBX MCP config to global Codex config", app.palette_codex_mcp_wire)
    yield catalog.command("/codex update", "Update host Codex CLI via the Agent PBX daemon", app.palette_codex_update)
    yield from model_commands.iter_model_commands(app, catalog)
    yield catalog.command("/codex terminal scrollback", "Use PBX-compatible Codex scrollback for managed panes", lambda: app.set_codex_terminal_mode(terminal_modes.scrollback))
    yield catalog.command("/codex terminal raw", "Use Codex raw scrollback for managed panes", lambda: app.set_codex_terminal_mode(terminal_modes.raw))
    yield catalog.command("/codex terminal default", "Use Codex default terminal behavior for managed panes", lambda: app.set_codex_terminal_mode(terminal_modes.default))
    yield catalog.command("/tmux", "Toggle tmux direct mode", app.palette_toggle_tmux)
    yield catalog.command(
        "/tmux terminal",
        "Toggle the embedded native tmux terminal",
        app.palette_toggle_embedded_terminal,
    )
    yield catalog.command(
        "/tmux scrollback",
        "Enter copy mode for the focused embedded tmux pane",
        app.palette_tmux_scrollback,
    )
    yield catalog.command(
        "/tmux scroll mode history",
        "Use wheel and swipe for tmux-owned history",
        lambda: app.set_embedded_terminal_scroll_mode(
            EMBEDDED_SCROLL_MODE_TMUX
        ),
    )
    yield catalog.command(
        "/tmux scroll mode child",
        "Forward wheel and swipe to the Codex child terminal",
        lambda: app.set_embedded_terminal_scroll_mode(
            EMBEDDED_SCROLL_MODE_CHILD
        ),
    )
    yield catalog.command(
        "/tmux pop out",
        "Open the selected runtime in its native tmux client",
        app.palette_tmux_pop_out,
    )
    yield catalog.command(
        "/tmux pop in",
        "Return the recorded outer tmux client to Agent PBX",
        app.palette_tmux_pop_in,
    )
    yield catalog.command("/latest", "Open the Latest tab", app.palette_latest)
    yield catalog.command("/thread", "Open the Thread tab", app.palette_thread)
    yield catalog.command("/thread export", "Export the selected Agent thread through the daemon evidence center", app.palette_thread_export)
    yield catalog.command("/files", "Open and refresh the Files tab", app.palette_files)
    yield catalog.command("/files refresh", "Refresh the selected agent Files tab", app.palette_files_refresh)
    yield catalog.command("/files up", "Open the parent directory in the Files tab", app.palette_files_up)
    yield catalog.command("/files search", "Focus file search for the selected agent", app.palette_files_search)
    yield catalog.command("/files search clear", "Clear file search results", app.palette_files_search_clear)
    yield catalog.command("/files copy path", "Copy the selected file path", app.palette_files_copy_path)
    yield catalog.command("/files copy text", "Copy the selected file text", app.palette_files_copy_text)
    yield catalog.command("/files open editor", "Open the selected file in the configured editor", app.palette_editor)
    yield catalog.command("/files open terminal", "Open the selected file in the managed native editor", app.palette_editor_terminal_open)
    yield catalog.command("/files open text", "Open the selected file in the Textual editor", app.palette_editor_text)
    yield catalog.command("/editor", "Open the selected file in the configured editor", app.palette_editor)
    yield catalog.command("/editor open", "Open the selected file in the configured editor", app.palette_editor)
    yield catalog.command("/editor workspace", "Open the configured project editor workspace", app.palette_editor_workspace)
    yield catalog.command("/editor text", "Open the selected file in the Textual editor", app.palette_editor_text)
    yield catalog.command("/editor save", "Save the current editor file", app.palette_editor_save)
    yield catalog.command("/editor revert", "Revert the current editor file from disk", app.palette_editor_revert)
    yield catalog.command("/editor check", "Run diagnostics for the current editor file", app.palette_editor_check)
    yield catalog.command("/editor copy path", "Copy the current editor file path", app.palette_editor_copy_path)
    yield catalog.command("/editor copy text", "Copy the current editor text", app.palette_editor_copy_text)
    yield catalog.command(
        "/view fullscreen",
        "Toggle the active right-side workspace full width",
        app.palette_right_pane_fullscreen,
    )
    yield catalog.command(
        "/editor fullscreen",
        "Toggle the active right-side workspace full width (compatibility alias)",
        app.palette_right_pane_fullscreen,
    )
    yield catalog.command("/editor terminal", "Open or attach the managed native workspace", app.palette_editor_terminal)
    yield catalog.command("/editor terminal open", "Open the selected file in the managed native editor", app.palette_editor_terminal_open)
    yield catalog.command("/editor terminal nvim", "Open or attach the Neovim workspace", app.palette_editor_terminal_nvim)
    yield catalog.command("/editor terminal vim", "Open or attach the Vim workspace", app.palette_editor_terminal_vim)
    yield catalog.command("/editor terminal nano", "Open or attach the Nano workspace", app.palette_editor_terminal_nano)
    yield catalog.command("/editor terminal close", "Stop the managed terminal editor", app.palette_editor_terminal_close)
    yield catalog.command(
        "/editor terminal forget",
        "Forget a stale managed terminal editor mapping",
        app.palette_editor_terminal_forget,
    )
    yield catalog.command("/editor close", "Close the current editor file", app.palette_editor_close)
    yield catalog.command("/editor focus", "Focus the Editor tab", app.palette_editor_focus)
    yield catalog.command("/editor reveal", "Reveal the current editor file in Files", app.palette_editor_reveal)
    yield catalog.command("/workerbee", "Open and refresh the WorkerBee tab", app.palette_workerbee)
    yield catalog.command("/workerbee preflight", "Check WorkerBee MCP bootstrap capability", app.palette_workerbee_preflight)
    if not app.workerbee_controlled_run_controls_hidden:
        yield catalog.command(
            "/workerbee run",
            "Review pending PBX-controlled WorkerBee runs",
            app.palette_workerbee_controlled_runs,
        )
        yield catalog.command(
            "/workerbee run status",
            "Show recent PBX-controlled WorkerBee run state",
            app.palette_workerbee_controlled_run_status,
        )
        yield catalog.command(
            "/workerbee run cancel",
            "Review and cancel an active PBX-controlled WorkerBee run",
            app.palette_workerbee_controlled_run_cancel,
        )
        yield catalog.command(
            "/workerbee run leases",
            "Review or revoke bounded WorkerBee campaign trust",
            app.palette_workerbee_controlled_run_leases,
        )
    if not app.workerbee_access_controls_hidden:
        yield catalog.command("/workerbee access", "Manage the selected Agent WorkerBee authority", app.palette_workerbee_access)
    if not app.workerbee_workspace_controls_hidden:
        yield catalog.command("/workerbee workspace", "Manage the selected Agent WorkerBee workspace", app.palette_workerbee_workspace)
        yield catalog.command("/workerbee policy", "Manage the selected Agent WorkerBee workspace policy", app.palette_workerbee_workspace)
    if not app.workerbee_snapshot_controls_hidden:
        yield catalog.command("/workerbee content", "Manage appliance snapshot content for the selected Agent", app.palette_workerbee_snapshot)
    yield catalog.command("/workerbee switch oss", "Preview switching future sessions to OSS WorkerBee", app.palette_workerbee_switch_oss)
    yield catalog.command("/workerbee switch debug", "Preview switching future sessions to platform-debug WorkerBee", app.palette_workerbee_switch_debug)
    yield catalog.command("/workerbee switch rollback", "Preview rollback of the last verified WorkerBee switch", app.palette_workerbee_switch_rollback)
    yield catalog.command("/campaigns", "Open and refresh operator campaigns", app.palette_campaigns)
    yield catalog.command("/campaign monitor", "Ask the root operator to monitor selected campaign", app.palette_campaign_monitor)
    yield catalog.command("/campaign report", "View the selected campaign report", app.palette_campaign_report)
    yield catalog.command("/campaign copy", "Copy selected campaign to a new Joplin note", app.palette_campaign_copy)
    yield catalog.command("/campaign export", "Export the selected campaign through the daemon evidence center", app.palette_campaign_export)
    yield catalog.command("/evidence export", "Export a filtered evidence case for the selected Agent", app.palette_evidence_export)
    yield catalog.command("/pr", "Open and refresh pull requests", app.palette_pull_requests)
    yield catalog.command("/pr refresh", "Refresh pull requests", app.palette_pull_requests_refresh)
    yield catalog.command("/pr review", "Ask selected agent to review the selected PR", app.palette_pull_request_review)
    yield catalog.command("/pr validate", "Ask selected agent to run WorkerBee validation for the selected PR", app.palette_pull_request_validate)
    yield catalog.command("/pr url", "Show the selected PR URL", app.palette_pull_request_url)
    yield catalog.command("/pr merge", "Merge selected PR when enabled", app.palette_pull_request_merge)
    yield catalog.command("/issue", "Open and refresh GitHub issues", app.palette_issues)
    yield catalog.command("/issue refresh", "Refresh GitHub issues", app.palette_issues_refresh)
    yield catalog.command("/issue mitigate", "Ask selected agent to mitigate the selected issue", app.palette_issue_mitigate)
    yield catalog.command("/issue url", "Show the selected issue URL", app.palette_issue_url)
    yield catalog.command("/issue clear", "Comment and close selected issue when enabled", app.palette_issue_clear)
    if app.joplin_configured:
        yield catalog.command("/joplin", "Open and refresh the Joplin tab", app.palette_joplin)
        yield catalog.command("/joplin refresh", "Refresh scoped Joplin notes", app.palette_joplin_refresh)
        yield catalog.command("/joplin new", "Create a scoped Joplin note", app.palette_joplin_new)
        yield catalog.command("/joplin rename", "Rename the selected Joplin note", app.palette_joplin_rename)
        yield catalog.command("/joplin delete", "Delete the selected Joplin note", app.palette_joplin_delete)
        yield catalog.command(
            "/joplin prompt",
            "Compose a PBX-expanded Joplin prompt for the native Codex pane",
            app.palette_joplin_prompt,
        )
        yield catalog.command("/joplin copy", "Copy latest tmux response or report to Joplin", app.palette_joplin_copy)
        yield catalog.command("/joplin copy force", "Copy even when the latest response was already saved", app.palette_joplin_copy_force)
        yield catalog.command("/joplin copy report", "Copy latest PBX report to Joplin", app.palette_joplin_copy_report)
        yield catalog.command("/joplin copy mode copy-first", "Use /copy before transcript for Joplin copies", lambda: app.palette_joplin_copy_mode(JOPLIN_COPY_MODE_COPY_FIRST))
        yield catalog.command("/joplin copy mode transcript-first", "Use transcript before /copy for Joplin copies", lambda: app.palette_joplin_copy_mode(JOPLIN_COPY_MODE_TRANSCRIPT_FIRST))
        yield catalog.command("/joplin copy mode tmux-capture", "Use visible tmux capture for Joplin copies", lambda: app.palette_joplin_copy_mode(JOPLIN_COPY_MODE_TMUX_CAPTURE))
        yield catalog.command("/joplin log start", "Start Joplin LOG for the selected agent", app.palette_joplin_log_start)
        yield catalog.command("/joplin log stop", "Stop Joplin LOG for the selected agent", app.palette_joplin_log_stop)
        yield catalog.command("/joplin save", "Save the selected Joplin note body", app.palette_joplin_save)
        yield catalog.command("/joplin edit", "Edit the selected Joplin note", app.palette_joplin_edit)
        yield catalog.command("/joplin preview", "Preview the current Joplin draft", app.palette_joplin_preview)
        yield catalog.command("/joplin note copy", "Copy selected text or the current note source", app.palette_joplin_note_copy)
        yield catalog.command("/joplin note copy source", "Copy the complete current note source", app.palette_joplin_note_copy_source)
        yield catalog.command("/joplin note paste", "Paste clipboard text into the active note editor", app.palette_joplin_note_paste)
        yield catalog.command(
            "/joplin promote conflict",
            "Promote a selected local Joplin conflict to a synchronizable note",
            app.palette_joplin_promote_conflict,
        )
        yield catalog.command(
            "/joplin retry write",
            "Retry the selected Agent's latest failed targeted Joplin write",
            app.palette_joplin_retry_write,
        )
        yield catalog.command(
            "/joplin sync",
            "Validate the complete Joplin profile, including E2EE",
            app.palette_joplin_sync,
        )
        yield catalog.command(
            "/joplin validate profile",
            "Validate the complete Joplin profile, including E2EE",
            app.palette_joplin_sync,
        )
    yield catalog.command("/plan", "Toggle plan mode for the selected agent", app.palette_toggle_plan_mode)
    yield catalog.command("/plan latest", "Show latest report plan options", app.palette_plan_latest)
    yield catalog.command("/plan thread", "Show selected thread plan options", app.palette_plan_thread)
    yield catalog.command("/operator start", "Start a new operator agent", app.palette_operator_start)
    yield catalog.command("/operator focus", "Focus the Operators table", app.palette_operator_focus)
    yield catalog.command(
        "/operator authority",
        "Manage the selected Operator execution authority and workspace set",
        app.palette_operator_authority,
    )
    yield catalog.command(
        "/operator history",
        "Show selected operator session history",
        app.palette_operator_history,
    )
    yield catalog.command(
        "/operator resume",
        "Resume the selected operator from local Codex history",
        app.palette_operator_resume,
    )
    yield catalog.command("/operator restart", "Restart the selected operator", app.palette_operator_restart)
    yield catalog.command("/operator stop", "Stop the selected operator", app.palette_operator_stop)
    yield catalog.command("/operator hide", "Hide the selected operator", app.palette_operator_hide)
    yield catalog.command("/operator purge", "Purge the selected operator", app.palette_operator_purge)
    yield catalog.command(
        "/operator git commit",
        "Commit an exact approved subset for a stopped broker-required edit fork",
        app.palette_operator_git_commit,
    )
    yield catalog.command(
        "/operator git recover",
        "Recover one exact pending broker transaction for the stopped edit fork",
        app.palette_operator_git_recover,
    )
    yield catalog.command("/operator fork next", "View the next fork pane for the selected operator", app.palette_operator_fork_next)
    yield catalog.command("/operator fork prev", "View the previous fork pane for the selected operator", app.palette_operator_fork_prev)
    yield catalog.command("/operator fork review", "Start a read-only review fork for the selected operator", app.palette_operator_fork_review)
    yield catalog.command("/operator handoffs", "Show handoffs for the selected operator", app.palette_operator_handoffs)
    yield catalog.command(
        "/operator ready actions",
        "Review approved local actions waiting on guarded TUI controls",
        app.palette_operator_ready_actions,
    )
    yield catalog.command(
        "/operator action validate",
        "Mark the selected fork's post-resume validation complete",
        app.palette_operator_action_validate,
    )
    yield catalog.command("/operator handoff preflight", "Preview delivery readiness for the oldest pending operator handoff", app.palette_operator_handoff_preflight)
    yield catalog.command("/operator handoff approve", "Open exact-ID Operator handoff approvals", app.palette_operator_handoff_approve)
    yield catalog.command("/operator handoff launch", "Launch the required fork for a pending handoff", app.palette_operator_handoff_launch)
    yield catalog.command("/operator knowledge links", "Show knowledge links for the selected operator", app.palette_operator_knowledge_links)
    yield catalog.command("/operator knowledge send", "Approve and send the oldest pending knowledge handoff", app.palette_operator_knowledge_send)
    yield catalog.command("/operator kb", "Show active KB entries for the selected operator", app.palette_operator_kb)
    yield catalog.command("/operator kb detail", "Show the latest active KB entry body", app.palette_operator_kb_detail)
    yield catalog.command("/operator kb proposed", "Show proposed KB entries for the selected operator", app.palette_operator_kb_proposed)
    yield catalog.command("/operator kb proposed detail", "Show the oldest proposed KB entry body", app.palette_operator_kb_proposed_detail)
    yield catalog.command("/operator kb search", "Search active KB entries with hybrid retrieval", app.palette_operator_kb_search)
    yield catalog.command("/operator kb history", "Show recent KB retrieval queries", app.palette_operator_kb_history)
    yield catalog.command("/operator kb misses", "Show KB retrieval misses and rejected matches", app.palette_operator_kb_misses)
    yield catalog.command("/operator kb seed", "Ask the selected operator to propose durable KB entries", app.palette_operator_kb_seed)
    yield catalog.command("/operator kb compile", "Compile KB proposals from the selected operator's latest report", app.palette_operator_kb_compile)
    yield catalog.command("/operator kb reindex", "Rebuild the operator KB search index", app.palette_operator_kb_reindex)
    yield catalog.command("/operator kb promote", "Promote the oldest clean proposed KB entry", app.palette_operator_kb_promote)
    yield catalog.command("/operator kb reject", "Reject the oldest proposed KB entry", app.palette_operator_kb_reject)
    yield catalog.command("/operator kb retire", "Retire the selected active KB entry", app.palette_operator_kb_retire)
    yield catalog.command(
        "/operator helpers",
        "Review durable helper lifecycle state",
        app.palette_operator_helpers,
    )
    yield catalog.command(
        "/operator resources",
        "Review pending typed dynamic-resource approvals",
        app.palette_operator_resources,
    )
    yield catalog.command("/operator project spawn", "Launch the oldest pending review project spawn", app.palette_operator_project_spawn)
    yield catalog.command("/operator project spawn inspect", "Inspect the oldest pending review project spawn", app.palette_operator_project_spawn_inspect)
    yield catalog.command("/operator project spawn cancel", "Cancel the oldest pending review project spawn", app.palette_operator_project_spawn_cancel)
    yield catalog.command("/commands reload", "Reload custom slash commands", app.palette_reload_custom_slash_commands)
    yield catalog.command(
        "/palette history",
        "Show command-name history and persistence status",
        app.palette_history_show,
    )
    yield catalog.command(
        "/palette history clear",
        "Clear command palette history",
        app.palette_history_clear,
    )
    yield catalog.command(
        "/palette history persistence on",
        "Persist safe command names across TUI restarts",
        lambda: app.palette_history_set_persistence(True),
    )
    yield catalog.command(
        "/palette history persistence off",
        "Keep command history in memory only",
        lambda: app.palette_history_set_persistence(False),
    )
    yield catalog.command(
        "/navigation back",
        "Return to the previous Agent, Operator, and tab destination",
        app.palette_navigation_back,
    )
    yield catalog.command(
        "/navigation forward",
        "Advance to the next Agent, Operator, and tab destination",
        app.palette_navigation_forward,
    )
    yield catalog.command("/agents prune", "Preview safe pruning for old terminal caller agents", app.palette_agents_prune)
    yield catalog.command("/agents prune apply", "Apply the latest agent prune preview", app.palette_agents_prune_apply)
    yield catalog.command("/agents prune stale", "Preview stale nonterminal caller pruning", app.palette_agents_prune_stale)
    yield catalog.command("/agents prune forks", "Preview safe operator-fork pruning", app.palette_agents_prune_forks)
    yield catalog.command("/agents prune undo", "Undo the latest agent prune batch", app.palette_agents_prune_undo)
    yield catalog.command(
        "/workspace launch",
        "Choose a Git project and launch an Agent or Agent + Operator",
        app.palette_managed_agent_launch,
    )
    yield catalog.command(
        "/agent launch",
        "Choose a Git project and launch a managed workspace",
        app.palette_managed_agent_launch,
    )
    yield catalog.command(
        "/agents migrate preview",
        "Preview restart/resume migration for starred Agents and Operators",
        app.palette_runtime_migration_preview,
    )
    yield catalog.command(
        "/agents migrate apply",
        "Apply the latest eligible runtime migration preview",
        app.palette_runtime_migration_apply,
    )
    yield catalog.command(
        "/agents migrate rollback",
        "Restore mappings from the latest runtime migration snapshot",
        app.palette_runtime_migration_rollback,
    )
    for command in app.palette_native_plan_selector_commands():
        yield catalog.adopt(command)
    for command in app.palette_dynamic_plan_commands():
        yield catalog.adopt(command)
    if app.is_tmux_direct_enabled():
        yield catalog.command("/gitstatus", "Run !git status in the selected tmux pane", app.palette_git_status)
        yield catalog.command("/gitdiff", "Run !git diff with an optional target in tmux", app.palette_git_diff)
        yield catalog.command("/gitpush", "Run !git push origin with an optional branch in tmux", app.palette_git_push)
        yield catalog.command("/gitstageandcommit", "Ask Codex to stage and commit changes", app.palette_git_stage_and_commit)
        for command in app.palette_custom_slash_commands():
            yield catalog.adopt(command)
    yield catalog.command("/hide agent", "Hide the selected agent from the Agents view", app.palette_hide_agent)
    yield catalog.command("/show hidden agents", "Toggle hidden agents in the Agents view", app.palette_toggle_hidden_agents)
    yield catalog.command("/unhide agent", "Unhide the selected hidden agent", app.palette_unhide_agent)
    yield catalog.command("/purge agent", "Hide selected agent and delete its thread data", app.palette_purge_agent)
    yield catalog.command("/theme cyberpunk", "Use the Cyberpunk theme", lambda: app.palette_set_theme(DEFAULT_TUI_THEME))
    yield catalog.command(
        "/theme cyberpunk-accessible",
        "Use the deuteranopia-friendly Cyberpunk theme",
        lambda: app.palette_set_theme(ACCESSIBLE_TUI_THEME),
    )
    yield catalog.command("/theme minimal", "Use the high-compatibility Minimal theme", lambda: app.palette_set_theme(MINIMAL_TUI_THEME))
    yield catalog.command(f"/theme {app.custom_theme_name}", "Use the custom TUI theme", lambda: app.palette_set_theme(app.custom_theme_name))
    yield catalog.command("/layout adaptive", "Use adaptive layout mode", lambda: app.palette_set_layout(ADAPTIVE_TUI_LAYOUT))
    yield catalog.command("/layout split", "Use split layout mode", lambda: app.palette_set_layout(SPLIT_TUI_LAYOUT))
    yield catalog.command("/layout compact", "Use compact layout mode", lambda: app.palette_set_layout(COMPACT_TUI_LAYOUT))
    yield catalog.command("/layout tiny", "Use tiny layout mode", lambda: app.palette_set_layout(TINY_TUI_LAYOUT))


__all__ = (
    "CodexTerminalModes",
    "CommandCatalog",
    "CommandDescriptor",
    "REVIEWED_COMMAND_TITLES",
    "iter_system_commands",
    "validate_command_titles",
)
