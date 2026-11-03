from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

import pytest

from agent_pbx.git_worktree import (
    LINKED_EDIT_PERMISSION_PROFILE,
    LinkedWorktreeQualificationError,
    qualify_linked_worktree_git_admin,
)


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
    assert plan.config_overrides() == (
        (
            f"permissions.{LINKED_EDIT_PERMISSION_PROFILE}",
            {
                "extends": ":workspace",
                "filesystem": {str(path): "write" for path in plan.writable_paths},
            },
        ),
        ("default_permissions", LINKED_EDIT_PERMISSION_PROFILE),
    )

    (fixture.linked / "change.txt").write_text("qualified\n", encoding="utf-8")
    _git(fixture.linked, "add", "change.txt")
    _git(fixture.linked, "commit", "-m", "qualified linked edit")
    assert _git(fixture.linked, "status", "--short") == ""
    assert _git(fixture.linked, "log", "-1", "--format=%s") == "qualified linked edit"


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


def test_linked_profile_extends_validated_workspace_based_user_profile(
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
                '":workspace_roots" = "write"',
            )
        ),
        encoding="utf-8",
    )

    plan = _qualify(fixture, codex_home=codex_home)

    assert plan is not None
    assert plan.base_permission_profile == "trusted-workstation"
    profile = plan.config_overrides()[0][1]
    assert isinstance(profile, dict)
    assert profile["extends"] == "trusted-workstation"


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
