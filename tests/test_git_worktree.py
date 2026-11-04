from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from uuid import uuid4

import pytest

from agent_pbx.git_worktree import (
    LINKED_EDIT_PERMISSION_PROFILE,
    LinkedWorktreeQualificationError,
    qualify_linked_worktree_git_admin,
)
from agent_pbx.codex_launch import codex_config_override
from agent_pbx.codex.event_bridge import AppServerJsonlClient


@dataclass(frozen=True)
class LinkedFixture:
    root: Path
    main: Path
    linked: Path
    sibling: Path


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
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


def _linked_fixture(tmp_path: Path, *, branch: str = "pbx/fork") -> LinkedFixture:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    sibling = tmp_path / "sibling"
    main.mkdir()
    _git(main, "init", "-b", "main")
    (main / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(main, "add", "README.md")
    _git(main, "commit", "-m", "baseline")
    _git(main, "worktree", "add", "-b", branch, str(linked))
    _git(main, "worktree", "add", "-b", "other/sibling", str(sibling))
    return LinkedFixture(tmp_path, main, linked, sibling)


def _qualify(fixture: LinkedFixture, **kwargs: object):
    return qualify_linked_worktree_git_admin(
        fixture.linked,
        approved_roots=(fixture.root,),
        fork_purpose="edit",
        access_mode="edit",
        **kwargs,
    )


def test_linked_edit_plan_is_minimal_and_supports_stage_and_commit(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    plan = _qualify(fixture)

    assert plan is not None
    common = (fixture.main / ".git").resolve()
    sibling_gitdir = Path(_git(fixture.sibling, "rev-parse", "--absolute-git-dir")).resolve()
    assert plan.permission_profile == LINKED_EDIT_PERMISSION_PROFILE
    assert plan.writable_paths == (
        plan.git_dir,
        common / "objects",
        common / "refs" / "heads" / "pbx",
        common / "logs" / "refs" / "heads" / "pbx",
    )
    assert common not in plan.writable_paths
    assert common / "worktrees" not in plan.writable_paths
    assert sibling_gitdir not in plan.writable_paths
    assert not any(sibling_gitdir.is_relative_to(path) for path in plan.writable_paths)
    profile_key, profile = plan.config_overrides()[0]
    assert profile_key == f"permissions.{LINKED_EDIT_PERMISSION_PROFILE}"
    assert profile["extends"] == ":workspace"
    assert profile["filesystem"] == {
        str(path): "write" for path in plan.writable_paths
    }
    assert "workspace_roots" not in profile
    assert plan.config_overrides()[1] == (
        "default_permissions",
        LINKED_EDIT_PERMISSION_PROFILE,
    )
    assert plan.public_dict()["grant_scope"] == "sandbox_filesystem_only"
    assert plan.public_dict()["additional_workspace_roots"] == []

    (fixture.linked / "change.txt").write_text("qualified\n", encoding="utf-8")
    _git(fixture.linked, "add", "change.txt")
    _git(fixture.linked, "commit", "-m", "qualified linked edit")
    assert _git(fixture.linked, "status", "--short") == ""
    assert _git(fixture.linked, "log", "-1", "--format=%s") == "qualified linked edit"


@pytest.mark.skipif(
    os.getenv("AGENT_PBX_RUN_CODEX_SANDBOX_CANARY") != "1"
    or shutil.which("codex") is None,
    reason="requires an explicitly enabled local Codex sandbox canary",
)
def test_codex_sandbox_profile_writes_git_admin_without_bootstrap_pollution(
    tmp_path: Path,
) -> None:
    """Exercise the generated profile without contacting a model provider."""

    fixture = _linked_fixture(tmp_path)
    plan = _qualify(fixture)
    assert plan is not None
    (fixture.linked / "canary.txt").write_text("sandbox canary\n", encoding="utf-8")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    profile_key, profile_value = plan.config_overrides()[0]
    command = [
        shutil.which("codex") or "codex",
        "sandbox",
        "-C",
        str(fixture.linked),
        "-c",
        codex_config_override(profile_key, profile_value),
        "-P",
        plan.permission_profile,
        "--",
        "sh",
        "-c",
        "git add canary.txt && git commit -m 'sandbox canary'",
    ]
    subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CODEX_HOME": str(codex_home),
            "GIT_AUTHOR_NAME": "Agent PBX Canary",
            "GIT_AUTHOR_EMAIL": "agent-pbx-canary@example.invalid",
            "GIT_COMMITTER_NAME": "Agent PBX Canary",
            "GIT_COMMITTER_EMAIL": "agent-pbx-canary@example.invalid",
        },
    )

    assert _git(fixture.linked, "status", "--short") == ""
    assert _git(fixture.linked, "log", "-1", "--format=%s") == "sandbox canary"
    bootstrap_names = (".git", ".agents", ".codex", ".aws")
    assert [
        child
        for root in plan.writable_paths
        for name in bootstrap_names
        if (child := root / name).exists()
    ] == []


@pytest.mark.skipif(
    os.getenv("AGENT_PBX_RUN_CODEX_SANDBOX_CANARY") != "1"
    or shutil.which("codex") is None,
    reason="requires an explicitly enabled local Codex startup canary",
)
@pytest.mark.asyncio
async def test_codex_legacy_writable_roots_comparison_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare the rejected split profile with legacy writable roots on 0.166."""

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            (
                'default_permissions = "pbx-workstation-dev"',
                "",
                "[permissions.pbx-workstation-dev]",
                'extends = ":workspace"',
                "",
                "[permissions.pbx-workstation-dev.filesystem]",
                "glob_scan_max_depth = 4",
                '":workspace_roots" = { "." = "write", ".git" = "write", "**/.env" = "deny", "**/.env.*" = "deny" }',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    current_root = tmp_path / "current"
    current_root.mkdir()
    fixture = _linked_fixture(current_root)
    plan = _qualify(fixture)
    assert plan is not None
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    unsafe_profile = {
        "extends": "pbx-workstation-dev",
        "filesystem": {str(path): "write" for path in plan.writable_paths},
    }
    command = [shutil.which("codex") or "codex", "app-server", "--stdio"]
    current_overrides = (
        (
            f"permissions.{LINKED_EDIT_PERMISSION_PROFILE}",
            unsafe_profile,
        ),
        ("default_permissions", LINKED_EDIT_PERMISSION_PROFILE),
    )
    for key, value in current_overrides:
        command.extend(("-c", codex_config_override(key, value)))
    client = AppServerJsonlClient(command, request_timeout=15.0)
    try:
        await client.start()
        current_result = await client.request(
            "thread/start",
            {
                "cwd": str(fixture.linked),
                "ephemeral": True,
            },
        )
        assert current_result.get("thread") or current_result.get("id")
    finally:
        await client.close()

    current_sandbox = [
        shutil.which("codex") or "codex",
        "sandbox",
        "-C",
        str(fixture.linked),
    ]
    for key, value in current_overrides:
        current_sandbox.extend(("-c", codex_config_override(key, value)))
    current_sandbox.extend(("-P", LINKED_EDIT_PERMISSION_PROFILE, "--", "true"))
    current_sandbox_result = subprocess.run(
        current_sandbox,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "CODEX_HOME": str(codex_home)},
    )
    assert current_sandbox_result.returncode == 0, current_sandbox_result.stderr

    bootstrap_names = (".git", ".agents", ".codex", ".aws")
    current_polluted = [
        child
        for root in plan.writable_paths
        for name in bootstrap_names
        if (child := root / name).exists()
    ]
    assert len(current_polluted) == 16
    assert current_result.get("activePermissionProfile") == {
        "id": LINKED_EDIT_PERMISSION_PROFILE,
        "extends": "pbx-workstation-dev",
    }

    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy = _linked_fixture(legacy_root)
    legacy_plan = _qualify(legacy)
    assert legacy_plan is not None
    (legacy.linked / ".env").write_text("CANARY_SECRET=not-for-tools\n", encoding="utf-8")
    (legacy.linked / "legacy-change.txt").write_text("legacy\n", encoding="utf-8")
    unrelated_target = Path.home() / f".agent-pbx-sandbox-canary-{uuid4().hex}"
    assert not unrelated_target.exists()
    legacy_overrides = (
        ("sandbox_mode", "workspace-write"),
        (
            "sandbox_workspace_write.writable_roots",
            [str(path) for path in legacy_plan.writable_paths],
        ),
        ("sandbox_workspace_write.exclude_slash_tmp", True),
        ("sandbox_workspace_write.exclude_tmpdir_env_var", True),
    )
    legacy_command = [shutil.which("codex") or "codex", "app-server", "--stdio"]
    for key, value in legacy_overrides:
        legacy_command.extend(("-c", codex_config_override(key, value)))
    legacy_client = AppServerJsonlClient(legacy_command, request_timeout=15.0)
    try:
        await legacy_client.start()
        legacy_config = await legacy_client.request(
            "config/read",
            {"cwd": str(legacy.linked), "includeLayers": False},
        )
        legacy_result = await legacy_client.request(
            "thread/start",
            {"cwd": str(legacy.linked), "ephemeral": True},
        )
    finally:
        await legacy_client.close()

    environment = {
        **os.environ,
        "CODEX_HOME": str(codex_home),
        "GIT_AUTHOR_NAME": "Agent PBX Canary",
        "GIT_AUTHOR_EMAIL": "agent-pbx-canary@example.invalid",
        "GIT_COMMITTER_NAME": "Agent PBX Canary",
        "GIT_COMMITTER_EMAIL": "agent-pbx-canary@example.invalid",
    }

    def legacy_sandbox(*inner: str) -> subprocess.CompletedProcess[str]:
        argv = [
            shutil.which("codex") or "codex",
            "sandbox",
            "-C",
            str(legacy.linked),
            "-P",
            "pbx-workstation-dev",
        ]
        for key, value in legacy_overrides:
            argv.extend(("-c", codex_config_override(key, value)))
        argv.extend(("--", *inner))
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )

    git_result = legacy_sandbox(
        "sh",
        "-c",
        "git add legacy-change.txt && git commit -m 'legacy canary'",
    )
    secret_result = legacy_sandbox("cat", ".env")
    try:
        unrelated_result = legacy_sandbox("touch", str(unrelated_target))
        unrelated_created = unrelated_target.exists()
    finally:
        unrelated_target.unlink(missing_ok=True)
    legacy_polluted = [
        child
        for root in legacy_plan.writable_paths
        for name in (".git", ".agents", ".codex", ".aws")
        if (child := root / name).exists()
    ]
    active = legacy_result.get("activePermissionProfile") or {}
    effective_config = legacy_config.get("config") or {}
    assert effective_config.get("sandbox_mode") == "workspace-write"
    assert (effective_config.get("sandbox_workspace_write") or {}).get(
        "writable_roots"
    ) == [str(path) for path in legacy_plan.writable_paths]
    assert (effective_config.get("sandbox_workspace_write") or {}).get(
        "exclude_slash_tmp"
    ) is True
    assert (effective_config.get("sandbox_workspace_write") or {}).get(
        "exclude_tmpdir_env_var"
    ) is True
    gates = {
        "base_profile_precedence_preserved": active.get("id")
        == "pbx-workstation-dev",
        "git_admin_operations_work": git_result.returncode == 0,
        "no_bootstrap_pollution": not legacy_polluted,
        "secret_deny_preserved": secret_result.returncode != 0,
        "unrelated_write_denied": (
            unrelated_result.returncode != 0 and not unrelated_created
        ),
    }
    assert gates == {
        "base_profile_precedence_preserved": False,
        "git_admin_operations_work": False,
        "no_bootstrap_pollution": True,
        "secret_deny_preserved": True,
        "unrelated_write_denied": True,
    }, {
        "gates": gates,
        "active_permission_profile": active,
        "current_pollution": current_polluted,
        "legacy_pollution": legacy_polluted,
        "git_stderr": git_result.stderr,
        "secret_stdout": secret_result.stdout,
        "unrelated_stderr": unrelated_result.stderr,
    }


@pytest.mark.parametrize(
    ("purpose", "access_mode"),
    (("review", "review_readonly"), ("edit", "review_readonly")),
)
def test_non_edit_roles_never_inspect_or_grant(
    tmp_path: Path,
    purpose: str,
    access_mode: str,
) -> None:
    work_root = tmp_path / "review"
    work_root.mkdir()
    (work_root / ".git").write_text("malformed and intentionally unreadable as Git\n")

    plan = qualify_linked_worktree_git_admin(
        work_root,
        approved_roots=(tmp_path,),
        fork_purpose=purpose,
        access_mode=access_mode,
    )

    assert plan is None


def test_standalone_and_non_git_projects_preserve_existing_behavior(tmp_path: Path) -> None:
    standalone = tmp_path / "standalone"
    standalone.mkdir()
    _git(standalone, "init", "-b", "main")
    plain = tmp_path / "plain"
    plain.mkdir()

    for path in (standalone, plain):
        assert qualify_linked_worktree_git_admin(
            path,
            approved_roots=(tmp_path,),
            fork_purpose="edit",
            access_mode="edit",
        ) is None


def test_bare_repository_is_rejected(tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    _git(tmp_path, "init", "--bare", str(bare))

    with pytest.raises(LinkedWorktreeQualificationError, match="bare repositories"):
        qualify_linked_worktree_git_admin(
            bare,
            approved_roots=(tmp_path,),
            fork_purpose="edit",
            access_mode="edit",
        )


def test_malformed_git_indirection_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    (fixture.linked / ".git").write_text("not-a-gitdir\n", encoding="utf-8")

    with pytest.raises(LinkedWorktreeQualificationError, match="malformed .git"):
        _qualify(fixture)


def test_gitdir_escape_is_rejected(tmp_path: Path) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    fixture = _linked_fixture(approved)
    escaped = tmp_path / "escaped-gitdir"
    escaped.mkdir()
    (fixture.linked / ".git").write_text(f"gitdir: {escaped}\n", encoding="utf-8")

    with pytest.raises(LinkedWorktreeQualificationError, match="escaped approved root"):
        qualify_linked_worktree_git_admin(
            fixture.linked,
            approved_roots=(approved,),
            fork_purpose="edit",
            access_mode="edit",
        )


def test_raw_gitdir_symlink_resolving_inside_approved_root_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _linked_fixture(tmp_path)
    real_gitdir = Path(_git(fixture.linked, "rev-parse", "--absolute-git-dir"))
    alias = tmp_path / "gitdir-alias"
    alias.symlink_to(real_gitdir, target_is_directory=True)
    (fixture.linked / ".git").write_text(f"gitdir: {alias}\n", encoding="utf-8")

    with pytest.raises(LinkedWorktreeQualificationError, match="symlinked Git"):
        _qualify(fixture)


def test_raw_gitdir_symlink_resolving_outside_approved_root_is_rejected(
    tmp_path: Path,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir()
    fixture = _linked_fixture(approved)
    outside = tmp_path / "outside-gitdir"
    outside.mkdir()
    alias = approved / "gitdir-alias"
    alias.symlink_to(outside, target_is_directory=True)
    (fixture.linked / ".git").write_text(f"gitdir: {alias}\n", encoding="utf-8")

    with pytest.raises(LinkedWorktreeQualificationError, match="symlinked Git"):
        qualify_linked_worktree_git_admin(
            fixture.linked,
            approved_roots=(approved,),
            fork_purpose="edit",
            access_mode="edit",
        )


def test_sibling_admin_gitdir_backpointer_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _linked_fixture(tmp_path)
    linked_gitdir = Path(_git(fixture.linked, "rev-parse", "--absolute-git-dir"))
    (linked_gitdir / "gitdir").write_text(
        f"{fixture.sibling / '.git'}\n",
        encoding="utf-8",
    )

    with pytest.raises(LinkedWorktreeQualificationError, match="backpointer"):
        _qualify(fixture)


def test_unapproved_root_is_rejected(tmp_path: Path) -> None:
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    fixture = _linked_fixture(fixture_root)
    approved = tmp_path / "approved"
    approved.mkdir()

    with pytest.raises(LinkedWorktreeQualificationError, match="outside approved"):
        qualify_linked_worktree_git_admin(
            fixture.linked,
            approved_roots=(approved,),
            fork_purpose="edit",
            access_mode="edit",
        )


def test_owner_mismatch_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)

    with pytest.raises(LinkedWorktreeQualificationError, match="owner mismatch"):
        _qualify(fixture, expected_uid=os.geteuid() + 1)


def test_critical_branch_ref_owner_mismatch_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = _linked_fixture(tmp_path)
    branch_ref = fixture.main / ".git" / "refs" / "heads" / "pbx" / "fork"
    real_stat = Path.stat

    def controlled_stat(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> os.stat_result:
        result = real_stat(path, *args, **kwargs)
        if path == branch_ref and kwargs.get("follow_symlinks") is False:
            values = list(result)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", controlled_stat)

    with pytest.raises(
        LinkedWorktreeQualificationError,
        match=rf"owner mismatch.*{branch_ref}",
    ):
        _qualify(fixture)


def test_detached_head_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    _git(fixture.linked, "checkout", "--detach")

    with pytest.raises(LinkedWorktreeQualificationError, match="attached local branch"):
        _qualify(fixture)


def test_top_level_branch_namespace_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path, branch="feature")

    with pytest.raises(LinkedWorktreeQualificationError, match="dedicated namespace"):
        _qualify(fixture)


def test_shared_branch_namespace_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    _git(fixture.main, "branch", "pbx/another")

    with pytest.raises(LinkedWorktreeQualificationError, match="not dedicated"):
        _qualify(fixture)


def test_symlinked_git_admin_path_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    object_dir = fixture.main / ".git" / "objects"
    moved = fixture.main / ".git" / "objects-real"
    object_dir.rename(moved)
    object_dir.symlink_to(moved, target_is_directory=True)

    with pytest.raises(LinkedWorktreeQualificationError, match="object database|symlinked"):
        _qualify(fixture)


def test_ambiguous_commondir_is_rejected(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    git_dir = Path(_git(fixture.linked, "rev-parse", "--absolute-git-dir"))
    (git_dir / "commondir").write_text(".\n", encoding="utf-8")

    with pytest.raises(LinkedWorktreeQualificationError, match="qualify|commondir|common"):
        _qualify(fixture)


def test_legacy_sandbox_mode_blocks_permission_profile(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        'sandbox_mode = "workspace-write"\n',
        encoding="utf-8",
    )

    with pytest.raises(LinkedWorktreeQualificationError, match="legacy sandbox_mode"):
        _qualify(fixture, codex_home=codex_home)


def test_linked_profile_materializes_safe_workspace_based_user_profile(
    tmp_path: Path,
) -> None:
    fixture = _linked_fixture(tmp_path)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            (
                'default_permissions = "trusted-workstation"',
                "",
                "[permissions.trusted-workstation]",
                'extends = ":workspace"',
                "",
                "[permissions.trusted-workstation.filesystem]",
                '":workspace_roots" = { "." = "write", ".git" = "write" }',
            )
        ),
        encoding="utf-8",
    )

    plan = _qualify(fixture, codex_home=codex_home)

    assert plan is not None
    assert plan.base_permission_profile == "trusted-workstation"
    profile = plan.config_overrides()[0][1]
    assert isinstance(profile, dict)
    assert profile["extends"] == ":workspace"
    assert profile["filesystem"] == {
        str(path): "write" for path in plan.writable_paths
    }
    assert "workspace_roots" not in profile


def test_base_profile_with_secret_deny_rules_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _linked_fixture(tmp_path)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            (
                'default_permissions = "trusted-workstation"',
                "",
                "[permissions.trusted-workstation]",
                'extends = ":workspace"',
                "",
                "[permissions.trusted-workstation.filesystem]",
                '":workspace_roots" = { "." = "write", ".git" = "write", "**/.env" = "deny" }',
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LinkedWorktreeQualificationError,
        match="split policy.*protected metadata mount targets",
    ):
        _qualify(fixture, codex_home=codex_home)


def test_base_profile_with_existing_git_admin_write_is_rejected(
    tmp_path: Path,
) -> None:
    fixture = _linked_fixture(tmp_path)
    common = fixture.main / ".git"
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            (
                'default_permissions = "unsafe-git"',
                "",
                "[permissions.unsafe-git]",
                'extends = ":workspace"',
                "",
                "[permissions.unsafe-git.filesystem]",
                f'"{common}" = "write"',
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LinkedWorktreeQualificationError,
        match="already grants Git administration write access",
    ):
        _qualify(fixture, codex_home=codex_home)
