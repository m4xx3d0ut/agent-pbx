from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .contracts import (
    DEFAULT_EVIDENCE_REDACTION_POLICY,
    EVIDENCE_EXPORT_MANIFEST_CONTRACT,
)


EVIDENCE_BUNDLE_SCHEMA = "agent-pbx.evidence-bundle/v1"
EVIDENCE_EXPORT_KINDS = frozenset({"thread", "campaign", "evidence_case"})
EVIDENCE_EXPORT_FORMATS = frozenset({"json", "markdown", "ndjson"})
EVIDENCE_CASE_CATEGORIES = frozenset(
    {
        "event_journal",
        "remote_audit",
        "model_elevation",
        "git_broker",
        "lifecycle",
        "runtime",
        "handoff",
        "workerbee_workspace",
        "workerbee_run",
        "workerbee_resource",
    }
)
MAX_EXPORT_RECORDS = 500
MAX_EXPORT_TEXT_BYTES = 4 * 1024 * 1024
MAX_REDACTED_TEXT = 32_000
_EXPORT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SECRET_LINE_RE = re.compile(
    r"(?i)(?:authorization|bearer|credential|password|private[_ -]?key|secret|token)"
)
_EVENT_PREFIXES: dict[str, tuple[str, ...]] = {
    "git_broker": ("git_broker", "operator_git_broker"),
    "lifecycle": ("lifecycle", "operator_lifecycle"),
    "runtime": ("runtime", "tmux_runtime", "managed_runtime"),
    "handoff": ("operator_handoff",),
    "workerbee_workspace": ("workerbee_workspace",),
    "workerbee_run": ("workerbee_controlled_run",),
    "workerbee_resource": ("workerbee_resource", "dynamic_resource"),
}


class EvidenceExportError(RuntimeError):
    pass


class EvidenceStore(Protocol):
    def list_thread(self, agent_id: str, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def get_operator_campaign(self, campaign_id: str) -> dict[str, Any] | None: ...

    def list_operator_campaign_assignments(
        self, *, campaign_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]: ...

    def list_operator_campaign_events(
        self, *, campaign_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...

    def get_report(self, report_id: str) -> dict[str, Any] | None: ...

    def list_operator_handoffs(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def list_operator_handoff_completion_deliveries(
        self, **kwargs: Any
    ) -> list[dict[str, Any]]: ...

    def list_operator_knowledge_links(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def list_operator_delegation_leases(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def list_events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]: ...

    def list_remote_audit(self, *, limit: int = 100) -> list[dict[str, Any]]: ...

    def list_model_elevations(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def list_workerbee_controlled_run_requests(self, **kwargs: Any) -> list[dict[str, Any]]: ...

    def list_workerbee_controlled_run_leases(self, **kwargs: Any) -> list[dict[str, Any]]: ...


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _bounded_text(value: object) -> str:
    text = str(value or "").replace("\x00", "")[:MAX_REDACTED_TEXT]
    lines: list[str] = []
    for line in text.splitlines():
        lines.append("[redacted]" if _SECRET_LINE_RE.search(line) else line)
    return "\n".join(lines)


def _select(record: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for field in fields:
        value = record.get(field)
        if value is None or isinstance(value, (str, int, float, bool)):
            selected[field] = value
    return selected


def _record(record_type: str, value: Mapping[str, Any]) -> dict[str, Any]:
    return {"record_type": record_type, **value}


def _file_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        stat.S_IFMT(value.st_mode),
        stat.S_IMODE(value.st_mode),
        value.st_uid,
        value.st_nlink,
    )


class EvidenceExportService:
    """Daemon-owned, bounded, redacted evidence exporter."""

    def __init__(self, store: EvidenceStore, *, root: Path, enabled: bool = False) -> None:
        self.store = store
        self.root = Path(root).expanduser().absolute()
        self.enabled = bool(enabled)

    def preview(
        self,
        *,
        kind: str,
        subject_id: str,
        scope: str = "all",
        item_ids: Sequence[str] = (),
        categories: Sequence[str] = (),
        formats: Sequence[str] = ("json", "markdown", "ndjson"),
    ) -> dict[str, Any]:
        self._require_enabled()
        bundle = self._bundle(
            kind=kind,
            subject_id=subject_id,
            scope=scope,
            item_ids=item_ids,
            categories=categories,
        )
        normalized_formats = self._formats(formats)
        rendered = self._render_all(bundle, normalized_formats)
        return {
            "kind": kind,
            "subject_id": subject_id,
            "record_count": len(bundle["records"]),
            "content_sha256": _sha256(_canonical_json(bundle)),
            "formats": list(normalized_formats),
            "bytes_by_format": {name: len(data) for name, data in rendered.items()},
            "redaction_policy": asdict(DEFAULT_EVIDENCE_REDACTION_POLICY),
            "writes_files": False,
        }

    def export(
        self,
        *,
        kind: str,
        subject_id: str,
        scope: str = "all",
        item_ids: Sequence[str] = (),
        categories: Sequence[str] = (),
        formats: Sequence[str] = ("json", "markdown", "ndjson"),
    ) -> dict[str, Any]:
        self._require_enabled()
        bundle = self._bundle(
            kind=kind,
            subject_id=subject_id,
            scope=scope,
            item_ids=item_ids,
            categories=categories,
        )
        normalized_formats = self._formats(formats)
        rendered = self._render_all(bundle, normalized_formats)
        export_id = uuid.uuid4().hex
        created_at = time.time()
        self._prepare_root()
        temporary = self.root / f".{export_id}.tmp"
        final = self.root / export_id
        if temporary.exists() or final.exists():
            raise EvidenceExportError("evidence export identifier collision")
        temporary.mkdir(mode=0o700)
        try:
            file_entries: list[dict[str, Any]] = []
            for name, data in rendered.items():
                filename = {
                    "json": "evidence.json",
                    "markdown": "evidence.md",
                    "ndjson": "evidence.ndjson",
                }[name]
                self._write_private(temporary / filename, data)
                file_entries.append(
                    {"format": name, "name": filename, "bytes": len(data), "sha256": _sha256(data)}
                )
            manifest = {
                "schema": EVIDENCE_EXPORT_MANIFEST_CONTRACT.schema,
                "export_id": export_id,
                "kind": str(bundle["kind"]),
                "subject_id": str(bundle["subject_id"]),
                "created_at": created_at,
                "content_sha256": _sha256(_canonical_json(bundle)),
                "record_count": len(bundle["records"]),
                "formats": list(normalized_formats),
                "files": file_entries,
                "redaction_policy": asdict(DEFAULT_EVIDENCE_REDACTION_POLICY),
            }
            self._write_private(
                temporary / "manifest.json",
                json.dumps(
                    manifest,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
                + b"\n",
            )
            self._fsync_dir(temporary)
            os.replace(temporary, final)
            self._fsync_dir(self.root)
        except Exception:
            self._remove_partial(temporary)
            raise
        return {**manifest, "path": str(final)}

    def verify(self, export_id: str) -> dict[str, Any]:
        self._require_enabled()
        export_dir = self._export_dir(export_id)
        manifest_path = export_dir / "manifest.json"
        manifest = self._read_manifest(manifest_path)
        if manifest.get("export_id") != export_id:
            raise EvidenceExportError("evidence manifest export identity mismatch")
        verified: list[dict[str, Any]] = []
        canonical_content_sha256: str | None = None
        for item in manifest["files"]:
            path = export_dir / item["name"]
            data = self._read_private_file(path)
            actual = _sha256(data)
            if actual != item["sha256"] or len(data) != item["bytes"]:
                raise EvidenceExportError(f"evidence file drift: {item['name']}")
            if item["format"] == "json":
                try:
                    bundle = json.loads(data)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise EvidenceExportError("JSON evidence is malformed") from exc
                canonical_content_sha256 = _sha256(_canonical_json(bundle))
            verified.append({"name": item["name"], "sha256": actual, "bytes": len(data)})
        if (
            canonical_content_sha256 is not None
            and canonical_content_sha256 != manifest["content_sha256"]
        ):
            raise EvidenceExportError("evidence canonical content drift")
        return {
            "export_id": export_id,
            "verified": True,
            "content_sha256": manifest["content_sha256"],
            "files": verified,
        }

    def render_joplin(self, export_id: str) -> dict[str, str]:
        self._require_enabled()
        self.verify(export_id)
        export_dir = self._export_dir(export_id)
        manifest = self._read_manifest(export_dir / "manifest.json")
        markdown = next(
            (entry for entry in manifest["files"] if entry["format"] == "markdown"),
            None,
        )
        if markdown is None:
            raise EvidenceExportError("export has no Markdown rendering")
        body = self._read_private_file(export_dir / markdown["name"]).decode("utf-8")
        title = f"Agent PBX Evidence - {manifest['kind']} - {manifest['subject_id']}"
        return {"title": title[:255], "body": body, "export_id": export_id}

    def retention_preview(self, *, older_than: float) -> dict[str, Any]:
        self._require_enabled()
        self._prepare_root()
        exports: list[dict[str, Any]] = []
        for entry in sorted(os.scandir(self.root), key=lambda item: item.name):
            if not _EXPORT_ID_RE.fullmatch(entry.name):
                continue
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                raise EvidenceExportError("malformed evidence export root entry")
            manifest = self._read_manifest(Path(entry.path) / "manifest.json")
            if float(manifest["created_at"]) < float(older_than):
                exports.append(
                    {
                        "export_id": entry.name,
                        "created_at": manifest["created_at"],
                        "kind": manifest["kind"],
                        "subject_id": manifest["subject_id"],
                    }
                )
        return {"older_than": float(older_than), "exports": exports, "writes_files": False}

    def _bundle(
        self,
        *,
        kind: str,
        subject_id: str,
        scope: str,
        item_ids: Sequence[str],
        categories: Sequence[str],
    ) -> dict[str, Any]:
        normalized_kind = str(kind).strip().lower()
        normalized_subject = str(subject_id).strip()
        if normalized_kind not in EVIDENCE_EXPORT_KINDS or not normalized_subject:
            raise EvidenceExportError("unsupported evidence export source")
        if normalized_kind == "thread":
            records = self._thread_records(normalized_subject, scope=scope, item_ids=item_ids)
        elif normalized_kind == "campaign":
            records = self._campaign_records(normalized_subject)
        else:
            records = self._case_records(normalized_subject, categories=categories)
        if len(records) > MAX_EXPORT_RECORDS:
            raise EvidenceExportError("evidence export record limit exceeded")
        bundle = {
            "schema": EVIDENCE_BUNDLE_SCHEMA,
            "kind": normalized_kind,
            "subject_id": normalized_subject,
            "generated_at": time.time(),
            "scope": str(scope or "all"),
            "records": records,
            "redaction_policy": asdict(DEFAULT_EVIDENCE_REDACTION_POLICY),
        }
        if len(_canonical_json(bundle)) > MAX_EXPORT_TEXT_BYTES:
            raise EvidenceExportError("evidence export byte limit exceeded")
        return bundle

    def _thread_records(
        self, agent_id: str, *, scope: str, item_ids: Sequence[str]
    ) -> list[dict[str, Any]]:
        items = self.store.list_thread(agent_id, limit=200)
        allowed_ids = {str(item) for item in item_ids if str(item)}
        if scope not in {"all", "item", "marked"}:
            raise EvidenceExportError("unsupported thread export scope")
        if scope != "all":
            items = [item for item in items if str(item.get("item_id")) in allowed_ids]
        records: list[dict[str, Any]] = []
        for item in items:
            kind = str(item.get("kind") or "")
            selected = _select(item, ("item_id", "kind", "title", "status", "created_at"))
            if kind == "report":
                selected["detail"] = _bounded_text(item.get("body"))
                metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
                selected["report_id"] = metadata.get("report_id")
                selected["needs_input"] = bool(metadata.get("needs_input", False))
            elif kind == "command":
                selected["prompt_body"] = "[excluded by evidence redaction policy]"
                metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
                selected["command_id"] = metadata.get("command_id")
                selected["command_type"] = metadata.get("type")
            else:
                raise EvidenceExportError("unknown thread evidence record class")
            records.append(_record("thread_item", selected))
        return records

    def _campaign_records(self, campaign_id: str) -> list[dict[str, Any]]:
        campaign = self.store.get_operator_campaign(campaign_id)
        if campaign is None:
            raise EvidenceExportError("operator campaign not found")
        records = [
            _record(
                "campaign",
                {
                    **_select(
                        campaign,
                        (
                            "campaign_id",
                            "operator_agent_id",
                            "title",
                            "status",
                            "summary",
                            "created_at",
                            "updated_at",
                            "completed_at",
                        ),
                    ),
                    "objective": _bounded_text(campaign.get("objective")),
                    "criteria": [
                        _bounded_text(value)
                        for value in campaign.get("criteria", [])
                        if isinstance(value, str)
                    ][:50],
                },
            )
        ]
        assignments = self.store.list_operator_campaign_assignments(
            campaign_id=campaign_id,
            limit=500,
        )
        report_ids: set[str] = set()
        for assignment in assignments:
            records.append(
                _record(
                    "assignment",
                    _select(
                        assignment,
                        (
                            "assignment_id",
                            "campaign_id",
                            "target_agent_id",
                            "operator_fork_id",
                            "fork_track_id",
                            "title",
                            "state",
                            "last_report_id",
                            "last_command_id",
                            "created_at",
                            "updated_at",
                            "completed_at",
                        ),
                    ),
                )
            )
            if assignment.get("last_report_id"):
                report_ids.add(str(assignment["last_report_id"]))
        for event in self.store.list_operator_campaign_events(campaign_id=campaign_id, limit=500):
            records.append(
                _record(
                    "campaign_event",
                    _select(
                        event,
                        (
                            "event_id",
                            "campaign_id",
                            "assignment_id",
                            "operator_agent_id",
                            "target_agent_id",
                            "event_type",
                            "summary",
                            "report_id",
                            "command_id",
                            "created_at",
                        ),
                    ),
                )
            )
            if event.get("report_id"):
                report_ids.add(str(event["report_id"]))
        for report_id in sorted(report_ids):
            report = self.store.get_report(report_id)
            if report:
                records.append(
                    _record(
                        "report",
                        {
                            **_select(
                                report,
                                (
                                    "report_id",
                                    "agent_id",
                                    "reporting_agent_id",
                                    "project",
                                    "status",
                                    "summary",
                                    "needs_input",
                                    "created_at",
                                ),
                            ),
                            "detail": _bounded_text(report.get("detail")),
                        },
                    )
                )
        operator_id = str(campaign.get("operator_agent_id") or "")
        for handoff in self.store.list_operator_handoffs(
            logical_operator_agent_id=operator_id,
            limit=500,
        ):
            handoff_scope = handoff.get("execution_scope")
            handoff_metadata = handoff.get("metadata")
            handoff_campaign_id = (
                handoff.get("campaign_id")
                or (
                    handoff_scope.get("campaign_id")
                    if isinstance(handoff_scope, Mapping)
                    else None
                )
                or (
                    handoff_metadata.get("campaign_id")
                    if isinstance(handoff_metadata, Mapping)
                    else None
                )
            )
            if str(handoff_campaign_id or "") != campaign_id:
                continue
            records.append(
                _record(
                    "handoff",
                    _select(
                        handoff,
                        (
                            "handoff_id",
                            "campaign_id",
                            "assignment_id",
                            "action_class",
                            "source_agent_id",
                            "target_operator_agent_id",
                            "target_caller_agent_id",
                            "status",
                            "created_at",
                            "updated_at",
                            "completed_at",
                        ),
                    ),
                )
            )
            metadata = handoff_metadata
            approval = (
                metadata.get("manual_approval")
                if isinstance(metadata, Mapping)
                else None
            )
            if isinstance(approval, Mapping):
                records.append(
                    _record(
                        "manual_approval",
                        _select(
                            approval,
                            (
                                "approval_id",
                                "handoff_id",
                                "campaign_id",
                                "action_class",
                                "workspace_set_digest",
                                "operator_agent_id",
                                "method",
                                "approved",
                                "approved_at",
                                "expires_at",
                            ),
                        ),
                    )
                )
            delivery = handoff.get("completion_delivery")
            if isinstance(delivery, Mapping):
                records.append(
                    _record(
                        "completion_receipt",
                        _select(
                            delivery,
                            (
                                "completion_delivery_id",
                                "handoff_id",
                                "source_operator_agent_id",
                                "recipient_agent_id",
                                "delivery_status",
                                "created_at",
                                "acknowledged_at",
                            ),
                        ),
                    )
                )
        for lease in self.store.list_operator_delegation_leases(
            source_operator_agent_id=operator_id,
            limit=500,
        ):
            campaign_ids = lease.get("campaign_ids")
            lease_matches = str(lease.get("campaign_id") or "") == campaign_id or (
                isinstance(campaign_ids, list) and campaign_id in campaign_ids
            )
            if lease_matches:
                selected_lease = _select(lease, (
                    "lease_id", "campaign_id", "source_operator_agent_id",
                    "target_operator_agent_id",
                    "status", "created_at", "approved_at", "expires_at", "revoked_at"
                ))
                selected_lease["campaign_ids"] = (
                    [str(value) for value in campaign_ids[:50]]
                    if isinstance(campaign_ids, list)
                    else [campaign_id]
                )
                records.append(_record("delegation_lease", selected_lease))
        for link in self.store.list_operator_knowledge_links(
            logical_operator_agent_id=operator_id,
            limit=500,
        ):
            if str(link.get("campaign_id") or "") == campaign_id:
                records.append(_record("knowledge_reference", _select(link, (
                    "link_id", "campaign_id", "source_agent_id", "target_agent_id", "status",
                    "created_at", "updated_at"
                ))))
        return records

    def _case_records(self, case_id: str, *, categories: Sequence[str]) -> list[dict[str, Any]]:
        requested = tuple(
            dict.fromkeys(
                str(item).strip() for item in categories if str(item).strip()
            )
        )
        if not requested or any(item not in EVIDENCE_CASE_CATEGORIES for item in requested):
            raise EvidenceExportError("evidence case requires reviewed categories")
        records: list[dict[str, Any]] = []
        events = self.store.list_events(after_id=0, limit=500)
        for category in requested:
            if category == "remote_audit":
                for item in self.store.list_remote_audit(limit=500):
                    target_agent = item.get("target_agent_id") or item.get("agent_id")
                    if case_id != "global" and str(target_agent or "") != case_id:
                        continue
                    records.append(_record(category, _select(item, (
                        "audit_id", "client_id", "role", "action",
                        "target_agent_id", "outcome", "created_at"
                    ))))
                continue
            if category == "model_elevation":
                for item in self.store.list_model_elevations(limit=500):
                    if case_id != "global" and str(item.get("agent_id") or "") != case_id:
                        continue
                    records.append(_record(category, _select(item, (
                        "request_id", "agent_id", "model", "status",
                        "requested_at", "decided_at", "expires_at"
                    ))))
                continue
            if category == "workerbee_run":
                campaign_ids: set[str] = set()
                for item in self.store.list_workerbee_controlled_run_requests(
                    limit=250,
                    expire=False,
                ):
                    if (
                        case_id != "global"
                        and str(item.get("target_fork_agent_id") or "") != case_id
                    ):
                        continue
                    if item.get("campaign_id"):
                        campaign_ids.add(str(item["campaign_id"]))
                    records.append(_record("workerbee_run_request", _select(item, (
                        "request_id", "campaign_id", "target_fork_agent_id",
                        "status", "created_at", "updated_at", "completed_at"
                    ))))
                for item in self.store.list_workerbee_controlled_run_leases(
                    limit=250,
                    expire=False,
                ):
                    if (
                        case_id != "global"
                        and str(item.get("campaign_id") or "") not in campaign_ids
                    ):
                        continue
                    records.append(_record("workerbee_run_lease", _select(item, (
                        "lease_id", "campaign_id", "status", "created_at",
                        "expires_at", "revoked_at"
                    ))))
                continue
            prefixes = _EVENT_PREFIXES.get(category, ())
            for event in events:
                event_type = str(event.get("type") or event.get("event_type") or "")
                if case_id != "global":
                    payload = event.get("payload")
                    payload_agent = (
                        payload.get("agent_id")
                        if isinstance(payload, Mapping)
                        else None
                    )
                    if str(event.get("agent_id") or payload_agent or "") != case_id:
                        continue
                if category == "event_journal" or any(
                    event_type.startswith(prefix) for prefix in prefixes
                ):
                    records.append(_record(category, _select(event, (
                        "id", "event_id", "type", "event_type", "agent_id", "created_at"
                    ))))
        return records

    def _formats(self, formats: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(str(item).strip().lower() for item in formats))
        if not normalized or any(item not in EVIDENCE_EXPORT_FORMATS for item in normalized):
            raise EvidenceExportError("unsupported evidence export format")
        return normalized

    def _render_all(self, bundle: Mapping[str, Any], formats: Sequence[str]) -> dict[str, bytes]:
        rendered: dict[str, bytes] = {}
        for format_name in formats:
            if format_name == "json":
                data = json.dumps(
                    bundle,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8") + b"\n"
            elif format_name == "ndjson":
                data = b"".join(_canonical_json(record) + b"\n" for record in bundle["records"])
            else:
                data = self._render_markdown(bundle).encode("utf-8")
            if len(data) > MAX_EXPORT_TEXT_BYTES:
                raise EvidenceExportError(f"{format_name} evidence rendering exceeds byte limit")
            rendered[format_name] = data
        return rendered

    def _render_markdown(self, bundle: Mapping[str, Any]) -> str:
        lines = [
            f"# Agent PBX Evidence: {bundle['kind']}",
            "",
            f"- Subject: `{bundle['subject_id']}`",
            f"- Records: {len(bundle['records'])}",
            f"- Redaction: `{DEFAULT_EVIDENCE_REDACTION_POLICY.schema}`",
            "",
        ]
        for index, record in enumerate(bundle["records"], start=1):
            lines.extend(
                [
                    f"## {index}. {record['record_type']}",
                    "",
                    "```json",
                    json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines).rstrip() + "\n"

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise EvidenceExportError("evidence export center is disabled")

    def _prepare_root(self) -> None:
        candidate = self.root.absolute()
        current = Path(candidate.anchor)
        for part in candidate.parts[1:]:
            current = current / part
            if not current.exists() and not current.is_symlink():
                break
            if current.is_symlink():
                raise EvidenceExportError(
                    "evidence export root ancestry must not contain symlinks"
                )
        if self.root.exists() and self.root.is_symlink():
            raise EvidenceExportError("evidence export root must not be a symlink")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = self.root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise EvidenceExportError("evidence export root ownership is invalid")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise EvidenceExportError("evidence export root must have mode 0700")

    def _export_dir(self, export_id: str) -> Path:
        if not _EXPORT_ID_RE.fullmatch(str(export_id)):
            raise EvidenceExportError("invalid evidence export identifier")
        self._prepare_root()
        path = self.root / export_id
        info = path.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.getuid()
        ):
            raise EvidenceExportError("evidence export directory is invalid")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise EvidenceExportError("evidence export directory must have mode 0700")
        return path

    def _read_manifest(self, path: Path) -> dict[str, Any]:
        data = self._read_private_file(path)
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceExportError("evidence manifest is malformed") from exc
        expected = {
            "schema", "export_id", "kind", "subject_id", "created_at", "content_sha256",
            "record_count", "formats", "files", "redaction_policy"
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise EvidenceExportError("evidence manifest schema is invalid")
        if value.get("schema") != EVIDENCE_EXPORT_MANIFEST_CONTRACT.schema:
            raise EvidenceExportError("unsupported evidence manifest schema")
        if (
            not _EXPORT_ID_RE.fullmatch(str(value.get("export_id") or ""))
            or value.get("kind") not in EVIDENCE_EXPORT_KINDS
            or not isinstance(value.get("subject_id"), str)
            or not value["subject_id"]
            or isinstance(value.get("created_at"), bool)
            or not isinstance(value.get("created_at"), (int, float))
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("content_sha256") or ""))
            or isinstance(value.get("record_count"), bool)
            or not isinstance(value.get("record_count"), int)
            or not 0 <= value["record_count"] <= MAX_EXPORT_RECORDS
            or not isinstance(value.get("formats"), list)
            or not value["formats"]
            or any(item not in EVIDENCE_EXPORT_FORMATS for item in value["formats"])
            or not isinstance(value.get("redaction_policy"), dict)
        ):
            raise EvidenceExportError("evidence manifest metadata is invalid")
        if (
            not isinstance(value.get("files"), list)
            or len(value["files"]) > len(EVIDENCE_EXPORT_FORMATS)
        ):
            raise EvidenceExportError("evidence manifest files are invalid")
        seen: set[str] = set()
        seen_formats: set[str] = set()
        for item in value["files"]:
            if not isinstance(item, dict) or set(item) != {"format", "name", "bytes", "sha256"}:
                raise EvidenceExportError("evidence manifest file entry is invalid")
            if item["format"] not in EVIDENCE_EXPORT_FORMATS or item["name"] not in {
                "evidence.json", "evidence.md", "evidence.ndjson"
            }:
                raise EvidenceExportError("evidence manifest file path is invalid")
            if (
                item["name"] in seen
                or item["format"] in seen_formats
                or not isinstance(item["bytes"], int)
                or isinstance(item["bytes"], bool)
                or not 0 <= item["bytes"] <= MAX_EXPORT_TEXT_BYTES
            ):
                raise EvidenceExportError("evidence manifest file metadata is invalid")
            if not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"])):
                raise EvidenceExportError("evidence manifest file digest is invalid")
            seen.add(item["name"])
            seen_formats.add(item["format"])
        if set(value["formats"]) != seen_formats:
            raise EvidenceExportError("evidence manifest format set is inconsistent")
        return value

    def _read_private_file(self, path: Path) -> bytes:
        before = path.lstat()
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if _file_identity(before) != _file_identity(opened):
                raise EvidenceExportError("evidence file changed during open")
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or opened.st_nlink != 1
            ):
                raise EvidenceExportError("evidence file identity is invalid")
            if stat.S_IMODE(opened.st_mode) != 0o600:
                raise EvidenceExportError("evidence file must have mode 0600")
            if opened.st_size > MAX_EXPORT_TEXT_BYTES:
                raise EvidenceExportError("evidence file exceeds byte limit")
            data = b""
            while len(data) <= MAX_EXPORT_TEXT_BYTES:
                chunk = os.read(descriptor, min(64 * 1024, MAX_EXPORT_TEXT_BYTES + 1 - len(data)))
                if not chunk:
                    break
                data += chunk
            after = os.fstat(descriptor)
            if (
                _file_identity(opened) != _file_identity(after)
                or opened.st_size != after.st_size
                or opened.st_mtime_ns != after.st_mtime_ns
                or opened.st_ctime_ns != after.st_ctime_ns
            ):
                raise EvidenceExportError("evidence file changed during read")
            if len(data) > MAX_EXPORT_TEXT_BYTES:
                raise EvidenceExportError("evidence file exceeds byte limit")
            return data
        finally:
            os.close(descriptor)

    def _write_private(self, path: Path, data: bytes) -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _remove_partial(path: Path) -> None:
        if not path.exists() or path.is_symlink():
            return
        for entry in path.iterdir():
            if entry.is_file() and not entry.is_symlink():
                entry.unlink()
        path.rmdir()
