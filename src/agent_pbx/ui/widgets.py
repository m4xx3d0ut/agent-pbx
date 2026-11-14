from __future__ import annotations

from typing import Any

from textual.events import Focus, Key
from textual.widgets import Markdown, TextArea


FOLLOW_UP_NEWLINE_KEYS = {
    "shift+enter",
    "shift+return",
    "alt+enter",
    "ctrl+enter",
    "ctrl+j",
    "newline",
}
FOLLOW_UP_EDIT_KEYS = {
    "ctrl+w": "delete_word_left",
}
SLASH_COMPLETION_FORWARD_KEYS = {"tab"}
SLASH_COMPLETION_BACKWARD_KEYS = {"shift+tab", "shift_tab", "backtab"}


def normalized_key_names(event: Key) -> set[str]:
    values = {event.key, getattr(event, "name", "")}
    values.update(getattr(event, "aliases", []) or [])
    return {value.strip().lower().replace("_", "+") for value in values if value}


def is_follow_up_newline_key(event: Key) -> bool:
    names = normalized_key_names(event)
    return bool(names & FOLLOW_UP_NEWLINE_KEYS) or any(
        ("shift" in name.split("+") or "alt" in name.split("+"))
        and ("enter" in name.split("+") or "return" in name.split("+"))
        for name in names
    )


def follow_up_edit_control(event: Key) -> str | None:
    for name in normalized_key_names(event):
        if edit := FOLLOW_UP_EDIT_KEYS.get(name):
            return edit
    return None


def slash_completion_direction(event: Key) -> int | None:
    names = normalized_key_names(event)
    if names & SLASH_COMPLETION_FORWARD_KEYS:
        return 1
    if names & SLASH_COMPLETION_BACKWARD_KEYS:
        return -1
    return None


class FollowUpTextArea(TextArea):
    async def _on_key(self, event: Key) -> None:
        completion_direction = slash_completion_direction(event)
        if completion_direction is not None:
            complete_file_async = getattr(self.app, "complete_file_reference_async", None)
            if complete_file_async is not None and await complete_file_async(
                self,
                direction=completion_direction,
            ):
                event.stop()
                event.prevent_default()
                return
            complete_file = getattr(self.app, "complete_file_reference", None)
            if complete_file is not None and complete_file(
                self,
                direction=completion_direction,
            ):
                event.stop()
                event.prevent_default()
                return
            complete = getattr(self.app, "complete_slash_command", None)
            if complete is not None and complete(self, direction=completion_direction):
                event.stop()
                event.prevent_default()
                return
        if event.key == "up":
            recall = getattr(self.app, "recall_sent_message", None)
            if recall is not None and recall(self, direction=-1):
                event.stop()
                event.prevent_default()
                return
        if event.key == "down":
            recall = getattr(self.app, "recall_sent_message", None)
            if recall is not None and recall(self, direction=1):
                event.stop()
                event.prevent_default()
                return
        if is_follow_up_newline_key(event):
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        edit = follow_up_edit_control(event)
        if edit is not None:
            event.stop()
            event.prevent_default()
            if edit == "delete_word_left":
                self.action_delete_word_left()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            send_input = getattr(self.app, "send_input", None)
            if send_input is not None:
                self.app.run_worker(
                    send_input(),
                    name="send-input",
                    exclusive=True,
                )
            return
        await super()._on_key(event)


class JoplinReferencePromptTextArea(FollowUpTextArea):
    """PBX-owned prompt input used when the native terminal owns raw ``@``."""

    def __init__(self, *, agent_id: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.agent_id = agent_id

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            submit = getattr(self.screen, "submit", None)
            if submit is not None:
                submit()
            return
        await super()._on_key(event)


class NavigationTextArea(TextArea):
    async def _on_key(self, event: Key) -> None:
        handle_joplin_shortcut = getattr(self.app, "handle_joplin_shortcut_key", None)
        if handle_joplin_shortcut is not None and handle_joplin_shortcut(
            event,
            focused=self,
        ):
            return
        if self.read_only:
            handle_shortcut = getattr(self.app, "handle_focus_shortcut_key", None)
            if handle_shortcut is not None and handle_shortcut(event):
                return
        await super()._on_key(event)


class ReadingMarkdown(Markdown):
    """Focusable Markdown reader with terminal-friendly keyboard scrolling."""

    can_focus = True

    async def _on_key(self, event: Key) -> None:
        handle_joplin_shortcut = getattr(self.app, "handle_joplin_shortcut_key", None)
        if handle_joplin_shortcut is not None and handle_joplin_shortcut(
            event,
            focused=self,
        ):
            return
        action = {
            "up": self.scroll_up,
            "down": self.scroll_down,
            "pageup": self.scroll_page_up,
            "pagedown": self.scroll_page_down,
            "home": self.scroll_home,
            "end": self.scroll_end,
        }.get(event.key)
        if action is None:
            await super()._on_key(event)
            return
        action(animate=False)
        event.stop()
        event.prevent_default()


class TmuxStreamTextArea(NavigationTextArea):
    def _on_focus(self, event: Focus) -> None:
        super()._on_focus(event)
        handle_focus = getattr(self.app, "handle_tmux_stream_focus", None)
        if handle_focus is not None:
            handle_focus(self)


__all__ = [
    "FOLLOW_UP_EDIT_KEYS",
    "FOLLOW_UP_NEWLINE_KEYS",
    "SLASH_COMPLETION_BACKWARD_KEYS",
    "SLASH_COMPLETION_FORWARD_KEYS",
    "FollowUpTextArea",
    "JoplinReferencePromptTextArea",
    "NavigationTextArea",
    "ReadingMarkdown",
    "TmuxStreamTextArea",
    "follow_up_edit_control",
    "is_follow_up_newline_key",
    "normalized_key_names",
    "slash_completion_direction",
]
