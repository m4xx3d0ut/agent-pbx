from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Sequence

from .workerbee_runtime import (
    WORKERBEE_BACKEND_PLATFORM_DEBUG,
    WORKERBEE_BACKEND_USER_APPLIANCE,
    canonical_workerbee_backend,
)


DEBUG_STAGE_SCRIPT_ENV = "AGENT_PBX_WORKERBEE_DEBUG_STAGE_SCRIPT"
DEBUG_STAGE_PYTHON_ENV = "AGENT_PBX_WORKERBEE_DEBUG_STAGE_PYTHON"
DEBUG_CONTAINER_ENV = "AGENT_PBX_WORKERBEE_DEBUG_CONTAINER"
DEBUG_IMPORT_ROOT_ENV = "AGENT_PBX_WORKERBEE_DEBUG_IMPORT_ROOT"
MATERIALIZER_STATE_ROOT_ENV = "AGENT_PBX_WORKERBEE_MATERIALIZER_STATE_ROOT"
DEFAULT_DEBUG_CONTAINER = "workerbee-mcp-debug"
DEFAULT_DEBUG_IMPORT_ROOT = "/workspace/imports"
_SAFE_BINDING_ID = re.compile(r"^wb-[a-f0-9]{32}$")


class WorkerBeeMaterializerError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[str]]


class WorkerBeeDebugMaterializer:
    """Stage exact Git snapshots into the selected platform-debug workspace domain."""

    def __init__(
        self,
        *,
        stage_script: str | Path | None = None,
        stage_python: str | Path | None = None,
        container: str | None = None,
        import_root: str | None = None,
        state_root: str | Path | None = None,
        docker_bin: str | None = None,
        runner: Runner = subprocess.run,
    ) -> None:
        self.stage_script = Path(
            stage_script or os.getenv(DEBUG_STAGE_SCRIPT_ENV) or ""
        ).expanduser()
        self.stage_python = str(
            stage_python or os.getenv(DEBUG_STAGE_PYTHON_ENV) or ""
        ).strip()
        self.container = str(
            container or os.getenv(DEBUG_CONTAINER_ENV) or DEFAULT_DEBUG_CONTAINER
        ).strip()
        self.import_root = str(
            import_root or os.getenv(DEBUG_IMPORT_ROOT_ENV) or DEFAULT_DEBUG_IMPORT_ROOT
        ).rstrip("/")
        self.state_root = Path(
            state_root
            or os.getenv(MATERIALIZER_STATE_ROOT_ENV)
            or (Path.home() / ".local" / "state" / "agent-pbx" / "workerbee-materializer")
        ).expanduser()
        self.docker_bin = str(docker_bin or shutil.which("docker") or "docker")
        self.runner = runner

    def stage(
        self,
        *,
        backend: str,
        source: Path,
        project: str,
        binding_id: str,
        include_paths: Sequence[str] = (),
    ) -> dict[str, Any]:
        selected_backend = canonical_workerbee_backend(backend)
        if selected_backend == WORKERBEE_BACKEND_USER_APPLIANCE:
            raise WorkerBeeMaterializerError(
                "The user-appliance workspace image is deferred and has not been qualified."
            )
        if selected_backend != WORKERBEE_BACKEND_PLATFORM_DEBUG:
            raise WorkerBeeMaterializerError(
                f"The materializer does not handle backend {selected_backend!r}."
            )
        self._validate_binding_id(binding_id)
        self._require_stage_contract()
        source_root = source.expanduser().resolve()
        receipt_dir = self.state_root / "receipts"
        receipt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_dir.chmod(0o700)
        durable_receipt = receipt_dir / f"{binding_id}.json"
        runtime_path = self._runtime_path(binding_id)
        with tempfile.TemporaryDirectory(
            prefix=f"{binding_id}-",
            dir=str(self._private_temp_root()),
        ) as temporary:
            temp_root = Path(temporary)
            staged = temp_root / binding_id
            staged_receipt = temp_root / "receipt.json"
            command = [
                self.stage_python,
                str(self.stage_script),
                "--source",
                str(source_root),
                "--destination",
                str(staged),
                "--project",
                project,
                "--receipt",
                str(staged_receipt),
            ]
            for value in include_paths:
                command.extend(("--include", str(value)))
            self._run(
                command,
                timeout=300,
                label="workspace staging",
                env=self._stage_environment(),
            )
            receipt = self._read_json(staged_receipt)
            expected = self._normalized_receipt(receipt)
            existing = self._container_marker(runtime_path, required=False)
            reused = existing is not None
            if existing is not None:
                self._require_marker_match(existing, expected)
            else:
                self._run(
                    [self.docker_bin, "exec", self.container, "mkdir", "-p", runtime_path],
                    timeout=30,
                    label="debug workspace directory creation",
                )
                self._run(
                    [self.docker_bin, "cp", f"{staged}/.", f"{self.container}:{runtime_path}"],
                    timeout=300,
                    label="debug workspace import",
                )
                uid, gid = self._container_identity()
                self._run(
                    [
                        self.docker_bin,
                        "exec",
                        "-u",
                        "0",
                        self.container,
                        "chown",
                        "-R",
                        f"{uid}:{gid}",
                        runtime_path,
                    ],
                    timeout=60,
                    label="debug workspace ownership normalization",
                )
                imported = self._container_marker(runtime_path, required=True)
                self._require_marker_match(imported or {}, expected)
            result = {
                "backend": selected_backend,
                "binding_id": binding_id,
                "runtime_workspace_path": runtime_path,
                "content_sha256": expected["content_sha256"],
                "source_commit": expected["source_commit"],
                "source_dirty": expected["source_dirty"],
                "receipt_path": str(durable_receipt),
                "container": self.container,
                "reused": reused,
            }
            self._write_private_json(durable_receipt, {**result, "stage_receipt": receipt})
            return result

    def release(
        self,
        *,
        backend: str,
        binding_id: str,
        runtime_workspace_path: str,
        content_sha256: str,
    ) -> dict[str, Any]:
        selected_backend = canonical_workerbee_backend(backend)
        if selected_backend != WORKERBEE_BACKEND_PLATFORM_DEBUG:
            raise WorkerBeeMaterializerError(
                "Only qualified platform-debug imports can be released by this helper."
            )
        self._validate_binding_id(binding_id)
        expected_path = self._runtime_path(binding_id)
        if runtime_workspace_path != expected_path:
            raise WorkerBeeMaterializerError("Runtime workspace path does not match the binding ID.")
        marker = self._container_marker(expected_path, required=True)
        if str((marker or {}).get("content_sha256") or "") != content_sha256:
            raise WorkerBeeMaterializerError("Runtime workspace digest does not match the release request.")
        self._run(
            [self.docker_bin, "exec", self.container, "rm", "-rf", "--", expected_path],
            timeout=60,
            label="debug workspace release",
        )
        return {
            "backend": selected_backend,
            "binding_id": binding_id,
            "runtime_workspace_path": expected_path,
            "released": True,
        }

    def _require_stage_contract(self) -> None:
        if not self.stage_python:
            raise WorkerBeeMaterializerError(
                f"{DEBUG_STAGE_PYTHON_ENV} is required for platform-debug staging."
            )
        if not self.stage_script.is_file():
            raise WorkerBeeMaterializerError(
                f"{DEBUG_STAGE_SCRIPT_ENV} does not point to the WorkerBee staging script."
            )
        if not self.container:
            raise WorkerBeeMaterializerError("Platform-debug container name is empty.")
        root = PurePosixPath(self.import_root)
        if not root.is_absolute() or ".." in root.parts:
            raise WorkerBeeMaterializerError("Platform-debug import root is unsafe.")

    def _runtime_path(self, binding_id: str) -> str:
        return str(PurePosixPath(self.import_root) / binding_id)

    @staticmethod
    def _validate_binding_id(binding_id: str) -> None:
        if not _SAFE_BINDING_ID.fullmatch(binding_id):
            raise WorkerBeeMaterializerError("WorkerBee binding ID is invalid.")

    def _container_marker(self, runtime_path: str, *, required: bool) -> dict[str, Any] | None:
        marker_path = f"{runtime_path}/.workerbee-workspace.json"
        result = self.runner(
            [self.docker_bin, "exec", self.container, "cat", marker_path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            if required:
                detail = (result.stderr or result.stdout or "marker unavailable").strip()
                raise WorkerBeeMaterializerError(
                    f"Imported WorkerBee workspace marker is unavailable: {detail}"
                )
            return None
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise WorkerBeeMaterializerError(
                "Imported WorkerBee workspace marker is invalid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise WorkerBeeMaterializerError(
                "Imported WorkerBee workspace marker is not an object."
            )
        return payload

    def _container_identity(self) -> tuple[str, str]:
        values: list[str] = []
        for flag in ("-u", "-g"):
            result = self.runner(
                [self.docker_bin, "exec", self.container, "id", flag],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            value = str(result.stdout or "").strip()
            if result.returncode != 0 or not value.isdigit():
                detail = (result.stderr or result.stdout or "identity unavailable").strip()
                raise WorkerBeeMaterializerError(
                    f"Unable to determine debug appliance user identity: {detail}"
                )
            values.append(value)
        return values[0], values[1]

    @staticmethod
    def _normalized_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
        required = ("content_sha256", "source_commit", "source_dirty")
        if any(key not in receipt for key in required):
            raise WorkerBeeMaterializerError("WorkerBee staging receipt is incomplete.")
        return {
            "content_sha256": str(receipt["content_sha256"]),
            "source_commit": str(receipt["source_commit"]),
            "source_dirty": bool(receipt["source_dirty"]),
            "owner_project": str(receipt.get("owner_project") or ""),
        }

    @staticmethod
    def _require_marker_match(
        marker: dict[str, Any],
        expected: dict[str, Any],
    ) -> None:
        if str(marker.get("content_sha256") or "") != expected["content_sha256"]:
            raise WorkerBeeMaterializerError("Imported WorkerBee workspace digest differs from staging.")
        if str(marker.get("source_commit") or "") != expected["source_commit"]:
            raise WorkerBeeMaterializerError("Imported WorkerBee workspace commit differs from staging.")
        if bool(marker.get("source_dirty")) != expected["source_dirty"]:
            raise WorkerBeeMaterializerError("Imported WorkerBee dirty state differs from staging.")
        owner = str(marker.get("owner_project") or "")
        if expected["owner_project"] and owner != expected["owner_project"]:
            raise WorkerBeeMaterializerError("Imported WorkerBee workspace owner differs from staging.")

    def _private_temp_root(self) -> Path:
        root = self.state_root / "tmp"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
        return root

    def _stage_environment(self) -> dict[str, str]:
        """Make a source-checkout staging script importable without installing it."""

        env = dict(os.environ)
        try:
            repository_root = self.stage_script.resolve().parents[2]
        except IndexError:
            return env
        source_root = repository_root / "src"
        if not source_root.is_dir():
            return env
        existing = str(env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = (
            f"{source_root}{os.pathsep}{existing}" if existing else str(source_root)
        )
        return env

    def _run(
        self,
        command: list[str],
        *,
        timeout: float,
        label: str,
        env: dict[str, str] | None = None,
    ) -> None:
        result = self.runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **({"env": env} if env is not None else {}),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "command failed").strip()
            raise WorkerBeeMaterializerError(f"{label} failed: {detail}")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerBeeMaterializerError("WorkerBee staging receipt is unreadable.") from exc
        if not isinstance(payload, dict):
            raise WorkerBeeMaterializerError("WorkerBee staging receipt is not an object.")
        return payload

    @staticmethod
    def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Materialize an Agent PBX WorkerBee appliance workspace binding."
    )
    subparsers = parser.add_subparsers(dest="operation", required=True)
    stage = subparsers.add_parser("stage")
    stage.add_argument("--backend", required=True)
    stage.add_argument("--source", type=Path, required=True)
    stage.add_argument("--project", required=True)
    stage.add_argument("--binding-id", required=True)
    stage.add_argument("--include", action="append", default=[])
    stage.add_argument("--json", action="store_true")
    release = subparsers.add_parser("release")
    release.add_argument("--backend", required=True)
    release.add_argument("--binding-id", required=True)
    release.add_argument("--runtime-workspace-path", required=True)
    release.add_argument("--content-sha256", required=True)
    release.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    materializer = WorkerBeeDebugMaterializer()
    try:
        if args.operation == "stage":
            result = materializer.stage(
                backend=args.backend,
                source=args.source,
                project=args.project,
                binding_id=args.binding_id,
                include_paths=args.include,
            )
        else:
            result = materializer.release(
                backend=args.backend,
                binding_id=args.binding_id,
                runtime_workspace_path=args.runtime_workspace_path,
                content_sha256=args.content_sha256,
            )
    except WorkerBeeMaterializerError as exc:
        parser.exit(2, f"agent-pbx-workerbee-workspace: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
