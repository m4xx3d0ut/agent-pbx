from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .contracts import KB_BUNDLE_V2_CONTRACT
from .store import Store


KB_BUNDLE_V2_SCHEMA = KB_BUNDLE_V2_CONTRACT.schema
KB_LOGICAL_PROJECT_SCHEMA = "agent-pbx.logical-project/v1"
KB_GIT_TREE_SCHEMA = "agent-pbx.operator-kb-git-tree/v1"
KB_SYNC_NAMESPACE = uuid.UUID("f3cd99b7-342d-49b4-bb79-3f8f9a0b0aa6")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STABLE_ID_RE = re.compile(r"^kbv2:[0-9a-f]{64}$")
MAX_BUNDLE_ENTRIES = 1000
MAX_ENTRY_BODY_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
KB_ENTRY_SCOPES = {"global", "project", "repo", "operator", "caller"}
KB_ENTRY_STATUSES = {"proposed", "active", "retired", "rejected"}


class OperatorKbSyncError(ValueError):
    pass


@dataclass(frozen=True)
class LogicalProjectIdentity:
    logical_project_id: str
    project: str
    repo_root: str | None
    git_remote: str | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "schema": KB_LOGICAL_PROJECT_SCHEMA,
            "logical_project_id": self.logical_project_id,
            "project": self.project,
            "repo_root": self.repo_root,
            "git_remote": self.git_remote,
        }


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def logical_project_identity(
    *,
    project: str,
    repo_root: str | None = None,
    git_remote: str | None = None,
) -> LogicalProjectIdentity:
    normalized_project = str(project or "").strip()
    normalized_root = str(repo_root or "").strip() or None
    normalized_remote = str(git_remote or "").strip() or None
    if not normalized_project:
        raise OperatorKbSyncError("logical project name is required")
    if len(normalized_project) > 240:
        raise OperatorKbSyncError("logical project name is too long")
    identity_payload = {
        "schema": KB_LOGICAL_PROJECT_SCHEMA,
        "project": normalized_project,
        "git_remote": normalized_remote,
    }
    digest = sha256_bytes(canonical_json(identity_payload))
    return LogicalProjectIdentity(
        logical_project_id=f"project:{digest}",
        project=normalized_project,
        repo_root=normalized_root,
        git_remote=normalized_remote,
    )


def _stable_id(project_id: str, entry: Mapping[str, Any]) -> str:
    metadata = entry.get("metadata")
    sync_metadata = metadata.get("kb_bundle_v2") if isinstance(metadata, Mapping) else None
    imported = sync_metadata.get("stable_id") if isinstance(sync_metadata, Mapping) else None
    if imported:
        if not STABLE_ID_RE.fullmatch(str(imported)):
            raise OperatorKbSyncError("stored KB stable identity is malformed")
        return str(imported)
    kb_id = str(entry.get("kb_id") or "").strip()
    if not kb_id:
        raise OperatorKbSyncError("KB entry has no stable local identity")
    stable_material = project_id.encode() + b"\0" + kb_id.encode()
    return f"kbv2:{sha256_bytes(stable_material)}"


def _entry_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    body = str(entry.get("body") or "")
    if not body or len(body.encode("utf-8")) > MAX_ENTRY_BODY_BYTES:
        raise OperatorKbSyncError("KB entry body is empty or exceeds the bundle limit")
    return {
        "scope": str(entry.get("scope") or "project"),
        "project": entry.get("project"),
        "repo_root": entry.get("repo_root"),
        "git_remote": entry.get("git_remote"),
        "branch": entry.get("branch"),
        "title": str(entry.get("title") or ""),
        "summary": str(entry.get("summary") or ""),
        "body": body,
        "tags": sorted({str(value) for value in entry.get("tags", []) if str(value)}),
        "redaction_status": str(entry.get("redaction_status") or "unreviewed"),
        "source_knowledge_link_id": entry.get("source_knowledge_link_id"),
        "source_handoff_id": entry.get("source_handoff_id"),
        "source_turn_ids": sorted(
            {str(value) for value in entry.get("source_turn_ids", []) if str(value)}
        ),
        "stale_after": entry.get("stale_after"),
        "expires_at": entry.get("expires_at"),
    }


def _reviewer_evidence(entry: Mapping[str, Any], content_hash: str) -> dict[str, Any]:
    metadata = entry.get("metadata")
    reviewer = (
        metadata.get("promoted_by_operator_agent_id")
        if isinstance(metadata, Mapping)
        else None
    )
    return {
        "status": str(entry.get("status") or "proposed"),
        "reviewer_operator_agent_id": str(reviewer or "") or None,
        "promoted_at": entry.get("promoted_at"),
        "redaction_status": str(entry.get("redaction_status") or "unreviewed"),
        "content_hash": content_hash,
    }


class OperatorKbBundleV2Service:
    def __init__(self, store: Store) -> None:
        self.store = store

    def export_bundle(
        self,
        *,
        entries: Sequence[Mapping[str, Any]],
        logical_project: LogicalProjectIdentity,
        parent_bundle_sha256: str | None = None,
        redaction_exclusions: Sequence[Mapping[str, str]] = (),
    ) -> dict[str, Any]:
        if parent_bundle_sha256 is not None and not SHA256_RE.fullmatch(
            parent_bundle_sha256
        ):
            raise OperatorKbSyncError("parent bundle digest is invalid")
        records: list[dict[str, Any]] = []
        for entry in entries:
            if entry.get("project") not in {None, logical_project.project}:
                raise OperatorKbSyncError("KB entry project differs from logical project")
            stable_id = _stable_id(logical_project.logical_project_id, entry)
            tombstone = str(entry.get("status") or "") in {"retired", "rejected"}
            sync = self.store.get_operator_kb_sync_record(
                logical_project_id=logical_project.logical_project_id,
                stable_id=stable_id,
            )
            if tombstone:
                content = {
                    "stable_id": stable_id,
                    "tombstone": True,
                    "terminal_status": str(entry.get("status")),
                }
                entry_payload: dict[str, Any] | None = None
            else:
                entry_payload = _entry_payload(entry)
                content = {
                    "stable_id": stable_id,
                    "tombstone": False,
                    "entry": entry_payload,
                }
            content_hash = sha256_bytes(canonical_json(content))
            if sync and sync["content_hash"] == content_hash:
                revision = int(sync["revision"])
                parent_content_hash = sync.get("parent_content_hash")
            elif sync:
                revision = int(sync["revision"]) + 1
                parent_content_hash = str(sync["content_hash"])
            else:
                revision = 1
                parent_content_hash = None
            records.append(
                {
                    "stable_id": stable_id,
                    "origin_kb_id": str(entry.get("kb_id") or ""),
                    "revision": revision,
                    "parent_content_hash": parent_content_hash,
                    "content_hash": content_hash,
                    "tombstone": tombstone,
                    "entry": entry_payload,
                    "reviewer_evidence": _reviewer_evidence(entry, content_hash),
                }
            )
        records.sort(key=lambda item: item["stable_id"])
        exclusions = sorted(
            (
                {
                    "kb_id": str(item.get("kb_id") or ""),
                    "reason": str(item.get("reason") or ""),
                }
                for item in redaction_exclusions
            ),
            key=lambda item: item["kb_id"],
        )
        if any(not item["kb_id"] or not item["reason"] for item in exclusions):
            raise OperatorKbSyncError("KB redaction exclusion is invalid")
        if len({item["kb_id"] for item in exclusions}) != len(exclusions):
            raise OperatorKbSyncError("KB redaction exclusions are not unique")
        unsigned = {
            "schema": KB_BUNDLE_V2_SCHEMA,
            "logical_project": logical_project.public_dict(),
            "parent_bundle_sha256": parent_bundle_sha256,
            "redaction_exclusions": exclusions,
            "entries": records,
        }
        if len(canonical_json(unsigned)) > MAX_BUNDLE_BYTES:
            raise OperatorKbSyncError("KB bundle exceeds the byte limit")
        return {**unsigned, "bundle_sha256": sha256_bytes(canonical_json(unsigned))}

    @classmethod
    def validate_bundle(cls, bundle: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "schema",
            "logical_project",
            "parent_bundle_sha256",
            "redaction_exclusions",
            "entries",
            "bundle_sha256",
        }
        if not isinstance(bundle, Mapping) or set(bundle) != expected:
            raise OperatorKbSyncError("KB bundle v2 fields are invalid")
        if bundle.get("schema") != KB_BUNDLE_V2_SCHEMA:
            raise OperatorKbSyncError("unsupported KB bundle schema")
        project = bundle.get("logical_project")
        if not isinstance(project, Mapping) or set(project) != {
            "schema",
            "logical_project_id",
            "project",
            "repo_root",
            "git_remote",
        }:
            raise OperatorKbSyncError("KB logical project identity is invalid")
        if (
            not isinstance(project.get("project"), str)
            or not project["project"].strip()
            or any(
                value is not None and not isinstance(value, str)
                for value in (project.get("repo_root"), project.get("git_remote"))
            )
        ):
            raise OperatorKbSyncError("KB logical project values are invalid")
        expected_project = logical_project_identity(
            project=str(project.get("project") or ""),
            repo_root=project.get("repo_root"),
            git_remote=project.get("git_remote"),
        )
        if project.get("schema") != KB_LOGICAL_PROJECT_SCHEMA or (
            project.get("logical_project_id") != expected_project.logical_project_id
        ):
            raise OperatorKbSyncError("KB logical project digest is invalid")
        parent = bundle.get("parent_bundle_sha256")
        if parent is not None and not SHA256_RE.fullmatch(str(parent)):
            raise OperatorKbSyncError("KB parent bundle digest is invalid")
        exclusions = bundle.get("redaction_exclusions")
        if not isinstance(exclusions, list):
            raise OperatorKbSyncError("KB redaction exclusions are invalid")
        normalized_exclusions: list[dict[str, str]] = []
        for item in exclusions:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"kb_id", "reason"}
                or not isinstance(item.get("kb_id"), str)
                or not item["kb_id"]
                or len(item["kb_id"]) > 120
                or not isinstance(item.get("reason"), str)
                or not item["reason"]
                or len(item["reason"]) > 240
            ):
                raise OperatorKbSyncError("KB redaction exclusion is invalid")
            normalized_exclusions.append(dict(item))
        if normalized_exclusions != sorted(
            normalized_exclusions,
            key=lambda item: item["kb_id"],
        ):
            raise OperatorKbSyncError("KB redaction exclusions are not sorted")
        if len({item["kb_id"] for item in normalized_exclusions}) != len(
            normalized_exclusions
        ):
            raise OperatorKbSyncError("KB redaction exclusions are not unique")
        entries = bundle.get("entries")
        if not isinstance(entries, list) or len(entries) > MAX_BUNDLE_ENTRIES:
            raise OperatorKbSyncError("KB bundle entry list is invalid")
        stable_ids: list[str] = []
        normalized: list[dict[str, Any]] = []
        for record in entries:
            normalized.append(cls._validate_record(record))
            stable_ids.append(normalized[-1]["stable_id"])
        if stable_ids != sorted(stable_ids) or len(set(stable_ids)) != len(stable_ids):
            raise OperatorKbSyncError("KB bundle stable IDs are not unique and sorted")
        unsigned = {key: bundle[key] for key in expected if key != "bundle_sha256"}
        digest = sha256_bytes(canonical_json(unsigned))
        if bundle.get("bundle_sha256") != digest:
            raise OperatorKbSyncError("KB bundle digest mismatch")
        if len(canonical_json(bundle)) > MAX_BUNDLE_BYTES:
            raise OperatorKbSyncError("KB bundle exceeds the byte limit")
        return {
            **dict(bundle),
            "logical_project": expected_project.public_dict(),
            "redaction_exclusions": normalized_exclusions,
            "entries": normalized,
        }

    def preview_import(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        validated = self.validate_bundle(bundle)
        project_id = validated["logical_project"]["logical_project_id"]
        actions: list[dict[str, Any]] = []
        for record in validated["entries"]:
            sync = self.store.get_operator_kb_sync_record(
                logical_project_id=project_id,
                stable_id=record["stable_id"],
            )
            local_kb_id = self._deterministic_local_kb_id(
                project_id,
                record["stable_id"],
            )
            local_entry = self.store.get_operator_kb_entry(local_kb_id)
            origin_entry = self.store.get_operator_kb_entry(record["origin_kb_id"])
            if sync is None and origin_entry is not None:
                if self._local_entry_content_hash(origin_entry, record["stable_id"]) == record[
                    "content_hash"
                ]:
                    action = "adopt"
                    reason = "origin entry matches the exported content"
                    local_kb_id = record["origin_kb_id"]
                else:
                    action = "conflict"
                    reason = "origin entry content differs from the bundle"
            elif sync is None and local_entry is None:
                action = "tombstone" if record["tombstone"] else "create"
                reason = "new stable identity"
            elif sync is None:
                if self._local_entry_content_hash(local_entry, record["stable_id"]) == record[
                    "content_hash"
                ]:
                    action = "recover"
                    reason = "deterministic local entry exists without sync state"
                else:
                    action = "conflict"
                    reason = "deterministic local entry differs from the bundle"
            elif (
                sync["content_hash"] == record["content_hash"]
                and int(sync["revision"]) == record["revision"]
                and bool(sync["tombstone"]) == record["tombstone"]
            ):
                action = "noop"
                reason = "content and revision already imported"
            elif (
                record["parent_content_hash"] == sync["content_hash"]
                and record["revision"] == int(sync["revision"]) + 1
            ):
                action = "tombstone" if record["tombstone"] else "update"
                reason = "linear successor"
            else:
                action = "conflict"
                reason = "incoming revision does not descend from local content"
            actions.append(
                {
                    "stable_id": record["stable_id"],
                    "action": action,
                    "reason": reason,
                    "local_kb_id": sync.get("local_kb_id") if sync else local_kb_id,
                    "incoming_revision": record["revision"],
                    "local_revision": int(sync["revision"]) if sync else None,
                }
            )
        counts = {
            name: sum(item["action"] == name for item in actions)
            for name in (
                "create",
                "update",
                "tombstone",
                "recover",
                "adopt",
                "noop",
                "conflict",
            )
        }
        return {
            "schema": "agent-pbx.operator-kb-reconcile-preview/v1",
            "bundle_sha256": validated["bundle_sha256"],
            "logical_project_id": project_id,
            "actions": actions,
            "counts": counts,
            "writes_state": False,
        }

    def import_bundle(
        self,
        *,
        bundle: Mapping[str, Any],
        logical_operator_agent_id: str,
        importing_operator_agent_id: str,
        redaction_status_by_stable_id: Mapping[str, str],
        trusted_reviewer_ids: set[str],
    ) -> dict[str, Any]:
        validated = self.validate_bundle(bundle)
        preview = self.preview_import(validated)
        if preview["counts"]["conflict"]:
            raise OperatorKbSyncError("KB bundle has unresolved conflicts")
        project_id = validated["logical_project"]["logical_project_id"]
        actions_by_id = {item["stable_id"]: item for item in preview["actions"]}
        applied: list[dict[str, Any]] = []
        for record in validated["entries"]:
            action = actions_by_id[record["stable_id"]]["action"]
            if action == "noop":
                applied.append({"stable_id": record["stable_id"], "action": "noop"})
                continue
            local_kb_id = str(
                actions_by_id[record["stable_id"]].get("local_kb_id")
                or self._deterministic_local_kb_id(project_id, record["stable_id"])
            )
            local_entry = self.store.get_operator_kb_entry(local_kb_id)
            sync = self.store.get_operator_kb_sync_record(
                logical_project_id=project_id,
                stable_id=record["stable_id"],
            )
            trusted_reviewer = bool(
                sync
                and sync.get("trusted_reviewer")
                and sync.get("content_hash") == record["content_hash"]
            )
            if action == "adopt" and local_entry is not None:
                trusted_reviewer = self._local_reviewer_is_trusted(
                    local_entry,
                    record["reviewer_evidence"],
                    trusted_reviewer_ids,
                )
            if action == "adopt":
                pass
            elif record["tombstone"]:
                if local_entry is not None:
                    self.store.update_operator_kb_entry(
                        local_kb_id,
                        status="retired",
                        metadata={
                            "kb_bundle_v2_tombstone": True,
                            "kb_bundle_v2_source": validated["bundle_sha256"],
                        },
                        event_type="operator_kb_bundle_v2_tombstoned",
                        operator_agent_id=importing_operator_agent_id,
                        event_summary="KB bundle v2 tombstone applied",
                    )
            else:
                entry_payload = record["entry"]
                assert isinstance(entry_payload, dict)
                imported_status = "active" if trusted_reviewer else "proposed"
                imported_redaction = redaction_status_by_stable_id.get(
                    record["stable_id"],
                    "unreviewed",
                )
                metadata = {
                    "kb_bundle_v2": {
                        "stable_id": record["stable_id"],
                        "revision": record["revision"],
                        "content_hash": record["content_hash"],
                        "bundle_sha256": validated["bundle_sha256"],
                    },
                    "imported_by_operator_agent_id": importing_operator_agent_id,
                    "source_evidence": {
                        "knowledge_link_id": entry_payload[
                            "source_knowledge_link_id"
                        ],
                        "handoff_id": entry_payload["source_handoff_id"],
                        "turn_ids": entry_payload["source_turn_ids"],
                    },
                }
                if local_entry is None:
                    local_entry = self.store.create_operator_kb_entry(
                        kb_id=local_kb_id,
                        scope=entry_payload["scope"],
                        project=entry_payload["project"],
                        repo_root=entry_payload["repo_root"],
                        git_remote=entry_payload["git_remote"],
                        branch=entry_payload["branch"],
                        title=entry_payload["title"],
                        summary=entry_payload["summary"],
                        body=entry_payload["body"],
                        tags=entry_payload["tags"],
                        status=imported_status,
                        redaction_status=imported_redaction,
                        created_by_operator_agent_id=logical_operator_agent_id,
                        created_by_agent_id=logical_operator_agent_id,
                        source_knowledge_link_id=None,
                        source_handoff_id=None,
                        source_turn_ids=[],
                        stale_after=entry_payload["stale_after"],
                        expires_at=entry_payload["expires_at"],
                        imported_at=time.time(),
                        metadata=metadata,
                    )
                else:
                    self.store.update_operator_kb_entry(
                        local_kb_id,
                        status=imported_status,
                        redaction_status=imported_redaction,
                        summary=entry_payload["summary"],
                        updates={
                            key: entry_payload[key]
                            for key in (
                                "scope",
                                "project",
                                "repo_root",
                                "git_remote",
                                "branch",
                                "title",
                                "body",
                                "tags",
                                "stale_after",
                                "expires_at",
                            )
                        },
                        metadata=metadata,
                        event_type="operator_kb_bundle_v2_updated",
                        operator_agent_id=importing_operator_agent_id,
                        event_summary="KB bundle v2 successor imported",
                    )
            reviewer_evidence = record["reviewer_evidence"]
            self.store.upsert_operator_kb_sync_record(
                logical_project_id=project_id,
                stable_id=record["stable_id"],
                local_kb_id=None if record["tombstone"] and local_entry is None else local_kb_id,
                revision=record["revision"],
                parent_content_hash=record["parent_content_hash"],
                content_hash=record["content_hash"],
                tombstone=record["tombstone"],
                trusted_reviewer=trusted_reviewer,
                reviewer_evidence=reviewer_evidence,
                source_bundle_sha256=validated["bundle_sha256"],
            )
            applied.append({"stable_id": record["stable_id"], "action": action})
        return {
            "schema": "agent-pbx.operator-kb-import-result/v2",
            "bundle_sha256": validated["bundle_sha256"],
            "logical_project_id": project_id,
            "applied": applied,
            "applied_count": sum(item["action"] != "noop" for item in applied),
            "unchanged_count": sum(item["action"] == "noop" for item in applied),
        }

    def prepare_git_tree(self, bundle: Mapping[str, Any]) -> dict[str, Any]:
        validated = self.validate_bundle(bundle)
        files: dict[str, str] = {
            "kb/manifest.json": canonical_json(
                {
                    "schema": validated["schema"],
                    "logical_project": validated["logical_project"],
                    "parent_bundle_sha256": validated["parent_bundle_sha256"],
                    "redaction_exclusions": validated["redaction_exclusions"],
                    "bundle_sha256": validated["bundle_sha256"],
                    "entry_count": len(validated["entries"]),
                }
            ).decode("utf-8")
            + "\n"
        }
        for record in validated["entries"]:
            directory = "tombstones" if record["tombstone"] else "entries"
            files[f"kb/{directory}/{record['stable_id']}.json"] = (
                canonical_json(record).decode("utf-8") + "\n"
            )
        file_receipts = [
            {
                "path": path,
                "sha256": sha256_bytes(content.encode("utf-8")),
                "bytes": len(content.encode("utf-8")),
            }
            for path, content in sorted(files.items())
        ]
        tree_sha256 = sha256_bytes(
            canonical_json(
                [{"path": item["path"], "sha256": item["sha256"]} for item in file_receipts]
            )
        )
        project_id = validated["logical_project"]["logical_project_id"]
        return {
            "schema": KB_GIT_TREE_SCHEMA,
            "bundle_sha256": validated["bundle_sha256"],
            "logical_project_id": project_id,
            "branch_namespace": (
                "refs/heads/pbx-kb-sync/"
                f"{project_id.removeprefix('project:')[:24]}"
            ),
            "tree_sha256": tree_sha256,
            "files": files,
            "file_receipts": file_receipts,
            "ambient_credentials_allowed": False,
            "daemon_git_execution": False,
        }

    @classmethod
    def _validate_record(cls, value: object) -> dict[str, Any]:
        expected = {
            "stable_id",
            "origin_kb_id",
            "revision",
            "parent_content_hash",
            "content_hash",
            "tombstone",
            "entry",
            "reviewer_evidence",
        }
        if not isinstance(value, Mapping) or set(value) != expected:
            raise OperatorKbSyncError("KB bundle record fields are invalid")
        stable_id = str(value.get("stable_id") or "")
        origin_kb_id = str(value.get("origin_kb_id") or "").strip()
        revision = value.get("revision")
        parent = value.get("parent_content_hash")
        content_hash = str(value.get("content_hash") or "")
        tombstone = value.get("tombstone")
        entry = value.get("entry")
        evidence = value.get("reviewer_evidence")
        if (
            not STABLE_ID_RE.fullmatch(stable_id)
            or not origin_kb_id
            or len(origin_kb_id) > 120
            or isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or (parent is not None and not SHA256_RE.fullmatch(str(parent)))
            or not SHA256_RE.fullmatch(content_hash)
            or not isinstance(tombstone, bool)
            or not isinstance(evidence, Mapping)
        ):
            raise OperatorKbSyncError("KB bundle record metadata is invalid")
        if tombstone:
            if entry is not None:
                raise OperatorKbSyncError("KB tombstone must not carry entry content")
            terminal_status = evidence.get("status")
            content = {
                "stable_id": stable_id,
                "tombstone": True,
                "terminal_status": terminal_status,
            }
        else:
            if not isinstance(entry, Mapping):
                raise OperatorKbSyncError("KB live record requires entry content")
            normalized_entry = cls._validate_entry_payload(entry)
            entry = normalized_entry
            content = {"stable_id": stable_id, "tombstone": False, "entry": entry}
        if sha256_bytes(canonical_json(content)) != content_hash:
            raise OperatorKbSyncError("KB record content digest mismatch")
        expected_evidence = {
            "status",
            "reviewer_operator_agent_id",
            "promoted_at",
            "redaction_status",
            "content_hash",
        }
        if set(evidence) != expected_evidence or evidence.get("content_hash") != content_hash:
            raise OperatorKbSyncError("KB reviewer evidence is invalid")
        evidence_status = evidence.get("status")
        evidence_reviewer = evidence.get("reviewer_operator_agent_id")
        evidence_promoted_at = evidence.get("promoted_at")
        evidence_redaction = evidence.get("redaction_status")
        if (
            not isinstance(evidence_status, str)
            or evidence_status not in KB_ENTRY_STATUSES
            or (
                evidence_reviewer is not None
                and (
                    not isinstance(evidence_reviewer, str)
                    or not evidence_reviewer
                    or len(evidence_reviewer) > 120
                )
            )
            or isinstance(evidence_promoted_at, bool)
            or (
                evidence_promoted_at is not None
                and not isinstance(evidence_promoted_at, (int, float))
            )
            or not isinstance(evidence_redaction, str)
            or not evidence_redaction
            or (tombstone and evidence_status not in {"retired", "rejected"})
        ):
            raise OperatorKbSyncError("KB reviewer evidence values are invalid")
        return {
            "stable_id": stable_id,
            "origin_kb_id": origin_kb_id,
            "revision": revision,
            "parent_content_hash": parent,
            "content_hash": content_hash,
            "tombstone": tombstone,
            "entry": entry,
            "reviewer_evidence": dict(evidence),
        }

    @staticmethod
    def _validate_entry_payload(value: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "scope", "project", "repo_root", "git_remote", "branch", "title",
            "summary", "body", "tags", "redaction_status",
            "source_knowledge_link_id", "source_handoff_id", "source_turn_ids",
            "stale_after", "expires_at",
        }
        if set(value) != expected:
            raise OperatorKbSyncError("KB entry payload fields are invalid")
        for key in ("scope", "title", "summary", "body", "redaction_status"):
            if not isinstance(value.get(key), str) or not str(value[key]).strip():
                raise OperatorKbSyncError(f"KB entry {key} is invalid")
        if value["scope"] not in KB_ENTRY_SCOPES:
            raise OperatorKbSyncError("KB entry scope is invalid")
        if len(str(value["body"]).encode("utf-8")) > MAX_ENTRY_BODY_BYTES:
            raise OperatorKbSyncError("KB entry body exceeds the bundle limit")
        if not isinstance(value.get("tags"), list) or not isinstance(
            value.get("source_turn_ids"), list
        ):
            raise OperatorKbSyncError("KB entry list fields are invalid")
        if any(not isinstance(item, str) for item in [*value["tags"], *value["source_turn_ids"]]):
            raise OperatorKbSyncError("KB entry list values are invalid")
        for key in ("tags", "source_turn_ids"):
            if value[key] != sorted(set(value[key])) or any(not item for item in value[key]):
                raise OperatorKbSyncError(f"KB entry {key} must be unique and sorted")
        for key in (
            "project",
            "repo_root",
            "git_remote",
            "branch",
            "source_knowledge_link_id",
            "source_handoff_id",
        ):
            item = value.get(key)
            if item is not None and (not isinstance(item, str) or not item):
                raise OperatorKbSyncError(f"KB entry {key} is invalid")
        for key in ("stale_after", "expires_at"):
            item = value.get(key)
            if isinstance(item, bool) or (
                item is not None and not isinstance(item, (int, float))
            ):
                raise OperatorKbSyncError(f"KB entry {key} is invalid")
        return dict(value)

    @staticmethod
    def _deterministic_local_kb_id(project_id: str, stable_id: str) -> str:
        return str(uuid.uuid5(KB_SYNC_NAMESPACE, f"{project_id}\0{stable_id}"))

    @staticmethod
    def _local_entry_content_hash(entry: Mapping[str, Any], stable_id: str) -> str:
        tombstone = str(entry.get("status") or "") in {"retired", "rejected"}
        if tombstone:
            content = {
                "stable_id": stable_id,
                "tombstone": True,
                "terminal_status": str(entry.get("status")),
            }
        else:
            content = {
                "stable_id": stable_id,
                "tombstone": False,
                "entry": _entry_payload(entry),
            }
        return sha256_bytes(canonical_json(content))

    def _local_reviewer_is_trusted(
        self,
        entry: Mapping[str, Any],
        evidence: Mapping[str, Any],
        trusted_reviewer_ids: set[str],
    ) -> bool:
        metadata = entry.get("metadata")
        reviewer = (
            metadata.get("promoted_by_operator_agent_id")
            if isinstance(metadata, Mapping)
            else None
        )
        return bool(
            reviewer
            and evidence.get("reviewer_operator_agent_id") == reviewer
            and evidence.get("content_hash")
            and entry.get("status") == "active"
            and evidence.get("status") == "active"
            and entry.get("redaction_status") == "clean"
            and evidence.get("redaction_status") == "clean"
            and evidence.get("promoted_at") == entry.get("promoted_at")
            and str(reviewer) in trusted_reviewer_ids
        )
