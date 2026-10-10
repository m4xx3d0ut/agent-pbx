from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_pbx.workerbee_harness import (
    WORKERBEE_BINDING_API_VERSION,
    content_sha256,
    load_workerbee_harness_binding,
    prepare_workerbee_harness_binding,
)


def committed_source(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "config", "user.name", "Harness"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "harness@example.invalid"],
        cwd=source,
        check=True,
    )
    (source / "README.md").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return source, commit


def test_fixture_binding_is_task_owned_and_content_verified(tmp_path) -> None:
    source, commit = committed_source(tmp_path)
    binding = prepare_workerbee_harness_binding(
        backend="fixture",
        project="agent-pbx-harness-unit",
        source_root=source,
        source_commit=commit,
        source_dirty=True,
        artifacts_root=tmp_path / "artifacts",
    )

    assert binding.status == "fixture_ready"
    assert binding.task_owned is True
    assert binding.live_endpoint_required is False
    assert content_sha256(Path(binding.snapshot_path)) == binding.workspace_sha256
    assert load_workerbee_harness_binding(Path(binding.receipt_path)) == binding
    payload = json.loads(Path(binding.receipt_path).read_text(encoding="utf-8"))
    assert payload["api_version"] == WORKERBEE_BINDING_API_VERSION
    assert Path(binding.receipt_path).stat().st_mode & 0o777 == 0o600


def test_committed_snapshot_is_content_addressed(tmp_path) -> None:
    source, commit = committed_source(tmp_path)
    binding = prepare_workerbee_harness_binding(
        backend="oss-local",
        project="agent-pbx-harness-oss",
        source_root=source,
        source_commit=commit,
        source_dirty=False,
        artifacts_root=tmp_path / "artifacts",
    )

    workspace = Path(binding.snapshot_path)
    assert (workspace / "README.md").read_text(encoding="utf-8") == "committed\n"
    assert not (workspace / ".git").exists()
    assert binding.status == "snapshot_ready"
    assert binding.workspace_sha256 == content_sha256(workspace)


def test_platform_debug_requires_explicit_authorization(tmp_path) -> None:
    source, commit = committed_source(tmp_path)

    with pytest.raises(ValueError, match="explicit --allow-platform-debug"):
        prepare_workerbee_harness_binding(
            backend="platform-debug-bound",
            project="agent-pbx-harness-platform",
            source_root=source,
            source_commit=commit,
            source_dirty=False,
            artifacts_root=tmp_path / "artifacts",
        )


def test_platform_debug_receipt_supplies_required_session_binding(tmp_path) -> None:
    source, commit = committed_source(tmp_path)
    binding = prepare_workerbee_harness_binding(
        backend="platform-debug-bound",
        project="agent-pbx-harness-platform",
        source_root=source,
        source_commit=commit,
        source_dirty=False,
        artifacts_root=tmp_path / "artifacts",
        allow_platform_debug=True,
        workerbee_workspace_path=Path("/imports/agent-pbx-harness-platform"),
    )

    arguments = binding.session_start_args(goal="Validate Agent PBX 2.2")
    assert binding.live_endpoint_required is True
    assert binding.status == "binding_ready"
    assert arguments["project"] == "agent-pbx-harness-platform"
    assert arguments["workspace_path"] == binding.workspace_path
    assert arguments["workspace_sha256"] == binding.workspace_sha256
    assert arguments["source_commit"] == commit


def test_live_binding_rejects_dirty_source_and_unowned_project(tmp_path) -> None:
    source, commit = committed_source(tmp_path)

    with pytest.raises(ValueError, match="clean committed"):
        prepare_workerbee_harness_binding(
            backend="oss-local",
            project="agent-pbx-harness-dirty",
            source_root=source,
            source_commit=commit,
            source_dirty=True,
            artifacts_root=tmp_path / "dirty-artifacts",
        )


def test_platform_snapshot_stays_unbound_until_import_path_is_supplied(tmp_path) -> None:
    source, commit = committed_source(tmp_path)
    binding = prepare_workerbee_harness_binding(
        backend="platform-debug-bound",
        project="agent-pbx-harness-unbound",
        source_root=source,
        source_commit=commit,
        source_dirty=False,
        artifacts_root=tmp_path / "artifacts",
        allow_platform_debug=True,
    )

    assert binding.status == "snapshot_ready_unbound"
    assert binding.workspace_path == ""
    with pytest.raises(ValueError, match="import root"):
        binding.session_start_args(goal="test")
    with pytest.raises(ValueError, match="task-owned"):
        prepare_workerbee_harness_binding(
            backend="fixture",
            project="agent-pbx",
            source_root=source,
            source_commit=commit,
            source_dirty=False,
            artifacts_root=tmp_path / "unsafe-artifacts",
        )


def test_receipt_detects_workspace_drift(tmp_path) -> None:
    source, commit = committed_source(tmp_path)
    binding = prepare_workerbee_harness_binding(
        backend="oss-local",
        project="agent-pbx-harness-drift",
        source_root=source,
        source_commit=commit,
        source_dirty=False,
        artifacts_root=tmp_path / "artifacts",
    )
    (Path(binding.snapshot_path) / "README.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        load_workerbee_harness_binding(Path(binding.receipt_path))
