from __future__ import annotations

from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import time
from typing import Any, Iterable, Mapping
import zlib

from .git_worktree import (
    LinkedWorktreeGitAdminPlan,
    LinkedWorktreeQualificationError,
    qualify_linked_worktree_git_admin,
)


BROKER_VERSION = "agent-pbx-linked-git-broker-v3"
BROKER_AUTHOR_NAME = "Agent PBX Operator"
BROKER_AUTHOR_EMAIL = "agent-pbx-operator@localhost.invalid"
BROKER_WRITE_STRATEGY = "tui_root_commit_broker"
DEDICATED_BRANCH_RE = re.compile(
    r"^refs/heads/pbx-operator-[0-9]+/[A-Za-z0-9._/-]+$"
)
UNSAFE_INDEX_EXTENSIONS = {b"link", b"sdir"}
GIT_INDEX_OBJECT_FORMATS = {
    "sha1": ("sha1", 20),
    "sha256": ("sha256", 32),
}
SUPPORTED_GIT_INDEX_VERSIONS = frozenset({2, 3, 4})
GIT_INDEX_EXTENDED_FLAG = 0x4000
GIT_INDEX_NAME_LENGTH_MASK = 0x0FFF
BROKER_GIT_ENV_KEYS = {
    "GIT_INDEX_FILE",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    "GIT_AUTHOR_DATE",
    "GIT_COMMITTER_DATE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}
RAW_BYTE_CONTENT_POLICY = "agent-pbx-raw-byte-no-filter-v1"
RAW_BYTE_CONTENT_POLICY_SHA256 = hashlib.sha256(
    RAW_BYTE_CONTENT_POLICY.encode("utf-8")
).hexdigest()
JOURNAL_ARTIFACT_NAMES = {
    "state.json",
    "state.tmp",
    "original.index",
    "prepared.index",
    "original.ref",
    "original.reflog",
    "objects",
}
JOURNAL_STATE_FIELDS = frozenset(
    {
        "version",
        "transaction_id",
        "phase",
        "old_head",
        "new_head",
        "tree",
        "original_index_sha256",
        "prepared_index_sha256",
        "original_ref_sha256",
        "original_reflog_sha256",
        "updated_ref_sha256",
        "updated_reflog_sha256",
        "rollback_reflog_sha256",
        "object_manifest",
        "expected_object_oids",
        "created_object_oids",
        "installed_object_oids",
        "object_residue_policy",
        "receipt",
    }
)
JOURNAL_PHASES = frozenset(
    {
        "objects_prepared",
        "objects_installing",
        "objects_installed",
        "ready",
        "index_installed",
        "ref_updated",
        "rollback_pending",
        "rolled_back",
        "done",
    }
)
JOURNAL_FULL_INSTALL_PHASES = frozenset(
    {
        "objects_installed",
        "ready",
        "index_installed",
        "ref_updated",
        "rollback_pending",
        "done",
    }
)
OBJECT_RESIDUE_POLICY = (
    "approved-content immutable objects may remain unreachable for normal Git GC"
)


class LinkedGitBrokerError(RuntimeError):
    """The root-approved linked-Git transaction is not safe to continue."""


class LinkedGitBrokerRestored(LinkedGitBrokerError):
    """Recovery restored or confirmed the exact original repository state."""

    def __init__(self, message: str, *, object_oids: Iterable[str] = ()) -> None:
        self.object_oids = tuple(str(oid) for oid in object_oids)
        super().__init__(message)


@dataclass(frozen=True)
class BrokerConfigIdentity:
    label: str
    path: str
    state: str
    sha256: str | None
    uid: int | None
    mode: int | None


@dataclass(frozen=True)
class BrokerPathIdentity:
    path: str
    state: str
    mode: str
    raw_sha256: str | None
    raw_blob_oid: str | None
    index_mode: str | None
    index_oid: str | None
    content_policy_sha256: str
    parent_chain_sha256: str
    st_dev: int | None
    st_ino: int | None
    st_uid: int | None
    st_nlink: int | None
    st_size: int | None
    st_mtime_ns: int | None
    st_ctime_ns: int | None


@dataclass(frozen=True)
class LinkedGitCommitReceipt:
    version: str
    transaction_id: str
    agent_id: str
    approved_roots: tuple[str, ...]
    codex_home: str | None
    codex_config_identities: tuple[BrokerConfigIdentity, ...]
    git_config_sha256: str
    worktree_root: str
    git_dir: str
    common_dir: str
    object_dir: str
    branch_ref: str
    branch: str
    write_strategy: str
    grant_scope: str
    base_permission_profile: str
    permission_profile: str
    upstream: str
    upstream_oid: str
    head: str
    head_tree: str
    index_tree: str
    approved_stage_paths: tuple[str, ...]
    index_sha256: str
    branch_ref_sha256: str
    branch_reflog_sha256: str
    path_identities: tuple[BrokerPathIdentity, ...]
    commit_subject: str
    author_name: str
    author_email: str
    authored_at: int
    qualified_git_admin_paths: tuple[str, ...]

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LinkedGitCommitResult:
    transaction_id: str
    status: str
    old_head: str
    new_head: str
    branch_ref: str
    committed_paths: tuple[str, ...]
    idempotent: bool = False

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PendingLinkedGitCommit:
    receipt: LinkedGitCommitReceipt
    phase: str


def load_pending_linked_git_commits(
    journal_root: str | Path,
) -> tuple[PendingLinkedGitCommit, ...]:
    """Load exact recoverable receipts from the fixed local journal root."""

    root = Path(journal_root).expanduser()
    if not root.is_absolute() or root != Path(os.path.abspath(root)):
        raise LinkedGitBrokerError("broker journal root must be one canonical absolute path")
    if not root.exists():
        return ()
    _require_no_symlink_components(root)
    _BrokerJournal._validate_directory(root, mode=0o700, label="journal root")
    pending: list[PendingLinkedGitCommit] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        metadata = child.lstat()
        if re.fullmatch(r"[0-9a-f]{64}\.lock", child.name):
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise LinkedGitBrokerError(
                    f"unsafe broker journal lock entry: {child}"
                )
            continue
        if (
            not re.fullmatch(r"[0-9a-f]{64}", child.name)
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise LinkedGitBrokerError(f"unexpected broker journal-root entry: {child}")
        journal = _BrokerJournal(root, child.name)
        journal.acquire_lock()
        try:
            state = journal.read_state()
            if state is None:
                raise LinkedGitBrokerError(
                    f"broker transaction has no recoverable state: {child.name}"
                )
            receipt_value = state.get("receipt")
            if not isinstance(receipt_value, Mapping):
                raise LinkedGitBrokerError("broker pending receipt is malformed")
            receipt = _receipt_from_public_dict(receipt_value)
            if receipt.transaction_id != child.name:
                raise LinkedGitBrokerError("broker pending transaction directory mismatch")
            _validate_journal_state(receipt, state, journal)
            phase = str(state.get("phase") or "")
            if phase not in {"done", "rolled_back"}:
                pending.append(PendingLinkedGitCommit(receipt=receipt, phase=phase))
        finally:
            journal.release_lock()
    return tuple(pending)


_RECEIPT_FIELDS = {
    "version",
    "transaction_id",
    "agent_id",
    "approved_roots",
    "codex_home",
    "codex_config_identities",
    "git_config_sha256",
    "worktree_root",
    "git_dir",
    "common_dir",
    "object_dir",
    "branch_ref",
    "branch",
    "write_strategy",
    "grant_scope",
    "base_permission_profile",
    "permission_profile",
    "upstream",
    "upstream_oid",
    "head",
    "head_tree",
    "index_tree",
    "approved_stage_paths",
    "index_sha256",
    "branch_ref_sha256",
    "branch_reflog_sha256",
    "path_identities",
    "commit_subject",
    "author_name",
    "author_email",
    "authored_at",
    "qualified_git_admin_paths",
}
_CONFIG_IDENTITY_FIELDS = {"label", "path", "state", "sha256", "uid", "mode"}
_PATH_IDENTITY_FIELDS = {
    "path",
    "state",
    "mode",
    "raw_sha256",
    "raw_blob_oid",
    "index_mode",
    "index_oid",
    "content_policy_sha256",
    "parent_chain_sha256",
    "st_dev",
    "st_ino",
    "st_uid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
}


def _receipt_from_public_dict(value: Mapping[str, Any]) -> LinkedGitCommitReceipt:
    """Rebuild one immutable receipt from a journal with strict schema checks."""

    if set(value) != _RECEIPT_FIELDS:
        missing = sorted(_RECEIPT_FIELDS - set(value))
        extra = sorted(set(value) - _RECEIPT_FIELDS)
        raise LinkedGitBrokerError(
            f"broker pending receipt schema mismatch; missing={missing}, extra={extra}"
        )

    version = _receipt_text(value, "version")
    if version != BROKER_VERSION:
        raise LinkedGitBrokerError("broker pending receipt version mismatch")
    transaction_id = _receipt_hex(value, "transaction_id", exact=64)
    agent_id = _receipt_text(value, "agent_id", reject_controls=True).strip()
    if not agent_id:
        raise LinkedGitBrokerError("broker pending receipt has an empty agent id")
    approved_roots = _receipt_string_tuple(value, "approved_roots", nonempty=True)
    for root in approved_roots:
        if not Path(root).is_absolute():
            raise LinkedGitBrokerError("broker pending approved roots must be absolute")
    codex_home_value = value.get("codex_home")
    if codex_home_value is not None and not isinstance(codex_home_value, str):
        raise LinkedGitBrokerError("broker pending codex_home has the wrong type")
    if isinstance(codex_home_value, str) and (
        not Path(codex_home_value).is_absolute() or "\0" in codex_home_value
    ):
        raise LinkedGitBrokerError("broker pending codex_home is not an absolute path")

    config_values = value.get("codex_config_identities")
    if not isinstance(config_values, list):
        raise LinkedGitBrokerError("broker pending config identities must be a list")
    config_identities: list[BrokerConfigIdentity] = []
    for item in config_values:
        if not isinstance(item, Mapping) or set(item) != _CONFIG_IDENTITY_FIELDS:
            raise LinkedGitBrokerError("broker pending config identity schema mismatch")
        state = _mapping_text(item, "state")
        if state not in {"present", "absent"}:
            raise LinkedGitBrokerError("broker pending config identity state is invalid")
        sha256 = _mapping_optional_hex(item, "sha256", exact=64)
        uid = _mapping_optional_int(item, "uid")
        mode = _mapping_optional_int(item, "mode")
        if state == "present" and (sha256 is None or uid is None or mode is None):
            raise LinkedGitBrokerError("present broker config identity is incomplete")
        if state == "absent" and any(
            item_value is not None for item_value in (sha256, uid, mode)
        ):
            raise LinkedGitBrokerError("absent broker config identity carries metadata")
        config_identities.append(
            BrokerConfigIdentity(
                label=_mapping_text(item, "label", reject_controls=True),
                path=_mapping_text(item, "path"),
                state=state,
                sha256=sha256,
                uid=uid,
                mode=mode,
            )
        )

    path_values = value.get("path_identities")
    if not isinstance(path_values, list):
        raise LinkedGitBrokerError("broker pending path identities must be a list")
    path_identities: list[BrokerPathIdentity] = []
    for item in path_values:
        if not isinstance(item, Mapping) or set(item) != _PATH_IDENTITY_FIELDS:
            raise LinkedGitBrokerError("broker pending path identity schema mismatch")
        path = _mapping_text(item, "path")
        _validate_relative_leaf(path)
        state = _mapping_text(item, "state")
        if state not in {"file", "symlink", "deleted"}:
            raise LinkedGitBrokerError("broker pending path identity state is invalid")
        raw_sha256 = _mapping_optional_hex(item, "raw_sha256", exact=64)
        raw_blob_oid = _mapping_optional_git_oid(item, "raw_blob_oid")
        index_oid = _mapping_optional_git_oid(item, "index_oid")
        content_policy_sha256 = _mapping_hex(
            item, "content_policy_sha256", exact=64
        )
        if content_policy_sha256 != RAW_BYTE_CONTENT_POLICY_SHA256:
            raise LinkedGitBrokerError("broker pending content policy is unsupported")
        mode = _mapping_text(item, "mode")
        index_mode_value = item.get("index_mode")
        if index_mode_value is not None and not isinstance(index_mode_value, str):
            raise LinkedGitBrokerError("broker pending index mode has the wrong type")
        if state == "deleted" and (raw_sha256 is not None or raw_blob_oid is not None):
            raise LinkedGitBrokerError("deleted broker path identity carries raw bytes")
        if state != "deleted" and (raw_sha256 is None or raw_blob_oid is None):
            raise LinkedGitBrokerError("broker path identity is missing raw-byte identity")
        parent_chain_sha256 = _mapping_hex(
            item, "parent_chain_sha256", exact=64
        )
        stat_values = {
            key: _mapping_optional_int(item, key)
            for key in (
                "st_dev",
                "st_ino",
                "st_uid",
                "st_nlink",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        }
        if state == "deleted" and any(
            stat_value is not None for stat_value in stat_values.values()
        ):
            raise LinkedGitBrokerError("deleted broker path carries file identity")
        if state != "deleted" and any(
            stat_value is None for stat_value in stat_values.values()
        ):
            raise LinkedGitBrokerError("broker path is missing file identity")
        path_identities.append(
            BrokerPathIdentity(
                path=path,
                state=state,
                mode=mode,
                raw_sha256=raw_sha256,
                raw_blob_oid=raw_blob_oid,
                index_mode=index_mode_value,
                index_oid=index_oid,
                content_policy_sha256=content_policy_sha256,
                parent_chain_sha256=parent_chain_sha256,
                **stat_values,
            )
        )

    approved_stage_paths = _receipt_string_tuple(
        value, "approved_stage_paths", nonempty=True
    )
    if tuple(sorted(approved_stage_paths)) != approved_stage_paths or len(
        approved_stage_paths
    ) != len(set(approved_stage_paths)):
        raise LinkedGitBrokerError("broker pending approved paths are not canonical")
    for path in approved_stage_paths:
        _validate_relative_leaf(path)
    if tuple(identity.path for identity in path_identities) != approved_stage_paths:
        raise LinkedGitBrokerError("broker pending path identities do not match approval")

    qualified_paths = _receipt_string_tuple(
        value, "qualified_git_admin_paths", nonempty=True
    )
    if len(qualified_paths) != 4 or any(
        not Path(path).is_absolute() for path in qualified_paths
    ):
        raise LinkedGitBrokerError("broker pending Git-admin paths are invalid")

    authored_at_value = value.get("authored_at")
    if type(authored_at_value) is not int or authored_at_value <= 0:
        raise LinkedGitBrokerError("broker pending authored_at has the wrong type")
    commit_subject = _receipt_text(value, "commit_subject", reject_controls=True)
    if commit_subject != _commit_subject(agent_id):
        raise LinkedGitBrokerError("broker pending commit subject is not canonical")
    author_name = _receipt_text(value, "author_name", reject_controls=True)
    author_email = _receipt_text(value, "author_email", reject_controls=True)
    if author_name != BROKER_AUTHOR_NAME or author_email != BROKER_AUTHOR_EMAIL:
        raise LinkedGitBrokerError("broker pending author identity is not canonical")

    receipt = LinkedGitCommitReceipt(
        version=version,
        transaction_id=transaction_id,
        agent_id=agent_id,
        approved_roots=approved_roots,
        codex_home=codex_home_value,
        codex_config_identities=tuple(config_identities),
        git_config_sha256=_receipt_hex(value, "git_config_sha256", exact=64),
        worktree_root=_receipt_absolute_path(value, "worktree_root"),
        git_dir=_receipt_absolute_path(value, "git_dir"),
        common_dir=_receipt_absolute_path(value, "common_dir"),
        object_dir=_receipt_absolute_path(value, "object_dir"),
        branch_ref=_receipt_text(value, "branch_ref", reject_controls=True),
        branch=_receipt_text(value, "branch", reject_controls=True),
        write_strategy=_receipt_text(
            value, "write_strategy", reject_controls=True
        ),
        grant_scope=_receipt_text(value, "grant_scope", reject_controls=True),
        base_permission_profile=_receipt_text(
            value, "base_permission_profile", reject_controls=True
        ),
        permission_profile=_receipt_text(
            value, "permission_profile", reject_controls=True
        ),
        upstream=_receipt_text(value, "upstream", reject_controls=True),
        upstream_oid=_receipt_git_oid(value, "upstream_oid"),
        head=_receipt_git_oid(value, "head"),
        head_tree=_receipt_git_oid(value, "head_tree"),
        index_tree=_receipt_git_oid(value, "index_tree"),
        approved_stage_paths=approved_stage_paths,
        index_sha256=_receipt_hex(value, "index_sha256", exact=64),
        branch_ref_sha256=_receipt_hex(value, "branch_ref_sha256", exact=64),
        branch_reflog_sha256=_receipt_hex(
            value, "branch_reflog_sha256", exact=64
        ),
        path_identities=tuple(path_identities),
        commit_subject=commit_subject,
        author_name=author_name,
        author_email=author_email,
        authored_at=authored_at_value,
        qualified_git_admin_paths=qualified_paths,
    )
    if (
        receipt.write_strategy != BROKER_WRITE_STRATEGY
        or receipt.grant_scope != BROKER_WRITE_STRATEGY
        or not receipt.base_permission_profile
        or not receipt.permission_profile
    ):
        raise LinkedGitBrokerError("broker pending strategy/profile identity is invalid")
    payload = {
        key: item
        for key, item in receipt.public_dict().items()
        if key != "transaction_id"
    }
    if _sha256(_canonical_json(payload)) != receipt.transaction_id:
        raise LinkedGitBrokerError("broker pending transaction identity is invalid")
    return receipt


def _receipt_text(
    value: Mapping[str, Any],
    key: str,
    *,
    reject_controls: bool = False,
) -> str:
    return _mapping_text(value, key, reject_controls=reject_controls)


def _mapping_text(
    value: Mapping[str, Any],
    key: str,
    *,
    reject_controls: bool = False,
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or "\0" in item:
        raise LinkedGitBrokerError(f"broker pending {key} has the wrong type")
    if reject_controls and any(ord(character) < 32 or ord(character) == 127 for character in item):
        raise LinkedGitBrokerError(f"broker pending {key} contains control characters")
    return item


def _receipt_string_tuple(
    value: Mapping[str, Any], key: str, *, nonempty: bool
) -> tuple[str, ...]:
    item = value.get(key)
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise LinkedGitBrokerError(f"broker pending {key} must be a string list")
    if nonempty and not item:
        raise LinkedGitBrokerError(f"broker pending {key} cannot be empty")
    if any("\0" in entry for entry in item):
        raise LinkedGitBrokerError(f"broker pending {key} contains NUL")
    return tuple(item)


def _mapping_optional_int(value: Mapping[str, Any], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if type(item) is not int or item < 0:
        raise LinkedGitBrokerError(f"broker pending {key} has the wrong type")
    return item


def _mapping_hex(value: Mapping[str, Any], key: str, *, exact: int) -> str:
    item = _mapping_text(value, key)
    if not re.fullmatch(rf"[0-9a-f]{{{exact}}}", item):
        raise LinkedGitBrokerError(f"broker pending {key} is not a valid digest")
    return item


def _receipt_hex(value: Mapping[str, Any], key: str, *, exact: int) -> str:
    return _mapping_hex(value, key, exact=exact)


def _mapping_optional_hex(
    value: Mapping[str, Any], key: str, *, exact: int
) -> str | None:
    if value.get(key) is None:
        return None
    return _mapping_hex(value, key, exact=exact)


def _mapping_git_oid(value: Mapping[str, Any], key: str) -> str:
    item = _mapping_text(value, key)
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", item):
        raise LinkedGitBrokerError(f"broker pending {key} is not a Git object id")
    return item


def _receipt_git_oid(value: Mapping[str, Any], key: str) -> str:
    return _mapping_git_oid(value, key)


def _mapping_optional_git_oid(value: Mapping[str, Any], key: str) -> str | None:
    if value.get(key) is None:
        return None
    return _mapping_git_oid(value, key)


def _receipt_absolute_path(value: Mapping[str, Any], key: str) -> str:
    item = _receipt_text(value, key)
    if not Path(item).is_absolute():
        raise LinkedGitBrokerError(f"broker pending {key} must be absolute")
    return item


def list_linked_git_commit_candidates(
    *,
    work_root: str | Path,
    approved_roots: Iterable[Path],
    proposed_paths: Iterable[str],
    codex_home: Path | None = None,
) -> tuple[str, ...]:
    """Qualify an explicit root-provided list without repository-wide discovery."""

    plan, _, _ = _qualify_authority(
        work_root=work_root,
        approved_roots=approved_roots,
        codex_home=codex_home,
    )
    _require_broker_topology(plan)
    _require_plain_clean_index(plan)
    paths = _normalize_stage_paths(proposed_paths)
    for path in paths:
        _path_identity(plan, path)
    return paths


def prepare_linked_git_commit(
    *,
    agent_id: str,
    work_root: str | Path,
    approved_roots: Iterable[Path],
    approved_stage_paths: Iterable[str],
    codex_home: Path | None = None,
    authored_at: int | None = None,
    commit_subject: str | None = None,
) -> LinkedGitCommitReceipt:
    """Capture one immutable receipt while observing approved leaves only."""

    raw_agent_id = str(agent_id or "")
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_agent_id):
        raise LinkedGitBrokerError("broker agent id cannot contain control characters")
    normalized_agent_id = raw_agent_id.strip()
    if not normalized_agent_id:
        raise LinkedGitBrokerError("broker transaction requires an exact fork agent id")
    plan, roots, normalized_codex_home = _qualify_authority(
        work_root=work_root,
        approved_roots=approved_roots,
        codex_home=codex_home,
    )
    _require_broker_topology(plan)
    _require_plain_clean_index(plan)
    stage_paths = _normalize_stage_paths(approved_stage_paths)
    identities = tuple(_path_identity(plan, path) for path in stage_paths)

    root = plan.worktree_root
    head = _git_text(root, "rev-parse", "HEAD")
    head_tree = _git_text(root, "rev-parse", "HEAD^{tree}")
    index_tree = head_tree
    branch_ref = _git_text(root, "symbolic-ref", "-q", "HEAD")
    if branch_ref != plan.branch_ref:
        raise LinkedGitBrokerError("active branch changed during broker qualification")
    branch = branch_ref.removeprefix("refs/heads/")
    upstream = _git_text(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    if not upstream:
        raise LinkedGitBrokerError("linked Git broker requires one configured upstream")
    upstream_oid = _git_text(root, "rev-parse", "@{upstream}")

    index_path = plan.git_dir / "index"
    ref_path = plan.common_dir.joinpath(*branch_ref.split("/"))
    reflog_path = plan.common_dir.joinpath("logs", *branch_ref.split("/"))
    timestamp = int(time.time()) if authored_at is None else int(authored_at)
    config_identities = _codex_config_identities(normalized_codex_home)
    git_config_sha256 = _git_config_sha256(root)
    payload: dict[str, Any] = {
        "version": BROKER_VERSION,
        "agent_id": normalized_agent_id,
        "approved_roots": tuple(str(path) for path in roots),
        "codex_home": (
            str(normalized_codex_home) if normalized_codex_home is not None else None
        ),
        "codex_config_identities": tuple(
            asdict(identity) for identity in config_identities
        ),
        "git_config_sha256": git_config_sha256,
        "worktree_root": str(root),
        "git_dir": str(plan.git_dir),
        "common_dir": str(plan.common_dir),
        "object_dir": str(plan.object_dir),
        "branch_ref": branch_ref,
        "branch": branch,
        "write_strategy": plan.write_strategy,
        "grant_scope": "tui_root_commit_broker",
        "base_permission_profile": plan.base_permission_profile,
        "permission_profile": plan.permission_profile,
        "upstream": upstream,
        "upstream_oid": upstream_oid,
        "head": head,
        "head_tree": head_tree,
        "index_tree": index_tree,
        "approved_stage_paths": stage_paths,
        "index_sha256": _sha256(_read_regular_bytes(index_path, "Git index")),
        "branch_ref_sha256": _sha256(
            _read_regular_bytes(ref_path, "active branch ref")
        ),
        "branch_reflog_sha256": _sha256(
            _read_regular_bytes(reflog_path, "active branch reflog")
        ),
        "path_identities": tuple(asdict(identity) for identity in identities),
        "commit_subject": _normalized_commit_subject(
            commit_subject,
            agent_id=normalized_agent_id,
        ),
        "author_name": BROKER_AUTHOR_NAME,
        "author_email": BROKER_AUTHOR_EMAIL,
        "authored_at": timestamp,
        "qualified_git_admin_paths": tuple(
            str(path) for path in plan.writable_paths
        ),
    }
    transaction_id = _sha256(_canonical_json(payload))
    return LinkedGitCommitReceipt(
        transaction_id=transaction_id,
        codex_config_identities=config_identities,
        path_identities=identities,
        **{
            key: value
            for key, value in payload.items()
            if key not in {"codex_config_identities", "path_identities"}
        },
    )


def execute_linked_git_commit(
    receipt: LinkedGitCommitReceipt,
    *,
    approved_roots: Iterable[Path],
    journal_root: str | Path,
    codex_home: Path | None = None,
    fault_at: str | None = None,
    _test_crash_at: str | None = None,
) -> LinkedGitCommitResult:
    """Execute one exact TUI-approved transaction without shell or hooks."""

    roots, normalized_codex_home = _validate_authority_inputs(
        receipt,
        approved_roots=approved_roots,
        codex_home=codex_home,
    )
    journal = _BrokerJournal(Path(journal_root), receipt.transaction_id)
    journal.ensure_private_root()
    journal.acquire_lock()
    try:
        state = journal.read_state()
        if state is not None:
            recovered = _recover_or_resume(
                receipt,
                state,
                journal=journal,
                approved_roots=roots,
                codex_home=normalized_codex_home,
            )
            if recovered is not None:
                return recovered
        elif journal.directory.exists():
            revalidated = _capture_again(receipt, roots, normalized_codex_home)
            if revalidated != receipt:
                raise LinkedGitBrokerError(
                    "state-less broker scratch exists and approval has drifted"
                )
            journal.remove_unstarted_workspace()

        revalidated = _capture_again(receipt, roots, normalized_codex_home)
        if revalidated != receipt:
            raise LinkedGitBrokerError(
                "linked Git broker approval receipt drifted before mutation"
            )
        plan = _qualified_plan(receipt, roots, normalized_codex_home)
        return _execute_prevalidated(
            receipt,
            plan,
            journal=journal,
            approved_roots=roots,
            codex_home=normalized_codex_home,
            fault_at=fault_at,
            test_crash_at=_test_crash_at,
        )
    finally:
        journal.release_lock()


def recover_linked_git_commit(
    receipt: LinkedGitCommitReceipt,
    *,
    approved_roots: Iterable[Path],
    journal_root: str | Path,
    codex_home: Path | None = None,
) -> LinkedGitCommitResult | None:
    """Complete or restore a crash only from exact ref/index identities."""

    roots, normalized_codex_home = _validate_authority_inputs(
        receipt,
        approved_roots=approved_roots,
        codex_home=codex_home,
    )
    journal = _BrokerJournal(Path(journal_root), receipt.transaction_id)
    journal.ensure_private_root()
    journal.acquire_lock()
    try:
        state = journal.read_state()
        if state is None:
            return None
        return _recover_or_resume(
            receipt,
            state,
            journal=journal,
            approved_roots=roots,
            codex_home=normalized_codex_home,
        )
    finally:
        journal.release_lock()


def _execute_prevalidated(
    receipt: LinkedGitCommitReceipt,
    plan: LinkedWorktreeGitAdminPlan,
    *,
    journal: "_BrokerJournal",
    approved_roots: tuple[Path, ...],
    codex_home: Path | None,
    fault_at: str | None,
    test_crash_at: str | None,
) -> LinkedGitCommitResult:
    root = plan.worktree_root
    index_path = plan.git_dir / "index"
    ref_path = plan.common_dir.joinpath(*receipt.branch_ref.split("/"))
    reflog_path = plan.common_dir.joinpath(
        "logs", *receipt.branch_ref.split("/")
    )
    journal.prepare_workspace()
    _copy_private_file(index_path, journal.original_index)
    _copy_private_file(ref_path, journal.original_ref)
    _copy_private_file(reflog_path, journal.original_reflog)
    _copy_private_file(index_path, journal.prepared_index)
    _fsync_directory(journal.directory)

    quarantine_env = {
        "GIT_INDEX_FILE": str(journal.prepared_index),
        "GIT_OBJECT_DIRECTORY": str(journal.object_quarantine),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(plan.object_dir),
    }
    identities = {identity.path: identity for identity in receipt.path_identities}
    for relative in receipt.approved_stage_paths:
        identity = identities[relative]
        if identity.state == "deleted":
            _git_run(
                root,
                "update-index",
                "--force-remove",
                "--",
                relative,
                env=quarantine_env,
            )
            continue
        raw = _identity_bytes(root, identity)
        oid = identity.raw_blob_oid
        if not oid:
            raise LinkedGitBrokerError(f"approved blob identity is absent for {relative}")
        index_info = (
            f"{identity.mode} {oid}\t".encode("ascii")
            + relative.encode("utf-8")
            + b"\0"
        )
        _git_run(
            root,
            "update-index",
            "-z",
            "--index-info",
            input_bytes=index_info,
            env=quarantine_env,
        )

    # This is the last approval check before any object creation. The private
    # index edits above affect only journal bytes.
    if _capture_again(receipt, approved_roots, codex_home) != receipt:
        raise LinkedGitBrokerError("linked Git broker detected post-approval drift")
    for relative in receipt.approved_stage_paths:
        identity = identities[relative]
        if identity.state == "deleted":
            continue
        raw = _identity_bytes(root, identity)
        oid = _git_text(
            root,
            "hash-object",
            "-w",
            "--no-filters",
            "--stdin",
            input_bytes=raw,
            env=quarantine_env,
        )
        if oid != identity.raw_blob_oid:
            raise LinkedGitBrokerError(
                f"approved blob identity changed for {relative}"
            )

    prepared_tree = _git_text(root, "write-tree", env=quarantine_env)
    changed_entries = _tree_changed_paths(
        root,
        receipt.head_tree,
        prepared_tree,
        env=quarantine_env,
    )
    if changed_entries != receipt.approved_stage_paths:
        raise LinkedGitBrokerError(
            "prepared index tree differs outside the explicitly approved paths"
        )
    prepared_index_sha256 = _sha256(
        _read_regular_bytes(journal.prepared_index, "prepared broker index")
    )
    os.chmod(journal.prepared_index, 0o600)
    commit_env = {
        **quarantine_env,
        **_fixed_identity_env(receipt),
    }
    new_head = _git_text(
        root,
        "commit-tree",
        prepared_tree,
        "-p",
        receipt.head,
        input_bytes=(receipt.commit_subject + "\n").encode("utf-8"),
        env=commit_env,
    )
    message = f"agent-pbx broker {receipt.transaction_id[:12]}"
    expected_ref = f"{new_head}\n".encode("ascii")
    original_reflog = _read_regular_bytes(
        journal.original_reflog, "journal original reflog"
    )
    expected_reflog = original_reflog + _reflog_line(
        receipt.head,
        new_head,
        receipt,
        message,
    )
    rollback_message = f"agent-pbx broker rollback {receipt.transaction_id[:12]}"
    rollback_reflog = expected_reflog + _reflog_line(
        new_head,
        receipt.head,
        receipt,
        rollback_message,
    )
    manifest = _object_manifest(
        journal=journal,
        receipt=receipt,
        prepared_tree=prepared_tree,
        new_head=new_head,
        root=root,
        quarantine_env=quarantine_env,
    )
    _fsync_file(journal.prepared_index)
    _fsync_directory(journal.directory)
    state: dict[str, Any] = {
        "version": BROKER_VERSION,
        "transaction_id": receipt.transaction_id,
        "phase": "objects_prepared",
        "old_head": receipt.head,
        "new_head": new_head,
        "tree": prepared_tree,
        "original_index_sha256": receipt.index_sha256,
        "prepared_index_sha256": prepared_index_sha256,
        "original_ref_sha256": receipt.branch_ref_sha256,
        "original_reflog_sha256": receipt.branch_reflog_sha256,
        "updated_ref_sha256": _sha256(expected_ref),
        "updated_reflog_sha256": _sha256(expected_reflog),
        "rollback_reflog_sha256": _sha256(rollback_reflog),
        "object_manifest": manifest,
        "expected_object_oids": [entry["oid"] for entry in manifest],
        "created_object_oids": [
            entry["oid"]
            for entry in manifest
            if entry["prepared_in_quarantine"]
        ],
        "installed_object_oids": [],
        "object_residue_policy": OBJECT_RESIDUE_POLICY,
        "receipt": receipt.public_dict(),
    }
    journal.write_state(state)
    _fault(fault_at, test_crash_at, "prepared")
    _fault(fault_at, test_crash_at, "objects_prepared")

    _install_object_manifest(
        receipt=receipt,
        plan=plan,
        journal=journal,
        state=state,
        fault_at=fault_at,
        test_crash_at=test_crash_at,
    )

    state["phase"] = "ready"
    journal.write_state(state)
    _fault(fault_at, test_crash_at, "ready")

    _replace_index_locked(
        source=journal.prepared_index,
        target=index_path,
        expected_current_sha256=receipt.index_sha256,
    )
    _fault(fault_at, test_crash_at, "index_replaced")
    if _sha256(_read_regular_bytes(index_path, "Git index")) != prepared_index_sha256:
        raise LinkedGitBrokerError("prepared index installation did not verify")
    state["phase"] = "index_installed"
    journal.write_state(state)
    _fault(fault_at, test_crash_at, "index_installed")

    try:
        _git_run(
            root,
            "update-ref",
            "-m",
            message,
            receipt.branch_ref,
            new_head,
            receipt.head,
            env=_fixed_identity_env(receipt),
        )
    except LinkedGitBrokerError as exc:
        _restore_original_index(
            receipt,
            plan,
            journal,
            expected_current_sha256=prepared_index_sha256,
        )
        _require_exact_ref_state(
            ref_path,
            reflog_path,
            ref_sha256=receipt.branch_ref_sha256,
            reflog_sha256=receipt.branch_reflog_sha256,
            label="failed-update original",
        )
        state["phase"] = "rolled_back"
        journal.write_state(state)
        raise LinkedGitBrokerError(
            f"branch update failed and exact index/ref/reflog rollback completed; "
            f"{_object_residue_disclosure(state)}: {exc}"
        ) from exc
    _fault(fault_at, test_crash_at, "ref_replaced")
    try:
        _require_exact_ref_state(
            ref_path,
            reflog_path,
            ref_sha256=str(state["updated_ref_sha256"]),
            reflog_sha256=str(state["updated_reflog_sha256"]),
            label="updated",
        )
    except LinkedGitBrokerError as exc:
        _rollback_exact_updated_state(
            receipt,
            plan,
            journal,
            state,
            prepared_index_sha256=prepared_index_sha256,
        )
        raise LinkedGitBrokerError(
            f"post-update ref verification failed; exact rollback completed; "
            f"{_object_residue_disclosure(state)}: {exc}"
        ) from exc
    state["phase"] = "ref_updated"
    journal.write_state(state)
    _fault(fault_at, test_crash_at, "ref_updated")

    try:
        _postverify(
            receipt,
            plan,
            new_head=new_head,
            tree=prepared_tree,
            index_sha256=prepared_index_sha256,
            updated_ref_sha256=str(state["updated_ref_sha256"]),
            updated_reflog_sha256=str(state["updated_reflog_sha256"]),
        )
    except LinkedGitBrokerError as exc:
        _rollback_exact_updated_state(
            receipt,
            plan,
            journal,
            state,
            prepared_index_sha256=prepared_index_sha256,
        )
        raise LinkedGitBrokerError(
            f"post-update verification failed; exact rollback completed; "
            f"{_object_residue_disclosure(state)}: {exc}"
        ) from exc
    state["phase"] = "done"
    journal.write_state(state)
    _fault(fault_at, test_crash_at, "done")
    return _result(receipt, new_head)


def _recover_or_resume(
    receipt: LinkedGitCommitReceipt,
    state: Mapping[str, Any],
    *,
    journal: "_BrokerJournal",
    approved_roots: tuple[Path, ...],
    codex_home: Path | None,
) -> LinkedGitCommitResult | None:
    _validate_journal_state(receipt, state, journal)
    plan = _qualified_plan(receipt, approved_roots, codex_home)
    phase = str(state.get("phase") or "")
    old_head = str(state.get("old_head") or "")
    new_head = str(state.get("new_head") or "")
    original_index_hash = str(state.get("original_index_sha256") or "")
    prepared_index_hash = str(state.get("prepared_index_sha256") or "")
    index_hash = _sha256(_read_regular_bytes(plan.git_dir / "index", "Git index"))
    current_head = _git_text(plan.worktree_root, "rev-parse", "HEAD")
    ref_path = plan.common_dir.joinpath(*receipt.branch_ref.split("/"))
    reflog_path = plan.common_dir.joinpath("logs", *receipt.branch_ref.split("/"))
    ref_hash = _sha256(_read_regular_bytes(ref_path, "active branch ref"))
    reflog_hash = _sha256(_read_regular_bytes(reflog_path, "active branch reflog"))
    original_ref = str(state.get("original_ref_sha256") or "")
    original_reflog = str(state.get("original_reflog_sha256") or "")
    updated_ref = str(state.get("updated_ref_sha256") or "")
    updated_reflog = str(state.get("updated_reflog_sha256") or "")
    rollback_reflog = str(state.get("rollback_reflog_sha256") or "")

    old_exact = (
        current_head == old_head
        and ref_hash == original_ref
        and reflog_hash == original_reflog
    )
    new_exact = (
        current_head == new_head
        and ref_hash == updated_ref
        and reflog_hash == updated_reflog
    )
    rollback_exact = (
        current_head == old_head
        and ref_hash == original_ref
        and reflog_hash == rollback_reflog
    )
    if phase == "rollback_pending" and rollback_exact and index_hash in {
        original_index_hash,
        prepared_index_hash,
    }:
        _restore_original_reflog(
            receipt,
            journal,
            ref_path=ref_path,
            reflog_path=reflog_path,
            expected_reflog_sha256=rollback_reflog,
        )
        if index_hash == prepared_index_hash:
            _restore_original_index(
                receipt,
                plan,
                journal,
                expected_current_sha256=prepared_index_hash,
            )
        updated = dict(state)
        updated["phase"] = "rolled_back"
        journal.write_state(updated)
        raise LinkedGitBrokerRestored(
            "interrupted broker rollback restored exact original state; "
            f"{_object_residue_disclosure(state)}; prepare a new approval",
            object_oids=state.get("created_object_oids") or (),
        )
    if phase == "done":
        if not new_exact or index_hash != prepared_index_hash:
            raise LinkedGitBrokerError(
                "completed broker journal no longer matches exact ref/index state"
            )
        _postverify(
            receipt,
            plan,
            new_head=new_head,
            tree=str(state.get("tree") or ""),
            index_sha256=prepared_index_hash,
            updated_ref_sha256=updated_ref,
            updated_reflog_sha256=updated_reflog,
        )
        return _result(receipt, new_head, idempotent=True)
    if phase == "rolled_back":
        if not old_exact or index_hash != original_index_hash:
            raise LinkedGitBrokerError(
                "rolled-back broker journal no longer matches exact original state"
            )
        raise LinkedGitBrokerRestored(
            f"broker transaction was rolled back; {_object_residue_disclosure(state)}; "
            "prepare a new approval",
            object_oids=state.get("created_object_oids") or (),
        )

    if old_exact and index_hash == original_index_hash:
        updated = dict(state)
        updated["phase"] = "rolled_back"
        journal.write_state(updated)
        raise LinkedGitBrokerRestored(
            "interrupted broker transaction was already at its exact original state; "
            f"{_object_residue_disclosure(state)}; prepare a new approval",
            object_oids=state.get("created_object_oids") or (),
        )
    if old_exact and index_hash == prepared_index_hash:
        _restore_original_index(
            receipt,
            plan,
            journal,
            expected_current_sha256=prepared_index_hash,
        )
        updated = dict(state)
        updated["phase"] = "rolled_back"
        journal.write_state(updated)
        raise LinkedGitBrokerRestored(
            "interrupted broker transaction restored the exact original index; "
            f"{_object_residue_disclosure(state)}; prepare a new approval",
            object_oids=state.get("created_object_oids") or (),
        )
    if new_exact and index_hash == prepared_index_hash:
        _postverify(
            receipt,
            plan,
            new_head=new_head,
            tree=str(state.get("tree") or ""),
            index_sha256=prepared_index_hash,
            updated_ref_sha256=updated_ref,
            updated_reflog_sha256=updated_reflog,
        )
        updated = dict(state)
        updated["phase"] = "done"
        journal.write_state(updated)
        return _result(receipt, new_head, idempotent=True)
    raise LinkedGitBrokerError(
        "broker journal cannot be resolved from exact old/new ref, reflog, and "
        "original/prepared index identities; manual review required"
    )


def _object_residue_disclosure(state: Mapping[str, Any]) -> str:
    oids = state.get("created_object_oids")
    count = len(oids) if isinstance(oids, list) else 0
    return (
        f"up to {count} journaled approved-content immutable object(s) may remain "
        "unreachable for normal Git GC; shared objects were not deleted"
    )


def _capture_again(
    receipt: LinkedGitCommitReceipt,
    approved_roots: tuple[Path, ...],
    codex_home: Path | None,
) -> LinkedGitCommitReceipt:
    return prepare_linked_git_commit(
        agent_id=receipt.agent_id,
        work_root=receipt.worktree_root,
        approved_roots=approved_roots,
        approved_stage_paths=receipt.approved_stage_paths,
        codex_home=codex_home,
        authored_at=receipt.authored_at,
        commit_subject=receipt.commit_subject,
    )


def _normalized_commit_subject(value: str | None, *, agent_id: str) -> str:
    subject = _commit_subject(agent_id) if value is None else str(value).strip()
    if not subject:
        raise LinkedGitBrokerError("broker commit subject cannot be empty")
    if len(subject) > 160:
        raise LinkedGitBrokerError("broker commit subject exceeds 160 characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in subject):
        raise LinkedGitBrokerError(
            "broker commit subject cannot contain control characters or newlines"
        )
    return subject


def _qualify_authority(
    *,
    work_root: str | Path,
    approved_roots: Iterable[Path],
    codex_home: Path | None,
) -> tuple[LinkedWorktreeGitAdminPlan, tuple[Path, ...], Path | None]:
    roots = _canonical_roots(approved_roots)
    normalized_codex_home = _canonical_codex_home(codex_home)
    before = _codex_config_identities(normalized_codex_home)
    try:
        plan = qualify_linked_worktree_git_admin(
            work_root,
            approved_roots=roots,
            fork_purpose="edit",
            access_mode="edit",
            codex_home=normalized_codex_home,
        )
    except LinkedWorktreeQualificationError as exc:
        raise LinkedGitBrokerError(str(exc)) from exc
    if plan is None:
        raise LinkedGitBrokerError("broker requires a qualified linked Git worktree")
    after = _codex_config_identities(normalized_codex_home)
    if before != after:
        raise LinkedGitBrokerError("Codex configuration changed during qualification")
    return plan, roots, normalized_codex_home


def _qualified_plan(
    receipt: LinkedGitCommitReceipt,
    approved_roots: tuple[Path, ...],
    codex_home: Path | None,
) -> LinkedWorktreeGitAdminPlan:
    plan, roots, normalized_codex_home = _qualify_authority(
        work_root=receipt.worktree_root,
        approved_roots=approved_roots,
        codex_home=codex_home,
    )
    if roots != tuple(Path(path) for path in receipt.approved_roots):
        raise LinkedGitBrokerError("broker approved-root identity drifted")
    expected_codex = (
        str(normalized_codex_home) if normalized_codex_home is not None else None
    )
    if expected_codex != receipt.codex_home:
        raise LinkedGitBrokerError("broker Codex home identity drifted")
    if _codex_config_identities(normalized_codex_home) != receipt.codex_config_identities:
        raise LinkedGitBrokerError("broker Codex configuration identity drifted")
    expected = (
        str(plan.worktree_root),
        str(plan.git_dir),
        str(plan.common_dir),
        str(plan.object_dir),
        plan.branch_ref,
        plan.write_strategy,
        "tui_root_commit_broker",
        plan.base_permission_profile,
        plan.permission_profile,
        tuple(str(path) for path in plan.writable_paths),
    )
    actual = (
        receipt.worktree_root,
        receipt.git_dir,
        receipt.common_dir,
        receipt.object_dir,
        receipt.branch_ref,
        receipt.write_strategy,
        receipt.grant_scope,
        receipt.base_permission_profile,
        receipt.permission_profile,
        receipt.qualified_git_admin_paths,
    )
    if expected != actual:
        raise LinkedGitBrokerError("broker repository qualification drifted")
    _require_broker_topology(plan)
    return plan


def _validate_authority_inputs(
    receipt: LinkedGitCommitReceipt,
    *,
    approved_roots: Iterable[Path],
    codex_home: Path | None,
) -> tuple[tuple[Path, ...], Path | None]:
    roots = _canonical_roots(approved_roots)
    expected_roots = tuple(Path(path) for path in receipt.approved_roots)
    if roots != expected_roots:
        raise LinkedGitBrokerError(
            "execution approved_roots differ from the immutable broker receipt"
        )
    normalized_codex_home = _canonical_codex_home(codex_home)
    expected_codex = (
        str(normalized_codex_home) if normalized_codex_home is not None else None
    )
    if expected_codex != receipt.codex_home:
        raise LinkedGitBrokerError(
            "execution Codex home differs from the immutable broker receipt"
        )
    if _codex_config_identities(normalized_codex_home) != receipt.codex_config_identities:
        raise LinkedGitBrokerError("execution Codex configuration identity drifted")
    return roots, normalized_codex_home


def _require_broker_topology(plan: LinkedWorktreeGitAdminPlan) -> None:
    if plan.write_strategy != BROKER_WRITE_STRATEGY:
        raise LinkedGitBrokerError(
            "TUI commit broker is available only for broker-required split profiles"
        )
    if not DEDICATED_BRANCH_RE.fullmatch(plan.branch_ref):
        raise LinkedGitBrokerError(
            "broker branch must use refs/heads/pbx-operator-N/<name>"
        )
    if (
        plan.common_dir in plan.writable_paths
        or plan.common_dir / "worktrees" in plan.writable_paths
    ):
        raise LinkedGitBrokerError(
            "broker qualification broadened to common Git administration"
        )
    if len(plan.writable_paths) != 4:
        raise LinkedGitBrokerError(
            "broker qualification did not produce exactly four Git-admin paths"
        )


def _require_plain_clean_index(plan: LinkedWorktreeGitAdminPlan) -> None:
    root = plan.worktree_root
    for key in ("core.sparseCheckout", "core.sparseCheckoutCone", "core.splitIndex"):
        if _git_optional_text(root, "config", "--bool", key) == "true":
            raise LinkedGitBrokerError(f"linked Git broker rejects {key}=true")
    index = _read_regular_bytes(plan.git_dir / "index", "Git index")
    object_format = _git_text(root, "rev-parse", "--show-object-format")
    extensions = _git_index_extension_signatures(
        index,
        object_format=object_format,
    )
    if any(marker in extensions for marker in UNSAFE_INDEX_EXTENSIONS):
        raise LinkedGitBrokerError(
            "linked Git broker rejects split or sparse index extensions"
        )
    mandatory = tuple(
        signature
        for signature in extensions
        if signature[:1].islower()
    )
    if mandatory:
        rendered = ", ".join(signature.decode("ascii") for signature in mandatory)
        raise LinkedGitBrokerError(
            "linked Git broker rejects unsupported mandatory index extension(s): "
            + rendered
        )
    if _git_bytes(root, "ls-files", "-u", "-z"):
        raise LinkedGitBrokerError("linked Git broker rejects unmerged index entries")
    result = _git_process(
        root,
        "diff-index",
        "--cached",
        "--quiet",
        "HEAD",
        "--",
        check=False,
    )
    if result.returncode != 0:
        if result.returncode == 1:
            raise LinkedGitBrokerError(
                "linked Git broker requires an initially empty staged set/index delta"
            )
        detail = result.stderr.decode(errors="replace").strip()
        raise LinkedGitBrokerError(detail or "unable to validate the Git index")


def _git_index_extension_signatures(
    index: bytes,
    *,
    object_format: str,
) -> tuple[bytes, ...]:
    """Return structurally parsed Git-index extension signatures.

    Git extension names are meaningful only after the declared entry table.
    Searching the complete byte stream is unsafe because object IDs and ordinary
    pathnames may contain the same bytes. This parser deliberately validates the
    full entry and extension boundaries before returning any signature.
    """

    format_spec = GIT_INDEX_OBJECT_FORMATS.get(str(object_format or "").strip())
    if format_spec is None:
        raise LinkedGitBrokerError(
            f"linked Git broker does not support object format {object_format!r}"
        )
    digest_name, object_id_width = format_spec
    header_size = 12
    if len(index) < header_size + object_id_width:
        raise LinkedGitBrokerError("Git index is truncated before its checksum")
    if index[:4] != b"DIRC":
        raise LinkedGitBrokerError("linked Git broker requires a regular Git index")

    version = int.from_bytes(index[4:8], "big")
    if version not in SUPPORTED_GIT_INDEX_VERSIONS:
        raise LinkedGitBrokerError(
            f"linked Git broker does not support Git index version {version}"
        )
    entry_count = int.from_bytes(index[8:12], "big")
    content_end = len(index) - object_id_width
    expected_checksum = index[content_end:]
    actual_checksum = hashlib.new(digest_name, index[:content_end]).digest()
    if actual_checksum != expected_checksum:
        raise LinkedGitBrokerError(
            f"Git index {object_format} checksum does not match its contents"
        )

    fixed_entry_size = 40 + object_id_width + 2
    minimum_entry_size = fixed_entry_size + (2 if version == 4 else 1)
    available_entry_bytes = content_end - header_size
    if entry_count > available_entry_bytes // minimum_entry_size:
        raise LinkedGitBrokerError(
            "Git index entry count cannot fit before the checksum region"
        )

    offset = header_size
    previous_path = b""
    empty_path_ordinals: list[int] = []
    for ordinal in range(entry_count):
        entry_start = offset
        fixed_end = entry_start + fixed_entry_size
        if fixed_end > content_end:
            raise LinkedGitBrokerError(
                f"Git index entry {ordinal} is truncated before its pathname"
            )
        flags = int.from_bytes(index[fixed_end - 2 : fixed_end], "big")
        cursor = fixed_end
        if flags & GIT_INDEX_EXTENDED_FLAG:
            if version == 2:
                raise LinkedGitBrokerError(
                    f"Git index v2 entry {ordinal} has unsupported extended flags"
                )
            if cursor + 2 > content_end:
                raise LinkedGitBrokerError(
                    f"Git index entry {ordinal} has truncated extended flags"
                )
            cursor += 2

        if version == 4:
            remove_count, cursor = _git_index_v4_remove_count(
                index,
                cursor=cursor,
                content_end=content_end,
                ordinal=ordinal,
            )
            if remove_count > len(previous_path):
                raise LinkedGitBrokerError(
                    f"Git index v4 entry {ordinal} removes beyond the previous pathname"
                )
            terminator = index.find(b"\0", cursor, content_end)
            if terminator < 0:
                raise LinkedGitBrokerError(
                    f"Git index v4 entry {ordinal} pathname is not terminated"
                )
            suffix = index[cursor:terminator]
            pathname = previous_path[: len(previous_path) - remove_count] + suffix
            if not pathname:
                empty_path_ordinals.append(ordinal)
            offset = terminator + 1
            previous_path = pathname
        else:
            terminator = index.find(b"\0", cursor, content_end)
            if terminator < 0:
                raise LinkedGitBrokerError(
                    f"Git index entry {ordinal} pathname is not terminated"
                )
            pathname = index[cursor:terminator]
            if not pathname:
                empty_path_ordinals.append(ordinal)
            unpadded_size = terminator + 1 - entry_start
            padded_size = (unpadded_size + 7) & ~7
            offset = entry_start + padded_size
            if offset > content_end:
                raise LinkedGitBrokerError(
                    f"Git index entry {ordinal} padding crosses the checksum region"
                )
            if any(index[terminator + 1 : offset]):
                raise LinkedGitBrokerError(
                    f"Git index entry {ordinal} has non-NUL padding"
                )
            previous_path = pathname

        declared_length = flags & GIT_INDEX_NAME_LENGTH_MASK
        if declared_length == GIT_INDEX_NAME_LENGTH_MASK:
            if len(previous_path) < GIT_INDEX_NAME_LENGTH_MASK:
                raise LinkedGitBrokerError(
                    f"Git index entry {ordinal} has an impossible pathname length flag"
                )
        elif declared_length != len(previous_path):
            raise LinkedGitBrokerError(
                f"Git index entry {ordinal} pathname length does not match its flags"
            )

    extensions: list[bytes] = []
    seen: set[bytes] = set()
    while offset < content_end:
        if content_end - offset < 8:
            raise LinkedGitBrokerError("Git index extension header is truncated")
        signature = index[offset : offset + 4]
        if len(signature) != 4 or not all(
            65 <= byte <= 90 or 97 <= byte <= 122 for byte in signature
        ):
            raise LinkedGitBrokerError(
                "Git index extension signature is not four ASCII letters"
            )
        if signature in seen:
            raise LinkedGitBrokerError(
                "Git index contains duplicate extension signature "
                + signature.decode("ascii")
            )
        payload_size = int.from_bytes(index[offset + 4 : offset + 8], "big")
        payload_start = offset + 8
        payload_end = payload_start + payload_size
        if payload_end > content_end:
            raise LinkedGitBrokerError(
                "Git index extension payload crosses the checksum region"
            )
        extensions.append(signature)
        seen.add(signature)
        offset = payload_end
    if offset != content_end:  # pragma: no cover - loop invariants are explicit
        raise LinkedGitBrokerError("Git index has an impossible extension boundary")
    if empty_path_ordinals and b"link" not in seen:
        raise LinkedGitBrokerError(
            "Git index has empty pathname entries without a split-index extension"
        )
    return tuple(extensions)


def _git_index_v4_remove_count(
    index: bytes,
    *,
    cursor: int,
    content_end: int,
    ordinal: int,
) -> tuple[int, int]:
    """Decode Git's OFS_DELTA-style pathname remove count."""

    if cursor >= content_end:
        raise LinkedGitBrokerError(
            f"Git index v4 entry {ordinal} has no pathname prefix length"
        )
    byte = index[cursor]
    cursor += 1
    value = byte & 0x7F
    encoded_bytes = 1
    while byte & 0x80:
        if cursor >= content_end:
            raise LinkedGitBrokerError(
                f"Git index v4 entry {ordinal} has a truncated pathname prefix length"
            )
        if encoded_bytes >= 10:
            raise LinkedGitBrokerError(
                f"Git index v4 entry {ordinal} pathname prefix length overflows"
            )
        byte = index[cursor]
        cursor += 1
        value = ((value + 1) << 7) | (byte & 0x7F)
        encoded_bytes += 1
    return value, cursor


def _normalize_stage_paths(paths: Iterable[str]) -> tuple[str, ...]:
    result = tuple(sorted(str(path) for path in paths))
    if not result:
        raise LinkedGitBrokerError(
            "broker approval must enumerate at least one explicit stage path"
        )
    if len(result) != len(set(result)):
        raise LinkedGitBrokerError("broker approval contains duplicate stage paths")
    for path in result:
        _validate_relative_leaf(path)
    return result


def _path_identity(
    plan: LinkedWorktreeGitAdminPlan,
    relative: str,
) -> BrokerPathIdentity:
    _validate_relative_leaf(relative)
    root = plan.worktree_root
    index_mode, index_oid = _index_identity(root, relative)
    parent_chain_sha256 = _approved_parent_chain_sha256(root, relative)
    metadata = _approved_leaf_lstat(root, relative)
    if metadata is None:
        if index_oid is None:
            raise LinkedGitBrokerError(
                f"untracked approved broker path disappeared: {relative}"
            )
        return BrokerPathIdentity(
            path=relative,
            state="deleted",
            mode=index_mode or "",
            raw_sha256=None,
            raw_blob_oid=None,
            index_mode=index_mode,
            index_oid=index_oid,
            content_policy_sha256=RAW_BYTE_CONTENT_POLICY_SHA256,
            parent_chain_sha256=parent_chain_sha256,
            st_dev=None,
            st_ino=None,
            st_uid=None,
            st_nlink=None,
            st_size=None,
            st_mtime_ns=None,
            st_ctime_ns=None,
        )
    metadata, raw = _read_approved_leaf(root, relative)
    if _approved_parent_chain_sha256(root, relative) != parent_chain_sha256:
        raise LinkedGitBrokerError(
            f"approved parent chain changed during capture: {relative}"
        )
    if stat.S_ISREG(metadata.st_mode):
        mode = "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
        state = "file"
    elif stat.S_ISLNK(metadata.st_mode):
        mode = "120000"
        state = "symlink"
    else:  # pragma: no cover - _read_approved_leaf rejects this first
        raise LinkedGitBrokerError(f"unsupported broker file type: {relative}")
    if index_mode == "160000":
        raise LinkedGitBrokerError(
            f"linked Git broker rejects gitlinks/submodules: {relative}"
        )
    raw_oid = _git_text(
        root,
        "hash-object",
        "--no-filters",
        "--stdin",
        input_bytes=raw,
    )
    if index_mode == mode and index_oid == raw_oid:
        raise LinkedGitBrokerError(
            f"approved broker path has no raw-byte or mode change: {relative!r}"
        )
    return BrokerPathIdentity(
        path=relative,
        state=state,
        mode=mode,
        raw_sha256=_sha256(raw),
        raw_blob_oid=raw_oid,
        index_mode=index_mode,
        index_oid=index_oid,
        content_policy_sha256=RAW_BYTE_CONTENT_POLICY_SHA256,
        parent_chain_sha256=parent_chain_sha256,
        st_dev=metadata.st_dev,
        st_ino=metadata.st_ino,
        st_uid=metadata.st_uid,
        st_nlink=metadata.st_nlink,
        st_size=metadata.st_size,
        st_mtime_ns=metadata.st_mtime_ns,
        st_ctime_ns=metadata.st_ctime_ns,
    )


def _verify_approved_paths(
    receipt: LinkedGitCommitReceipt,
    plan: LinkedWorktreeGitAdminPlan,
) -> None:
    root = plan.worktree_root
    for identity in receipt.path_identities:
        if identity.content_policy_sha256 != RAW_BYTE_CONTENT_POLICY_SHA256:
            raise LinkedGitBrokerError(
                f"broker postverify content-policy drift: {identity.path}"
            )
        index_mode, index_oid = _index_identity(root, identity.path)
        if identity.state == "deleted":
            if (
                _approved_parent_chain_sha256(root, identity.path)
                != identity.parent_chain_sha256
            ):
                raise LinkedGitBrokerError(
                    f"broker postverify deleted-path parent drift: {identity.path}"
                )
            if _approved_leaf_lstat(root, identity.path) is not None or index_oid is not None:
                raise LinkedGitBrokerError(
                    f"broker postverify deletion mismatch: {identity.path}"
                )
            continue
        raw = _identity_bytes(root, identity)
        if index_mode != identity.mode or index_oid != identity.raw_blob_oid:
            raise LinkedGitBrokerError(
                f"broker postverify index entry mismatch: {identity.path}"
            )
        if _git_text(
            root,
            "hash-object",
            "--no-filters",
            "--stdin",
            input_bytes=raw,
        ) != identity.raw_blob_oid:
            raise LinkedGitBrokerError(
                f"broker postverify blob mismatch: {identity.path}"
            )


def _postverify(
    receipt: LinkedGitCommitReceipt,
    plan: LinkedWorktreeGitAdminPlan,
    *,
    new_head: str,
    tree: str,
    index_sha256: str,
    updated_ref_sha256: str,
    updated_reflog_sha256: str,
) -> None:
    root = plan.worktree_root
    if _git_text(root, "rev-parse", "HEAD") != new_head:
        raise LinkedGitBrokerError("broker postverify HEAD mismatch")
    if _git_text(root, "symbolic-ref", "-q", "HEAD") != receipt.branch_ref:
        raise LinkedGitBrokerError("broker postverify branch mismatch")
    if _git_text(root, "rev-parse", "--abbrev-ref", "@{upstream}") != receipt.upstream:
        raise LinkedGitBrokerError("broker postverify upstream identity mismatch")
    if _git_text(root, "rev-parse", "@{upstream}") != receipt.upstream_oid:
        raise LinkedGitBrokerError("broker postverify upstream OID drift")
    if _git_config_sha256(root) != receipt.git_config_sha256:
        raise LinkedGitBrokerError("broker postverify Git configuration drift")
    if _sha256(_read_regular_bytes(plan.git_dir / "index", "Git index")) != index_sha256:
        raise LinkedGitBrokerError("broker postverify index mismatch")
    if _git_text(root, "rev-parse", "HEAD^{tree}") != tree:
        raise LinkedGitBrokerError("broker postverify commit tree mismatch")
    if _tree_changed_paths(root, receipt.head_tree, tree) != receipt.approved_stage_paths:
        raise LinkedGitBrokerError(
            "broker postverify commit changed paths outside approval"
        )
    result = _git_process(
        root,
        "diff-index",
        "--cached",
        "--quiet",
        "HEAD",
        "--",
        check=False,
    )
    if result.returncode != 0:
        raise LinkedGitBrokerError("broker postverify found a staged index delta")
    ref_path = plan.common_dir.joinpath(*receipt.branch_ref.split("/"))
    reflog_path = plan.common_dir.joinpath("logs", *receipt.branch_ref.split("/"))
    _require_exact_ref_state(
        ref_path,
        reflog_path,
        ref_sha256=updated_ref_sha256,
        reflog_sha256=updated_reflog_sha256,
        label="postverify",
    )
    _verify_approved_paths(receipt, plan)


def _restore_original_index(
    receipt: LinkedGitCommitReceipt,
    plan: LinkedWorktreeGitAdminPlan,
    journal: "_BrokerJournal",
    *,
    expected_current_sha256: str,
) -> None:
    _replace_index_locked(
        source=journal.original_index,
        target=plan.git_dir / "index",
        expected_current_sha256=expected_current_sha256,
    )
    if _sha256(_read_regular_bytes(plan.git_dir / "index", "Git index")) != receipt.index_sha256:
        raise LinkedGitBrokerError("broker could not restore the original index")


def _rollback_exact_updated_state(
    receipt: LinkedGitCommitReceipt,
    plan: LinkedWorktreeGitAdminPlan,
    journal: "_BrokerJournal",
    state: dict[str, Any],
    *,
    prepared_index_sha256: str,
) -> None:
    """Rollback only an exact broker-owned new-ref/new-index state."""

    ref_path = plan.common_dir.joinpath(*receipt.branch_ref.split("/"))
    reflog_path = plan.common_dir.joinpath("logs", *receipt.branch_ref.split("/"))
    _require_exact_ref_state(
        ref_path,
        reflog_path,
        ref_sha256=str(state["updated_ref_sha256"]),
        reflog_sha256=str(state["updated_reflog_sha256"]),
        label="rollback source",
    )
    if _sha256(_read_regular_bytes(plan.git_dir / "index", "Git index")) != prepared_index_sha256:
        raise LinkedGitBrokerError(
            "rollback source index drifted; refusing to overwrite concurrent staging"
        )
    state["phase"] = "rollback_pending"
    journal.write_state(state)
    message = f"agent-pbx broker rollback {receipt.transaction_id[:12]}"
    _git_run(
        plan.worktree_root,
        "update-ref",
        "-m",
        message,
        receipt.branch_ref,
        receipt.head,
        str(state["new_head"]),
        env=_fixed_identity_env(receipt),
    )
    _require_exact_ref_state(
        ref_path,
        reflog_path,
        ref_sha256=receipt.branch_ref_sha256,
        reflog_sha256=str(state["rollback_reflog_sha256"]),
        label="rollback CAS",
    )
    _restore_original_reflog(
        receipt,
        journal,
        ref_path=ref_path,
        reflog_path=reflog_path,
        expected_reflog_sha256=str(state["rollback_reflog_sha256"]),
    )
    _restore_original_index(
        receipt,
        plan,
        journal,
        expected_current_sha256=prepared_index_sha256,
    )
    _require_exact_ref_state(
        ref_path,
        reflog_path,
        ref_sha256=receipt.branch_ref_sha256,
        reflog_sha256=receipt.branch_reflog_sha256,
        label="rolled-back original",
    )
    state["phase"] = "rolled_back"
    journal.write_state(state)


def _restore_original_reflog(
    receipt: LinkedGitCommitReceipt,
    journal: "_BrokerJournal",
    *,
    ref_path: Path,
    reflog_path: Path,
    expected_reflog_sha256: str,
) -> None:
    """Restore exact reflog bytes while holding Git's cooperative ref/log locks."""

    ref_lock = ref_path.with_name(f"{ref_path.name}.lock")
    log_lock = reflog_path.with_name(f"{reflog_path.name}.lock")
    opened: list[tuple[Path, int]] = []
    try:
        for lock, source in ((ref_lock, None), (log_lock, journal.original_reflog)):
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(lock, flags, 0o600)
            opened.append((lock, descriptor))
            if source is not None:
                payload = _read_private_file(source)
                os.write(descriptor, payload)
                os.fsync(descriptor)
        if _sha256(_read_regular_bytes(ref_path, "rollback branch ref")) != receipt.branch_ref_sha256:
            raise LinkedGitBrokerError("rollback branch ref drifted before reflog restore")
        if _sha256(_read_regular_bytes(reflog_path, "rollback branch reflog")) != expected_reflog_sha256:
            raise LinkedGitBrokerError("rollback reflog drifted before exact restore")
        for _, descriptor in opened:
            os.close(descriptor)
        opened.clear()
        os.replace(log_lock, reflog_path)
        ref_lock.unlink()
        _fsync_directory(ref_path.parent)
        if reflog_path.parent != ref_path.parent:
            _fsync_directory(reflog_path.parent)
    finally:
        for lock, descriptor in opened:
            os.close(descriptor)
            lock.unlink(missing_ok=True)
        ref_lock.unlink(missing_ok=True)
        log_lock.unlink(missing_ok=True)


def _replace_index_locked(
    *,
    source: Path,
    target: Path,
    expected_current_sha256: str,
) -> None:
    if target.is_symlink() or not target.is_file():
        raise LinkedGitBrokerError("Git index target is not a regular file")
    lock = target.with_name(f"{target.name}.lock")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock, flags, stat.S_IMODE(target.stat().st_mode))
    except FileExistsError as exc:
        raise LinkedGitBrokerError(
            f"Git index is locked; refusing to overwrite concurrent staging: {lock}"
        ) from exc
    installed = False
    try:
        current = _read_regular_bytes(target, "Git index")
        if _sha256(current) != expected_current_sha256:
            raise LinkedGitBrokerError(
                "Git index changed after broker approval; concurrent staging was preserved"
            )
        with source.open("rb") as reader, os.fdopen(descriptor, "wb", closefd=False) as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        os.close(descriptor)
        descriptor = -1
        os.replace(lock, target)
        installed = True
        _fsync_directory(target.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not installed:
            lock.unlink(missing_ok=True)


def _validate_journal_state(
    receipt: LinkedGitCommitReceipt,
    state: Mapping[str, Any],
    journal: "_BrokerJournal",
) -> None:
    if set(state) != JOURNAL_STATE_FIELDS:
        missing = sorted(JOURNAL_STATE_FIELDS - set(state))
        extra = sorted(set(state) - JOURNAL_STATE_FIELDS)
        raise LinkedGitBrokerError(
            "broker journal state schema mismatch"
            f" (missing={missing}, extra={extra})"
        )
    scalar_fields = (
        "version",
        "transaction_id",
        "phase",
        "old_head",
        "new_head",
        "tree",
        "original_index_sha256",
        "prepared_index_sha256",
        "original_ref_sha256",
        "original_reflog_sha256",
        "updated_ref_sha256",
        "updated_reflog_sha256",
        "rollback_reflog_sha256",
        "object_residue_policy",
    )
    if any(type(state.get(field)) is not str for field in scalar_fields):
        raise LinkedGitBrokerError("broker journal state scalar type mismatch")
    if state["version"] != BROKER_VERSION:
        raise LinkedGitBrokerError("broker journal version mismatch")
    if state["transaction_id"] != receipt.transaction_id:
        raise LinkedGitBrokerError("broker journal transaction identity mismatch")
    if state["phase"] not in JOURNAL_PHASES:
        raise LinkedGitBrokerError("broker journal phase is unknown")
    for field in ("old_head", "new_head", "tree"):
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", state[field]):
            raise LinkedGitBrokerError(
                f"broker journal {field} is not a Git object id"
            )
    for field in (
        "transaction_id",
        "original_index_sha256",
        "prepared_index_sha256",
        "original_ref_sha256",
        "original_reflog_sha256",
        "updated_ref_sha256",
        "updated_reflog_sha256",
        "rollback_reflog_sha256",
    ):
        if not re.fullmatch(r"[0-9a-f]{64}", state[field]):
            raise LinkedGitBrokerError(
                f"broker journal {field} is not a SHA-256 digest"
            )
    journal_receipt = state["receipt"]
    if not isinstance(journal_receipt, Mapping):
        raise LinkedGitBrokerError("broker journal receipt is malformed")
    try:
        parsed_receipt = _receipt_from_public_dict(journal_receipt)
    except LinkedGitBrokerError as exc:
        raise LinkedGitBrokerError(
            f"broker journal receipt bytes do not match approval: {exc}"
        ) from exc
    if parsed_receipt != receipt or _canonical_json(journal_receipt) != _canonical_json(
        receipt.public_dict()
    ):
        raise LinkedGitBrokerError("broker journal receipt bytes do not match approval")
    expected = {
        journal.original_index: state["original_index_sha256"],
        journal.prepared_index: state["prepared_index_sha256"],
        journal.original_ref: state["original_ref_sha256"],
        journal.original_reflog: state["original_reflog_sha256"],
    }
    for path, digest in expected.items():
        if not digest or _sha256(_read_private_file(path)) != digest:
            raise LinkedGitBrokerError(f"broker journal artifact identity mismatch: {path.name}")
    _validate_object_manifest_state(state, journal)


def _validate_object_manifest_state(
    state: Mapping[str, Any],
    journal: "_BrokerJournal",
) -> None:
    manifest = state.get("object_manifest")
    expected_oids = state.get("expected_object_oids")
    created_oids = state.get("created_object_oids")
    installed_oids = state.get("installed_object_oids")
    if not isinstance(manifest, list) or not manifest:
        raise LinkedGitBrokerError("broker journal object manifest is unavailable")
    if not all(
        type(value) is list
        for value in (expected_oids, created_oids, installed_oids)
    ):
        raise LinkedGitBrokerError("broker journal object progress is malformed")
    normalized: list[dict[str, Any]] = []
    for entry in manifest:
        if not isinstance(entry, Mapping) or set(entry) != {
            "oid",
            "type",
            "prepared_in_quarantine",
        }:
            raise LinkedGitBrokerError("broker journal object entry is malformed")
        oid = entry.get("oid")
        object_type = entry.get("type")
        prepared = entry.get("prepared_in_quarantine")
        if (
            not isinstance(oid, str)
            or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid)
            or object_type not in {"blob", "tree", "commit"}
            or type(prepared) is not bool
        ):
            raise LinkedGitBrokerError("broker journal object entry is invalid")
        normalized.append(
            {
                "oid": oid,
                "type": object_type,
                "prepared_in_quarantine": prepared,
            }
        )
    normalized.sort(key=lambda item: (item["type"], item["oid"]))
    if normalized != manifest or len({entry["oid"] for entry in manifest}) != len(manifest):
        raise LinkedGitBrokerError("broker journal object manifest is not canonical")
    manifest_oids = [entry["oid"] for entry in manifest]
    manifest_created = [
        entry["oid"] for entry in manifest if entry["prepared_in_quarantine"]
    ]
    if expected_oids != manifest_oids or created_oids != manifest_created:
        raise LinkedGitBrokerError("broker journal object identity list mismatch")
    if not all(
        isinstance(oid, str)
        and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid)
        for oid in (*expected_oids, *created_oids, *installed_oids)
    ):
        raise LinkedGitBrokerError("broker journal object progress OID is invalid")
    if installed_oids != manifest_created[: len(installed_oids)]:
        raise LinkedGitBrokerError(
            "broker journal installed-object progress is not a canonical prefix"
        )
    phase = state["phase"]
    installed_count = len(installed_oids)
    created_count = len(manifest_created)
    if phase == "objects_prepared" and installed_count != 0:
        raise LinkedGitBrokerError(
            "broker journal objects_prepared phase has installed-object progress"
        )
    if phase == "objects_installing" and not (
        0 < installed_count < created_count
    ):
        raise LinkedGitBrokerError(
            "broker journal objects_installing phase has impossible progress"
        )
    if phase in JOURNAL_FULL_INSTALL_PHASES and installed_oids != manifest_created:
        raise LinkedGitBrokerError(
            f"broker journal {phase} phase lacks complete object installation"
        )
    files = _quarantine_object_files(journal.object_quarantine)
    if set(files) != set(manifest_created):
        raise LinkedGitBrokerError("broker quarantine differs from the object manifest")
    for entry in manifest:
        if not entry["prepared_in_quarantine"]:
            continue
        object_type, _ = _loose_object_payload(
            files[entry["oid"]],
            entry["oid"],
        )
        if object_type != entry["type"]:
            raise LinkedGitBrokerError("broker quarantine object type drifted")
    if state["object_residue_policy"] != OBJECT_RESIDUE_POLICY:
        raise LinkedGitBrokerError("broker journal object-residue policy mismatch")


def _result(
    receipt: LinkedGitCommitReceipt,
    new_head: str,
    *,
    idempotent: bool = False,
) -> LinkedGitCommitResult:
    return LinkedGitCommitResult(
        transaction_id=receipt.transaction_id,
        status="committed",
        old_head=receipt.head,
        new_head=new_head,
        branch_ref=receipt.branch_ref,
        committed_paths=receipt.approved_stage_paths,
        idempotent=idempotent,
    )


def _require_exact_ref_state(
    ref_path: Path,
    reflog_path: Path,
    *,
    ref_sha256: str,
    reflog_sha256: str,
    label: str,
) -> None:
    if _sha256(_read_regular_bytes(ref_path, f"{label} branch ref")) != ref_sha256:
        raise LinkedGitBrokerError(f"broker {label} branch ref bytes drifted")
    if _sha256(_read_regular_bytes(reflog_path, f"{label} branch reflog")) != reflog_sha256:
        raise LinkedGitBrokerError(f"broker {label} branch reflog bytes drifted")


def _fixed_identity_env(receipt: LinkedGitCommitReceipt) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": receipt.author_name,
        "GIT_AUTHOR_EMAIL": receipt.author_email,
        "GIT_COMMITTER_NAME": receipt.author_name,
        "GIT_COMMITTER_EMAIL": receipt.author_email,
        "GIT_AUTHOR_DATE": f"@{receipt.authored_at} +0000",
        "GIT_COMMITTER_DATE": f"@{receipt.authored_at} +0000",
    }


def _reflog_line(
    old_head: str,
    new_head: str,
    receipt: LinkedGitCommitReceipt,
    message: str,
) -> bytes:
    return (
        f"{old_head} {new_head} {receipt.author_name} <{receipt.author_email}> "
        f"{receipt.authored_at} +0000\t{message}\n"
    ).encode("utf-8")


def _tree_changed_paths(
    root: Path,
    old_tree: str,
    new_tree: str,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    return _nul_paths(
        _git_bytes(
            root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            old_tree,
            new_tree,
            "--",
            env=env,
        )
    )


def _index_identity(root: Path, relative: str) -> tuple[str | None, str | None]:
    raw = _git_bytes(root, "ls-files", "--stage", "-z", "--", relative)
    if not raw:
        return None, None
    records = [item for item in raw.split(b"\0") if item]
    if len(records) != 1:
        raise LinkedGitBrokerError(
            f"broker path has ambiguous index entries: {relative}"
        )
    header, separator, path = records[0].partition(b"\t")
    if not separator or path.decode("utf-8", errors="strict") != relative:
        raise LinkedGitBrokerError(f"unable to parse index identity for {relative}")
    fields = header.decode("ascii", errors="strict").split()
    if len(fields) != 3 or fields[2] != "0":
        raise LinkedGitBrokerError(
            f"broker path is not an ordinary stage-0 entry: {relative}"
        )
    if fields[0] == "160000":
        raise LinkedGitBrokerError(
            f"linked Git broker rejects gitlinks/submodules: {relative}"
        )
    return fields[0], fields[1]


def _identity_bytes(root: Path, identity: BrokerPathIdentity) -> bytes:
    if identity.state not in {"file", "symlink"}:
        raise LinkedGitBrokerError(
            f"broker path is not writable content: {identity.path}"
        )
    if _approved_parent_chain_sha256(root, identity.path) != identity.parent_chain_sha256:
        raise LinkedGitBrokerError(
            f"approved parent chain drifted for {identity.path}"
        )
    metadata, raw = _read_approved_leaf(root, identity.path)
    actual_state = "symlink" if stat.S_ISLNK(metadata.st_mode) else "file"
    actual_mode = (
        "120000"
        if actual_state == "symlink"
        else ("100755" if metadata.st_mode & stat.S_IXUSR else "100644")
    )
    if actual_state != identity.state or actual_mode != identity.mode:
        raise LinkedGitBrokerError(f"approved mode/type drifted for {identity.path}")
    expected_metadata = (
        identity.st_dev,
        identity.st_ino,
        identity.st_uid,
        identity.st_nlink,
        identity.st_size,
        identity.st_mtime_ns,
        identity.st_ctime_ns,
    )
    actual_metadata = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_uid,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    if actual_metadata != expected_metadata:
        raise LinkedGitBrokerError(f"approved file identity drifted for {identity.path}")
    if _sha256(raw) != identity.raw_sha256:
        raise LinkedGitBrokerError(f"approved bytes drifted for {identity.path}")
    return raw


def _read_approved_leaf(root: Path, relative: str) -> tuple[os.stat_result, bytes]:
    """Read one approved leaf through no-follow directory descriptors.

    The broker commits these exact raw bytes with ``hash-object --no-filters``;
    it never reads worktree attribute files or runs clean filters.
    """

    _validate_relative_leaf(relative)
    parts = Path(relative).parts
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
        metadata = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        if stat.S_ISREG(metadata.st_mode):
            leaf = os.open(
                parts[-1],
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current,
            )
            descriptors.append(leaf)
            opened = os.fstat(leaf)
            if _leaf_open_identity(opened) != _leaf_open_identity(metadata):
                raise LinkedGitBrokerError(
                    f"approved path raced during open: {relative}"
                )
            before = _leaf_content_identity(opened)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(leaf, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after_metadata = os.fstat(leaf)
            if _leaf_content_identity(after_metadata) != before:
                raise LinkedGitBrokerError(
                    f"approved file changed during read: {relative}"
                )
            raw = b"".join(chunks)
            if len(raw) != after_metadata.st_size:
                raise LinkedGitBrokerError(
                    f"approved file size changed during read: {relative}"
                )
            return after_metadata, raw
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(parts[-1], dir_fd=current)
            after_metadata = os.stat(
                parts[-1], dir_fd=current, follow_symlinks=False
            )
            if _leaf_content_identity(after_metadata) != _leaf_content_identity(
                metadata
            ):
                raise LinkedGitBrokerError(
                    f"approved symlink raced during read: {relative}"
                )
            return after_metadata, target.encode("utf-8")
        if stat.S_ISDIR(metadata.st_mode):
            raise LinkedGitBrokerError(
                f"broker paths must be enumerated leaves, not directories: {relative}"
            )
        raise LinkedGitBrokerError(f"unsupported broker file type: {relative}")
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise LinkedGitBrokerError(
            f"unable to read approved path without following symlinks: {relative}: {exc}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _approved_leaf_lstat(root: Path, relative: str) -> os.stat_result | None:
    """Return one approved leaf's metadata without following any parent symlink."""

    _validate_relative_leaf(relative)
    parts = Path(relative).parts
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(root, flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
        try:
            return os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        except FileNotFoundError:
            return None
    except OSError as exc:
        raise LinkedGitBrokerError(
            f"unable to inspect approved path without following symlinks: {relative}: {exc}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _approved_parent_chain_sha256(root: Path, relative: str) -> str:
    """Hash stable identities for the canonical root and approved leaf parents."""

    _validate_relative_leaf(relative)
    parts = Path(relative).parts
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    identities: list[tuple[int, int, int, int, int, int]] = []
    try:
        current = os.open(root, flags)
        descriptors.append(current)
        identities.append(_leaf_open_identity(os.fstat(current)))
        for part in parts[:-1]:
            current = os.open(part, flags, dir_fd=current)
            descriptors.append(current)
            identities.append(_leaf_open_identity(os.fstat(current)))
        return _sha256(_canonical_json(identities))
    except OSError as exc:
        raise LinkedGitBrokerError(
            f"unable to qualify approved parent chain without symlinks: {relative}: {exc}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _leaf_open_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        stat.S_IMODE(metadata.st_mode),
        metadata.st_uid,
        metadata.st_nlink,
    )


def _leaf_content_identity(
    metadata: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int, int]:
    return (
        *_leaf_open_identity(metadata),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _commit_subject(agent_id: str) -> str:
    subject = f"Agent PBX approved commit for {agent_id}"
    return subject if len(subject) <= 180 else subject[:180].rstrip()


def _validate_relative_leaf(value: str) -> None:
    path = Path(value)
    if not value or path.is_absolute() or value.startswith("-"):
        raise LinkedGitBrokerError(f"unsafe broker path: {value!r}")
    if "\0" in value or any(part in {"", ".", ".."} for part in path.parts):
        raise LinkedGitBrokerError(f"unsafe broker path: {value!r}")
    if path.parts[0] == ".git":
        raise LinkedGitBrokerError("broker paths cannot target .git")


def _require_safe_parent(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise LinkedGitBrokerError("broker path escaped the worktree") from exc
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise LinkedGitBrokerError(
                f"broker path contains symlink parent: {current}"
            )


def _nul_paths(payload: bytes) -> tuple[str, ...]:
    paths = tuple(
        sorted(
            item.decode("utf-8", errors="strict")
            for item in payload.split(b"\0")
            if item
        )
    )
    for path in paths:
        _validate_relative_leaf(path)
    return paths


def _canonical_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for candidate in roots:
        raw = Path(candidate).expanduser()
        try:
            resolved = raw.resolve(strict=True)
        except OSError as exc:
            raise LinkedGitBrokerError(f"approved root is unavailable: {raw}: {exc}") from exc
        if raw.absolute() != resolved or not resolved.is_dir():
            raise LinkedGitBrokerError(
                f"approved roots must be canonical directories: {raw}"
            )
        if resolved not in result:
            result.append(resolved)
    if not result:
        raise LinkedGitBrokerError("no approved project roots are available")
    return tuple(sorted(result, key=str))


def _canonical_codex_home(codex_home: Path | None) -> Path | None:
    if codex_home is None:
        return None
    raw = Path(codex_home).expanduser()
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise LinkedGitBrokerError(f"Codex home is unavailable: {raw}: {exc}") from exc
    if raw.absolute() != resolved or not resolved.is_dir():
        raise LinkedGitBrokerError("Codex home must be one canonical directory")
    _require_no_symlink_components(resolved)
    return resolved


def _codex_config_identities(
    codex_home: Path | None,
) -> tuple[BrokerConfigIdentity, ...]:
    candidates: list[tuple[str, Path]] = []
    if codex_home is not None:
        candidates.append(("user", codex_home / "config.toml"))
    candidates.extend(
        (
            ("managed", Path("/etc/codex/managed_config.toml")),
            ("requirements", Path("/etc/codex/requirements.toml")),
        )
    )
    result: list[BrokerConfigIdentity] = []
    for label, path in candidates:
        if not os.path.lexists(path):
            result.append(
                BrokerConfigIdentity(label, str(path), "absent", None, None, None)
            )
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise LinkedGitBrokerError(
                f"Codex {label} configuration must be a regular non-symlink file: {path}"
            )
        result.append(
            BrokerConfigIdentity(
                label=label,
                path=str(path),
                state="present",
                sha256=_sha256(path.read_bytes()),
                uid=metadata.st_uid,
                mode=stat.S_IMODE(metadata.st_mode),
            )
        )
    return tuple(result)


def _git_config_sha256(root: Path) -> str:
    local = _git_bytes(
        root,
        "config",
        "--local",
        "--no-includes",
        "--null",
        "--list",
    )
    worktree_result = _git_process(
        root,
        "config",
        "--worktree",
        "--no-includes",
        "--null",
        "--list",
        check=False,
    )
    worktree = worktree_result.stdout if worktree_result.returncode == 0 else b""
    return _sha256(local + b"\0--worktree--\0" + worktree)


def _git_optional_text(root: Path, *args: str) -> str:
    result = _git_process(root, *args, check=False)
    return result.stdout.decode("utf-8", errors="strict").strip() if result.returncode == 0 else ""


def _git_text(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    return _git_bytes(
        root, *args, input_bytes=input_bytes, env=env
    ).decode("utf-8", errors="strict").strip()


def _git_bytes(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> bytes:
    return _git_process(
        root,
        *args,
        input_bytes=input_bytes,
        env=env,
        check=True,
    ).stdout


def _git_run(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    _git_process(root, *args, input_bytes=input_bytes, env=env, check=True)


def _git_process(
    root: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: Mapping[str, str] | None = None,
    check: bool,
) -> subprocess.CompletedProcess[bytes]:
    supplied_env = dict(env or {})
    unknown_env = sorted(set(supplied_env) - BROKER_GIT_ENV_KEYS)
    if unknown_env:
        raise LinkedGitBrokerError(
            "unsupported broker Git environment keys: " + ", ".join(unknown_env)
        )
    git_binary = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
    if not git_binary:
        raise LinkedGitBrokerError("an absolute Git executable is required")
    process_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/nonexistent-agent-pbx-broker-home",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        **supplied_env,
    }
    result = subprocess.run(
        [
            str(Path(git_binary).resolve(strict=True)),
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "commit.gpgSign=false",
            "-C",
            str(root),
            *args,
        ],
        input=input_bytes,
        capture_output=True,
        env=process_env,
        timeout=30,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip()
        raise LinkedGitBrokerError(
            detail or f"Git broker command failed: {args[0] if args else 'git'}"
        )
    return result


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_regular_bytes(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise LinkedGitBrokerError(f"unable to read {label}: {path}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise LinkedGitBrokerError(f"{label} is not a regular non-symlink file: {path}")
    return path.read_bytes()


def _copy_private_file(source: Path, target: Path) -> None:
    payload = _read_regular_bytes(source, source.name)
    descriptor = os.open(
        target,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as writer:
            writer.write(payload)
            writer.flush()
            os.fsync(writer.fileno())
    finally:
        os.close(descriptor)


def _read_private_file(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise LinkedGitBrokerError(f"unsafe broker journal artifact: {path}")
    return path.read_bytes()


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _normalize_private_object_quarantine(root: Path) -> None:
    """Make the Git-created quarantine private, durable, and inspectable."""

    for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        metadata = child.lstat()
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise LinkedGitBrokerError(
                f"unsafe broker object-quarantine entry: {child}"
            )
        if stat.S_ISREG(metadata.st_mode):
            os.chmod(child, 0o600, follow_symlinks=False)
            _fsync_file(child)
        elif stat.S_ISDIR(metadata.st_mode):
            os.chmod(child, 0o700, follow_symlinks=False)
            _fsync_directory(child)
        else:
            raise LinkedGitBrokerError(
                f"unsupported broker object-quarantine entry: {child}"
            )
    os.chmod(root, 0o700, follow_symlinks=False)
    _fsync_directory(root)


def _remove_private_object_quarantine(root: Path) -> None:
    """Remove only a validated private quarantine before shared-object install."""

    _BrokerJournal._validate_private_object_quarantine(root)
    for child in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if child.is_dir():
            child.rmdir()
        else:
            child.unlink()
    root.rmdir()


def _loose_object_payload(path: Path, oid: str) -> tuple[str, bytes]:
    compressed = _read_private_file(path)
    try:
        payload = zlib.decompress(compressed)
        header, content = payload.split(b"\0", 1)
        object_type_bytes, size_bytes = header.split(b" ", 1)
        object_type = object_type_bytes.decode("ascii")
        declared_size = int(size_bytes.decode("ascii"))
    except (ValueError, UnicodeDecodeError, zlib.error) as exc:
        raise LinkedGitBrokerError(
            f"invalid broker quarantine object {oid}: {exc}"
        ) from exc
    if object_type not in {"blob", "tree", "commit"} or declared_size != len(content):
        raise LinkedGitBrokerError(f"invalid broker quarantine object header: {oid}")
    algorithm = hashlib.sha1 if len(oid) == 40 else hashlib.sha256
    if algorithm(payload).hexdigest() != oid:
        raise LinkedGitBrokerError(f"broker quarantine object identity mismatch: {oid}")
    return object_type, content


def _quarantine_object_files(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for child in root.rglob("*"):
        if child.is_dir():
            continue
        relative = child.relative_to(root)
        if len(relative.parts) != 2:
            raise LinkedGitBrokerError(
                f"unexpected broker quarantine object path: {child}"
            )
        oid = "".join(relative.parts)
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
            raise LinkedGitBrokerError(
                f"unexpected broker quarantine object name: {child}"
            )
        result[oid] = child
    return result


def _object_manifest(
    *,
    journal: "_BrokerJournal",
    receipt: LinkedGitCommitReceipt,
    prepared_tree: str,
    new_head: str,
    root: Path,
    quarantine_env: Mapping[str, str],
) -> list[dict[str, Any]]:
    _normalize_private_object_quarantine(journal.object_quarantine)
    files = _quarantine_object_files(journal.object_quarantine)
    manifest: dict[str, dict[str, Any]] = {}
    for oid, path in files.items():
        object_type, _ = _loose_object_payload(path, oid)
        reported = _git_text(root, "cat-file", "-t", oid, env=quarantine_env)
        if reported != object_type:
            raise LinkedGitBrokerError(
                f"broker quarantine object type mismatch: {oid}"
            )
        manifest[oid] = {
            "oid": oid,
            "type": object_type,
            "prepared_in_quarantine": True,
        }
    expected: list[tuple[str, str]] = [
        (identity.raw_blob_oid, "blob")
        for identity in receipt.path_identities
        if identity.raw_blob_oid is not None
    ]
    expected.extend(((prepared_tree, "tree"), (new_head, "commit")))
    for oid, object_type in expected:
        entry = manifest.get(oid)
        if entry is None:
            reported = _git_text(root, "cat-file", "-t", oid, env=quarantine_env)
            if reported != object_type:
                raise LinkedGitBrokerError(
                    f"broker expected object type mismatch: {oid}"
                )
            manifest[oid] = {
                "oid": oid,
                "type": object_type,
                "prepared_in_quarantine": False,
            }
        elif entry["type"] != object_type:
            raise LinkedGitBrokerError(f"broker object manifest type mismatch: {oid}")
    result = sorted(manifest.values(), key=lambda item: (item["type"], item["oid"]))
    if not result:
        raise LinkedGitBrokerError("broker object manifest is empty")
    return result


def _install_object_manifest(
    *,
    receipt: LinkedGitCommitReceipt,
    plan: LinkedWorktreeGitAdminPlan,
    journal: "_BrokerJournal",
    state: dict[str, Any],
    fault_at: str | None,
    test_crash_at: str | None,
) -> None:
    manifest = state.get("object_manifest")
    if not isinstance(manifest, list):
        raise LinkedGitBrokerError("broker object manifest is unavailable")
    installed = list(state.get("installed_object_oids") or [])
    created = [
        str(entry.get("oid") or "")
        for entry in manifest
        if isinstance(entry, Mapping) and entry.get("prepared_in_quarantine")
    ]
    for entry in manifest:
        if not isinstance(entry, Mapping) or not entry.get("prepared_in_quarantine"):
            continue
        oid = str(entry.get("oid") or "")
        if oid in installed:
            continue
        source = journal.object_quarantine / oid[:2] / oid[2:]
        object_type, content = _loose_object_payload(source, oid)
        if object_type != entry.get("type"):
            raise LinkedGitBrokerError(f"broker object type drifted before install: {oid}")
        args = ["hash-object", "-w", "--literally", "-t", object_type, "--stdin"]
        if object_type == "blob":
            args = ["hash-object", "-w", "--no-filters", "--stdin"]
        installed_oid = _git_text(
            plan.worktree_root,
            *args,
            input_bytes=content,
        )
        if installed_oid != oid:
            raise LinkedGitBrokerError(
                f"shared Git object installation identity mismatch: {oid}"
            )
        installed.append(oid)
        state["phase"] = (
            "objects_installed" if installed == created else "objects_installing"
        )
        state["installed_object_oids"] = installed
        journal.write_state(state)
        _fault(fault_at, test_crash_at, f"object_installed_{len(installed)}")
        if len(installed) == 1 and state["phase"] == "objects_installing":
            _fault(fault_at, test_crash_at, "objects_installing")
    state["phase"] = "objects_installed"
    journal.write_state(state)
    _fault(fault_at, test_crash_at, "objects_installed")


def _fault(selected: str | None, crash_selected: str | None, phase: str) -> None:
    if crash_selected == phase:
        os._exit(97)
    if selected == phase:
        raise LinkedGitBrokerError(f"injected broker fault after {phase}")


def _require_no_symlink_components(path: Path) -> None:
    if not path.is_absolute():
        raise LinkedGitBrokerError(f"broker path must be absolute: {path}")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if not os.path.lexists(current):
            continue
        if stat.S_ISLNK(current.lstat().st_mode):
            raise LinkedGitBrokerError(
                f"broker path contains a symlink component: {current}"
            )


class _BrokerJournal:
    def __init__(self, root: Path, transaction_id: str) -> None:
        raw_root = root.expanduser()
        if not raw_root.is_absolute():
            raise LinkedGitBrokerError("broker journal root must be absolute")
        canonical_lexical = Path(os.path.abspath(raw_root))
        if raw_root != canonical_lexical:
            raise LinkedGitBrokerError(
                "broker journal root must be a canonical path without dot segments"
            )
        self.root = canonical_lexical
        self.transaction_id = transaction_id
        self.directory = self.root / transaction_id
        self.state_path = self.directory / "state.json"
        self.state_temp = self.directory / "state.tmp"
        self.lock_path = self.root / f"{transaction_id}.lock"
        self.original_index = self.directory / "original.index"
        self.prepared_index = self.directory / "prepared.index"
        self.original_ref = self.directory / "original.ref"
        self.original_reflog = self.directory / "original.reflog"
        self.object_quarantine = self.directory / "objects"
        self._lock_fd: int | None = None

    def ensure_private_root(self) -> None:
        _require_no_symlink_components(self.root.parent)
        if not self.root.parent.is_dir():
            raise LinkedGitBrokerError(
                f"broker journal parent is unavailable: {self.root.parent}"
            )
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
            _fsync_directory(self.root.parent)
        self._validate_directory(self.root, mode=0o700, label="journal root")
        _require_no_symlink_components(self.root)

    def prepare_workspace(self) -> None:
        self.directory.mkdir(mode=0o700)
        self._validate_directory(
            self.directory,
            mode=0o700,
            label="transaction directory",
        )
        self.object_quarantine.mkdir(mode=0o700)
        self._validate_directory(
            self.object_quarantine,
            mode=0o700,
            label="object quarantine",
        )
        _fsync_directory(self.directory)
        _fsync_directory(self.root)

    def acquire_lock(self) -> None:
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
        self._lock_fd = os.open(self.lock_path, flags, 0o600)
        try:
            self._validate_regular_fd(
                self._lock_fd,
                mode=0o600,
                label="journal lock",
            )
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, LinkedGitBrokerError) as exc:
            os.close(self._lock_fd)
            self._lock_fd = None
            if isinstance(exc, BlockingIOError):
                raise LinkedGitBrokerError(
                    "linked Git broker transaction is already locked"
                ) from exc
            raise

    def release_lock(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def read_state(self) -> dict[str, Any] | None:
        if self.directory.exists():
            self._validate_directory(
                self.directory,
                mode=0o700,
                label="transaction directory",
            )
            self._validate_directory_entries()
        self._reconcile_state_temp()
        if not self.state_path.exists():
            return None
        return self._read_json(self.state_path)

    def write_state(self, state: Mapping[str, Any]) -> None:
        self._validate_directory(
            self.directory,
            mode=0o700,
            label="transaction directory",
        )
        self._reconcile_state_temp()
        payload = json.dumps(
            state,
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        descriptor = os.open(
            self.state_temp,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
        os.replace(self.state_temp, self.state_path)
        _fsync_directory(self.directory)

    def remove_unstarted_workspace(self) -> None:
        self._validate_directory(
            self.directory,
            mode=0o700,
            label="transaction directory",
        )
        self._validate_directory_entries()
        for child in tuple(self.directory.iterdir()):
            if child.name not in JOURNAL_ARTIFACT_NAMES:
                raise LinkedGitBrokerError(
                    f"unexpected broker scratch entry: {child}"
                )
            if child == self.object_quarantine:
                _remove_private_object_quarantine(child)
            else:
                _read_private_file(child)
                child.unlink()
        self.directory.rmdir()
        _fsync_directory(self.root)

    def _reconcile_state_temp(self) -> None:
        if not self.state_temp.exists():
            return
        value = self._read_json(self.state_temp)
        if not isinstance(value, dict):
            raise LinkedGitBrokerError("orphan broker state.tmp is malformed")
        os.replace(self.state_temp, self.state_path)
        _fsync_directory(self.directory)

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            payload = _read_private_file(path)
            value = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LinkedGitBrokerError(f"unable to read broker journal: {exc}") from exc
        if not isinstance(value, dict):
            raise LinkedGitBrokerError("broker journal is malformed")
        return value

    def _validate_directory_entries(self) -> None:
        for child in self.directory.iterdir():
            if child.name not in JOURNAL_ARTIFACT_NAMES:
                raise LinkedGitBrokerError(
                    f"unexpected broker journal entry: {child}"
                )
            if child.name == "objects":
                self._validate_private_object_quarantine(child)
                continue
            metadata = child.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise LinkedGitBrokerError(f"unsafe broker journal entry: {child}")

    @classmethod
    def _validate_private_object_quarantine(cls, root: Path) -> None:
        cls._validate_directory(root, mode=0o700, label="object quarantine")
        for child in root.rglob("*"):
            metadata = child.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise LinkedGitBrokerError(
                    f"unsafe broker object-quarantine entry: {child}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                if stat.S_IMODE(metadata.st_mode) != 0o700:
                    raise LinkedGitBrokerError(
                        f"unsafe broker object-quarantine directory: {child}"
                    )
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise LinkedGitBrokerError(
                    f"unsafe broker object-quarantine file: {child}"
                )

    @staticmethod
    def _validate_directory(path: Path, *, mode: int, label: str) -> None:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise LinkedGitBrokerError(f"unsafe broker {label}: {path}")

    @staticmethod
    def _validate_regular_fd(fd: int, *, mode: int, label: str) -> None:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != mode
        ):
            raise LinkedGitBrokerError(f"unsafe broker {label}")
