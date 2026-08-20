from pathlib import Path

from agent_pbx.schemas import AgentRegisterRequest
from agent_pbx.store import Store


def test_register_agent_merges_metadata_without_dropping_cwd(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            name="Agent One",
            metadata={"cwd": str(tmp_path), "task": "initial"},
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"pbx_mode": "report", "task": "updated"},
        )
    )

    assert agent["name"] == "Agent One"
    assert agent["metadata"] == {
        "cwd": str(tmp_path),
        "task": "updated",
        "pbx_mode": "report",
    }


def test_register_agent_empty_cwd_does_not_clear_existing_cwd(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": str(tmp_path)},
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"cwd": "", "pbx_mode": "nohup"},
        )
    )

    assert agent["metadata"]["cwd"] == str(tmp_path)
    assert agent["metadata"]["pbx_mode"] == "nohup"


def test_register_agent_backfills_cwd_from_absolute_repo_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"repo": str(repo), "task": "review"},
        )
    )

    assert agent["metadata"]["cwd"] == str(repo)


def test_register_agent_preserves_operator_identity_on_default_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
            },
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="k1s",
            name="Codex k1s Stage 1 fork",
            metadata={"pbx_mode": "report", "cwd": str(tmp_path / "k1s")},
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "agent-pbx-operator"
    assert agent["name"] == "operator-0"
    assert agent["metadata"]["agent_type"] == "operator"
    assert agent["metadata"]["operator_role"] == "root"
    assert agent["metadata"]["cwd"] == str(tmp_path / "agent-pbx")
    assert agent["metadata"]["pbx_mode"] == "report"


def test_register_agent_canonicalizes_root_operator_project_on_bad_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
                "tmux_pane_id": "%1",
                "launched_by": "agent-pbx-tui",
            },
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="k1s-workerbee-private",
            name="Codex k1s Stage 1 fork",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "k1s-workerbee-private"),
                "tmux_pane_id": "%99",
                "pbx_mode": "report",
            },
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "agent-pbx-operator"
    assert agent["name"] == "operator-0"
    assert agent["metadata"]["agent_type"] == "operator"
    assert agent["metadata"]["operator_role"] == "root"
    assert agent["metadata"]["cwd"] == str(tmp_path / "agent-pbx")
    assert agent["metadata"]["tmux_pane_id"] == "%1"
    assert agent["metadata"]["pbx_mode"] == "report"


def test_register_agent_allows_explicit_operator_demote(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            name="operator-0",
            agent_type="operator",
            metadata={"agent_type": "operator", "operator_role": "root"},
        )
    )
    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="k1s",
            name="Codex k1s Stage 1 fork",
            metadata={"agent_type": "caller", "cwd": str(tmp_path / "k1s")},
        )
    )

    assert agent["agent_type"] == "caller"
    assert agent["project"] == "k1s"
    assert agent["name"] == "Codex k1s Stage 1 fork"
    assert agent["metadata"]["agent_type"] == "caller"


def test_register_agent_preserves_operator_fork_identity_on_bad_refresh(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={"agent_type": "operator", "operator_role": "root"},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="caller-1",
            project="demo",
            metadata={"cwd": str(tmp_path / "demo"), "codex_session_id": "session-1"},
        )
    )
    store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0-fork-caller-1",
            project="demo",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "fork",
                "logical_operator_id": "operator-0",
                "source_caller_agent_id": "caller-1",
                "source_codex_session_id": "session-1",
                "cwd": str(tmp_path / "demo"),
                "tmux_pane_id": "%42",
            },
        )
    )
    store.create_operator_fork(
        logical_operator_agent_id="operator-0",
        fork_agent_id="operator-0-fork-caller-1",
        source_caller_agent_id="caller-1",
        source_codex_session_id="session-1",
        cwd=str(tmp_path / "demo"),
        tmux_pane_id="%42",
        status="running",
        metadata={"operator_fork_pending": False},
    )

    agent = store.register_agent(
        AgentRegisterRequest(
            agent_id="operator-0-fork-caller-1",
            project="agent-pbx-operator",
            agent_type="operator",
            metadata={
                "agent_type": "operator",
                "operator_role": "root",
                "cwd": str(tmp_path / "agent-pbx"),
                "tmux_pane_id": "%1",
                "pbx_mode": "report",
            },
        )
    )

    assert agent["agent_type"] == "operator"
    assert agent["project"] == "demo"
    assert agent["metadata"]["agent_type"] == "operator"
    assert agent["metadata"]["operator_role"] == "fork"
    assert agent["metadata"]["logical_operator_id"] == "operator-0"
    assert agent["metadata"]["source_caller_agent_id"] == "caller-1"
    assert agent["metadata"]["source_codex_session_id"] == "session-1"
    assert agent["metadata"]["cwd"] == str(tmp_path / "demo")
    assert agent["metadata"]["tmux_pane_id"] == "%42"
    assert agent["metadata"]["pbx_mode"] == "report"
