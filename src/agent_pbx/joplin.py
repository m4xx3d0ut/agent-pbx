from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any

import httpx


JOPLIN_BIN_ENV = "AGENT_PBX_JOPLIN_BIN"
JOPLIN_PROFILE_ENV = "AGENT_PBX_JOPLIN_PROFILE"
JOPLIN_API_URL_ENV = "AGENT_PBX_JOPLIN_API_URL"
JOPLIN_TOKEN_ENV = "AGENT_PBX_JOPLIN_TOKEN"
JOPLIN_NOTEBOOK_ENV = "AGENT_PBX_JOPLIN_NOTEBOOK"
JOPLIN_TIMEOUT_ENV = "AGENT_PBX_JOPLIN_TIMEOUT_SECONDS"
JOPLIN_SYNC_ON_WRITE_ENV = "AGENT_PBX_JOPLIN_SYNC_ON_WRITE"
JOPLIN_WEBDAV_URL_ENV = "AGENT_PBX_JOPLIN_WEBDAV_URL"
JOPLIN_WEBDAV_USERNAME_ENV = "AGENT_PBX_JOPLIN_WEBDAV_USERNAME"
JOPLIN_WEBDAV_PASSWORD_ENV = "AGENT_PBX_JOPLIN_WEBDAV_PASSWORD"

DEFAULT_JOPLIN_NOTEBOOK = "Agent PBX"
DEFAULT_JOPLIN_TIMEOUT_SECONDS = 15.0
TERMINAL_LOG_STATUSES = {
    "blocked",
    "canceled",
    "cancelled",
    "complete",
    "completed",
    "done",
    "failed",
}


class JoplinApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @classmethod
    def from_response(cls, path: str, response: httpx.Response) -> JoplinApiError:
        message = response.text.strip()
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            raw_error = payload.get("error") or payload.get("message")
            if raw_error:
                message = str(raw_error)
        return cls(
            f"Joplin API {response.status_code} for {path}: {message}",
            status_code=response.status_code,
        )


class JoplinScopeError(ValueError):
    pass


@dataclass(frozen=True)
class JoplinConfig:
    api_url: str | None = None
    token: str | None = None
    notebook: str = DEFAULT_JOPLIN_NOTEBOOK
    joplin_bin: Path | None = None
    profile: Path | None = None
    timeout_seconds: float = DEFAULT_JOPLIN_TIMEOUT_SECONDS
    sync_on_write: bool = False
    webdav_url: str | None = None
    webdav_username: str | None = None
    webdav_password_configured: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_url and self.token)


class JoplinService:
    def __init__(
        self,
        config: JoplinConfig | None = None,
        *,
        client: httpx.Client | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config or env_joplin_config()
        self.client = client
        self.clock = clock
        self._root_notebook_id: str | None = None
        self._folder_cache: dict[tuple[str | None, str], str] = {}
        self._sync_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        base = {
            "configured": self.config.configured,
            "available": False,
            "api_url": self.config.api_url,
            "notebook": self.config.notebook,
            "joplin_bin": str(self.config.joplin_bin) if self.config.joplin_bin else None,
            "profile": str(self.config.profile) if self.config.profile else None,
            "sync_on_write": self.config.sync_on_write,
            "webdav_url": self.config.webdav_url,
            "webdav_username": self.config.webdav_username,
            "webdav_password_configured": self.config.webdav_password_configured,
            "checked_at": self.clock(),
            "root_notebook_id": self._root_notebook_id,
            "error": None,
        }
        if not self.config.configured:
            return {
                **base,
                "error": {
                    "code": "JOPLIN_NOT_CONFIGURED",
                    "message": (
                        f"{JOPLIN_API_URL_ENV} and {JOPLIN_TOKEN_ENV} must be set"
                    ),
                    "retryable": True,
                    "remediation": (
                        "Start Agent PBX with a Joplin REST API URL and token. "
                        "Use a disposable Joplin profile for integration tests."
                    ),
                },
            }
        try:
            root_id = self.find_folder_id(self.config.notebook, parent_id=None)
        except Exception as exc:  # noqa: BLE001 - status should explain any failure
            return {
                **base,
                "error": {
                    "code": "JOPLIN_UNAVAILABLE",
                    "message": str(exc),
                    "retryable": True,
                },
            }
        return {
            **base,
            "available": True,
            "root_notebook_id": root_id,
        }

    def list_notes_for_agent(self, agent: dict[str, Any]) -> list[dict[str, Any]]:
        folder_id = self.ensure_agent_folder(agent)
        notes = self._get_paginated(
            f"/folders/{folder_id}/notes",
            {
                "fields": "id,parent_id,title,created_time,updated_time",
                "order_by": "updated_time",
                "order_dir": "DESC",
            },
        )
        return [self._note_summary(note) for note in notes]

    def list_notes_for_project(self, project: str) -> list[dict[str, Any]]:
        folder_ids = self.project_folder_ids(project)
        notes: list[dict[str, Any]] = []
        for folder_id in sorted(folder_ids):
            notes.extend(
                self._get_paginated(
                    f"/folders/{folder_id}/notes",
                    {
                        "fields": "id,parent_id,title,created_time,updated_time",
                        "order_by": "updated_time",
                        "order_dir": "DESC",
                    },
                )
            )
        summaries = [self._note_summary(note) for note in notes]
        summaries.sort(
            key=lambda note: float(note.get("updated_time") or 0),
            reverse=True,
        )
        return summaries

    def get_note_for_agent(self, agent: dict[str, Any], note_id: str) -> dict[str, Any]:
        folder_id = self.ensure_agent_folder(agent)
        note = self._request(
            "GET",
            f"/notes/{note_id}",
            params={"fields": "id,parent_id,title,body,created_time,updated_time"},
        )
        if str(note.get("parent_id") or "") != folder_id:
            raise JoplinScopeError("note is outside the selected agent's Joplin scope")
        return self._note_response(note)

    def get_note_for_project(self, project: str, note_id: str) -> dict[str, Any]:
        folder_ids = self.project_folder_ids(project)
        note = self._request(
            "GET",
            f"/notes/{note_id}",
            params={"fields": "id,parent_id,title,body,created_time,updated_time"},
        )
        if str(note.get("parent_id") or "") not in folder_ids:
            raise JoplinScopeError("note is outside the selected project's Joplin scope")
        return self._note_response(note)

    def create_note_for_project(
        self,
        project: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        folder_id = self.ensure_project_folder(project)
        note = self._request(
            "POST",
            "/notes",
            json={
                "parent_id": folder_id,
                "title": title,
                "body": body,
            },
        )
        if self.config.sync_on_write:
            self.sync()
        return self._note_response(note)

    def update_note_for_project(
        self,
        project: str,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_note_for_project(project, note_id)
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if payload:
            self._request("PUT", f"/notes/{note_id}", json=payload)
        updated = self.get_note_for_project(project, note_id)
        if updated["updated_time"] == existing["updated_time"] and payload:
            updated["updated_time"] = self.clock()
        if payload and self.config.sync_on_write:
            self.sync()
        return updated

    def delete_note_for_project(
        self,
        project: str,
        note_id: str,
    ) -> dict[str, Any]:
        existing = self.get_note_for_project(project, note_id)
        self._request("DELETE", f"/notes/{note_id}")
        if self.config.sync_on_write:
            self.sync()
        return existing

    def update_note_for_agent(
        self,
        agent: dict[str, Any],
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        existing = self.get_note_for_agent(agent, note_id)
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if payload:
            self._request("PUT", f"/notes/{note_id}", json=payload)
        updated = self.get_note_for_agent(agent, note_id)
        if updated["updated_time"] == existing["updated_time"] and payload:
            updated["updated_time"] = self.clock()
        if payload and self.config.sync_on_write:
            self.sync()
        return updated

    def delete_note_for_agent(
        self,
        agent: dict[str, Any],
        note_id: str,
    ) -> dict[str, Any]:
        existing = self.get_note_for_agent(agent, note_id)
        self._request("DELETE", f"/notes/{note_id}")
        if self.config.sync_on_write:
            self.sync()
        return existing

    def create_note_for_agent(
        self,
        agent: dict[str, Any],
        *,
        event_type: str,
        body: str,
        title: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        folder_id = self.ensure_agent_folder(agent)
        note_title = title or scoped_note_title(
            session_id or agent_session_id(agent),
            event_type,
        )
        note = self._request(
            "POST",
            "/notes",
            json={
                "parent_id": folder_id,
                "title": note_title,
                "body": body,
            },
        )
        if self.config.sync_on_write:
            self.sync()
        return self._note_response(note)

    def create_document(
        self,
        *,
        agent: dict[str, Any],
        title: str,
        body: str,
        session_id: str | None = None,
        mermaid_blocks: list[str] | None = None,
        assets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self.create_note_for_agent(
            agent,
            event_type="DOC",
            title=title,
            session_id=session_id,
            body=format_document_body(
                body,
                mermaid_blocks=mermaid_blocks or [],
                assets=assets or [],
            ),
        )

    def start_log(self, store: Any, agent: dict[str, Any]) -> dict[str, Any]:
        active = store.get_active_joplin_log(agent["agent_id"])
        if active is not None:
            return {**active, "already_active": True}
        session_id = agent_session_id(agent)
        body = format_log_header(agent, session_id)
        note = self.create_note_for_agent(
            agent,
            event_type="LOG",
            body=body,
            session_id=session_id,
        )
        return store.start_joplin_log(
            agent_id=agent["agent_id"],
            project=agent["project"],
            session_id=session_id,
            note_id=note["id"],
            title=note["title"],
        )

    def stop_log(self, store: Any, agent_id: str) -> dict[str, Any] | None:
        return store.stop_active_joplin_log(agent_id)

    def append_command_log(self, store: Any, command: dict[str, Any]) -> None:
        agent_id = command.get("agent_id")
        if not agent_id:
            return
        active = store.get_active_joplin_log(str(agent_id))
        if active is None:
            return
        if command.get("type") not in {"send_input", "start_task"}:
            return
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        message = payload.get("message") or payload.get("task") or payload
        section = markdown_section("Operator Prompt", str(message))
        self.append_to_note(str(active["note_id"]), section)
        store.touch_joplin_log(active["log_id"])

    def append_report_log(self, store: Any, report: dict[str, Any]) -> None:
        if str(report.get("status") or "").lower() not in TERMINAL_LOG_STATUSES:
            return
        active = store.get_active_joplin_log(str(report["agent_id"]))
        if active is None:
            return
        body = "\n\n".join(
            [
                f"Status: {report.get('status')}",
                f"Summary: {report.get('summary')}",
                str(report.get("detail") or ""),
            ]
        )
        self.append_to_note(str(active["note_id"]), markdown_section("Agent Response", body))
        store.touch_joplin_log(active["log_id"])

    def append_log_section(
        self,
        store: Any,
        agent_id: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any] | None:
        active = store.get_active_joplin_log(agent_id)
        if active is None:
            return None
        self.append_to_note(str(active["note_id"]), markdown_section(title, body))
        store.touch_joplin_log(active["log_id"])
        return store.get_joplin_log(active["log_id"]) or active

    def append_to_note(self, note_id: str, markdown: str) -> dict[str, Any]:
        note = self._request(
            "GET",
            f"/notes/{note_id}",
            params={"fields": "id,parent_id,title,body,created_time,updated_time"},
        )
        existing = str(note.get("body") or "")
        body = f"{existing.rstrip()}\n\n{markdown.strip()}\n"
        self._request("PUT", f"/notes/{note_id}", json={"body": body})
        if self.config.sync_on_write:
            self.sync()
        return self._note_response({**note, "body": body})

    def ensure_project_folder(self, project: str) -> str:
        root_id = self.ensure_root_notebook()
        return self.ensure_folder(project, parent_id=root_id)

    def project_folder_ids(self, project: str) -> set[str]:
        project_id = self.ensure_project_folder(project)
        folders = self._get_paginated(
            "/folders",
            {"fields": "id,parent_id,title"},
        )
        children_by_parent: dict[str, list[str]] = {}
        for folder in folders:
            folder_id = str(folder.get("id") or "")
            parent_id = normalize_parent_id(folder.get("parent_id"))
            if not folder_id or parent_id is None:
                continue
            children_by_parent.setdefault(parent_id, []).append(folder_id)
        scoped = {project_id}
        pending = [project_id]
        while pending:
            parent_id = pending.pop()
            for child_id in children_by_parent.get(parent_id, []):
                if child_id in scoped:
                    continue
                scoped.add(child_id)
                pending.append(child_id)
        return scoped

    def ensure_agent_folder(self, agent: dict[str, Any]) -> str:
        project_id = self.ensure_project_folder(str(agent["project"]))
        return self.ensure_folder(str(agent["agent_id"]), parent_id=project_id)

    def ensure_root_notebook(self) -> str:
        if self._root_notebook_id:
            return self._root_notebook_id
        self._root_notebook_id = self.ensure_folder(self.config.notebook, parent_id=None)
        return self._root_notebook_id

    def ensure_folder(self, title: str, *, parent_id: str | None) -> str:
        key = (parent_id, title)
        if key in self._folder_cache:
            return self._folder_cache[key]
        folder_id = self.find_folder_id(title, parent_id=parent_id)
        if folder_id is not None:
            self._folder_cache[key] = folder_id
            return folder_id
        payload: dict[str, Any] = {"title": title}
        if parent_id:
            payload["parent_id"] = parent_id
        folder = self._request("POST", "/folders", json=payload)
        folder_id = str(folder["id"])
        self._folder_cache[key] = folder_id
        return folder_id

    def find_folder_id(self, title: str, *, parent_id: str | None) -> str | None:
        folders = self._get_paginated(
            "/folders",
            {"fields": "id,parent_id,title"},
        )
        for folder in folders:
            if (
                str(folder.get("title") or "") == title
                and normalize_parent_id(folder.get("parent_id"))
                == normalize_parent_id(parent_id)
            ):
                return str(folder["id"])
        return None

    def sync(self) -> None:
        command = self.sync_command()
        timeout = max(self.config.timeout_seconds, 60.0)
        with self._sync_lock:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise JoplinApiError(f"Joplin sync failed: {exc}") from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise JoplinApiError(
                f"Joplin sync failed with exit code {result.returncode}: {message}"
            )
        sync_error = joplin_sync_error(result.stdout, result.stderr)
        if sync_error:
            raise JoplinApiError(f"Joplin sync reported an error: {sync_error}")

    def sync_command(self) -> list[str]:
        joplin_bin = self.config.joplin_bin
        executable = str(joplin_bin) if joplin_bin else shutil.which("joplin")
        if not executable:
            raise JoplinApiError(
                f"{JOPLIN_SYNC_ON_WRITE_ENV}=1 requires {JOPLIN_BIN_ENV} "
                "or a joplin executable on PATH"
            )
        command = [executable]
        if self.config.profile:
            command.extend(["--profile", str(self.config.profile)])
        command.append("sync")
        return command

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.config.configured:
            raise RuntimeError("Joplin is not configured")
        url = f"{self.config.api_url.rstrip('/')}/{path.lstrip('/')}"
        request_params = dict(params or {})
        request_params["token"] = self.config.token
        client = self.client or httpx.Client(timeout=self.config.timeout_seconds)
        close_client = self.client is None
        try:
            response = client.request(
                method,
                url,
                params=request_params,
                json=json,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise JoplinApiError.from_response(path, exc.response) from exc
            if not response.content:
                return {}
            data = response.json()
            return data if isinstance(data, dict) else {"items": data}
        except httpx.HTTPError as exc:
            raise JoplinApiError(f"Joplin API request failed for {path}: {exc}") from exc
        finally:
            if close_client:
                client.close()

    def _get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        page = 1
        items: list[dict[str, Any]] = []
        while True:
            payload = self._request(
                "GET",
                path,
                params={**(params or {}), "page": page},
            )
            raw_items = payload.get("items")
            if isinstance(raw_items, list):
                items.extend(item for item in raw_items if isinstance(item, dict))
            if not payload.get("has_more"):
                return items
            page += 1

    @staticmethod
    def _note_summary(note: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(note.get("id") or ""),
            "parent_id": str(note.get("parent_id") or ""),
            "title": str(note.get("title") or ""),
            "created_time": note.get("created_time"),
            "updated_time": note.get("updated_time"),
        }

    @classmethod
    def _note_response(cls, note: dict[str, Any]) -> dict[str, Any]:
        return {
            **cls._note_summary(note),
            "body": str(note.get("body") or ""),
        }


class JoplinGateway:
    def __init__(
        self,
        store: Any,
        service: JoplinService,
        *,
        sync_on_write: bool | None = None,
        clock: Any = time.time,
    ) -> None:
        self.store = store
        self.service = service
        self.config = service.config
        self.sync_on_write = (
            bool(sync_on_write)
            if sync_on_write is not None
            else bool(getattr(service.config, "sync_on_write", False))
        )
        self.clock = clock

    @classmethod
    def from_config(
        cls,
        store: Any,
        config: JoplinConfig,
        *,
        client: httpx.Client | None = None,
        clock: Any = time.time,
    ) -> JoplinGateway:
        service = JoplinService(
            replace(config, sync_on_write=False),
            client=client,
            clock=clock,
        )
        return cls(store, service, sync_on_write=config.sync_on_write, clock=clock)

    def status(self) -> dict[str, Any]:
        payload = dict(self.service.status())
        payload["sync_on_write"] = self.sync_on_write
        payload["sync"] = self.sync_status()
        return payload

    def sync_status(self) -> dict[str, Any]:
        return {
            "enabled": bool(getattr(self.config, "configured", False)),
            "sync_on_write": self.sync_on_write,
            **self.store.joplin_sync_status(),
        }

    def request_sync(
        self,
        *,
        reason: str = "manual",
        agent_id: str | None = None,
        note_id: str | None = None,
    ) -> dict[str, Any]:
        if not getattr(self.config, "configured", False):
            raise RuntimeError("Joplin is not configured")
        job = self.store.enqueue_joplin_sync(
            reason=reason,
            agent_id=agent_id,
            note_id=note_id,
        )
        self.store.append_event(
            "joplin_sync_queued",
            {
                "sync_id": job["sync_id"],
                "reason": reason,
                "agent_id": agent_id,
                "note_id": note_id,
            },
            agent_id or note_id or job["sync_id"],
        )
        return job

    def run_sync_job(self, job: dict[str, Any]) -> None:
        self.service.sync()

    def list_notes_for_agent(self, agent: dict[str, Any]) -> list[dict[str, Any]]:
        return self.service.list_notes_for_agent(agent)

    def list_notes_for_project(self, project: str) -> list[dict[str, Any]]:
        return self.service.list_notes_for_project(project)

    def get_note_for_agent(self, agent: dict[str, Any], note_id: str) -> dict[str, Any]:
        return self.service.get_note_for_agent(agent, note_id)

    def get_note_for_project(self, project: str, note_id: str) -> dict[str, Any]:
        return self.service.get_note_for_project(project, note_id)

    def create_note_for_project(
        self,
        project: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        note = self.service.create_note_for_project(project, title=title, body=body)
        self._enqueue_write_sync(
            "project_note_create",
            note_id=str(note.get("id") or ""),
        )
        return note

    def update_note_for_project(
        self,
        project: str,
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        note = self.service.update_note_for_project(
            project,
            note_id,
            title=title,
            body=body,
        )
        if title is not None or body is not None:
            self._enqueue_write_sync("project_note_update", note_id=note_id)
        return note

    def delete_note_for_project(
        self,
        project: str,
        note_id: str,
    ) -> dict[str, Any]:
        note = self.service.delete_note_for_project(project, note_id)
        self._enqueue_write_sync("project_note_delete", note_id=note_id)
        return note

    def update_note_for_agent(
        self,
        agent: dict[str, Any],
        note_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
    ) -> dict[str, Any]:
        note = self.service.update_note_for_agent(
            agent,
            note_id,
            title=title,
            body=body,
        )
        if title is not None or body is not None:
            self._enqueue_write_sync("note_update", agent=agent, note_id=note_id)
        return note

    def delete_note_for_agent(
        self,
        agent: dict[str, Any],
        note_id: str,
    ) -> dict[str, Any]:
        note = self.service.delete_note_for_agent(agent, note_id)
        self._enqueue_write_sync("note_delete", agent=agent, note_id=note_id)
        return note

    def create_note_for_agent(
        self,
        agent: dict[str, Any],
        *,
        event_type: str,
        body: str,
        title: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        note = self.service.create_note_for_agent(
            agent,
            event_type=event_type,
            body=body,
            title=title,
            session_id=session_id,
        )
        self._enqueue_write_sync(
            f"note_{event_type.lower()}_create",
            agent=agent,
            note_id=str(note.get("id") or ""),
        )
        return note

    def create_document(
        self,
        *,
        agent: dict[str, Any],
        title: str,
        body: str,
        session_id: str | None = None,
        mermaid_blocks: list[str] | None = None,
        assets: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        note = self.service.create_document(
            agent=agent,
            title=title,
            body=body,
            session_id=session_id,
            mermaid_blocks=mermaid_blocks,
            assets=assets,
        )
        self._enqueue_write_sync(
            "document_create",
            agent=agent,
            note_id=str(note.get("id") or ""),
        )
        return note

    def start_log(self, store: Any, agent: dict[str, Any]) -> dict[str, Any]:
        log = self.service.start_log(store, agent)
        if not log.get("already_active"):
            self._enqueue_write_sync(
                "log_start",
                agent=agent,
                note_id=str(log.get("note_id") or ""),
            )
        return log

    def stop_log(self, store: Any, agent_id: str) -> dict[str, Any] | None:
        return self.service.stop_log(store, agent_id)

    def append_command_log(self, store: Any, command: dict[str, Any]) -> None:
        agent_id = command.get("agent_id")
        active = (
            store.get_active_joplin_log(str(agent_id))
            if agent_id and command.get("type") in {"send_input", "start_task"}
            else None
        )
        self.service.append_command_log(store, command)
        if active is not None:
            self._enqueue_write_sync(
                "log_command_append",
                agent_id=str(agent_id),
                note_id=str(active.get("note_id") or ""),
            )

    def append_report_log(self, store: Any, report: dict[str, Any]) -> None:
        status = str(report.get("status") or "").lower()
        active = (
            store.get_active_joplin_log(str(report.get("agent_id")))
            if status in TERMINAL_LOG_STATUSES
            else None
        )
        self.service.append_report_log(store, report)
        if active is not None:
            self._enqueue_write_sync(
                "log_report_append",
                agent_id=str(report.get("agent_id") or ""),
                note_id=str(active.get("note_id") or ""),
            )

    def append_log_section(
        self,
        store: Any,
        agent_id: str,
        *,
        title: str,
        body: str,
    ) -> dict[str, Any] | None:
        log = self.service.append_log_section(
            store,
            agent_id,
            title=title,
            body=body,
        )
        if log is not None:
            self._enqueue_write_sync(
                "log_section_append",
                agent_id=agent_id,
                note_id=str(log.get("note_id") or ""),
            )
        return log

    def _enqueue_write_sync(
        self,
        reason: str,
        *,
        agent: dict[str, Any] | None = None,
        agent_id: str | None = None,
        note_id: str | None = None,
    ) -> None:
        if not self.sync_on_write:
            return
        resolved_agent_id = agent_id or (
            str(agent.get("agent_id")) if agent and agent.get("agent_id") else None
        )
        self.request_sync(
            reason=reason,
            agent_id=resolved_agent_id,
            note_id=note_id or None,
        )


def env_joplin_config() -> JoplinConfig:
    return JoplinConfig(
        api_url=env_text_value(JOPLIN_API_URL_ENV),
        token=env_text_value(JOPLIN_TOKEN_ENV),
        notebook=env_text_value(JOPLIN_NOTEBOOK_ENV) or DEFAULT_JOPLIN_NOTEBOOK,
        joplin_bin=env_path_value(JOPLIN_BIN_ENV),
        profile=env_path_value(JOPLIN_PROFILE_ENV),
        timeout_seconds=env_float(JOPLIN_TIMEOUT_ENV, DEFAULT_JOPLIN_TIMEOUT_SECONDS),
        sync_on_write=env_flag(JOPLIN_SYNC_ON_WRITE_ENV),
        webdav_url=env_text_value(JOPLIN_WEBDAV_URL_ENV),
        webdav_username=env_text_value(JOPLIN_WEBDAV_USERNAME_ENV),
        webdav_password_configured=bool(env_text_value(JOPLIN_WEBDAV_PASSWORD_ENV)),
    )


def env_text_value(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def env_path_value(name: str) -> Path | None:
    value = env_text_value(name)
    return Path(value).expanduser() if value else None


def env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on", "y"}


def joplin_sync_error(stdout: str, stderr: str) -> str:
    output = "\n".join(part.strip() for part in (stdout, stderr) if part.strip())
    if not output:
        return ""
    markers = (
        "Last error:",
        "Master key is not loaded",
        "Your password is needed to decrypt",
    )
    for line in reversed(output.splitlines()):
        if any(marker in line for marker in markers):
            return line.strip()
    return ""


def normalize_parent_id(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def agent_session_id(agent: dict[str, Any]) -> str:
    metadata = agent.get("metadata") if isinstance(agent.get("metadata"), dict) else {}
    for key in ("session_id", "session", "run_id"):
        value = metadata.get(key)
        if value:
            return slugify(str(value))
    return slugify(str(agent.get("agent_id") or "agent"))


def scoped_note_title(session_id: str, event_type: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{slugify(session_id)}-{timestamp}-{event_type.upper()}"


def format_log_header(agent: dict[str, Any], session_id: str) -> str:
    created = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return "\n".join(
        [
            f"# Agent PBX LOG: {agent.get('agent_id')}",
            "",
            f"- Project: {agent.get('project')}",
            f"- Session: {session_id}",
            f"- Created: {created}",
            "",
        ]
    ).rstrip()


def format_copy_body(
    *,
    agent: dict[str, Any],
    source: str,
    title: str,
    body: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    copied = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# {title}",
        "",
        f"- Agent: {agent.get('agent_id')}",
        f"- Project: {agent.get('project')}",
        f"- Source: {source}",
        f"- Copied: {copied}",
        "",
        body,
    ]
    if metadata:
        import json

        lines.extend(["", "```json", json.dumps(metadata, indent=2, sort_keys=True), "```"])
    return "\n".join(lines).rstrip() + "\n"


def format_document_body(
    body: str,
    *,
    mermaid_blocks: list[str],
    assets: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    if assets:
        import json

        lines.extend(
            [
                "---",
                "agent_pbx_assets: " + json.dumps(assets, sort_keys=True),
                "---",
                "",
            ]
        )
    lines.append(body.rstrip())
    for block in mermaid_blocks:
        clean = block.strip()
        if clean:
            lines.extend(["", "```mermaid", clean, "```"])
    return "\n".join(lines).rstrip() + "\n"


def markdown_section(title: str, body: str) -> str:
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"## {title} ({timestamp})\n\n{body.strip()}\n"


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return slug or "item"
