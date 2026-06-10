from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.pull_requests import (
    PullRequestConfig,
    PullRequestService,
)
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


def fake_pr_view_payload(number: int = 7) -> dict[str, Any]:
    return {
        "number": number,
        "title": "Improve PR support",
        "state": "OPEN",
        "isDraft": False,
        "author": {"login": "dev"},
        "headRefName": "feature/prs",
        "baseRefName": "dev",
        "updatedAt": "2026-06-09T12:00:00Z",
        "createdAt": "2026-06-09T11:00:00Z",
        "url": f"https://github.com/owner/repo/pull/{number}",
        "body": "Adds PR workflow.",
        "labels": [{"name": "enhancement"}],
        "reviewDecision": "REVIEW_REQUIRED",
        "mergeStateStatus": "CLEAN",
        "mergeable": "MERGEABLE",
        "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}],
        "files": [{"path": "src/agent_pbx/pull_requests.py", "additions": 10}],
        "commits": [{"oid": "abc123"}],
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
    elif argv[1:3] == ["pr", "list"]:
        payload = [fake_pr_view_payload()]
    elif argv[1:3] == ["pr", "view"]:
        payload = fake_pr_view_payload(int(argv[3]))
    elif argv[1:3] == ["pr", "merge"]:
        return subprocess.CompletedProcess(argv, 0, "Merged pull request\n", "")
    else:
        return subprocess.CompletedProcess(argv, 1, "", "unknown command")
    return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")


def make_service(tmp_path: Path, *, merge_enabled: bool = False) -> PullRequestService:
    gh = make_executable(tmp_path / "gh")
    return PullRequestService(
        PullRequestConfig(
            enabled=True,
            merge_enabled=merge_enabled,
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


def test_pull_request_service_lists_prs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    service = make_service(tmp_path)

    result = service.list_for_agent(
        {"agent_id": "agent-1", "project": "repo", "metadata": {"cwd": str(repo)}}
    )

    assert result["available"] is True
    assert result["repo"] == "owner/repo"
    assert result["pull_requests"][0]["number"] == 7
    assert result["pull_requests"][0]["checks"]["success"] == 1


def test_pull_request_api_queues_review_request(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.pull_requests = make_service(tmp_path)
    client = TestClient(app)
    register_agent(client, repo)

    listed = client.get("/v1/agents/agent-1/pull-requests")
    requested = client.post("/v1/agents/agent-1/pull-requests/7/review-request")
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")

    assert listed.status_code == 200
    assert listed.json()["pull_requests"][0]["number"] == 7
    assert requested.status_code == 200
    assert requested.json()["command"]["type"] == "send_input"
    command = polled.json()[0]
    assert command["payload"]["source"] == "pull_request_review"
    assert "Review GitHub PR #7" in command["payload"]["message"]


def test_pull_request_api_generates_review_prompt_without_queue(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.pull_requests = make_service(tmp_path)
    client = TestClient(app)
    register_agent(client, repo)

    requested = client.post(
        "/v1/agents/agent-1/pull-requests/7/review-request",
        json={"queue": False},
    )
    polled = client.get("/v1/agents/agent-1/commands?wait_seconds=0")

    assert requested.status_code == 200
    assert requested.json()["command"] is None
    assert "Review GitHub PR #7" in requested.json()["prompt"]
    assert polled.json() == []


def test_pull_request_merge_is_disabled_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite"))
    app.state.pull_requests = make_service(tmp_path, merge_enabled=False)
    client = TestClient(app)
    register_agent(client, repo)

    response = client.post(
        "/v1/agents/agent-1/pull-requests/7/merge",
        json={"method": "squash", "confirm": "merge PR #7"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "PR_MERGE_DISABLED"


@pytest.mark.asyncio
async def test_mcp_pr_context_is_read_only(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    mcp = build_mcp_server(store, pull_requests=make_service(tmp_path))

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
            "pbx_pr_context",
            {"agent_id": "agent-1", "pr_number": 7},
        )
    )

    assert context["number"] == 7
    assert context["repo"] == "owner/repo"
    assert context["files"][0]["path"] == "src/agent_pbx/pull_requests.py"
    with pytest.raises(Exception):
        await mcp.call_tool(
            "pbx_pr_merge",
            {"agent_id": "agent-1", "pr_number": 7},
        )
