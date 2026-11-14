from types import SimpleNamespace

from textual.widgets import Button, Checkbox, DataTable, Input, Select, Static, TextArea

from agent_pbx import tui
from agent_pbx.ui import preferences
from agent_pbx.ui import screens


def test_command_joplin_codex_screen_aliases_are_object_identical() -> None:
    names = (
        "GitDiffScreen",
        "GitPushScreen",
        "CustomSlashCommandArgScreen",
        "EditorCloseConfirmScreen",
        "FileDeleteConfirmScreen",
        "FilePathActionScreen",
        "FileTrashScreen",
        "JoplinReferencePromptScreen",
        "IssueClearConfirmScreen",
        "LinkedGitCommitConfirmScreen",
        "LinkedGitCommitPathsScreen",
        "LinkedGitRecoveryConfirmScreen",
        "LinkedGitReviewArtifactScreen",
        "ManagedProjectPickerScreen",
        "JoplinNoteTitleScreen",
        "JoplinDeleteConfirmScreen",
        "JoplinPasteConfirmScreen",
        "ModelElevationScreen",
        "PullRequestMergeConfirmScreen",
        "RuntimeMigrationScreen",
        "SettingsScreen",
        "slugify",
    )

    for name in names:
        assert getattr(tui, name) is getattr(screens, name)

    assert tui.normalize_joplin_copy_mode is preferences.normalize_joplin_copy_mode


async def test_extracted_managed_project_picker_preserves_launch_order_and_payload() -> (
    None
):
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    launched: list[dict[str, object]] = []
    projects = [
        {
            "path": "/tmp/demo",
            "name": "demo",
            "branch": "dev",
            "available": True,
            "owned_by": ["agent-existing"],
        },
        {
            "path": "/tmp/next",
            "name": "next",
            "branch": "main",
            "available": True,
            "owned_by": ["agent-next"],
        },
    ]

    async def launch(**request: object) -> bool:
        assert app.screen is not screen
        assert app.query_one("#agent-id", Input) is not None
        launched.append(request)
        return True

    app.launch_managed_workspace_from_picker = launch  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.ManagedProjectPickerScreen(projects, ["/tmp"])
        app.push_screen(screen)
        await pilot.pause()

        table = screen.query_one("#managed-projects", DataTable)
        assert table.row_count == 2
        assert screen.selected_path == "/tmp/demo"
        assert screen.selected_owners == ["agent-existing"]
        table.focus()
        await pilot.press("down", "enter")
        await pilot.pause()
        assert screen.selected_path == "/tmp/next"
        assert screen.selected_owners == ["agent-next"]
        screen.query_one("#managed-agent-id", Input).value = "agent-demo"
        screen.query_one("#managed-launch-mode", Select).value = "agent_operator"
        screen.query_one("#managed-agent-profile", Select).value = "sol-xhigh"

        screen.query_one("#managed-project-launch", Button).press()
        await pilot.pause()

    assert launched == [
        {
            "project_path": "/tmp/next",
            "agent_id": "agent-demo",
            "profile_id": "sol-xhigh",
            "runtime_server_mode": "dedicated",
            "launch_mode": "agent_operator",
            "existing_owner_ids": ["agent-next"],
        }
    ]


async def test_extracted_runtime_migration_preserves_apply_and_rollback_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    applied: list[dict[str, object]] = []
    rolled_back: list[str] = []
    batch = {
        "batch_id": "batch-1",
        "eligible_count": 1,
        "blocked_count": 0,
        "retention_until": "2026-11-21T00:00:00Z",
        "candidates": [
            {
                "agent_id": "agent-demo",
                "agent_type": "caller",
                "session_id": "session-demo",
                "eligible": True,
                "blockers": [],
            }
        ],
    }

    async def apply(received: dict[str, object]) -> list[dict[str, object]]:
        assert app.screen is apply_screen
        applied.append(received)
        return []

    async def rollback(batch_id: str) -> None:
        assert app.screen is rollback_screen
        rolled_back.append(batch_id)

    app.apply_runtime_migration_batch = apply  # type: ignore[method-assign]
    app.rollback_runtime_migration_batch = rollback  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        apply_screen = screens.RuntimeMigrationScreen(batch)
        app.push_screen(apply_screen)
        await pilot.pause()

        assert (
            apply_screen.query_one("#runtime-migration-candidates", DataTable).row_count
            == 1
        )
        assert "Eligible 1 · Blocked 0" in str(
            apply_screen.query_one("#runtime-migration-summary", Static).renderable
        )
        apply_screen.query_one("#runtime-migration-apply", Button).press()
        await pilot.pause()
        assert app.screen is not apply_screen

        rollback_screen = screens.RuntimeMigrationScreen(batch)
        app.push_screen(rollback_screen)
        await pilot.pause()
        rollback_screen.query_one("#runtime-migration-rollback", Button).press()
        await pilot.pause()
        assert app.screen is not rollback_screen

    assert applied == [batch]
    assert rolled_back == ["batch-1"]


def test_extracted_settings_normalizes_constructor_values() -> None:
    screen = screens.SettingsScreen(
        visual_flash_enabled=True,
        terminal_bell_enabled=False,
        agent_blink_enabled=True,
        low_power_enabled=False,
        layout_mode="adaptive",
        split_percent=42,
        joplin_copy_mode="legacy-clipboard",
        tmux_direct_enabled=True,
        tmux_features_available=False,
        tmux_runtime_server_mode="outer-if-present",
        embedded_terminal_enabled=True,
        embedded_terminal_scroll_mode="invalid",
        tmux_popout_mode="auto",
        custom_theme_name="1337",
        theme_name="cyberpunk",
    )

    assert screen.joplin_copy_mode == "copy_first"
    assert screen.tmux_runtime_server_mode == "outer_if_present"
    assert screen.embedded_terminal_scroll_mode == "tmux"


async def test_extracted_settings_preserves_controls_and_app_hooks() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    calls: list[tuple[str, object]] = []

    def adjust_split_percent(delta: int) -> None:
        calls.append(("split", delta))
        app.split_percent += delta

    def reset_split_percent() -> None:
        calls.append(("split_reset", None))
        app.split_percent = 42

    app.adjust_split_percent = adjust_split_percent  # type: ignore[method-assign]
    app.reset_split_percent = reset_split_percent  # type: ignore[method-assign]
    app.set_layout_mode = lambda value: calls.append(("layout", value))  # type: ignore[method-assign]
    app.set_ui_theme = lambda value: calls.append(("theme", value))  # type: ignore[method-assign]
    app.set_joplin_copy_mode = lambda value: calls.append(("joplin", value))  # type: ignore[method-assign]
    app.set_tmux_runtime_server_mode = lambda value: calls.append(("runtime", value))  # type: ignore[method-assign]
    app.set_embedded_terminal_scroll_mode = lambda value: calls.append(
        ("scroll", value)
    )  # type: ignore[method-assign]
    app.set_tmux_popout_mode = lambda value: calls.append(("popout", value))  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        screen = screens.SettingsScreen(
            visual_flash_enabled=True,
            terminal_bell_enabled=False,
            agent_blink_enabled=True,
            low_power_enabled=False,
            layout_mode="adaptive",
            split_percent=42,
            joplin_copy_mode="copy_first",
            tmux_direct_enabled=False,
            tmux_features_available=False,
            tmux_runtime_server_mode="dedicated",
            embedded_terminal_enabled=False,
            embedded_terminal_scroll_mode="tmux",
            tmux_popout_mode="auto",
            custom_theme_name="1337",
            theme_name="cyberpunk",
        )
        app.push_screen(screen)
        await pilot.pause()

        assert screen.query_one("#visual-flash", Checkbox).value is True
        assert screen.query_one("#terminal-bell", Checkbox).value is False
        assert screen.query_one("#tmux-direct", Checkbox).disabled is True
        assert screen.query_one("#embedded-terminal-v2", Checkbox).disabled is True
        assert (
            screen.query_one("#embedded-terminal-scroll-mode", Select).disabled is True
        )
        assert screen.query_one("#layout-mode", Select).value == "adaptive"
        assert screen.query_one("#joplin-copy-mode", Select).value == "copy_first"
        assert screen.query_one("#tmux-runtime-mode", Select).value == "dedicated"
        assert screen.query_one("#theme-mode", Select).value == "cyberpunk"
        assert screen.query_one("#settings-close", Button).label.plain == "Close"

        await pilot.click("#split-widen")
        await pilot.pause()
        assert calls[-1] == ("split", 5)
        assert str(screen.query_one("#split-percent-label", Static).renderable) == (
            "Agents width: 47%"
        )

        await pilot.click("#split-reset")
        await pilot.pause()
        assert calls[-1] == ("split_reset", None)
        assert str(screen.query_one("#split-percent-label", Static).renderable) == (
            "Agents width: 42%"
        )

        screen.query_one("#layout-mode", Select).value = "compact"
        screen.query_one("#theme-mode", Select).value = "minimal"
        screen.query_one("#joplin-copy-mode", Select).value = "transcript_first"
        screen.query_one("#tmux-runtime-mode", Select).value = "outer_if_present"
        screen.query_one("#embedded-terminal-scroll-mode", Select).value = "child"
        screen.query_one("#tmux-popout-mode", Select).value = "suspend_attach"
        await pilot.pause()

        assert ("layout", "compact") in calls
        assert ("theme", "minimal") in calls
        assert ("joplin", "transcript_first") in calls
        assert ("runtime", "outer_if_present") in calls
        assert ("scroll", "child") in calls
        assert ("popout", "suspend_attach") in calls

        await pilot.press("escape")
        await pilot.pause()
        assert app.screen is not screen


async def test_extracted_git_diff_cancel_dismisses_without_submit() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765", tmux_direct=True)
    submitted: list[tuple[str, str]] = []

    async def submit(agent_id: str, target: str = "") -> None:
        submitted.append((agent_id, target))

    app.palette_git_diff_target = submit  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.GitDiffScreen(agent_id="agent-1")
        app.push_screen(screen)
        await pilot.pause()

        await pilot.click("#palette-gitdiff-cancel")
        await pilot.pause()

        assert app.screen is not screen

    assert submitted == []


async def test_extracted_linked_git_paths_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    reviewed: list[tuple[object, tuple[str, ...]]] = []
    context = SimpleNamespace(
        agent_id="operator-7-fork-linked",
        work_root="/tmp/linked",
        authority_sha256="7" * 64,
    )

    async def review(received_context: object, paths: tuple[str, ...]) -> None:
        reviewed.append((received_context, paths))

    app.review_linked_git_broker_commit = review  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.LinkedGitCommitPathsScreen(
            context=context,
            suggested_paths=("src/app.py", "tests/test_app.py"),
        )
        app.push_screen(submit_screen)
        await pilot.pause()

        await pilot.click("#linked-git-broker-review")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert reviewed == [
            (context, ("src/app.py", "tests/test_app.py")),
        ]

        cancel_screen = screens.LinkedGitCommitPathsScreen(context=context)
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#linked-git-broker-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert len(reviewed) == 1


async def test_extracted_linked_git_recovery_requires_exact_confirmation() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    recovered: list[tuple[object, object, str]] = []
    context = SimpleNamespace(
        agent_id="operator-7-fork-linked",
        work_root="/tmp/linked",
        authority_sha256="7" * 64,
    )
    identity = SimpleNamespace(
        path="src/app.py",
        state="file",
        mode="100644",
        raw_sha256="1" * 64,
        raw_blob_oid="2" * 40,
        index_mode="100644",
        index_oid="3" * 40,
    )
    receipt = SimpleNamespace(
        transaction_id="e" * 64,
        branch="pbx/operator-7",
        head="a" * 40,
        head_tree="b" * 40,
        index_tree="b" * 40,
        upstream="origin/dev",
        upstream_oid="c" * 40,
        index_sha256="d" * 64,
        branch_ref_sha256="5" * 64,
        branch_reflog_sha256="6" * 64,
        path_identities=(identity,),
    )
    pending = SimpleNamespace(phase="prepared", receipt=receipt)

    async def recover(
        received_context: object,
        received_pending: object,
        confirmation: str,
    ) -> None:
        recovered.append((received_context, received_pending, confirmation))

    app.recover_linked_git_broker_commit = recover  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        screen = screens.LinkedGitRecoveryConfirmScreen(
            context=context,
            pending=pending,
        )
        app.push_screen(screen)
        await pilot.pause()
        confirmation = screen.query_one("#linked-git-broker-confirmation", Input)

        confirmation.value = "wrong"
        screen.submit()
        await pilot.pause()
        assert app.screen is screen
        assert recovered == []

        confirmation.value = screen.expected_confirmation
        screen.submit()
        await pilot.pause()

        assert app.screen is not screen
        assert recovered == [
            (context, pending, screen.expected_confirmation),
        ]


async def test_extracted_model_elevation_unmount_clears_modal_agent() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    preset = tui.codex_model_preset_for("sol-max")
    assert preset is not None

    async with app.run_test() as pilot:
        screen = screens.ModelElevationScreen(
            agent_id="operator-5",
            preset=preset,
        )
        app.model_elevation_modal_agent_ids.add("operator-5")
        app.push_screen(screen)
        await pilot.pause()

        screen.dismiss()
        await pilot.pause()

    assert "operator-5" not in app.model_elevation_modal_agent_ids


async def test_extracted_file_path_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    submitted: list[tuple[str, str, str, str | None]] = []

    async def apply(
        agent_id: str,
        action: str,
        path: str,
        *,
        source: str | None = None,
    ) -> None:
        submitted.append((agent_id, action, path, source))

    app.apply_file_path_action = apply  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.FilePathActionScreen(
            agent_id="agent-1",
            action="move",
            source="src/old.py",
        )
        app.push_screen(submit_screen)
        await pilot.pause()
        submit_screen.query_one("#file-path-action-input", Input).value = "src/new.py"

        await pilot.click("#file-path-action-apply")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert submitted == [("agent-1", "move", "src/new.py", "src/old.py")]

        cancel_screen = screens.FilePathActionScreen(
            agent_id="agent-1",
            action="create_file",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#file-path-action-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert submitted == [("agent-1", "move", "src/new.py", "src/old.py")]


async def test_extracted_file_delete_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    deleted: list[tuple[str, dict[str, object]]] = []

    async def delete(agent_id: str, preview: dict[str, object]) -> None:
        deleted.append((agent_id, preview))

    app.apply_file_delete = delete  # type: ignore[method-assign]
    preview = {
        "path": "src/old.py",
        "kind": "file",
        "item_count": 1,
        "size": 12,
        "delete_mode": "trash",
    }
    async with app.run_test() as pilot:
        submit_screen = screens.FileDeleteConfirmScreen(
            agent_id="agent-1",
            preview=preview,
        )
        app.push_screen(submit_screen)
        await pilot.pause()

        await pilot.click("#file-delete-confirm")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert deleted == [("agent-1", preview)]

        cancel_screen = screens.FileDeleteConfirmScreen(
            agent_id="agent-1",
            preview=preview,
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#file-delete-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert deleted == [("agent-1", preview)]


async def test_extracted_file_trash_restore_and_close() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    restored: list[tuple[str, str]] = []

    async def restore(agent_id: str, trash_id: str) -> None:
        restored.append((agent_id, trash_id))

    app.restore_file_trash = restore  # type: ignore[method-assign]
    entries = [
        {
            "trash_id": "trash-1",
            "created_at": 1_700_000_000,
            "original_path": "src/old.py",
            "git_state": "untracked",
        }
    ]
    async with app.run_test() as pilot:
        restore_screen = screens.FileTrashScreen(
            agent_id="agent-1",
            entries=entries,
        )
        app.push_screen(restore_screen)
        await pilot.pause()

        await pilot.click("#file-trash-restore")
        await pilot.pause()

        assert app.screen is not restore_screen
        assert restored == [("agent-1", "trash-1")]

        close_screen = screens.FileTrashScreen(
            agent_id="agent-1",
            entries=entries,
        )
        app.push_screen(close_screen)
        await pilot.pause()

        await pilot.click("#file-trash-close")
        await pilot.pause()

        assert app.screen is not close_screen
        assert restored == [("agent-1", "trash-1")]


async def test_extracted_editor_close_discard_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    closed: list[tuple[str, bool, bool]] = []

    def close(
        agent_id: str,
        *,
        notify: bool = False,
        discarded: bool = False,
    ) -> bool:
        closed.append((agent_id, notify, discarded))
        return True

    app.close_editor_document = close  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        discard_screen = screens.EditorCloseConfirmScreen(
            agent_id="agent-1",
            path="src/app.py",
        )
        app.push_screen(discard_screen)
        await pilot.pause()

        await pilot.click("#editor-close-discard")
        await pilot.pause()

        assert app.screen is not discard_screen
        assert closed == [("agent-1", True, True)]

        cancel_screen = screens.EditorCloseConfirmScreen(
            agent_id="agent-1",
            path="src/app.py",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#editor-close-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert closed == [("agent-1", True, True)]


async def test_extracted_pull_request_merge_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    merged: list[tuple[str, int, str, str]] = []

    async def merge(
        agent_id: str,
        number: int,
        *,
        method: str,
        confirm: str,
    ) -> None:
        merged.append((agent_id, number, method, confirm))

    app.merge_pull_request = merge  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.PullRequestMergeConfirmScreen(
            agent_id="agent-1",
            number=7,
            title="Ship extraction",
            method="merge",
        )
        app.push_screen(submit_screen)
        await pilot.pause()
        submit_screen.query_one("#pr-merge-confirm", Input).value = "merge PR #7"

        await pilot.click("#pr-merge-run")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert merged == [("agent-1", 7, "merge", "merge PR #7")]

        cancel_screen = screens.PullRequestMergeConfirmScreen(
            agent_id="agent-1",
            number=8,
            title="Do not merge",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#pr-merge-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert merged == [("agent-1", 7, "merge", "merge PR #7")]


async def test_extracted_issue_clear_submit_and_cancel() -> None:
    app = tui.AgentPBXTUI(server="http://127.0.0.1:8765")
    cleared: list[tuple[str, int, str, str]] = []

    async def clear(
        agent_id: str,
        number: int,
        *,
        comment: str,
        confirm: str,
    ) -> None:
        cleared.append((agent_id, number, comment, confirm))

    app.clear_issue = clear  # type: ignore[method-assign]
    async with app.run_test() as pilot:
        submit_screen = screens.IssueClearConfirmScreen(
            agent_id="agent-1",
            number=9,
            title="Resolved issue",
        )
        app.push_screen(submit_screen)
        await pilot.pause()
        submit_screen.query_one("#issue-clear-comment", TextArea).load_text(
            "Resolved by the focused extraction."
        )
        submit_screen.query_one("#issue-clear-confirm", Input).value = "clear issue #9"

        await pilot.click("#issue-clear-run")
        await pilot.pause()

        assert app.screen is not submit_screen
        assert cleared == [
            (
                "agent-1",
                9,
                "Resolved by the focused extraction.",
                "clear issue #9",
            )
        ]

        cancel_screen = screens.IssueClearConfirmScreen(
            agent_id="agent-1",
            number=10,
            title="Keep open",
        )
        app.push_screen(cancel_screen)
        await pilot.pause()

        await pilot.click("#issue-clear-cancel")
        await pilot.pause()

        assert app.screen is not cancel_screen
        assert len(cleared) == 1
