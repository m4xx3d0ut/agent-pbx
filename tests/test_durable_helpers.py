from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.durable_helpers import DurableHelperError, DurableHelperRegistry
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def registry(tmp_path: Path, *, measurements: bool = True) -> DurableHelperRegistry:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"pbx_mode": "report", "operator_role": "root"},
        )
    )
    source_root = tmp_path / "proposals"
    source_root.mkdir(mode=0o700)
    return DurableHelperRegistry(
        store,
        root=tmp_path / "registry",
        approved_source_roots=[source_root],
        enabled=True,
        measurements_enabled=measurements,
    )


def write_helper(root: Path, name: str = "helper.py", marker: str = "one") -> Path:
    path = root / "proposals" / name
    path.write_text(
        "from pathlib import Path\n"
        f"MARKER = {marker!r}\n"
        "def main(output_dir: str) -> None:\n"
        "    Path(output_dir, 'result.txt').write_text(MARKER)\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def propose(registry: DurableHelperRegistry, path: Path, *, name: str = "demo") -> dict:
    return registry.propose(
        source_path=str(path),
        name=name,
        scopes=["workspace.read", "evidence.render"],
        kb_refs=[],
        operator_agent_id="operator-0",
    )


def activate(registry: DurableHelperRegistry, helper: dict) -> dict:
    receipt = registry.validate(
        helper_id=helper["helper_id"],
        operator_agent_id="operator-0",
    )
    assert receipt["passed"] is True
    reviewed = registry.transition(
        helper_id=helper["helper_id"],
        target_status="reviewed",
        operator_agent_id="operator-0",
    )
    assert reviewed["status"] == "reviewed"
    return registry.transition(
        helper_id=helper["helper_id"],
        target_status="active",
        operator_agent_id="operator-0",
    )


def test_exact_path_proposal_validation_materialization_and_invocation_plan(
    tmp_path: Path,
) -> None:
    service = registry(tmp_path)
    source = write_helper(tmp_path)

    helper = propose(service, source)
    assert helper["status"] == "proposed"
    assert helper["manifest"]["runtime"] == "python3-isolated"
    assert oct((tmp_path / "registry" / "objects").stat().st_mode & 0o777) == "0o700"
    assert propose(service, source)["idempotent"] is True

    active = activate(service, helper)
    assert active["status"] == "active"
    plan = service.invocation_plan(
        helper_id=helper["helper_id"],
        scope="workspace.read",
        operator_agent_id="operator-0",
    )
    assert plan["shell"] is False
    assert plan["execution_performed"] is False
    assert plan["environment"] == {}
    assert plan["argv"][1:3] == ["-I", "-S"]
    materialized = Path(plan["argv"][3])
    assert materialized.stat().st_mode & 0o777 == 0o444
    assert Path(plan["cwd"]).stat().st_mode & 0o777 == 0o700

    metrics = service.measurements()
    assert metrics["proposals"] == 1
    assert metrics["activations"] == 1
    assert metrics["reuses"] == 1
    assert metrics["reuse_ratio"] == 1.0


def test_proposal_rejects_outside_symlink_and_drift(tmp_path: Path) -> None:
    service = registry(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("pass\n", encoding="utf-8")
    outside.chmod(0o600)
    with pytest.raises(DurableHelperError, match="outside approved"):
        propose(service, outside)

    linked = tmp_path / "proposals" / "linked.py"
    linked.symlink_to(outside)
    with pytest.raises(DurableHelperError, match="symlink"):
        propose(service, linked)

    unsafe = write_helper(tmp_path, name="unsafe.py")
    unsafe.chmod(0o666)
    with pytest.raises(DurableHelperError, match="bounded, unshared"):
        propose(service, unsafe)


def test_failed_validation_cannot_be_reviewed(tmp_path: Path) -> None:
    service = registry(tmp_path)
    source = tmp_path / "proposals" / "broken.py"
    source.write_text("def broken(:\n", encoding="utf-8")
    source.chmod(0o600)
    helper = propose(service, source)

    receipt = service.validate(
        helper_id=helper["helper_id"],
        operator_agent_id="operator-0",
    )
    assert receipt["passed"] is False
    assert receipt["executed_helper_code"] is False
    assert not any((tmp_path / "registry" / "validation").iterdir())
    with pytest.raises(DurableHelperError, match="no matching successful"):
        service.transition(
            helper_id=helper["helper_id"],
            target_status="reviewed",
            operator_agent_id="operator-0",
        )


def test_isolated_validation_compiles_without_executing_helper(tmp_path: Path) -> None:
    service = registry(tmp_path)
    marker = tmp_path / "must-not-exist"
    source = tmp_path / "proposals" / "side-effect.py"
    source.write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    helper = propose(service, source)

    receipt = service.validate(
        helper_id=helper["helper_id"],
        operator_agent_id="operator-0",
    )

    assert receipt["passed"] is True
    assert receipt["executed_helper_code"] is False
    assert not marker.exists()


def test_replacement_quarantine_and_content_free_measurements(tmp_path: Path) -> None:
    service = registry(tmp_path)
    first = propose(service, write_helper(tmp_path, marker="one"))
    activate(service, first)
    second_source = write_helper(tmp_path, name="helper-v2.py", marker="two")
    second = propose(service, second_source)
    activate(service, second)

    assert service.store.get_durable_helper_bundle(first["helper_id"])["status"] == (
        "deprecated"
    )
    quarantined = service.transition(
        helper_id=second["helper_id"],
        target_status="quarantined",
        operator_agent_id="operator-0",
    )
    assert quarantined["status"] == "quarantined"
    materialized = tmp_path / "registry" / "materialized" / second["helper_id"]
    assert not materialized.exists()
    metrics = service.measurements()
    assert metrics["replacements"] == 1
    assert metrics["quarantines"] == 1
    assert "one" not in json.dumps(metrics)
    assert "two" not in json.dumps(metrics)


def test_export_import_is_proposed_idempotent_and_digest_bound(tmp_path: Path) -> None:
    source = registry(tmp_path / "source")
    helper = propose(source, write_helper(tmp_path / "source"))
    activate(source, helper)
    exported = source.export_bundle(helper["helper_id"])

    target = registry(tmp_path / "target", measurements=False)
    imported = target.import_bundle(bundle=exported, operator_agent_id="operator-0")
    assert imported["status"] == "proposed"
    assert imported["object_sha256"] == helper["object_sha256"]
    assert target.import_bundle(
        bundle=exported,
        operator_agent_id="operator-0",
    )["idempotent"] is True

    drifted = json.loads(json.dumps(exported))
    drifted["content_base64"] = "cGFzcwo="
    with pytest.raises(DurableHelperError, match="digest mismatch|content drift"):
        target.import_bundle(bundle=drifted, operator_agent_id="operator-0")


def test_registry_is_default_disabled(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    service = DurableHelperRegistry(
        store,
        root=tmp_path / "registry",
        approved_source_roots=[tmp_path],
    )
    with pytest.raises(DurableHelperError, match="disabled"):
        service.list()
    assert not (tmp_path / "registry").exists()


def test_current_python_toolcache_mode_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    python = tmp_path / "python"
    python.write_bytes(b"hosted-toolcache-python")
    python.chmod(0o775)
    monkeypatch.setattr("agent_pbx.durable_helpers.sys.executable", str(python))

    service = registry(tmp_path / "current")
    resolved, identity = service._verified_python_binary()

    assert resolved == python.resolve()
    assert identity.st_ino == os.stat(python).st_ino


def test_separately_configured_shared_writable_python_is_rejected(
    tmp_path: Path,
) -> None:
    python = tmp_path / "custom-python"
    python.write_bytes(b"custom-python")
    python.chmod(0o775)
    service = registry(tmp_path / "custom")
    service.python_bin = python

    with pytest.raises(DurableHelperError, match="identity is unsafe"):
        service._verified_python_binary()


def test_api_is_default_disabled_and_root_operator_controlled(tmp_path: Path) -> None:
    disabled = TestClient(create_app(ServerConfig(db_path=tmp_path / "disabled.sqlite")))
    disabled_register = disabled.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "operator_role": "root"},
        },
    )
    assert disabled_register.status_code == 200
    assert disabled.get(
        "/v1/operator/helpers",
        params={"operator_agent_id": "operator-0"},
    ).status_code == 409

    source_root = tmp_path / "proposals"
    source_root.mkdir(mode=0o700)
    source = source_root / "helper.py"
    source.write_text("VALUE = 'api'\n", encoding="utf-8")
    source.chmod(0o600)
    client = TestClient(
        create_app(
            ServerConfig(
                db_path=tmp_path / "enabled.sqlite",
                durable_helpers_enabled=True,
                durable_helper_root=tmp_path / "helper-state",
                durable_helper_proposal_roots=(source_root,),
            )
        )
    )
    root = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {"pbx_mode": "report", "operator_role": "root"},
        },
    )
    assert root.status_code == 200
    fork = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "operator-0-fork-demo",
            "project": "agent-pbx-operator",
            "agent_type": "operator",
            "metadata": {
                "pbx_mode": "report",
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
            },
        },
    )
    assert fork.status_code == 200

    denied = client.post(
        "/v1/operator/helpers/propose",
        json={
            "operator_agent_id": "operator-0-fork-demo",
            "source_path": str(source),
            "name": "api-demo",
            "scopes": ["workspace.read"],
            "kb_refs": [],
        },
    )
    assert denied.status_code == 400

    proposed = client.post(
        "/v1/operator/helpers/propose",
        json={
            "operator_agent_id": "operator-0",
            "source_path": str(source),
            "name": "api-demo",
            "scopes": ["workspace.read"],
            "kb_refs": [],
        },
    )
    assert proposed.status_code == 200
    helper_id = proposed.json()["helper_id"]
    validated = client.post(
        f"/v1/operator/helpers/{helper_id}/validate",
        json={"operator_agent_id": "operator-0"},
    )
    assert validated.status_code == 200
    assert validated.json()["passed"] is True
    reviewed = client.post(
        f"/v1/operator/helpers/{helper_id}/transition",
        json={"operator_agent_id": "operator-0", "target_status": "reviewed"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"
