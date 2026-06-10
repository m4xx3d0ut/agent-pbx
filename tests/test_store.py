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
