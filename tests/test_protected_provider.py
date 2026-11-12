from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.git_worktree import _git as qualified_git
from agent_pbx.git_worktree import qualify_linked_worktree_git_admin
from agent_pbx.operator import OperatorService
from agent_pbx.operator_delegation import normalize_execution_scope
from agent_pbx.protected_provider import (
    ACTION_CLASS,
    ENVELOPE_SCHEMA,
    TOPOLOGY_SCHEMA,
    ProtectedProviderDiagnosticError,
    ProtectedProviderDiagnosticService,
    canonical_sha256,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


WORKSPACE_DIGEST = "a" * 64


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Agent PBX Test",
            "GIT_AUTHOR_EMAIL": "agent-pbx@example.invalid",
            "GIT_COMMITTER_NAME": "Agent PBX Test",
            "GIT_COMMITTER_EMAIL": "agent-pbx@example.invalid",
        },
    )
    return result.stdout.strip()


def linked_repository(tmp_path: Path) -> tuple[Path, str, str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    main.mkdir()
    git(main, "init", "-b", "main")
    (main / "README.md").write_text("baseline\n", encoding="utf-8")
    git(main, "add", "README.md")
    git(main, "commit", "-m", "baseline")
    head = git(main, "rev-parse", "HEAD")
    git(main, "checkout", "-b", "provider/candidate")
    (main / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    git(main, "add", "candidate.txt")
    git(main, "commit", "-m", "candidate")
    candidate = git(main, "rev-parse", "HEAD")
    expected_tree = git(main, "rev-parse", "HEAD^{tree}")
    git(main, "checkout", "main")
    git(main, "worktree", "add", "-b", "pbx-operator-9/merge", str(linked), head)
    git(linked, "remote", "add", "origin", str(main))
    git(
        linked,
        "config",
        "remote.origin.fetch",
        "+refs/heads/*:refs/remotes/origin/*",
    )
    git(linked, "update-ref", "refs/remotes/origin/dev", head)
    git(linked, "branch", "--set-upstream-to", "origin/dev")
    return linked, head, candidate, expected_tree


def reviewed_envelope(
    tmp_path: Path,
    *,
    campaign_id: str,
) -> tuple[dict[str, Any], Path]:
    window_start = time.time()
    linked, head, candidate, expected_tree = linked_repository(tmp_path)
    plan = qualify_linked_worktree_git_admin(
        linked,
        approved_roots=(tmp_path,),
        fork_purpose="edit",
        access_mode="edit",
        codex_home=None,
    )
    assert plan is not None
    topology = {
        "schema": TOPOLOGY_SCHEMA,
        "worktree_root": str(plan.worktree_root),
        "git_dir": str(plan.git_dir),
        "common_dir": str(plan.common_dir),
        "object_dir": str(plan.object_dir),
        "branch_ref": plan.branch_ref,
        "ref_namespace": str(plan.ref_namespace),
        "reflog_namespace": str(plan.reflog_namespace),
    }
    envelope: dict[str, Any] = {
        "schema": ENVELOPE_SCHEMA,
        "envelope_id": "reviewed-envelope-1",
        "campaign_id": campaign_id,
        "workspace_set_digest": WORKSPACE_DIGEST,
        "repository_root": str(plan.worktree_root),
        "git_dir": str(plan.git_dir),
        "common_dir": str(plan.common_dir),
        "object_dir": str(plan.object_dir),
        "branch_ref": plan.branch_ref,
        "head_oid": head,
        "upstream_ref": "origin/dev",
        "upstream_oid": head,
        "topology_sha256": canonical_sha256(topology),
        "owner_uid": os.geteuid(),
        "workspace_generation": "workspace-generation-1",
        "provider_generation": "provider-generation-1",
        "provider_receipt_id": "provider-receipt-1",
        "provider_receipt_sha256": "b" * 64,
        "merge_base_oid": head,
        "candidate_oid": candidate,
        "expected_tree_oid": expected_tree,
        "protected_path_names": [".env.local", "private/provider.json"],
        "issued_at": window_start - 60,
        "expires_at": window_start + 3600,
        "reviewed_by_operator_agent_id": "operator-0",
    }
    envelope["envelope_sha256"] = canonical_sha256(envelope)
    return envelope, linked


def approved_context(
    tmp_path: Path,
    *,
    store: Store | None = None,
) -> tuple[
    Store,
    ProtectedProviderDiagnosticService,
    dict[str, Any],
    Path,
]:
    store = store or Store(tmp_path / "pbx.sqlite")
    store.init()
    for request in (
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "operator_role": "root",
                "operator_authority_activated_workspace_set_digest": WORKSPACE_DIGEST,
            },
        ),
        AgentRegisterRequest(
            agent_id="operator-9",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"operator_role": "root"},
        ),
    ):
        store.register_agent(request)
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-0",
        title="Protected provider diagnostics",
        objective="Reconcile reviewed metadata before provider work",
        criteria=[],
        assignments=[],
    )
    envelope, linked = reviewed_envelope(
        tmp_path / "repository",
        campaign_id=str(campaign["campaign_id"]),
    )
    service = OperatorService(store)
    handoff = service.create_handoff(
        operator_agent_id="operator-0",
        source_agent_id="operator-0",
        target_operator_agent_id="operator-9",
        message="Reconcile reviewed protected-provider metadata only.",
        execution_scope={
            "action_class": ACTION_CLASS,
            "campaign_id": campaign["campaign_id"],
            "workspace_set_digest": WORKSPACE_DIGEST,
            "mutation_mode": "read_only",
            "allowed_roots": [str(linked.parent)],
            "protected_provider_merge_envelope": envelope,
        },
    )
    approved = service.approve_handoff(
        operator_agent_id="operator-0",
        handoff_id=str(handoff["handoff_id"]),
        delivery="record_only",
        metadata={"approved_by": "agent-pbx-tui"},
    )["handoff"]
    manual = approved["metadata"]["manual_approval"]
    arguments = {
        "operator_agent_id": "operator-0",
        "handoff_id": approved["handoff_id"],
        "approval_id": manual["approval_id"],
        "envelope_sha256": envelope["envelope_sha256"],
        "workspace_generation": envelope["workspace_generation"],
        "provider_generation": envelope["provider_generation"],
        "provider_receipt_id": envelope["provider_receipt_id"],
        "provider_receipt_sha256": envelope["provider_receipt_sha256"],
    }
    return (
        store,
        ProtectedProviderDiagnosticService(
            store,
            clock=lambda: float(manual["approved_at"]) + 1,
        ),
        arguments,
        linked,
    )


def test_metadata_only_diagnostic_reconciles_reviewed_envelope(
    tmp_path: Path,
) -> None:
    _store, service, arguments, _linked = approved_context(tmp_path)

    result = service.diagnose(**arguments)

    assert result["status"] == "ready"
    assert result["authority"] == "metadata_only_read_only"
    assert result["workspace_generation_match"] is True
    assert result["provider_generation_match"] is True
    assert result["provider_receipt_match"] is True
    assert result["merge_envelope_match"] is True
    assert result["protected_path_count"] == 2
    serialized = json.dumps(result, sort_keys=True)
    assert ".env.local" not in serialized
    assert "private/provider.json" not in serialized


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("provider_generation", "drifted", "provider_generation_mismatch"),
        ("provider_receipt_sha256", "c" * 64, "provider_receipt_sha256_mismatch"),
        ("envelope_sha256", "d" * 64, "envelope_mismatch"),
        ("approval_id", "foreign-approval", "manual_approval_invalid"),
    ),
)
def test_generation_receipt_envelope_and_approval_drift_fail_before_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
    code: str,
) -> None:
    _store, service, arguments, _linked = approved_context(tmp_path)
    monkeypatch.setattr(
        "agent_pbx.protected_provider.qualify_linked_worktree_git_admin",
        lambda *args, **kwargs: pytest.fail("Git qualification must not run"),
    )
    arguments[field] = value

    with pytest.raises(ProtectedProviderDiagnosticError) as exc_info:
        service.diagnose(**arguments)

    assert exc_info.value.code == code


def test_upstream_and_topology_drift_fail_closed(tmp_path: Path) -> None:
    _store, service, arguments, linked = approved_context(tmp_path)
    candidate = git(linked, "rev-parse", "provider/candidate")
    git(linked, "update-ref", "refs/remotes/origin/dev", candidate)

    with pytest.raises(ProtectedProviderDiagnosticError) as exc_info:
        service.diagnose(**arguments)

    assert exc_info.value.code == "upstream_oid_mismatch"


@pytest.mark.parametrize("boundary", ("before_issuance", "at_expiry"))
def test_manual_approval_must_be_inside_envelope_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    store, service, arguments, _linked = approved_context(tmp_path)
    handoff = store.get_operator_handoff(arguments["handoff_id"])
    assert handoff is not None
    envelope = handoff["execution_scope"]["protected_provider_merge_envelope"]
    metadata = dict(handoff["metadata"])
    manual = dict(metadata["manual_approval"])
    manual["approved_at"] = (
        envelope["issued_at"] - 1
        if boundary == "before_issuance"
        else envelope["expires_at"]
    )
    metadata["manual_approval"] = manual
    assert store.update_operator_handoff(
        arguments["handoff_id"], metadata=metadata
    ) is not None
    monkeypatch.setattr(
        "agent_pbx.protected_provider.qualify_linked_worktree_git_admin",
        lambda *args, **kwargs: pytest.fail("Git qualification must not run"),
    )

    with pytest.raises(ProtectedProviderDiagnosticError) as exc_info:
        service.diagnose(**arguments)

    assert exc_info.value.code == "manual_approval_invalid"


def test_action_scope_is_manual_read_only_and_exact_envelope_bound(
    tmp_path: Path,
) -> None:
    campaign_id = "campaign-scope-1"
    envelope, linked = reviewed_envelope(
        tmp_path / "repository",
        campaign_id=campaign_id,
    )
    scope = normalize_execution_scope(
        {
            "action_class": ACTION_CLASS,
            "campaign_id": campaign_id,
            "workspace_set_digest": WORKSPACE_DIGEST,
            "mutation_mode": "read_only",
            "allowed_roots": [str(linked.parent)],
            "protected_provider_merge_envelope": envelope,
        }
    )
    assert scope["provider_action"] is False
    assert scope["credential_access"] is False
    assert "provider_or_cloud" in scope["denied_actions"]
    assert scope["protected_provider_merge_envelope"] == envelope

    widened = dict(scope)
    widened["mutation_mode"] = "workspace"
    with pytest.raises(ValueError, match="must remain read-only"):
        normalize_execution_scope(widened)

    provider_widened = dict(scope)
    provider_widened["provider_action"] = True
    with pytest.raises(ValueError, match="cannot grant provider"):
        normalize_execution_scope(provider_widened)


def test_global_controller_api_exposes_only_the_bounded_diagnostic(
    tmp_path: Path,
) -> None:
    app = create_app(ServerConfig(db_path=tmp_path / "api.sqlite", token="secret"))
    store = app.state.store
    _store, service, arguments, _linked = approved_context(
        tmp_path / "context",
        store=store,
    )
    app.state.protected_provider_diagnostics = service
    client = TestClient(app)

    assert client.post(
        "/v2/operator/protected-provider/diagnose", json=arguments
    ).status_code in {401, 403}
    response = client.post(
        "/v2/operator/protected-provider/diagnose",
        headers={"Authorization": "Bearer secret"},
        json=arguments,
    )

    assert response.status_code == 200
    assert response.json()["authority"] == "metadata_only_read_only"
    assert qualified_git(_linked, "status", "--short") == ""
