from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


REQUEST_SHA256 = "a" * 64
WORKSPACE_SET_DIGEST = "b" * 64


def resource_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(agent_id="caller-1", project="dynamic-resource-test")
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"operator_role": "root", "pbx_mode": "report"},
        )
    )
    return store


def request_values(
    request_id: str | None = None,
    *,
    status: str = "pending_approval",
    max_uses: int = 4,
) -> dict[str, Any]:
    return {
        "request_id": request_id or str(uuid.uuid4()),
        "request_sha256": REQUEST_SHA256,
        "resource_class": "managed_cache",
        "requester_agent_id": "caller-1",
        "operator_agent_id": "operator-0",
        "campaign_id": str(uuid.uuid4()),
        "handoff_id": None,
        "workspace_set_digest": WORKSPACE_SET_DIGEST,
        "workerbee_binding_id": None,
        "workerbee_backend_generation": None,
        "provider_capability_sha256": None,
        "spec": {"name": "pytest-cache", "max_bytes": 4096},
        "provider": "agent-pbx-local/v1",
        "ttl_seconds": 3600,
        "quota": 4096,
        "max_uses": max_uses,
        "status": status,
        "approval_mode": "manual",
        "delegation_lease_id": None,
        "restart_required": False,
        "expires_at": time.time() + 3600,
    }


def event_types(store: Store, request_id: str) -> list[str]:
    return [
        str(item["event_type"])
        for item in store.list_dynamic_resource_events(request_id=request_id)
    ]


def global_event_types(store: Store) -> list[str]:
    return [
        str(item["type"])
        for item in store.list_events(after_id=0, limit=1000)
        if str(item["type"]).startswith("dynamic_resource_")
    ]


def test_request_single_flight_and_resource_event_are_atomic(tmp_path: Path) -> None:
    store = resource_store(tmp_path)
    failed_id = str(uuid.uuid4())
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_dynamic_resource_requested_event
            BEFORE INSERT ON dynamic_resource_events
            WHEN NEW.event_type = 'requested'
            BEGIN
                SELECT RAISE(ABORT, 'forced dynamic resource request failure');
            END;
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced dynamic resource request failure",
    ):
        store.request_or_reuse_dynamic_resource(**request_values(failed_id))
    assert store.get_dynamic_resource_request(failed_id) is None
    assert global_event_types(store) == []

    with store.connect() as connection:
        connection.execute("DROP TRIGGER fail_dynamic_resource_requested_event")

    barrier = threading.Barrier(8)

    def create(_index: int) -> tuple[str, bool]:
        barrier.wait(timeout=5)
        request, created = store.request_or_reuse_dynamic_resource(
            **request_values(str(uuid.uuid4()))
        )
        return str(request["request_id"]), created

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(8)))

    request_ids = {request_id for request_id, _created in results}
    assert len(request_ids) == 1
    assert sum(created for _request_id, created in results) == 1
    request_id = request_ids.pop()
    assert event_types(store, request_id) == ["requested"]
    assert global_event_types(store) == ["dynamic_resource_requested"]
    assert len(store.list_dynamic_resource_requests()) == 1


def test_transition_compare_and_swap_and_event_are_atomic(tmp_path: Path) -> None:
    store = resource_store(tmp_path)
    request, _created = store.request_or_reuse_dynamic_resource(**request_values())
    request_id = str(request["request_id"])
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_dynamic_resource_approved_event
            BEFORE INSERT ON dynamic_resource_events
            WHEN NEW.event_type = 'approved'
            BEGIN
                SELECT RAISE(ABORT, 'forced dynamic resource transition failure');
            END;
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced dynamic resource transition failure",
    ):
        store.update_dynamic_resource_request(
            request_id,
            expected_statuses=("pending_approval",),
            status="approved",
            operator_agent_id="operator-0",
            event_type="approved",
            approval={"approved": True},
        )
    persisted = store.get_dynamic_resource_request(request_id)
    assert persisted is not None
    assert persisted["status"] == "pending_approval"
    assert persisted["approval"] is None
    assert event_types(store, request_id) == ["requested"]
    assert global_event_types(store) == ["dynamic_resource_requested"]

    with store.connect() as connection:
        connection.execute("DROP TRIGGER fail_dynamic_resource_approved_event")

    barrier = threading.Barrier(2)

    def transition(target: str) -> tuple[str, str]:
        barrier.wait(timeout=5)
        try:
            updated = store.update_dynamic_resource_request(
                request_id,
                expected_statuses=("pending_approval",),
                status=target,
                operator_agent_id="operator-0",
                event_type=target,
                approval={"approved": target == "approved"},
            )
            return "ok", str(updated["status"])
        except ValueError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(transition, ("approved", "rejected")))

    assert sum(kind == "ok" for kind, _value in results) == 1
    assert sum(
        kind == "error" and value == "dynamic resource state changed"
        for kind, value in results
    ) == 1
    persisted = store.get_dynamic_resource_request(request_id)
    assert persisted is not None
    assert persisted["status"] in {"approved", "rejected"}
    assert event_types(store, request_id) == ["requested", persisted["status"]]
    assert global_event_types(store) == [
        "dynamic_resource_requested",
        f"dynamic_resource_{persisted['status']}",
    ]


def test_consumption_quota_and_used_event_are_atomic(tmp_path: Path) -> None:
    store = resource_store(tmp_path)
    request, _created = store.request_or_reuse_dynamic_resource(
        **request_values(status="leased", max_uses=4)
    )
    request_id = str(request["request_id"])
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_dynamic_resource_used_event
            BEFORE INSERT ON dynamic_resource_events
            WHEN NEW.event_type = 'used'
            BEGIN
                SELECT RAISE(ABORT, 'forced dynamic resource use failure');
            END;
            """
        )

    with pytest.raises(
        sqlite3.IntegrityError,
        match="forced dynamic resource use failure",
    ):
        store.consume_dynamic_resource_request(
            request_id,
            requester_agent_id="caller-1",
        )
    assert store.get_dynamic_resource_request(request_id)["use_count"] == 0
    assert event_types(store, request_id) == ["requested"]

    with store.connect() as connection:
        connection.execute("DROP TRIGGER fail_dynamic_resource_used_event")

    barrier = threading.Barrier(8)

    def consume(_index: int) -> tuple[str, int | str]:
        barrier.wait(timeout=5)
        try:
            updated = store.consume_dynamic_resource_request(
                request_id,
                requester_agent_id="caller-1",
            )
            return "ok", int(updated["use_count"])
        except ValueError as exc:
            return "error", str(exc)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume, range(8)))

    assert sum(kind == "ok" for kind, _value in results) == 4
    assert sum(
        kind == "error" and value == "dynamic resource use quota is exhausted"
        for kind, value in results
    ) == 4
    persisted = store.get_dynamic_resource_request(request_id)
    assert persisted is not None
    assert persisted["use_count"] == 4
    assert event_types(store, request_id) == ["requested", "used", "used", "used", "used"]
    assert global_event_types(store).count("dynamic_resource_used") == 4


def test_dynamic_resource_row_and_event_json_fallbacks_are_preserved(
    tmp_path: Path,
) -> None:
    store = resource_store(tmp_path)
    request, _created = store.request_or_reuse_dynamic_resource(
        **request_values(status="leased")
    )
    request_id = str(request["request_id"])
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE dynamic_resource_requests
            SET spec_json = '{', approval_json = '{',
                provider_receipt_json = '{', cleanup_receipt_json = '{',
                restart_required = 1
            WHERE request_id = ?
            """,
            (request_id,),
        )
        connection.execute(
            """
            UPDATE dynamic_resource_events
            SET metadata_json = '{'
            WHERE request_id = ?
            """,
            (request_id,),
        )

    persisted = store.get_dynamic_resource_request(request_id)
    assert persisted is not None
    assert persisted["spec"] is None
    assert persisted["approval"] is None
    assert persisted["provider_receipt"] is None
    assert persisted["cleanup_receipt"] is None
    assert persisted["ttl_seconds"] == 3600
    assert persisted["quota"] == 4096
    assert persisted["max_uses"] == 4
    assert persisted["use_count"] == 0
    assert persisted["restart_required"] is True
    assert store.list_dynamic_resource_events(request_id=request_id)[0]["metadata"] == {}
