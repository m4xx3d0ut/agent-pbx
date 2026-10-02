from agent_pbx.contracts import (
    CapabilityRecord,
    CapabilitySupport,
    CodexRuntimeState,
    RuntimeEvidence,
    RuntimeEvidenceSource,
    RuntimeIdentity,
)


def test_runtime_evidence_priority_preserves_structured_source_order() -> None:
    sources = [
        RuntimeEvidenceSource.APP_SERVER,
        RuntimeEvidenceSource.PBX_REPORT,
        RuntimeEvidenceSource.HOOK,
        RuntimeEvidenceSource.TRANSCRIPT,
        RuntimeEvidenceSource.PROCESS,
        RuntimeEvidenceSource.TERMINAL,
        RuntimeEvidenceSource.TMUX_HEURISTIC,
    ]
    priorities = [
        RuntimeEvidence(
            state=CodexRuntimeState.READY,
            source=source,
            observed_at=1.0,
        ).priority
        for source in sources
    ]
    assert priorities == sorted(priorities, reverse=True)


def test_runtime_identity_keeps_control_plane_and_terminal_ids_distinct() -> None:
    identity = RuntimeIdentity(
        entity_id="operator-0-review-fork-3",
        project="agent-pbx",
        entity_type="operator",
        codex_session_id="codex-thread",
        tmux_server="/tmp/tmux-1000/default",
        tmux_session="$9",
        tmux_window="@12",
        tmux_pane="%77",
    )
    assert identity.entity_id != identity.codex_session_id
    assert identity.tmux_pane == "%77"


def test_capability_support_requires_explicit_observation() -> None:
    record = CapabilityRecord(
        name="custom_agents",
        support=CapabilitySupport.ADVERTISED,
        source="schema",
        observed_at=1.0,
    )
    assert record.support is not CapabilitySupport.OBSERVED
