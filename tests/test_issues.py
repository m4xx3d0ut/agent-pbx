from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.issues import IssueConfig, IssueService
from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.store import Store


def make_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def tool_json(result: object) -> object:
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        return json.loads(content[0].text)
    return json.loads(result[0].text)  # type: ignore[index, attr-defined]


def fake_issue_payload(number: int = 9) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Fix issue workflow",
        "state": "OPEN",
        "author": {"login": "reporter"},
        "url": f"https://github.com/owner/repo/issues/{number}",
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "dev"}],
        "milestone": {"title": "v1"},
        "updatedAt": "2026-06-10T12:00:00Z",
        "createdAt": "2026-06-10T11:00:00Z",
        "closed": False,
        "closedAt": None,
        "body": "Something needs mitigation.",
        "comments": [
            {
                "author": {"login": "reviewer"},
                "body": "Confirmed.",
                "createdAt": "2026-06-10T12:30:00Z",
                "updatedAt": "2026-06-10T12:30:00Z",
                "url": "https://github.com/owner/repo/issues/9#issuecomment-1",
            }
        ],
    }


def fake_runner(
    argv: list[str],
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    if argv[1:4] == ["repo", "view", "--json"]:
        payload: Any = {
            "nameWithOwner": "owner/repo",
            "url": "https://github.com/owner/repo",
        }
    elif argv[1:3] == ["issue", "list"]:
        payload = [fake_issue_payload()]
    elif argv[1:3] == ["issue", "view"]:
        payload = fake_issue_payload(int(argv[3]))
    elif argv[1:3] == ["issue", "comment"]:
        return subprocess.CompletedProcess(argv, 0, "Commented on issue\n", "")
    elif argv[1:3] == ["issue", "close"]:
        return subprocess.CompletedProcess(
            argv,
            0,
            "Closed issue https://github.com/owner/repo/issues/9\n",
            "",
        )
    else:
        return subprocess.CompletedProcess(argv, 1, "", "unknown command")
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def make_service(tmp_path: Path, *, close_enabled: bool = False) -> IssueService:
    gh = make_executable(tmp_path / "gh")
    return IssueService(
        IssueConfig(
            enabled=True,
            close_enabled=close_enabled,
            gh_bin=str(gh),
            allowed_repos=("owner/repo",),
        ),
        runner=fake_runner,
    )


def register_agent(client: TestClient, repo: Path) -> None:
    response = client.post(
        "/v1/agents/register",
        json={
            "agent_id": "agent-1",
            "project": "repo",
            "metadata": {"cwd": str(repo), "pbx_mode": "report"},
        },
    )
    assert response.status_code == 200


def test_issue_service_lists_issues(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    service = make_service(tmp_path)

    result = service.list_for_agent(
        {"agent_id": "agent-1", "project": "repo", "metadata": {"cwd": str(repo)}}
    )

    assert result["available"] is True
    assert result["repo"] == "owner/repo"
    assert result["issues"][0]["number"] == 9
    assert result["issues"][0]["labels"] == ["bug"]
    assert result["issues"][0]["assignees"] == ["dev"]


def test_issue_api_queues_mitigation_request(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.issues = make_service(tmp_path)
    client = TestClient(app)
    register_agent(client, repo)

    listed = client.get("/v1/agents/agent-1/issues")
    requested = client.post("/v1/agents/agent-1/issues/9/mitigation-request")
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")

    assert listed.status_code == 200
    assert listed.json()["issues"][0]["number"] == 9
    assert requested.status_code == 200
    assert requested.json()["command"]["type"] == "send_input"
    command = polled.json()[0]
    assert command["payload"]["source"] == "issue_mitigation"
    assert "Mitigate GitHub Issue #9" in command["payload"]["message"]


def test_issue_api_generates_mitigation_prompt_without_queue(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.issues = make_service(tmp_path)
    client = TestClient(app)
    register_agent(client, repo)

    requested = client.post(
        "/v1/agents/agent-1/issues/9/mitigation-request",
        json={"queue": False},
    )
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")

    assert requested.status_code == 200
    assert requested.json()["command"] is None
    assert "Mitigate GitHub Issue #9" in requested.json()["prompt"]
    assert polled.json() == []


def test_issue_clear_is_disabled_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.issues = make_service(tmp_path, close_enabled=False)
    client = TestClient(app)
    register_agent(client, repo)

    response = client.post(
        "/v1/agents/agent-1/issues/9/clear",
        json={"comment": "Fixed and verified.", "confirm": "clear issue #9"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ISSUE_CLOSE_DISABLED"


def test_issue_clear_requires_confirmation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.issues = make_service(tmp_path, close_enabled=True)
    client = TestClient(app)
    register_agent(client, repo)

    response = client.post(
        "/v1/agents/agent-1/issues/9/clear",
        json={"comment": "Fixed and verified.", "confirm": "close it"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ISSUE_CLEAR_CONFIRMATION_REQUIRED"


def test_issue_clear_comments_then_closes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.issues = make_service(tmp_path, close_enabled=True)
    client = TestClient(app)
    register_agent(client, repo)

    response = client.post(
        "/v1/agents/agent-1/issues/9/clear",
        json={"comment": "Fixed and verified.", "confirm": "clear issue #9"},
    )

    assert response.status_code == 200
    assert response.json()["closed"] is True
    assert response.json()["repo"] == "owner/repo"


@pytest.mark.asyncio
async def test_mcp_issue_context_is_read_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store, issues=make_service(tmp_path))

    await mcp.call_tool(
        "pbx_register_agent",
        {
            "agent_id": "agent-1",
            "project": "repo",
            "metadata": {"cwd": str(repo)},
        },
    )
    context = tool_json(
        await mcp.call_tool(
            "pbx_issue_context",
            {"agent_id": "agent-1", "issue_number": 9},
        )
    )

    assert context["number"] == 9
    assert context["repo"] == "owner/repo"
    assert context["comments"][0]["body"] == "Confirmed."
    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_issue_clear",
            {"agent_id": "agent-1", "issue_number": 9},
        )
