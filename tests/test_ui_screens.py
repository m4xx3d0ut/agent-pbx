from agent_pbx import tui
from agent_pbx.ui import screens


def test_command_joplin_codex_screen_aliases_are_object_identical() -> None:
    names = (
        "GitDiffScreen",
        "GitPushScreen",
        "CustomSlashCommandArgScreen",
        "JoplinReferencePromptScreen",
        "JoplinNoteTitleScreen",
        "JoplinDeleteConfirmScreen",
        "JoplinPasteConfirmScreen",
        "ModelElevationScreen",
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
