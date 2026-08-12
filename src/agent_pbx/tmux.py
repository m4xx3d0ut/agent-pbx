from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
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
        "#{window_name}",
    ]
)
DEFAULT_SUBMIT_DELAY_SECONDS = 0.08
DEFAULT_QUIT_WAIT_SECONDS = 5.0
PANE_EXIT_POLL_SECONDS = 0.1
FALSE_ENV_VALUES = {"0", "false", "no", "off", "n", "disabled", ""}
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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
    window_name: str = ""

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
    if len(parts) not in {11, 12}:
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
        window_name=parts[11] if len(parts) == 12 else "",
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


def session_exists(session_name: str, *, tmux_bin: str = "tmux") -> bool:
    result = subprocess.run(
        [tmux_bin, "has-session", "-t", session_name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def launch_pane(
    *,
    session_name: str,
    window_name: str,
    command: str,
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    tmux_bin: str = "tmux",
) -> str:
    args: list[str]
    if session_exists(session_name, tmux_bin=tmux_bin):
        args = [
            tmux_bin,
            "new-window",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            session_name,
            "-n",
            window_name,
        ]
    else:
        args = [
            tmux_bin,
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            session_name,
            "-n",
            window_name,
        ]
    if cwd:
        args.extend(["-c", cwd])
    if env:
        for key, value in env.items():
            if not ENV_KEY_PATTERN.match(key):
                raise ValueError(f"invalid tmux environment key: {key!r}")
            args.extend(["-e", f"{key}={value}"])
    args.append(command)
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux launch failed").strip()
        raise RuntimeError(message)
    pane_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    if not pane_id:
        raise RuntimeError("tmux did not return a launched pane id")
    return pane_id


def kill_pane(target: str, *, tmux_bin: str = "tmux") -> None:
    result = subprocess.run(
        [tmux_bin, "kill-pane", "-t", target],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "tmux kill-pane failed").strip()
        raise RuntimeError(message)


def pane_exists(target: str, *, tmux_bin: str = "tmux") -> bool:
    result = subprocess.run(
        [tmux_bin, "display-message", "-p", "-t", target, "#{pane_id}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def pane_start_command(target: str, *, tmux_bin: str = "tmux") -> str:
    result = subprocess.run(
        [tmux_bin, "display-message", "-p", "-t", target, "#{pane_start_command}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (
            result.stderr or result.stdout or "tmux pane_start_command lookup failed"
        ).strip()
        raise RuntimeError(message)
    return result.stdout.strip()


def wait_for_pane_exit(
    target: str,
    *,
    timeout_seconds: float = DEFAULT_QUIT_WAIT_SECONDS,
    interval_seconds: float = PANE_EXIT_POLL_SECONDS,
    tmux_bin: str = "tmux",
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if not pane_exists(target, tmux_bin=tmux_bin):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.01, interval_seconds))


def quit_pane(
    target: str,
    *,
    tmux_bin: str = "tmux",
    timeout_seconds: float = DEFAULT_QUIT_WAIT_SECONDS,
) -> bool:
    send_literal_keys(target, "/q", tmux_bin=tmux_bin)
    return wait_for_pane_exit(
        target,
        timeout_seconds=timeout_seconds,
        tmux_bin=tmux_bin,
    )


def capture_start_arg(lines: int) -> str:
    return "0" if lines <= 0 else f"-{lines}"


def capture_pane(
    target: str,
    *,
    lines: int = 0,
    tmux_bin: str = "tmux",
    join_wrapped: bool = True,
    alternate_screen: bool = False,
    copy_mode: bool = False,
    preserve_trailing_spaces: bool = False,
) -> str:
    args = [tmux_bin, "capture-pane", "-p"]
    if join_wrapped:
        args.append("-J")
    if alternate_screen:
        args.append("-a")
    if copy_mode:
        args.append("-M")
    if preserve_trailing_spaces:
        args.append("-N")
    args.extend(["-S", capture_start_arg(int(lines)), "-t", target])
    result = subprocess.run(
        args,
        capture_output=True,
        check=True,
        text=True,
    )
    return result.stdout.rstrip("\n")


def bracketed_paste_enabled() -> bool:
    value = os.getenv("AGENT_PBX_TUI_TMUX_BRACKETED_PASTE")
    if value is None:
        return True
    return value.strip().lower() not in FALSE_ENV_VALUES


def send_text(
    target: str,
    text: str,
    *,
    tmux_bin: str = "tmux",
    submit_delay_seconds: float = DEFAULT_SUBMIT_DELAY_SECONDS,
    bracketed_paste: bool | None = None,
    submit: bool = True,
) -> None:
    buffer_name = f"agent-pbx-{os.getpid()}"
    subprocess.run(
        [tmux_bin, "load-buffer", "-b", buffer_name, "-"],
        input=text,
        check=True,
        text=True,
    )
    paste_cmd = [tmux_bin, "paste-buffer"]
    use_bracketed_paste = (
        bracketed_paste if bracketed_paste is not None else bracketed_paste_enabled()
    )
    if use_bracketed_paste:
        paste_cmd.append("-p")
    paste_cmd.extend(["-d", "-b", buffer_name, "-t", target])
    subprocess.run(
        paste_cmd,
        check=True,
        text=True,
    )
    if submit:
        if submit_delay_seconds > 0:
            time.sleep(submit_delay_seconds)
        subprocess.run(
            [tmux_bin, "send-keys", "-t", target, "C-m"],
            check=True,
            text=True,
        )


def send_literal_keys(
    target: str,
    text: str,
    *,
    tmux_bin: str = "tmux",
    submit_delay_seconds: float = DEFAULT_SUBMIT_DELAY_SECONDS,
    submit: bool = True,
) -> None:
    subprocess.run(
        [tmux_bin, "send-keys", "-t", target, "-l", text],
        check=True,
        text=True,
    )
    if submit:
        if submit_delay_seconds > 0:
            time.sleep(submit_delay_seconds)
        subprocess.run(
            [tmux_bin, "send-keys", "-t", target, "C-m"],
            check=True,
            text=True,
        )


def send_key(target: str, key: str, *, tmux_bin: str = "tmux") -> None:
    subprocess.run(
        [tmux_bin, "send-keys", "-t", target, key],
        check=True,
        text=True,
    )


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


def pane_matches_agent(pane: TmuxPane, agent: Mapping[str, Any]) -> bool:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    agent_cwd = str(metadata.get("cwd") or "")
    project = str(agent.get("project") or "").strip().lower()
    pane_text = f"{pane.title} {pane.cwd}".lower()
    if not agent_cwd and not project:
        return True
    if agent_cwd and pane.cwd == agent_cwd:
        return True
    if project and project in pane_text:
        return True
    return False


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
