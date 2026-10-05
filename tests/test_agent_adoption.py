from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

from agent_pbx.agent_adoption import (
    AgentPaneAdoptionService,
    codex_session_ids_for_process_tree,
    current_codex_session_ids_for_process_tree,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def tmux(socket_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-S", str(socket_path), *args],
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is unavailable")
def test_agent_pane_adoption_moves_and_restores_live_pane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "tmux.sock"
    source = tmux(
        socket_path,
        "new-session",
        "-d",
        "-s",
        "workspace",
        "-n",
        "mixed",
        "sleep 300",
    )
    assert source.returncode == 0, source.stderr
    assert tmux(socket_path, "split-window", "-d", "-t", "workspace:mixed", "sleep 300").returncode == 0
    panes = tmux(
        socket_path,
        "list-panes",
        "-t",
        "workspace:mixed",
        "-F",
        "#{pane_id}\t#{pane_pid}\t#{window_layout}",
    ).stdout.splitlines()
    anchor_id = panes[0].split("\t")[0]
    pane_id, pane_pid, source_layout = panes[1].split("\t")
    monkeypatch.setenv("TMUX", f"{socket_path},123,0")

    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-a",
            project="demo",
            metadata={
                "cwd": str(tmp_path),
                "codex_session_id": "019e373c-ae09-7530-8250-c7d8f4db439a",
                "tmux_pane_id": pane_id,
            },
        )
    )
    service = AgentPaneAdoptionService(store)
    try:
        preview = service.preview(
            agent_id="agent-a",
            pane_id=pane_id,
            origin_session_name="agent-pbx",
            origin_client_tty="/dev/pts/9",
        )
        assert preview["plan"]["eligible"] is True
        assert preview["plan"]["source"]["window_layout"] == source_layout
        applied = service.apply(preview["batch_id"])
        result = applied["result"]
        assert result["pane_id"] == pane_id
        assert result["pane_pid"] == int(pane_pid)
        assert result["session_name"] == "agent-pbx-agents"
        assert tmux(
            socket_path,
            "display-message",
            "-p",
            "-t",
            pane_id,
            "#{session_name}:#{window_name}:#{pane_pid}",
        ).stdout.strip() == f"agent-pbx-agents:agent-a:{pane_pid}"
        mapping = store.get_tmux_runtime_mapping("agent-a")
        assert mapping is not None
        assert mapping["metadata"]["popped_in"] is True

        restored = service.rollback(preview["batch_id"])
        assert restored["result"]["status"] == "restored"
        assert tmux(
            socket_path,
            "display-message",
            "-p",
            "-t",
            pane_id,
            "#{session_name}:#{window_id}:#{pane_pid}:#{window_layout}",
        ).stdout.strip() == f"workspace:@0:{pane_pid}:{source_layout}"
        assert tmux(
            socket_path,
            "display-message",
            "-p",
            "-t",
            anchor_id,
            "#{pane_id}",
        ).stdout.strip() == anchor_id
        assert store.get_tmux_runtime_mapping("agent-a") is None
    finally:
        tmux(socket_path, "kill-server")


def test_agent_pane_adoption_blocks_duplicate_pbx_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "tmux.sock"
    server = subprocess.Popen(
        ["tmux", "-S", str(socket_path), "new-session", "-d", "-s", "workspace", "sleep 300"]
    )
    assert server.wait(timeout=5) == 0
    monkeypatch.setenv("TMUX", f"{socket_path},123,0")
    pane_id = tmux(socket_path, "list-panes", "-t", "workspace", "-F", "#{pane_id}").stdout.strip()
    session_id = "019e373c-ae09-7530-8250-c7d8f4db439a"
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id in ("agent-a", "agent-alias"):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="demo",
                metadata={"codex_session_id": session_id, "tmux_pane_id": pane_id},
            )
        )
    try:
        preview = AgentPaneAdoptionService(store).preview(
            agent_id="agent-a",
            pane_id=pane_id,
        )
        assert preview["plan"]["eligible"] is False
        assert "agent-alias" in " ".join(preview["plan"]["blockers"])
    finally:
        tmux(socket_path, "kill-server")


def test_codex_session_ids_use_open_rollout_files(tmp_path: Path) -> None:
    proc = tmp_path / "42"
    (proc / "task" / "42").mkdir(parents=True)
    (proc / "task" / "42" / "children").write_text("")
    (proc / "cmdline").write_bytes(b"codex\0resume\0")
    (proc / "fd").mkdir()
    session_id = "019e373c-ae09-7530-8250-c7d8f4db439a"
    rollout = (
        tmp_path
        / ".codex/sessions/2026/05/17"
        / f"rollout-2026-05-17T18-39-44-{session_id}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text("")
    os.symlink(rollout, proc / "fd" / "7")

    assert codex_session_ids_for_process_tree(42, proc_root=tmp_path) == (session_id,)


def test_current_codex_session_prefers_writable_rollout_over_fork_source_argv(
    tmp_path: Path,
) -> None:
    proc = tmp_path / "42"
    (proc / "task" / "42").mkdir(parents=True)
    (proc / "task" / "42" / "children").write_text("")
    source_id = "019e373c-ae09-7530-8250-c7d8f4db439a"
    current_id = "019e373c-ae09-7530-8250-c7d8f4db439b"
    (proc / "cmdline").write_bytes(f"codex\0fork\0{source_id}\0".encode())
    (proc / "fd").mkdir()
    (proc / "fdinfo").mkdir()
    rollout = (
        tmp_path
        / ".codex/sessions/2026/05/17"
        / f"rollout-2026-05-17T18-39-44-{current_id}.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text("")
    os.symlink(rollout, proc / "fd" / "7")
    (proc / "fdinfo" / "7").write_text("flags:\t0100001\n")

    assert codex_session_ids_for_process_tree(42, proc_root=tmp_path) == (
        source_id,
        current_id,
    )
    assert current_codex_session_ids_for_process_tree(42, proc_root=tmp_path) == (
        current_id,
    )
