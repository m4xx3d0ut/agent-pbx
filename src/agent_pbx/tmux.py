from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Mapping, Sequence


TMUX_PANE_FORMAT = "\t".join(
    [
        "#{session_name}",
        "#{window_index}",
        "#{pane_index}",
        "#{pane_id}",
        "#{pane_active}",
        "#{pane_current_command}",
        "#{pane_title}",
        "#{pane_current_path}",
        "#{pane_width}",
        "#{pane_height}",
        "#{history_size}",
    ]
)


@dataclass(frozen=True)
class TmuxPane:
    session_name: str
    window_index: str
    pane_index: str
    pane_id: str
    active: bool
    current_command: str
    title: str
    cwd: str
    width: int
    height: int
    history_size: int

    @property
    def target_label(self) -> str:
        return f"{self.session_name}:{self.window_index}.{self.pane_index}"


def int_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def parse_pane_line(line: str) -> TmuxPane | None:
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 11:
        return None
    return TmuxPane(
        session_name=parts[0],
        window_index=parts[1],
        pane_index=parts[2],
        pane_id=parts[3],
        active=parts[4] == "1",
        current_command=parts[5],
        title=parts[6],
        cwd=parts[7],
        width=int_or_zero(parts[8]),
        height=int_or_zero(parts[9]),
        history_size=int_or_zero(parts[10]),
    )


def list_panes(tmux_bin: str = "tmux") -> list[TmuxPane]:
    result = subprocess.run(
        [tmux_bin, "list-panes", "-a", "-F", TMUX_PANE_FORMAT],
        capture_output=True,
        check=True,
        text=True,
    )
    panes: list[TmuxPane] = []
    for line in result.stdout.splitlines():
        pane = parse_pane_line(line)
        if pane is not None:
            panes.append(pane)
    return panes


def capture_pane(target: str, *, lines: int = 500, tmux_bin: str = "tmux") -> str:
    safe_lines = max(1, int(lines))
    result = subprocess.run(
        [
            tmux_bin,
            "capture-pane",
            "-p",
            "-J",
            "-S",
            f"-{safe_lines}",
            "-t",
            target,
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def send_text(target: str, text: str, *, tmux_bin: str = "tmux") -> None:
    buffer_name = f"agent-pbx-{os.getpid()}"
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            prefix="agent-pbx-tmux-",
        ) as handle:
            handle.write(text)
            path = handle.name
        subprocess.run(
            [tmux_bin, "load-buffer", "-b", buffer_name, path],
            check=True,
            text=True,
        )
        subprocess.run(
            [tmux_bin, "paste-buffer", "-d", "-b", buffer_name, "-t", target],
            check=True,
            text=True,
        )
        subprocess.run(
            [tmux_bin, "send-keys", "-t", target, "Enter"],
            check=True,
            text=True,
        )
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)


def score_pane_for_agent(pane: TmuxPane, agent: Mapping[str, Any]) -> int:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    agent_cwd = str(metadata.get("cwd") or "")
    project = str(agent.get("project") or "")
    agent_id = str(agent.get("agent_id") or "")
    name = str(agent.get("name") or "")
    pane_text = f"{pane.title} {pane.cwd} {pane.current_command}".lower()
    score = 0

    if agent_cwd:
        if pane.cwd == agent_cwd:
            score += 80
        else:
            cwd_name = Path(agent_cwd).name.lower()
            if cwd_name and cwd_name in pane.cwd.lower():
                score += 20

    command = pane.current_command.lower()
    if command in {"codex", "node"} or "codex" in command:
        score += 30

    for value, points in (
        (project, 20),
        (Path(agent_cwd).name if agent_cwd else "", 15),
        (agent_id, 10),
        (name, 10),
    ):
        token = value.strip().lower()
        if token and token in pane_text:
            score += points

    return score


def choose_pane_for_agent(
    panes: Sequence[TmuxPane],
    agent: Mapping[str, Any],
    *,
    min_score: int = 50,
) -> TmuxPane | None:
    scored = sorted(
        ((score_pane_for_agent(pane, agent), pane) for pane in panes),
        key=lambda item: (-item[0], item[1].target_label, item[1].pane_id),
    )
    if not scored or scored[0][0] < min_score:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


def ranked_panes_for_agent(
    panes: Sequence[TmuxPane],
    agent: Mapping[str, Any],
) -> list[TmuxPane]:
    return [
        pane
        for _, pane in sorted(
            ((score_pane_for_agent(pane, agent), pane) for pane in panes),
            key=lambda item: (-item[0], item[1].target_label, item[1].pane_id),
        )
    ]
