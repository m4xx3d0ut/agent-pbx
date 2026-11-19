from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from agent_pbx.doctor import _workerbee_runtime_binding_check, _workerbee_switcher_check
from agent_pbx.mcp_daemon import MCPDaemonConfig
from agent_pbx.ui.controllers.workerbee_projection import WorkerBeeProjectionController
from agent_pbx.ui.controllers.workerbee_selector import WorkerBeeSelectorController
from agent_pbx.workerbee_cutover import WorkerBeeCutover
from agent_pbx.workerbee_readback import binding_readback, run_readback


def test_bound_identity_distinguishes_stale_legacy_and_exact_generation():
    selected = {"backend": "oss", "instance_id": "a" * 24, "fingerprint": "b" * 64, "generation": 4}
    assert binding_readback(selected, selected)["state"] == "matches_selected"
    assert binding_readback(selected, {**selected, "generation": 3})["rebind_required"]
    assert binding_readback(selected, {**selected, "fingerprint": "c" * 64})["state"] == "rebind_required"
    assert binding_readback(selected, {"backend": "oss", "generation": 4})["state"] == "legacy_unverified"
    run = run_readback({
        "status": "running", "backend": "oss", "backend_instance_id": "a" * 24,
        "backend_fingerprint": "b" * 64, "backend_generation": 2,
    }, selected)
    assert run["bound"]["generation"] == 2 and run["selected"]["generation"] == 4
    assert run["source_mode"] == "immutable-commit-export" and run["rebind_required"]


def test_doctor_uses_closed_durable_evidence_without_any_upstream_probe(tmp_path: Path, monkeypatch):
    cutover = WorkerBeeCutover(tmp_path / "workerbee-runtime")
    cutover.start()
    values = {"backend": "oss", "endpoint": "http://127.0.0.1:9991/mcp",
              "server_name": "fixture", "server_version": "1"}
    selection = cutover.observe(values, reason="fixture")
    cutover.enable_serving()
    with cutover.operation("fixture_observation"):
        cutover.record_observation({**values, "server_version": "2", "ready": True})
    cutover.stop()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Doctor attempted an upstream probe with durable selector state")

    monkeypatch.setattr("agent_pbx.doctor.inspect_workerbee_switcher", forbidden)
    config = MCPDaemonConfig(state_root=tmp_path, db_path=tmp_path / "pbx.sqlite")
    assert _workerbee_switcher_check(forbidden, 1.0, config=config).status == "pass"
    check = _workerbee_runtime_binding_check(config, forbidden, 1.0)
    assert check.status == "warn"
    assert "recovery_required" in check.summary and "mismatch" in check.summary
    assert selection.instance_id in check.detail
    assert "127.0.0.1" not in check.detail


@pytest.mark.asyncio
async def test_tui_held_readback_and_stale_preview_send_zero_mutation_requests():
    tx = {"transaction_id": "reviewed", "target": "platform_debug", "preview_token": "preview-secret"}
    calls = []
    selected = {"backend": "oss", "generation": 3, "instance_id": "exact-instance"}
    gate = {"admission": "recovery_required", "reason": "external_identity_drift"}
    rendered = SimpleNamespace(text="")
    notices = []

    def route(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={
            "selected": selected, "admission": gate, "observation_state": "mismatch",
            "last_transaction": {"phase": "previewed", "transaction_id": "different"},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(route), base_url="http://fixture") as client:
        host = SimpleNamespace(
            api_client=lambda: client, token=None, selected_agent_id=None,
            workerbee_status_by_agent={"old": {}}, workerbee_project_visibility=object(),
            query_one_or_none=lambda *_: rendered, notify=lambda msg, **_: notices.append(msg),
        )
        selector = WorkerBeeSelectorController(host)
        diagnostic = await selector._governed_workerbee_preflight()
        assert diagnostic.code == "WORKERBEE_DAEMON_OWNED" and not host.workerbee_status_by_agent
        assert "exact-instance" in rendered.text and "recovery_required" in rendered.text
        await selector.apply_workerbee_switch(tx, confirmation="switch platform_debug")
        assert "stale" in notices[-1]
    assert calls == [("GET", "/v1/workerbee/switch")] * 2


def test_tui_projection_displays_exact_binding_requiring_rebind():
    text = WorkerBeeProjectionController(SimpleNamespace()).format_workerbee_status({
        "agent_id": "synthetic",
        "selected_runtime": {
            "backend": "oss", "generation": 4, "instance_id": "selected-instance",
            "admission": {"admission": "open"},
            "gateway": {
                "state": "rebind_required", "rebind_required": True,
                "bound": {"backend": "oss", "generation": 2, "instance_id": "old-instance"},
            },
        },
    })
    assert "Backend generation: 4" in text
    assert "selected-instance" in text and "old-instance" in text and "rebind_required" in text


@pytest.mark.asyncio
async def test_tui_recovered_rollback_uses_current_publication_evidence():
    source = {"backend": "oss", "generation": 1, "instance_id": "prior-instance"}
    after = {"backend": "platform_debug", "generation": 2, "instance_id": "current-instance"}
    journal = {"phase": "verified", "transaction_id": "published-tx", "source": source, "after": after}
    shown = []
    calls = []

    def route(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={
            "selected": after, "last_transaction": {"transaction_id": "later-preview"},
            "rollback_transaction": journal, "admission": {"admission": "open"},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(route), base_url="http://fixture") as client:
        host = SimpleNamespace(api_client=lambda: client, token=None, push_screen=shown.append)
        selector = WorkerBeeSelectorController(host)
        await selector.preview_workerbee_switch_rollback()
    assert calls == [("GET", "/v1/workerbee/switch")]
    modal = shown[0]
    assert modal.expected_confirmation() == "rollback published-tx"
    assert modal.transaction["before"] == after
    assert modal.transaction["target_runtime"] == {**source, "generation": 3}
