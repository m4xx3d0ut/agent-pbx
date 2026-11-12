from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import difflib
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


GIT_REVIEW_ARTIFACT_SCHEMA = "agent-pbx.git-review-artifact/v1"
GIT_REVIEW_MAX_SOURCE_BYTES = 1024 * 1024
GIT_REVIEW_MAX_PATH_DIFF_BYTES = 32 * 1024
GIT_REVIEW_MAX_TOTAL_DIFF_BYTES = 256 * 1024
GIT_REVIEW_MAX_PATHS = 512

_HEX_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_HEX_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_PATH_STATUS = frozenset({"added", "deleted", "modified", "mode_changed"})
_CONTENT_KIND = frozenset({"text", "binary", "large"})


class GitReviewArtifactError(ValueError):
    """An exact broker review artifact is malformed or no longer trustworthy."""


@dataclass(frozen=True)
class GitReviewContent:
    """Bounded content supplied for one already-approved Git leaf."""

    mode: str | None
    oid: str | None
    size: int
    prefix: bytes
    complete: bool


@dataclass(frozen=True)
class GitReviewPath:
    path: str
    status: str
    old_mode: str | None
    new_mode: str | None
    old_oid: str | None
    new_oid: str | None
    old_size: int
    new_size: int
    content_kind: str
    additions: int
    deletions: int
    unified_diff: str
    truncated: bool
    truncation_reason: str | None


@dataclass(frozen=True)
class GitReviewArtifact:
    schema: str
    agent_id: str
    branch_ref: str
    head: str
    head_tree: str
    index_tree: str
    index_sha256: str
    parent_oids: tuple[str, ...]
    approved_paths: tuple[str, ...]
    paths: tuple[GitReviewPath, ...]
    total_additions: int
    total_deletions: int
    binary_path_count: int
    truncated_path_count: int
    rendered_diff_bytes: int
    truncated: bool
    sha256: str

    def unsigned_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("sha256")
        return value

    def public_dict(self) -> dict[str, Any]:
        return json.loads(
            json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))
        )

    def render_full_diff(self) -> str:
        blocks: list[str] = []
        for entry in self.paths:
            if entry.unified_diff:
                blocks.append(entry.unified_diff)
                continue
            blocks.append(
                "\n".join(
                    (
                        "diff --agent-pbx "
                        f"a/{_display_path(entry.path)} "
                        f"b/{_display_path(entry.path)}",
                        f"status {entry.status}",
                        f"content {entry.content_kind}",
                        f"old mode {entry.old_mode or '-'}",
                        f"new mode {entry.new_mode or '-'}",
                        f"old oid {entry.old_oid or '-'}",
                        f"new oid {entry.new_oid or '-'}",
                        f"old size {entry.old_size}",
                        f"new size {entry.new_size}",
                        (
                            f"review truncated: {entry.truncation_reason}"
                            if entry.truncated
                            else "no textual body"
                        ),
                    )
                )
            )
        return "\n\n".join(blocks)

    @classmethod
    def from_public_dict(cls, value: Mapping[str, Any]) -> "GitReviewArtifact":
        expected = {
            "schema",
            "agent_id",
            "branch_ref",
            "head",
            "head_tree",
            "index_tree",
            "index_sha256",
            "parent_oids",
            "approved_paths",
            "paths",
            "total_additions",
            "total_deletions",
            "binary_path_count",
            "truncated_path_count",
            "rendered_diff_bytes",
            "truncated",
            "sha256",
        }
        if set(value) != expected:
            raise GitReviewArtifactError("Git review artifact schema fields mismatch")
        if value.get("schema") != GIT_REVIEW_ARTIFACT_SCHEMA:
            raise GitReviewArtifactError("Git review artifact schema version mismatch")

        agent_id = _text(value, "agent_id")
        branch_ref = _text(value, "branch_ref")
        head = _oid(value, "head")
        head_tree = _oid(value, "head_tree")
        index_tree = _oid(value, "index_tree")
        index_sha256 = _sha256(value, "index_sha256")
        parent_oids = _string_tuple(value, "parent_oids")
        if not parent_oids or any(not _HEX_OID_RE.fullmatch(oid) for oid in parent_oids):
            raise GitReviewArtifactError("Git review artifact parents are invalid")
        if parent_oids[0] != head or len(parent_oids) != len(set(parent_oids)):
            raise GitReviewArtifactError("Git review artifact parent binding is invalid")
        approved_paths = _string_tuple(value, "approved_paths")
        if (
            not approved_paths
            or len(approved_paths) > GIT_REVIEW_MAX_PATHS
            or approved_paths != tuple(sorted(approved_paths))
            or len(approved_paths) != len(set(approved_paths))
        ):
            raise GitReviewArtifactError("Git review artifact paths are not canonical")

        raw_paths = value.get("paths")
        if not isinstance(raw_paths, list):
            raise GitReviewArtifactError("Git review artifact path details must be a list")
        path_fields = {
            "path",
            "status",
            "old_mode",
            "new_mode",
            "old_oid",
            "new_oid",
            "old_size",
            "new_size",
            "content_kind",
            "additions",
            "deletions",
            "unified_diff",
            "truncated",
            "truncation_reason",
        }
        paths: list[GitReviewPath] = []
        for item in raw_paths:
            if not isinstance(item, Mapping) or set(item) != path_fields:
                raise GitReviewArtifactError("Git review artifact path schema mismatch")
            path = _mapping_text(item, "path")
            status = _mapping_text(item, "status")
            content_kind = _mapping_text(item, "content_kind")
            if status not in _PATH_STATUS or content_kind not in _CONTENT_KIND:
                raise GitReviewArtifactError("Git review artifact path classification is invalid")
            old_mode = _optional_mode(item, "old_mode")
            new_mode = _optional_mode(item, "new_mode")
            old_oid = _optional_oid(item, "old_oid")
            new_oid = _optional_oid(item, "new_oid")
            if old_oid is None and new_oid is None:
                raise GitReviewArtifactError("Git review artifact path has no object identity")
            truncated = _mapping_bool(item, "truncated")
            reason = item.get("truncation_reason")
            if reason is not None and not isinstance(reason, str):
                raise GitReviewArtifactError("Git review artifact truncation reason is invalid")
            if truncated != (reason is not None):
                raise GitReviewArtifactError(
                    "Git review artifact truncation evidence is inconsistent"
                )
            if reason not in {None, "source_content_limit", "rendered_diff_limit"}:
                raise GitReviewArtifactError(
                    "Git review artifact truncation reason is unsupported"
                )
            unified_diff = _mapping_text(item, "unified_diff")
            if len(unified_diff.encode("utf-8")) > GIT_REVIEW_MAX_PATH_DIFF_BYTES:
                raise GitReviewArtifactError(
                    "Git review artifact path diff exceeds its bound"
                )
            paths.append(
                GitReviewPath(
                    path=path,
                    status=status,
                    old_mode=old_mode,
                    new_mode=new_mode,
                    old_oid=old_oid,
                    new_oid=new_oid,
                    old_size=_mapping_nonnegative_int(item, "old_size"),
                    new_size=_mapping_nonnegative_int(item, "new_size"),
                    content_kind=content_kind,
                    additions=_mapping_nonnegative_int(item, "additions"),
                    deletions=_mapping_nonnegative_int(item, "deletions"),
                    unified_diff=unified_diff,
                    truncated=truncated,
                    truncation_reason=reason,
                )
            )
        if tuple(item.path for item in paths) != approved_paths:
            raise GitReviewArtifactError("Git review artifact details do not match approval")

        artifact = cls(
            schema=GIT_REVIEW_ARTIFACT_SCHEMA,
            agent_id=agent_id,
            branch_ref=branch_ref,
            head=head,
            head_tree=head_tree,
            index_tree=index_tree,
            index_sha256=index_sha256,
            parent_oids=parent_oids,
            approved_paths=approved_paths,
            paths=tuple(paths),
            total_additions=_nonnegative_int(value, "total_additions"),
            total_deletions=_nonnegative_int(value, "total_deletions"),
            binary_path_count=_nonnegative_int(value, "binary_path_count"),
            truncated_path_count=_nonnegative_int(value, "truncated_path_count"),
            rendered_diff_bytes=_nonnegative_int(value, "rendered_diff_bytes"),
            truncated=_bool(value, "truncated"),
            sha256=_sha256(value, "sha256"),
        )
        if artifact.total_additions != sum(item.additions for item in artifact.paths):
            raise GitReviewArtifactError("Git review artifact addition count mismatch")
        if artifact.total_deletions != sum(item.deletions for item in artifact.paths):
            raise GitReviewArtifactError("Git review artifact deletion count mismatch")
        if artifact.binary_path_count != sum(
            item.content_kind == "binary" for item in artifact.paths
        ):
            raise GitReviewArtifactError("Git review artifact binary count mismatch")
        if artifact.truncated_path_count != sum(item.truncated for item in artifact.paths):
            raise GitReviewArtifactError("Git review artifact truncation count mismatch")
        rendered_bytes = sum(len(item.unified_diff.encode("utf-8")) for item in artifact.paths)
        if artifact.rendered_diff_bytes != rendered_bytes:
            raise GitReviewArtifactError("Git review artifact rendered-byte count mismatch")
        if artifact.rendered_diff_bytes > GIT_REVIEW_MAX_TOTAL_DIFF_BYTES:
            raise GitReviewArtifactError("Git review artifact exceeds its total bound")
        if artifact.truncated != any(item.truncated for item in artifact.paths):
            raise GitReviewArtifactError("Git review artifact truncation flag mismatch")
        if _digest(artifact.unsigned_dict()) != artifact.sha256:
            raise GitReviewArtifactError("Git review artifact digest mismatch")
        return artifact


def build_git_review_artifact(
    *,
    agent_id: str,
    branch_ref: str,
    head: str,
    head_tree: str,
    index_tree: str,
    index_sha256: str,
    parent_oids: Iterable[str],
    approved_paths: Iterable[str],
    old_content: Mapping[str, GitReviewContent],
    new_content: Mapping[str, GitReviewContent],
) -> GitReviewArtifact:
    """Build a bounded artifact from only the broker's exact approved leaves."""

    canonical_paths = tuple(approved_paths)
    if (
        not canonical_paths
        or len(canonical_paths) > GIT_REVIEW_MAX_PATHS
        or canonical_paths != tuple(sorted(canonical_paths))
        or len(canonical_paths) != len(set(canonical_paths))
        or set(old_content) != set(canonical_paths)
        or set(new_content) != set(canonical_paths)
    ):
        raise GitReviewArtifactError("Git review inputs do not match exact approval")
    parents = tuple(parent_oids)
    if not parents or parents[0] != head:
        raise GitReviewArtifactError("Git review parents do not bind the approved HEAD")

    remaining = GIT_REVIEW_MAX_TOTAL_DIFF_BYTES
    details: list[GitReviewPath] = []
    for path in canonical_paths:
        item = _review_path(
            path,
            old_content[path],
            new_content[path],
            maximum_diff_bytes=min(GIT_REVIEW_MAX_PATH_DIFF_BYTES, remaining),
        )
        remaining -= len(item.unified_diff.encode("utf-8"))
        details.append(item)

    artifact = GitReviewArtifact(
        schema=GIT_REVIEW_ARTIFACT_SCHEMA,
        agent_id=agent_id,
        branch_ref=branch_ref,
        head=head,
        head_tree=head_tree,
        index_tree=index_tree,
        index_sha256=index_sha256,
        parent_oids=parents,
        approved_paths=canonical_paths,
        paths=tuple(details),
        total_additions=sum(item.additions for item in details),
        total_deletions=sum(item.deletions for item in details),
        binary_path_count=sum(item.content_kind == "binary" for item in details),
        truncated_path_count=sum(item.truncated for item in details),
        rendered_diff_bytes=sum(len(item.unified_diff.encode("utf-8")) for item in details),
        truncated=any(item.truncated for item in details),
        sha256="",
    )
    return replace(artifact, sha256=_digest(artifact.unsigned_dict()))


def _review_path(
    path: str,
    old: GitReviewContent,
    new: GitReviewContent,
    *,
    maximum_diff_bytes: int,
) -> GitReviewPath:
    status = _status(old, new)
    truncated = False
    reason: str | None = None
    additions = 0
    deletions = 0
    diff_text = ""

    if not old.complete or not new.complete:
        kind = "large"
        truncated = True
        reason = "source_content_limit"
    elif _is_binary(old.prefix) or _is_binary(new.prefix):
        kind = "binary"
    else:
        kind = "text"
        old_text = old.prefix.decode("utf-8", errors="strict")
        new_text = new.prefix.decode("utf-8", errors="strict")
        lines = list(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{_display_path(path)}",
                tofile=f"b/{_display_path(path)}",
                lineterm="",
            )
        )
        full = "\n".join(lines)
        if full and not full.endswith("\n"):
            full += "\n"
        additions = sum(
            line.startswith("+") and not line.startswith("+++")
            for line in lines
        )
        deletions = sum(
            line.startswith("-") and not line.startswith("---")
            for line in lines
        )
        diff_text, clipped = _truncate_utf8(full, maximum_diff_bytes)
        if clipped:
            truncated = True
            reason = "rendered_diff_limit"

    return GitReviewPath(
        path=path,
        status=status,
        old_mode=old.mode,
        new_mode=new.mode,
        old_oid=old.oid,
        new_oid=new.oid,
        old_size=old.size,
        new_size=new.size,
        content_kind=kind,
        additions=additions,
        deletions=deletions,
        unified_diff=diff_text,
        truncated=truncated,
        truncation_reason=reason,
    )


def _status(old: GitReviewContent, new: GitReviewContent) -> str:
    if old.oid is None:
        return "added"
    if new.oid is None:
        return "deleted"
    if old.oid == new.oid and old.mode != new.mode:
        return "mode_changed"
    return "modified"


def _is_binary(payload: bytes) -> bool:
    if b"\0" in payload:
        return True
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return True
    return any(
        (ord(character) < 32 and character not in {"\t", "\n", "\r"})
        or ord(character) == 127
        for character in text
    )


def _truncate_utf8(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    clipped = encoded[: max(0, limit)]
    while clipped:
        try:
            return clipped.decode("utf-8"), True
        except UnicodeDecodeError as exc:
            clipped = clipped[: exc.start]
    return "", True


def _display_path(value: str) -> str:
    result: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\\":
            result.append("\\\\")
        elif character == "\n":
            result.append("\\n")
        elif character == "\r":
            result.append("\\r")
        elif character == "\t":
            result.append("\\t")
        elif codepoint < 32 or codepoint == 127:
            result.append(f"\\x{codepoint:02x}")
        else:
            result.append(character)
    return "".join(result)


def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text(value: Mapping[str, Any], key: str) -> str:
    return _mapping_text(value, key)


def _mapping_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or "\0" in item:
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item


def _oid(value: Mapping[str, Any], key: str) -> str:
    item = _text(value, key)
    if not _HEX_OID_RE.fullmatch(item):
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item


def _sha256(value: Mapping[str, Any], key: str) -> str:
    item = _text(value, key)
    if not _HEX_SHA256_RE.fullmatch(item):
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item


def _string_tuple(value: Mapping[str, Any], key: str) -> tuple[str, ...]:
    item = value.get(key)
    if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return tuple(item)


def _nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    return _mapping_nonnegative_int(value, key)


def _mapping_nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if type(item) is not int or item < 0:
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item


def _bool(value: Mapping[str, Any], key: str) -> bool:
    return _mapping_bool(value, key)


def _mapping_bool(value: Mapping[str, Any], key: str) -> bool:
    item = value.get(key)
    if type(item) is not bool:
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item


def _optional_mode(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not re.fullmatch(r"[0-7]{6}", item):
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item


def _optional_oid(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not _HEX_OID_RE.fullmatch(item):
        raise GitReviewArtifactError(f"Git review artifact {key} is invalid")
    return item
