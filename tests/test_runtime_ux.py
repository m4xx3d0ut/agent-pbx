from __future__ import annotations

import json
from pathlib import Path
import stat
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.codex import runtime as codex_runtime
from agent_pbx.codex.runtime import CodexRuntimeService
from agent_pbx.codex_cli import CodexModelOption
from agent_pbx.config import ServerConfig
from agent_pbx.contracts import CodexRuntimeState
from agent_pbx.ui.runtime import (
    TuiHostTerminalIdentity,
    assess_tui_host_terminal_identity,
    capture_interactive_tui_host_identity,
    finalize_tui_host_watchdog,
    runtime_header,
    runtime_topology,
    watch_tui_host_liveness,
)
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


class FakeTerminalStream:
    def __init__(self, *, interactive: bool, fd: int = 7) -> None:
        self.interactive = interactive
        self.fd = fd

    def isatty(self) -> bool:
        return self.interactive

    def fileno(self) -> int:
        return self.fd


def terminal_stat(*, inode: int = 22) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=stat.S_IFCHR | 0o600,
        st_dev=11,
        st_ino=inode,
        st_rdev=33,
    )


def test_host_terminal_identity_is_disabled_for_noninteractive_launch() -> None:
    assert (
        capture_interactive_tui_host_identity(
            stdin=FakeTerminalStream(interactive=False),
            stdout=FakeTerminalStream(interactive=True),
            process_start_reader=lambda _pid: pytest.fail(
                "noninteractive launch must not inspect its parent"
            ),
        )
        is None
    )


def test_host_terminal_identity_proves_exact_parent_and_terminal() -> None:
    identity = capture_interactive_tui_host_identity(
        stdin=FakeTerminalStream(interactive=True),
        stdout=FakeTerminalStream(interactive=True),
        getppid=lambda: 42,
        fstat=lambda _fd: terminal_stat(),  # type: ignore[arg-type]
        ttyname=lambda _fd: "/dev/pts/7",
        tcgetpgrp=lambda _fd: 99,
        process_start_reader=lambda _pid: 1234,
    )
    assert identity is not None
    assert identity.parent_pid == 42
    assert identity.parent_start_ticks == 1234

    assessment = assess_tui_host_terminal_identity(
        identity,
        getppid=lambda: 42,
        fstat=lambda _fd: terminal_stat(),  # type: ignore[arg-type]
        ttyname=lambda _fd: "/dev/pts/7",
        tcgetpgrp=lambda _fd: 99,
        process_start_reader=lambda _pid: 1234,
    )
    assert assessment.live is True
    assert assessment.reason_code == "live"


@pytest.mark.parametrize(
    ("parent_pid", "parent_ticks", "inode", "reason"),
    (
        (43, 1234, 22, "parent_changed"),
        (42, 1235, 22, "parent_identity_changed"),
        (42, 1234, 23, "host_terminal_changed"),
    ),
)
def test_host_terminal_identity_fails_closed_on_drift(
    parent_pid: int,
    parent_ticks: int,
    inode: int,
    reason: str,
) -> None:
    identity = capture_interactive_tui_host_identity(
        stdin=FakeTerminalStream(interactive=True),
        stdout=FakeTerminalStream(interactive=True),
        getppid=lambda: 42,
        fstat=lambda _fd: terminal_stat(),  # type: ignore[arg-type]
        ttyname=lambda _fd: "/dev/pts/7",
        tcgetpgrp=lambda _fd: 99,
        process_start_reader=lambda _pid: 1234,
    )
    assert identity is not None

    assessment = assess_tui_host_terminal_identity(
        identity,
        getppid=lambda: parent_pid,
        fstat=lambda _fd: terminal_stat(inode=inode),  # type: ignore[arg-type]
        ttyname=lambda _fd: "/dev/pts/7",
        tcgetpgrp=lambda _fd: 99,
        process_start_reader=lambda _pid: parent_ticks,
    )
    assert assessment.live is False
    assert assessment.reason_code == reason


def test_host_watchdog_releases_exact_writer_and_terminates_orphan() -> None:
    identity = capture_interactive_tui_host_identity(
        stdin=FakeTerminalStream(interactive=True),
        stdout=FakeTerminalStream(interactive=True),
        getppid=lambda: 42,
        fstat=lambda _fd: terminal_stat(),  # type: ignore[arg-type]
        ttyname=lambda _fd: "/dev/pts/7",
        tcgetpgrp=lambda _fd: 99,
        process_start_reader=lambda _pid: 1234,
    )
    assert identity is not None
    app = SimpleNamespace(
        host_liveness_lost=False,
        embedded_terminal_agent_id="agent/a",
        tmux_terminal_client_id="tui-exact",
        server="http://127.0.0.1:8765",
        token="secret",
    )
    posts: list[tuple[str, dict[str, object]]] = []
    terminated: list[bool] = []
    hard_exits: list[int] = []
    stopped = threading.Event()

    def post(url: str, **kwargs: object) -> SimpleNamespace:
        posts.append((url, kwargs))
        return SimpleNamespace(status_code=200)

    def terminate() -> None:
        terminated.append(True)
        stopped.set()

    watch_tui_host_liveness(
        app,
        identity,
        stopped,
        assess=lambda _identity: SimpleNamespace(
            live=False, reason_code="parent_changed"
        ),
        post=post,
        terminate=terminate,
        hard_exit=hard_exits.append,
        check_seconds=0.0,
        termination_grace_seconds=0.0,
    )

    assert app.host_liveness_lost is True
    assert terminated == [True]
    assert hard_exits == []
    assert posts == [
        (
            "http://127.0.0.1:8765/v2/tmux/runtimes/agent%2Fa/writer/release",
            {
                "json": {"client_id": "tui-exact", "ttl_seconds": 30.0},
                "headers": {"Authorization": "Bearer secret"},
                "timeout": 2.0,
            },
        )
    ]


def test_host_watchdog_hard_exits_when_graceful_termination_stalls() -> None:
    identity = TuiHostTerminalIdentity(0, "/dev/pts/7", 1, 2, 3, 42, 1234)
    app = SimpleNamespace(
        host_liveness_lost=False,
        embedded_terminal_agent_id=None,
        tmux_terminal_client_id="tui-exact",
        server="http://127.0.0.1:8765",
        token="secret",
    )
    terminated: list[bool] = []
    hard_exits: list[int] = []

    watch_tui_host_liveness(
        app,
        identity,
        threading.Event(),
        assess=lambda _identity: SimpleNamespace(
            live=False, reason_code="host_terminal_unavailable"
        ),
        terminate=lambda: terminated.append(True),
        hard_exit=hard_exits.append,
        check_seconds=0.0,
        termination_grace_seconds=0.0,
    )

    assert app.host_liveness_lost is True
    assert terminated == [True]
    assert hard_exits == [0]


def test_host_watchdog_hard_exits_after_run_when_host_was_lost() -> None:
    identity = TuiHostTerminalIdentity(0, "/dev/pts/7", 1, 2, 3, 42, 1234)
    app = SimpleNamespace(
        host_liveness_lost=False,
        embedded_terminal_agent_id="agent-a",
        tmux_terminal_client_id="tui-exact",
        server="http://127.0.0.1:8765",
        token="secret",
    )
    posts: list[str] = []
    hard_exits: list[int] = []

    assert finalize_tui_host_watchdog(
        app,
        identity,
        assess=lambda _identity: SimpleNamespace(
            live=False, reason_code="parent_changed"
        ),
        post=lambda url, **_kwargs: posts.append(url)
        or SimpleNamespace(status_code=200),
        hard_exit=hard_exits.append,
    )
    assert app.host_liveness_lost is True
    assert posts == [
        "http://127.0.0.1:8765/v2/tmux/runtimes/agent-a/writer/release"
    ]
    assert hard_exits == [0]


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


def direct_model_catalog() -> tuple[CodexModelOption, ...]:
    return (
        CodexModelOption(
            slug="gpt-6.2-sol",
            display_name="Sol 6.2",
            default_reasoning_level="medium",
            supported_reasoning_levels=("low", "medium", "high", "xhigh", "max", "ultra"),
            service_tiers=("fast", "ultrafast"),
            default_service_tier="",
            visibility="list",
            shell_type="shell",
            supported_in_api=True,
            supports_verbosity=True,
            default_verbosity="high",
        ),
    )


def compatibility_model_catalog() -> tuple[CodexModelOption, ...]:
    return (
        CodexModelOption(
            slug="gpt-5.6-sol",
            display_name="Sol 5.6",
            default_reasoning_level="xhigh",
            supported_reasoning_levels=("high", "xhigh", "max"),
            service_tiers=(),
            default_service_tier="",
            visibility="list",
            shell_type="shell",
            supported_in_api=True,
            supports_verbosity=True,
            default_verbosity="high",
        ),
    )


def test_runtime_model_catalog_loads_at_monotonic_zero_and_respects_ttl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 0.0
    monkeypatch.setattr(
        codex_runtime,
        "time",
        SimpleNamespace(monotonic=lambda: now),
    )
    calls: list[float] = []

    def load_catalog() -> tuple[CodexModelOption, ...]:
        calls.append(now)
        return direct_model_catalog() if len(calls) == 1 else ()

    _client, app = runtime_client(tmp_path)
    service = CodexRuntimeService(app.state.store, catalog_loader=load_catalog)
    assert service._catalog() == direct_model_catalog()
    assert calls == [0.0]
    now = 300.0
    assert service._catalog() == direct_model_catalog()
    assert calls == [0.0]
    now = 301.0
    assert service._catalog() == ()
    now = 302.0
    assert service._catalog() == ()
    assert calls == [0.0, 301.0]


def test_runtime_direct_model_choice_can_be_adopted_per_entity(tmp_path: Path) -> None:
    client, app = runtime_client(tmp_path)
    app.state.codex_runtime.catalog_loader = direct_model_catalog
    app.state.codex_runtime._model_catalog_loaded_at = None

    observed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": "gpt-6.2-sol",
                    "modelProvider": "openai",
                    "effort": "low",
                    "summary": "detailed",
                },
            },
        },
    )
    assert observed.status_code == 200
    assert observed.json()["profile"]["selection_class"] == "catalog_observed"

    adopted = client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 200
    assert adopted.json()["profile_id"] == "catalog:gpt-6.2-sol:low"
    metadata = app.state.store.get_agent("agent-a")["metadata"]
    assert metadata["codex_model_preset"] == "catalog:gpt-6.2-sol:low"
    assert metadata["codex_model"] == "gpt-6.2-sol"
    assert metadata["codex_model_reasoning_effort"] == "low"


@pytest.mark.parametrize(
    ("model_slug", "provider", "catalog", "expected_class"),
    (
        (
            "gpt-6.2-sol",
            "ollama",
            direct_model_catalog(),
            "custom_provider_observed",
        ),
        ("gpt-6.2-sol", "openai", (), "catalog_stale"),
        ("gpt-6.2-unknown", "openai", direct_model_catalog(), "unknown"),
    ),
)
def test_runtime_adoption_revalidates_provider_and_catalog_evidence(
    tmp_path: Path,
    model_slug: str,
    provider: str,
    catalog: tuple[CodexModelOption, ...],
    expected_class: str,
) -> None:
    client, app = runtime_client(tmp_path)
    app.state.codex_runtime.catalog_loader = lambda: catalog
    app.state.codex_runtime._model_catalog_loaded_at = None

    observed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": model_slug,
                    "modelProvider": provider,
                    "effort": "high",
                },
            },
        },
    )
    assert observed.status_code == 200
    assert observed.json()["profile"]["selection_class"] == expected_class

    adopted = client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 409
    metadata = app.state.store.get_agent("agent-a")["metadata"]
    assert "codex_model_adopted_at" not in metadata


def test_runtime_adoption_rechecks_catalog_after_observation(tmp_path: Path) -> None:
    client, app = runtime_client(tmp_path)
    app.state.codex_runtime.catalog_loader = direct_model_catalog
    app.state.codex_runtime._model_catalog_loaded_at = None
    observed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": "gpt-6.2-sol",
                    "modelProvider": "openai",
                    "effort": "high",
                },
            },
        },
    )
    assert observed.status_code == 200
    assert observed.json()["profile"]["selection_class"] == "managed_qualified"

    app.state.codex_runtime.catalog_loader = lambda: ()
    app.state.codex_runtime._model_catalog_loaded_at = None
    adopted = client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 409


def test_runtime_direct_elevation_cannot_be_adopted_without_lease(
    tmp_path: Path,
) -> None:
    client, app = runtime_client(tmp_path)
    app.state.codex_runtime.catalog_loader = direct_model_catalog
    app.state.codex_runtime._model_catalog_loaded_at = None
    observed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": "gpt-6.2-sol",
                    "modelProvider": "openai",
                    "effort": "max",
                },
            },
        },
    )
    assert observed.status_code == 200
    assert observed.json()["profile"]["requires_approval"] is True

    adopted = client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 409
    assert "elevation lease" in adopted.json()["detail"]


def test_runtime_direct_elevation_can_be_adopted_with_matching_active_lease(
    tmp_path: Path,
) -> None:
    client, app = runtime_client(tmp_path)
    app.state.codex_runtime.catalog_loader = direct_model_catalog
    app.state.codex_runtime._model_catalog_loaded_at = None
    requested = client.post(
        "/v2/codex/elevations",
        headers=HEADERS,
        json={
            "agent_id": "agent-a",
            "requested_profile": "sol-6.2-max",
            "prior_profile": "sol-6.2-high",
            "justification": "Bounded difficult task",
            "scope": "one_task",
        },
    )
    assert requested.status_code == 200, requested.text
    lease_id = requested.json()["lease_id"]
    approved = client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=HEADERS,
        json={
            "approved": True,
            "approved_by": "agent-pbx-tui-user",
            "duration_seconds": 60,
        },
    )
    assert approved.status_code == 200
    activated = client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=HEADERS,
        json={"transition": {"phase": "runtime_associated"}},
    )
    assert activated.status_code == 200

    observed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": "gpt-6.2-sol",
                    "modelProvider": "openai",
                    "effort": "max",
                },
            },
        },
    )
    assert observed.status_code == 200

    adopted = client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 200
    assert adopted.json()["profile_id"] == "sol-6.2-max"
    metadata = app.state.store.get_agent("agent-a")["metadata"]
    assert metadata["codex_model_preset"] == "sol-6.2-max"


def test_runtime_custom_provider_elevation_is_rejected_even_with_lease(
    tmp_path: Path,
) -> None:
    client, app = runtime_client(tmp_path)
    app.state.codex_runtime.catalog_loader = direct_model_catalog
    app.state.codex_runtime._model_catalog_loaded_at = None
    requested = client.post(
        "/v2/codex/elevations",
        headers=HEADERS,
        json={
            "agent_id": "agent-a",
            "requested_profile": "sol-6.2-max",
            "prior_profile": "sol-6.2-high",
            "justification": "Bounded difficult task",
            "scope": "one_task",
        },
    )
    lease_id = requested.json()["lease_id"]
    client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=HEADERS,
        json={
            "approved": True,
            "approved_by": "agent-pbx-tui-user",
            "duration_seconds": 60,
        },
    )
    client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=HEADERS,
        json={"transition": {"phase": "runtime_associated"}},
    )
    observed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": "gpt-6.2-sol",
                    "modelProvider": "ollama",
                    "effort": "max",
                },
            },
        },
    )
    assert observed.status_code == 200
    assert observed.json()["profile"]["selection_class"] == "custom_provider_observed"

    adopted = client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 409


@pytest.mark.parametrize(
    ("requested_profile", "canonical_profile"),
    (
        ("sol-5.6-max", "sol-5.6-max"),
        ("sol-max", "sol-5.6-max"),
        ("sol5.6-max", "sol-5.6-max"),
        ("gpt-5.6-sol-max", "sol-5.6-max"),
    ),
)
def test_runtime_elevation_request_canonicalizes_legacy_sol_aliases(
    tmp_path: Path,
    requested_profile: str,
    canonical_profile: str,
) -> None:
    client, _app = runtime_client(tmp_path)
    requested = client.post(
        "/v2/codex/elevations",
        headers=HEADERS,
        json={
            "agent_id": "agent-a",
            "requested_profile": requested_profile,
            "prior_profile": "sol-xhigh",
            "justification": "Compatibility rollback validation",
            "scope": "one_task",
        },
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["requested_profile"] == canonical_profile


def test_runtime_legacy_alias_lease_matches_canonical_observed_profile(
    tmp_path: Path,
) -> None:
    legacy_client, legacy_app = runtime_client(tmp_path)
    legacy_app.state.codex_runtime.catalog_loader = compatibility_model_catalog
    legacy_app.state.codex_runtime._model_catalog_loaded_at = None

    legacy_lease, _created = legacy_app.state.store.request_or_reuse_model_elevation(
        agent_id="agent-a",
        requested_profile="sol-max",
        prior_profile="sol-xhigh",
        justification="Persisted compatibility rollback validation",
        scope="one_task",
    )
    lease_id = legacy_lease["lease_id"]
    decided = legacy_client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=HEADERS,
        json={
            "approved": True,
            "approved_by": "agent-pbx-tui-user",
            "duration_seconds": 60,
        },
    )
    assert decided.status_code == 200
    activated = legacy_client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=HEADERS,
        json={"transition": {"phase": "runtime_associated"}},
    )
    assert activated.status_code == 200
    observed = legacy_client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-root",
                "threadSettings": {
                    "model": "gpt-5.6-sol",
                    "modelProvider": "openai",
                    "effort": "max",
                },
            },
        },
    )
    assert observed.status_code == 200
    adopted = legacy_client.post(
        "/v2/agents/agent-a/codex-runtime/adopt-model",
        headers=HEADERS,
    )
    assert adopted.status_code == 200
    assert adopted.json()["profile_id"] == "sol-5.6-max"


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
            "parent_thread_id": "thread-root",
            "started_at": snapshot["children"][0]["started_at"],
            "finished_at": None,
            "last_transition_at": snapshot["children"][0]["last_transition_at"],
            "transition_source": "app_server",
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


def test_runtime_projection_accepts_current_subagent_activity_without_paths_or_roles(
    tmp_path: Path,
) -> None:
    client, app = runtime_client(tmp_path)
    started = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/started",
            "params": {
                "threadId": "thread-root",
                "item": {
                    "id": "activity-1",
                    "type": "subAgentActivity",
                    "agentThreadId": "thread-child-current",
                    "agentPath": "/private/.codex/agents/security-reviewer.toml",
                    "kind": "started",
                    "model": "gpt-5.6-sol",
                    "reasoningEffort": "high",
                },
            },
        },
    )
    assert started.status_code == 200
    child = started.json()["children"][0]
    assert child["child_id"] == "thread-child-current"
    assert child["parent_id"] == "agent-a"
    assert child["state"] == "starting"
    assert child["role"] is None

    completed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/completed",
            "params": {
                "threadId": "thread-root",
                "item": {
                    "id": "activity-2",
                    "type": "subAgentActivity",
                    "agentThreadId": "thread-child-current",
                    "agentPath": "/private/.codex/agents/security-reviewer.toml",
                    "kind": "completed",
                },
            },
        },
    )
    assert completed.status_code == 200
    child = completed.json()["children"][0]
    assert child["state"] == "complete"
    assert child["finished_at"] is not None
    serialized = json.dumps(
        app.state.store.list_subject_events(
            "agent-a", event_type="codex_runtime_observed"
        )
    )
    assert "security-reviewer.toml" not in serialized
    assert "/private" not in serialized


def test_runtime_projection_degrades_unknown_and_partial_subagent_activity_safely(
    tmp_path: Path,
) -> None:
    client, _app = runtime_client(tmp_path)
    unknown = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/started",
            "params": {
                "item": {
                    "type": "subAgentActivity",
                    "agentThreadId": "thread-child-future",
                    "agentPath": "future",
                    "kind": "future-kind",
                }
            },
        },
    )
    assert unknown.status_code == 200
    assert unknown.json()["children"][0]["state"] == "unknown"

    partial = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/completed",
            "params": {"item": {"type": "subAgentActivity", "kind": "completed"}},
        },
    )
    assert partial.status_code == 200
    assert partial.json()["children"][0]["child_id"] == "thread-child-future"


def test_collab_tool_completion_does_not_falsely_complete_child(tmp_path: Path) -> None:
    client, _app = runtime_client(tmp_path)
    item = {
        "type": "collabAgentToolCall",
        "tool": "spawnAgent",
        "status": "completed",
        "senderThreadId": "thread-root",
        "receiverThreadIds": ["thread-child"],
    }
    started = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/started",
            "observed_at": 200.0,
            "params": {"item": item},
        },
    )
    assert started.status_code == 200
    completed = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/completed",
            "observed_at": 210.0,
            "params": {"item": item},
        },
    )
    assert completed.status_code == 200
    child = completed.json()["children"][0]
    assert child["state"] == "starting"
    assert child["finished_at"] is None


def test_older_historical_child_event_cannot_overwrite_newer_live_state(
    tmp_path: Path,
) -> None:
    client, app = runtime_client(tmp_path)
    live = client.post(
        "/v2/agents/agent-a/codex-runtime/events",
        headers=HEADERS,
        json={
            "thread_id": "thread-root",
            "method": "item/started",
            "observed_at": 300.0,
            "params": {
                "item": {
                    "type": "subAgentActivity",
                    "agentThreadId": "thread-child",
                    "kind": "interacted",
                }
            },
        },
    )
    assert live.status_code == 200
    runtime = app.state.codex_runtime
    historical = runtime.observe_historical_item(
        "agent-a",
        thread_id="thread-root",
        method="item/completed",
        params={
            "item": {
                "type": "subAgentActivity",
                "agentThreadId": "thread-child",
                "kind": "completed",
            }
        },
        observed_at=200.0,
    )
    child = historical["children"][0]
    assert child["state"] == "thinking"
    assert child["last_transition_at"] == 300.0
    assert child["transition_source"] == "app_server"


def test_runtime_bounds_active_and_recent_terminal_children(tmp_path: Path) -> None:
    _client, app = runtime_client(tmp_path)
    runtime = app.state.codex_runtime
    for index in range(40):
        runtime.observe_discovered_child(
            "agent-a",
            thread_id="thread-root",
            child_id=f"active-{index:02d}",
            state=CodexRuntimeState.THINKING,
            observed_at=100.0 + index,
        )
        runtime.observe_discovered_child(
            "agent-a",
            thread_id="thread-root",
            child_id=f"done-{index:02d}",
            state=CodexRuntimeState.COMPLETE,
            observed_at=200.0 + index,
            finished_at=200.0 + index,
        )

    children = runtime.snapshot("agent-a")["children"]
    assert len(children) == 64
    assert sum(child["state"] == "thinking" for child in children) == 32
    assert sum(child["state"] == "complete" for child in children) == 32


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


def test_runtime_restart_generation_rejects_stale_profile_projection(
    tmp_path: Path,
) -> None:
    _client, app = runtime_client(tmp_path)
    runtime = app.state.codex_runtime
    runtime.observe_protocol(
        "agent-a",
        thread_id="thread-root",
        method="thread/started",
        params={
            "thread": {
                "id": "thread-root",
                "model": "gpt-5.6-sol",
                "reasoningEffort": "xhigh",
                "modelProvider": "openai",
            }
        },
        observed_at=100.0,
    )
    app.state.store.merge_agent_metadata(
        "agent-a",
        {
            "codex_model_preset": "sol-6.2-xhigh",
            "codex_model": "gpt-6.2-sol",
            "codex_model_reasoning_effort": "xhigh",
            "last_tmux_restart_at": 200.0,
        },
    )

    restarted = CodexRuntimeService(
        app.state.store,
        catalog_loader=direct_model_catalog,
    )
    snapshot = restarted.snapshot("agent-a")
    assert snapshot["profile"]["profile_id"] == "sol-6.2-xhigh"
    assert snapshot["profile"]["model"] == "gpt-6.2-sol"

    stale = restarted.observe_protocol(
        "agent-a",
        thread_id="thread-root",
        method="thread/started",
        params={
            "thread": {
                "id": "thread-root",
                "model": "gpt-5.6-sol",
                "reasoningEffort": "xhigh",
                "modelProvider": "openai",
            }
        },
        observed_at=150.0,
    )
    assert stale["profile"]["model"] == "gpt-6.2-sol"

    current = restarted.observe_protocol(
        "agent-a",
        thread_id="thread-root",
        method="thread/settings/updated",
        params={
            "threadSettings": {
                "model": "gpt-6.2-sol",
                "effort": "high",
                "modelProvider": "openai",
            }
        },
        observed_at=201.0,
    )
    assert current["profile"]["model"] == "gpt-6.2-sol"
    assert current["profile"]["reasoning_effort"] == "high"
    assert current["profile"]["observation_source"] == "thread_settings_updated"


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
    assert "STALE · last THINKING" in topology.plain
    assert "reasoning" not in topology.plain.lower()


def test_runtime_topology_separates_current_availability_from_lifecycle() -> None:
    snapshot = json.loads(
        (Path(__file__).parent / "fixtures" / "codex_subagent_not_loaded_v1.json")
        .read_text(encoding="utf-8")
    )

    topology = runtime_topology(snapshot, now=2_000.0)

    assert "SUBAGENTS 0 active · 8 stale · 14 done" in topology.plain
    assert "Descartes · stale-08" in topology.plain
    assert "UNLOADED · last THINKING" in topology.plain
    assert "done-14" not in topology.plain


def test_runtime_topology_orders_confirmed_active_before_recent_stale_and_done() -> None:
    snapshot = {
        "capabilities": {"native_subagents": "observed"},
        "children": [
            {
                "child_id": "done-child",
                "state": "complete",
                "nickname": "Done",
                "finished_at": 1_995.0,
                "availability": "unloaded",
                "availability_observed_at": 1_995.0,
            },
            {
                "child_id": "stale-child",
                "state": "thinking",
                "nickname": "Stale",
                "last_transition_at": 1_990.0,
                "availability": "unloaded",
                "availability_observed_at": 1_999.0,
            },
            {
                "child_id": "active-child",
                "state": "executing",
                "nickname": "Active",
                "last_transition_at": 1_998.0,
                "availability": "active",
                "availability_observed_at": 1_999.0,
            },
        ],
    }

    topology = runtime_topology(snapshot, now=2_000.0)

    assert "SUBAGENTS 1 active · 1 stale · 1 done" in topology.plain
    assert topology.plain.index("Active · active-ch") < topology.plain.index(
        "Stale · stale-ch"
    )
    assert topology.plain.index("Stale · stale-ch") < topology.plain.index(
        "Done · done-chi"
    )


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


async def test_tui_agent_selection_preloads_workerbee_runtime_status() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[tuple[str, bool]] = []

    async def no_refresh(_agent_id: str, **_kwargs: object) -> None:
        return None

    async def load_workerbee(
        agent_id: str,
        *,
        render_detail: bool = True,
        selection_generation: int | None = None,
    ) -> None:
        loaded.append((agent_id, render_detail))
        app.workerbee_status_by_agent[agent_id] = {
            "available": True,
            "running": False,
        }
        if app.selected_agent_id == agent_id:
            app.render_codex_runtime_surface()

    app.refresh_selected_agent = no_refresh  # type: ignore[method-assign]
    app.load_codex_runtime_status = no_refresh  # type: ignore[method-assign]
    app.load_workerbee_status = load_workerbee  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "agent-a": {
                "agent_id": "agent-a",
                "project": "demo",
                "status": "working",
                "last_seen_at": 100.0,
                "metadata": {},
            }
        }
        await app.select_agent("agent-a")
        await pilot.pause()

        assert loaded == [("agent-a", False)]
        assert "WB ◇" in str(app.query_one("#codex-runtime-status").renderable)

        await app.select_agent("agent-a")
        await pilot.pause()

        assert loaded == [("agent-a", False)]


async def test_tui_native_subagents_palette_sends_codex_command() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    sent: list[tuple[str, str]] = []

    async def fake_send(agent_id: str, message: str) -> bool:
        sent.append((agent_id, message))
        return True

    app.send_text_to_tmux = fake_send  # type: ignore[method-assign]
    app.is_tmux_direct_enabled = lambda _agent_id: True  # type: ignore[method-assign]
    app.selected_agent_id = "agent-a"

    assert await app.open_native_codex_subagents() is True
    assert sent == [("agent-a", "/subagents")]


async def test_tui_restored_selection_preloads_workerbee_runtime_status() -> None:
    app = AgentPBXTUI(server="http://127.0.0.1:8765")
    loaded: list[tuple[str, bool]] = []

    async def load_workerbee(
        agent_id: str,
        *,
        render_detail: bool = True,
        selection_generation: int | None = None,
    ) -> None:
        loaded.append((agent_id, render_detail))
        app.workerbee_status_by_agent[agent_id] = {
            "available": True,
            "running": False,
        }

    app.load_workerbee_status = load_workerbee  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        app.agents = {
            "agent-a": {
                "agent_id": "agent-a",
                "project": "demo",
                "status": "working",
                "last_seen_at": 100.0,
                "metadata": {},
            }
        }
        app.pending_remote_view_state = {
            "selected_agent_id": "agent-a",
            "active_agent_tab": "latest-tab",
        }

        app.apply_pending_remote_view_state()
        await pilot.pause()

        assert app.selected_agent_id == "agent-a"
        assert loaded == [("agent-a", False)]
