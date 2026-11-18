from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.codex.profiles import (
    MANAGED_CODEX_PROFILES,
    managed_profile_from_metadata,
    portable_codex_keymap_overrides,
    validate_managed_profile,
)
from agent_pbx.codex.skills import ManagedSkillPackService, PersonalityOverlay
from agent_pbx.codex_cli import CodexModelOption
from agent_pbx.config import ServerConfig


def model(slug: str, *levels: str) -> CodexModelOption:
    return CodexModelOption(
        slug=slug,
        display_name=slug,
        default_reasoning_level=levels[0] if levels else "",
        supported_reasoning_levels=levels,
        service_tiers=(),
        default_service_tier="",
        visibility="list",
        shell_type="responses",
        supported_in_api=True,
        supports_verbosity=True,
    )


def test_catalog_adopted_profile_recovers_from_entity_metadata() -> None:
    profile = managed_profile_from_metadata(
        {
            "codex_model_preset": "catalog:gpt-6.2-sol:low",
            "codex_model_label": "Sol 6.2 / low",
            "codex_model": "gpt-6.2-sol",
            "codex_model_reasoning_effort": "low",
            "codex_model_verbosity": "high",
            "codex_model_reasoning_summary": "detailed",
        }
    )

    assert profile is not None
    assert profile.profile_id == "catalog:gpt-6.2-sol:low"
    assert profile.model == "gpt-6.2-sol"
    assert profile.reasoning_effort == "low"
    assert profile.elevated is False


def register(client: TestClient, root: Path, *, agent_id: str = "agent-a") -> None:
    response = client.post(
        "/v1/agents/register",
        headers={"Authorization": "Bearer secret"},
        json={
            "agent_id": agent_id,
            "project": "demo",
            "metadata": {"cwd": str(root)},
        },
    )
    assert response.status_code == 200


def test_managed_profiles_validate_model_and_reasoning_catalog() -> None:
    catalog = (
        model("gpt-5.6-sol", "high", "xhigh", "max"),
        model("gpt-5.6-terra", "xhigh", "max"),
    )
    assert validate_managed_profile(MANAGED_CODEX_PROFILES["sol-high"], catalog).valid
    assert validate_managed_profile(MANAGED_CODEX_PROFILES["sol-xhigh"], catalog).valid
    unavailable = validate_managed_profile(
        MANAGED_CODEX_PROFILES["codex-5.5-xhigh"], catalog
    )
    assert not unavailable.valid
    assert "absent" in unavailable.errors[0]


def test_managed_profiles_include_portable_editor_keymap() -> None:
    overrides = MANAGED_CODEX_PROFILES["sol-high"].overrides()

    assert portable_codex_keymap_overrides() == (
        'tui.keymap.composer.submit=["enter"]',
        'tui.keymap.editor.insert_newline=["ctrl-j","shift-enter","alt-enter","ctrl-enter"]',
    )
    assert set(portable_codex_keymap_overrides()).issubset(overrides)


def test_skill_pack_apply_drift_and_rollback(tmp_path: Path) -> None:
    service = ManagedSkillPackService(
        tmp_path,
        pack_names=("agent-pbx-base", "roses-architect", "agent-role"),
    )
    assert {item.action for item in service.preview()} == {"create"}
    result = service.apply(max_estimated_tokens=5_000)
    assert result["changed"] == ["agent-pbx-base", "roses-architect", "agent-role"]
    assert service.drift() == ()
    target = tmp_path / ".codex/skills/agent-role/SKILL.md"
    target.write_text(target.read_text() + "\nlocal drift\n", encoding="utf-8")
    assert service.drift()[0].action == "update"
    rolled_back = service.rollback()
    assert set(rolled_back["removed"]) == {
        "agent-pbx-base",
        "roses-architect",
        "agent-role",
    }
    assert not target.exists()


def test_managed_skill_sources_begin_with_valid_yaml_frontmatter() -> None:
    service = ManagedSkillPackService(Path.cwd())

    for name in service.pack_names:
        body = service._source(name)
        lines = body.splitlines()
        assert lines[0] == "---"
        assert "name:" in lines[1]
        assert lines[3] == "---"
        assert "<!-- agent-pbx-managed-skill:v2 start -->" in body


def test_skill_pack_refuses_unmanaged_target_and_budget(tmp_path: Path) -> None:
    service = ManagedSkillPackService(tmp_path, pack_names=("agent-role",))
    target = tmp_path / ".codex/skills/agent-role/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("project-owned skill\n", encoding="utf-8")
    with pytest.raises(ValueError, match="refuse_unmanaged"):
        service.apply()
    target.unlink()
    with pytest.raises(ValueError, match="above the reviewed limit"):
        service.apply(max_estimated_tokens=1)


def test_personality_overlay_exposes_metadata_without_private_body(tmp_path: Path) -> None:
    path = tmp_path / "personality.local.md"
    path.write_text("private Rosie relationship text", encoding="utf-8")
    public = PersonalityOverlay.load(path).public_dict()
    assert public["active"] is True
    assert public["name"] == "Rosie-0, known simply as Rosie"
    assert "relationship" not in str(public)


def test_profiles_and_managed_skills_api(tmp_path: Path, monkeypatch) -> None:
    overlay = tmp_path / "personality.local.md"
    overlay.write_text("private overlay contents", encoding="utf-8")
    monkeypatch.setenv("AGENT_PBX_PERSONALITY_OVERLAY", str(overlay))
    monkeypatch.setattr(
        "agent_pbx.api.inspect_codex_model_catalog",
        lambda: (
            model("gpt-6.2-sol", "medium", "high", "xhigh", "max", "ultra"),
            model("gpt-6.2-astra", "high", "xhigh", "max", "ultra"),
            model("gpt-6-luna", "high", "xhigh", "max"),
            model("gpt-6.1-terra", "high", "xhigh", "max", "ultra"),
            model("gpt-5.6-sol", "high", "xhigh", "max"),
            model("gpt-5.6-terra", "xhigh", "max"),
        ),
    )
    root = tmp_path / "repo"
    root.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    register(client, root)

    profiles = client.get(
        "/v2/codex/profiles", headers={"Authorization": "Bearer secret"}
    )
    assert profiles.status_code == 200
    assert profiles.json()["default_profile"] == "sol-6.2-medium"
    assert profiles.json()["role_defaults"] == {
        "caller": "sol-6.2-medium",
        "operator": "sol-6.2-high",
        "edit_fork": "sol-6.2-high",
        "review_fork": "sol-6.2-high",
    }
    assert profiles.json()["model_policy"]["control_mode"] == "guard_elevated"
    by_id = {item["profile_id"]: item for item in profiles.json()["profiles"]}
    assert by_id["luna-6-high"]["available"] is True
    assert by_id["sol-6.2-ultra"]["execution_mode"] == "delegated"
    assert by_id["astra-6.2-xhigh"]["approval_class"] == "bounded"
    overlay_view = profiles.json()["personality"]["local_overlay"]
    assert overlay_view["active"] is True
    assert "private overlay contents" not in profiles.text

    preview = client.get(
        "/v2/agents/agent-a/skills/preview",
        headers={"Authorization": "Bearer secret"},
    )
    assert preview.status_code == 200
    assert {item["name"] for item in preview.json()["packs"]} == {
        "agent-pbx-base",
        "roses-architect",
        "agent-role",
    }
    applied = client.post(
        "/v2/agents/agent-a/skills/apply",
        headers={"Authorization": "Bearer secret"},
        json={},
    )
    assert applied.status_code == 200
    rolled_back = client.post(
        "/v2/agents/agent-a/skills/rollback",
        headers={"Authorization": "Bearer secret"},
    )
    assert rolled_back.status_code == 200


def test_elevated_model_requires_distinct_approval_and_tracks_lease(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    register(client, root)
    headers = {"Authorization": "Bearer secret"}
    requested = client.post(
        "/v2/codex/elevations",
        headers=headers,
        json={
            "agent_id": "agent-a",
            "requested_profile": "sol-max",
            "prior_profile": "sol-xhigh",
            "justification": "Difficult bounded architecture synthesis",
            "scope": "one_task",
        },
    )
    assert requested.status_code == 200
    lease_id = requested.json()["lease_id"]

    self_approval = client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=headers,
        json={"approved": True, "approved_by": "agent-a"},
    )
    assert self_approval.status_code == 409

    approved = client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=headers,
        json={
            "approved": True,
            "approved_by": "agent-pbx-tui-user",
            "duration_seconds": 600,
        },
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    repeated_approval = client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=headers,
        json={
            "approved": True,
            "approved_by": "agent-pbx-tui-user",
            "duration_seconds": 600,
        },
    )
    assert repeated_approval.status_code == 200
    assert repeated_approval.json()["lease_id"] == lease_id
    activated = client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=headers,
        json={"transition": {"phase": "safe_boundary"}},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    repeated_activation = client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=headers,
        json={"transition": {"repeated": True}},
    )
    assert repeated_activation.status_code == 200
    assert repeated_activation.json()["status"] == "active"
    assert repeated_activation.json()["transition"]["repeated"] is True
    revert = client.post(
        f"/v2/codex/elevations/{lease_id}/revert",
        headers=headers,
        json={
            "reason": "one_task_completed",
            "transition": {"safe_boundary_source": "app_server"},
        },
    )
    assert revert.status_code == 200
    assert revert.json()["status"] == "revert_pending"
    assert revert.json()["transition"]["revert_reason"] == "one_task_completed"
    finished = client.post(
        f"/v2/codex/elevations/{lease_id}/finish",
        headers=headers,
        json={"status": "reverted", "transition": {"verified": True}},
    )
    assert finished.status_code == 200
    assert finished.json()["transition"]["verified"] is True


def test_current_elevations_are_limited_by_active_model_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AGENT_PBX_CODEX_MODEL_ELEVATION_PROFILES",
        "sol-6.2-max",
    )
    root = tmp_path / "repo"
    root.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    register(client, root)
    headers = {"Authorization": "Bearer secret"}

    allowed = client.post(
        "/v2/codex/elevations",
        headers=headers,
        json={
            "agent_id": "agent-a",
            "requested_profile": "sol-6.2-max",
            "prior_profile": "sol-6.2-high",
            "justification": "Bounded difficult task",
            "scope": "one_task",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["requested_profile"] == "sol-6.2-max"

    blocked = client.post(
        "/v2/codex/elevations",
        headers=headers,
        json={
            "agent_id": "agent-a",
            "requested_profile": "astra-6.2-xhigh",
            "prior_profile": "sol-6.2-high",
            "justification": "Not allowed in the committed public matrix",
            "scope": "one_task",
        },
    )
    assert blocked.status_code == 403
    assert "active Agent PBX model policy" in blocked.json()["detail"]


def test_elevated_model_request_reuses_open_lease(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    register(client, root)
    headers = {"Authorization": "Bearer secret"}
    request = {
        "agent_id": "agent-a",
        "requested_profile": "sol-max",
        "prior_profile": "sol-xhigh",
        "justification": "Difficult bounded architecture synthesis",
        "scope": "one_task",
    }

    first = client.post("/v2/codex/elevations", headers=headers, json=request)
    second = client.post("/v2/codex/elevations", headers=headers, json=request)
    listed = client.get(
        "/v2/codex/elevations",
        headers=headers,
        params={"agent_id": "agent-a"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["reused"] is False
    assert second.json()["reused"] is True
    assert second.json()["lease_id"] == first.json()["lease_id"]
    assert len(listed.json()) == 1


def test_active_elevation_expiry_requests_safe_revert(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    client = TestClient(app)
    register(client, root)
    headers = {"Authorization": "Bearer secret"}
    requested = client.post(
        "/v2/codex/elevations",
        headers=headers,
        json={
            "agent_id": "agent-a",
            "requested_profile": "sol-max",
            "prior_profile": "sol-xhigh",
            "justification": "Difficult bounded architecture synthesis",
            "scope": "one_task",
        },
    ).json()
    lease_id = requested["lease_id"]
    client.post(
        f"/v2/codex/elevations/{lease_id}/decision",
        headers=headers,
        json={
            "approved": True,
            "approved_by": "agent-pbx-tui-user",
            "duration_seconds": 60,
        },
    )
    activated = client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=headers,
        json={"transition": {"phase": "runtime_associated"}},
    ).json()
    store = app.state.store

    changed = store.expire_model_elevations(
        current_time=float(activated["expires_at"]) + 1.0
    )
    lease = store.get_model_elevation(lease_id)

    assert changed == 1
    assert lease is not None
    assert lease["status"] == "revert_pending"
    assert lease["finished_at"] is None
    assert lease["transition"]["phase"] == "runtime_associated"
    assert lease["transition"]["revert_reason"] == "lease_expired"
