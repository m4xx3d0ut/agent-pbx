"""Provider-free qualification through the daemon ASGI API and mounted MCP gateway."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from itertools import permutations
import json
from pathlib import Path
import socket
import subprocess

import httpx
import pytest

import test_workerbee_controlled_run as controlled_run_fixtures
from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.workerbee_modes import reviewed_tools_for_access_profile, tools_for_access_profile
from test_workerbee_cutover import FakeAdapter, sha
from test_workerbee_gateway import git_repo


SOURCE_MODES = {
    "oss": "native-direct",
    "platform_debug": "snapshot-import",
    "user_appliance": "managed-clone",
}
STATUS_TOOL = "workerbee_v1_project_status"
QUARANTINED_TOOL = "workerbee_v1_unreviewed_cp4_fixture"
ROOT_HEADERS = {"Authorization": "Bearer synthetic-cp4-controller"}
SWITCH = "/v1/workerbee/switch"


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        pytest.fail("CP4 attempted a real network operation")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, denied)


class IsolatedAdapter(FakeAdapter):
    """Only the WorkerBee boundary is simulated, with each route independently bound."""

    def __init__(self, source):
        super().__init__(active=source)
        self.endpoints = {
            backend: f"http://127.0.0.1:{19801 + index}/mcp"
            for index, backend in enumerate(SOURCE_MODES)
        }
        self.calls = []
        self.drift_transport = False
        self.drift_route_for = ""
        self.paused_methods = {}
        self.unblock = asyncio.Event()

    def runtime(self, backend):
        return {**super().runtime(backend), "endpoint": self.endpoints[backend]}

    def cutover_qualification(self, backend):
        evidence = super().cutover_qualification(backend)
        if backend == self.drift_route_for:
            return replace(evidence, route_sha256=sha("different route:" + backend))
        return evidence

    @staticmethod
    def advertised_tools(backend):
        tools = (*tools_for_access_profile("local-deploy"), QUARANTINED_TOOL)
        if backend != "oss":
            return (*tools, "workerbee_v1_workspace_status")
        return tools

    async def rpc(self, request):
        backend = self.active
        assert str(request.url) == self.endpoints[backend]
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        method = payload["method"]
        self.calls.append((backend, method, payload.get("params", {})))
        if method in self.paused_methods:
            self.paused_methods[method].set()
            await self.unblock.wait()
        if method == "initialize":
            identity = self.cutover_qualification(backend).transport_identity_sha256
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": backend, "version": "1.2.3"},
                "_meta": {
                    "workerbee_transport_identity_sha256": (
                        sha("same URL, different runtime") if self.drift_transport else identity
                    ),
                },
            }
        elif method == "notifications/initialized":
            return httpx.Response(202)
        elif method == "tools/list":
            tools = self.advertised_tools(backend)
            result = {"tools": [{"name": tool, "inputSchema": {"type": "object"}} for tool in tools]}
        elif method == "tools/call":
            result = {"content": [], "structuredContent": {"backend": backend, "ok": True}}
        else:
            pytest.fail(f"Unexpected fixture MCP method {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    def http_client(self, headers=None, timeout=None, auth=None):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.rpc), headers=headers,
            timeout=timeout, auth=auth, trust_env=False,
        )


def install_synthetic_materializers(app, root, adapter, *, managed_clone):
    workspaces = app.state.workerbee_workspaces
    workspaces.helper_bin = "/synthetic/provider-free-materializer"
    managed = {}

    def snapshot(argv, **kwargs):
        assert adapter.active == "platform_debug"
        assert argv[:4] == [workspaces.helper_bin, "stage", "--backend", "platform_debug"]
        source = Path(argv[argv.index("--source") + 1])
        assert source.is_relative_to(root)
        binding_id = argv[argv.index("--binding-id") + 1]
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({
            "backend": "platform_debug",
            "runtime_workspace_path": f"/workspace/imports/{binding_id}",
            "content_sha256": sha(binding_id + commit),
            "source_commit": commit,
            "source_dirty": False,
            "receipt_path": f"/synthetic/snapshots/{binding_id}.json",
        }), stderr="")

    workspaces.runner = snapshot
    original = workspaces._materialized_binding

    def materialize(*, include_paths, **kwargs):
        runtime = kwargs["runtime"]
        if runtime.backend != "user_appliance" or not managed_clone:
            return original(include_paths=include_paths, **kwargs)
        assert adapter.active == runtime.backend
        assert kwargs["source"].is_relative_to(root) and not include_paths
        assert kwargs["source_mode"] == "managed-clone"
        template = workspaces._direct_binding(**kwargs)
        target = f"/appliance/clone/{template.binding_id}"
        mapping = workspaces._path_domain_receipt(
            binding_id=template.binding_id,
            runtime=runtime,
            source_mode="managed-clone",
            host_source_root=kwargs["source"],
            allowed_host_root=kwargs["allowed_host_root"],
            container_target_root=target,
            runtime_workspace_path=target,
            workspace_policy_digest=kwargs["workspace_policy_digest"],
            created_at=template.created_at,
        )
        qualification = adapter.cutover_qualification(runtime.backend)
        managed[template.binding_id] = {
            "console": f"https://console.synthetic.invalid/{template.binding_id}",
            "route_sha256": qualification.route_sha256,
            "transport_sha256": qualification.transport_identity_sha256,
            "instance_id": runtime.instance_id,
            "generation": runtime.generation,
        }
        return replace(
            template, runtime_workspace_path=target, path_domain=mapping,
            content_sha256=sha(template.binding_id + template.source_commit),
            materializer_receipt=f"/synthetic/managed/{template.binding_id}.json",
        )

    workspaces._materialized_binding = materialize
    return managed


@asynccontextmanager
async def daemon(root, adapter, monkeypatch, *, seed=True, managed_clone=True):
    app = create_app(ServerConfig(db_path=root / "pbx.sqlite", token="synthetic-cp4-controller"))
    switcher = app.state.workerbee_switcher
    if adapter is not None:
        switcher.adapter = switcher.durable.adapter = adapter
        monkeypatch.setattr("agent_pbx.workerbee_gateway.pinned_workerbee_http_client", adapter.http_client)
        monkeypatch.setattr("agent_pbx.workerbee_mcp.pinned_workerbee_http_client", adapter.http_client)
    managed = install_synthetic_materializers(app, root, adapter, managed_clone=managed_clone)
    async with app.router.lifespan_context(app):
        if seed and adapter is not None:
            switcher.cutover.observe(adapter.discover(), reason="synthetic CP4 boot")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost",
            headers=ROOT_HEADERS, trust_env=False,
        ) as client:
            yield client, app, managed


def expect(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


async def preview(client, target):
    return expect(await client.post(f"{SWITCH}/preview", json={"target": target}))


async def apply(client, record, *, headers=None, **overrides):
    return await client.post(f"{SWITCH}/apply", headers=headers, json={
        "transaction_id": record["transaction_id"],
        "preview_token": record["preview_token"],
        "confirm_target": record["target"],
        "confirm_privileged": True,
        **overrides,
    })


async def rollback(client, record):
    return expect(await client.post(f"{SWITCH}/rollback", json={
        "transaction_id": record["transaction_id"], "confirm": True,
    }))


async def bind(client, agent, repo, backend, project):
    mode = SOURCE_MODES[backend]
    expect(await client.post("/v1/agents/register", json={
        "agent_id": agent, "project": project,
        "metadata": {
            "cwd": str(repo), "workerbee_source_mode": mode, "workerbee_mcp_profile": "observe-local",
        },
    }))
    binding = expect(await client.post(f"/v2/agents/{agent}/workerbee/workspaces", json={
        "source_mode": mode,
    }))
    assert binding["source_mode"] == mode
    return binding, await issue(client, agent, binding["binding_id"])


async def issue(client, agent, binding_id):
    return expect(await client.post(f"/v2/agents/{agent}/workerbee/leases", json={
        "profile": "observe-local", "workspace_binding_id": binding_id, "ttl_seconds": 600,
    }))


async def gateway(client, lease, method="tools/list", *, token=None, **params):
    return await client.post(lease["gateway_url"], headers={
        "Authorization": f"Bearer {lease['token'] if token is None else token}",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-03-26",
    }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


@pytest.mark.asyncio
@pytest.mark.parametrize(("source", "target"), list(permutations(SOURCE_MODES, 2)))
async def test_exact_directed_transition(tmp_path, monkeypatch, source, target):
    adapter = IsolatedAdapter(source)
    repo = tmp_path / "source"
    other_repo = tmp_path / "other"
    git_repo(repo)
    git_repo(other_repo)
    async with daemon(tmp_path, adapter, monkeypatch) as (client, app, managed):
        binding, old_lease = await bind(client, "source-agent", repo, source, "project-a")
        await assert_gateway_scope(client, adapter, old_lease, source, "project-a")
        plan = await preview(client, target)
        assert plan["source"]["backend"] == source
        assert plan["target_runtime"]["backend"] == target
        assert plan["target_runtime"]["source_mode"] == SOURCE_MODES[target]
        assert plan["target_runtime"]["generation"] == plan["source"]["generation"] + 1
        journal = app.state.workerbee_runtime.cutover.journal(plan["transaction_id"])
        assert plan["target_qualification"] == {
            key: journal["target_qualification"][key]
            for key in ("artifact_sha256", "effective_config_sha256", "route_sha256")
        }
        for backend, evidence in (
            (source, journal["source_qualification"]), (target, journal["target_qualification"]),
        ):
            assert evidence["artifact_sha256"] == sha("artifact:" + backend)
            assert evidence["route_sha256"] == sha("route:" + backend)
            assert evidence["transport_identity_sha256"] == sha("transport:" + backend)
        assert adapter.endpoints[source] != adapter.endpoints[target]
        assert plan["target_runtime"]["instance_id"] != plan["source"]["instance_id"]
        after = expect(await apply(client, plan))
        assert after["phase"] == "verified"
        assert after["after"] == plan["target_runtime"]
        assert adapter.effects == [("activate", target)]
        calls_before = list(adapter.calls)
        await assert_rebind_required(client, old_lease)
        assert adapter.calls == calls_before

        # Roll back against the reviewed inventory before adding any new bindings.
        restored = await rollback(client, plan)
        assert restored["phase"] == "rolled_back"
        assert restored["rollback_after"]["backend"] == source
        assert restored["rollback_after"]["generation"] == after["after"]["generation"] + 1
        await assert_rebind_required(client, old_lease)
        assert adapter.calls == calls_before
        assert adapter.effects == [("activate", target), ("restore", source)]

        rebound, rebound_lease = await bind(client, "source-agent", repo, source, "project-a")
        assert rebound["binding_id"] != binding["binding_id"]
        assert rebound["backend_generation"] == restored["rollback_after"]["generation"]
        assert rebound["backend_instance_id"] == binding["backend_instance_id"]
        assert expect(await gateway(client, old_lease), 403)["code"] == "WORKERBEE_GATEWAY_LEASE_REVOKED"
        await assert_gateway_scope(client, adapter, rebound_lease, source, "project-a")

        plan = await preview(client, target)
        after = expect(await apply(client, plan))
        target_binding, target_lease = await bind(client, "target-agent", other_repo, target, "project-b")
        assert target_binding["backend_generation"] == after["after"]["generation"]
        assert target_binding["backend_instance_id"] == after["after"]["instance_id"]
        assert target_lease["lease"]["backend_generation"] == target_binding["backend_generation"]
        for workspace, checkout, backend in (
            (binding, repo, source), (target_binding, other_repo, target),
        ):
            assert workspace["path_domain"]["host_source_root"] == str(checkout)
            assert workspace["path_domain"]["allowed_host_root"] == str(checkout)
            expected_path = {
                "oss": str(checkout),
                "platform_debug": f"/workspace/imports/{workspace['binding_id']}",
                "user_appliance": f"/appliance/clone/{workspace['binding_id']}",
            }[backend]
            assert workspace["runtime_workspace_path"] == expected_path
            if backend == "user_appliance":
                assert managed[workspace["binding_id"]] == {
                    "console": f"https://console.synthetic.invalid/{workspace['binding_id']}",
                    "route_sha256": sha("route:" + backend),
                    "transport_sha256": sha("transport:" + backend),
                    "instance_id": workspace["backend_instance_id"],
                    "generation": workspace["backend_generation"],
                }
        await assert_gateway_scope(client, adapter, target_lease, target, "project-b")
        wrong_scope = await client.post("/v2/agents/target-agent/workerbee/leases", json={
            "profile": "observe-local", "workspace_binding_id": binding["binding_id"],
        })
        expect(wrong_scope, 409)
        assert expect(await gateway(client, target_lease, token=old_lease["token"]), 403)["code"] == (
            "WORKERBEE_GATEWAY_AUTH_FAILED"
        )
        await assert_rebind_required(client, rebound_lease)

        # Keep the URL, server name, and version unchanged while changing transport evidence.
        adapter.drift_transport = True
        before = gateway_dispatches(adapter)
        with_drift = expect(await gateway(client, target_lease))
        assert "error" in with_drift
        assert gateway_dispatches(adapter) == before
        adapter.drift_transport = False
        await assert_gateway_scope(client, adapter, target_lease, target, "project-b")

        service = app.state.workerbee_gateway
        with monkeypatch.context() as expired:
            expired.setattr(service, "clock", lambda: target_lease["lease"]["expires_at"])
            assert expect(await gateway(client, target_lease), 403)["code"] == (
                "WORKERBEE_GATEWAY_LEASE_EXPIRED"
            )
        lease_id = target_lease["lease"]["lease_id"]
        expect(await client.delete(f"/v2/agents/target-agent/workerbee/leases/{lease_id}"))
        assert expect(await gateway(client, target_lease), 403)["code"] == "WORKERBEE_GATEWAY_LEASE_REVOKED"


async def assert_rebind_required(client, lease):
    assert expect(await gateway(client, lease), 409)["code"] == "WORKERBEE_BACKEND_REBIND_REQUIRED"


def gateway_dispatches(adapter):
    return [call for call in adapter.calls if call[1] in {"tools/list", "tools/call"}]


async def assert_gateway_scope(client, adapter, lease, backend, project):
    listed = expect(await gateway(client, lease))
    assert {item["name"] for item in listed["result"]["tools"]} == set(
        reviewed_tools_for_access_profile("observe-local", backend)
    ).intersection(adapter.advertised_tools(backend))
    assert not any(item["name"].startswith("workerbee_v1_cloud_") for item in listed["result"]["tools"])
    status = expect(await gateway(client, lease, "tools/call", name=STATUS_TOOL, arguments={}))
    assert status["result"]["structuredContent"] == {"backend": backend, "ok": True}
    assert [call for call in adapter.calls if call[1] == "tools/call"][-1] == (
        backend, "tools/call", {"name": STATUS_TOOL, "arguments": {"project": project}},
    )
    before = [call for call in adapter.calls if call[1] == "tools/call"]
    for name, arguments in (
        (STATUS_TOOL, {"project": "foreign-project"}),
        (QUARANTINED_TOOL, {}),
        ("workerbee_v1_session_start", {}),
    ):
        denied = expect(await gateway(client, lease, "tools/call", name=name, arguments=arguments))
        assert "error" in denied or denied["result"]["isError"]
    assert [call for call in adapter.calls if call[1] == "tools/call"] == before


@pytest.mark.asyncio
async def test_default_and_legacy_effectful_adapters_remain_gated(tmp_path, monkeypatch):
    adapter = IsolatedAdapter("oss")
    async with daemon(tmp_path, None, monkeypatch) as (client, app, _):
        switcher = app.state.workerbee_switcher
        switcher.cutover.observe(adapter.discover(), reason="synthetic source")
        legacy = switcher.adapter
        assert next(d for d in legacy.descriptors() if d.backend == "user_appliance").qualification == "unavailable"
        monkeypatch.setattr(legacy, "activate", lambda *_: pytest.fail("legacy adapter executed"))
        for target in ("user_appliance", "platform_debug"):
            body = expect(await client.post(f"{SWITCH}/preview", json={"target": target}), 409)
            assert "lacks immutable artifact, route, and transport evidence" in body["detail"]
        assert switcher.cutover.latest() is None
        assert switcher.cutover.selected().generation == 1


@pytest.mark.asyncio
async def test_unqualified_managed_materializer_remains_deferred(tmp_path, monkeypatch):
    repo = tmp_path / "source"
    git_repo(repo)
    adapter = IsolatedAdapter("user_appliance")
    async with daemon(tmp_path, adapter, monkeypatch, managed_clone=False) as (client, _, _):
        response = await client.post("/v2/agents/new-agent/workerbee/workspaces", json={
            "project": "demo", "source_root": str(repo), "source_mode": "managed-clone",
        })
        assert expect(response, 409)["detail"]["code"] == "WORKERBEE_USER_APPLIANCE_DEFERRED"


@pytest.mark.asyncio
async def test_controller_preview_and_inventory_binding(tmp_path, monkeypatch):
    adapter = IsolatedAdapter("oss")
    repo = tmp_path / "source"
    git_repo(repo)
    async with daemon(tmp_path, adapter, monkeypatch) as (client, _, _):
        plan = await preview(client, "user_appliance")
        other = expect(await client.post("/v2/remote/tokens", json={
            "role": "controller", "label": "synthetic-competing-controller",
        }))
        expect(await apply(client, plan, headers={"Authorization": f"Bearer {other['token']}"}), 409)
        expect(await apply(client, plan, confirm_target="oss"), 409)
        expect(await apply(client, plan, confirm_privileged=False), 409)
        expect(await apply(client, plan, preview_token="different-synthetic-preview-token"), 409)
        expect(await apply(client, plan, headers={"Authorization": "Bearer invalid-synthetic"}), 403)
        assert not adapter.effects
        await bind(client, "fixture-agent", repo, "oss", "new-inventory")
        refused = expect(await apply(client, plan))
        assert refused["phase"] == "plan_drift"
        assert not adapter.effects
        assert expect(await client.get(SWITCH))["admission"]["admission"] == "open"

        route_plan = await preview(client, "user_appliance")
        adapter.drift_route_for = "user_appliance"
        assert expect(await apply(client, route_plan))["phase"] == "plan_drift"
        assert not adapter.effects
        assert expect(await client.get(SWITCH))["selected"]["generation"] == 1
        adapter.drift_route_for = ""
        revised = await preview(client, "user_appliance")
        completed = expect(await apply(client, revised))
        repeated = expect(await apply(client, revised))
        assert repeated == completed and adapter.effects == [("activate", "user_appliance")]


@pytest.mark.asyncio
async def test_gateway_catalog_and_read_drain_before_effect(tmp_path, monkeypatch):
    adapter = IsolatedAdapter("oss")
    left, right = tmp_path / "left", tmp_path / "right"
    git_repo(left)
    git_repo(right)
    async with daemon(tmp_path, adapter, monkeypatch) as (client, app, _):
        _, catalog_lease = await bind(client, "catalog-agent", left, "oss", "project-left")
        _, read_lease = await bind(client, "read-agent", right, "oss", "project-right")
        plan = await preview(client, "platform_debug")
        competitor = await preview(client, "user_appliance")
        owner = app.state.workerbee_runtime.cutover
        loop = asyncio.get_running_loop()
        draining = asyncio.Event()
        original_drain = owner.drain

        def on_drain(*args, **kwargs):
            loop.call_soon_threadsafe(draining.set)
            return original_drain(*args, **kwargs)

        monkeypatch.setattr(owner, "drain", on_drain)
        read_entered = adapter.paused_methods["tools/call"] = asyncio.Event()
        calls = [
            asyncio.create_task(gateway(client, read_lease, "tools/call", name=STATUS_TOOL, arguments={})),
        ]
        try:
            await asyncio.wait_for(read_entered.wait(), timeout=5)
            catalog_entered = adapter.paused_methods["tools/list"] = asyncio.Event()
            calls.append(asyncio.create_task(gateway(client, catalog_lease)))
            await asyncio.wait_for(catalog_entered.wait(), timeout=5)
            cutover = asyncio.create_task(apply(client, plan))
            calls.append(cutover)
            await asyncio.wait_for(draining.wait(), timeout=5)
            status = expect(await client.get(SWITCH))
            assert status["admission"]["admission"] == "draining"
            assert status["admission"]["admitted"] == 2
            assert status["selected"]["backend"] == "oss"
            assert status["targets"] == []
            before = list(adapter.calls)
            assert expect(await gateway(client, read_lease), 409)["code"] == "WORKERBEE_CUTOVER_CLOSED"
            refused_lease = expect(await client.post("/v2/agents/read-agent/workerbee/leases", json={
                "profile": "observe-local", "workspace_binding_id": read_lease["lease"]["workspace_binding_id"],
            }), 409)
            assert refused_lease["detail"]["code"] == "WORKERBEE_CUTOVER_CLOSED"
            agent = expect(await client.get("/v1/agents/read-agent/workerbee"))
            assert agent["status_source"] == "local_selector"
            assert agent["error"]["code"] == "WORKERBEE_CUTOVER_CLOSED"
            expect(await apply(client, competitor), 409)
            assert not adapter.effects and not cutover.done()
            assert adapter.calls == before
        finally:
            adapter.unblock.set()
            results = await asyncio.wait_for(asyncio.gather(*calls), timeout=10)
        for result in results[:2]:
            assert "result" in expect(result)
        assert expect(results[2])["phase"] == "verified"
        assert adapter.effects == [("activate", "platform_debug")]


@pytest.mark.asyncio
@pytest.mark.parametrize(("fault", "phase", "generation", "held"), [
    ("before_effect", "interrupted_before_effect", 1, False),
    ("unknown_effect", "activate_intent", 1, True),
    ("verify_target", "verified", 2, False),
    ("after_publish", "verified", 2, False),
])
async def test_api_restart_recovery(tmp_path, monkeypatch, fault, phase, generation, held):
    adapter = IsolatedAdapter("oss")
    async with daemon(tmp_path, adapter, monkeypatch) as (client, app, _):
        plan = await preview(client, "platform_debug")
        transaction = plan["transaction_id"]
        switcher = app.state.workerbee_switcher
        owner = switcher.cutover
        if fault == "before_effect":
            claim = owner.claim

            def lose_after_claim(*args, **kwargs):
                claim(*args, **kwargs)
                raise RuntimeError("synthetic interruption before adapter effect")

            monkeypatch.setattr(owner, "claim", lose_after_claim)
            expect(await apply(client, plan), 409)
            assert expect(await client.get(SWITCH))["admission"]["admission"] == "draining"
        else:
            adapter.activate_error = fault == "unknown_effect"
            adapter.verify_error = fault == "verify_target"
            if fault == "after_publish":
                def lose_completion():
                    raise RuntimeError("synthetic interruption after selection CAS")

                switcher.durable.invalidate = lose_completion
            expect(await apply(client, plan), 409)
            view = expect(await client.get(SWITCH))
            assert view["admission"]["admission"] == "recovery_required"
            assert not view["targets"] and "preview_token" not in view["last_transaction"]
    adapter.activate_error = adapter.verify_error = False
    async with daemon(tmp_path, adapter, monkeypatch, seed=False) as (client, app, _):
        view = expect(await client.get(SWITCH))
        assert view["admission"]["admission"] == ("recovery_required" if held else "open")
        assert view["selected"]["generation"] == generation
        assert view["last_transaction"]["phase"] == phase
        assert view["last_transaction"]["transaction_id"] == transaction
        assert adapter.effects == ([] if fault == "before_effect" else [("activate", "platform_debug")])
        if held:
            expect(await client.post(f"{SWITCH}/preview", json={"target": "oss"}), 409)
        else:
            noop = await preview(client, view["selected"]["backend"])
            assert expect(await apply(client, noop))["phase"] == "verified_noop"
            assert app.state.workerbee_runtime.selected().generation == generation


@pytest.mark.asyncio
async def test_approved_executor_uses_existing_reservation_during_api_drain(tmp_path, monkeypatch):
    adapter = IsolatedAdapter("platform_debug")
    original_switcher = None

    def retain_daemon_switcher(config):
        nonlocal original_switcher
        app = create_app(config)
        original_switcher = app.state.workerbee_switcher
        return app

    with monkeypatch.context() as construction:
        construction.setattr(controlled_run_fixtures, "create_app", retain_daemon_switcher)
        app, fork_id, campaign_id = controlled_run_fixtures._api_app(tmp_path, monkeypatch)
    assert original_switcher is not None
    app.state.workerbee_switcher = original_switcher
    original_switcher.adapter = original_switcher.durable.adapter = adapter
    owner = original_switcher.cutover
    preflight_entered, allow_preflight, draining = (asyncio.Event() for _ in range(3))
    calls = []

    async def isolated_executor_call(endpoint, name, arguments, **kwargs):
        assert endpoint == adapter.endpoints["platform_debug"]
        step = name.rsplit("_", 1)[-1]
        calls.append(step)
        if step == "preflight":
            preflight_entered.set()
            await allow_preflight.wait()
            return {"data": {"ok": True, **{
                key: arguments[key]
                for key in ("request_sha256", "profile_sha256", "tool_manifest_sha256")
            }}}
        if step == "start":
            assert owner.posture()["admission"] == "draining"
            return {"data": {"run_id": "synthetic-run", "workspace_id": "synthetic-run-workspace"}}
        if step == "status":
            return {"data": {"status": "succeeded"}}
        if step == "receipt":
            return {"data": {"cleanup": {"complete": True, "residue": []}}}
        pytest.fail(f"Unqualified executor operation {step}")

    executor = app.state.workerbee_controlled_run_executor
    monkeypatch.setattr(executor, "tool_caller", isolated_executor_call)

    async def capabilities(endpoint, name, arguments, **kwargs):
        assert endpoint == adapter.endpoints["platform_debug"]
        assert not arguments
        if name == "workerbee_v1_runtime_policy_status":
            return {"ok": True, "data": {"profile": "linux-strict-workload"}}
        assert name == "workerbee_v1_capabilities"
        return {"data": {"mcp_tools": list(controlled_run_fixtures.CONTROLLED_RUN_REQUIRED_TOOLS)}}

    monkeypatch.setattr("agent_pbx.api.call_workerbee_mcp_tool", capabilities)
    loop = asyncio.get_running_loop()
    drain = owner.drain

    def note_drain(*args, **kwargs):
        loop.call_soon_threadsafe(draining.set)
        return drain(*args, **kwargs)

    monkeypatch.setattr(owner, "drain", note_drain)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost", trust_env=False,
    ) as client:
        owner.observe(adapter.discover(), reason="synthetic controlled-run source")
        plan = await preview(client, "oss")
        payload = controlled_run_fixtures._api_payload(fork_id, campaign_id)
        created = expect(await client.post("/v2/workerbee/controlled-runs", json=payload))
        request_id = created["request"]["request_id"]
        approval = f"/v2/workerbee/controlled-runs/{request_id}/decision"
        expect(await client.post(approval, json={"approve": True, "decided_by": "root"}))
        task = executor._tasks[request_id]
        switching = None
        try:
            await asyncio.wait_for(preflight_entered.wait(), timeout=5)
            switching = asyncio.create_task(apply(client, plan))
            await asyncio.wait_for(draining.wait(), timeout=5)
            assert owner.posture()["admitted"] == 1
            assert not switching.done() and not adapter.effects
            denied = expect(await client.post("/v2/workerbee/controlled-runs", json=payload), 409)
            assert denied["detail"]["code"] == "WORKERBEE_CUTOVER_CLOSED"
        finally:
            allow_preflight.set()
            await asyncio.wait_for(task, timeout=10)
            if switching is not None:
                result = await asyncio.wait_for(switching, timeout=10)
        assert calls == ["preflight", "start", "status", "receipt"] and not executor._tasks
        assert app.state.store.get_workerbee_controlled_run_request(request_id)["status"] == "succeeded"
        assert expect(result)["phase"] == "verified"
        assert adapter.effects == [("activate", "oss")]
