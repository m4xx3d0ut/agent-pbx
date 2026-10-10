from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
from typing import Any, Callable


WORKERBEE_BINDING_API_VERSION = "agent-pbx.workerbee-harness-binding/v1"
WORKERBEE_BINDING_FILENAME = "workerbee-binding.json"
WORKERBEE_HARNESS_BACKENDS = ("fixture", "oss-local", "platform-debug-bound")


@dataclass(frozen=True, slots=True)
class WorkerBeeHarnessBinding:
    backend: str
    project: str
    source_root: str
    source_commit: str
    source_dirty: bool
    workspace_path: str
    workspace_sha256: str
    task_owned: bool
    live_endpoint_required: bool
    status: str
    receipt_path: str

    def public_dict(self) -> dict[str, Any]:
        return {
            "api_version": WORKERBEE_BINDING_API_VERSION,
            **asdict(self),
        }

    def session_start_args(self, *, goal: str) -> dict[str, Any]:
        return {
            "cwd": self.workspace_path,
            "goal": goal,
            "project": self.project,
            "source_commit": self.source_commit,
            "source_dirty": self.source_dirty,
            "workspace_path": self.workspace_path,
            "workspace_sha256": self.workspace_sha256,
            "open_dashboard": False,
        }


def _validate_project(project: str) -> str:
    value = project.strip()
    if not value.startswith("agent-pbx-harness-"):
        raise ValueError("WorkerBee harness project must be task-owned")
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in value):
        raise ValueError("WorkerBee harness project contains unsafe characters")
    return value


def _member_destination(root: Path, member: tarfile.TarInfo) -> Path:
    name = PurePosixPath(member.name)
    if name.is_absolute() or ".." in name.parts:
        raise ValueError(f"unsafe Git archive member: {member.name}")
    destination = (root / Path(*name.parts)).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Git archive member escapes workspace: {member.name}") from exc
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        if target.is_absolute() or ".." in target.parts:
            raise ValueError(f"unsafe Git archive link: {member.name}")
    return destination


def _extract_git_archive(data: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive.getmembers():
            target = _member_destination(destination, member)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                target.symlink_to(member.linkname)
                continue
            if not member.isfile():
                raise ValueError(f"unsupported Git archive member: {member.name}")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unable to read Git archive member: {member.name}")
            with target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)


def content_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(b"L\0")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"F\0")
            digest.update(relative.encode())
            digest.update(b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"D\0")
            digest.update(relative.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def prepare_workerbee_harness_binding(
    *,
    backend: str,
    project: str,
    source_root: Path,
    source_commit: str,
    source_dirty: bool,
    artifacts_root: Path,
    allow_platform_debug: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> WorkerBeeHarnessBinding:
    if backend not in WORKERBEE_HARNESS_BACKENDS:
        raise ValueError(f"unsupported WorkerBee harness backend: {backend}")
    selected_project = _validate_project(project)
    if backend == "platform-debug-bound" and not allow_platform_debug:
        raise ValueError(
            "platform-debug WorkerBee requires explicit --allow-platform-debug authorization"
        )
    if source_dirty and backend != "fixture":
        raise ValueError(
            "live WorkerBee harness bindings require a clean committed source snapshot"
        )

    binding_root = artifacts_root / "workerbee"
    workspace = binding_root / "workspace"
    receipt = binding_root / WORKERBEE_BINDING_FILENAME
    if binding_root.exists():
        raise ValueError(f"WorkerBee binding already exists: {binding_root}")
    binding_root.mkdir(parents=True, mode=0o700)

    if backend == "fixture":
        workspace.mkdir(mode=0o700)
        (workspace / "README.md").write_text(
            "Agent PBX isolated WorkerBee fixture workspace.\n", encoding="utf-8"
        )
        status = "fixture_ready"
    else:
        result = runner(
            ["git", "archive", "--format=tar", source_commit],
            cwd=source_root,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace") if result.stderr else ""
            raise RuntimeError(stderr.strip() or "unable to create committed Git snapshot")
        _extract_git_archive(result.stdout, workspace)
        status = "snapshot_ready"

    digest = content_sha256(workspace)
    binding = WorkerBeeHarnessBinding(
        backend=backend,
        project=selected_project,
        source_root=str(source_root.resolve()),
        source_commit=source_commit,
        source_dirty=source_dirty,
        workspace_path=str(workspace.resolve()),
        workspace_sha256=digest,
        task_owned=True,
        live_endpoint_required=backend == "platform-debug-bound",
        status=status,
        receipt_path=str(receipt.resolve()),
    )
    receipt.write_text(
        json.dumps(binding.public_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt.chmod(0o600)
    return binding


def load_workerbee_harness_binding(path: Path) -> WorkerBeeHarnessBinding:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.pop("api_version", "") != WORKERBEE_BINDING_API_VERSION:
        raise ValueError("unsupported WorkerBee harness binding receipt")
    binding = WorkerBeeHarnessBinding(**payload)
    _validate_project(binding.project)
    if content_sha256(Path(binding.workspace_path)) != binding.workspace_sha256:
        raise ValueError("WorkerBee harness workspace content does not match its receipt")
    return binding
