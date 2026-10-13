from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from agent_pbx.workerbee_materializer import (
    WorkerBeeDebugMaterializer,
    WorkerBeeMaterializerError,
)


def test_debug_materializer_stages_and_verifies_isolated_import(tmp_path: Path) -> None:
    stage_script = tmp_path / "stage.py"
    stage_script.write_text("# fixture\n", encoding="utf-8")
    source = tmp_path / "source"
    source.mkdir()
    marker: dict[str, object] | None = None
    commands: list[list[str]] = []
    digest = "a" * 64
    commit = "b" * 40

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal marker
        commands.append(list(command))
        if command[0] == "/fixture/python":
            destination = Path(command[command.index("--destination") + 1])
            receipt = Path(command[command.index("--receipt") + 1])
            destination.mkdir(parents=True)
            (destination / ".workerbee-workspace.json").write_text("{}\n")
            receipt.write_text(
                json.dumps(
                    {
                        "content_sha256": digest,
                        "source_commit": commit,
                        "source_dirty": False,
                        "owner_project": "demo",
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["exec", "debug"] and command[3] == "cat":
            if marker is None:
                return subprocess.CompletedProcess(command, 1, "", "missing")
            return subprocess.CompletedProcess(command, 0, json.dumps(marker), "")
        if command[1:3] == ["exec", "debug"] and command[3:5] == ["id", "-u"]:
            return subprocess.CompletedProcess(command, 0, "65532\n", "")
        if command[1:3] == ["exec", "debug"] and command[3:5] == ["id", "-g"]:
            return subprocess.CompletedProcess(command, 0, "65532\n", "")
        if command[1] == "cp":
            marker = {
                "content_sha256": digest,
                "source_commit": commit,
                "source_dirty": False,
                "owner_project": "demo",
            }
        return subprocess.CompletedProcess(command, 0, "", "")

    materializer = WorkerBeeDebugMaterializer(
        stage_script=stage_script,
        stage_python="/fixture/python",
        container="debug",
        state_root=tmp_path / "state",
        docker_bin="docker",
        runner=runner,
    )
    result = materializer.stage(
        backend="platform_debug",
        source=source,
        project="demo",
        binding_id=f"wb-{'1' * 32}",
    )

    assert result["runtime_workspace_path"] == f"/workspace/imports/wb-{'1' * 32}"
    assert result["content_sha256"] == digest
    assert result["source_commit"] == commit
    assert result["reused"] is False
    assert any(command[1] == "cp" for command in commands if command[0] == "docker")
    assert [
        "docker",
        "exec",
        "-u",
        "0",
        "debug",
        "chown",
        "-R",
        "65532:65532",
        f"/workspace/imports/wb-{'1' * 32}",
    ] in commands
    receipt = Path(str(result["receipt_path"]))
    assert receipt.stat().st_mode & 0o777 == 0o600


def test_debug_materializer_rejects_deferred_user_appliance(tmp_path: Path) -> None:
    materializer = WorkerBeeDebugMaterializer(
        stage_script=tmp_path / "missing.py",
        stage_python="/fixture/python",
    )

    with pytest.raises(WorkerBeeMaterializerError, match="deferred"):
        materializer.stage(
            backend="user_appliance",
            source=tmp_path,
            project="demo",
            binding_id=f"wb-{'2' * 32}",
        )


def test_debug_materializer_release_requires_exact_path_and_digest(tmp_path: Path) -> None:
    stage_script = tmp_path / "stage.py"
    stage_script.write_text("# fixture\n", encoding="utf-8")
    digest = "c" * 64
    commands: list[list[str]] = []

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(list(command))
        if command[3] == "cat":
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"content_sha256": digest}),
                "",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    binding_id = f"wb-{'3' * 32}"
    runtime_path = f"/workspace/imports/{binding_id}"
    materializer = WorkerBeeDebugMaterializer(
        stage_script=stage_script,
        stage_python="/fixture/python",
        container="debug",
        state_root=tmp_path / "state",
        docker_bin="docker",
        runner=runner,
    )

    with pytest.raises(WorkerBeeMaterializerError, match="path"):
        materializer.release(
            backend="platform_debug",
            binding_id=binding_id,
            runtime_workspace_path="/workspace/imports/wrong",
            content_sha256=digest,
        )
    result = materializer.release(
        backend="platform_debug",
        binding_id=binding_id,
        runtime_workspace_path=runtime_path,
        content_sha256=digest,
    )
    assert result["released"] is True
    assert commands[-1][-2:] == ["--", runtime_path]


def test_debug_materializer_adds_checkout_src_to_staging_pythonpath(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "workerbee-private"
    stage_script = repository / "scripts" / "dev" / "stage.py"
    stage_script.parent.mkdir(parents=True)
    stage_script.write_text("# fixture\n", encoding="utf-8")
    (repository / "src" / "workerbee").mkdir(parents=True)
    materializer = WorkerBeeDebugMaterializer(
        stage_script=stage_script,
        stage_python="/fixture/python",
    )

    env = materializer._stage_environment()

    assert env["PYTHONPATH"].split(":")[0] == str(repository / "src")
