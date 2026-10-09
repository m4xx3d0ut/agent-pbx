from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.codex.runtime import CodexRuntimeService
from agent_pbx.config import ServerConfig
from agent_pbx.contracts import CodexRuntimeState
from agent_pbx.ui.runtime import runtime_header, runtime_topology
from agent_pbx.ui.theme import (
    CYBERPUNK_PALETTE,
    PBX_PALETTE,
    codex_syntax_theme_xml,
    runtime_color,
    terminal_color_depth,
    textual_palette,
    tmux_theme_options,
)
from agent_pbx.tui import AgentPBXTUI


HEADERS = {"Authorization": "Bearer secret"}


def runtime_client(tmp_path: Path) -> tuple[TestClient, object]:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    response = client.post(
        "/v1/agents/register",
        headers=HEADERS,
        json={
            "agent_id": "agent-a",
            "project": "demo",
            "metadata": {
                "cwd": str(tmp_path),
                "codex_session_id": "thread-root",
                "codex_model": "gpt-5.6-sol",
                "codex_model_reasoning_effort": "xhigh",
            },
        },
    )
    assert response.status_code == 200
    return client, app


def test_runtime_endpoint_rejects_unrelated_thread(tmp_path: Path) -> None:
    client, _app = runtime_client(tmp_path)
    response = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-other",
            "method": "turn/started",
            "params": {"threadId": "thread-other"},
        },
    )
    assert response.status_code == 409
    assert "does not match" in response.json()["detail"]


def test_runtime_projection_observes_children_without_prompt_or_reasoning(
    tmp_path: Path,
) -> None:
    client, app = runtime_client(tmp_path)
    response = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/started",
            "params": {
                "threadId": "thread-root",
                "item": {
                    "id": "call-1",
                    "type": "collabAgentToolCall",
                    "tool": "spawnAgent",
                    "status": "inProgress",
                    "senderThreadId": "thread-root",
                    "receiverThreadIds": ["thread-child"],
                    "model": "gpt-5.6-sol",
                    "reasoningEffort": "high",
                    "prompt": "private child task must never persist",
                    "reasoning": "private reasoning must never persist",
                    "agentsStates": {
                        "thread-child": {
                            "status": "running",
                            "message": "private child status message",
                        }
                    },
                },
            },
        },
    )
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["state"] == "delegating"
    assert snapshot["capabilities"]["native_subagents"] == "observed"
    assert snapshot["children"] == [
        {
            "child_id": "thread-child",
            "parent_id": "agent-a",
            "state": "thinking",
            "role": None,
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
            "started_at": snapshot["children"][0]["started_at"],
            "finished_at": None,
        }
    ]
    serialized = json.dumps(
        app.state.store.list_subject_events(
            "agent-a", event_type="codex_runtime_observed"
        )
    )
    assert "private child task" not in serialized
    assert "private reasoning" not in serialized
    assert "private child status" not in serialized
    assert "prompt" not in serialized


def test_runtime_context_and_persisted_projection_restore(tmp_path: Path) -> None:
    client, app = runtime_client(tmp_path)
    response = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/tokenUsage/updated",
            "params": {
                "threadId": "thread-root",
                "turnId": "turn-1",
                "tokenUsage": {
                    "last": {"totalTokens": 25_000},
                    "total": {"totalTokens": 30_000},
                    "modelContextWindow": 100_000,
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["context"] == {
        "used_tokens": 25000,
        "capacity_tokens": 100000,
        "remaining_percent": 75.0,
    }
    restored = CodexRuntimeService(app.state.store).snapshot("agent-a")
    assert restored["context"]["remaining_percent"] == 75.0
    assert restored["session_id"] == "thread-root"


def test_runtime_presentation_requires_observed_child_capability() -> None:
    snapshot = {
        "state": "thinking",
        "profile": {"model": "gpt-5.6-sol", "reasoning_effort": "xhigh"},
        "context": {"remaining_percent": 61},
        "branch": "feat/runtime",
        "evidence": {"source": "app_server"},
        "capabilities": {"native_subagents": "advertised"},
        "children": [{"child_id": "child-1", "state": "thinking"}],
    }
    header = runtime_header(snapshot, {"available": True, "running": True}, frame=2)
    assert "THINKING" in header.text.plain
    assert "5.6-SOL/XHIGH" in header.text.plain
    assert "CTX 61%" in header.text.plain
    assert "BR feat/runtime" in header.text.plain
    assert "WB ●" in header.text.plain
    assert runtime_topology(snapshot).plain == ""

    snapshot["capabilities"]["native_subagents"] = "observed"
    topology = runtime_topology(snapshot)
    assert "SUBAGENTS" in topology.plain
    assert "child-1" in topology.plain
    assert "THINKING" in topology.plain
    assert "reasoning" not in topology.plain.lower()


def test_accessible_theme_has_terminal_fallbacks_and_shared_generators() -> None:
    for state in CodexRuntimeState:
        assert runtime_color(state, 4)
        assert runtime_color(state, 8).startswith("color(")
        assert runtime_color(state, 24).startswith("#")
    assert terminal_color_depth({"TERM": "xterm"}) == 4
    assert terminal_color_depth({"TERM": "xterm-256color"}) == 8
    assert terminal_color_depth({"COLORTERM": "truecolor"}) == 24
    tmux = "\n".join(tmux_theme_options())
    codex = codex_syntax_theme_xml()
    assert PBX_PALETTE["primary"] in tmux
    assert PBX_PALETTE["primary"] in codex
    assert "Agent PBX 1337 Accessible" in codex


def test_legacy_cyberpunk_theme_keeps_its_public_rgb_contract() -> None:
    assert textual_palette() == {
        "primary": "#00e5ff",
        "secondary": "#9b5cff",
        "warning": "#fcee09",
        "error": "#ff2e88",
        "success": "#38ff9c",
        "accent": "#ff3df2",
        "foreground": "#f2f7ff",
        "background": "#070b16",
        "surface": "#101826",
        "panel": "#1a102a",
        "boost": "#2b174b",
    }
    assert CYBERPUNK_PALETTE["muted"] == "#697386"
    assert textual_palette(PBX_PALETTE)["primary"] == "#5ee7ff"


def test_accessible_cyberpunk_theme_has_distinct_high_contrast_surfaces() -> None:
    accessible = textual_palette(PBX_PALETTE)
    legacy = textual_palette(CYBERPUNK_PALETTE)

    for key in ("background", "surface", "panel", "boost"):
        assert accessible[key] != legacy[key]

    def relative_luminance(color: str) -> float:
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            channel / 12.92
            if channel <= 0.04045
            else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        ]
        return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2])

    def contrast_ratio(first: str, second: str) -> float:
        lighter, darker = sorted(
            (relative_luminance(first), relative_luminance(second)),
            reverse=True,
        )
        return (lighter + 0.05) / (darker + 0.05)

    for key in ("background", "surface", "panel", "boost"):
        assert contrast_ratio(accessible["foreground"], accessible[key]) >= 7.0


async def test_tui_runtime_surface_is_outer_and_capability_gated() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    async with app.run_test() as pilot:
        await pilot.pause()
        app.selected_agent_id = "agent-a"
        app.codex_runtime_by_agent["agent-a"] = {
            "state": "delegating",
            "profile": {
                "model": "gpt-5.6-sol",
                "reasoning_effort": "xhigh",
            },
            "context": {"remaining_percent": 52},
            "branch": "feat/runtime-ux",
            "evidence": {"source": "app_server"},
            "capabilities": {"native_subagents": "observed"},
            "children": [
                {
                    "child_id": "child-1234567890",
                    "state": "executing",
                    "model": "gpt-5.6-sol",
                    "reasoning_effort": "high",
                }
            ],
        }
        app.workerbee_status_by_agent["agent-a"] = {
            "available": True,
            "running": True,
        }
        app.render_codex_runtime_surface()
        await pilot.pause()

        status = app.query_one("#codex-runtime-status")
        topology = app.query_one("#codex-runtime-topology")
        assert "DELEGATING" in str(status.renderable)
        assert "WB ●" in str(status.renderable)
        assert topology.has_class("has-subagents")
        assert "child-12" in str(topology.renderable)
        assert "EXECUTING" in str(topology.renderable)
