from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.operator_authority import (
    OPERATOR_AUTHORITY_CONTROL_MODE_ENV,
    OPERATOR_AUTHORITY_DEFAULT_PROFILE_ENV,
    OPERATOR_AUTHORITY_PROFILE_CEILING_ENV,
    OPERATOR_AUTHORITY_ROOT_CEILING_ENV,
    OperatorAuthorityError,
    OperatorAuthorityService,
    operator_authority_launch_overrides,
    operator_authority_sandbox,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store
from agent_pbx.workerbee_access import WorkerBeeAccessPolicyService
from agent_pbx.workerbee_policy import WorkerBeeWorkspacePolicyService


def git_repo(root: Path) -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=root,
        check=True,
    )
    (root / "README.md").write_text("workspace\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def register_operator(
    store: Store,
    agent_id: str,
    *,
    review: bool = False,
) -> None:
    metadata = {
        "agent_type": "operator",
        "operator_role": "fork" if "fork" in agent_id else "root",
        "fork_purpose": "review" if review else "edit",
        "access_mode": "review_readonly" if review else "edit",
    }
    store.register_agent(
        AgentRegisterRequest(
            agent_id=agent_id,
            agent_type="operator",
            project="agent-pbx-operator",
            metadata=metadata,
        )
    )


def register_edit_fork(store: Store, agent_id: str, root: Path) -> None:
    register_operator(store, "operator-7")
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"cwd": str(root), "codex_session_id": "source-session"},
        )
    )
    register_operator(store, agent_id)
    store.create_operator_fork(
        logical_operator_agent_id="operator-7",
        fork_agent_id=agent_id,
        source_caller_agent_id="caller-1",
        source_codex_session_id="source-session",
        cwd=str(root),
        fork_purpose="edit",
        access_mode="edit",
        metadata={"workerbee_mcp_profile": "observe-local"},
    )


def test_public_posture_is_scoped_and_collapses_nested_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "git"
    nested = root / "k1s-wt"
    nested.mkdir(parents=True)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = OperatorAuthorityService(
        store,
        environ={"AGENT_PBX_PROJECT_ROOTS": f"{root}:{nested}"},
    )

    posture = service.posture()

    assert posture.default_profile == "scoped-edit"
    assert posture.profile_ceiling == "trusted-workbench"
    assert posture.root_ceiling == (str(root),)


def test_collection_discovers_explicit_git_members_and_excludes_temp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "git"
    collection = root / "k1s-wt"
    first = collection / "k1s"
    second = collection / "k1s-workerbee"
    excluded = collection / "temp" / "not-selected"
    git_repo(first)
    git_repo(second)
    git_repo(excluded)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = OperatorAuthorityService(
        store,
        environ={OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]'},
    )

    view = service.discover_collection(str(collection))

    assert [item["path"] for item in view["members"]] == [str(first), str(second)]
    assert str(collection) not in [item["path"] for item in view["members"]]
    assert str(excluded) not in [item["path"] for item in view["members"]]


def test_workspace_sets_are_versioned_and_reject_outside_ceiling(tmp_path: Path) -> None:
    root = tmp_path / "git"
    first = root / "first"
    second = root / "second"
    outside = tmp_path / "outside"
    git_repo(first)
    git_repo(second)
    git_repo(outside)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = OperatorAuthorityService(
        store,
        environ={OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]'},
    )

    v1 = service.save_workspace_set(
        set_id="rosie-stackops",
        label="Rosie StackOps",
        members=[str(first)],
        updated_by="test",
    )
    v2 = service.save_workspace_set(
        set_id="rosie-stackops",
        label="Rosie StackOps",
        members=[str(first), str(second)],
        updated_by="test",
    )

    assert v1["version"] == 1
    assert v2["version"] == 2
    assert len(v2["members"]) == 2
    assert service.get_workspace_set("rosie-stackops", version=1) == v1
    with pytest.raises(OperatorAuthorityError) as denied:
        service.preview_workspace_set(
            label="Outside", members=[str(outside)]
        )
    assert denied.value.code == "OPERATOR_AUTHORITY_ROOT_CEILING"


def test_linked_worktree_set_carries_only_qualified_git_admin_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "git"
    main = root / "main"
    linked = root / "linked"
    git_repo(main)
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "pbx/operator-7", str(linked)],
        cwd=main,
        check=True,
    )
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'default_permissions = ":workspace"\n', encoding="utf-8"
    )
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = OperatorAuthorityService(
        store,
        environ={
            OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]',
            "CODEX_HOME": str(codex_home),
        },
    )

    saved = service.save_workspace_set(
        set_id="linked",
        label="Linked",
        members=[str(linked)],
        updated_by="test",
    )
    member = saved["members"][0]

    assert member["linked_worktree"] is True
    assert member["git_admin_paths"]
    common = main / ".git"
    assert str(common / "objects") in member["git_admin_paths"]
    assert str(common) not in member["git_admin_paths"]


def test_trusted_assignment_coordinates_workerbee_and_pins_set_version(
    tmp_path: Path,
) -> None:
    root = tmp_path / "git"
    first = root / "first"
    second = root / "second"
    git_repo(first)
    git_repo(second)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_edit_fork(store, "operator-7-fork-edit", first)
    access = WorkerBeeAccessPolicyService(store, environ={})
    workspace = WorkerBeeWorkspacePolicyService(store, environ={})
    service = OperatorAuthorityService(
        store,
        environ={
            OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]',
            OPERATOR_AUTHORITY_DEFAULT_PROFILE_ENV: "trusted-workbench",
        },
        workerbee_access=access,
        workerbee_workspace=workspace,
    )
    saved = service.save_workspace_set(
        set_id="rosie-stackops",
        label="Rosie StackOps",
        members=[str(first), str(second)],
        updated_by="test",
    )

    assignment = service.set_assignment(
        "operator-7-fork-edit",
        profile="trusted-workbench",
        workspace_set_id=saved["set_id"],
        profile_label="Rosie Workbench",
        updated_by="test",
    )

    assert assignment["effective_profile"] == "trusted-workbench"
    assert assignment["pending_activation"] is True
    assert assignment["workspace_set"]["version"] == 1
    assert access.get_profile("operator-7-fork-edit")["effective_profile"] == "local-deploy"
    policy = workspace.get_policy("operator-7-fork-edit")["effective"]
    assert policy["policy"] == "scoped-workbench"
    assert policy["request_mode"] == "automatic"
    assert policy["allowed_roots"] == [str(first), str(second)]
    metadata = store.get_agent("operator-7-fork-edit")["metadata"]
    assert metadata["workerbee_mcp_profile"] == "local-deploy"
    assert metadata["operator_authority_workspace_roots"] == [str(first), str(second)]
    assert metadata["operator_authority_requires_restart"] is True


def test_activation_allows_new_commit_but_rejects_topology_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "git"
    repo = root / "repo"
    git_repo(repo)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator(store, "operator-7")
    service = OperatorAuthorityService(
        store,
        environ={OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]'},
    )
    saved = service.save_workspace_set(
        set_id="set", label="Set", members=[str(repo)], updated_by="test"
    )
    service.set_assignment(
        "operator-7",
        profile="trusted-workbench",
        workspace_set_id=saved["set_id"],
        updated_by="test",
    )

    (repo / "next.txt").write_text("normal work\n", encoding="utf-8")
    subprocess.run(["git", "add", "next.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "normal work"], cwd=repo, check=True)
    activated = service.mark_activated("operator-7")
    assert store.get_agent("operator-7")["metadata"][
        "operator_authority_requires_restart"
    ] is False
    assert activated["effective_profile"] == "trusted-workbench"
    assert activated["pending_activation"] is False
    assert activated["activated_profile"] == "trusted-workbench"

    service.set_assignment(
        "operator-7",
        profile="trusted-workbench",
        workspace_set_id=saved["set_id"],
        updated_by="test",
    )
    subprocess.run(["git", "switch", "-qc", "other"], cwd=repo, check=True)
    with pytest.raises(OperatorAuthorityError) as drifted:
        service.mark_activated("operator-7")
    assert drifted.value.code == "OPERATOR_WORKSPACE_SET_DRIFT"
    assert store.get_agent("operator-7")["metadata"][
        "operator_authority_requires_restart"
    ] is True


def test_review_fork_cannot_be_elevated(tmp_path: Path) -> None:
    root = tmp_path / "git"
    repo = root / "repo"
    git_repo(repo)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator(store, "operator-7-fork-review", review=True)
    service = OperatorAuthorityService(
        store,
        environ={OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]'},
    )
    saved = service.save_workspace_set(
        set_id="set", label="Set", members=[str(repo)], updated_by="test"
    )

    with pytest.raises(OperatorAuthorityError) as denied:
        service.set_assignment(
            "operator-7-fork-review",
            profile="trusted-workbench",
            workspace_set_id=saved["set_id"],
            updated_by="test",
        )
    assert denied.value.code == "OPERATOR_AUTHORITY_REVIEW_READONLY"
    assert service.get_assignment("operator-7-fork-review")["effective_profile"] == "review-readonly"


def test_control_and_profile_ceilings_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "git"
    repo = root / "repo"
    git_repo(repo)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    register_operator(store, "operator-6")
    locked = OperatorAuthorityService(
        store,
        environ={
            OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]',
            OPERATOR_AUTHORITY_CONTROL_MODE_ENV: "read_only",
        },
    )
    with pytest.raises(OperatorAuthorityError) as blocked:
        locked.save_workspace_set(
            set_id="set", label="Set", members=[str(repo)], updated_by="test"
        )
    assert blocked.value.code == "OPERATOR_AUTHORITY_POLICY_LOCKED"

    scoped = OperatorAuthorityService(
        store,
        environ={
            OPERATOR_AUTHORITY_ROOT_CEILING_ENV: f'["{root}"]',
            OPERATOR_AUTHORITY_PROFILE_CEILING_ENV: "scoped-edit",
        },
    )
    with pytest.raises(OperatorAuthorityError) as elevated:
        scoped.set_assignment(
            "operator-6",
            profile="full-access",
            workspace_set_id=None,
            updated_by="test",
        )
    assert elevated.value.code == "OPERATOR_AUTHORITY_PROFILE_CEILING"


def test_launch_overrides_are_exact_and_review_forks_ignore_them(tmp_path: Path) -> None:
    roots = [str(tmp_path / "first"), str(tmp_path / "second")]
    overrides = operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": roots,
            "access_mode": "edit",
        }
    )

    assert overrides[0][0] == "permissions.agent-pbx-trusted-workbench"
    assert overrides[0][1]["workspace_roots"] == {root: True for root in roots}
    assert overrides[1] == ("default_permissions", "agent-pbx-trusted-workbench")
    assert operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": roots,
            "access_mode": "review_readonly",
        }
    ) == ()
    assert operator_authority_sandbox(
        {"operator_authority_profile": "full-access", "access_mode": "edit"}
    ) == "danger-full-access"
    assert operator_authority_sandbox(
        {
            "operator_authority_profile": "full-access",
            "access_mode": "review_readonly",
        }
    ) == "workspace-write"


def test_operator_authority_api_saves_set_and_assigns_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "git"
    first = root / "first"
    second = root / "second"
    git_repo(first)
    git_repo(second)
    monkeypatch.setenv(OPERATOR_AUTHORITY_ROOT_CEILING_ENV, f'["{root}"]')
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))

    with TestClient(app) as client:
        registered = client.post(
            "/v1/agents/register",
            json={
                "agent_id": "operator-8-fork-edit",
                "agent_type": "operator",
                "project": "agent-pbx-operator",
                "metadata": {
                    "agent_type": "operator",
                    "operator_role": "fork",
                    "fork_purpose": "edit",
                    "access_mode": "edit",
                },
            },
        )
        assert registered.status_code == 200
        saved = client.put(
            "/v2/operator/workspace-sets/rosie-stackops",
            json={
                "label": "Rosie StackOps",
                "members": [str(first), str(second)],
                "collections": [],
            },
        )
        assert saved.status_code == 200, saved.text
        assigned = client.put(
            "/v2/agents/operator-8-fork-edit/operator-authority",
            json={
                "profile": "trusted-workbench",
                "workspace_set_id": "rosie-stackops",
                "profile_label": "Rosie Workbench",
            },
        )
        assert assigned.status_code == 200, assigned.text
        view = assigned.json()
        assert view["effective_profile"] == "trusted-workbench"
        assert view["workspace_set"]["member_count"] == 2
        access = client.get(
            "/v2/agents/operator-8-fork-edit/workerbee/access-profile"
        ).json()
        assert access["effective_profile"] == "local-deploy"
