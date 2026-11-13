from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import DURABLE_HELPER_BUNDLE_CONTRACT, HelperReuseMeasurement
from .store import Store


HELPER_EXPORT_SCHEMA = "agent-pbx.durable-helper-export/v1"
HELPER_VALIDATION_SCHEMA = "agent-pbx.durable-helper-validation/v1"
HELPER_INVOCATION_SCHEMA = "agent-pbx.durable-helper-invocation/v1"
HELPER_STATUSES = frozenset(
    {
        "draft",
        "proposed",
        "reviewed",
        "active",
        "deprecated",
        "retired",
        "rejected",
        "quarantined",
    }
)
HELPER_TERMINAL_STATUSES = frozenset({"retired", "rejected", "quarantined"})
HELPER_TRANSITIONS: dict[str, frozenset[str]] = {
    "reviewed": frozenset({"proposed"}),
    "active": frozenset({"reviewed"}),
    "deprecated": frozenset({"active"}),
    "retired": frozenset({"reviewed", "active", "deprecated"}),
    "rejected": frozenset({"draft", "proposed", "reviewed"}),
    "quarantined": frozenset(HELPER_STATUSES - HELPER_TERMINAL_STATUSES),
}
HELPER_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,119}$")
HELPER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_HELPER_BYTES = 1024 * 1024
MAX_HELPER_EXPORT_BYTES = 2 * 1024 * 1024
MAX_HELPER_SCOPES = 32
MAX_HELPER_KB_REFS = 64


class DurableHelperError(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable_file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        stat.S_IMODE(value.st_mode),
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


class DurableHelperRegistry:
    """Private, content-addressed registry for reviewed Python helpers."""

    def __init__(
        self,
        store: Store,
        *,
        root: Path,
        approved_source_roots: Sequence[Path] = (),
        enabled: bool = False,
        measurements_enabled: bool = False,
        python_bin: Path | None = None,
    ) -> None:
        self.store = store
        self.root = Path(root).expanduser().absolute()
        self.approved_source_roots = tuple(
            Path(item).expanduser().absolute() for item in approved_source_roots
        )
        self.enabled = bool(enabled)
        self.measurements_enabled = bool(measurements_enabled)
        self.python_bin = Path(python_bin or sys.executable).expanduser().absolute()

    def propose(
        self,
        *,
        source_path: str,
        name: str,
        scopes: Sequence[str],
        kb_refs: Sequence[str],
        operator_agent_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        normalized_name = str(name or "").strip()
        if not HELPER_NAME_RE.fullmatch(normalized_name):
            raise DurableHelperError("durable helper name is invalid")
        normalized_scopes = self._scopes(scopes)
        normalized_kb_refs = self._kb_refs(kb_refs, require_local=True)
        source, content, identity = self._read_exact_source(source_path)
        object_sha256 = _sha256(content)
        existing = self.store.find_durable_helper_bundle(
            name=normalized_name,
            object_sha256=object_sha256,
        )
        if existing is not None:
            self.store.update_durable_helper_bundle(
                existing["helper_id"],
                event_type="reused",
                operator_agent_id=operator_agent_id,
                event_metadata={"reason": "identical exact-path proposal"},
            )
            return {**existing, "idempotent": True}
        self._prepare_root()
        self._install_object(object_sha256, content)
        helper_id = str(uuid.uuid4())
        provenance = {
            "schema": "agent-pbx.durable-helper-provenance/v1",
            "source_path": str(source),
            "source_dev": identity.st_dev,
            "source_inode": identity.st_ino,
            "source_uid": identity.st_uid,
            "source_gid": identity.st_gid,
            "source_mode": stat.S_IMODE(identity.st_mode),
            "source_size": identity.st_size,
            "proposed_at": time.time(),
        }
        manifest = {
            "schema": DURABLE_HELPER_BUNDLE_CONTRACT.schema,
            "helper_id": helper_id,
            "name": normalized_name,
            "runtime": "python3-isolated",
            "entrypoint": "helper.py",
            "object_sha256": object_sha256,
            "object_bytes": len(content),
            "scopes": normalized_scopes,
            "kb_refs": normalized_kb_refs,
        }
        bundle = self.store.create_durable_helper_bundle(
            helper_id=helper_id,
            name=normalized_name,
            object_sha256=object_sha256,
            object_bytes=len(content),
            source_path=str(source),
            manifest=manifest,
            provenance=provenance,
            scopes=normalized_scopes,
            kb_refs=normalized_kb_refs,
            status="proposed",
            created_by_operator_agent_id=operator_agent_id,
        )
        return {**bundle, "idempotent": False}

    def validate(self, *, helper_id: str, operator_agent_id: str) -> dict[str, Any]:
        self._require_enabled()
        bundle = self._bundle(helper_id)
        if bundle["status"] not in {"proposed", "reviewed"}:
            raise DurableHelperError("helper is not in a validatable state")
        object_path = self._verified_object(bundle)
        self._prepare_root()
        validation_root = self._private_subdir("validation")
        harness = Path(tempfile.mkdtemp(prefix="validate-", dir=validation_root))
        os.chmod(harness, 0o700)
        receipt: dict[str, Any]
        try:
            candidate = harness / "helper.py"
            self._copy_private(object_path, candidate, final_mode=0o400)
            python_path, python_before = self._verified_python_binary()
            command = [
                str(python_path),
                "-I",
                "-S",
                "-c",
                (
                    "from pathlib import Path; "
                    "p=Path('helper.py'); b=p.read_bytes(); "
                    "compile(b, 'helper.py', 'exec')"
                ),
            ]
            passed = False
            reason = "validation failed"
            try:
                completed = subprocess.run(
                    command,
                    cwd=harness,
                    env={
                        "HOME": str(harness),
                        "PATH": "/usr/bin:/bin",
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    timeout=5.0,
                    check=False,
                )
                passed = completed.returncode == 0
                reason = "passed" if passed else "python syntax validation failed"
            except (OSError, subprocess.TimeoutExpired):
                reason = "isolated validator unavailable or timed out"
            receipt = {
                "schema": HELPER_VALIDATION_SCHEMA,
                "helper_id": helper_id,
                "object_sha256": bundle["object_sha256"],
                "validator": "python-compile-isolated/v1",
                "python_identity": {
                    "path": str(python_path),
                    "device": python_before.st_dev,
                    "inode": python_before.st_ino,
                    "uid": python_before.st_uid,
                    "mode": stat.S_IMODE(python_before.st_mode),
                },
                "passed": passed,
                "reason": reason,
                "validated_at": time.time(),
                "executed_helper_code": False,
                "network_authority": False,
                "credential_authority": False,
            }
            python_after = python_path.lstat()
            if _stable_file_identity(python_after) != _stable_file_identity(
                python_before
            ):
                receipt["passed"] = False
                receipt["reason"] = "isolated validator identity changed"
        finally:
            self._remove_validation_harness(harness)
        updated = self.store.update_durable_helper_bundle(
            helper_id,
            validation_receipt=receipt,
            event_type="validation_passed" if receipt["passed"] else "validation_failed",
            operator_agent_id=operator_agent_id,
            event_metadata={
                "object_sha256": bundle["object_sha256"],
                "validator": receipt["validator"],
            },
        )
        if updated is None:
            raise DurableHelperError("helper disappeared during validation")
        return receipt

    def transition(
        self,
        *,
        helper_id: str,
        target_status: str,
        operator_agent_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        target = str(target_status or "").strip().lower()
        bundle = self._bundle(helper_id)
        if target not in HELPER_TRANSITIONS or bundle["status"] not in HELPER_TRANSITIONS[target]:
            raise DurableHelperError("durable helper lifecycle transition is invalid")
        if target == "reviewed":
            receipt = bundle.get("validation_receipt")
            if (
                not isinstance(receipt, Mapping)
                or receipt.get("passed") is not True
                or receipt.get("helper_id") != helper_id
                or receipt.get("object_sha256") != bundle["object_sha256"]
            ):
                raise DurableHelperError("helper has no matching successful validation")
        materialization: dict[str, Any] | None = None
        if target == "active":
            materialization = self._materialize(bundle)
            for other in self.store.list_durable_helper_bundles(status="active", limit=1000):
                if other["helper_id"] == helper_id or other["name"] != bundle["name"]:
                    continue
                self.store.update_durable_helper_bundle(
                    other["helper_id"],
                    status="deprecated",
                    event_type="replaced",
                    operator_agent_id=operator_agent_id,
                    event_metadata={"replacement_helper_id": helper_id},
                )
        if target in HELPER_TERMINAL_STATUSES:
            self._remove_materialization(bundle)
        updated = self.store.update_durable_helper_bundle(
            helper_id,
            status=target,
            reviewed_by_operator_agent_id=(
                operator_agent_id if target == "reviewed" else None
            ),
            activated_by_operator_agent_id=(
                operator_agent_id if target == "active" else None
            ),
            event_type=target,
            operator_agent_id=operator_agent_id,
            event_metadata={
                "object_sha256": bundle["object_sha256"],
                "materialization": materialization,
            },
        )
        if updated is None:
            if materialization is not None:
                self._remove_materialization(bundle)
            raise DurableHelperError("helper disappeared during transition")
        return updated

    def invocation_plan(
        self,
        *,
        helper_id: str,
        scope: str,
        operator_agent_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        bundle = self._bundle(helper_id)
        if bundle["status"] != "active":
            raise DurableHelperError("only active helpers can be invoked")
        normalized_scope = str(scope or "").strip()
        if normalized_scope not in bundle["scopes"]:
            raise DurableHelperError("requested helper scope is not reviewed")
        materialized = self._verified_materialization(bundle)
        output = self._output_directory(helper_id)
        return {
            "schema": HELPER_INVOCATION_SCHEMA,
            "helper_id": helper_id,
            "object_sha256": bundle["object_sha256"],
            "scope": normalized_scope,
            "argv": [
                str(self.python_bin),
                "-I",
                "-S",
                str(materialized),
                "--output-dir",
                str(output),
            ],
            "cwd": str(output),
            "environment": {},
            "shell": False,
            "explicit_invocation_required": True,
            "execution_performed": False,
        }

    def export_bundle(self, helper_id: str) -> dict[str, Any]:
        self._require_enabled()
        bundle = self._bundle(helper_id)
        content = self._read_object(self._verified_object(bundle))
        unsigned = {
            "schema": HELPER_EXPORT_SCHEMA,
            "helper_id": helper_id,
            "name": bundle["name"],
            "object_sha256": bundle["object_sha256"],
            "object_bytes": bundle["object_bytes"],
            "content_base64": base64.b64encode(content).decode("ascii"),
            "manifest": bundle["manifest"],
            "provenance": bundle["provenance"],
            "validation_receipt": bundle.get("validation_receipt"),
            "scopes": bundle["scopes"],
            "kb_refs": bundle["kb_refs"],
            "source_status": bundle["status"],
        }
        return {**unsigned, "bundle_sha256": _sha256(_canonical_json(unsigned))}

    def import_bundle(
        self,
        *,
        bundle: Mapping[str, Any],
        operator_agent_id: str,
    ) -> dict[str, Any]:
        self._require_enabled()
        validated, content = self._validate_export_bundle(bundle)
        existing = self.store.find_durable_helper_bundle(
            name=validated["name"],
            object_sha256=validated["object_sha256"],
        )
        if existing is not None:
            return {**existing, "idempotent": True}
        self._prepare_root()
        self._install_object(validated["object_sha256"], content)
        helper_id = str(uuid.uuid4())
        manifest = {
            **validated["manifest"],
            "helper_id": helper_id,
        }
        record = self.store.create_durable_helper_bundle(
            helper_id=helper_id,
            name=validated["name"],
            object_sha256=validated["object_sha256"],
            object_bytes=validated["object_bytes"],
            source_path=f"bundle:{validated['bundle_sha256']}",
            manifest=manifest,
            provenance={
                "schema": "agent-pbx.durable-helper-import-provenance/v1",
                "source_bundle_sha256": validated["bundle_sha256"],
                "source_helper_id": validated["helper_id"],
                "imported_at": time.time(),
            },
            scopes=validated["scopes"],
            kb_refs=validated["kb_refs"],
            status="proposed",
            created_by_operator_agent_id=operator_agent_id,
        )
        return {**record, "idempotent": False}

    def list(self, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        self._require_enabled()
        if status is not None and status not in HELPER_STATUSES:
            raise DurableHelperError("durable helper status is invalid")
        return self.store.list_durable_helper_bundles(status=status, limit=limit)

    def measurements(self) -> dict[str, Any]:
        self._require_enabled()
        if not self.measurements_enabled:
            return {
                "schema": "agent-pbx.durable-helper-measurements/v1",
                "enabled": False,
                "proposals": 0,
                "activations": 0,
                "reuses": 0,
                "replacements": 0,
                "quarantines": 0,
                "reuse_ratio": None,
                "churn_ratio": None,
            }
        event_types = [
            str(item["event_type"])
            for item in self.store.list_durable_helper_events(limit=2000)
        ]
        measurement = HelperReuseMeasurement(
            enabled=True,
            proposals=event_types.count("proposed"),
            activations=event_types.count("active"),
            reuses=event_types.count("reused"),
            replacements=event_types.count("replaced"),
            quarantines=event_types.count("quarantined"),
        )
        return {
            "schema": "agent-pbx.durable-helper-measurements/v1",
            "enabled": True,
            "proposals": measurement.proposals,
            "activations": measurement.activations,
            "reuses": measurement.reuses,
            "replacements": measurement.replacements,
            "quarantines": measurement.quarantines,
            "reuse_ratio": measurement.reuse_ratio,
            "churn_ratio": measurement.churn_ratio,
        }

    def _validate_export_bundle(
        self,
        bundle: Mapping[str, Any],
    ) -> tuple[dict[str, Any], bytes]:
        expected = {
            "schema", "helper_id", "name", "object_sha256", "object_bytes",
            "content_base64", "manifest", "provenance", "validation_receipt",
            "scopes", "kb_refs", "source_status", "bundle_sha256",
        }
        if not isinstance(bundle, Mapping) or set(bundle) != expected:
            raise DurableHelperError("durable helper export fields are invalid")
        if len(_canonical_json(bundle)) > MAX_HELPER_EXPORT_BYTES:
            raise DurableHelperError("durable helper export exceeds the byte limit")
        if bundle.get("schema") != HELPER_EXPORT_SCHEMA:
            raise DurableHelperError("unsupported durable helper export schema")
        unsigned = {key: bundle[key] for key in expected if key != "bundle_sha256"}
        if bundle.get("bundle_sha256") != _sha256(_canonical_json(unsigned)):
            raise DurableHelperError("durable helper export digest mismatch")
        if not HELPER_NAME_RE.fullmatch(str(bundle.get("name") or "")):
            raise DurableHelperError("durable helper export name is invalid")
        if not SHA256_RE.fullmatch(str(bundle.get("object_sha256") or "")):
            raise DurableHelperError("durable helper export object digest is invalid")
        size = bundle.get("object_bytes")
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or not (1 <= size <= MAX_HELPER_BYTES)
        ):
            raise DurableHelperError("durable helper export size is invalid")
        if not isinstance(bundle.get("content_base64"), str):
            raise DurableHelperError("durable helper export content is malformed")
        try:
            content = base64.b64decode(str(bundle.get("content_base64") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise DurableHelperError("durable helper export content is malformed") from exc
        if len(content) != size or _sha256(content) != bundle["object_sha256"]:
            raise DurableHelperError("durable helper export content drift")
        manifest = bundle.get("manifest")
        manifest_fields = {
            "schema", "helper_id", "name", "runtime", "entrypoint",
            "object_sha256", "object_bytes", "scopes", "kb_refs",
        }
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != manifest_fields
            or manifest.get("schema") != DURABLE_HELPER_BUNDLE_CONTRACT.schema
            or manifest.get("helper_id") != bundle.get("helper_id")
            or manifest.get("runtime") != "python3-isolated"
            or manifest.get("entrypoint") != "helper.py"
            or manifest.get("object_sha256") != bundle["object_sha256"]
            or manifest.get("object_bytes") != size
            or manifest.get("name") != bundle["name"]
        ):
            raise DurableHelperError("durable helper export manifest is invalid")
        try:
            uuid.UUID(str(bundle.get("helper_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DurableHelperError("durable helper export identity is invalid") from exc
        if (
            not isinstance(bundle.get("provenance"), Mapping)
            or (
                bundle.get("validation_receipt") is not None
                and not isinstance(bundle.get("validation_receipt"), Mapping)
            )
            or bundle.get("source_status") not in HELPER_STATUSES
        ):
            raise DurableHelperError("durable helper export evidence is invalid")
        scopes = self._scopes(bundle.get("scopes") or [])
        kb_refs = self._kb_refs(bundle.get("kb_refs") or [], require_local=False)
        if manifest.get("scopes") != scopes or manifest.get("kb_refs") != kb_refs:
            raise DurableHelperError("durable helper export authority drift")
        return {**dict(bundle), "scopes": scopes, "kb_refs": kb_refs}, content

    def _read_exact_source(self, source_path: str) -> tuple[Path, bytes, os.stat_result]:
        raw = Path(str(source_path or ""))
        if not raw.is_absolute() or not self.approved_source_roots:
            raise DurableHelperError("helper proposal requires an approved absolute path")
        for root in self.approved_source_roots:
            self._require_approved_root(root)
        self._reject_symlink_components(raw)
        try:
            resolved = raw.resolve(strict=True)
            roots = tuple(root.resolve(strict=True) for root in self.approved_source_roots)
        except (OSError, RuntimeError) as exc:
            raise DurableHelperError("helper source path cannot be resolved safely") from exc
        matched = next((root for root in roots if resolved.is_relative_to(root)), None)
        if matched is None or resolved == matched:
            raise DurableHelperError("helper source is outside approved proposal roots")
        current = matched
        for part in resolved.relative_to(matched).parts[:-1]:
            current = current / part
            value = current.lstat()
            mode = stat.S_IMODE(value.st_mode)
            if stat.S_ISLNK(value.st_mode):
                raise DurableHelperError("helper source ancestry contains a symlink")
            if value.st_uid != os.getuid() or mode & 0o002:
                raise DurableHelperError("helper source ancestry owner or mode is unsafe")
            if mode & 0o020 and value.st_gid != os.getgid():
                raise DurableHelperError("helper source ancestry group is unsafe")
        before = resolved.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_size < 1
            or before.st_size > MAX_HELPER_BYTES
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise DurableHelperError("helper source must be a bounded, unshared regular file")
        descriptor = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if _stable_file_identity(opened) != _stable_file_identity(before):
                raise DurableHelperError("helper source changed before read")
            chunks: list[bytes] = []
            remaining = MAX_HELPER_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if len(content) > MAX_HELPER_BYTES or (
            _stable_file_identity(after) != _stable_file_identity(opened)
            or _stable_file_identity(resolved.lstat()) != _stable_file_identity(opened)
        ):
            raise DurableHelperError("helper source changed during read")
        return resolved, content, opened

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                value = current.lstat()
            except OSError as exc:
                raise DurableHelperError("helper path component is unavailable") from exc
            if stat.S_ISLNK(value.st_mode):
                raise DurableHelperError("helper path component is a symlink")

    @classmethod
    def _require_approved_root(cls, root: Path) -> None:
        cls._reject_symlink_components(root)
        try:
            value = root.lstat()
        except OSError as exc:
            raise DurableHelperError("helper proposal root is unavailable") from exc
        mode = stat.S_IMODE(value.st_mode)
        if (
            not stat.S_ISDIR(value.st_mode)
            or value.st_uid != os.getuid()
            or mode & 0o002
            or (mode & 0o020 and value.st_gid != os.getgid())
        ):
            raise DurableHelperError("helper proposal root owner or mode is unsafe")

    def _scopes(self, scopes: Sequence[object]) -> list[str]:
        if isinstance(scopes, (str, bytes)):
            raise DurableHelperError("helper scopes must be an explicit list")
        normalized = sorted({str(item).strip() for item in scopes if str(item).strip()})
        if (
            not normalized
            or len(normalized) > MAX_HELPER_SCOPES
            or any(not HELPER_SCOPE_RE.fullmatch(item) for item in normalized)
        ):
            raise DurableHelperError("helper scopes are invalid")
        return normalized

    def _kb_refs(self, refs: Sequence[object], *, require_local: bool) -> list[str]:
        if isinstance(refs, (str, bytes)):
            raise DurableHelperError("helper KB references must be an explicit list")
        normalized = sorted({str(item).strip() for item in refs if str(item).strip()})
        if len(normalized) > MAX_HELPER_KB_REFS or any(len(item) > 120 for item in normalized):
            raise DurableHelperError("helper KB references are invalid")
        if require_local and any(
            self.store.get_operator_kb_entry(item) is None for item in normalized
        ):
            raise DurableHelperError("helper KB reference is not present locally")
        return normalized

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise DurableHelperError("durable helper registry is disabled")

    def _verified_python_binary(self) -> tuple[Path, os.stat_result]:
        try:
            path = self.python_bin.resolve(strict=True)
            value = path.lstat()
            current_python = Path(sys.executable).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise DurableHelperError("isolated validator binary is unavailable") from exc
        mode = stat.S_IMODE(value.st_mode)
        # The default validator is the interpreter already executing Agent PBX.
        # Some supported Python distributions (notably GitHub's hosted
        # toolcache) install that interpreter group-writable.  Rejecting the
        # process's own executable does not add a trust boundary: replacing it
        # would already compromise the running control plane.  Keep the strict
        # no-shared-write rule for every separately configured interpreter.
        uses_current_python = path == current_python
        if (
            not stat.S_ISREG(value.st_mode)
            or (not uses_current_python and mode & 0o022)
            or not mode & 0o100
        ):
            raise DurableHelperError("isolated validator binary identity is unsafe")
        return path, value

    def _bundle(self, helper_id: str) -> dict[str, Any]:
        try:
            normalized = str(uuid.UUID(str(helper_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise DurableHelperError("durable helper id is invalid") from exc
        bundle = self.store.get_durable_helper_bundle(normalized)
        if bundle is None:
            raise DurableHelperError("durable helper not found")
        return bundle

    def _prepare_root(self) -> None:
        if not self.root.exists():
            self.root.mkdir(parents=True, mode=0o700)
        self._require_private_directory(self.root)
        for name in ("objects", "validation", "materialized", "outputs"):
            self._private_subdir(name)

    def _private_subdir(self, name: str) -> Path:
        path = self.root / name
        if not path.exists():
            path.mkdir(mode=0o700)
        self._require_private_directory(path)
        return path

    @staticmethod
    def _require_private_directory(path: Path) -> None:
        value = path.lstat()
        if (
            not stat.S_ISDIR(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o700
        ):
            raise DurableHelperError(f"durable helper directory is unsafe: {path}")

    def _object_path(self, digest: str) -> Path:
        if not SHA256_RE.fullmatch(digest):
            raise DurableHelperError("durable helper object digest is invalid")
        directory = self._private_subdir("objects") / digest[:2]
        if not directory.exists():
            directory.mkdir(mode=0o700)
        self._require_private_directory(directory)
        return directory / digest[2:]

    def _install_object(self, digest: str, content: bytes) -> Path:
        path = self._object_path(digest)
        if path.exists():
            if _sha256(self._read_object(path)) != digest:
                raise DurableHelperError("durable helper object collision")
            return path
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o400)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            if _sha256(self._read_object(path)) != digest:
                raise DurableHelperError("durable helper object race")
        finally:
            temporary.unlink(missing_ok=True)
        self._fsync_dir(path.parent)
        return path

    def _verified_object(self, bundle: Mapping[str, Any]) -> Path:
        self._prepare_root()
        path = self._object_path(str(bundle["object_sha256"]))
        content = self._read_object(path)
        if len(content) != bundle["object_bytes"] or _sha256(content) != bundle["object_sha256"]:
            raise DurableHelperError("durable helper object drift")
        return path

    @staticmethod
    def _read_object(path: Path) -> bytes:
        return DurableHelperRegistry._read_private_file(path, expected_mode=0o400)

    @staticmethod
    def _read_private_file(path: Path, *, expected_mode: int) -> bytes:
        value = path.lstat()
        if (
            not stat.S_ISREG(value.st_mode)
            or stat.S_ISLNK(value.st_mode)
            or value.st_uid != os.getuid()
            or value.st_nlink != 1
            or stat.S_IMODE(value.st_mode) != expected_mode
        ):
            raise DurableHelperError("durable helper object is unsafe")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            opened = os.fstat(descriptor)
            if _stable_file_identity(opened) != _stable_file_identity(value):
                raise DurableHelperError("durable helper object changed before read")
            chunks: list[bytes] = []
            remaining = MAX_HELPER_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if len(content) > MAX_HELPER_BYTES:
            raise DurableHelperError("durable helper object exceeds byte limit")
        if (
            _stable_file_identity(after) != _stable_file_identity(opened)
            or _stable_file_identity(path.lstat()) != _stable_file_identity(opened)
        ):
            raise DurableHelperError("durable helper object changed during read")
        return content

    @staticmethod
    def _copy_private(source: Path, target: Path, *, final_mode: int) -> None:
        content = DurableHelperRegistry._read_object(source)
        descriptor = os.open(
            target,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, final_mode)
        finally:
            os.close(descriptor)

    def _materialize(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        object_path = self._verified_object(bundle)
        materialized_root = self._private_subdir("materialized")
        helper_root = materialized_root / str(bundle["helper_id"])
        if not helper_root.exists():
            helper_root.mkdir(mode=0o700)
        self._require_private_directory(helper_root)
        final = helper_root / str(bundle["object_sha256"])
        if not final.exists():
            temporary = helper_root / f".{bundle['object_sha256']}.{uuid.uuid4().hex}.tmp"
            temporary.mkdir(mode=0o700)
            try:
                self._copy_private(object_path, temporary / "helper.py", final_mode=0o444)
                manifest = _canonical_json(bundle["manifest"]) + b"\n"
                self._write_private(temporary / "manifest.json", manifest, final_mode=0o400)
                self._fsync_dir(temporary)
                os.replace(temporary, final)
                self._fsync_dir(helper_root)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
        helper = self._verified_materialization(bundle)
        output = self._output_directory(str(bundle["helper_id"]))
        return {
            "helper_path": str(helper),
            "output_path": str(output),
            "object_sha256": bundle["object_sha256"],
            "helper_mode": "0444",
            "output_mode": "0700",
        }

    def _verified_materialization(self, bundle: Mapping[str, Any]) -> Path:
        directory = (
            self._private_subdir("materialized")
            / str(bundle["helper_id"])
            / str(bundle["object_sha256"])
        )
        helper = directory / "helper.py"
        manifest = directory / "manifest.json"
        if not directory.exists() or set(item.name for item in directory.iterdir()) != {
            "helper.py", "manifest.json"
        }:
            raise DurableHelperError("durable helper materialization is incomplete")
        helper_value = helper.lstat()
        if (
            not stat.S_ISREG(helper_value.st_mode)
            or stat.S_ISLNK(helper_value.st_mode)
            or stat.S_IMODE(helper_value.st_mode) != 0o444
            or _sha256(self._read_private_file(helper, expected_mode=0o444))
            != bundle["object_sha256"]
        ):
            raise DurableHelperError("durable helper materialization drift")
        manifest_value = manifest.lstat()
        if (
            not stat.S_ISREG(manifest_value.st_mode)
            or stat.S_ISLNK(manifest_value.st_mode)
            or stat.S_IMODE(manifest_value.st_mode) != 0o400
            or self._read_private_file(manifest, expected_mode=0o400)
            != _canonical_json(bundle["manifest"]) + b"\n"
        ):
            raise DurableHelperError("durable helper manifest drift")
        return helper

    def _output_directory(self, helper_id: str) -> Path:
        output = self._private_subdir("outputs") / helper_id
        if not output.exists():
            output.mkdir(mode=0o700)
        self._require_private_directory(output)
        return output

    def _remove_materialization(self, bundle: Mapping[str, Any]) -> None:
        helper_id = str(bundle["helper_id"])
        try:
            uuid.UUID(helper_id)
        except ValueError as exc:
            raise DurableHelperError("refusing malformed materialization identity") from exc
        helper_root = self._private_subdir("materialized") / helper_id
        if not os.path.lexists(helper_root):
            return
        self._require_private_directory(helper_root)
        expected = helper_root / str(bundle["object_sha256"])
        if set(helper_root.iterdir()) != {expected}:
            raise DurableHelperError("refusing unknown materialization residue")
        self._verified_materialization(bundle)
        shutil.rmtree(expected)
        helper_root.rmdir()
        self._fsync_dir(helper_root.parent)

    @staticmethod
    def _write_private(path: Path, content: bytes, *, final_mode: int) -> None:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                view = view[written:]
            os.fsync(descriptor)
            os.fchmod(descriptor, final_mode)
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _remove_validation_harness(self, path: Path) -> None:
        validation_root = self.root / "validation"
        if path.parent != validation_root or not path.name.startswith("validate-"):
            raise DurableHelperError("refusing unsafe validation cleanup")
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or stat.S_ISLNK(value.st_mode):
            raise DurableHelperError("validation harness identity is unsafe")
        shutil.rmtree(path)
