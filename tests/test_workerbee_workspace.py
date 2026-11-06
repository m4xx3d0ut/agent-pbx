from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_pbx.workerbee_direct_bind as direct_bind
from agent_pbx.workerbee_runtime import WorkerBeeRuntimeRegistry
from agent_pbx.workerbee_workspace import (
    WORKERBEE_WORKSPACE_BINDING_LEGACY_API_VERSION,
    WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV,
    WorkerBeeWorkspaceError,
    WorkerBeeWorkspaceIncludePolicyError,
    WorkerBeeWorkspaceService,
    configured_workerbee_workspace_include_paths,
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


def test_workspace_include_policy_is_agent_and_source_bound(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    env = {
        WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV: json.dumps(
            {
                "agent-1": {
                    "source_root": str(source),
                    "include_paths": [
                        "business-site/product-pages/agent-pbx",
                        "business-site/docs/agent-pbx-product-site.md",
                        "business-site/product-pages/agent-pbx",
                    ],
                }
            }
        )
    }

    assert configured_workerbee_workspace_include_paths(
        agent_id="agent-1",
        source_root=source,
        env=env,
    ) == (
        "business-site/product-pages/agent-pbx",
        "business-site/docs/agent-pbx-product-site.md",
    )
    assert configured_workerbee_workspace_include_paths(
        agent_id="agent-2",
        source_root=source,
        env=env,
    ) == ()
    with pytest.raises(WorkerBeeWorkspaceIncludePolicyError, match="does not match"):
        configured_workerbee_workspace_include_paths(
            agent_id="agent-1",
            source_root=tmp_path / "other",
            env=env,
        )


@pytest.mark.parametrize(
    "value",
    ["../secret", "/absolute", "nested//file", "nested/./file", ".git/config", "a\\b"],
)
def test_workspace_include_policy_rejects_unsafe_paths(
    tmp_path: Path,
    value: str,
) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    env = {
        WORKERBEE_WORKSPACE_INCLUDE_POLICY_ENV: json.dumps(
            {
                "agent-1": {
                    "source_root": str(source),
                    "include_paths": [value],
                }
            }
        )
    }

    with pytest.raises(WorkerBeeWorkspaceIncludePolicyError, match="unsafe path"):
        configured_workerbee_workspace_include_paths(
            agent_id="agent-1",
            source_root=source,
            env=env,
        )


def test_workspace_service_rejects_unsafe_include_before_materializer(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    service = WorkerBeeWorkspaceService(
        tmp_path / "state",
        helper_bin="/helper",
        runner=lambda *_args, **_kwargs: pytest.fail("materializer must not run"),
    )

    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.prepare(
            runtime=runtime(tmp_path, "platform_debug"),
            project="demo",
            source_root=source,
            include_paths=("../outside",),
        )

    assert failure.value.code == "WORKERBEE_WORKSPACE_INCLUDE_INVALID"


def test_oss_binding_uses_direct_host_checkout(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    commit = git_repo(source)
    service = WorkerBeeWorkspaceService(tmp_path / "state")

    binding = service.prepare(
        runtime=runtime(tmp_path, "oss"), project="demo", source_root=source
    )

    assert binding.backend == "oss"
    assert binding.source_mode == "native-direct"
    assert binding.source_mode_version == 1
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
    assert binding.source_mode == "snapshot-import"
    assert binding.runtime_workspace_path == "/workspace/imports/demo-123"
    assert binding.content_sha256 == "a" * 64
    assert binding.session_start_args(goal="deploy")["workspace_path"] == binding.runtime_workspace_path
    service.verify_runtime(binding, selected)


def test_workspace_service_loads_v2_receipt_as_explicit_legacy_source_mode(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    service = WorkerBeeWorkspaceService(tmp_path / "state")
    binding = service.prepare(
        runtime=runtime(tmp_path, "oss"), project="demo", source_root=source
    )
    payload = binding.public_dict()
    payload["api_version"] = WORKERBEE_WORKSPACE_BINDING_LEGACY_API_VERSION
    for key in (
        "source_mode",
        "source_mode_version",
        "helper_receipt_id",
        "helper_receipt_sha256",
        "helper_adapter_receipt_sha256",
        "capability_evidence_sha256",
        "helper_mount_id",
        "helper_binary_sha256",
        "helper_receipt_expires_at",
    ):
        payload.pop(key)
    receipt_path = service.state_root / "bindings" / f"{binding.binding_id}.json"
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = service.load(binding.binding_id)

    assert migrated.source_mode == "native-direct"
    assert migrated.source_mode_version == 0
    assert migrated.workspace_strategy == "direct_host"


def trusted_direct_fixture(
    tmp_path: Path,
    selected,
    source: Path,
    *,
    payload_overrides: dict[str, object] | None = None,
    administrator_ceiling: bool = True,
    approved_roots: tuple[Path, ...] | None = None,
    path_mode: int = 0o700,
):
    source.parent.chmod(path_mode)
    source.chmod(path_mode)
    helper = tmp_path / "trusted-direct-helper"
    helper.write_bytes(b"#!/bin/sh\nexit 0\n")
    helper.chmod(0o700)
    helper_stat = helper.stat()
    source_stat = source.stat()
    receipt_id = "helper-receipt-1"
    runtime_path = "/workspace/direct-bind/demo"
    if (source / ".git").exists():
        source_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        source_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=source,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        source_status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z"],
            cwd=source,
            check=True,
            capture_output=True,
        ).stdout
    else:
        source_commit = "1" * 40
        source_tree = "2" * 40
        source_status = b""
    source_status_sha256 = hashlib.sha256(source_status).hexdigest()
    identity = {
        "source_commit": source_commit,
        "source_tree": source_tree,
        "source_dirty": bool(source_status),
        "source_status_sha256": source_status_sha256,
    }
    binding_id = WorkerBeeWorkspaceService._binding_id(
        selected,
        "demo",
        identity,
        source_mode="trusted-direct-bind",
    )
    account = direct_bind.pwd.getpwuid(os.getuid())
    group = direct_bind.grp.getgrgid(account.pw_gid)
    private_group = direct_bind.WorkerBeePrivateGroupIdentity(
        uid=os.getuid(),
        gid=account.pw_gid,
        username=account.pw_name,
        group_name=group.gr_name,
        primary_gid_users=tuple(
            sorted(entry.pw_name for entry in direct_bind.pwd.getpwall() if entry.pw_gid == account.pw_gid)
        ),
        explicit_members=tuple(sorted(group.gr_mem)),
    )
    workerbee_receipt_sha256 = "a" * 64
    payload: dict[str, object] = {
        "schema": direct_bind.TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION,
        "action": "verify-direct-bind",
        "status": "accepted",
        "receipt_id": receipt_id,
        "binding_id": binding_id,
        "workerbee_receipt_schema": direct_bind.TRUSTED_DIRECT_BIND_CANONICAL_SCHEMA,
        "workerbee_receipt_phase": "prepared",
        "workerbee_receipt_sha256": workerbee_receipt_sha256,
        "source_identity_sha256": "b" * 64,
        "source_dev": source_stat.st_dev,
        "source_ino": source_stat.st_ino,
        "source_uid": source_stat.st_uid,
        "source_gid": source_stat.st_gid,
        "source_mode": stat.S_IMODE(source_stat.st_mode),
        "source_git_commit": source_commit,
        "source_git_tree": source_tree,
        "source_git_status_sha256": source_status_sha256,
        "runtime": runtime_path,
        "backend_id": selected.backend,
        "backend_instance": selected.instance_id,
        "backend_image_digest": "sha256:" + "c" * 64,
        "backend_fingerprint": selected.fingerprint,
        "backend_generation": selected.generation,
        "mount_target_sha256": hashlib.sha256(runtime_path.encode()).hexdigest(),
        "mount_read_only": True,
        "mount_propagation": "rprivate",
        "mount_nested_count": 0,
        "private_group_uid": private_group.uid,
        "private_group_gid": private_group.gid,
        "private_group_identity_sha256": private_group.digest(),
        "path_component_identity_sha256": "d" * 64,
        "protected_mask_count": 0,
        "protected_mask_paths_sha256": hashlib.sha256(b"[]").hexdigest(),
        "helper_sha256": hashlib.sha256(helper.read_bytes()).hexdigest(),
        "helper_dev": helper_stat.st_dev,
        "helper_ino": helper_stat.st_ino,
        "helper_uid": helper_stat.st_uid,
        "helper_gid": helper_stat.st_gid,
        "helper_mode": stat.S_IMODE(helper_stat.st_mode),
        "generation_identity_sha256": "e" * 64,
        "issued_at": 95.0,
        "expires_at": 110.0,
        "release_required": True,
        "adapter_conformance_sha256": direct_bind.TRUSTED_DIRECT_BIND_ADAPTER_CONFORMANCE_SHA256,
        "adapter_receipt_sha256": "",
    }
    payload.update(payload_overrides or {})
    payload["adapter_receipt_sha256"] = (
        direct_bind.trusted_direct_adapter_receipt_sha256(payload)
    )
    raw = (json.dumps(payload, sort_keys=True) + "\n").encode()
    receipt_root = tmp_path / "direct-receipts"
    receipt_root.mkdir(mode=0o700)
    receipt = receipt_root / f"{receipt_id}.json"
    receipt.write_bytes(raw)
    receipt.chmod(0o600)
    calls: list[list[str]] = []

    def runner(command, **_kwargs):
        calls.append(list(command))
        operation = command[1]
        response = (
            {
                "receipt_id": receipt_id,
                "binding_id": command[3],
                "schema": direct_bind.TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION,
                "action": "release-direct-bind",
                "status": "released",
                "workerbee_receipt_sha256": workerbee_receipt_sha256,
                "adapter_receipt_sha256": payload["adapter_receipt_sha256"],
                "cleanup_complete": True,
                "mask_residue_count": 0,
                "adapter_receipt_present": False,
            }
            if operation == "release-direct-bind"
            else dict(payload)
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(response),
            stderr="",
        )

    service = WorkerBeeWorkspaceService(
        tmp_path / "state",
        helper_bin=helper,
        trusted_direct_receipt_root=receipt_root,
        trusted_direct_approved_roots=approved_roots or (source.parent,),
        trusted_direct_administrator_ceiling=administrator_ceiling,
        runner=runner,
        clock=lambda: 100.0,
    )
    return service, receipt_id, workerbee_receipt_sha256, calls


def test_trusted_direct_adapter_conformance_fixture_is_byte_exact() -> None:
    fixture_path = (
        Path(__file__).parent
        / "fixtures"
        / "workerbee_trusted_direct_bind_adapter_v2.json"
    )
    raw = fixture_path.read_bytes()
    payload = json.loads(raw)

    assert len(raw) == direct_bind.TRUSTED_DIRECT_BIND_ADAPTER_CONFORMANCE_BYTES
    assert hashlib.sha256(raw).hexdigest() == (
        direct_bind.TRUSTED_DIRECT_BIND_ADAPTER_CONFORMANCE_SHA256
    )
    assert not raw.endswith(b"\n")
    assert payload["schema"] == direct_bind.TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION
    assert payload["workerbee_receipt_owner"] == (
        direct_bind.TRUSTED_DIRECT_BIND_CANONICAL_SCHEMA
    )
    assert tuple(payload["verify_output_fields"]) == (
        direct_bind.TRUSTED_DIRECT_BIND_VERIFY_OUTPUT_FIELDS
    )
    assert tuple(payload["release_output_fields"]) == (
        direct_bind.TRUSTED_DIRECT_BIND_RELEASE_OUTPUT_FIELDS
    )
    assert payload["commands"]["verify-direct-bind"]["exact_argv"][-2:] == [
        "--expires-at",
        "<unix-seconds>",
    ]
    assert "--expires-at" not in payload["commands"]["release-direct-bind"][
        "exact_argv"
    ]


@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong"])
def test_trusted_direct_bind_revalidation_rejects_response_field_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path, selected, source
    )
    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )
    receipt_path = tmp_path / "direct-receipts" / f"{receipt_id}.json"
    response = json.loads(receipt_path.read_text(encoding="utf-8"))
    if mutation == "extra":
        response["unexpected"] = True
    elif mutation == "missing":
        response.pop("protected_mask_count")
    else:
        response["protected_mask_count"] = 1

    def drifted(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(response),
            stderr="",
        )

    assert service.trusted_direct_verifier is not None
    service.trusted_direct_verifier.runner = drifted
    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.revalidate_trusted_direct(binding, selected)

    assert failure.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_REVALIDATION_FAILED"


def test_trusted_direct_bind_rejects_adapter_receipt_self_digest_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    runtime = WorkerBeeRuntimeRegistry(tmp_path / "runtime")
    selected = runtime.observe(
        {
            "backend": "platform_debug",
            "endpoint": "http://127.0.0.1:8765/mcp",
            "server_name": "WorkerBee Debug",
            "server_version": "2.3.0rc2",
        }
    )
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
    )
    assert service.trusted_direct_verifier is not None
    receipt_path = service.trusted_direct_verifier.receipt_root / f"{receipt_id}.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["source_identity_sha256"] = "9" * 64
    receipt_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    receipt_path.chmod(0o600)

    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )

    assert failure.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH"


def test_trusted_direct_bind_requires_ceiling_safe_root_and_verified_helper(
    tmp_path: Path,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir()
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
    )

    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )

    assert binding.source_mode == "trusted-direct-bind"
    assert binding.workspace_strategy == "trusted_direct_bind"
    assert binding.runtime_workspace_path == "/workspace/direct-bind/demo"
    assert binding.helper_receipt_id == receipt_id
    assert binding.helper_receipt_sha256 == receipt_sha256
    assert len(binding.capability_evidence_sha256) == 64


def test_trusted_direct_bind_rejects_missing_ceiling_and_receipt_drift(
    tmp_path: Path,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir()
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
        administrator_ceiling=False,
    )

    with pytest.raises(WorkerBeeWorkspaceError) as ceiling:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )
    assert ceiling.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_CEILING_REQUIRED"

    authorized = tmp_path / "authorized"
    authorized.mkdir(mode=0o700)
    service, _receipt_id, _receipt_sha256, _calls = trusted_direct_fixture(
        authorized,
        selected,
        source,
    )
    with pytest.raises(WorkerBeeWorkspaceError) as unknown:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id="unknown-receipt",
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )
    assert unknown.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_UNKNOWN"


def test_trusted_direct_bind_rejects_cross_id_host_receipt(tmp_path: Path) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
        payload_overrides={"receipt_id": "different-receipt"},
    )

    with pytest.raises(WorkerBeeWorkspaceError) as mismatch:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )

    assert mismatch.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH"


def test_trusted_direct_bind_accepts_private_primary_group_0775(tmp_path: Path) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
        path_mode=0o775,
    )

    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )

    assert binding.source_mode == "trusted-direct-bind"
    assert len(binding.capability_evidence_sha256) == 64


def test_trusted_direct_bind_rejects_world_and_foreign_group_write_before_git_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path, selected, source
    )
    monkeypatch.setattr(
        WorkerBeeWorkspaceService,
        "_source_identity",
        lambda *_args, **_kwargs: pytest.fail("Git/source identity must not run"),
    )
    source.chmod(0o777)
    with pytest.raises(WorkerBeeWorkspaceError) as world:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )
    assert world.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_MODE_UNSAFE"

    source.chmod(0o775)
    real_account = direct_bind.pwd.getpwuid(os.getuid())
    monkeypatch.setattr(
        direct_bind.pwd,
        "getpwall",
        lambda: [
            real_account,
            SimpleNamespace(pw_name="foreign", pw_gid=os.getgid()),
        ],
    )
    with pytest.raises(WorkerBeeWorkspaceError) as foreign:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )
    assert foreign.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_PRIVATE_GROUP_UNPROVEN"


def test_trusted_direct_bind_denied_source_performs_zero_git_identity_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    approved = tmp_path / "approved"
    approved.mkdir(mode=0o700)
    denied = tmp_path / "denied"
    denied.mkdir(mode=0o700)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        denied,
        approved_roots=(approved,),
    )
    monkeypatch.setattr(
        WorkerBeeWorkspaceService,
        "_source_identity",
        lambda *_args, **_kwargs: pytest.fail("Git/source identity must not run"),
    )

    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=denied,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )

    assert failure.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_DENIED"


def test_trusted_direct_bind_rejects_lexical_symlink_before_git_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    link = tmp_path / "approved" / "repo-link"
    link.symlink_to(source, target_is_directory=True)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
    )
    monkeypatch.setattr(
        WorkerBeeWorkspaceService,
        "_source_identity",
        lambda *_args, **_kwargs: pytest.fail("Git/source identity must not run"),
    )

    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=link,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )

    assert failure.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_SYMLINK_DENIED"


def test_trusted_direct_bind_revalidates_and_releases_exact_helper_mount(
    tmp_path: Path,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
    )
    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )

    service.revalidate_trusted_direct(
        binding,
        selected,
    )
    released = service.release(binding.binding_id)

    assert released.state == "released"
    assert [command[1] for command in calls] == [
        "verify-direct-bind",
        "release-direct-bind",
    ]
    assert all("--receipt-id" in command for command in calls)
    assert "--expires-at" in calls[0]
    assert "--expires-at" not in calls[1]
    assert "--json" not in calls[0]
    assert "--json" not in calls[1]


def test_trusted_direct_bind_operation_revalidation_rechecks_service_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path, selected, source, path_mode=0o775
    )
    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )

    real_account = direct_bind.pwd.getpwuid(os.getuid())
    monkeypatch.setattr(
        direct_bind.pwd,
        "getpwall",
        lambda: [
            real_account,
            SimpleNamespace(pw_name="foreign", pw_gid=os.getgid()),
        ],
    )
    with pytest.raises(WorkerBeeWorkspaceError) as group_drift:
        service.revalidate_trusted_direct(binding, selected)
    assert group_drift.value.code == (
        "WORKERBEE_TRUSTED_DIRECT_BIND_PRIVATE_GROUP_UNPROVEN"
    )

    monkeypatch.undo()
    service.trusted_direct_administrator_ceiling = False
    with pytest.raises(WorkerBeeWorkspaceError) as ceiling:
        service.revalidate_trusted_direct(binding, selected)
    assert ceiling.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_CEILING_REQUIRED"


def test_trusted_direct_bind_incomplete_release_preserves_ready_binding(
    tmp_path: Path,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
    )
    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )

    def incomplete(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "receipt_id": receipt_id,
                    "binding_id": command[3],
                    "status": "released",
                    "cleanup_complete": False,
                    "mask_residue_count": 1,
                    "adapter_receipt_present": True,
                }
            ),
            stderr="",
        )

    assert service.trusted_direct_verifier is not None
    service.trusted_direct_verifier.runner = incomplete
    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.release(binding.binding_id)

    assert failure.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_RELEASE_INCOMPLETE"
    assert service.load(binding.binding_id).state == "ready"


@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong"])
def test_trusted_direct_bind_release_rejects_response_field_drift(
    tmp_path: Path,
    mutation: str,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path, selected, source
    )
    binding = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        source_mode="trusted-direct-bind",
        trusted_direct_bind_receipt_id=receipt_id,
        trusted_direct_bind_receipt_sha256=receipt_sha256,
    )
    response: dict[str, object] = {
        "schema": direct_bind.TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION,
        "action": "release-direct-bind",
        "status": "released",
        "receipt_id": receipt_id,
        "binding_id": binding.binding_id,
        "workerbee_receipt_sha256": receipt_sha256,
        "adapter_receipt_sha256": binding.helper_adapter_receipt_sha256,
        "cleanup_complete": True,
        "mask_residue_count": 0,
        "adapter_receipt_present": False,
    }
    if mutation == "extra":
        response["unexpected"] = True
    elif mutation == "missing":
        response.pop("cleanup_complete")
    else:
        response["mask_residue_count"] = 1

    def drifted(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(response),
            stderr="",
        )

    assert service.trusted_direct_verifier is not None
    service.trusted_direct_verifier.runner = drifted
    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.release(binding.binding_id)

    assert failure.value.code == "WORKERBEE_TRUSTED_DIRECT_BIND_RELEASE_INCOMPLETE"
    assert service.load(binding.binding_id).state == "ready"


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"runtime": "/workspace/direct-bind/../escape"}, "WORKERBEE_TRUSTED_DIRECT_BIND_RUNTIME_PATH_DENIED"),
        ({"backend_generation": 99}, "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH"),
        ({"mount_nested_count": 1}, "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH"),
    ],
)
def test_trusted_direct_bind_rejects_unsafe_closed_receipt(
    tmp_path: Path,
    override: dict[str, object],
    code: str,
) -> None:
    source = tmp_path / "approved" / "repo"
    source.parent.mkdir(mode=0o700)
    git_repo(source)
    selected = runtime(tmp_path, "platform_debug")
    service, receipt_id, receipt_sha256, _calls = trusted_direct_fixture(
        tmp_path,
        selected,
        source,
        payload_overrides=override,
    )

    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.prepare(
            runtime=selected,
            project="demo",
            source_root=source,
            source_mode="trusted-direct-bind",
            trusted_direct_bind_receipt_id=receipt_id,
            trusted_direct_bind_receipt_sha256=receipt_sha256,
        )

    assert failure.value.code == code


def test_source_mode_backend_mismatch_never_falls_back(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    git_repo(source)
    service = WorkerBeeWorkspaceService(tmp_path / "state")

    with pytest.raises(WorkerBeeWorkspaceError) as failure:
        service.prepare(
            runtime=runtime(tmp_path, "oss"),
            project="demo",
            source_root=source,
            source_mode="snapshot-import",
        )

    assert failure.value.code == "WORKERBEE_SOURCE_MODE_BACKEND_INCOMPATIBLE"


def test_debug_binding_identity_changes_with_explicit_include_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo"
    commit = git_repo(source)
    extra = source / "business-site" / "product-pages" / "agent-pbx" / "index.html"
    extra.parent.mkdir(parents=True)
    extra.write_text("first\n", encoding="utf-8")

    def runner(command, **_kwargs):
        binding_id = command[command.index("--binding-id") + 1]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "backend": "platform_debug",
                    "runtime_workspace_path": f"/workspace/imports/{binding_id}",
                    "content_sha256": "a" * 64,
                    "source_commit": commit,
                    "source_dirty": True,
                    "receipt_path": f"/private/{binding_id}.json",
                }
            ),
            stderr="",
        )

    service = WorkerBeeWorkspaceService(
        tmp_path / "state", helper_bin="/helper", runner=runner
    )
    selected = runtime(tmp_path, "platform_debug")
    include_paths = ("business-site/product-pages/agent-pbx",)
    first = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        include_paths=include_paths,
    )
    extra.write_text("second\n", encoding="utf-8")
    second = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
        include_paths=include_paths,
    )

    assert first.binding_id != second.binding_id
    assert first.runtime_workspace_path != second.runtime_workspace_path


def test_debug_binding_identity_changes_with_dirty_submodule_content(
    tmp_path: Path,
) -> None:
    submodule_source = tmp_path / "submodule-source"
    submodule_commit = git_repo(submodule_source)
    source = tmp_path / "repo"
    git_repo(source)
    subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-C",
            str(source),
            "submodule",
            "add",
            str(submodule_source),
            "business-site",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "commit", "-qam", "add submodule"],
        check=True,
    )
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert subprocess.run(
        ["git", "-C", str(source / "business-site"), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == submodule_commit

    def runner(command, **_kwargs):
        binding_id = command[command.index("--binding-id") + 1]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "backend": "platform_debug",
                    "runtime_workspace_path": f"/workspace/imports/{binding_id}",
                    "content_sha256": "b" * 64,
                    "source_commit": commit,
                    "source_dirty": True,
                    "receipt_path": f"/private/{binding_id}.json",
                }
            ),
            stderr="",
        )

    service = WorkerBeeWorkspaceService(
        tmp_path / "state", helper_bin="/helper", runner=runner
    )
    selected = runtime(tmp_path, "platform_debug")
    submodule_readme = source / "business-site" / "README.md"
    submodule_readme.write_text("first dirty value\n", encoding="utf-8")
    first = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
    )
    submodule_readme.write_text("second dirty value\n", encoding="utf-8")
    second = service.prepare(
        runtime=selected,
        project="demo",
        source_root=source,
    )

    assert first.binding_id != second.binding_id


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
