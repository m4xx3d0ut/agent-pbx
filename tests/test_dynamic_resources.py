from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.dynamic_resources import (
    DynamicResourceBroker,
    DynamicResourceError,
    RESOURCE_APPROVAL_METHOD,
    RESOURCE_CLEANUP_SCHEMA,
    RESOURCE_RECEIPT_SCHEMA,
    resource_approval_confirmation,
)
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


WORKSPACE_DIGEST = "a" * 64


class FakeProvider:
    def __init__(self, provider_id: str = "agent-pbx-local/v1") -> None:
        self.provider_id = provider_id
        self.acquire_calls = 0
        self.release_calls = 0
        self.recover_calls = 0
        self.fail_acquire = False
        self.fail_release = False
        self.recoverable = True

    def acquire(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self.acquire_calls += 1
        if self.fail_acquire:
            raise RuntimeError("fixture acquire failed")
        return self.receipt(request)

    def release(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self.release_calls += 1
        if self.fail_release:
            raise RuntimeError("fixture release failed")
        provider_receipt = request["provider_receipt"]
        return {
            "schema": RESOURCE_CLEANUP_SCHEMA,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "provider": request["provider"],
            "provider_generation": provider_receipt["provider_generation"],
            "resource_identity": provider_receipt["resource_identity"],
            "cleanup_complete": True,
            "result": "removed",
            "cleaned_at": time.time(),
        }

    def recover(self, request: Mapping[str, Any]) -> dict[str, Any] | None:
        self.recover_calls += 1
        return self.receipt(request) if self.recoverable else None

    @staticmethod
    def receipt(request: Mapping[str, Any]) -> dict[str, Any]:
        import hashlib
        import json

        spec_bytes = json.dumps(
            request["spec"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        metadata: dict[str, Any] = {}
        metadata_bytes = json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return {
            "schema": RESOURCE_RECEIPT_SCHEMA,
            "request_id": request["request_id"],
            "request_sha256": request["request_sha256"],
            "resource_class": request["resource_class"],
            "provider": request["provider"],
            "provider_generation": "fixture-generation-1",
            "resource_identity": f"fixture:{request['request_id']}",
            "workspace_set_digest": request["workspace_set_digest"],
            "workerbee_binding_id": request["workerbee_binding_id"],
            "workerbee_backend_generation": request[
                "workerbee_backend_generation"
            ],
            "provider_capability_sha256": request[
                "provider_capability_sha256"
            ],
            "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
            "resource_metadata": metadata,
            "resource_metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
            "quota": request["quota"],
            "max_uses": request["max_uses"],
            "activated_at": time.time(),
            "expires_at": request["expires_at"],
            "restart_required": request["restart_required"],
        }


def broker(
    tmp_path: Any,
    *,
    clock: Any = time.time,
) -> tuple[DynamicResourceBroker, Store, FakeProvider, str]:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    for agent_id, agent_type in (
        ("caller-1", "caller"),
        ("operator-0", "operator"),
        ("operator-7", "operator"),
        ("operator-8", "operator"),
    ):
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project="dynamic-resources",
                agent_type=agent_type,
                metadata={
                    "operator_role": "root",
                    "operator_authority_activated_workspace_set_digest": (
                        WORKSPACE_DIGEST
                    ),
                }
                if agent_type == "operator"
                else {},
            )
        )
    campaign = store.create_operator_campaign(
        operator_agent_id="operator-7",
        title="Dynamic resource fixture",
        objective="Exercise exact typed resource authority",
        criteria=[],
        assignments=[],
    )
    provider = FakeProvider()
    service = DynamicResourceBroker(
        store,
        enabled=True,
        providers={provider.provider_id: provider},
        clock=clock,
    )
    return service, store, provider, campaign["campaign_id"]


def request_cache(
    service: DynamicResourceBroker,
    campaign_id: str,
    **overrides: Any,
) -> dict[str, Any]:
    values: dict[str, Any] = {
        "resource_class": "managed_cache",
        "requester_agent_id": "caller-1",
        "operator_agent_id": "operator-7",
        "campaign_id": campaign_id,
        "handoff_id": None,
        "workspace_set_digest": WORKSPACE_DIGEST,
        "spec": {"name": "pytest-cache", "max_bytes": 4096},
        "ttl_seconds": 3600,
        "quota": 4096,
        "max_uses": 1,
    }
    values.update(overrides)
    return service.request(**values)


def test_manual_single_flight_f8_lease_use_and_cleanup(tmp_path: Any) -> None:
    service, store, provider, campaign_id = broker(tmp_path)

    request = request_cache(service, campaign_id, runtime_state="running")
    duplicate = request_cache(service, campaign_id, runtime_state="running")
    assert request["created"] is True
    assert duplicate["created"] is False
    assert duplicate["request_id"] == request["request_id"]
    assert request["restart_required"] is True
    assert request["approval_confirmation"] == resource_approval_confirmation(request)

    with pytest.raises(DynamicResourceError, match="confirmation mismatch"):
        service.approve(
            request_id=request["request_id"],
            operator_agent_id="operator-7",
            approval_method=RESOURCE_APPROVAL_METHOD,
            confirmation="APPROVE RESOURCE wrong wrong",
        )
    assert provider.acquire_calls == 0
    assert store.get_dynamic_resource_request(request["request_id"])["status"] == (
        "pending_approval"
    )

    approved = service.approve(
        request_id=request["request_id"],
        operator_agent_id="operator-7",
        approval_method=RESOURCE_APPROVAL_METHOD,
        confirmation=resource_approval_confirmation(request),
    )
    assert approved["status"] == "approved"
    leased = service.acquire(
        request_id=request["request_id"], operator_agent_id="operator-7"
    )
    assert leased["status"] == "leased"
    assert leased["provider_receipt"]["restart_required"] is True
    assert provider.acquire_calls == 1
    assert service.use(
        request_id=request["request_id"], requester_agent_id="caller-1"
    )["use_count"] == 1
    with pytest.raises(ValueError, match="exhausted"):
        service.use(
            request_id=request["request_id"], requester_agent_id="caller-1"
        )

    released = service.release(
        request_id=request["request_id"], operator_agent_id="operator-7"
    )
    assert released["status"] == "released"
    assert released["cleanup_receipt"]["cleanup_complete"] is True
    assert provider.release_calls == 1
    assert [event["event_type"] for event in store.list_dynamic_resource_events(
        request_id=request["request_id"]
    )] == ["requested", "approved", "leased", "used", "released"]
    assert service.release(
        request_id=request["request_id"], operator_agent_id="operator-7"
    )["status"] == "released"
    assert provider.release_calls == 1


def test_concurrent_requests_are_single_flight(tmp_path: Any) -> None:
    service, store, _provider, campaign_id = broker(tmp_path)

    with ThreadPoolExecutor(max_workers=6) as pool:
        requests = list(
            pool.map(lambda _index: request_cache(service, campaign_id), range(12))
        )

    assert len({request["request_id"] for request in requests}) == 1
    assert sum(request["created"] is True for request in requests) == 1
    assert len(store.list_dynamic_resource_requests()) == 1


def test_strict_specs_auto_policy_and_disabled_classes(tmp_path: Any) -> None:
    service, _store, _provider, campaign_id = broker(tmp_path)

    auto = request_cache(
        service,
        campaign_id,
        approval_mode="auto",
        policy="trusted_rosie",
    )
    assert auto["status"] == "approved"
    assert auto["approval"]["method"] == "trusted-rosie-reviewed-low-risk/v1"

    for resource_class, spec in (
        ("workspace_unix_socket", {"relative_path": "run/service.sock"}),
        (
            "loopback_service",
            {"host": "127.0.0.1", "port": 8080, "protocol": "tcp"},
        ),
        ("port_reservation", {"port": 9090, "protocol": "tcp"}),
    ):
        created = service.request(
            resource_class=resource_class,
            requester_agent_id="caller-1",
            operator_agent_id="operator-7",
            campaign_id=campaign_id,
            handoff_id=None,
            workspace_set_digest=WORKSPACE_DIGEST,
            spec=spec,
        )
        assert created["resource_class"] == resource_class
        assert created["quota"] == 1

    with pytest.raises(DynamicResourceError, match="reviewed Trusted Rosie"):
        service.request(
            resource_class="loopback_service",
            requester_agent_id="caller-1",
            operator_agent_id="operator-7",
            campaign_id=campaign_id,
            handoff_id=None,
            workspace_set_digest=WORKSPACE_DIGEST,
            spec={"host": "127.0.0.1", "port": 8080, "protocol": "tcp"},
            approval_mode="auto",
            policy="trusted_rosie",
        )
    with pytest.raises(DynamicResourceError, match="not qualified"):
        service.request(
            resource_class="credential_reference",
            requester_agent_id="caller-1",
            operator_agent_id="operator-7",
            campaign_id=campaign_id,
            handoff_id=None,
            workspace_set_digest=WORKSPACE_DIGEST,
            spec={"credential": "forbidden"},
        )
    with pytest.raises(DynamicResourceError, match="fields are invalid"):
        request_cache(
            service,
            campaign_id,
            spec={
                "name": "pytest-cache",
                "max_bytes": 4096,
                "command": "sh -c anything",
            },
        )
    with pytest.raises(DynamicResourceError, match="quota does not match"):
        request_cache(service, campaign_id, quota=1)
    for unsafe in ("../run.sock", "tmp/./run.sock", "tmp//run.sock", "/tmp/run.sock"):
        with pytest.raises(DynamicResourceError, match="unsafe"):
            service.request(
                resource_class="workspace_unix_socket",
                requester_agent_id="caller-1",
                operator_agent_id="operator-7",
                campaign_id=campaign_id,
                handoff_id=None,
                workspace_set_digest=WORKSPACE_DIGEST,
                spec={"relative_path": unsafe},
            )


def test_campaign_lease_is_scope_bound_and_single_use(tmp_path: Any) -> None:
    service, store, _provider, campaign_id = broker(tmp_path)
    lease = store.create_operator_delegation_lease(
        source_operator_agent_id="operator-0",
        target_operator_agent_ids=["operator-7"],
        campaign_ids=[campaign_id],
        workspace_set_digest=WORKSPACE_DIGEST,
        allowed_action_classes=["dynamic_resource:managed_cache"],
        allowed_roots=[],
        exact_paths=[],
        denied_actions=[],
        max_uses=1,
        expires_at=time.time() + 3600,
        requested_by="operator-0",
    )
    store.decide_operator_delegation_lease(
        lease["lease_id"], approve=True, approved_by="root-user"
    )
    request = request_cache(
        service,
        campaign_id,
        approval_mode="campaign_lease",
        delegation_lease_id=lease["lease_id"],
    )
    leased = service.acquire(
        request_id=request["request_id"], operator_agent_id="operator-7"
    )
    assert leased["status"] == "leased"
    used_lease = store.get_operator_delegation_lease(lease["lease_id"])
    assert used_lease is not None
    assert used_lease["status"] == "exhausted"
    assert used_lease["use_count"] == 1

    other_lease = store.create_operator_delegation_lease(
        source_operator_agent_id="operator-0",
        target_operator_agent_ids=["operator-8"],
        campaign_ids=[campaign_id],
        workspace_set_digest=WORKSPACE_DIGEST,
        allowed_action_classes=["dynamic_resource"],
        allowed_roots=[],
        exact_paths=[],
        denied_actions=[],
        max_uses=1,
        expires_at=time.time() + 3600,
        requested_by="operator-0",
    )
    store.decide_operator_delegation_lease(
        other_lease["lease_id"], approve=True, approved_by="root-user"
    )
    blocked = request_cache(
        service,
        campaign_id,
        approval_mode="campaign_lease",
        delegation_lease_id=other_lease["lease_id"],
    )
    with pytest.raises(DynamicResourceError, match="outside the delegation lease"):
        service.acquire(
            request_id=blocked["request_id"], operator_agent_id="operator-7"
        )


def test_workerbee_generation_receipts_recovery_and_drift(tmp_path: Any) -> None:
    service, store, provider, campaign_id = broker(tmp_path)
    workerbee = FakeProvider("workerbee-existing/v1")
    service.providers[workerbee.provider_id] = workerbee

    with pytest.raises(DynamicResourceError, match="binding and backend generation"):
        service.request(
            resource_class="existing_workerbee_service",
            requester_agent_id="caller-1",
            operator_agent_id="operator-7",
            campaign_id=campaign_id,
            handoff_id=None,
            workspace_set_digest=WORKSPACE_DIGEST,
            spec={"service_id": "database"},
        )
    request = service.request(
        resource_class="existing_workerbee_service",
        requester_agent_id="caller-1",
        operator_agent_id="operator-7",
        campaign_id=campaign_id,
        handoff_id=None,
        workspace_set_digest=WORKSPACE_DIGEST,
        spec={"service_id": "database"},
        workerbee_binding_id="binding-1",
        workerbee_backend_generation="generation-3",
    )
    service.approve(
        request_id=request["request_id"],
        operator_agent_id="operator-7",
        approval_method=RESOURCE_APPROVAL_METHOD,
        confirmation=resource_approval_confirmation(request),
    )
    original = workerbee.receipt

    def drifted_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
        receipt = original(value)
        receipt["workerbee_backend_generation"] = "generation-4"
        return receipt

    workerbee.receipt = drifted_receipt  # type: ignore[method-assign]
    with pytest.raises(DynamicResourceError, match="acquisition failed"):
        service.acquire(
            request_id=request["request_id"], operator_agent_id="operator-7"
        )
    assert store.get_dynamic_resource_request(request["request_id"])["status"] == (
        "recovery_required"
    )
    workerbee.receipt = original  # type: ignore[method-assign]
    recovered = service.recover(
        request_id=request["request_id"], operator_agent_id="operator-7"
    )
    assert recovered["status"] == "leased"

    workerbee.fail_release = True
    with pytest.raises(DynamicResourceError, match="requires recovery"):
        service.release(
            request_id=request["request_id"], operator_agent_id="operator-7"
        )
    assert store.get_dynamic_resource_request(request["request_id"])["status"] == (
        "recovery_required"
    )
    assert provider.acquire_calls == 0


def test_disabled_broker_and_approval_identity_fail_closed(tmp_path: Any) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = DynamicResourceBroker(store)
    with pytest.raises(DynamicResourceError, match="disabled"):
        request_cache(service, str(uuid.uuid4()))

    service, _store, _provider, campaign_id = broker(tmp_path / "enabled")
    request = request_cache(service, campaign_id)
    with pytest.raises(DynamicResourceError, match="operator mismatch"):
        service.approve(
            request_id=request["request_id"],
            operator_agent_id="operator-8",
            approval_method=RESOURCE_APPROVAL_METHOD,
            confirmation=resource_approval_confirmation(request),
        )
    with pytest.raises(DynamicResourceError, match="exact F8"):
        service.approve(
            request_id=request["request_id"],
            operator_agent_id="operator-7",
            approval_method="api-button",
            confirmation=resource_approval_confirmation(request),
        )
    with pytest.raises(DynamicResourceError, match="requester is not registered"):
        request_cache(service, campaign_id, requester_agent_id="unknown-caller")
    with pytest.raises(DynamicResourceError, match="activated operator authority"):
        request_cache(service, campaign_id, workspace_set_digest="d" * 64)
    with pytest.raises(DynamicResourceError, match="campaign is not active"):
        request_cache(service, str(uuid.uuid4()))


def test_expired_manual_approval_is_terminal_without_provider_call(tmp_path: Any) -> None:
    now = [time.time()]
    service, store, provider, campaign_id = broker(
        tmp_path,
        clock=lambda: now[0],
    )
    request = request_cache(service, campaign_id, ttl_seconds=60)
    now[0] += 61

    with pytest.raises(DynamicResourceError, match="expired"):
        service.approve(
            request_id=request["request_id"],
            operator_agent_id="operator-7",
            approval_method=RESOURCE_APPROVAL_METHOD,
            confirmation=resource_approval_confirmation(request),
        )

    assert store.get_dynamic_resource_request(request["request_id"])["status"] == (
        "expired"
    )
    assert provider.acquire_calls == 0


def test_api_is_default_disabled_and_root_f8_controlled(tmp_path: Any) -> None:
    disabled = TestClient(create_app(ServerConfig(db_path=tmp_path / "disabled.sqlite")))
    assert disabled.get(
        "/v1/operator/resources",
        params={"operator_agent_id": "operator-0"},
    ).status_code == 409

    client = TestClient(
        create_app(
            ServerConfig(
                db_path=tmp_path / "enabled.sqlite",
                dynamic_resources_enabled=True,
            )
        )
    )
    for agent_id, role in (
        ("operator-0", "root"),
        ("operator-0-fork-demo", "fork"),
    ):
        metadata = {
            "pbx_mode": "report",
            "operator_role": role,
            "operator_authority_activated_workspace_set_digest": (
                WORKSPACE_DIGEST
            ),
        }
        if role == "fork":
            metadata["logical_operator_id"] = "operator-0"
        assert client.post(
            "/v1/agents/register",
            json={
                "agent_id": agent_id,
                "project": "agent-pbx-operator",
                "agent_type": "operator",
                "metadata": metadata,
            },
        ).status_code == 200
    assert client.post(
        "/v1/agents/register",
        json={
            "agent_id": "caller-1",
            "project": "dynamic-resources",
            "metadata": {"pbx_mode": "report"},
        },
    ).status_code == 200

    campaign = client.app.state.store.create_operator_campaign(
        operator_agent_id="operator-0",
        title="Dynamic resource API fixture",
        objective="Exercise exact typed resource authority",
        criteria=[],
        assignments=[],
    )

    payload = {
        "operator_agent_id": "operator-0",
        "resource_class": "managed_cache",
        "requester_agent_id": "caller-1",
        "campaign_id": campaign["campaign_id"],
        "workspace_set_digest": WORKSPACE_DIGEST,
        "spec": {"name": "api-cache", "max_bytes": 1024},
        "quota": 1024,
    }
    created = client.post("/v1/operator/resources/request", json=payload)
    assert created.status_code == 200
    request = created.json()
    assert request["status"] == "pending_approval"

    denied = client.post(
        "/v1/operator/resources/request",
        json={**payload, "operator_agent_id": "operator-0-fork-demo"},
    )
    assert denied.status_code == 400

    wrong = client.post(
        f"/v1/operator/resources/{request['request_id']}/approve",
        json={
            "operator_agent_id": "operator-0",
            "approval_method": RESOURCE_APPROVAL_METHOD,
            "confirmation": "APPROVE RESOURCE wrong wrong",
        },
    )
    assert wrong.status_code == 400
    approved = client.post(
        f"/v1/operator/resources/{request['request_id']}/approve",
        json={
            "operator_agent_id": "operator-0",
            "approval_method": RESOURCE_APPROVAL_METHOD,
            "confirmation": request["approval_confirmation"],
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
