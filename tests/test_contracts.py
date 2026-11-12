from agent_pbx.contracts import (
    CapabilityRecord,
    CapabilitySupport,
    CodexRuntimeState,
    DEFAULT_EVIDENCE_REDACTION_POLICY,
    ENGINEERING_CONTRACTS,
    HelperReuseMeasurement,
    RuntimeEvidence,
    RuntimeEvidenceSource,
    RuntimeIdentity,
    WORKERBEE_DEVELOPMENT_CAPABILITY_BOUNDARY,
    engineering_feature_defaults,
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


def test_engineering_contracts_have_unique_versioned_schemas_and_disabled_gates() -> None:
    schemas = [contract.schema for contract in ENGINEERING_CONTRACTS]
    gates = [contract.feature_gate.value for contract in ENGINEERING_CONTRACTS]
    assert len(schemas) == len(set(schemas))
    assert len(gates) == len(set(gates))
    assert all(schema.endswith(("/v1", "/v2")) for schema in schemas)
    assert engineering_feature_defaults() == {gate: False for gate in gates}


def test_evidence_redaction_policy_is_closed_by_default() -> None:
    policy = DEFAULT_EVIDENCE_REDACTION_POLICY
    assert policy.allows("report_id") is True
    assert policy.allows("credential") is False
    assert policy.allows("hidden_reasoning") is False
    assert policy.allows("prompt_body") is False
    assert policy.allows("terminal_history") is False
    assert policy.include_prompt_bodies is False
    assert policy.include_terminal_history is False
    assert policy.include_hidden_reasoning is False


def test_helper_reuse_measurement_is_opt_in_and_content_free() -> None:
    disabled = HelperReuseMeasurement()
    assert disabled.reuse_ratio is None
    assert disabled.churn_ratio is None
    enabled = HelperReuseMeasurement(
        enabled=True,
        proposals=4,
        activations=4,
        reuses=3,
        replacements=1,
        quarantines=0,
    )
    assert enabled.reuse_ratio == 0.75
    assert enabled.churn_ratio == 0.25


def test_workerbee_development_boundary_accepts_only_exact_readonly_evidence() -> None:
    boundary = WORKERBEE_DEVELOPMENT_CAPABILITY_BOUNDARY
    evidence = {
        "schema": boundary.schema,
        "provider": "workerbee-development/v1",
        "provider_generation": "provider-7",
        "backend": "platform_debug",
        "backend_instance": "debug-1",
        "backend_generation": "generation-7",
        "workspace_binding_id": "binding-1",
        "resource_classes": list(boundary.resource_classes),
        "mount_targets": {},
        "request_schema": "agent-pbx.workerbee-development-resource/v1",
        "receipt_schema": "workerbee.platform-debug-development-resource/v1",
        "cleanup_schema": (
            "workerbee.platform-debug-development-resource-cleanup/v1"
        ),
        "trusted_direct_bind_read_only": True,
        "trusted_direct_bind_rprivate": True,
        "writable_source": False,
        "max_ttl_seconds": 3600,
        "max_quota_bytes": 4096,
        "issued_at": 1.0,
        "expires_at": 2.0,
        "capability_sha256": "a" * 64,
    }
    boundary.validate_provider_evidence(evidence)

    for field, value in (
        ("trusted_direct_bind_read_only", False),
        ("trusted_direct_bind_rprivate", False),
        ("writable_source", True),
        ("backend_generation", True),
    ):
        invalid = dict(evidence)
        invalid[field] = value
        try:
            boundary.validate_provider_evidence(invalid)
        except ValueError:
            pass
        else:  # pragma: no cover - makes the fail-closed assertion explicit
            raise AssertionError(f"invalid capability evidence accepted: {field}")
