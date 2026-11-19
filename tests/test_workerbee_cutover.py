from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_backend_adapter import WorkerBeeSwitchBackend
from agent_pbx.workerbee_cutover import WorkerBeeCutover, WorkerBeeCutoverError
from agent_pbx.workerbee_durable_switch import SCHEMA, WorkerBeeCutoverQualification
from agent_pbx.workerbee_gateway import WorkerBeeGatewayError, WorkerBeeGatewayService
from agent_pbx.workerbee_runtime import WORKERBEE_BACKENDS, WorkerBeeRuntimeRegistry, runtime_fingerprint
from agent_pbx.workerbee_switcher import WorkerBeeSwitchService
from agent_pbx.workerbee_workspace import WorkerBeeWorkspaceService


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class FakeAdapter:
    platform_debug_posture = "loopback"

    def __init__(self, active: str = "oss") -> None:
        self.active = active
        self.effects: list[tuple[str, str]] = []
        self.drift = ""
        self.activate_error = False
        self.verify_error = False

    def runtime(self, backend: str) -> dict:
        return {
            "backend": backend, "ready": True,
            "endpoint": "http://127.0.0.1:18701/mcp",
            "server_name": backend, "server_version": "1.2.3",
            "platform_debug_posture": self.platform_debug_posture,
        }

    def descriptors(self):
        return tuple(WorkerBeeSwitchBackend(name, "verified", sha(name)) for name in WORKERBEE_BACKENDS)

    def discover(self):
        return self.runtime(self.active)

    def cutover_qualification(self, backend: str) -> WorkerBeeCutoverQualification:
        return WorkerBeeCutoverQualification(
            SCHEMA, backend, sha("artifact:" + backend + self.drift),
            sha("config:" + backend), sha("route:" + backend),
            runtime_fingerprint(self.runtime(backend)), sha("transport:" + backend),
        )

    def preflight(self, target: str) -> str:
        return f"plan for {target}"

    def activate(self, target: str) -> None:
        self.effects.append(("activate", target))
        self.active = target
        if self.activate_error:
            raise RuntimeError("fake lost command completion")

    def verify(self, target: str, **_):
        if self.verify_error and self.effects:
            raise RuntimeError("fake lost verification")
        evidence = self.cutover_qualification(target)
        return {
            "verified": self.active == target,
            "after": self.discover(),
            "capability": {
                "ready": True,
                "workerbee_transport_identity_sha256": evidence.transport_identity_sha256,
            },
            "cutover_qualification": asdict(evidence),
        }

    def restore(self, target: str, **kwargs):
        self.effects.append(("restore", target))
        self.active = target
        return self.verify(target, **kwargs)


def start(tmp_path: Path, adapter=None, **kwargs):
    adapter = adapter or FakeAdapter()
    cutover = WorkerBeeCutover(tmp_path / "selector")
    cutover.start()
    if cutover.selected() is None:
        cutover.observe(adapter.discover(), reason="initial fixture")
    service = WorkerBeeSwitchService(
        state_root=tmp_path / "switch", cutover=cutover, adapter=adapter, **kwargs,
    )
    service.recover_startup()
    cutover.enable_serving()
    return cutover, service, adapter


def apply(service, preview, *, controller="root"):
    return service.apply(
        preview["transaction_id"], controller=controller,
        preview_token=preview["preview_token"], confirm_target=preview["target"],
        confirm_privileged=True,
    )


def test_legacy_import_once_and_reject_second_owner(tmp_path):
    root = tmp_path / "selector"
    adapter = FakeAdapter()
    legacy = WorkerBeeRuntimeRegistry(root)
    record = legacy.observe(adapter.discover())
    raw = legacy.selection_path.read_bytes()
    owner, _, _ = start(tmp_path, adapter)
    with pytest.raises(WorkerBeeCutoverError, match="Another daemon"):
        WorkerBeeCutover(root).start()
    assert legacy.selected() == record == owner.selected()
    with pytest.raises(ValueError, match="owned"):
        legacy.observe(adapter.discover(), force_generation=True)
    owner.stop()
    owner, _, _ = start(tmp_path, adapter)
    assert owner.selected() == record
    assert legacy.selection_path.read_bytes() == raw
    owner.stop()


@pytest.mark.parametrize("field,value", [
    ("generation", True), ("instance_id", "bogus"), ("endpoint", "http://user:token@localhost/mcp"),
])
def test_bad_legacy_record_prevents_startup(tmp_path, field, value):
    root = tmp_path / "selector"
    legacy = WorkerBeeRuntimeRegistry(root)
    selection = legacy.observe(FakeAdapter().discover()).public_dict()
    legacy.selection_path.write_text(json.dumps({**selection, field: value}), encoding="utf-8")
    with pytest.raises(ValueError, match="identity"):
        WorkerBeeCutover(root).start()


@pytest.mark.parametrize(("source", "target"), [
    (source, target) for source in WORKERBEE_BACKENDS for target in WORKERBEE_BACKENDS
    if source != target
])
def test_durable_directed_cutover_and_rollback(tmp_path, source, target):
    owner, service, adapter = start(tmp_path, FakeAdapter(source))
    preview = service.preview(target, controller="root")
    with pytest.raises(WorkerBeeCutoverError, match="controller differs"):
        apply(service, preview, controller="foreign")
    result = apply(service, preview)
    assert result["phase"] == "verified"
    assert owner.selected().backend == target and owner.selected().generation == 2
    assert apply(service, preview) == result
    with owner.operation("catalog") as operation:
        identity = SimpleNamespace(
            serverInfo=SimpleNamespace(name=target, version="1.2.3"),
            meta={"workerbee_transport_identity_sha256": sha("transport:" + target)},
        )
        operation.verify_connection(identity)
        identity.meta["workerbee_transport_identity_sha256"] = sha("transport:" + source)
        with pytest.raises(WorkerBeeCutoverError, match="Connected WorkerBee identity"):
            operation.verify_connection(identity)
    restored = service.rollback(preview["transaction_id"], confirm=True, controller="root")
    assert restored["phase"] == "rolled_back"
    assert owner.selected().backend == source and owner.selected().generation == 3
    assert service.rollback(preview["transaction_id"], confirm=True, controller="root") == restored
    assert adapter.effects == [("activate", target), ("restore", source)]
    assert owner.posture()["admission"] == "open"
    owner.stop()


def test_noop_stays_same_generation(tmp_path):
    owner, service, adapter = start(tmp_path)
    preview = service.preview("oss", controller="root")
    assert apply(service, preview)["phase"] == "verified_noop"
    assert owner.selected().generation == 1
    assert not adapter.effects
    owner.stop()


@pytest.mark.parametrize("kind", ["gateway_catalog", "gateway_call", "bind", "controlled_run_execute"])
def test_close_drains_pinned_operations_and_refuses_new_work(tmp_path, monkeypatch, kind):
    owner, service, adapter = start(tmp_path)
    preview = service.preview("platform_debug", controller="root")
    closing = threading.Event()
    original = owner.drain

    def drain(*args, **kwargs):
        closing.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, "drain", drain)
    results = []
    with owner.operation(kind, lease_id="lease", binding_id="binding") as ticket:
        worker = threading.Thread(target=lambda: results.append(apply(service, preview)))
        worker.start()
        assert closing.wait(3)
        assert ticket.runtime.generation == 1
        assert not adapter.effects
        with pytest.raises(WorkerBeeCutoverError, match="admission is closed"):
            with owner.operation("new_read", inherit=False):
                pytest.fail("admitted a new read during drain")
        with pytest.raises(WorkerBeeCutoverError, match="differs"):
            with owner.operation("cross_binding", binding_id="another"):
                pytest.fail("inherited a different binding")
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert results[0]["phase"] == "verified"
    owner.stop()


def test_drain_timeout_keeps_old_generation_and_no_effect(tmp_path):
    owner, service, adapter = start(tmp_path)
    preview = service.preview("platform_debug", controller="root")
    service.durable.drain_timeout = 0
    with owner.operation("gateway_read"):
        result = apply(service, preview)
    assert result["phase"] == "drain_timeout"
    assert owner.selected().generation == 1
    assert not adapter.effects
    owner.stop()


def test_inventory_drift_closes_attempt_before_effect(tmp_path):
    inventory = {"workspace_bindings": ["a"]}
    owner, service, adapter = start(tmp_path, impact_provider=lambda: inventory)
    preview = service.preview("platform_debug", controller="root")
    inventory["workspace_bindings"] = ["b"]
    assert apply(service, preview)["phase"] == "plan_drift"
    assert not adapter.effects
    assert owner.selected().generation == 1
    owner.stop()


def test_ambiguous_adapter_intent_survives_restart_without_replay(tmp_path):
    owner, service, adapter = start(tmp_path)
    preview = service.preview("platform_debug", controller="root")
    adapter.activate_error = True
    with pytest.raises(WorkerBeeCutoverError, match="exact readback"):
        apply(service, preview)
    owner.stop()
    owner, service, _ = start(tmp_path, adapter)
    assert owner.posture()["admission"] == "recovery_required"
    assert owner.journal(preview["transaction_id"])["phase"] == "activate_intent"
    assert owner.selected().generation == 1
    assert adapter.effects == [("activate", "platform_debug")]
    owner.stop()


def test_recover_interrupted_verification_without_reactivation(tmp_path):
    owner, service, adapter = start(tmp_path)
    preview = service.preview("platform_debug", controller="root")
    adapter.verify_error = True
    with pytest.raises(WorkerBeeCutoverError, match="exact readback"):
        apply(service, preview)
    assert owner.journal(preview["transaction_id"])["phase"] == "verify_target"
    owner.stop()
    adapter.verify_error = False
    owner, _, _ = start(tmp_path, adapter)
    assert owner.journal(preview["transaction_id"])["phase"] == "verified"
    assert owner.selected().generation == 2
    assert adapter.effects == [("activate", "platform_debug")]
    owner.stop()


def test_recover_after_publication_without_advancing_twice(tmp_path):
    def stop_before_reopen():
        raise RuntimeError("fake crash after committed CAS")

    owner, service, adapter = start(tmp_path, cache_invalidator=stop_before_reopen)
    preview = service.preview("platform_debug", controller="root")
    with pytest.raises(WorkerBeeCutoverError, match="exact readback"):
        apply(service, preview)
    assert owner.selected().generation == 2
    assert owner.journal(preview["transaction_id"])["phase"] == "published"
    owner.stop()
    owner, _, _ = start(tmp_path, adapter)
    assert owner.journal(preview["transaction_id"])["phase"] == "verified"
    assert owner.selected().generation == 2
    assert adapter.effects == [("activate", "platform_debug")]
    owner.stop()


def test_production_http_routes_use_durable_owner_and_closed_gate(tmp_path):
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    switcher = app.state.workerbee_switcher
    adapter = FakeAdapter()
    switcher.adapter = switcher.durable.adapter = adapter
    owner = switcher.cutover
    with TestClient(app) as client:
        owner.observe(adapter.discover(), reason="qualified fixture")
        preview = client.post("/v1/workerbee/switch/preview", json={"target": "platform_debug"})
        assert preview.status_code == 200, preview.text
        record = preview.json()
        denied = client.post("/v1/workerbee/switch/apply", json={
            "transaction_id": record["transaction_id"], "preview_token": record["preview_token"],
            "confirm_target": record["target"], "confirm_privileged": False,
        })
        assert denied.status_code == 409 and not adapter.effects
        adapter.activate_error = True
        held = client.post("/v1/workerbee/switch/apply", json={
            "transaction_id": record["transaction_id"], "preview_token": record["preview_token"],
            "confirm_target": record["target"], "confirm_privileged": True,
        })
        assert held.status_code == 409
        status = client.get("/v1/workerbee/switch").json()
        assert status["admission"]["admission"] == "recovery_required"
        blocked = client.put(
            "/v2/workerbee/workspace-policy",
            json={"policy": "scoped-workbench", "request_mode": "approval"},
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "WORKERBEE_CUTOVER_CLOSED"


def test_restart_pre_effect_reopens_without_replay(tmp_path):
    owner, service, adapter = start(tmp_path)
    preview = service.preview("platform_debug", controller="root")
    owner.claim(
        preview["transaction_id"], "root", phase="previewed", selection=owner.selected(),
    )
    owner.stop()
    owner, _, _ = start(tmp_path, adapter)
    assert owner.posture()["admission"] == "open"
    assert owner.selected().generation == 1
    assert owner.journal(preview["transaction_id"])["phase"] == "interrupted_before_effect"
    assert adapter.effects == []
    owner.stop()


def test_restart_holds_if_committed_backend_observation_drifts(tmp_path):
    owner, service, adapter = start(tmp_path)
    assert apply(service, service.preview("platform_debug", controller="root"))["phase"] == "verified"
    owner.stop()
    adapter.active = "oss"
    owner, _, _ = start(tmp_path, adapter)
    assert owner.posture()["reason"] == "committed_runtime_readback_failed"
    assert owner.selected().backend == "platform_debug"
    assert owner.selected().generation == 2
    assert adapter.effects == [("activate", "platform_debug")]
    owner.stop()


def test_old_gateway_lease_never_revives_after_rollback(tmp_path):
    owner, service, adapter = start(tmp_path)
    registry = WorkerBeeRuntimeRegistry(owner.root, cutover=owner)
    source = tmp_path / "repo"
    source.mkdir()
    subprocess.run(["git", "-C", str(source), "init", "-q"], check=True)
    subprocess.run([
        "git", "-C", str(source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "--no-gpg-sign", "-qm", "fixture",
    ], check=True)
    workspaces = WorkerBeeWorkspaceService(owner.root / "workspaces")
    gateway = WorkerBeeGatewayService(owner.root / "gateway", registry, workspaces)
    binding = workspaces.prepare(runtime=registry.selected(), project="test", source_root=source)
    old_lease, token = gateway.issue(
        agent_id="agent", codex_session_id="session", profile="observe-local",
        workspace_binding_id=binding.binding_id,
    )
    assert gateway.validate(old_lease.lease_id, token) == old_lease
    preview = service.preview("platform_debug", controller="root")
    assert apply(service, preview)["phase"] == "verified"
    with pytest.raises(WorkerBeeGatewayError) as wrong_backend:
        gateway.validate(old_lease.lease_id, token)
    assert wrong_backend.value.code == "WORKERBEE_BACKEND_REBIND_REQUIRED"
    assert service.rollback(preview["transaction_id"], confirm=True, controller="root")["phase"] == "rolled_back"
    with pytest.raises(WorkerBeeGatewayError) as old_generation:
        gateway.validate(old_lease.lease_id, token)
    assert old_generation.value.code == "WORKERBEE_BACKEND_REBIND_REQUIRED"
    assert registry.selected().generation == 3
    assert adapter.effects == [("activate", "platform_debug"), ("restore", "oss")]
    owner.stop()


def test_uncertain_remote_operation_keeps_gate_closed_across_owners(tmp_path):
    owner, _, adapter = start(tmp_path)
    with owner.operation("gateway_call", lease_id="agent-lease", binding_id="binding") as ticket:
        owner.pin_workspace("agent-lease", "binding", sha("domain"))
        ticket.dispatch()
    assert owner.posture()["reason"] == "uncertain_operation"
    owner.stop()
    owner, _, _ = start(tmp_path, adapter)
    assert owner.posture()["reason"] == "orphaned_operations"
    assert owner.posture()["admitted"] == 1
    assert owner.selected().generation == 1
    owner.stop()


def _readback_app(tmp_path, adapter=None):
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="test-admin"))
    switcher = app.state.workerbee_switcher
    adapter = adapter or FakeAdapter()
    switcher.adapter = switcher.durable.adapter = adapter
    return app, switcher.cutover, adapter


def test_selector_readback_never_promotes_an_observation_and_stays_local_when_held(tmp_path, monkeypatch):
    app, owner, adapter = _readback_app(tmp_path)
    headers = {"Authorization": "Bearer test-admin"}
    with TestClient(app, headers=headers) as client:
        view = client.get("/v2/workerbee/runtime").json()
        assert view["selected"] is None and owner.selected() is None
        assert view["bootstrap_available"]
        assert view["observed"]["backend"] == "oss"
        assert view["observation_state"] == "probed"
        assert "endpoint" not in view["observed"]
        assert {row["backend"]: row["qualification_state"] for row in view["backends"]} == {
            "oss": "probed", "platform_debug": "configured_only", "user_appliance": "configured_only",
        }
        owner.observe(adapter.discover(), reason="isolated admitted bootstrap")
        adapter.active = "platform_debug"
        view = client.get("/v2/workerbee/runtime").json()
        assert view["selected"]["backend"] == "oss" and view["selected"]["generation"] == 1
        assert view["observed"]["backend"] == "platform_debug"
        assert view["observation_state"] == "mismatch"
        assert view["admission"]["admission"] == "recovery_required"

    def unexpected():
        pytest.fail("held selector attempted backend work")

    monkeypatch.setattr(adapter, "discover", unexpected)
    app, owner, _ = _readback_app(tmp_path, adapter)
    with TestClient(app, headers=headers) as client:
        held = client.get("/v1/workerbee/switch").json()
        assert held["selected"] == view["selected"] and held["observed"] == view["observed"]
        assert held["targets"] == [] and not held["bootstrap_available"]
        assert held["admission"]["reason"] == "external_identity_drift"
        registered = client.post("/v1/agents/register", json={
            "agent_id": "held-agent", "project": "synthetic",
        })
        assert registered.status_code == 200
        response = client.get("/v1/agents/held-agent/workerbee")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status_source"] == "local_selector" and not payload["available"]
        assert payload["selected_runtime"]["generation"] == 1
        assert payload["selected_runtime"]["admission"]["admission"] == "recovery_required"


def test_switch_owner_readback_restarts_without_reissuing_preview_and_pins_rollback(tmp_path):
    app, owner, adapter = _readback_app(tmp_path)
    headers = {"Authorization": "Bearer test-admin"}
    with TestClient(app, headers=headers) as client:
        owner.observe(adapter.discover(), reason="isolated seed")
        response = client.post("/v1/workerbee/switch/preview", json={"target": "platform_debug"})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["target_runtime"]["source_mode"] == "snapshot-import"
        assert preview["target_runtime"]["generation"] == 2

    app, owner, _ = _readback_app(tmp_path, adapter)
    with TestClient(app, headers=headers) as client:
        view = client.get("/v1/workerbee/switch").json()
        assert view["last_transaction"]["transaction_id"] == preview["transaction_id"]
        assert view["last_transaction"]["target_runtime"] == preview["target_runtime"]
        assert "preview_token" not in view["last_transaction"] and "preview_token_sha256" not in view["last_transaction"]
        other = client.post("/v2/remote/tokens", json={"role": "controller", "label": "other-fixture"})
        assert other.status_code == 200, other.text
        other_headers = {"Authorization": f"Bearer {other.json()['token']}"}
        other_view = client.get("/v1/workerbee/switch", headers=other_headers).json()
        assert other_view["last_transaction"] is None
        assert view["rollback_transaction"] is None
        response = client.post("/v1/workerbee/switch/apply", json={
            "transaction_id": preview["transaction_id"], "preview_token": preview["preview_token"],
            "confirm_target": preview["target"], "confirm_privileged": True,
        })
        assert response.status_code == 200, response.text
        verified = response.json()
        assert verified["after"] == preview["target_runtime"]

    app, owner, _ = _readback_app(tmp_path, adapter)
    with TestClient(app, headers=headers) as client:
        view = client.get("/v1/workerbee/switch").json()
        assert view["selected"] == verified["after"]
        assert view["rollback_transaction"]["after"] == verified["after"]
        assert view["rollback_transaction"]["source"] == preview["source"]
        assert view["rollback_transaction"]["transaction_id"] == preview["transaction_id"]


def test_selector_role_visibility_and_scoped_legacy_binding(tmp_path):
    app, owner, adapter = _readback_app(tmp_path)
    headers = {"Authorization": "Bearer test-admin"}
    with TestClient(app, headers=headers) as client:
        for agent in ("team-a", "team-b"):
            registered = client.post("/v1/agents/register", json={
                "agent_id": agent, "project": "synthetic",
                "metadata": {"workerbee_backend": "oss", "workerbee_backend_generation": 1},
            })
            assert registered.status_code == 200
        owner.observe(adapter.discover(), reason="isolated seed")
        assert client.get("/v1/workerbee/switch").status_code == 200
        for role in ("observer", "controller"):
            token = client.post("/v2/remote/tokens", json={
                "role": role, "label": f"synthetic-{role}", "allowed_agent_ids": ["team-a"],
            })
            assert token.status_code == 200, token.text
            scoped = {"Authorization": f"Bearer {token.json()['token']}"}
            assert client.get("/v1/workerbee/switch", headers=scoped).status_code == 403
            assert client.get("/v2/workerbee/runtime", headers=scoped).status_code == 403
            assert client.get("/v2/agents/team-b/workerbee/access-profile", headers=scoped).status_code == 403
            own = client.get("/v2/agents/team-a/workerbee/access-profile", headers=scoped)
            if role == "observer":
                assert own.status_code == 403
            else:
                assert own.status_code == 200, own.text
                binding = own.json()["runtime_binding"]
                assert binding["gateway"]["state"] == "legacy_unverified"
                assert binding["gateway"]["rebind_required"]
                assert binding["selected"]["generation"] == 1
                assert "backend_catalog" not in binding and "endpoint" not in binding["selected"]
