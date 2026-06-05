from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app
from agent_pbx.config import ServerConfig
from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.joplin import JoplinConfig, JoplinService
from agent_pbx.schemas import AgentRegisterRequest, CommandCreateRequest, ReportCreateRequest
from agent_pbx.store import Store


class FakeJoplinApi:
    def __init__(self) -> None:
        self.folders: dict[str, dict[str, object]] = {}
        self.notes: dict[str, dict[str, object]] = {}
        self.next_folder = 1
        self.next_note = 1

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        assert query.get("token") == ["secret"]
        path = request.url.path
        if path == "/folders" and request.method == "GET":
            return self.json({"items": list(self.folders.values()), "has_more": False})
        if path == "/folders" and request.method == "POST":
            payload = json.loads(request.content.decode())
            folder_id = f"folder-{self.next_folder}"
            self.next_folder += 1
            folder = {
                "id": folder_id,
                "title": payload["title"],
                "parent_id": payload.get("parent_id", ""),
            }
            self.folders[folder_id] = folder
            return self.json(folder)
        if path == "/notes" and request.method == "GET":
            parent_id = query.get("parent_id", [""])[0]
            notes = [
                note
                for note in self.notes.values()
                if str(note.get("parent_id") or "") == parent_id
            ]
            return self.json({"items": notes, "has_more": False})
        if path == "/notes" and request.method == "POST":
            payload = json.loads(request.content.decode())
            note_id = f"note-{self.next_note}"
            self.next_note += 1
            note = {
                "id": note_id,
                "parent_id": payload["parent_id"],
                "title": payload["title"],
                "body": payload["body"],
                "created_time": 1_700_000_000_000 + self.next_note,
                "updated_time": 1_700_000_000_000 + self.next_note,
            }
            self.notes[note_id] = note
            return self.json(note)
        if path.startswith("/notes/"):
            note_id = path.rsplit("/", 1)[-1]
            if note_id not in self.notes:
                return self.json({"error": "missing"}, status_code=404)
            if request.method == "GET":
                return self.json(self.notes[note_id])
            if request.method == "PUT":
                payload = json.loads(request.content.decode())
                self.notes[note_id].update(payload)
                self.notes[note_id]["updated_time"] = 1_700_000_010_000
                return self.json(self.notes[note_id])
        return self.json({"error": path}, status_code=404)

    @staticmethod
    def json(payload: object, *, status_code: int = 200) -> httpx.Response:
        return httpx.Response(status_code, json=payload)


def make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    store.register_agent(
        AgentRegisterRequest(
            agent_id="agent-1",
            project="demo",
            metadata={"session_id": "session-1"},
        )
    )
    return store


def make_service(fake: FakeJoplinApi) -> JoplinService:
    return JoplinService(
        JoplinConfig(api_url="http://joplin.local", token="secret"),
        client=fake.client(),
        clock=lambda: 123.0,
    )


class FakeJoplinService:
    def __init__(self, _config: object | None = None) -> None:
        self.config = SimpleNamespace(configured=True)
        self.created: list[dict[str, object]] = []
        self.command_logs: list[str] = []
        self.report_logs: list[str] = []
        self.notes: dict[str, dict[str, object]] = {}
        self.active_log: dict[str, object] | None = None

    def status(self) -> dict[str, object]:
        return {
            "configured": True,
            "available": True,
            "api_url": "http://joplin.local",
            "notebook": "Agent PBX",
            "checked_at": 123.0,
            "error": None,
        }

    def list_notes_for_agent(self, _agent: dict[str, object]) -> list[dict[str, object]]:
        return [
            {
                "id": note["id"],
                "parent_id": note["parent_id"],
                "title": note["title"],
                "created_time": note["created_time"],
                "updated_time": note["updated_time"],
            }
            for note in self.notes.values()
        ]

    def get_note_for_agent(
        self, _agent: dict[str, object], note_id: str
    ) -> dict[str, object]:
        return self.notes[note_id]

    def update_note_for_agent(
        self,
        _agent: dict[str, object],
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, object]:
        if title is not None:
            self.notes[note_id]["title"] = title
        if body is not None:
            self.notes[note_id]["body"] = body
        return self.notes[note_id]

    def create_note_for_agent(
        self,
        _agent: dict[str, object],
        *,
        event_type: str,
        body: str,
        title: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, object]:
        note = {
            "id": f"note-{len(self.notes) + 1}",
            "parent_id": "folder-agent",
            "title": title or f"{session_id or 'session'}-{event_type}",
            "body": body,
            "created_time": 1.0,
            "updated_time": 1.0,
        }
        self.notes[str(note["id"])] = note
        self.created.append(note)
        return note

    def create_document(self, **kwargs: object) -> dict[str, object]:
        return self.create_note_for_agent(
            kwargs["agent"],  # type: ignore[arg-type]
            event_type="DOC",
            title=str(kwargs["title"]),
            body=str(kwargs["body"]),
            session_id=kwargs.get("session_id"),  # type: ignore[arg-type]
        )

    def start_log(
        self, store: Store, agent: dict[str, object]
    ) -> dict[str, object]:
        note = self.create_note_for_agent(
            agent,
            event_type="LOG",
            body="log",
            title="LOG",
        )
        self.active_log = store.start_joplin_log(
            agent_id=str(agent["agent_id"]),
            project=str(agent["project"]),
            session_id="session",
            note_id=str(note["id"]),
            title=str(note["title"]),
        )
        return self.active_log

    def stop_log(self, store: Store, agent_id: str) -> dict[str, object] | None:
        return store.stop_active_joplin_log(agent_id)

    def append_command_log(self, _store: Store, command: dict[str, object]) -> None:
        self.command_logs.append(str(command["command_id"]))

    def append_report_log(self, _store: Store, report: dict[str, object]) -> None:
        self.report_logs.append(str(report["report_id"]))


def test_joplin_service_creates_scoped_notebooks_and_notes() -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    agent = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {"session_id": "session-1"},
    }

    status = service.status()
    note = service.create_note_for_agent(
        agent,
        event_type="COPY",
        title="Copy",
        body="Body",
    )
    notes = service.list_notes_for_agent(agent)
    fetched = service.get_note_for_agent(agent, note["id"])
    updated = service.update_note_for_agent(agent, note["id"], body="Updated")

    assert status["available"] is True
    assert [folder["title"] for folder in fake.folders.values()] == [
        "Agent PBX",
        "demo",
        "agent-1",
    ]
    assert note["title"] == "Copy"
    assert notes[0]["id"] == note["id"]
    assert fetched["body"] == "Body"
    assert updated["body"] == "Updated"


def test_joplin_log_appends_operator_prompt_and_terminal_report(tmp_path: Path) -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    store = make_store(tmp_path)
    agent = store.get_agent("agent-1")
    assert agent is not None

    log = service.start_log(store, agent)
    command = store.create_command(
        CommandCreateRequest(
            agent_id="agent-1",
            type="send_input",
            payload={"message": "Please proceed"},
        )
    )
    service.append_command_log(store, command)
    report = store.create_report(
        "agent-1",
        ReportCreateRequest(
            project="demo",
            status="done",
            summary="Done",
            detail="Finished the task.",
        ),
    )
    service.append_report_log(store, report)
    note = service.get_note_for_agent(agent, log["note_id"])
    stopped = service.stop_log(store, "agent-1")

    assert "Operator Prompt" in note["body"]
    assert "Please proceed" in note["body"]
    assert "Agent Response" in note["body"]
    assert "Finished the task." in note["body"]
    assert stopped is not None
    assert stopped["active"] is False


def test_joplin_api_status_copy_log_and_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinService()
    monkeypatch.setattr("agent_pbx.api.JoplinService", lambda _config: fake)
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={
            "agent_id": "agent-1",
            "project": "demo",
            "metadata": {"session_id": "session-1"},
        },
    )
    report = client.post(
        "/v1/agents/agent-1/reports",
        json={
            "project": "demo",
            "status": "done",
            "summary": "Finished",
            "detail": "Report detail",
        },
    ).json()

    status = client.get("/v1/joplin/status")
    copied = client.post("/v1/agents/agent-1/joplin/copy")
    notes = client.get("/v1/agents/agent-1/joplin/notes")
    note_id = copied.json()["id"]
    updated = client.put(
        f"/v1/agents/agent-1/joplin/notes/{note_id}",
        json={"body": "Edited"},
    )
    started = client.post("/v1/agents/agent-1/joplin/log/start")
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Proceed"},
        },
    ).json()
    stopped = client.post("/v1/agents/agent-1/joplin/log/stop")

    assert status.json()["available"] is True
    assert copied.status_code == 200
    assert "Report detail" in copied.json()["body"]
    assert notes.json()[0]["id"] == note_id
    assert updated.json()["body"] == "Edited"
    assert started.json()["active"] is True
    assert stopped.json()["active"] is False
    assert report["report_id"] in fake.report_logs
    assert command["command_id"] in fake.command_logs


@pytest.mark.asyncio
async def test_mcp_joplin_tools_create_document_and_log_hooks(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    fake = FakeJoplinService()
    mcp = build_mcp_server(store, joplin=fake)  # type: ignore[arg-type]

    status = tool_json(await mcp.call_tool("pbx_joplin_status", {}))
    note = tool_json(
        await mcp.call_tool(
            "pbx_joplin_create_document",
            {
                "agent_id": "agent-1",
                "project": "demo",
                "title": "Doc",
                "body": "Body",
                "mermaid_blocks": ["graph TD; A-->B;"],
            },
        )
    )
    command = tool_json(
        await mcp.call_tool(
            "pbx_queue_command",
            {
                "agent_id": "agent-1",
                "command_type": "send_input",
                "payload": {"message": "Proceed"},
            },
        )
    )

    assert status["available"] is True
    assert note["title"] == "Doc"
    assert command["command_id"] in fake.command_logs


def tool_json(result: object) -> object:
    if isinstance(result, tuple) and len(result) == 2:
        content, structured = result
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        return json.loads(content[0].text)
    return json.loads(result[0].text)  # type: ignore[index, attr-defined]
