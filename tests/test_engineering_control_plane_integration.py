from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.dynamic_resources import RESOURCE_APPROVAL_METHOD
from agent_pbx.workerbee_development_resources import (
    MOUNT_TARGETS,
    PROVIDER_CAPABILITY_SCHEMA,
    PROVIDER_CLEANUP_SCHEMA,
    PROVIDER_ID,
    PROVIDER_RECEIPT_SCHEMA,
    PROVIDER_REQUEST_SCHEMA,
    RESOURCE_CLASSES,
    unsigned_digest,
)


WORKSPACE_DIGEST = "d" * 64
BINDING_ID = "integration-binding-1"
BACKEND_GENERATION = "integration-generation-1"


class RestartableWorkerBeeAdapter:
    """Provider-free runtime truth fixture shared across app restarts."""

    def __init__(self) -> None:
        now = time.time()
        self.capability: dict[str, Any] = {
            "schema": PROVIDER_CAPABILITY_SCHEMA,
            "provider": PROVIDER_ID,
            "provider_generation": "integration-provider-1",
            "backend": "platform_debug",
            "backend_instance": "integration-runtime-1",
            "backend_generation": BACKEND_GENERATION,
            "workspace_binding_id": BINDING_ID,
            "resource_classes": list(RESOURCE_CLASSES),
            "mount_targets": dict(MOUNT_TARGETS),
            "request_schema": PROVIDER_REQUEST_SCHEMA,
            "receipt_schema": PROVIDER_RECEIPT_SCHEMA,
            "cleanup_schema": PROVIDER_CLEANUP_SCHEMA,
            "trusted_direct_bind_read_only": True,
            "trusted_direct_bind_rprivate": True,
            "writable_source": False,
            "max_ttl_seconds": 3600,
            "max_quota_bytes": 1_000_000,
            "issued_at": now,
            "expires_at": now + 3600,
        }
        self.capability["capability_sha256"] = unsigned_digest(
            self.capability,
            "capability_sha256",
        )
        self.resources: dict[str, dict[str, Any]] = {}

    def capabilities(
        self,
        *,
        workspace_binding_id: str,
        backend_generation: str,
    ) -> Mapping[str, Any]:
        assert workspace_binding_id == BINDING_ID
        assert backend_generation == BACKEND_GENERATION
        return dict(self.capability)

    def acquire(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "schema": PROVIDER_RECEIPT_SCHEMA,
            "status": "acquired",
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider": PROVIDER_ID,
            "provider_generation": self.capability["provider_generation"],
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "resource_class": request["resource_class"],
            "provider_resource_id": f"integration:{request['request_id']}",
            "workspace_binding_id": request["workspace_binding_id"],
            "backend_generation": request["backend_generation"],
            "mount_target": request["mount_target"],
            "owner_uid": 1000,
            "owner_gid": 1000,
            "mode": 0o700,
            "quota_bytes": request["quota_bytes"],
            "source_mount_read_only": True,
            "source_mount_rprivate": True,
            "writable_source": False,
            "activated_at": time.time(),
            "expires_at": request["expires_at"],
        }
        payload["receipt_sha256"] = unsigned_digest(payload, "receipt_sha256")
        self.resources[str(request["request_id"])] = payload
        return dict(payload)

    def status(self, request: Mapping[str, Any]) -> Mapping[str, Any] | None:
        value = self.resources.get(str(request["request_id"]))
        return dict(value) if value is not None else None

    def release(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        payload: dict[str, Any] = {
            "schema": PROVIDER_CLEANUP_SCHEMA,
            "status": "released",
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider": PROVIDER_ID,
            "provider_generation": self.capability["provider_generation"],
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "provider_resource_id": request["provider_resource_id"],
            "workspace_binding_id": request["workspace_binding_id"],
            "backend_generation": request["backend_generation"],
            "cleanup_complete": True,
            "residue_count": 0,
            "released_at": time.time(),
        }
        payload["receipt_sha256"] = unsigned_digest(payload, "receipt_sha256")
        self.resources.pop(str(request["request_id"]), None)
        return payload


def integration_config(root: Path) -> ServerConfig:
    return ServerConfig(
        db_path=root / "state" / "agent-pbx.sqlite",
        evidence_exports_enabled=True,
        evidence_export_root=root / "exports",
        dynamic_resources_enabled=True,
        workerbee_development_resources_enabled=True,
    )


def register_integration_agents(client: TestClient) -> None:
    assert client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-integration",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {
                "operator_role": "root",
                "operator_authority_activated_workspace_set_digest": (
                    WORKSPACE_DIGEST
                ),
            },
        },
    ).status_code == 200
    assert client.post(
        "/v1/agents/register",
        json={
            "agent_id": "caller-integration",
            "project": "integration-fixture",
            "metadata": {"pbx_mode": "report"},
        },
    ).status_code == 200


def test_cross_client_approval_restart_recovery_export_and_cleanup(
    tmp_path: Path,
) -> None:
    config = integration_config(tmp_path)
    adapter = RestartableWorkerBeeAdapter()
    first_app = create_app(config, workerbee_development_adapter=adapter)
    request_client = TestClient(first_app)
    approval_client = TestClient(first_app)
    register_integration_agents(request_client)
    campaign = first_app.state.store.create_operator_campaign(
        operator_agent_id="operator-integration",
        title="Integrated resource campaign",
        objective="Prove restart-safe receipt and evidence flow",
        criteria=[],
        assignments=[],
    )
    created = request_client.post(
        "/v1/operator/resources/request",
        json={
            "operator_agent_id": "operator-integration",
            "resource_class": "managed_venv",
            "requester_agent_id": "caller-integration",
            "campaign_id": campaign["campaign_id"],
            "workspace_set_digest": WORKSPACE_DIGEST,
            "spec": {"max_bytes": 4096},
            "workerbee_binding_id": BINDING_ID,
            "workerbee_backend_generation": BACKEND_GENERATION,
            "provider_capability_sha256": adapter.capability[
                "capability_sha256"
            ],
            "quota": 4096,
        },
    )
    assert created.status_code == 200
    request = created.json()
    assert approval_client.post(
        f"/v1/operator/resources/{request['request_id']}/approve",
        json={
            "operator_agent_id": "operator-integration",
            "approval_method": RESOURCE_APPROVAL_METHOD,
            "confirmation": request["approval_confirmation"],
        },
    ).json()["status"] == "approved"
    assert approval_client.post(
        f"/v1/operator/resources/{request['request_id']}/acquire",
        json={"operator_agent_id": "operator-integration"},
    ).json()["status"] == "leased"
    first_app.state.store.append_event(
        "dynamic_resource_secret_probe",
        {"secret": "must-never-export"},
        "operator-integration",
    )

    restarted_app = create_app(config, workerbee_development_adapter=adapter)
    restarted_client = TestClient(restarted_app)
    recovered = restarted_client.post(
        f"/v1/operator/resources/{request['request_id']}/recover",
        json={"operator_agent_id": "operator-integration"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["status"] == "leased"
    exported = restarted_client.post(
        "/v1/evidence/exports",
        json={
            "kind": "evidence_case",
            "subject_id": "operator-integration",
            "categories": ["workerbee_resource"],
            "formats": ["json", "ndjson"],
        },
    )
    assert exported.status_code == 200
    body = Path(exported.json()["path"], "evidence.json").read_text(
        encoding="utf-8"
    )
    assert "dynamic_resource_leased" in body
    assert "dynamic_resource_secret_probe" in body
    assert "must-never-export" not in body
    assert "provider_receipt" not in body
    assert restarted_client.post(
        "/v1/evidence/verify",
        json={"export_id": exported.json()["export_id"]},
    ).json()["verified"] is True
    released = restarted_client.post(
        f"/v1/operator/resources/{request['request_id']}/release",
        json={"operator_agent_id": "operator-integration"},
    )
    assert released.status_code == 200
    assert released.json()["status"] == "released"
    assert adapter.resources == {}


def test_public_and_rosie_policy_never_auto_approve_workerbee_development(
    tmp_path: Path,
) -> None:
    adapter = RestartableWorkerBeeAdapter()
    app = create_app(
        integration_config(tmp_path),
        workerbee_development_adapter=adapter,
    )
    client = TestClient(app)
    register_integration_agents(client)
    campaign = app.state.store.create_operator_campaign(
        operator_agent_id="operator-integration",
        title="Policy matrix campaign",
        objective="Prove public and Rosie boundaries",
        criteria=[],
        assignments=[],
    )
    base = {
        "operator_agent_id": "operator-integration",
        "requester_agent_id": "caller-integration",
        "campaign_id": campaign["campaign_id"],
        "workspace_set_digest": WORKSPACE_DIGEST,
    }
    public_auto = client.post(
        "/v1/operator/resources/request",
        json={
            **base,
            "resource_class": "managed_cache",
            "spec": {"name": "public-cache", "max_bytes": 4096},
            "quota": 4096,
            "approval_mode": "auto",
            "policy": "public",
        },
    )
    assert public_auto.status_code == 400
    rosie_auto = client.post(
        "/v1/operator/resources/request",
        json={
            **base,
            "resource_class": "managed_cache",
            "spec": {"name": "rosie-cache", "max_bytes": 4096},
            "quota": 4096,
            "approval_mode": "auto",
            "policy": "trusted_rosie",
        },
    )
    assert rosie_auto.status_code == 200
    assert rosie_auto.json()["status"] == "approved"
    development_auto = client.post(
        "/v1/operator/resources/request",
        json={
            **base,
            "resource_class": "managed_venv",
            "spec": {"max_bytes": 4096},
            "quota": 4096,
            "workerbee_binding_id": BINDING_ID,
            "workerbee_backend_generation": BACKEND_GENERATION,
            "provider_capability_sha256": adapter.capability[
                "capability_sha256"
            ],
            "approval_mode": "auto",
            "policy": "trusted_rosie",
        },
    )
    assert development_auto.status_code == 400
    assert len(app.state.store.list_dynamic_resource_requests()) == 1
