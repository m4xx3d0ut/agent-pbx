from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.evidence_export import EvidenceExportError, EvidenceExportService


class FakeEvidenceStore:
    def list_thread(self, agent_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        assert agent_id == "agent-1"
        return [
            {
                "item_id": "report:r1",
                "kind": "report",
                "title": "Done",
                "status": "done",
                "created_at": 1.0,
                "body": "safe line\ntoken=never-export-this",
                "metadata": {"report_id": "r1", "needs_input": False},
            },
            {
                "item_id": "command:c1",
                "kind": "command",
                "title": "Operator prompt",
                "status": "acked",
                "created_at": 2.0,
                "body": "prompt body must not leave the daemon",
                "metadata": {"command_id": "c1", "type": "send_input"},
            },
        ][:limit]

    def get_operator_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        if campaign_id != "campaign-1":
            return None
        return {
            "campaign_id": campaign_id,
            "operator_agent_id": "operator-1",
            "title": "Campaign",
            "objective": "Objective",
            "criteria": ["One"],
            "status": "complete",
            "summary": "Done",
            "created_at": 1.0,
            "updated_at": 2.0,
            "completed_at": 2.0,
        }

    def list_operator_campaign_assignments(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{
            "assignment_id": "assignment-1", "campaign_id": "campaign-1",
            "target_agent_id": "agent-1", "title": "Work", "prompt": "secret prompt",
            "state": "complete", "last_report_id": "r1", "created_at": 1.0,
            "updated_at": 2.0, "completed_at": 2.0,
        }]

    def list_operator_campaign_events(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{
            "event_id": 1, "campaign_id": "campaign-1", "assignment_id": "assignment-1",
            "event_type": "assignment_complete", "summary": "Finished",
            "detail": {"prompt_body": "do not export"}, "report_id": "r1", "created_at": 2.0,
        }]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        return {
            "report_id": report_id, "agent_id": "agent-1", "reporting_agent_id": "agent-1",
            "project": "demo", "status": "done", "summary": "Done",
            "detail": "safe\npassword=do-not-export", "needs_input": False, "created_at": 2.0,
        }

    def list_operator_handoffs(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{
            "handoff_id": "handoff-1", "campaign_id": "campaign-1",
            "assignment_id": "assignment-1", "action_class": "review",
            "source_agent_id": "operator-1", "target_operator_agent_id": "operator-2",
            "status": "complete", "message": "not exported", "created_at": 1.0,
            "updated_at": 2.0, "completed_at": 2.0,
            "metadata": {"manual_approval": {
                "approval_id": "approval-1", "handoff_id": "handoff-1",
                "campaign_id": "campaign-1", "action_class": "review",
                "workspace_set_digest": "a" * 64, "operator_agent_id": "operator-1",
                "method": "f8", "approved": True, "approved_at": 1.5, "expires_at": 3.0,
            }},
            "completion_delivery": {
                "completion_delivery_id": "delivery-1", "handoff_id": "handoff-1",
                "source_operator_agent_id": "operator-1", "recipient_agent_id": "operator-2",
                "delivery_status": "delivered", "created_at": 2.0, "acknowledged_at": 3.0,
            },
        }]

    def list_operator_handoff_completion_deliveries(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_operator_knowledge_links(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{
            "link_id": "link-1", "campaign_id": "campaign-1", "source_agent_id": "agent-1",
            "target_agent_id": "operator-1", "status": "active", "body": "not exported",
            "created_at": 1.0, "updated_at": 2.0,
        }]

    def list_operator_delegation_leases(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [{
            "lease_id": "lease-1", "campaign_id": "campaign-1",
            "source_operator_agent_id": "operator-1", "target_operator_agent_id": "operator-2",
            "status": "expired", "created_at": 1.0, "approved_at": 1.5,
            "expires_at": 2.0, "revoked_at": None,
        }]

    def list_events(self, *, after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {"id": 1, "type": "git_broker_done", "agent_id": "agent-1", "created_at": 1.0,
             "payload": {"secret": "never"}},
            {"id": 2, "type": "git_broker_done", "agent_id": "agent-2", "created_at": 1.0},
        ]

    def list_remote_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [{
            "audit_id": "audit-1", "agent_id": "agent-1", "client_id": "client",
            "principal": "operator", "action": "view", "allowed": True,
            "reason": "approved", "created_at": 1.0, "payload": "not exported",
        }]

    def list_model_elevations(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return []

    def list_workerbee_controlled_run_requests(self, **_kwargs: Any) -> list[dict[str, Any]]:
        assert _kwargs.get("expire") is False
        return []

    def list_workerbee_controlled_run_leases(self, **_kwargs: Any) -> list[dict[str, Any]]:
        assert _kwargs.get("expire") is False
        return []


def test_evidence_export_is_disabled_by_default(tmp_path: Path) -> None:
    service = EvidenceExportService(FakeEvidenceStore(), root=tmp_path / "exports")

    with pytest.raises(EvidenceExportError, match="disabled"):
        service.preview(kind="thread", subject_id="agent-1")


def test_thread_export_is_private_hashed_and_redacted(tmp_path: Path) -> None:
    root = tmp_path / "exports"
    service = EvidenceExportService(FakeEvidenceStore(), root=root, enabled=True)

    preview = service.preview(kind="thread", subject_id="agent-1")
    result = service.export(kind="thread", subject_id="agent-1")
    verified = service.verify(result["export_id"])

    assert preview["writes_files"] is False
    assert preview["record_count"] == 2
    assert verified["verified"] is True
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    export_dir = root / result["export_id"]
    assert stat.S_IMODE(export_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in export_dir.iterdir())
    rendered = (export_dir / "evidence.json").read_text(encoding="utf-8")
    assert "prompt body must not leave" not in rendered
    assert "never-export-this" not in rendered
    assert "[redacted]" in rendered
    assert "[excluded by evidence redaction policy]" in rendered


def test_campaign_export_uses_positive_field_selection(tmp_path: Path) -> None:
    service = EvidenceExportService(FakeEvidenceStore(), root=tmp_path / "exports", enabled=True)

    result = service.export(kind="campaign", subject_id="campaign-1", formats=["json"])
    body = Path(result["path"], "evidence.json").read_text(encoding="utf-8")

    assert "assignment-1" in body
    assert "delivery-1" in body
    assert "approval-1" in body
    assert "link-1" in body
    assert "lease-1" in body
    assert "secret prompt" not in body
    assert "do-not-export" not in body
    assert "not exported" not in body


def test_filtered_evidence_case_excludes_other_agents_and_payloads(tmp_path: Path) -> None:
    service = EvidenceExportService(FakeEvidenceStore(), root=tmp_path / "exports", enabled=True)

    result = service.export(
        kind="evidence_case",
        subject_id="agent-1",
        categories=["git_broker", "remote_audit", "workerbee_run"],
        formats=["json"],
    )
    records = json.loads(Path(result["path"], "evidence.json").read_text())["records"]

    assert {record["record_type"] for record in records} == {"git_broker", "remote_audit"}
    assert all("payload" not in record for record in records)
    assert all(record.get("agent_id") != "agent-2" for record in records)


def test_verify_rejects_drift_and_retention_is_preview_only(tmp_path: Path) -> None:
    service = EvidenceExportService(FakeEvidenceStore(), root=tmp_path / "exports", enabled=True)
    result = service.export(kind="thread", subject_id="agent-1", formats=["json"])
    export_dir = Path(result["path"])
    evidence = export_dir / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    os.chmod(evidence, 0o600)

    with pytest.raises(EvidenceExportError, match="drift"):
        service.verify(result["export_id"])

    preview = service.retention_preview(older_than=result["created_at"] + 1)
    assert preview["writes_files"] is False
    assert preview["exports"][0]["export_id"] == result["export_id"]
    assert export_dir.exists()


def test_evidence_api_is_default_disabled_and_enabled_with_private_root(tmp_path: Path) -> None:
    disabled = create_app(ServerConfig(db_path=tmp_path / "disabled.sqlite"))
    with TestClient(disabled) as client:
        response = client.post(
            "/v1/evidence/preview",
            json={"kind": "thread", "subject_id": "agent-1"},
        )
        assert response.status_code == 409

    enabled = create_app(
        ServerConfig(
            db_path=tmp_path / "enabled.sqlite",
            evidence_exports_enabled=True,
            evidence_export_root=tmp_path / "private-exports",
        )
    )
    enabled.state.evidence_exports.store = FakeEvidenceStore()
    with TestClient(enabled) as client:
        created = client.post(
            "/v1/evidence/exports",
            json={"kind": "thread", "subject_id": "agent-1"},
        )
        assert created.status_code == 200
        export_id = created.json()["export_id"]
        verified = client.post("/v1/evidence/verify", json={"export_id": export_id})
        rendered = client.post(f"/v1/evidence/{export_id}/joplin-render")
        assert verified.json()["verified"] is True
        assert rendered.json()["body"].startswith("# Agent PBX Evidence")
