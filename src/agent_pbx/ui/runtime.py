from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from rich.text import Text

from ..contracts import CodexRuntimeState
from .theme import PBX_PALETTE, runtime_color


STATE_LABELS: dict[CodexRuntimeState, str] = {
    CodexRuntimeState.STARTING: "STARTING",
    CodexRuntimeState.READY: "READY",
    CodexRuntimeState.THINKING: "THINKING",
    CodexRuntimeState.DELEGATING: "DELEGATING",
    CodexRuntimeState.EXECUTING: "EXECUTING",
    CodexRuntimeState.WAITING_TOOL: "TOOL",
    CodexRuntimeState.WAITING_USER: "INPUT",
    CodexRuntimeState.COMPACTING: "COMPACTING",
    CodexRuntimeState.REVIEWING: "REVIEWING",
    CodexRuntimeState.COMPLETE: "COMPLETE",
    CodexRuntimeState.INTERRUPTED: "INTERRUPTED",
    CodexRuntimeState.ERROR: "ERROR",
    CodexRuntimeState.UNKNOWN: "UNKNOWN",
}

STATE_FRAMES: dict[CodexRuntimeState, tuple[str, ...]] = {
    CodexRuntimeState.STARTING: ("◐", "◓", "◑", "◒"),
    CodexRuntimeState.READY: ("◆",),
    CodexRuntimeState.THINKING: ("◐", "◓", "◑", "◒"),
    CodexRuntimeState.DELEGATING: ("◇⇢◆", "◇─◆", "◆⇠◇", "◆─◇"),
    CodexRuntimeState.EXECUTING: ("▸", "▹"),
    CodexRuntimeState.WAITING_TOOL: ("▸", "▹"),
    CodexRuntimeState.WAITING_USER: ("◈",),
    CodexRuntimeState.COMPACTING: ("◒", "◐"),
    CodexRuntimeState.REVIEWING: ("◎", "◉"),
    CodexRuntimeState.COMPLETE: ("✓",),
    CodexRuntimeState.INTERRUPTED: ("■",),
    CodexRuntimeState.ERROR: ("✕",),
    CodexRuntimeState.UNKNOWN: ("?",),
}


@dataclass(frozen=True)
class RuntimeHeader:
    text: Text
    state: CodexRuntimeState
    animated: bool


def runtime_state(snapshot: Mapping[str, Any] | None) -> CodexRuntimeState:
    raw = str((snapshot or {}).get("state") or "unknown")
    try:
        return CodexRuntimeState(raw)
    except ValueError:
        return CodexRuntimeState.UNKNOWN


def runtime_header(
    snapshot: Mapping[str, Any] | None,
    workerbee: Mapping[str, Any] | None = None,
    *,
    frame: int = 0,
    color_depth: int = 24,
) -> RuntimeHeader:
    snapshot = snapshot or {}
    state = runtime_state(snapshot)
    frames = STATE_FRAMES[state]
    symbol = frames[frame % len(frames)]
    text = Text()
    text.append(f"{symbol} {STATE_LABELS[state]}", style=f"bold {runtime_color(state, color_depth)}")
    parts: list[str] = []
    profile = snapshot.get("profile")
    if isinstance(profile, Mapping):
        model = short_model(str(profile.get("model") or ""))
        effort = str(profile.get("reasoning_effort") or "").upper()
        if model:
            parts.append(f"{model}{'/' + effort if effort else ''}")
    context = snapshot.get("context")
    if isinstance(context, Mapping):
        remaining = context.get("remaining_percent")
        if isinstance(remaining, (int, float)):
            parts.append(f"CTX {remaining:g}%")
    branch = single_line(snapshot.get("branch"), 48)
    if branch:
        parts.append(f"BR {branch}")
    parts.append(workerbee_label(workerbee))
    evidence = snapshot.get("evidence")
    if isinstance(evidence, Mapping):
        source = single_line(evidence.get("source"), 32)
        if source:
            parts.append(source.replace("_", " "))
    for part in parts:
        text.append(" │ ", style=PBX_PALETTE["muted"])
        text.append(part, style=PBX_PALETTE["foreground"])
    return RuntimeHeader(text, state, len(frames) > 1)


def runtime_topology(
    snapshot: Mapping[str, Any] | None,
    *,
    frame: int = 0,
    color_depth: int = 24,
    limit: int = 6,
) -> Text:
    snapshot = snapshot or {}
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, Mapping) or capabilities.get("native_subagents") != "observed":
        return Text()
    raw_children = snapshot.get("children")
    if not isinstance(raw_children, list) or not raw_children:
        return Text()
    children = [item for item in raw_children if isinstance(item, Mapping)][: max(1, limit)]
    text = Text("SUBAGENTS", style=f"bold {PBX_PALETTE['muted']}")
    for index, child in enumerate(children):
        state = runtime_state(child)
        frames = STATE_FRAMES[state]
        symbol = frames[frame % len(frames)]
        connector = "└─" if index == len(children) - 1 else "├─"
        label = single_line(child.get("role"), 28) or short_session(
            single_line(child.get("child_id"), 240) or "child"
        )
        model = short_model(single_line(child.get("model"), 160) or "")
        effort = single_line(child.get("reasoning_effort"), 40)
        suffix = f" · {model}{'/' + effort.upper() if effort else ''}" if model else ""
        text.append(f"\n{connector} ", style=PBX_PALETTE["muted"])
        text.append(symbol, style=f"bold {runtime_color(state, color_depth)}")
        text.append(f" {label}  {STATE_LABELS[state]}{suffix}", style=PBX_PALETTE["foreground"])
    extra = len(raw_children) - len(children)
    if extra > 0:
        text.append(f"\n└─ +{extra} more", style=PBX_PALETTE["muted"])
    return text


def workerbee_label(status: Mapping[str, Any] | None) -> str:
    if not status:
        return "WB ?"
    if status.get("error") or not status.get("available"):
        return "WB ▲"
    if status.get("running") is True:
        return "WB ●"
    return "WB ◇"


def short_model(model: str) -> str:
    normalized = model.strip()
    if normalized.startswith("gpt-"):
        normalized = normalized[4:]
    return normalized.upper()


def short_session(session_id: str) -> str:
    value = session_id.strip()
    return value if len(value) <= 12 else f"{value[:8]}…{value[-3:]}"


def single_line(value: object, limit: int) -> str:
    if value is None:
        return ""
    return str(value).replace("\n", " ").replace("\r", " ").strip()[:limit]
