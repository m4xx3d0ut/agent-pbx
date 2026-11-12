from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.dynamic_resources import (
    RESOURCE_APPROVAL_METHOD,
    DynamicResourceBroker,
    DynamicResourceError,
    resource_approval_confirmation,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store
from agent_pbx.workerbee_development_resources import (
    MOUNT_TARGETS,
    PROVIDER_CAPABILITY_SCHEMA,
    PROVIDER_CLEANUP_SCHEMA,
    PROVIDER_ID,
    PROVIDER_RECEIPT_SCHEMA,
    PROVIDER_REQUEST_SCHEMA,
    RESOURCE_CLASSES,
    WorkerBeeDevelopmentProvider,
    sha256,
    unsigned_digest,
)


WORKSPACE_DIGEST = "b" * 64
BINDING_ID = "binding-dev-1"
BACKEND_GENERATION = "generation-dev-1"


class FakeWorkerBeeDevelopmentAdapter:
    def __init__(self) -> None:
        now = time.time()
        self.capability: dict[str, Any] = {
            "schema": PROVIDER_CAPABILITY_SCHEMA,
            "provider": PROVIDER_ID,
            "provider_generation": "provider-generation-1",
            "backend": "platform_debug",
            "backend_instance": "platform-debug-fixture",
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
        self.acquire_calls = 0
        self.release_calls = 0
        self.status_calls = 0
        self.cleanup_residue_count = 0

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
        self.acquire_calls += 1
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
            "provider_resource_id": f"resource:{request['request_id']}",
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

    def release(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        self.release_calls += 1
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
            "residue_count": self.cleanup_residue_count,
            "released_at": time.time(),
        }
        payload["receipt_sha256"] = unsigned_digest(payload, "receipt_sha256")
        if self.cleanup_residue_count == 0:
            self.resources.pop(str(request["request_id"]), None)
        return payload

    def status(self, request: Mapping[str, Any]) -> Mapping[str, Any] | None:
        self.status_calls += 1
        value = self.resources.get(str(request["request_id"]))
        return dict(value) if value is not None else None


def build_broker(
    tmp_path: Path,
    *,
    include_provider: bool = True,
) -> tuple[DynamicResourceBroker, Store, FakeWorkerBeeDevelopmentAdapter, str]:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-dev",
            project="workerbee-development",
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "operator_role": "root",
                "operator_authority_activated_workspace_set_digest": (
                    WORKSPACE_DIGEST
                ),
            },
        )
    )
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-0",
        title="WorkerBee development fixture",
        objective="Validate typed development overlays",
        criteria=[],
        assignments=[],
    )
    adapter = FakeWorkerBeeDevelopmentAdapter()
    provider = WorkerBeeDevelopmentProvider(adapter)
    broker = DynamicResourceBroker(
        store,
        enabled=True,
        providers={provider.provider_id: provider} if include_provider else {},
    )
    return broker, store, adapter, campaign["campaign_id"]


def request_resource(
    broker: DynamicResourceBroker,
    campaign_id: str,
    adapter: FakeWorkerBeeDevelopmentAdapter,
    resource_class: str,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "resource_class": resource_class,
        "requester_agent_id": "caller-dev",
        "operator_agent_id": "operator-0",
        "campaign_id": campaign_id,
        "handoff_id": None,
        "workspace_set_digest": WORKSPACE_DIGEST,
        "spec": {"max_bytes": 4096},
        "workerbee_binding_id": BINDING_ID,
        "workerbee_backend_generation": BACKEND_GENERATION,
        "provider_capability_sha256": adapter.capability[
            "capability_sha256"
        ],
        "ttl_seconds": 3600,
        "quota": 4096,
        "max_uses": 1,
    }
    values.update(overrides)
    return broker.request(**values)


def test_contract_fixture_matches_closed_public_adapter() -> None:
    path = (
        Path(__file__).parent
        / "fixtures"
        / "workerbee_development_resources_v1.json"
    )
    raw = path.read_bytes()
    assert len(raw) == 1149
    assert sha256(raw) == (
        "e898c4b2b8d878a9bbb5c12dce4f60f9c9369e45d770af9d805c81b8b3842227"
    )
    fixture = json.loads(raw)
    assert fixture == {
        "schema": "agent-pbx.workerbee-development-resource-conformance/v1",
        "provider": PROVIDER_ID,
        "capability_schema": PROVIDER_CAPABILITY_SCHEMA,
        "request_schema": PROVIDER_REQUEST_SCHEMA,
        "receipt_schema": PROVIDER_RECEIPT_SCHEMA,
        "cleanup_schema": PROVIDER_CLEANUP_SCHEMA,
        "resource_classes": list(RESOURCE_CLASSES),
        "mount_targets": MOUNT_TARGETS,
        "invariants": {
            "trusted_direct_bind_read_only": True,
            "trusted_direct_bind_rprivate": True,
            "writable_source": False,
            "resource_mode": 0o700,
            "cleanup_residue_count": 0,
        },
    }


@pytest.mark.parametrize("resource_class", RESOURCE_CLASSES)
def test_all_development_classes_are_generation_bound_and_zero_residue(
    tmp_path: Path,
    resource_class: str,
) -> None:
    broker, store, adapter, campaign_id = build_broker(tmp_path)
    request = request_resource(broker, campaign_id, adapter, resource_class)
    assert request["provider"] == PROVIDER_ID
    assert request["provider_capability_sha256"] == adapter.capability[
        "capability_sha256"
    ]
    assert request["restart_required"] is False
    broker.approve(
        request_id=request["request_id"],
        operator_agent_id="operator-0",
        approval_method=RESOURCE_APPROVAL_METHOD,
        confirmation=resource_approval_confirmation(request),
    )
    leased = broker.acquire(
        request_id=request["request_id"],
        operator_agent_id="operator-0",
    )
    metadata = leased["provider_receipt"]["resource_metadata"]
    assert metadata["mount_target"] == MOUNT_TARGETS[resource_class]
    assert metadata["mode"] == 0o700
    assert metadata["source_mount_read_only"] is True
    assert metadata["source_mount_rprivate"] is True
    assert metadata["writable_source"] is False
    assert broker.recover(
        request_id=request["request_id"],
        operator_agent_id="operator-0",
    )["status"] == "leased"
    assert adapter.status_calls == 1
    released = broker.release(
        request_id=request["request_id"],
        operator_agent_id="operator-0",
    )
    assert released["status"] == "released"
    assert released["cleanup_receipt"]["result"] == (
        "provider-released-zero-residue"
    )
    assert adapter.resources == {}
    assert store.get_dynamic_resource_request(request["request_id"])[
        "provider_capability_sha256"
    ] == adapter.capability["capability_sha256"]


def test_missing_provider_drift_auto_and_cleanup_residue_fail_closed(
    tmp_path: Path,
) -> None:
    broker, store, adapter, campaign_id = build_broker(
        tmp_path / "missing",
        include_provider=False,
    )
    with pytest.raises(DynamicResourceError, match="not configured"):
        request_resource(broker, campaign_id, adapter, "managed_venv")
    assert store.list_dynamic_resource_requests() == []

    broker, store, adapter, campaign_id = build_broker(tmp_path / "drift")
    with pytest.raises(DynamicResourceError, match="supplied together"):
        request_resource(
            broker,
            campaign_id,
            adapter,
            "managed_venv",
            workerbee_backend_generation=None,
        )
    with pytest.raises(DynamicResourceError, match="automatic resources require"):
        request_resource(
            broker,
            campaign_id,
            adapter,
            "managed_venv",
            approval_mode="auto",
            policy="trusted_rosie",
        )
    with pytest.raises(DynamicResourceError, match="digest mismatch"):
        request_resource(
            broker,
            campaign_id,
            adapter,
            "managed_venv",
            provider_capability_sha256="c" * 64,
        )
    request = request_resource(broker, campaign_id, adapter, "managed_venv")
    broker.approve(
        request_id=request["request_id"],
        operator_agent_id="operator-0",
        approval_method=RESOURCE_APPROVAL_METHOD,
        confirmation=resource_approval_confirmation(request),
    )
    adapter.capability["provider_generation"] = "provider-generation-2"
    adapter.capability["capability_sha256"] = unsigned_digest(
        adapter.capability,
        "capability_sha256",
    )
    with pytest.raises(DynamicResourceError, match="acquisition failed"):
        broker.acquire(
            request_id=request["request_id"],
            operator_agent_id="operator-0",
        )
    assert store.get_dynamic_resource_request(request["request_id"])["status"] == (
        "recovery_required"
    )

    broker, store, adapter, campaign_id = build_broker(tmp_path / "residue")
    request = request_resource(broker, campaign_id, adapter, "service_state")
    broker.approve(
        request_id=request["request_id"],
        operator_agent_id="operator-0",
        approval_method=RESOURCE_APPROVAL_METHOD,
        confirmation=resource_approval_confirmation(request),
    )
    broker.acquire(request_id=request["request_id"], operator_agent_id="operator-0")
    adapter.cleanup_residue_count = 1
    with pytest.raises(DynamicResourceError, match="requires recovery"):
        broker.release(
            request_id=request["request_id"],
            operator_agent_id="operator-0",
        )
    assert store.get_dynamic_resource_request(request["request_id"])["status"] == (
        "recovery_required"
    )


def test_api_gate_is_disabled_without_private_adapter_and_admits_injected_one(
    tmp_path: Path,
) -> None:
    adapter = FakeWorkerBeeDevelopmentAdapter()
    disabled = TestClient(
        create_app(
            ServerConfig(
                db_path=tmp_path / "disabled.sqlite",
                dynamic_resources_enabled=True,
            ),
            workerbee_development_adapter=adapter,
        )
    )
    enabled = TestClient(
        create_app(
            ServerConfig(
                db_path=tmp_path / "enabled.sqlite",
                dynamic_resources_enabled=True,
                workerbee_development_resources_enabled=True,
            ),
            workerbee_development_adapter=adapter,
        )
    )
    for client in (disabled, enabled):
        client.app.state.store.register_agent(
            AgentRegisterRequest(
                agent_id="caller-dev",
                project="workerbee-development",
            )
        )
        client.app.state.store.register_agent(
            AgentRegisterRequest(
                agent_id="operator-0",
                project="agent-pbx-operator",
                agent_type="operator",
                metadata={
                    "operator_role": "root",
                    "operator_authority_activated_workspace_set_digest": (
                        WORKSPACE_DIGEST
                    ),
                },
            )
        )
    payload = {
        "operator_agent_id": "operator-0",
        "resource_class": "managed_venv",
        "requester_agent_id": "caller-dev",
        "workspace_set_digest": WORKSPACE_DIGEST,
        "spec": {"max_bytes": 4096},
        "workerbee_binding_id": BINDING_ID,
        "workerbee_backend_generation": BACKEND_GENERATION,
        "provider_capability_sha256": adapter.capability[
            "capability_sha256"
        ],
        "quota": 4096,
    }
    for client in (disabled, enabled):
        campaign = client.app.state.store.create_operator_campaign(
            operator_agent_id="operator-0",
            title="WorkerBee API fixture",
            objective="Validate injected provider gate",
            criteria=[],
            assignments=[],
        )
        response = client.post(
            "/v1/operator/resources/request",
            json={**payload, "campaign_id": campaign["campaign_id"]},
        )
        if client is disabled:
            assert response.status_code == 400
            assert client.app.state.store.list_dynamic_resource_requests() == []
        else:
            assert response.status_code == 200
            assert response.json()["provider"] == PROVIDER_ID
