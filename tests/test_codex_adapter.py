from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.codex.adapter import CodexSessionAdapter, RuntimeCapture
from agent_pbx.codex.capabilities import (
    CAPABILITY_CACHE_VERSION,
    CodexCapabilityProbe,
    CodexCapabilitySnapshot,
    CodexFeature,
    parse_feature_list,
)
from agent_pbx.codex.protocol import parse_protocol_message
from agent_pbx.codex.state import RuntimeStateReducer
from agent_pbx.codex.state import app_server_event_state
from agent_pbx.codex_cli import CodexCliCapabilities, CodexModelOption
from agent_pbx.config import ServerConfig
from agent_pbx.contracts import (
    CapabilityRecord,
    CapabilitySupport,
    CodexRuntimeState,
    RuntimeEvidence,
    RuntimeEvidenceSource,
    RuntimeIdentity,
)


def capability_snapshot() -> CodexCapabilitySnapshot:
    return CodexCapabilitySnapshot(
        executable="/usr/bin/codex",
        executable_hash="hash",
        version="0.159.3",
        host="host",
        profile_hash="profile",
        probed_at=1.0,
        commands=("app-server",),
        options=("--model",),
        models=("gpt-5.6-sol",),
        features=(CodexFeature("multi_agent", "stable", True),),
        capabilities=(
            CapabilityRecord(
                "app_server_schema",
                CapabilitySupport.OBSERVED,
                "schema",
                1.0,
            ),
        ),
    )


def test_feature_parser_preserves_multiword_stage() -> None:
    features = parse_feature_list(
        "multi_agent stable true\nnext_feature under development false\n"
    )
    assert features == (
        CodexFeature("multi_agent", "stable", True),
        CodexFeature("next_feature", "under development", False),
    )


def test_capability_probe_caches_by_binary_profile_and_host(
    tmp_path: Path, monkeypatch
) -> None:
    executable = tmp_path / "codex"
    executable.write_bytes(b"codex fixture")
    cache = tmp_path / "capabilities.json"
    monkeypatch.setattr("agent_pbx.codex.capabilities.shutil.which", lambda _: str(executable))
    monkeypatch.setattr(
        "agent_pbx.codex.capabilities.inspect_codex_cli",
        lambda *_args, **_kwargs: CodexCliCapabilities(
            "0.159.3", ("app-server",), ("--model",)
        ),
    )
    monkeypatch.setattr(
        "agent_pbx.codex.capabilities.inspect_codex_model_catalog",
        lambda *_args, **_kwargs: (
            CodexModelOption(
                "gpt-5.6-sol",
                "Sol",
                "xhigh",
                ("high", "xhigh"),
                (),
                "",
                "list",
                "responses",
                True,
            ),
        ),
    )
    monkeypatch.setattr(
        "agent_pbx.codex.capabilities.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="multi_agent stable true\ngoals stable true\n",
            stderr="",
        ),
    )
    probe = CodexCapabilityProbe(cache_path=cache)
    monkeypatch.setattr(probe, "_probe_schema", lambda _: ("ClientRequest.json",))
    first = probe.inspect(profile={"model": "gpt-5.6-sol"})
    assert first.capability("app_server_schema").support is CapabilitySupport.OBSERVED
    assert first.models == ("gpt-5.6-sol",)
    assert cache.exists()
    monkeypatch.setattr(probe, "_probe_schema", lambda _: (_ for _ in ()).throw(AssertionError()))
    second = probe.inspect(profile={"model": "gpt-5.6-sol"})
    assert second.cache_key == first.cache_key
    assert second.as_dict()["api_version"] == CAPABILITY_CACHE_VERSION


def test_state_reducer_keeps_structured_evidence_over_newer_heuristic() -> None:
    reducer = RuntimeStateReducer()
    reducer.add(
        RuntimeEvidence(
            CodexRuntimeState.COMPLETE,
            RuntimeEvidenceSource.APP_SERVER,
            observed_at=1.0,
        )
    )
    reducer.add(
        RuntimeEvidence(
            CodexRuntimeState.THINKING,
            RuntimeEvidenceSource.TMUX_HEURISTIC,
            observed_at=2.0,
        )
    )
    assert reducer.current().state is CodexRuntimeState.COMPLETE


def test_current_app_server_event_shapes_normalize_without_ansi_parsing() -> None:
    assert app_server_event_state(
        "thread/status/changed",
        {"status": {"type": "active", "activeFlags": ["waitingOnApproval"]}},
    ) is CodexRuntimeState.WAITING_USER
    assert app_server_event_state(
        "item/started",
        {"item": {"type": "commandExecution", "status": "inProgress"}},
    ) is CodexRuntimeState.EXECUTING
    assert app_server_event_state(
        "item/started",
        {"item": {"type": "collabAgentToolCall", "status": "inProgress"}},
    ) is CodexRuntimeState.DELEGATING


def test_adapter_rejects_unrelated_app_server_thread() -> None:
    adapter = CodexSessionAdapter(
        RuntimeIdentity(
            entity_id="agent-a",
            project="demo",
            entity_type="caller",
            codex_session_id="thread-a",
        )
    )
    assert (
        adapter.observe_app_server(
            thread_id="thread-b", method="turn/started", observed_at=1.0
        )
        is None
    )
    accepted = adapter.observe_app_server(
        thread_id="thread-a", method="turn/started", observed_at=2.0
    )
    assert accepted is not None
    assert accepted.state is CodexRuntimeState.THINKING
    assert adapter.association.app_server_verified


def test_adapter_capture_order_and_transcript_provenance() -> None:
    adapter = CodexSessionAdapter(
        RuntimeIdentity("agent-a", "demo", "caller", codex_session_id="thread-a")
    )
    calls: list[str] = []

    def empty_copy() -> RuntimeCapture:
        calls.append("copy")
        return RuntimeCapture("  ", "copy")

    def transcript() -> RuntimeCapture:
        calls.append("transcript")
        return RuntimeCapture(
            "full response\n",
            "transcript",
            session_id="thread-a",
            path="/tmp/rollout.jsonl",
            phase="final_answer",
            line_index=17,
            mtime=3.0,
        )

    result = adapter.capture_latest(
        copy_capture=empty_copy,
        transcript_capture=transcript,
        tmux_capture=lambda: calls.append("tmux"),  # type: ignore[arg-type,return-value]
    )
    assert calls == ["copy", "transcript"]
    assert result == RuntimeCapture(
        "full response",
        "transcript",
        "thread-a",
        "/tmp/rollout.jsonl",
        "final_answer",
        17,
        3.0,
    )


def test_protocol_parser_accepts_notifications_only() -> None:
    message = parse_protocol_message(
        '{"method":"turn/started","params":{"threadId":"thread-a"}}'
    )
    assert message is not None
    assert message.params["threadId"] == "thread-a"
    assert parse_protocol_message('{"result":{}}') is None


def test_capabilities_endpoint_uses_probe_snapshot(tmp_path: Path) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    snapshot = capability_snapshot()
    app.state.codex_capabilities = SimpleNamespace(inspect=lambda **_: snapshot)
    response = TestClient(app).get(
        "/v2/codex/capabilities", headers={"Authorization": "Bearer secret"}
    )
    assert response.status_code == 200
    assert response.json()["version"] == "0.159.3"
    assert response.json()["capabilities"][0]["support"] == "observed"
