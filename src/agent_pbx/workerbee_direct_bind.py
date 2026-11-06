from __future__ import annotations

from dataclasses import dataclass
import grp
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import stat
import subprocess
import time
from typing import Any, Callable, Mapping

from .workerbee_modes import WORKERBEE_SOURCE_TRUSTED_DIRECT_BIND
from .workerbee_runtime import WorkerBeeRuntimeSelection, canonical_workerbee_backend


TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION = (
    "agent-pbx.workerbee-trusted-direct-bind-helper/v2"
)
TRUSTED_DIRECT_BIND_CANONICAL_SCHEMA = (
    "workerbee.platform-debug-trusted-direct-bind/v1"
)
TRUSTED_DIRECT_BIND_VERIFY_OPERATION = "verify-direct-bind"
TRUSTED_DIRECT_BIND_RELEASE_OPERATION = "release-direct-bind"
TRUSTED_DIRECT_BIND_ADAPTER_CONFORMANCE_BYTES = 2768
TRUSTED_DIRECT_BIND_ADAPTER_CONFORMANCE_SHA256 = (
    "867b2008699280385bedbebaecc463fd8f6fef5f97c72a751c4f1bbb1fddcc55"
)
TRUSTED_DIRECT_BIND_VERIFY_OUTPUT_FIELDS = (
    "schema",
    "action",
    "status",
    "receipt_id",
    "binding_id",
    "workerbee_receipt_schema",
    "workerbee_receipt_phase",
    "workerbee_receipt_sha256",
    "source_identity_sha256",
    "source_dev",
    "source_ino",
    "source_uid",
    "source_gid",
    "source_mode",
    "source_git_commit",
    "source_git_tree",
    "source_git_status_sha256",
    "runtime",
    "backend_id",
    "backend_instance",
    "backend_image_digest",
    "backend_fingerprint",
    "backend_generation",
    "mount_target_sha256",
    "mount_read_only",
    "mount_propagation",
    "mount_nested_count",
    "private_group_uid",
    "private_group_gid",
    "private_group_identity_sha256",
    "path_component_identity_sha256",
    "protected_mask_count",
    "protected_mask_paths_sha256",
    "helper_sha256",
    "helper_dev",
    "helper_ino",
    "helper_uid",
    "helper_gid",
    "helper_mode",
    "generation_identity_sha256",
    "issued_at",
    "expires_at",
    "release_required",
    "adapter_conformance_sha256",
    "adapter_receipt_sha256",
)
TRUSTED_DIRECT_BIND_RELEASE_OUTPUT_FIELDS = (
    "schema",
    "action",
    "status",
    "receipt_id",
    "binding_id",
    "workerbee_receipt_sha256",
    "adapter_receipt_sha256",
    "cleanup_complete",
    "mask_residue_count",
    "adapter_receipt_present",
)
TRUSTED_DIRECT_BIND_RECEIPT_ROOT_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_DIRECT_RECEIPT_ROOT"
)
TRUSTED_DIRECT_BIND_RUNTIME_PREFIX_ENV = (
    "AGENT_PBX_WORKERBEE_TRUSTED_DIRECT_RUNTIME_PREFIX"
)
DEFAULT_TRUSTED_DIRECT_BIND_RUNTIME_PREFIX = "/workspace/direct-bind"
MAX_RECEIPT_BYTES = 128 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def trusted_direct_adapter_receipt_sha256(payload: Mapping[str, Any]) -> str:
    canonical = dict(payload)
    canonical.pop("adapter_receipt_sha256", None)
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class WorkerBeeTrustedDirectBindError(RuntimeError):
    def __init__(self, code: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.remediation = remediation

    def public_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class WorkerBeeTrustedDirectBindEvidence:
    receipt_id: str
    receipt_sha256: str
    adapter_receipt_sha256: str
    capability_evidence_sha256: str
    binding_id: str
    source_root: str
    source_git_commit: str
    source_git_tree: str
    source_git_status_sha256: str
    runtime_workspace_path: str
    mount_target_sha256: str
    helper_sha256: str
    expires_at: float
    verify_payload_json: str


@dataclass(frozen=True, slots=True)
class WorkerBeePrivateGroupIdentity:
    uid: int
    gid: int
    username: str
    group_name: str
    primary_gid_users: tuple[str, ...]
    explicit_members: tuple[str, ...]

    def digest(self) -> str:
        payload = {
            "policy": "owner-private-primary-group-v1",
            "uid": self.uid,
            "gid": self.gid,
            "username": self.username,
            "group_name": self.group_name,
            "primary_gid_users": list(self.primary_gid_users),
            "explicit_members": list(self.explicit_members),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class WorkerBeeTrustedDirectBindVerifier:
    """Resolve helper-created receipts from a private host-owned store."""

    def __init__(
        self,
        *,
        receipt_root: str | Path,
        helper_bin: str | Path,
        runtime_prefix: str = DEFAULT_TRUSTED_DIRECT_BIND_RUNTIME_PREFIX,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.receipt_root = Path(receipt_root).expanduser()
        self.helper_bin = Path(helper_bin).expanduser()
        self.runtime_prefix = self._runtime_path(runtime_prefix, allow_prefix=True)
        self.runner = runner
        self.clock = clock
        self._private_group_identity_cache: WorkerBeePrivateGroupIdentity | None = None

    def admit(
        self,
        *,
        receipt_id: str,
        receipt_sha256: str,
        runtime: WorkerBeeRuntimeSelection,
        source_root: str | Path,
        approved_roots: tuple[str | Path, ...],
        administrator_ceiling: bool,
    ) -> WorkerBeeTrustedDirectBindEvidence:
        if not administrator_ceiling:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_CEILING_REQUIRED",
                "trusted-direct-bind requires an explicit administrator ceiling.",
            )
        source, source_stat = self._admit_source_path(source_root, approved_roots)
        payload, actual_digest = self._load_private_receipt(
            receipt_id=receipt_id,
            expected_sha256=receipt_sha256,
        )
        if payload.get("receipt_id") != str(receipt_id):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH",
                "The host-owned receipt ID does not match the requested receipt.",
            )
        evidence = self._validate_payload(
            payload=payload,
            actual_digest=actual_digest,
            expected_workerbee_receipt_sha256=receipt_sha256,
            runtime=runtime,
            source=source,
            source_stat=source_stat,
        )
        return evidence

    def revalidate(
        self,
        *,
        receipt_id: str,
        receipt_sha256: str,
        runtime: WorkerBeeRuntimeSelection,
        source_root: str | Path,
        approved_roots: tuple[str | Path, ...],
        administrator_ceiling: bool,
        binding_id: str,
    ) -> WorkerBeeTrustedDirectBindEvidence:
        evidence = self.admit(
            receipt_id=receipt_id,
            receipt_sha256=receipt_sha256,
            runtime=runtime,
            source_root=source_root,
            approved_roots=approved_roots,
            administrator_ceiling=administrator_ceiling,
        )
        self._run_helper(
            TRUSTED_DIRECT_BIND_VERIFY_OPERATION,
            evidence=evidence,
            binding_id=binding_id,
            require_cleanup=False,
        )
        return evidence

    def release(
        self,
        *,
        evidence: WorkerBeeTrustedDirectBindEvidence,
        binding_id: str,
    ) -> None:
        self._run_helper(
            TRUSTED_DIRECT_BIND_RELEASE_OPERATION,
            evidence=evidence,
            binding_id=binding_id,
            require_cleanup=True,
        )

    def _admit_source_path(
        self,
        source_root: str | Path,
        approved_roots: tuple[str | Path, ...],
    ) -> tuple[Path, os.stat_result]:
        source_lexical = Path(source_root).expanduser()
        if not source_lexical.is_absolute():
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                "trusted-direct-bind source paths must be absolute.",
            )
        self._require_lexical_real_path(source_lexical, expect_directory=True)
        try:
            source = source_lexical.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                "trusted-direct-bind source path cannot be resolved safely.",
            ) from exc
        roots: list[Path] = []
        for raw_root in approved_roots:
            lexical_root = Path(raw_root).expanduser()
            if not lexical_root.is_absolute():
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_INVALID",
                    "Approved trusted-direct-bind roots must be absolute.",
                )
            self._require_lexical_real_path(lexical_root, expect_directory=True)
            try:
                roots.append(lexical_root.resolve(strict=True))
            except (OSError, RuntimeError) as exc:
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_INVALID",
                    "An approved trusted-direct-bind root is unavailable.",
                ) from exc
        if not roots:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_ROOTS_REQUIRED",
                "trusted-direct-bind requires approved canonical roots.",
            )
        selected_root = next(
            (root for root in roots if source == root or root in source.parents),
            None,
        )
        if selected_root is None:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_ROOT_DENIED",
                "The source checkout is outside the administrator-approved roots.",
            )
        private_group = self._private_group_identity()
        current = selected_root
        relative_parts = source.relative_to(selected_root).parts
        for part in ("", *relative_parts):
            if part:
                current /= part
            details = current.lstat()
            if details.st_uid != os.getuid():
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_OWNER_MISMATCH",
                    "trusted-direct-bind source ancestry has an unexpected owner.",
                )
            if details.st_mode & 0o002:
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_MODE_UNSAFE",
                    "trusted-direct-bind source ancestry is world writable.",
                )
            if details.st_mode & 0o020 and details.st_gid != private_group.gid:
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_MODE_UNSAFE",
                    "Group-writable trusted-direct-bind ancestry is not owned by the daemon private primary group.",
                )
            if not stat.S_ISDIR(details.st_mode):
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                    "trusted-direct-bind source ancestry is not directory-only.",
                )
        return source, source.lstat()

    def _private_group_identity(self) -> WorkerBeePrivateGroupIdentity:
        try:
            uid = os.getuid()
            gid = os.getgid()
            account = pwd.getpwuid(uid)
            group = grp.getgrgid(gid)
            primary_users = tuple(
                sorted({entry.pw_name for entry in pwd.getpwall() if entry.pw_gid == gid})
            )
            explicit_members = tuple(sorted(set(group.gr_mem)))
        except (KeyError, OSError) as exc:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PRIVATE_GROUP_UNPROVEN",
                "The daemon private primary group identity cannot be proven.",
            ) from exc
        identity = WorkerBeePrivateGroupIdentity(
            uid=uid,
            gid=gid,
            username=str(account.pw_name),
            group_name=str(group.gr_name),
            primary_gid_users=primary_users,
            explicit_members=explicit_members,
        )
        if (
            int(account.pw_gid) != gid
            or primary_users != (identity.username,)
            or any(member != identity.username for member in explicit_members)
        ):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PRIVATE_GROUP_UNPROVEN",
                "The writable primary group is shared with another account or explicit member.",
            )
        cached = self._private_group_identity_cache
        if cached is not None and identity != cached:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PRIVATE_GROUP_DRIFT",
                "The daemon private primary group identity changed after admission.",
            )
        if cached is None:
            self._private_group_identity_cache = identity
        return identity

    @staticmethod
    def _require_lexical_real_path(path: Path, *, expect_directory: bool) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current /= part
            try:
                details = current.lstat()
            except (OSError, RuntimeError) as exc:
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                    "trusted-direct-bind lexical path ancestry is unavailable.",
                ) from exc
            if stat.S_ISLNK(details.st_mode):
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_SYMLINK_DENIED",
                    "trusted-direct-bind lexical path ancestry contains a symbolic link.",
                )
            if current != path and not stat.S_ISDIR(details.st_mode):
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                    "trusted-direct-bind lexical ancestry contains a special file.",
                )
        final = path.lstat()
        if expect_directory and not stat.S_ISDIR(final.st_mode):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_PATH_INVALID",
                "trusted-direct-bind path is not a real directory.",
            )

    def _load_private_receipt(
        self,
        *,
        receipt_id: str,
        expected_sha256: str,
    ) -> tuple[dict[str, Any], str]:
        selected_id = str(receipt_id or "").strip()
        selected_digest = str(expected_sha256 or "").strip().lower()
        if not _SAFE_ID.fullmatch(selected_id) or not _SHA256.fullmatch(selected_digest):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "trusted-direct-bind receipt ID or digest is invalid.",
            )
        root = self.receipt_root
        self._require_lexical_real_path(root, expect_directory=True)
        root_stat = root.lstat()
        if root_stat.st_uid != os.getuid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_ROOT_INVALID",
                "trusted-direct-bind receipt root must be owner-only mode 0700.",
            )
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            try:
                fd = os.open(
                    f"{selected_id}.json",
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=root_fd,
                )
            except OSError as exc:
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_UNKNOWN",
                    "The host-owned trusted-direct-bind receipt was not found.",
                ) from exc
            try:
                details = os.fstat(fd)
                if (
                    not stat.S_ISREG(details.st_mode)
                    or details.st_nlink != 1
                    or details.st_uid != os.getuid()
                    or stat.S_IMODE(details.st_mode) != 0o600
                    or details.st_size <= 0
                    or details.st_size > MAX_RECEIPT_BYTES
                ):
                    raise WorkerBeeTrustedDirectBindError(
                        "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                        "The host-owned trusted-direct-bind receipt has unsafe metadata.",
                    )
                chunks: list[bytes] = []
                remaining = details.st_size
                while remaining:
                    chunk = os.read(fd, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                after = os.fstat(fd)
                stable_before = (
                    details.st_dev,
                    details.st_ino,
                    stat.S_IFMT(details.st_mode),
                    details.st_uid,
                    details.st_gid,
                    details.st_nlink,
                    details.st_size,
                    details.st_mtime_ns,
                    details.st_ctime_ns,
                )
                stable_after = (
                    after.st_dev,
                    after.st_ino,
                    stat.S_IFMT(after.st_mode),
                    after.st_uid,
                    after.st_gid,
                    after.st_nlink,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                )
                if len(raw) != details.st_size or stable_after != stable_before:
                    raise WorkerBeeTrustedDirectBindError(
                        "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_CHANGED",
                        "The host-owned trusted-direct-bind receipt changed during read.",
                    )
            finally:
                os.close(fd)
        finally:
            os.close(root_fd)
        actual_digest = hashlib.sha256(raw).hexdigest()
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "The host-owned trusted-direct-bind receipt is invalid JSON.",
            ) from exc
        if not isinstance(payload, dict):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "The host-owned trusted-direct-bind receipt must be an object.",
            )
        return payload, actual_digest

    def _validate_payload(
        self,
        *,
        payload: Mapping[str, Any],
        actual_digest: str,
        expected_workerbee_receipt_sha256: str,
        runtime: WorkerBeeRuntimeSelection,
        source: Path,
        source_stat: os.stat_result,
    ) -> WorkerBeeTrustedDirectBindEvidence:
        if set(payload) != set(TRUSTED_DIRECT_BIND_VERIFY_OUTPUT_FIELDS):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "The host-owned trusted-direct-bind receipt schema is not exact.",
            )
        helper, helper_stat, helper_sha = self._helper_identity()
        runtime_path = self._runtime_path(str(payload["runtime"]))
        if not self._under_runtime_prefix(runtime_path):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RUNTIME_PATH_DENIED",
                "The direct-bind runtime target is outside the approved prefix.",
            )
        private_group = self._private_group_identity()
        expected_source = (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_uid,
            source_stat.st_gid,
            stat.S_IMODE(source_stat.st_mode),
        )
        receipt_source = (
            payload["source_dev"],
            payload["source_ino"],
            payload["source_uid"],
            payload["source_gid"],
            payload["source_mode"],
        )
        expected_helper = (
            helper_stat.st_dev,
            helper_stat.st_ino,
            helper_stat.st_uid,
            helper_stat.st_gid,
            stat.S_IMODE(helper_stat.st_mode),
            helper_sha,
        )
        receipt_helper = (
            payload["helper_dev"],
            payload["helper_ino"],
            payload["helper_uid"],
            payload["helper_gid"],
            payload["helper_mode"],
            payload["helper_sha256"],
        )
        sha_fields = (
            "workerbee_receipt_sha256",
            "source_identity_sha256",
            "source_git_status_sha256",
            "mount_target_sha256",
            "private_group_identity_sha256",
            "path_component_identity_sha256",
            "protected_mask_paths_sha256",
            "helper_sha256",
            "generation_identity_sha256",
            "adapter_conformance_sha256",
            "adapter_receipt_sha256",
        )
        if any(not _SHA256.fullmatch(str(payload[field])) for field in sha_fields):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "The host-owned trusted-direct-bind receipt contains an invalid digest.",
            )
        if payload["adapter_receipt_sha256"] != trusted_direct_adapter_receipt_sha256(
            payload
        ):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH",
                "The host-owned adapter receipt self-digest does not match its canonical flat payload.",
            )
        git_oid = re.compile(r"^[0-9a-f]{40,64}$")
        image_digest = str(payload["backend_image_digest"])
        image_digest_value = image_digest.removeprefix("sha256:")
        try:
            nested_count = int(payload["mount_nested_count"])
            mask_count = int(payload["protected_mask_count"])
            issued_at = float(payload["issued_at"])
            expires_at = float(payload["expires_at"])
        except (TypeError, ValueError) as exc:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_INVALID",
                "The host-owned helper receipt contains invalid numeric evidence.",
            ) from exc
        if (
            payload["schema"] != TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION
            or payload["action"] != TRUSTED_DIRECT_BIND_VERIFY_OPERATION
            or payload["status"] != "accepted"
            or not _SAFE_ID.fullmatch(str(payload["receipt_id"]))
            or not _SAFE_ID.fullmatch(str(payload["binding_id"]))
            or payload["workerbee_receipt_schema"] != TRUSTED_DIRECT_BIND_CANONICAL_SCHEMA
            or payload["workerbee_receipt_phase"] != "prepared"
            or payload["workerbee_receipt_sha256"]
            != expected_workerbee_receipt_sha256
            or canonical_workerbee_backend(payload["backend_id"]) != runtime.backend
            or payload["backend_instance"] != runtime.instance_id
            or payload["backend_fingerprint"] != runtime.fingerprint
            or payload["backend_generation"] != runtime.generation
            or receipt_source != expected_source
            or not git_oid.fullmatch(str(payload["source_git_commit"]))
            or not git_oid.fullmatch(str(payload["source_git_tree"]))
            or not _SHA256.fullmatch(image_digest_value)
            or payload["mount_target_sha256"]
            != hashlib.sha256(runtime_path.encode()).hexdigest()
            or payload["mount_read_only"] is not True
            or payload["mount_propagation"] != "rprivate"
            or nested_count < 0
            or mask_count < 0
            or nested_count != mask_count
            or payload["private_group_uid"] != private_group.uid
            or payload["private_group_gid"] != private_group.gid
            or payload["private_group_identity_sha256"] != private_group.digest()
            or receipt_helper != expected_helper
            or payload["release_required"] is not True
            or payload["adapter_conformance_sha256"]
            != TRUSTED_DIRECT_BIND_ADAPTER_CONFORMANCE_SHA256
        ):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_MISMATCH",
                "The host-owned helper receipt does not match runtime, source, mount, or helper identity.",
            )
        now = self.clock()
        if issued_at > now or expires_at <= now or expires_at - issued_at > 3600:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RECEIPT_STALE",
                "The host-owned helper receipt is stale or has an unsafe lifetime.",
            )
        capability_fields = {
            "adapter_file_sha256": actual_digest,
            "verify_payload": payload,
            "agent_pbx_path_mode_policy": {
                "policy": "owner-private-primary-group-v1",
                "attestation_sha256": private_group.digest(),
            },
        }
        verify_payload_json = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":")
        )
        return WorkerBeeTrustedDirectBindEvidence(
            receipt_id=str(payload["receipt_id"]),
            receipt_sha256=str(payload["workerbee_receipt_sha256"]),
            adapter_receipt_sha256=str(payload["adapter_receipt_sha256"]),
            capability_evidence_sha256=hashlib.sha256(
                json.dumps(capability_fields, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            binding_id=str(payload["binding_id"]),
            source_root=str(source),
            source_git_commit=str(payload["source_git_commit"]),
            source_git_tree=str(payload["source_git_tree"]),
            source_git_status_sha256=str(payload["source_git_status_sha256"]),
            runtime_workspace_path=runtime_path,
            mount_target_sha256=str(payload["mount_target_sha256"]),
            helper_sha256=helper_sha,
            expires_at=expires_at,
            verify_payload_json=verify_payload_json,
        )

    def _helper_identity(self) -> tuple[Path, os.stat_result, str]:
        helper = self.helper_bin
        if not helper.is_absolute():
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_HELPER_INVALID",
                "The trusted-direct-bind helper path must be absolute.",
            )
        self._require_lexical_real_path(helper, expect_directory=False)
        canonical = helper.resolve(strict=True)
        details = canonical.lstat()
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid not in {0, os.getuid()}
            or details.st_mode & 0o022
        ):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_HELPER_INVALID",
                "The trusted-direct-bind helper has unsafe ownership, mode, or type.",
            )
        digest = hashlib.sha256()
        with canonical.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return canonical, details, digest.hexdigest()

    def _runtime_path(self, value: str, *, allow_prefix: bool = False) -> str:
        if not value or "\\" in value or "\x00" in value:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RUNTIME_PATH_DENIED",
                "The direct-bind runtime target is malformed.",
            )
        raw_parts = value.split("/")
        path = PurePosixPath(value)
        if not path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts[1:]):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RUNTIME_PATH_DENIED",
                "The direct-bind runtime target must be canonical and traversal-free.",
            )
        if not allow_prefix and path == PurePosixPath(self.runtime_prefix):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_RUNTIME_PATH_DENIED",
                "The direct-bind runtime target must be below the approved prefix.",
            )
        return path.as_posix()

    def _under_runtime_prefix(self, value: str) -> bool:
        path = PurePosixPath(value)
        prefix = PurePosixPath(self.runtime_prefix)
        return prefix in path.parents

    def _run_helper(
        self,
        operation: str,
        *,
        evidence: WorkerBeeTrustedDirectBindEvidence,
        binding_id: str,
        require_cleanup: bool,
    ) -> None:
        helper, _stat, helper_sha = self._helper_identity()
        if helper_sha != evidence.helper_sha256:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_HELPER_DRIFT",
                "The trusted-direct-bind helper changed after admission.",
            )
        command = [
            str(helper),
            operation,
            "--binding-id",
            binding_id,
            "--receipt-id",
            evidence.receipt_id,
            "--receipt-sha256",
            evidence.receipt_sha256,
        ]
        if operation == TRUSTED_DIRECT_BIND_VERIFY_OPERATION:
            command.extend(("--expires-at", str(evidence.expires_at)))
        result = self.runner(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_HELPER_FAILED",
                f"trusted-direct-bind helper {operation} failed.",
            )
        try:
            payload = json.loads(result.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_HELPER_FAILED",
                f"trusted-direct-bind helper {operation} returned invalid evidence.",
            ) from exc
        if (
            not isinstance(payload, dict)
            or payload.get("receipt_id") != evidence.receipt_id
            or payload.get("binding_id") != binding_id
        ):
            raise WorkerBeeTrustedDirectBindError(
                "WORKERBEE_TRUSTED_DIRECT_BIND_HELPER_FAILED",
                f"trusted-direct-bind helper {operation} returned mismatched evidence.",
            )
        if require_cleanup:
            if (
                set(payload) != set(TRUSTED_DIRECT_BIND_RELEASE_OUTPUT_FIELDS)
                or payload.get("schema") != TRUSTED_DIRECT_BIND_RECEIPT_API_VERSION
                or payload.get("action") != TRUSTED_DIRECT_BIND_RELEASE_OPERATION
                or payload.get("status") != "released"
                or payload.get("workerbee_receipt_sha256") != evidence.receipt_sha256
                or payload.get("adapter_receipt_sha256")
                != evidence.adapter_receipt_sha256
                or payload.get("cleanup_complete") is not True
                or payload.get("mask_residue_count") != 0
                or payload.get("adapter_receipt_present") is not False
            ):
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_RELEASE_INCOMPLETE",
                    "trusted-direct-bind cleanup did not prove zero residue.",
                )
        else:
            expected = json.loads(evidence.verify_payload_json)
            if (
                set(payload) != set(TRUSTED_DIRECT_BIND_VERIFY_OUTPUT_FIELDS)
                or payload != expected
            ):
                raise WorkerBeeTrustedDirectBindError(
                    "WORKERBEE_TRUSTED_DIRECT_BIND_REVALIDATION_FAILED",
                    "trusted-direct-bind operation-time evidence drifted from the admitted receipt.",
                )
