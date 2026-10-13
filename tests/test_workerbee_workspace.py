from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_workspace import (
    WorkerBeeWorkspaceError,
    WorkerBeeWorkspaceService,
)


def git_repo(root: Path) -> str:
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def runtime(tmp_path: Path, backend: str):
    registry = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    return registry.observe(
        {
            "backend": backend,
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "K1S WorkerBee",
            "server_version": "1.30.0",
        }
    )


def test_oss_binding_uses_direct_host_checkout(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    commit = git_repo(source)
    service = WorkerBeeWorkspaceService(tmp_path / "state")

    binding = service.prepare(
        runtime=runtime(tmp_path, "oss"), project="demo", source_root=source
    )

    assert binding.backend == "oss"
    assert binding.workspace_strategy == "direct_host"
    assert binding.runtime_workspace_path == str(source.resolve())
    assert binding.source_commit == commit
    assert binding.session_start_args(goal="test")["cwd"] == str(source.resolve())
    assert "workspace_sha256" not in binding.session_start_args(goal="test")
    assert service.load(binding.binding_id) == binding


def test_debug_binding_uses_exact_materializer_receipt(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    commit = git_repo(source)

    def runner(command, **_kwargs):
        assert command[1:4] == ["stage", "--backend", "platform_debug"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "backend": "platform_debug",
                    "runtime_workspace_path": "/workspace/imports/demo-123",
                    "content_sha256": "a" * 64,
                    "source_commit": commit,
                    "source_dirty": False,
                    "receipt_path": "/private/demo.json",
                }
            ),
            stderr="",
        )

    service = WorkerBeeWorkspaceService(
        tmp_path / "state", helper_bin="/helper", runner=runner
    )
    selected = runtime(tmp_path, "platform_debug")
    binding = service.prepare(runtime=selected, project="demo", source_root=source)

    assert binding.workspace_strategy == "content_addressed_import"
    assert binding.runtime_workspace_path == "/workspace/imports/demo-123"
    assert binding.content_sha256 == "a" * 64
    assert binding.session_start_args(goal="deploy")["workspace_path"] == binding.runtime_workspace_path
    service.verify_runtime(binding, selected)


def test_binding_cannot_cross_backend_or_generation(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    service = WorkerBeeWorkspaceService(tmp_path / "state")
    selected = runtime(tmp_path, "oss")
    binding = service.prepare(runtime=selected, project="demo", source_root=source)

    other = runtime(tmp_path / "other", "platform_debug")
    with pytest.raises(WorkerBeeWorkspaceError) as backend_error:
        service.verify_runtime(binding, other)
    assert backend_error.value.code == "WORKERBEE_WORKSPACE_BACKEND_MISMATCH"

    registry = WorkerBeeRuntimeRegistry(tmp_path / "next")
    first = registry.observe(
        {"backend": "oss", "endpoint": "http://127.0.0.1:8765/mcp"}
    )
    old = service.prepare(runtime=first, project="other", source_root=source)
    next_generation = registry.observe(
        {"backend": "oss", "endpoint": "http://127.0.0.1:8765/mcp"},
        force_generation=True,
    )
    with pytest.raises(WorkerBeeWorkspaceError) as generation_error:
        service.verify_runtime(old, next_generation)
    assert generation_error.value.code == "WORKERBEE_BACKEND_REBIND_REQUIRED"


def test_user_appliance_contract_is_deferred_without_materializer(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    service = WorkerBeeWorkspaceService(tmp_path / "state")

    with pytest.raises(WorkerBeeWorkspaceError) as error:
        service.prepare(
            runtime=runtime(tmp_path, "user_appliance"),
            project="demo",
            source_root=source,
        )

    assert error.value.code == "WORKERBEE_USER_APPLIANCE_DEFERRED"
