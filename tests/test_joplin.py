from __future__ import annotations

import asyncio
import fcntl
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient
import pytest

from agent_pbx.api import create_app, run_joplin_sync_worker
from agent_pbx.config import ServerConfig
from agent_pbx.mcp_tools import build_mcp_server
from agent_pbx.joplin import (
    JoplinApiError,
    JoplinConfig,
    JoplinConflictError,
    JoplinE2EELockedError,
    JoplinEncryptedNoteError,
    JoplinGateway,
    JoplinProfileCoordinator,
    JoplinProfileInUseError,
    JoplinSyncLockError,
    JoplinScopeError,
    JoplinService,
)
from agent_pbx.schemas import AgentRegisterRequest, CommandCreateRequest, ReportCreateRequest
from agent_pbx.store import Store


class FakeJoplinApi:
    def __init__(self) -> None:
        self.folders: dict[str, dict[str, object]] = {}
        self.notes: dict[str, dict[str, object]] = {}
        self.resources: dict[str, dict[str, object]] = {}
        self.next_folder = 1
        self.next_note = 1
        self.folder_note_requests: list[str] = []

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        query = parse_qs(request.url.query.decode())
        assert query.get("token") == ["secret"]
        path = request.url.path
        if path == "/folders" and request.method == "GET":
            return self.json({"items": list(self.folders.values()), "has_more": False})
        if path == "/resources" and request.method == "GET":
            return self.json({"items": list(self.resources.values()), "has_more": False})
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
        if path.startswith("/folders/") and path.endswith("/notes"):
            folder_id = path.split("/")[2]
            self.folder_note_requests.append(folder_id)
            notes = [
                note
                for note in self.notes.values()
                if str(note.get("parent_id") or "") == folder_id
            ]
            return self.json({"items": notes, "has_more": False})
        if path == "/notes" and request.method == "GET":
            notes = list(self.notes.values())
            if "parent_id" in query:
                parent_id = query.get("parent_id", [""])[0]
                notes = [
                    note
                    for note in notes
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
                "encryption_applied": 0,
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
            if request.method == "DELETE":
                deleted = self.notes.pop(note_id)
                return self.json(deleted)
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


def test_joplin_note_revision_rejects_stale_update_and_preserves_markdown() -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    body = "# Heading 🐝\n\n```python\nprint('ok')\n```\n\n```mermaid\ngraph TD\n```"
    created = service.create_note_for_project("demo", title="Architecture", body=body)
    base = service.get_note_for_project("demo", str(created["id"]))

    fake.notes[str(created["id"])]["body"] = "External edit"
    fake.notes[str(created["id"])]["updated_time"] = 1_700_000_020_000

    with pytest.raises(JoplinConflictError) as caught:
        service.update_note_for_project(
            "demo",
            str(created["id"]),
            body=body + "\nlocal",
            base_revision=str(base["revision"]),
            base_title=str(base["title"]),
            base_body=str(base["body"]),
        )

    assert caught.value.current["body"] == "External edit"
    assert caught.value.as_error()["code"] == "JOPLIN_NOTE_CONFLICT"
    assert fake.notes[str(created["id"])]["body"] == "External edit"


def test_joplin_encrypted_note_is_reported_as_locked_and_never_exposes_ciphertext() -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    agent = {"agent_id": "agent-1", "project": "demo", "metadata": {}}
    created = service.create_note_for_agent(
        agent,
        event_type="NOTE",
        title="Remote note",
        body="plaintext before sync",
    )
    raw = fake.notes[str(created["id"])]
    raw["body"] = "JED01000022000000ciphertext"
    raw["encryption_applied"] = 1

    note = service.get_note_for_agent(agent, str(created["id"]))
    summaries = service.list_notes_for_agent(agent)

    assert note["body"] == ""
    assert note["encryption_applied"] == 1
    assert note["decryption_pending"] is True
    assert summaries[0]["decryption_pending"] is True
    assert "ciphertext" not in json.dumps(note)
    with pytest.raises(JoplinEncryptedNoteError):
        service.update_note_for_agent(agent, str(created["id"]), body="overwrite")
    with pytest.raises(JoplinEncryptedNoteError):
        service.delete_note_for_agent(agent, str(created["id"]))
    with pytest.raises(JoplinEncryptedNoteError):
        service.append_to_note(str(created["id"]), "append")
    assert fake.notes[str(created["id"])]["body"] == "JED01000022000000ciphertext"


def test_joplin_status_counts_pending_encrypted_items() -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    service.ensure_root_notebook()
    fake.notes["locked-note"] = {
        "id": "locked-note",
        "parent_id": "folder-1",
        "title": "Locked",
        "body": "ciphertext",
        "encryption_applied": 1,
    }
    fake.resources["locked-resource"] = {
        "id": "locked-resource",
        "encryption_applied": 1,
    }
    fake.folders["locked-folder"] = {
        "id": "locked-folder",
        "title": "Locked folder",
        "parent_id": "folder-1",
        "encryption_applied": 1,
    }

    status = service.status()

    assert status["available"] is True
    assert status["encryption"] == {
        "state": "locked",
        "pending_total": 3,
        "pending_notes": 1,
        "pending_resources": 1,
        "pending_folders": 1,
        "checked_at": 123.0,
    }


def test_joplin_sync_zero_exit_fails_when_e2ee_items_remain(tmp_path: Path) -> None:
    fake = FakeJoplinApi()
    fake.notes["locked-note"] = {
        "id": "locked-note",
        "parent_id": "folder",
        "title": "Locked",
        "body": "ciphertext",
        "encryption_applied": 1,
    }
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            e2ee_wait_seconds=0,
        ),
        client=fake.client(),
    )
    service.sync = lambda: None  # type: ignore[method-assign]
    gateway = JoplinGateway(make_store(tmp_path), service)

    with pytest.raises(JoplinE2EELockedError) as caught:
        gateway.run_sync_job({"sync_id": "sync-locked"})

    assert caught.value.code == "JOPLIN_E2EE_LOCKED"
    assert caught.value.encryption["pending_notes"] == 1


def test_joplin_targeted_sync_ignores_unrelated_encrypted_note(
    tmp_path: Path,
) -> None:
    fake = FakeJoplinApi()
    fake.notes["target-note"] = {
        "id": "target-note",
        "parent_id": "target-folder",
        "title": "Target",
        "body": "Saved response",
        "encryption_applied": 0,
    }
    fake.notes["unrelated-locked-note"] = {
        "id": "unrelated-locked-note",
        "parent_id": "other-folder",
        "title": "Unrelated",
        "body": "ciphertext",
        "encryption_applied": 1,
    }
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            e2ee_wait_seconds=0,
        ),
        client=fake.client(),
    )
    service.sync = lambda: None  # type: ignore[method-assign]
    gateway = JoplinGateway(make_store(tmp_path), service)

    gateway.run_sync_job(
        {
            "sync_id": "sync-target",
            "reason": "note_copy_create",
            "reasons": ["note_copy_create"],
            "note_id": "target-note",
            "note_ids": ["target-note"],
        }
    )

    assert service.encryption_status(force=True)["pending_total"] == 1


def test_joplin_targeted_sync_fails_when_target_remains_encrypted(
    tmp_path: Path,
) -> None:
    fake = FakeJoplinApi()
    fake.notes["target-note"] = {
        "id": "target-note",
        "parent_id": "target-folder",
        "title": "Target",
        "body": "ciphertext",
        "encryption_applied": 1,
    }
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            e2ee_wait_seconds=0,
        ),
        client=fake.client(),
    )
    service.sync = lambda: None  # type: ignore[method-assign]
    gateway = JoplinGateway(make_store(tmp_path), service)

    with pytest.raises(JoplinE2EELockedError) as caught:
        gateway.run_sync_job(
            {
                "sync_id": "sync-target",
                "reason": "note_update",
                "note_id": "target-note",
                "note_ids": ["target-note"],
            }
        )

    assert caught.value.encryption["scope"] == "sync_targets"
    assert caught.value.encryption["pending_note_ids"] == ["target-note"]


def test_joplin_targeted_delete_sync_accepts_missing_target(tmp_path: Path) -> None:
    fake = FakeJoplinApi()
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            e2ee_wait_seconds=0,
        ),
        client=fake.client(),
    )
    service.sync = lambda: None  # type: ignore[method-assign]
    gateway = JoplinGateway(make_store(tmp_path), service)

    gateway.run_sync_job(
        {
            "sync_id": "sync-delete",
            "reason": "note_delete",
            "note_id": "deleted-note",
            "note_ids": ["deleted-note"],
        }
    )


def test_joplin_profile_coordinator_external_mode_refuses_live_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            joplin_bin=tmp_path / "joplin",
            profile=tmp_path / "profile",
            profile_owner_mode="external",
        )
    )
    coordinator = JoplinProfileCoordinator(service)
    sync_calls: list[bool] = []
    monkeypatch.setattr(coordinator, "_validated_server_pid", lambda _profile: 123)
    monkeypatch.setattr(service, "sync", lambda: sync_calls.append(True))

    with pytest.raises(JoplinProfileInUseError) as caught:
        coordinator.run_sync()

    assert caught.value.code == "JOPLIN_PROFILE_IN_USE"
    assert sync_calls == []


def test_joplin_profile_coordinator_managed_mode_stops_syncs_and_restores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            joplin_bin=tmp_path / "joplin",
            profile=profile,
            profile_owner_mode="managed_cli",
        )
    )
    coordinator = JoplinProfileCoordinator(service)
    events: list[str] = []
    monkeypatch.setattr(coordinator, "_validated_server_pid", lambda _profile: 321)
    monkeypatch.setattr(coordinator, "_stop_server", lambda pid: events.append(f"stop:{pid}"))
    monkeypatch.setattr(coordinator, "_start_server", lambda path: events.append(f"start:{path}"))
    monkeypatch.setattr(service, "sync", lambda: events.append("sync"))

    coordinator.run_sync()

    assert events == ["stop:321", "sync", f"start:{profile}"]


def test_joplin_profile_coordinator_restores_server_after_sync_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / "profile"
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            joplin_bin=tmp_path / "joplin",
            profile=profile,
            profile_owner_mode="managed_cli",
        )
    )
    coordinator = JoplinProfileCoordinator(service)
    events: list[str] = []
    monkeypatch.setattr(coordinator, "_validated_server_pid", lambda _profile: 321)
    monkeypatch.setattr(coordinator, "_stop_server", lambda pid: events.append(f"stop:{pid}"))
    monkeypatch.setattr(coordinator, "_start_server", lambda path: events.append(f"start:{path}"))

    def fail_sync() -> None:
        events.append("sync")
        raise JoplinApiError("sync failed")

    monkeypatch.setattr(service, "sync", fail_sync)

    with pytest.raises(JoplinApiError, match="sync failed"):
        coordinator.run_sync()

    assert events == ["stop:321", "sync", f"start:{profile}"]


def test_joplin_gateway_records_two_client_conflict_and_rebases_merge(
    tmp_path: Path,
) -> None:
    fake = FakeJoplinApi()
    store = make_store(tmp_path)
    gateway = JoplinGateway(store, make_service(fake))
    agent = store.get_agent("agent-1")
    assert agent is not None
    created = gateway.create_note_for_agent(
        agent,
        event_type="NOTE",
        title="Shared",
        body="base",
    )
    note_id = str(created["id"])
    first = gateway.begin_edit_for_agent(agent, note_id, client_id="client-a")
    second = gateway.begin_edit_for_agent(agent, note_id, client_id="client-b")

    gateway.update_note_for_agent(
        agent,
        note_id,
        body="client a",
        edit_id=str(first["edit"]["edit_id"]),
    )
    with pytest.raises(JoplinConflictError) as caught:
        gateway.update_note_for_agent(
            agent,
            note_id,
            body="client b",
            edit_id=str(second["edit"]["edit_id"]),
        )

    conflict_id = caught.value.conflict_id
    assert conflict_id
    resolution = gateway.resolve_edit_conflict(
        conflict_id,
        resolution="review_merge",
        agent=agent,
    )
    assert "<<<<<<< LOCAL DRAFT" in resolution["draft"]["body"]
    assert resolution["edit"]["status"] == "editing"
    assert resolution["edit"]["base_body"] == "client a"


def test_joplin_sync_jobs_coalesce_only_redundant_work(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    first = store.enqueue_joplin_sync(
        reason="note_update",
        agent_id="agent-1",
        note_id="note-1",
        profile_key="profile-a",
    )
    second = store.enqueue_joplin_sync(
        reason="note_update",
        agent_id="agent-1",
        note_id="note-2",
        profile_key="profile-a",
    )
    manual = store.enqueue_joplin_sync(
        reason="manual",
        profile_key="profile-a",
    )

    assert second["sync_id"] == first["sync_id"]
    assert second["coalesced"] is True
    assert set(second["note_ids"]) == {"note-1", "note-2"}
    assert manual["sync_id"] != first["sync_id"]


def test_joplin_sync_profile_lock_reports_actionable_error(tmp_path: Path) -> None:
    fake = FakeJoplinApi()
    store = make_store(tmp_path)
    gateway = JoplinGateway(store, make_service(fake))
    gateway.sync_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with gateway.sync_lock_path.open("a+", encoding="utf-8") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(JoplinSyncLockError) as caught:
            gateway.run_sync_job({"sync_id": "sync-1"})
    assert caught.value.code == "JOPLIN_SYNC_LOCKED"


def test_joplin_api_two_client_conflict_has_recovery_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinApi()

    def service_factory(config: JoplinConfig) -> JoplinService:
        return JoplinService(config, client=fake.client(), clock=lambda: 123.0)

    monkeypatch.setattr("agent_pbx.api.JoplinService", service_factory)
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            joplin_api_url="http://joplin.local",
            joplin_token="secret",
        )
    )
    with TestClient(app) as client:
        client.post(
            "/v1/agents/register",
            json={"agent_id": "agent-1", "project": "demo", "metadata": {}},
        )
        created = client.post(
            "/v1/agents/agent-1/joplin/notes",
            json={"title": "Shared", "body": "base"},
        ).json()
        note_id = created["id"]
        edit_a = client.post(
            f"/v1/agents/agent-1/joplin/notes/{note_id}/edit",
            json={"client_id": "client-a"},
        ).json()
        edit_b = client.post(
            f"/v1/agents/agent-1/joplin/notes/{note_id}/edit",
            json={"client_id": "client-b"},
        ).json()
        saved = client.put(
            f"/v1/agents/agent-1/joplin/notes/{note_id}",
            json={"body": "from a", "edit_id": edit_a["edit"]["edit_id"]},
        )
        conflicted = client.put(
            f"/v1/agents/agent-1/joplin/notes/{note_id}",
            json={"body": "from b", "edit_id": edit_b["edit"]["edit_id"]},
        )
        detail = conflicted.json()["detail"]
        resolved = client.post(
            f"/v1/agents/agent-1/joplin/conflicts/{detail['conflict_id']}/resolve",
            json={"resolution": "save_conflict_copy"},
        )

    assert saved.status_code == 200
    assert conflicted.status_code == 409
    assert detail["code"] == "JOPLIN_NOTE_CONFLICT"
    assert "<<<<<<< LOCAL DRAFT" in detail["merged"]["body"]
    assert resolved.status_code == 200
    assert resolved.json()["conflict_copy"]["body"] == "from b"


def test_joplin_api_returns_locked_note_and_blocks_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinApi()

    def service_factory(config: JoplinConfig) -> JoplinService:
        return JoplinService(config, client=fake.client(), clock=lambda: 123.0)

    monkeypatch.setattr("agent_pbx.api.JoplinService", service_factory)
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            joplin_api_url="http://joplin.local",
            joplin_token="secret",
        )
    )
    with TestClient(app) as client:
        client.post(
            "/v1/agents/register",
            json={"agent_id": "agent-1", "project": "demo", "metadata": {}},
        )
        created = client.post(
            "/v1/agents/agent-1/joplin/notes",
            json={"title": "Locked", "body": "plain"},
        ).json()
        raw = fake.notes[str(created["id"])]
        raw["body"] = "JED01000022000000ciphertext"
        raw["encryption_applied"] = 1

        fetched = client.get(f"/v1/agents/agent-1/joplin/notes/{created['id']}")
        edit = client.post(
            f"/v1/agents/agent-1/joplin/notes/{created['id']}/edit",
            json={"client_id": "client-a"},
        )
        updated = client.put(
            f"/v1/agents/agent-1/joplin/notes/{created['id']}",
            json={"body": "overwrite"},
        )
        deleted = client.delete(
            f"/v1/agents/agent-1/joplin/notes/{created['id']}"
        )

    assert fetched.status_code == 200
    assert fetched.json()["body"] == ""
    assert fetched.json()["decryption_pending"] is True
    assert "ciphertext" not in fetched.text
    for response in (edit, updated, deleted):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "JOPLIN_NOTE_ENCRYPTED"
        assert "ciphertext" not in response.text


class FakeJoplinService:
    def __init__(self, _config: object | None = None) -> None:
        self.config = SimpleNamespace(configured=True)
        self.created: list[dict[str, object]] = []
        self.command_logs: list[str] = []
        self.report_logs: list[str] = []
        self.sync_calls = 0
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

    def list_notes_for_project(self, _project: str) -> list[dict[str, object]]:
        return self.list_notes_for_agent({})

    def get_note_for_agent(
        self, _agent: dict[str, object], note_id: str
    ) -> dict[str, object]:
        return self.notes[note_id]

    def get_note_for_project(
        self, _project: str, note_id: str
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

    def update_note_for_project(
        self,
        _project: str,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, object]:
        return self.update_note_for_agent(
            {},
            note_id,
            title=title,
            body=body,
        )

    def delete_note_for_agent(
        self,
        _agent: dict[str, object],
        note_id: str,
    ) -> dict[str, object]:
        return self.notes.pop(note_id)

    def delete_note_for_project(
        self,
        _project: str,
        note_id: str,
    ) -> dict[str, object]:
        return self.notes.pop(note_id)

    def create_note_for_project(
        self,
        _project: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, object]:
        note = {
            "id": f"note-{len(self.notes) + 1}",
            "parent_id": "folder-project",
            "title": title,
            "body": body,
            "created_time": 1.0,
            "updated_time": 1.0,
        }
        self.notes[str(note["id"])] = note
        self.created.append(note)
        return note

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

    def append_log_section(
        self,
        store: Store,
        agent_id: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, object] | None:
        active = store.get_active_joplin_log(agent_id)
        if active is None:
            return None
        note = self.notes[str(active["note_id"])]
        note["body"] = f"{note['body']}\n\n## {title}\n\n{body}"
        store.touch_joplin_log(str(active["log_id"]))
        return store.get_joplin_log(str(active["log_id"]))

    def sync(self) -> None:
        self.sync_calls += 1


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
    deleted = service.delete_note_for_agent(agent, note["id"])

    assert status["available"] is True
    assert [folder["title"] for folder in fake.folders.values()] == [
        "Agent PBX",
        "demo",
        "agent-1",
    ]
    assert note["title"] == "Copy"
    assert notes[0]["id"] == note["id"]
    assert fake.folder_note_requests == [str(note["parent_id"])]
    assert fetched["body"] == "Body"
    assert updated["body"] == "Updated"
    assert deleted["id"] == note["id"]
    assert note["id"] not in fake.notes


def test_joplin_service_sync_on_write_runs_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinApi()
    profile = tmp_path / "joplin-profile"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="Done", stderr="")

    monkeypatch.setattr("agent_pbx.joplin.subprocess.run", fake_run)
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            joplin_bin=Path("/opt/joplin/bin/joplin"),
            profile=profile,
            sync_on_write=True,
        ),
        client=fake.client(),
    )
    agent = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {"session_id": "session-1"},
    }

    note = service.create_note_for_agent(
        agent,
        event_type="COPY",
        title="Copy",
        body="Body",
    )
    service.update_note_for_agent(agent, note["id"], title="Renamed")
    service.delete_note_for_agent(agent, note["id"])

    assert commands == [
        ["/opt/joplin/bin/joplin", "--profile", str(profile), "sync"],
        ["/opt/joplin/bin/joplin", "--profile", str(profile), "sync"],
        ["/opt/joplin/bin/joplin", "--profile", str(profile), "sync"],
    ]


def test_joplin_service_sync_on_write_reports_cli_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinApi()

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="sync target failed")

    monkeypatch.setattr("agent_pbx.joplin.subprocess.run", fake_run)
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            joplin_bin=tmp_path / "joplin",
            sync_on_write=True,
        ),
        client=fake.client(),
    )
    agent = {"agent_id": "agent-1", "project": "demo", "metadata": {}}

    with pytest.raises(JoplinApiError, match="sync target failed"):
        service.create_note_for_agent(
            agent,
            event_type="COPY",
            title="Copy",
            body="Body",
        )


def test_joplin_service_sync_on_write_reports_zero_exit_last_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinApi()

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "Synchronization target:  (6)\n"
                "Last error: Error: Could not encrypt item abc: "
                "Master key is not loaded: key-1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_pbx.joplin.subprocess.run", fake_run)
    service = JoplinService(
        JoplinConfig(
            api_url="http://joplin.local",
            token="secret",
            joplin_bin=tmp_path / "joplin",
            sync_on_write=True,
        ),
        client=fake.client(),
    )
    agent = {"agent_id": "agent-1", "project": "demo", "metadata": {}}

    with pytest.raises(JoplinApiError, match="Master key is not loaded"):
        service.create_note_for_agent(
            agent,
            event_type="COPY",
            title="Copy",
            body="Body",
        )


def test_joplin_gateway_sync_on_write_enqueues_durable_job(tmp_path: Path) -> None:
    fake = FakeJoplinApi()
    store = make_store(tmp_path)
    gateway = JoplinGateway(
        store,
        JoplinService(
            JoplinConfig(api_url="http://joplin.local", token="secret"),
            client=fake.client(),
        ),
        sync_on_write=True,
    )
    agent = store.get_agent("agent-1")
    assert agent is not None

    note = gateway.create_note_for_agent(
        agent,
        event_type="COPY",
        title="Copy",
        body="Body",
    )

    jobs = store.list_joplin_sync_jobs()
    reopened = Store(tmp_path / "pbx.sqlite")
    reopened.init()
    reopened_jobs = reopened.list_joplin_sync_jobs()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"
    assert jobs[0]["reason"] == "note_copy_create"
    assert jobs[0]["agent_id"] == "agent-1"
    assert jobs[0]["note_id"] == note["id"]
    assert reopened_jobs[0]["sync_id"] == jobs[0]["sync_id"]
    assert reopened.joplin_sync_status()["pending"] == 1


def test_joplin_service_lists_notes_with_folder_scoped_endpoint() -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    agent = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {"session_id": "session-1"},
    }
    note = service.create_note_for_agent(
        agent,
        event_type="COPY",
        title="Scoped",
        body="Body",
    )
    fake.notes["outside"] = {
        "id": "outside",
        "parent_id": "unrelated-folder",
        "title": "Outside",
        "body": "Nope",
        "created_time": 1_700_000_000_000,
        "updated_time": 1_700_000_000_000,
    }

    notes = service.list_notes_for_agent(agent)

    assert [item["id"] for item in notes] == [note["id"]]
    assert fake.folder_note_requests == [str(note["parent_id"])]


def test_joplin_service_project_scope_includes_direct_and_descendant_notes() -> None:
    fake = FakeJoplinApi()
    service = make_service(fake)
    agent = {
        "agent_id": "agent-1",
        "project": "demo",
        "metadata": {"session_id": "session-1"},
    }
    project_folder = service.ensure_project_folder("demo")
    fake.notes["weekly"] = {
        "id": "weekly",
        "parent_id": project_folder,
        "title": "weekly-update-052926-060826",
        "body": "External project note",
        "created_time": 1_700_000_000_000,
        "updated_time": 1_700_000_030_000,
    }
    agent_note = service.create_note_for_agent(
        agent,
        event_type="COPY",
        title="Agent Copy",
        body="Agent body",
    )
    fake.notes["outside"] = {
        "id": "outside",
        "parent_id": "unrelated-folder",
        "title": "Outside",
        "body": "Nope",
        "created_time": 1_700_000_000_000,
        "updated_time": 1_700_000_040_000,
    }

    notes = service.list_notes_for_project("demo")
    fetched = service.get_note_for_project("demo", "weekly")
    updated = service.update_note_for_project("demo", "weekly", body="Edited")
    deleted = service.delete_note_for_project("demo", agent_note["id"])

    assert [note["id"] for note in notes] == ["weekly", agent_note["id"]]
    assert fetched["body"] == "External project note"
    assert updated["body"] == "Edited"
    assert deleted["id"] == agent_note["id"]
    with pytest.raises(JoplinScopeError):
        service.get_note_for_project("demo", "outside")


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
    service.append_log_section(
        store,
        "agent-1",
        title="Tmux Response",
        body="Copied from tmux.",
    )
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
    assert "Tmux Response" in note["body"]
    assert "Copied from tmux." in note["body"]
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
    project_created = client.post(
        "/v1/projects/demo/joplin/notes",
        json={"title": "weekly-update-052926-060826", "body": "Project note"},
    )
    project_notes = client.get("/v1/projects/demo/joplin/notes")
    project_note_id = project_created.json()["id"]
    project_fetched = client.get(f"/v1/projects/demo/joplin/notes/{project_note_id}")
    project_updated = client.put(
        f"/v1/projects/demo/joplin/notes/{project_note_id}",
        json={"body": "Project note edited"},
    )
    created = client.post(
        "/v1/agents/agent-1/joplin/notes",
        json={"title": "Scratch", "body": "Draft"},
    )
    copied = client.post("/v1/agents/agent-1/joplin/copy")
    notes = client.get("/v1/agents/agent-1/joplin/notes")
    note_id = copied.json()["id"]
    updated = client.put(
        f"/v1/agents/agent-1/joplin/notes/{note_id}",
        json={"body": "Edited"},
    )
    started = client.post("/v1/agents/agent-1/joplin/log/start")
    appended = client.post(
        "/v1/agents/agent-1/joplin/log/append",
        json={"title": "Operator Prompt", "body": "Tmux prompt"},
    )
    command = client.post(
        "/v1/commands",
        json={
            "agent_id": "agent-1",
            "type": "send_input",
            "payload": {"message": "Proceed"},
        },
    ).json()
    stopped = client.post("/v1/agents/agent-1/joplin/log/stop")
    deleted = client.delete(f"/v1/agents/agent-1/joplin/notes/{created.json()['id']}")
    project_deleted = client.delete(
        f"/v1/projects/demo/joplin/notes/{project_note_id}"
    )

    assert status.json()["available"] is True
    assert project_created.status_code == 200
    assert project_created.json()["title"] == "weekly-update-052926-060826"
    assert any(note["id"] == project_note_id for note in project_notes.json())
    assert project_fetched.json()["body"] == "Project note"
    assert project_updated.json()["body"] == "Project note edited"
    assert created.status_code == 200
    assert created.json()["title"] == "Scratch"
    assert copied.status_code == 200
    assert "Report detail" in copied.json()["body"]
    assert any(note["id"] == note_id for note in notes.json())
    assert updated.json()["body"] == "Edited"
    assert started.json()["active"] is True
    assert appended.json()["active"] is True
    assert stopped.json()["active"] is False
    assert deleted.json()["title"] == "Scratch"
    assert project_deleted.json()["title"] == "weekly-update-052926-060826"
    assert report["report_id"] in fake.report_logs
    assert command["command_id"] in fake.command_logs


def test_joplin_api_returns_not_found_for_out_of_scope_note(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ScopeFailingJoplinService(FakeJoplinService):
        def get_note_for_agent(
            self,
            _agent: dict[str, object],
            _note_id: str,
        ) -> dict[str, object]:
            raise JoplinScopeError("note is outside the selected agent's Joplin scope")

    monkeypatch.setattr(
        "agent_pbx.api.JoplinService",
        lambda _config: ScopeFailingJoplinService(),
    )
    client = TestClient(create_app(ServerConfig(db_path=tmp_path / "pbx.sqlite")))
    client.post(
        "/v1/agents/register",
        json={"agent_id": "agent-1", "project": "demo"},
    )

    response = client.get("/v1/agents/agent-1/joplin/notes/outside")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "JOPLIN_NOTE_OUT_OF_SCOPE"


def test_joplin_api_sync_status_manual_and_sync_on_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeJoplinService()
    monkeypatch.setattr("agent_pbx.api.JoplinService", lambda _config: fake)
    app = create_app(
        ServerConfig(
            db_path=tmp_path / "pbx.sqlite",
            joplin_api_url="http://joplin.local",
            joplin_token="secret",
            joplin_sync_on_write=True,
        )
    )
    with TestClient(app) as client:
        client.post(
            "/v1/agents/register",
            json={
                "agent_id": "agent-1",
                "project": "demo",
                "metadata": {"session_id": "session-1"},
            },
        )
        created = client.post(
            "/v1/agents/agent-1/joplin/notes",
            json={"title": "Scratch", "body": "Draft"},
        )
        manual = client.post("/v1/joplin/sync")
        status = client.get("/v1/joplin/sync")

    jobs = app.state.store.list_joplin_sync_jobs()
    reasons = {job["reason"] for job in jobs}
    assert created.status_code == 200
    assert manual.status_code == 200
    assert manual.json()["reason"] == "manual"
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["sync_on_write"] is True
    assert {"note_note_create", "manual"}.issubset(reasons)


@pytest.mark.asyncio
async def test_joplin_sync_worker_records_failure_status_and_event(
    tmp_path: Path,
) -> None:
    class FailingGateway:
        def run_sync_job(self, _job: dict[str, object]) -> None:
            raise JoplinApiError("sync failed")

    store = make_store(tmp_path)
    job = store.enqueue_joplin_sync(reason="manual")
    worker = asyncio.create_task(
        run_joplin_sync_worker(
            store,
            FailingGateway(),  # type: ignore[arg-type]
            interval_seconds=0.01,
        )
    )
    try:
        for _ in range(100):
            if store.joplin_sync_status()["failed"]:
                break
            await asyncio.sleep(0.01)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    status = store.joplin_sync_status()
    failed = store.get_joplin_sync_job(str(job["sync_id"]))
    events = store.list_events()
    assert status["failed"] == 1
    assert status["latest_error"]["sync_id"] == job["sync_id"]
    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["error"] == "sync failed"
    assert events[-1]["type"] == "joplin_sync_failed"
    assert events[-1]["payload"]["sync_id"] == job["sync_id"]


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
