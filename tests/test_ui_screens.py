from textual.widgets import Input, TextArea

from agent_pbx import tui
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
        "JoplinNoteTitleScreen",
        "JoplinDeleteConfirmScreen",
        "JoplinPasteConfirmScreen",
        "ModelElevationScreen",
        "PullRequestMergeConfirmScreen",
        "slugify",
    )

    for name in names:
        assert getattr(tui, name) is getattr(screens, name)


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
