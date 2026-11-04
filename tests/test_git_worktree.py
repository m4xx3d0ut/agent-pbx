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


def test_linked_edit_plan_preserves_direct_minimal_launch_grants(
    tmp_path: Path,
) -> None:
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
    public = plan.public_dict()
    assert public["write_strategy"] == "direct_permission_profile"
    assert public["grant_scope"] == "sandbox_filesystem_only"
    assert public["additional_workspace_roots"] == []
    assert public["active_write_grants"] == [
        str(path) for path in plan.writable_paths
    ]
    assert public["writable_paths"] == [str(path) for path in plan.writable_paths]
    assert public["qualified_git_admin_paths"] == [
        str(path) for path in plan.writable_paths
    ]

    (fixture.linked / "change.txt").write_text("qualified\n", encoding="utf-8")
    _git(fixture.linked, "add", "change.txt")
    _git(fixture.linked, "commit", "-m", "qualified linked edit")
    assert _git(fixture.linked, "status", "--short") == ""
    assert _git(fixture.linked, "log", "-1", "--format=%s") == "qualified linked edit"


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
        current_exec = await client.request(
            "command/exec",
            {
                "command": ["true"],
                "cwd": str(fixture.linked),
                "permissionProfile": LINKED_EDIT_PERMISSION_PROFILE,
            },
        )
    finally:
        await client.close()
    assert current_exec.get("exitCode") == 0, current_exec

    bootstrap_names = (".git", ".agents", ".codex", ".aws")
    current_polluted = [
        child
        for root in plan.writable_paths
        for name in bootstrap_names
        if (child := root / name).exists()
    ]
    assert all(
        child.name in bootstrap_names
        and any(child.parent == root for root in plan.writable_paths)
        for child in current_polluted
    )
    assert current_result.get("activePermissionProfile") == {
        "id": LINKED_EDIT_PERMISSION_PROFILE,
        "extends": "pbx-workstation-dev",
    }

    flat_root = tmp_path / "flat"
    flat_root.mkdir()
    flat = _linked_fixture(flat_root)
    flat_plan = _qualify(flat)
    assert flat_plan is not None
    (flat.linked / ".env").write_text(
        "WORKSPACE_SECRET=not-for-tools\n",
        encoding="utf-8",
    )
    (flat.linked / "flat-change.txt").write_text("flat\n", encoding="utf-8")
    absolute_secret = tmp_path / "absolute-secret.env"
    absolute_secret.write_text("ABSOLUTE_SECRET=not-for-tools\n", encoding="utf-8")
    flat_profile = {
        "extends": ":workspace",
        "filesystem": {
            "glob_scan_max_depth": 4,
            ":workspace_roots": {
                ".": "write",
                ".git": "write",
                "**/.env": "deny",
                "**/.env.*": "deny",
            },
            str(absolute_secret): "deny",
            **{str(path): "write" for path in flat_plan.writable_paths},
        },
    }
    flat_overrides = (
        (f"permissions.{LINKED_EDIT_PERMISSION_PROFILE}", flat_profile),
        ("default_permissions", LINKED_EDIT_PERMISSION_PROFILE),
    )
    flat_command = [shutil.which("codex") or "codex", "app-server", "--stdio"]
    for key, value in flat_overrides:
        flat_command.extend(("-c", codex_config_override(key, value)))
    flat_client = AppServerJsonlClient(flat_command, request_timeout=15.0)
    try:
        await flat_client.start()
        flat_config = await flat_client.request(
            "config/read",
            {"cwd": str(flat.linked), "includeLayers": False},
        )
        flat_result = await flat_client.request(
            "thread/start",
            {"cwd": str(flat.linked), "ephemeral": True},
        )
        flat_environment = {
            "GIT_AUTHOR_NAME": "Agent PBX Canary",
            "GIT_AUTHOR_EMAIL": "agent-pbx-canary@example.invalid",
            "GIT_COMMITTER_NAME": "Agent PBX Canary",
            "GIT_COMMITTER_EMAIL": "agent-pbx-canary@example.invalid",
        }

        async def flat_exec(*command: str) -> dict[str, object]:
            return await flat_client.request(
                "command/exec",
                {
                    "command": list(command),
                    "cwd": str(flat.linked),
                    "env": flat_environment,
                    "permissionProfile": LINKED_EDIT_PERMISSION_PROFILE,
                },
            )

        flat_stage = await flat_exec("git", "add", "flat-change.txt")
        flat_commit = await flat_exec(
            "git",
            "commit",
            "-m",
            "flat canary",
        )
        flat_workspace_secret = await flat_exec("cat", ".env")
        flat_absolute_secret = await flat_exec("cat", str(absolute_secret))
        flat_unrelated = flat.root / "unrelated-write"
        flat_unrelated_result = await flat_exec("touch", str(flat_unrelated))
        common = (flat.main / ".git").resolve()
        sibling_gitdir = Path(
            _git(flat.sibling, "rev-parse", "--absolute-git-dir")
        ).resolve()
        flat_shared_ref = await flat_exec(
            "git",
            "update-ref",
            "refs/heads/shared-canary",
            "HEAD",
        )
        flat_sibling = await flat_exec(
            "touch",
            str(sibling_gitdir / "forbidden"),
        )
        flat_hook = await flat_exec(
            "touch",
            str(common / "hooks" / "forbidden"),
        )
        flat_config_write = await flat_exec(
            "git",
            "config",
            "--local",
            "agent-pbx.forbidden",
            "true",
        )
        flat_common = await flat_exec("touch", str(common / "forbidden"))
    finally:
        await flat_client.close()
    flat_polluted = [
        child
        for root in flat_plan.writable_paths
        for name in bootstrap_names
        if (child := root / name).exists()
    ]
    assert (flat_result.get("activePermissionProfile") or {}).get("id") == (
        LINKED_EDIT_PERMISSION_PROFILE
    )
    assert (flat_config.get("config") or {}).get("default_permissions") == (
        LINKED_EDIT_PERMISSION_PROFILE
    )
    flat_gates = {
        "index_write_works": flat_stage.get("exitCode") == 0,
        "object_ref_reflog_writes_work": flat_commit.get("exitCode") == 0,
        "workspace_secret_denied": flat_workspace_secret.get("exitCode") != 0,
        "absolute_secret_denied": flat_absolute_secret.get("exitCode") != 0,
        "unrelated_write_denied": (
            flat_unrelated_result.get("exitCode") != 0 and not flat_unrelated.exists()
        ),
        "shared_ref_denied": (
            flat_shared_ref.get("exitCode") != 0
            and not (common / "refs" / "heads" / "shared-canary").exists()
        ),
        "sibling_admin_denied": (
            flat_sibling.get("exitCode") != 0
            and not (sibling_gitdir / "forbidden").exists()
        ),
        "hooks_denied": (
            flat_hook.get("exitCode") != 0
            and not (common / "hooks" / "forbidden").exists()
        ),
        "config_denied": flat_config_write.get("exitCode") != 0,
        "broad_common_git_denied": (
            flat_common.get("exitCode") != 0
            and not (common / "forbidden").exists()
        ),
    }
    assert flat_gates == {
        "index_write_works": True,
        "object_ref_reflog_writes_work": True,
        "workspace_secret_denied": True,
        "absolute_secret_denied": True,
        "unrelated_write_denied": False,
        "shared_ref_denied": False,
        "sibling_admin_denied": False,
        "hooks_denied": False,
        "config_denied": False,
        "broad_common_git_denied": False,
    }, {
        "gates": flat_gates,
        "active_permission_profile": flat_result.get("activePermissionProfile"),
        "pollution": flat_polluted,
        "stage": flat_stage,
        "commit": flat_commit,
        "workspace_secret": flat_workspace_secret,
        "absolute_secret": flat_absolute_secret,
        "shared_ref": flat_shared_ref,
    }
    # Codex may create and later clean these bootstrap mount targets. That
    # timing is not the safety decision for this rejected representation; its
    # deterministic authorization broadening above is. If artifacts remain,
    # they must still be limited to the known bootstrap names and exact roots.
    assert all(
        child.name in bootstrap_names
        and any(child.parent == root for root in flat_plan.writable_paths)
        for child in flat_polluted
    )

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
        "secret_deny_preserved": secret_result.returncode != 0,
        "unrelated_write_denied": (
            unrelated_result.returncode != 0 and not unrelated_created
        ),
    }
    assert gates == {
        "base_profile_precedence_preserved": False,
        "git_admin_operations_work": False,
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
    assert all(
        child.name in bootstrap_names
        and any(child.parent == root for root in legacy_plan.writable_paths)
        for child in legacy_polluted
    )


@pytest.mark.skipif(
    os.getenv("AGENT_PBX_RUN_CODEX_SANDBOX_CANARY") != "1"
    or shutil.which("codex") is None,
    reason="requires an explicitly enabled local Codex startup canary",
)
@pytest.mark.asyncio
async def test_codex_absolute_workspace_deny_translation_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject absolute workspace translation unless every policy gate holds."""

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    fixture = _linked_fixture(fixture_root)
    plan = _qualify(fixture)
    assert plan is not None
    nested = fixture.linked / "nested"
    nested.mkdir()
    root_secret = fixture.linked / ".env"
    nested_secret = nested / ".env.production"
    root_secret.write_text("ROOT_SECRET=denied\n", encoding="utf-8")
    nested_secret.write_text("NESTED_SECRET=denied\n", encoding="utf-8")
    secret_link = fixture.linked / "secret-link"
    secret_link.symlink_to(nested_secret)
    absolute_secret = tmp_path / "absolute-secret.env"
    absolute_secret.write_text("ABSOLUTE_SECRET=denied\n", encoding="utf-8")
    change = fixture.linked / "translated-change.txt"
    change.write_text("translated\n", encoding="utf-8")

    workspace = str(fixture.linked.resolve())
    translated_denies = {
        f"{workspace}/.env": "deny",
        f"{workspace}/**/.env": "deny",
        f"{workspace}/.env.*": "deny",
        f"{workspace}/**/.env.*": "deny",
        f"{workspace}/*.env": "deny",
        f"{workspace}/**/*.env": "deny",
    }
    profile = {
        "extends": ":workspace",
        "filesystem": {
            "glob_scan_max_depth": 4,
            **translated_denies,
            str(absolute_secret): "deny",
            **{str(path): "write" for path in plan.writable_paths},
        },
    }
    overrides = (
        (f"permissions.{LINKED_EDIT_PERMISSION_PROFILE}", profile),
        ("default_permissions", LINKED_EDIT_PERMISSION_PROFILE),
    )
    command = [shutil.which("codex") or "codex", "app-server", "--stdio"]
    for key, value in overrides:
        command.extend(("-c", codex_config_override(key, value)))
    client = AppServerJsonlClient(command, request_timeout=15.0)
    try:
        await client.start()
        config = await client.request(
            "config/read",
            {"cwd": workspace, "includeLayers": False},
        )
        thread = await client.request(
            "thread/start",
            {"cwd": workspace, "ephemeral": True},
        )
        git_environment = {
            "GIT_AUTHOR_NAME": "Agent PBX Canary",
            "GIT_AUTHOR_EMAIL": "agent-pbx-canary@example.invalid",
            "GIT_COMMITTER_NAME": "Agent PBX Canary",
            "GIT_COMMITTER_EMAIL": "agent-pbx-canary@example.invalid",
        }

        async def execute(*argv: str) -> dict[str, object]:
            return await client.request(
                "command/exec",
                {
                    "command": list(argv),
                    "cwd": workspace,
                    "env": git_environment,
                    "permissionProfile": LINKED_EDIT_PERMISSION_PROFILE,
                },
            )

        stage = await execute("git", "add", change.name)
        commit = await execute("git", "commit", "-m", "translated canary")
        root_secret_read = await execute("cat", str(root_secret))
        nested_secret_read = await execute("cat", str(nested_secret))
        traversal_read = await execute("cat", "nested/../.env")
        symlink_read = await execute("cat", str(secret_link))
        absolute_secret_read = await execute("cat", str(absolute_secret))
        unrelated = tmp_path / "unrelated-write"
        unrelated_write = await execute("touch", str(unrelated))
        common = fixture.main / ".git"
        sibling_gitdir = Path(
            _git(fixture.sibling, "rev-parse", "--absolute-git-dir")
        ).resolve()
        shared_ref = await execute(
            "git",
            "update-ref",
            "refs/heads/shared-canary",
            "HEAD",
        )
        sibling_write = await execute(
            "touch",
            str(sibling_gitdir / "forbidden"),
        )
        hook_write = await execute(
            "touch",
            str(common / "hooks" / "forbidden"),
        )
        config_write = await execute(
            "git",
            "config",
            "--local",
            "agent-pbx.forbidden",
            "true",
        )
        broad_common_write = await execute(
            "touch",
            str(common / "forbidden"),
        )
    finally:
        await client.close()

    bootstrap_names = (".git", ".agents", ".codex", ".aws")
    polluted = [
        child
        for root in plan.writable_paths
        for name in bootstrap_names
        if (child := root / name).exists()
    ]
    effective = config.get("config") or {}
    active = thread.get("activePermissionProfile") or {}
    gates = {
        "profile_readback": (
            effective.get("default_permissions") == LINKED_EDIT_PERMISSION_PROFILE
            and active.get("id") == LINKED_EDIT_PERMISSION_PROFILE
        ),
        "no_workspace_root_expansion": "workspace_roots" not in profile,
        "index_write_works": stage.get("exitCode") == 0,
        "object_ref_reflog_writes_work": commit.get("exitCode") == 0,
        "root_secret_denied": root_secret_read.get("exitCode") != 0,
        "nested_secret_denied": nested_secret_read.get("exitCode") != 0,
        "traversal_denied": traversal_read.get("exitCode") != 0,
        "symlink_denied": symlink_read.get("exitCode") != 0,
        "absolute_secret_denied": absolute_secret_read.get("exitCode") != 0,
        "unrelated_write_denied": (
            unrelated_write.get("exitCode") != 0 and not unrelated.exists()
        ),
        "shared_ref_denied": (
            shared_ref.get("exitCode") != 0
            and not (common / "refs" / "heads" / "shared-canary").exists()
        ),
        "sibling_admin_denied": (
            sibling_write.get("exitCode") != 0
            and not (sibling_gitdir / "forbidden").exists()
        ),
        "hooks_denied": (
            hook_write.get("exitCode") != 0
            and not (common / "hooks" / "forbidden").exists()
        ),
        "config_denied": config_write.get("exitCode") != 0,
        "broad_common_git_denied": (
            broad_common_write.get("exitCode") != 0
            and not (common / "forbidden").exists()
        ),
    }
    assert gates == {
        "profile_readback": True,
        "no_workspace_root_expansion": True,
        "index_write_works": True,
        "object_ref_reflog_writes_work": True,
        "root_secret_denied": True,
        "nested_secret_denied": True,
        "traversal_denied": True,
        "symlink_denied": True,
        "absolute_secret_denied": True,
        "unrelated_write_denied": False,
        "shared_ref_denied": False,
        "sibling_admin_denied": False,
        "hooks_denied": False,
        "config_denied": False,
        "broad_common_git_denied": False,
    }, {
        "gates": gates,
        "active_permission_profile": active,
        "pollution": polluted,
        "stage": stage,
        "commit": commit,
    }
    assert all(
        child.name in bootstrap_names
        and any(child.parent == root for root in plan.writable_paths)
        for child in polluted
    )


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
    assert plan.public_dict()["write_strategy"] == "direct_permission_profile"


def test_base_profile_with_secret_deny_rules_qualifies_broker_without_grants(
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

    plan = _qualify(fixture, codex_home=codex_home)

    assert plan is not None
    assert plan.base_permission_profile == "trusted-workstation"
    assert plan.config_overrides() == ()
    assert plan.public_dict()["write_strategy"] == "tui_root_commit_broker"
    assert plan.public_dict()["active_write_grants"] == []


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


def test_qualification_ignores_host_git_environment_and_rejects_config_includes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _linked_fixture(tmp_path)
    poison = tmp_path / "poison"
    poison.mkdir()
    monkeypatch.setenv("GIT_DIR", str(poison))
    monkeypatch.setenv("GIT_WORK_TREE", str(poison))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(poison / "global"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(poison / "objects"))
    assert _qualify(fixture) is not None

    common = fixture.main / ".git"
    external = tmp_path / "external.gitconfig"
    external.write_text("[core]\n\tbare = true\n", encoding="utf-8")
    with (common / "config").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[include]\n\tpath = {external}\n")
    external.chmod(0)
    try:
        with pytest.raises(
            LinkedWorktreeQualificationError,
            match="include/includeIf",
        ):
            _qualify(fixture)
    finally:
        external.chmod(0o600)


def test_qualification_rejects_worktree_include_and_promisor_config(
    tmp_path: Path,
) -> None:
    include_case = tmp_path / "include-case"
    include_case.mkdir()
    fixture = _linked_fixture(include_case)
    git_dir = Path(_git(fixture.linked, "rev-parse", "--absolute-git-dir"))
    (git_dir / "config.worktree").write_text(
        "[includeIf \"gitdir:/**\"]\n\tpath = /tmp/forbidden\n",
        encoding="utf-8",
    )
    with pytest.raises(
        LinkedWorktreeQualificationError,
        match="include/includeIf",
    ):
        _qualify(fixture)

    promisor_case = tmp_path / "promisor-case"
    promisor_case.mkdir()
    promisor = _linked_fixture(promisor_case)
    with (promisor.main / ".git" / "config").open("a", encoding="utf-8") as handle:
        handle.write(
            "\n[extensions]\n\tpartialClone = origin\n"
            "[remote \"origin\"]\n\tpromisor = true\n"
        )
    with pytest.raises(
        LinkedWorktreeQualificationError,
        match="partial-clone/promisor",
    ):
        _qualify(promisor)


def test_qualification_rejects_object_alternates(tmp_path: Path) -> None:
    fixture = _linked_fixture(tmp_path)
    info = fixture.main / ".git" / "objects" / "info"
    info.mkdir(exist_ok=True)
    (info / "alternates").write_text("/tmp/forbidden-objects\n", encoding="utf-8")

    with pytest.raises(
        LinkedWorktreeQualificationError,
        match="object alternates",
    ):
        _qualify(fixture)
