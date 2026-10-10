from agent_pbx.ui.palette_history import (
    PaletteCommandHistory,
    safe_palette_command_name,
)


def test_palette_history_is_bounded_and_deduplicated() -> None:
    history = PaletteCommandHistory(limit=3)

    assert history.record("/refresh") is True
    assert history.record("/detail") is True
    assert history.record("/REFRESH") is True
    assert history.record("/latest") is True
    assert history.record("/thread") is True

    assert history.entries == ("/REFRESH", "/latest", "/thread")


def test_palette_history_rejects_prompts_controls_and_sensitive_names() -> None:
    rejected = (
        "write a deployment plan",
        "/send\nprivate body",
        "/configure api-token",
        "/save password",
        "/palette history",
        "/palette history persistence on",
    )

    assert all(safe_palette_command_name(value) is None for value in rejected)

    history = PaletteCommandHistory(rejected)
    assert history.entries == ()


def test_palette_history_navigation_preserves_draft_and_entries() -> None:
    history = PaletteCommandHistory(("/refresh", "/latest", "/thread"))
    navigator = history.navigator()

    assert navigator.previous("/draft") == "/thread"
    assert navigator.previous("edited recalled command") == "/latest"
    assert navigator.previous("ignored") == "/refresh"
    assert navigator.previous("ignored") == "/refresh"
    assert navigator.next() == "/latest"
    assert navigator.next() == "/thread"
    assert navigator.next() == "/draft"
    assert navigator.active is False
    assert history.entries == ("/refresh", "/latest", "/thread")
