from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.codex.profiles import (
    MANAGED_CODEX_PROFILES,
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
        model("gpt-5.6-sol", "xhigh", "max"),
        model("gpt-5.6-terra", "xhigh", "max"),
    )
    assert validate_managed_profile(MANAGED_CODEX_PROFILES["sol-xhigh"], catalog).valid
    unavailable = validate_managed_profile(
        MANAGED_CODEX_PROFILES["codex-5.5-xhigh"], catalog
    )
    assert not unavailable.valid
    assert "absent" in unavailable.errors[0]


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
            model("gpt-5.6-sol", "xhigh", "max"),
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
    assert profiles.json()["default_profile"] == "sol-xhigh"
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
    activated = client.post(
        f"/v2/codex/elevations/{lease_id}/activate",
        headers=headers,
        json={"transition": {"phase": "safe_boundary"}},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    finished = client.post(
        f"/v2/codex/elevations/{lease_id}/finish",
        headers=headers,
        json={"status": "reverted", "transition": {"verified": True}},
    )
    assert finished.status_code == 200
    assert finished.json()["transition"]["verified"] is True

