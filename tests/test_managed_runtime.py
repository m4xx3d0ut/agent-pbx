from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.codex_cli import CodexModelOption
from agent_pbx.config import ServerConfig
from agent_pbx.managed_runtime import ManagedRuntimeService
from agent_pbx.runtime_tmux import RuntimeTmuxPane
from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def model(slug: str, *levels: str) -> CodexModelOption:
    return CodexModelOption(
        slug=slug,
        display_name=slug,
        default_reasoning_level=levels[0],
        supported_reasoning_levels=levels,
        service_tiers=(),
        default_service_tier="",
        visibility="list",
        shell_type="responses",
        supported_in_api=True,
        supports_verbosity=True,
    )


def init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def service(store: Store, root: Path) -> ManagedRuntimeService:
    return ManagedRuntimeService(
        store,
        project_roots=(root,),
        codex_bin="/usr/bin/codex-test",
        tmux_bin="tmux-test",
        catalog_loader=lambda _command: (model("gpt-5.6-sol", "xhigh", "max"),),
    )


def test_project_discovery_and_launch_preview_are_git_scoped(tmp_path: Path) -> None:
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    (root / "plain").mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    runtime = service(store, root)

    projects = runtime.discover_projects()
    assert [item["path"] for item in projects] == [str(repo)]
    preview = runtime.preview_launch(project_path=str(repo))
    assert preview["agent_id"] == "codex-demo"
    assert preview["model"] == "gpt-5.6-sol"
    assert preview["runtime_server"]["effective_mode"] == "dedicated"
    assert preview["command"][0] == "/usr/bin/codex-test"

    with pytest.raises(ValueError, match="outside approved roots"):
        runtime.preview_launch(project_path=str(init_repo(tmp_path / "other")))


def test_project_discovery_marks_active_owner_and_refuses_duplicate(tmp_path: Path) -> None:
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="existing",
            project="demo",
            metadata={"cwd": str(repo)},
        )
    )
    runtime = service(store, root)
    project = runtime.discover_projects()[0]
    assert project["owned_by"] == ["existing"]
    assert project["available"] is False
    with pytest.raises(ValueError, match="already owned"):
        runtime.preview_launch(project_path=str(repo), agent_id="new")


def test_managed_launch_atomically_registers_agent_and_mapping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    runtime = service(store, root)
    commands: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[0] == "tmux-test":
            commands.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "%17\n", "")
        return real_run(argv, **kwargs)

    pane = RuntimeTmuxPane(
        session_name="agent-pbx-codex-demo-12345678",
        window_id="@1",
        window_name="codex",
        pane_id="%17",
        pane_pid=123,
        cwd=str(repo),
        current_command="codex",
        title="demo",
    )
    monkeypatch.setattr("agent_pbx.managed_runtime.subprocess.run", fake_run)
    monkeypatch.setattr("agent_pbx.managed_runtime.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr("agent_pbx.managed_runtime.list_runtime_panes", lambda _identity: (pane,))
    monkeypatch.setattr("agent_pbx.managed_runtime.process_start_ticks", lambda _pid: 99)
    monkeypatch.setattr(runtime, "_runtime_session_name", lambda _agent_id: pane.session_name)

    result = runtime.launch(project_path=str(repo), token="secret")

    assert result["launched"] is True
    assert store.get_agent("codex-demo")["metadata"]["tmux_pane_id"] == "%17"  # type: ignore[index]
    assert store.get_tmux_runtime_mapping("codex-demo")["process_start_ticks"] == 99  # type: ignore[index]
    assert any("AGENT_PBX_AGENT_ID=codex-demo" in item for item in commands[0])


def test_managed_launch_failure_kills_session_without_orphan_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    runtime = service(store, root)
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[0] == "tmux-test":
            calls.append(list(argv))
            return subprocess.CompletedProcess(argv, 0, "%17\n", "")
        return real_run(argv, **kwargs)

    monkeypatch.setattr("agent_pbx.managed_runtime.subprocess.run", fake_run)
    monkeypatch.setattr("agent_pbx.managed_runtime.validate_tmux_socket", lambda _path: (True, ""))
    monkeypatch.setattr("agent_pbx.managed_runtime.list_runtime_panes", lambda _identity: ())

    with pytest.raises(RuntimeError, match="not visible"):
        runtime.launch(project_path=str(repo))

    assert store.get_agent("codex-demo") is None
    assert any("kill-session" in call for call in calls)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")
def test_managed_launch_on_real_dedicated_tmux_server(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    fake_codex = tmp_path / "codex-test"
    fake_codex.write_text("#!/bin/sh\nwhile :; do sleep 1; done\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    runtime = ManagedRuntimeService(
        store,
        project_roots=(root,),
        codex_bin=str(fake_codex),
        tmux_bin=shutil.which("tmux") or "tmux",
        catalog_loader=lambda _command: (model("gpt-5.6-sol", "xhigh", "max"),),
    )

    result = runtime.launch(project_path=str(repo))
    mapping = result["runtime_mapping"]
    try:
        assert mapping["state"] == "ready"
        assert mapping["socket_path"].endswith("agent-pbx/runtime-tmux.sock")
        assert mapping["pane_pid"]
        assert store.get_agent("codex-demo") is not None
    finally:
        subprocess.run(
            [
                shutil.which("tmux") or "tmux",
                "-S",
                mapping["socket_path"],
                "kill-session",
                "-t",
                mapping["session_name"],
            ],
            capture_output=True,
        )


def test_runtime_migration_snapshot_results_and_rollback(tmp_path: Path) -> None:
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-a",
            project="demo",
            metadata={"cwd": str(repo), "codex_session_id": "thread-a", "model": "old"},
        )
    )
    store.set_agent_starred("agent-a", starred=True)
    store.upsert_tmux_runtime_mapping(
        entity_id="agent-a",
        server_mode="dedicated",
        server_id="server-a",
        socket_path="/tmp/server-a.sock",
        session_name="runtime-a",
        pane_id="%1",
        cwd=str(repo),
        state="ready",
    )
    runtime = service(store, root)

    preview = runtime.preview_migration(target_cli_version="0.200.0")
    assert preview["eligible_count"] == 1
    batch_id = preview["batch_id"]
    recorded = runtime.record_migration_results(
        batch_id,
        [{"agent_id": "agent-a", "status": "complete", "pane_id": "%1"}],
    )
    assert recorded["status"] == "complete"

    changed = store.get_agent("agent-a")
    assert changed is not None
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-a",
            project="demo",
            metadata={**changed["metadata"], "model": "new"},
        )
    )
    store.upsert_tmux_runtime_mapping(
        entity_id="agent-a",
        server_mode="dedicated",
        server_id="server-a",
        socket_path="/tmp/server-a.sock",
        session_name="runtime-a",
        pane_id="%9",
        cwd=str(repo),
        state="ready",
    )
    rolled_back = runtime.rollback_migration(batch_id)
    assert rolled_back["restored"] == [{"agent_id": "agent-a", "status": "restored"}]
    assert store.get_agent("agent-a")["metadata"]["model"] == "old"  # type: ignore[index]
    assert store.get_tmux_runtime_mapping("agent-a")["pane_id"] == "%1"  # type: ignore[index]


def test_managed_runtime_api_discovers_projects_and_persists_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "git"
    repo = init_repo(root / "demo")
    monkeypatch.setenv("AGENT_PBX_PROJECT_ROOTS", str(root))
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite", token="secret"))
    app.state.managed_runtime.catalog_loader = lambda _command: (
        model("gpt-5.6-sol", "xhigh", "max"),
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret"}

    discovered = client.get("/v2/projects/discover", headers=headers)
    assert discovered.status_code == 200
    assert discovered.json()["projects"][0]["path"] == str(repo)
    preview = client.post(
        "/v2/agents/managed-launch/preview",
        headers=headers,
        json={"project_path": str(repo)},
    )
    assert preview.status_code == 200
    assert preview.json()["agent_id"] == "codex-demo"

    register = client.post(
        "/v1/agents/register",
        headers=headers,
        json={
            "agent_id": "agent-a",
            "project": "demo",
            "metadata": {"cwd": str(repo), "codex_session_id": "thread-a"},
        },
    )
    assert register.status_code == 200
    migration = client.post(
        "/v2/runtime-migrations/preview",
        headers=headers,
        json={"agent_ids": ["agent-a"], "target_cli_version": "0.200.0"},
    )
    assert migration.status_code == 200
    assert migration.json()["blocked_count"] == 1
    listed = client.get("/v2/runtime-migrations", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["batches"][0]["batch_id"] == migration.json()["batch_id"]
