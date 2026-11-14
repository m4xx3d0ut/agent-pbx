from textual.events import Key

from agent_pbx import tui
from agent_pbx.ui import widgets


def test_tui_widget_compatibility_aliases_are_object_identical() -> None:
    names = (
        "FollowUpTextArea",
        "JoplinReferencePromptTextArea",
        "NavigationTextArea",
        "ReadingMarkdown",
        "TmuxStreamTextArea",
        "normalized_key_names",
        "is_follow_up_newline_key",
        "follow_up_edit_control",
        "slash_completion_direction",
    )

    for name in names:
        assert getattr(tui, name) is getattr(widgets, name)


def test_extracted_widget_key_helpers_preserve_terminal_alias_semantics() -> None:
    assert widgets.normalized_key_names(Key("shift_enter", None)) == {
        "shift+enter"
    }
    assert widgets.is_follow_up_newline_key(Key("shift_enter", None)) is True
    assert widgets.is_follow_up_newline_key(Key("ctrl+j", None)) is True
    assert widgets.is_follow_up_newline_key(Key("enter", None)) is False
    assert widgets.follow_up_edit_control(Key("ctrl+w", None)) == "delete_word_left"
    assert widgets.follow_up_edit_control(Key("ctrl+u", None)) is None
    assert widgets.slash_completion_direction(Key("tab", "\t")) == 1
    assert widgets.slash_completion_direction(Key("shift_tab", None)) == -1
    assert widgets.slash_completion_direction(Key("enter", None)) is None
