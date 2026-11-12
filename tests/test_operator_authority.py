from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.git_worktree import LinkedWorktreeGitAdminPlan
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


def linked_git_admin_plan(tmp_path: Path) -> LinkedWorktreeGitAdminPlan:
    worktree = tmp_path / "linked"
    common = tmp_path / "main" / ".git"
    git_dir = common / "worktrees" / "linked"
    object_dir = common / "objects"
    ref_namespace = common / "refs" / "heads" / "pbx-operator-6"
    reflog_namespace = common / "logs" / "refs" / "heads" / "pbx-operator-6"
    for directory in (
        worktree,
        git_dir,
        object_dir,
        ref_namespace,
        reflog_namespace,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    git_file = worktree / ".git"
    git_file.write_text(f"gitdir: {git_dir}\n", encoding="utf-8")
    return LinkedWorktreeGitAdminPlan(
        worktree_root=worktree,
        git_file=git_file,
        git_dir=git_dir,
        common_dir=common,
        object_dir=object_dir,
        branch_ref="refs/heads/pbx-operator-6/durable",
        ref_namespace=ref_namespace,
        reflog_namespace=reflog_namespace,
        writable_paths=(git_dir, object_dir, ref_namespace, reflog_namespace),
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
    assert metadata["operator_authority_git_common_dirs"] == [
        str(first / ".git"),
        str(second / ".git"),
    ]
    assert metadata["operator_authority_requires_restart"] is True

    activated = service.mark_activated("operator-7-fork-edit")
    assert activated["pending_activation"] is False
    activated_metadata = store.get_agent("operator-7-fork-edit")["metadata"]
    assert activated_metadata["operator_authority_requires_restart"] is False
    assert activated_metadata["operator_authority_activated_profile"] == "trusted-workbench"


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
    Path(roots[0]).mkdir()
    Path(roots[1]).mkdir()
    (Path(roots[0]) / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    (Path(roots[1]) / ".env.local").write_text(
        "SECRET=redacted\n", encoding="utf-8"
    )
    overrides = operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": roots,
            "access_mode": "edit",
        }
    )

    assert overrides[0][0] == "permissions.agent-pbx-trusted-workbench"
    assert overrides[0][1]["workspace_roots"] == {root: True for root in roots}
    filesystem = overrides[0][1]["filesystem"]
    assert filesystem[":workspace_roots"] == {".": "write", ".git": "write"}
    assert filesystem[str(Path(roots[0]) / ".env")] == "deny"
    assert filesystem[str(Path(roots[1]) / ".env.local")] == "deny"
    assert str(Path(roots[0]) / ".env.production") not in filesystem
    assert "glob_scan_max_depth" not in filesystem
    assert not any("*" in path for path in filesystem)
    assert overrides[1] == ("default_permissions", "agent-pbx-trusted-workbench")
    assert operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": roots,
            "access_mode": "review_readonly",
        }
    ) == ()
    assert operator_authority_launch_overrides(
        {
            "operator_authority_profile": "scoped-edit",
            "operator_authority_workspace_roots": roots,
            "operator_authority_git_common_dirs": [str(Path(roots[0]) / ".git")],
            "access_mode": "edit",
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


def test_launch_overrides_use_common_git_root_instead_of_narrow_admin_mounts(
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    common = main / ".git"
    separate_common = tmp_path / "separate" / ".git"
    narrow = common / "refs" / "heads" / "pbx-operator-7"

    overrides = operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": [str(linked)],
            "operator_authority_git_common_dirs": [
                str(common),
                str(separate_common),
            ],
            "operator_authority_git_admin_paths": [str(narrow)],
            "access_mode": "edit",
        }
    )

    permission = overrides[0][1]
    assert permission["workspace_roots"] == {
        str(linked): True,
        str(common): True,
        str(separate_common): True,
    }
    assert str(narrow) not in permission["filesystem"]
    assert str(narrow) not in permission["workspace_roots"]


def test_launch_overrides_preserve_nested_common_git_root_and_exactly_deduplicate(
    tmp_path: Path,
) -> None:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    common = main / ".git"
    canonical_alias = main / "nested" / ".." / ".git"

    overrides = operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": [str(main), str(linked), str(main)],
            "operator_authority_git_common_dirs": [
                str(main),
                str(common),
                str(canonical_alias),
            ],
            "access_mode": "edit",
        }
    )

    assert overrides[0][1]["workspace_roots"] == {
        str(main): True,
        str(linked): True,
        str(common): True,
    }


def test_launch_overrides_merge_exact_post_activation_git_admin_exception(
    tmp_path: Path,
) -> None:
    plan = linked_git_admin_plan(tmp_path)
    (plan.worktree_root / ".env").write_text("SECRET=redacted\n", encoding="utf-8")
    digest = "a" * 64
    overrides = operator_authority_launch_overrides(
        {
            "operator_authority_profile": "trusted-workbench",
            "operator_authority_workspace_roots": [
                str(plan.worktree_root),
                str(plan.git_dir),
                str(plan.worktree_root),
            ],
            "operator_authority_git_common_dirs": [
                str(plan.common_dir),
                str(plan.common_dir),
            ],
            "operator_workspace_set_digest": digest,
            "operator_authority_activated_workspace_set_digest": digest,
            "fork_purpose": "edit",
            "access_mode": "edit",
        },
        linked_git_admin_plan=plan,
    )

    permission = overrides[0][1]
    assert permission["workspace_roots"] == {
        str(plan.worktree_root): True,
        str(plan.common_dir): True,
    }
    filesystem = permission["filesystem"]
    assert filesystem[str(plan.git_dir)] == "write"
    assert filesystem[str(plan.worktree_root / ".env")] == "deny"
    assert str(plan.object_dir) not in filesystem
    assert str(plan.ref_namespace) not in filesystem
    assert str(plan.reflog_namespace) not in filesystem
    assert permission["network"] == {
        "enabled": True,
        "allow_local_binding": True,
        "domains": {"*": "allow"},
    }


def test_launch_overrides_reject_drifted_or_noncanonical_git_admin_exception(
    tmp_path: Path,
) -> None:
    plan = linked_git_admin_plan(tmp_path)
    digest = "b" * 64
    metadata = {
        "operator_authority_profile": "trusted-workbench",
        "operator_authority_workspace_roots": [str(plan.worktree_root)],
        "operator_authority_git_common_dirs": [str(plan.common_dir)],
        "operator_workspace_set_digest": digest,
        "operator_authority_activated_workspace_set_digest": digest,
        "fork_purpose": "edit",
        "access_mode": "edit",
    }

    with pytest.raises(OperatorAuthorityError) as drifted:
        operator_authority_launch_overrides(
            {
                **metadata,
                "operator_authority_activated_workspace_set_digest": "c" * 64,
            },
            linked_git_admin_plan=plan,
        )
    assert drifted.value.code == "OPERATOR_AUTHORITY_GIT_ADMIN_WORKSPACE_DRIFT"

    with pytest.raises(OperatorAuthorityError) as broad:
        operator_authority_launch_overrides(
            metadata,
            linked_git_admin_plan=replace(
                plan,
                git_dir=plan.common_dir,
                writable_paths=(plan.common_dir, *plan.writable_paths[1:]),
            ),
        )
    assert broad.value.code == "OPERATOR_AUTHORITY_GIT_ADMIN_TOPOLOGY"

    alias = plan.common_dir / "worktrees" / "alias"
    alias.symlink_to(plan.git_dir, target_is_directory=True)
    with pytest.raises(OperatorAuthorityError) as symlinked:
        operator_authority_launch_overrides(
            metadata,
            linked_git_admin_plan=replace(
                plan,
                git_dir=alias,
                writable_paths=(alias, *plan.writable_paths[1:]),
            ),
        )
    assert symlinked.value.code == "OPERATOR_AUTHORITY_GIT_ADMIN_NONCANONICAL"


def test_launch_overrides_never_grant_git_admin_to_review_or_nontrusted_forks(
    tmp_path: Path,
) -> None:
    plan = linked_git_admin_plan(tmp_path)
    digest = "d" * 64
    base = {
        "operator_authority_workspace_roots": [str(plan.worktree_root)],
        "operator_authority_git_common_dirs": [str(plan.common_dir)],
        "operator_workspace_set_digest": digest,
        "operator_authority_activated_workspace_set_digest": digest,
    }
    assert operator_authority_launch_overrides(
        {
            **base,
            "operator_authority_profile": "trusted-workbench",
            "fork_purpose": "review",
            "access_mode": "review_readonly",
        },
        linked_git_admin_plan=plan,
    ) == ()
    assert operator_authority_launch_overrides(
        {
            **base,
            "operator_authority_profile": "scoped-edit",
            "fork_purpose": "edit",
            "access_mode": "edit",
        },
        linked_git_admin_plan=plan,
    ) == ()


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
