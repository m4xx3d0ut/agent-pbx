from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
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
JOPLIN_PROFILE_OWNER_MODE_ENV = "AGENT_PBX_JOPLIN_PROFILE_OWNER_MODE"
JOPLIN_WEBDAV_URL_ENV = "AGENT_PBX_JOPLIN_WEBDAV_URL"
JOPLIN_WEBDAV_USERNAME_ENV = "AGENT_PBX_JOPLIN_WEBDAV_USERNAME"
JOPLIN_WEBDAV_PASSWORD_ENV = "AGENT_PBX_JOPLIN_WEBDAV_PASSWORD"

DEFAULT_JOPLIN_NOTEBOOK = "Agent PBX"
DEFAULT_JOPLIN_TIMEOUT_SECONDS = 15.0
DEFAULT_JOPLIN_E2EE_WAIT_SECONDS = 10.0
JOPLIN_PROFILE_OWNER_MODES = {"external", "managed_cli"}
JOPLIN_DELETE_SYNC_REASONS = {"note_delete", "project_note_delete"}
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
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str = "JOPLIN_API_ERROR",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable

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
        code = (
            "JOPLIN_AUTH_ERROR"
            if response.status_code in {401, 403}
            else "JOPLIN_RATE_LIMITED"
            if response.status_code == 429
            else "JOPLIN_NOTE_NOT_FOUND"
            if response.status_code == 404
            else "JOPLIN_API_ERROR"
        )
        return cls(
            f"Joplin API {response.status_code} for {path}: {message}",
            status_code=response.status_code,
            code=code,
            retryable=response.status_code not in {401, 403, 404},
        )


class JoplinScopeError(ValueError):
    pass


class JoplinConflictError(RuntimeError):
    """Raised when a note changed after the editor loaded its base revision."""

    def __init__(
        self,
        message: str,
        *,
        note_id: str,
        base_revision: str,
        current: dict[str, Any],
        local_title: str | None,
        local_body: str | None,
        base_title: str | None = None,
        base_body: str | None = None,
        conflict_id: str | None = None,
        merged_title: str | None = None,
        merged_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.note_id = note_id
        self.base_revision = base_revision
        self.current = current
        self.local_title = local_title
        self.local_body = local_body
        self.base_title = base_title
        self.base_body = base_body
        self.conflict_id = conflict_id
        self.merged_title = merged_title
        self.merged_body = merged_body

    def as_error(self) -> dict[str, Any]:
        return {
            "code": "JOPLIN_NOTE_CONFLICT",
            "message": str(self),
            "retryable": False,
            "note_id": self.note_id,
            "base_revision": self.base_revision,
            "current": self.current,
            "local": {
                "title": self.local_title,
                "body": self.local_body,
            },
            "base": {
                "title": self.base_title,
                "body": self.base_body,
            },
            "conflict_id": self.conflict_id,
            "merged": {
                "title": self.merged_title,
                "body": self.merged_body,
            },
        }


class JoplinSyncLockError(JoplinApiError):
    def __init__(self, message: str) -> None:
        super().__init__(
            message,
            code="JOPLIN_SYNC_LOCKED",
            retryable=True,
        )


class JoplinEncryptedNoteError(JoplinApiError):
    def __init__(self, note_id: str) -> None:
        super().__init__(
            "Joplin note is encrypted and cannot be read or changed until the "
            "profile decrypts it",
            status_code=409,
            code="JOPLIN_NOTE_ENCRYPTED",
            retryable=True,
        )
        self.note_id = note_id


class JoplinE2EELockedError(JoplinApiError):
    def __init__(self, encryption: dict[str, Any]) -> None:
        pending = int(encryption.get("pending_total") or 0)
        super().__init__(
            f"Joplin sync completed but {pending} item(s) remain encrypted; "
            "unlock E2EE in the configured profile and retry",
            status_code=409,
            code="JOPLIN_E2EE_LOCKED",
            retryable=True,
        )
        self.encryption = encryption


class JoplinSyncTargetError(JoplinApiError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        note_ids: list[str],
    ) -> None:
        super().__init__(
            message,
            status_code=409,
            code=code,
            retryable=True,
        )
        self.note_ids = note_ids


class JoplinProfileInUseError(JoplinApiError):
    def __init__(self, message: str, *, code: str = "JOPLIN_PROFILE_IN_USE") -> None:
        super().__init__(message, status_code=409, code=code, retryable=True)


def joplin_note_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def joplin_note_revision(note: dict[str, Any]) -> str:
    payload = {
        "id": str(note.get("id") or ""),
        "title": str(note.get("title") or ""),
        "body": str(note.get("body") or ""),
        "updated_time": note.get("updated_time"),
        "user_updated_time": note.get("user_updated_time"),
        "encryption_applied": int(bool(note.get("encryption_applied"))),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def merge_joplin_text(base: str, local: str, current: str) -> tuple[str, bool]:
    """Return a conservative three-way merge and whether conflicts remain."""

    if local == current:
        return local, False
    if local == base:
        return current, False
    if current == base:
        return local, False
    return (
        "\n".join(
            (
                "<<<<<<< LOCAL DRAFT",
                local,
                "||||||| EDIT BASE",
                base,
                "=======",
                current,
                ">>>>>>> CURRENT JOPLIN",
            )
        ),
        True,
    )


@dataclass(frozen=True)
class JoplinConfig:
    api_url: str | None = None
    token: str | None = None
    notebook: str = DEFAULT_JOPLIN_NOTEBOOK
    joplin_bin: Path | None = None
    profile: Path | None = None
    timeout_seconds: float = DEFAULT_JOPLIN_TIMEOUT_SECONDS
    sync_on_write: bool = False
    profile_owner_mode: str = "external"
    e2ee_wait_seconds: float = DEFAULT_JOPLIN_E2EE_WAIT_SECONDS
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
        self._encryption_lock = threading.Lock()
        self._encryption_cache: tuple[float, dict[str, Any]] | None = None

    def status(self) -> dict[str, Any]:
        base = {
            "configured": self.config.configured,
            "available": False,
            "api_url": self.config.api_url,
            "notebook": self.config.notebook,
            "joplin_bin": str(self.config.joplin_bin) if self.config.joplin_bin else None,
            "profile": str(self.config.profile) if self.config.profile else None,
            "sync_on_write": self.config.sync_on_write,
            "profile_owner_mode": self.config.profile_owner_mode,
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
        encryption: dict[str, Any] | None = None
        encryption_error: dict[str, Any] | None = None
        try:
            encryption = self.encryption_status()
        except JoplinApiError as exc:
            encryption_error = {
                "code": exc.code,
                "message": str(exc),
                "retryable": exc.retryable,
            }
        except Exception as exc:  # status remains usable if an older API omits fields
            encryption_error = {
                "code": "JOPLIN_E2EE_STATUS_UNAVAILABLE",
                "message": str(exc),
                "retryable": True,
            }
        return {
            **base,
            "available": True,
            "root_notebook_id": root_id,
            "encryption": encryption,
            "encryption_error": encryption_error,
        }

    def list_notes_for_agent(self, agent: dict[str, Any]) -> list[dict[str, Any]]:
        folder_id = self.ensure_agent_folder(agent)
        notes = self._get_paginated(
            f"/folders/{folder_id}/notes",
            {
                "fields": (
                    "id,parent_id,title,created_time,updated_time,user_updated_time,"
                    "encryption_applied"
                ),
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
                        "fields": (
                            "id,parent_id,title,created_time,updated_time,"
                            "user_updated_time,encryption_applied"
                        ),
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
            params={
                "fields": (
                    "id,parent_id,title,body,created_time,updated_time,"
                    "user_updated_time,encryption_applied"
                )
            },
        )
        if str(note.get("parent_id") or "") != folder_id:
            raise JoplinScopeError("note is outside the selected agent's Joplin scope")
        return self._note_response(note)

    def get_note_for_project(self, project: str, note_id: str) -> dict[str, Any]:
        folder_ids = self.project_folder_ids(project)
        note = self._request(
            "GET",
            f"/notes/{note_id}",
            params={
                "fields": (
                    "id,parent_id,title,body,created_time,updated_time,"
                    "user_updated_time,encryption_applied"
                )
            },
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
        base_revision: str | None = None,
        base_title: str | None = None,
        base_body: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        existing = self.get_note_for_project(project, note_id)
        self.assert_note_mutable(existing)
        self._assert_note_revision(
            existing,
            base_revision=base_revision,
            title=title,
            body=body,
            base_title=base_title,
            base_body=base_body,
            force=force,
        )
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
        self.assert_note_mutable(existing)
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
        base_revision: str | None = None,
        base_title: str | None = None,
        base_body: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        existing = self.get_note_for_agent(agent, note_id)
        self.assert_note_mutable(existing)
        self._assert_note_revision(
            existing,
            base_revision=base_revision,
            title=title,
            body=body,
            base_title=base_title,
            base_body=base_body,
            force=force,
        )
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
        self.assert_note_mutable(existing)
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
            params={
                "fields": (
                    "id,parent_id,title,body,created_time,updated_time,"
                    "user_updated_time,encryption_applied"
                )
            },
        )
        self.assert_note_mutable(note)
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
            except subprocess.TimeoutExpired as exc:
                raise JoplinApiError(
                    f"Joplin sync timed out after {timeout:.0f}s",
                    code="JOPLIN_SYNC_TIMEOUT",
                    retryable=True,
                ) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise JoplinApiError(
                    f"Joplin sync failed: {exc}",
                    code="JOPLIN_SYNC_CLI_ERROR",
                    retryable=True,
                ) from exc
        if result.returncode != 0:
            message = (result.stderr or result.stdout).strip()
            raise JoplinApiError(
                f"Joplin sync failed with exit code {result.returncode}: {message}",
                code="JOPLIN_SYNC_CLI_ERROR",
                retryable=True,
            )
        sync_error = joplin_sync_error(result.stdout, result.stderr)
        if sync_error:
            raise JoplinApiError(
                f"Joplin sync reported an error: {sync_error}",
                code="JOPLIN_SYNC_CONFLICT"
                if "conflict" in sync_error.lower()
                else "JOPLIN_SYNC_CLI_ERROR",
                retryable=True,
            )

    @staticmethod
    def note_encrypted(note: dict[str, Any]) -> bool:
        try:
            return int(note.get("encryption_applied") or 0) != 0
        except (TypeError, ValueError):
            return bool(note.get("encryption_applied"))

    @classmethod
    def assert_note_mutable(cls, note: dict[str, Any]) -> None:
        if cls.note_encrypted(note):
            raise JoplinEncryptedNoteError(str(note.get("id") or "unknown"))

    def encryption_status(self, *, force: bool = False) -> dict[str, Any]:
        now = self.clock()
        with self._encryption_lock:
            if (
                not force
                and self._encryption_cache is not None
                and now - self._encryption_cache[0] < 10.0
            ):
                return dict(self._encryption_cache[1])
            counts: dict[str, int] = {}
            for item_type, path in (
                ("notes", "/notes"),
                ("resources", "/resources"),
                ("folders", "/folders"),
            ):
                items = self._get_paginated(
                    path,
                    {"fields": "id,encryption_applied"},
                )
                counts[item_type] = sum(self.note_encrypted(item) for item in items)
            payload: dict[str, Any] = {
                "state": "locked" if any(counts.values()) else "ready",
                "pending_total": sum(counts.values()),
                "pending_notes": counts["notes"],
                "pending_resources": counts["resources"],
                "pending_folders": counts["folders"],
                "checked_at": now,
            }
            self._encryption_cache = (now, payload)
            return dict(payload)

    def wait_for_decryption(self) -> dict[str, Any]:
        timeout = max(0.0, float(self.config.e2ee_wait_seconds))
        deadline = time.monotonic() + timeout
        while True:
            encryption = self.encryption_status(force=True)
            if not int(encryption.get("pending_total") or 0):
                return encryption
            if time.monotonic() >= deadline:
                raise JoplinE2EELockedError(encryption)
            time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))

    def wait_for_sync_targets(
        self,
        note_ids: list[str],
        *,
        expect_deleted: bool = False,
    ) -> dict[str, Any]:
        targets = list(
            dict.fromkeys(
                note_id.strip() for note_id in note_ids if note_id.strip()
            )
        )
        if not targets:
            return {
                "state": "ready",
                "target_note_ids": [],
                "expected_deleted": expect_deleted,
            }
        timeout = max(0.0, float(self.config.e2ee_wait_seconds))
        deadline = time.monotonic() + timeout
        while True:
            encrypted: list[str] = []
            missing: list[str] = []
            present: list[str] = []
            for note_id in targets:
                try:
                    note = self._request(
                        "GET",
                        f"/notes/{note_id}",
                        params={"fields": "id,encryption_applied"},
                    )
                except JoplinApiError as exc:
                    if exc.status_code == 404:
                        missing.append(note_id)
                        continue
                    raise
                present.append(note_id)
                if self.note_encrypted(note):
                    encrypted.append(note_id)

            if expect_deleted and not present:
                return {
                    "state": "ready",
                    "target_note_ids": targets,
                    "expected_deleted": True,
                }
            if not expect_deleted and not missing and not encrypted:
                return {
                    "state": "ready",
                    "target_note_ids": targets,
                    "expected_deleted": False,
                }
            if time.monotonic() < deadline:
                time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
                continue
            if encrypted:
                raise JoplinE2EELockedError(
                    {
                        "state": "locked",
                        "scope": "sync_targets",
                        "pending_total": len(encrypted),
                        "pending_notes": len(encrypted),
                        "pending_resources": 0,
                        "pending_folders": 0,
                        "pending_note_ids": encrypted,
                        "checked_at": self.clock(),
                    }
                )
            if expect_deleted:
                raise JoplinSyncTargetError(
                    "Joplin sync completed but the targeted deleted note(s) remain present",
                    code="JOPLIN_SYNC_TARGET_PRESENT",
                    note_ids=present,
                )
            raise JoplinSyncTargetError(
                "Joplin sync completed but the targeted note(s) are unavailable",
                code="JOPLIN_SYNC_TARGET_MISSING",
                note_ids=missing,
            )

    @staticmethod
    def _assert_note_revision(
        existing: dict[str, Any],
        *,
        base_revision: str | None,
        title: str | None,
        body: str | None,
        base_title: str | None,
        base_body: str | None,
        force: bool,
    ) -> None:
        if force or not base_revision:
            return
        current_revision = str(existing.get("revision") or "")
        if current_revision == base_revision:
            return
        raise JoplinConflictError(
            "Joplin note changed after this edit began",
            note_id=str(existing.get("id") or ""),
            base_revision=base_revision,
            current=existing,
            local_title=title,
            local_body=body,
            base_title=base_title,
            base_body=base_body,
        )

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
            raise JoplinApiError(
                f"Joplin API request failed for {path}: {exc}",
                code="JOPLIN_OFFLINE",
                retryable=True,
            ) from exc
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
        encrypted = JoplinService.note_encrypted(note)
        return {
            "id": str(note.get("id") or ""),
            "parent_id": str(note.get("parent_id") or ""),
            "title": str(note.get("title") or ""),
            "created_time": note.get("created_time"),
            "updated_time": note.get("updated_time"),
            "user_updated_time": note.get("user_updated_time"),
            "encryption_applied": int(encrypted),
            "decryption_pending": encrypted,
        }

    @classmethod
    def _note_response(cls, note: dict[str, Any]) -> dict[str, Any]:
        encrypted = cls.note_encrypted(note)
        response = {
            **cls._note_summary(note),
            # Never expose an encrypted payload as note text. Joplin normally
            # returns an empty body for locked items, but this remains fail-closed
            # if a client version returns serialized ciphertext instead.
            "body": "" if encrypted else str(note.get("body") or ""),
        }
        response["title_hash"] = joplin_note_hash(response["title"])
        response["body_hash"] = joplin_note_hash(response["body"])
        response["revision"] = joplin_note_revision(response)
        return response


class JoplinProfileCoordinator:
    """Serialize CLI sync with the Data API server for one Joplin profile."""

    def __init__(self, service: JoplinService) -> None:
        self.service = service
        self.config = service.config

    def run_sync(self) -> None:
        mode = str(self.config.profile_owner_mode or "external").strip().lower()
        if mode not in JOPLIN_PROFILE_OWNER_MODES:
            raise JoplinApiError(
                f"unsupported Joplin profile owner mode: {mode}",
                code="JOPLIN_PROFILE_OWNER_MODE_INVALID",
                retryable=False,
            )
        profile = self.config.profile
        if profile is None:
            if mode == "managed_cli":
                raise JoplinProfileInUseError(
                    "managed Joplin profile ownership requires an explicit profile path",
                    code="JOPLIN_PROFILE_REQUIRED",
                )
            self.service.sync()
            return

        server_pid = self._validated_server_pid(profile)
        if mode == "external" and server_pid is not None:
            raise JoplinProfileInUseError(
                "the Joplin Data API server is using this profile; stop it before "
                "CLI sync or set managed_cli ownership"
            )
        if mode == "external":
            self.service.sync()
            return

        if server_pid is not None:
            self._stop_server(server_pid)
        sync_error: Exception | None = None
        try:
            self.service.sync()
        except Exception as exc:  # preserve sync failure after server recovery
            sync_error = exc
        restart_error: Exception | None = None
        try:
            self._start_server(profile)
        except Exception as exc:
            restart_error = exc
        if restart_error is not None:
            if sync_error is not None:
                raise JoplinProfileInUseError(
                    f"Joplin sync failed ({sync_error}) and the Data API server "
                    f"could not be restored ({restart_error})",
                    code="JOPLIN_PROFILE_RECOVERY_FAILED",
                ) from restart_error
            raise restart_error
        if sync_error is not None:
            raise sync_error

    def _validated_server_pid(self, profile: Path) -> int | None:
        pid_path = profile.expanduser().resolve() / "clipper-pid.txt"
        try:
            raw_pid = pid_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            if self._api_server_reachable():
                raise JoplinProfileInUseError(
                    "Joplin Data API is reachable but its profile pid cannot be "
                    "verified; refusing to run CLI sync",
                    code="JOPLIN_PROFILE_SERVER_UNVERIFIED",
                )
            return None
        except OSError as exc:
            raise JoplinProfileInUseError(
                f"cannot inspect Joplin Data API pid file: {exc}",
                code="JOPLIN_PROFILE_SERVER_UNVERIFIED",
            ) from exc
        try:
            pid = int(raw_pid)
        except ValueError as exc:
            raise JoplinProfileInUseError(
                "Joplin Data API pid file is invalid",
                code="JOPLIN_PROFILE_SERVER_UNVERIFIED",
            ) from exc
        if pid <= 1 or not self._process_exists(pid):
            return None
        if not self._process_owned_by_user(pid):
            raise JoplinProfileInUseError(
                "Joplin Data API process is not owned by the Agent PBX user",
                code="JOPLIN_PROFILE_SERVER_UNVERIFIED",
            )
        command = self._process_command(pid)
        if not self._server_command_matches(command, profile):
            raise JoplinProfileInUseError(
                "Joplin Data API pid does not identify the configured profile server",
                code="JOPLIN_PROFILE_SERVER_UNVERIFIED",
            )
        return pid

    @staticmethod
    def _process_owned_by_user(pid: int) -> bool:
        try:
            return Path(f"/proc/{pid}").stat().st_uid == os.getuid()
        except OSError:
            pass
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "uid="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0 and int(result.stdout.strip()) == os.getuid()
        except (OSError, subprocess.SubprocessError, ValueError):
            return False

    def _api_server_reachable(self) -> bool:
        if not self.config.api_url:
            return False
        try:
            response = httpx.get(
                f"{str(self.config.api_url).rstrip('/')}/ping",
                timeout=1.0,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and "JoplinClipperServer" in response.text

    @staticmethod
    def _process_exists(pid: int) -> bool:
        try:
            state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[2]
        except (OSError, IndexError):
            state = ""
        if state == "Z":
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @staticmethod
    def _process_command(pid: int) -> list[str]:
        proc_cmdline = Path(f"/proc/{pid}/cmdline")
        try:
            data = proc_cmdline.read_bytes()
        except OSError:
            data = b""
        if data:
            return [part.decode("utf-8", errors="replace") for part in data.split(b"\0") if part]
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        return result.stdout.strip().split()

    def _server_command_matches(self, command: list[str], profile: Path) -> bool:
        if not command:
            return False
        profile_text = str(profile.expanduser().resolve())
        try:
            profile_index = command.index("--profile")
        except ValueError:
            return False
        if profile_index + 1 >= len(command):
            return False
        try:
            command_profile = str(Path(command[profile_index + 1]).expanduser().resolve())
        except OSError:
            return False
        if command_profile != profile_text:
            return False
        if not any(
            command[index : index + 2] == ["server", "start"]
            for index in range(max(0, len(command) - 1))
        ):
            return False
        executable = self.service.sync_command()[0]
        expected = str(Path(executable).expanduser().resolve())
        return any(
            str(Path(part).expanduser().resolve()) == expected
            for part in command
            if part and not part.startswith("-")
        )

    def _stop_server(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + min(max(self.config.timeout_seconds, 5.0), 30.0)
        while self._process_exists(pid):
            if time.monotonic() >= deadline:
                raise JoplinProfileInUseError(
                    "Joplin Data API server did not stop before the safe sync deadline"
                )
            time.sleep(0.1)

    def _start_server(self, profile: Path) -> None:
        executable = self.service.sync_command()[0]
        profile = profile.expanduser().resolve()
        log_path = profile / "agent-pbx-server.log"
        profile.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            try:
                subprocess.Popen(
                    [executable, "--profile", str(profile), "server", "start"],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as exc:
                raise JoplinProfileInUseError(
                    f"unable to restart Joplin Data API server: {exc}",
                    code="JOPLIN_PROFILE_RECOVERY_FAILED",
                ) from exc
        deadline = time.monotonic() + min(max(self.config.timeout_seconds, 5.0), 30.0)
        last_error = "server did not answer"
        while time.monotonic() < deadline:
            try:
                url = f"{str(self.config.api_url).rstrip('/')}/ping"
                response = httpx.get(url, timeout=1.0)
                if (
                    response.status_code == 200
                    and "JoplinClipperServer" in response.text
                ):
                    return
                last_error = f"HTTP {response.status_code}"
            except httpx.HTTPError as exc:
                last_error = str(exc)
            time.sleep(0.2)
        raise JoplinProfileInUseError(
            f"Joplin Data API server did not recover after sync: {last_error}",
            code="JOPLIN_PROFILE_RECOVERY_FAILED",
        )


class JoplinGateway:
    def __init__(
        self,
        store: Any,
        service: JoplinService,
        *,
        sync_on_write: bool | None = None,
        clock: Any = time.time,
        profile_coordinator: JoplinProfileCoordinator | None = None,
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
        self.profile_coordinator = profile_coordinator or JoplinProfileCoordinator(service)
        profile_source = str(
            getattr(service.config, "profile", None)
            or getattr(service.config, "api_url", None)
            or "default"
        )
        self.profile_key = hashlib.sha256(profile_source.encode("utf-8")).hexdigest()[:16]
        store_path = Path(getattr(store, "path", Path.cwd() / "agent-pbx.sqlite"))
        self.sync_lock_path = store_path.parent / f"joplin-sync-{self.profile_key}.lock"

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
            profile_key=self.profile_key,
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
        self.sync_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.sync_lock_path.open("a+", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise JoplinSyncLockError(
                    f"another Joplin sync owns profile {self.profile_key}"
                ) from exc
            lock_file.seek(0)
            lock_file.truncate()
            lock_file.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "sync_id": str(job.get("sync_id") or ""),
                        "profile_key": self.profile_key,
                        "started_at": self.clock(),
                    },
                    sort_keys=True,
                )
            )
            lock_file.flush()
            try:
                self.profile_coordinator.run_sync()
                note_ids = [
                    str(note_id)
                    for note_id in job.get("note_ids", [])
                    if str(note_id).strip()
                ]
                if not note_ids and job.get("note_id"):
                    note_ids = [str(job["note_id"])]
                reasons = {
                    str(reason)
                    for reason in job.get("reasons", [])
                    if str(reason).strip()
                }
                if not reasons and job.get("reason"):
                    reasons = {str(job["reason"])}
                if note_ids:
                    delete_reasons = reasons & JOPLIN_DELETE_SYNC_REASONS
                    if delete_reasons and delete_reasons != reasons:
                        raise JoplinApiError(
                            "Joplin sync job mixes deleted and retained note targets",
                            code="JOPLIN_SYNC_TARGET_AMBIGUOUS",
                            retryable=False,
                        )
                    self.service.wait_for_sync_targets(
                        note_ids,
                        expect_deleted=bool(delete_reasons),
                    )
                    # Keep global E2EE health current for the status surface, but
                    # do not fail a targeted write because an unrelated item is
                    # still encrypted elsewhere in the profile.
                    try:
                        self.service.encryption_status(force=True)
                    except JoplinApiError:
                        pass
                else:
                    # Manual/profile-wide sync remains strict: it is the operator's
                    # explicit whole-profile consistency check.
                    self.service.wait_for_decryption()
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def list_notes_for_agent(self, agent: dict[str, Any]) -> list[dict[str, Any]]:
        return self.service.list_notes_for_agent(agent)

    def list_notes_for_project(self, project: str) -> list[dict[str, Any]]:
        return self.service.list_notes_for_project(project)

    def get_note_for_agent(self, agent: dict[str, Any], note_id: str) -> dict[str, Any]:
        return self.service.get_note_for_agent(agent, note_id)

    def get_note_for_project(self, project: str, note_id: str) -> dict[str, Any]:
        return self.service.get_note_for_project(project, note_id)

    def begin_edit_for_agent(
        self,
        agent: dict[str, Any],
        note_id: str,
        *,
        client_id: str,
    ) -> dict[str, Any]:
        note = self.service.get_note_for_agent(agent, note_id)
        self.service.assert_note_mutable(note)
        edit = self.store.begin_joplin_edit(
            client_id=client_id,
            scope_key=f"agent:{agent.get('agent_id')}",
            agent_id=str(agent.get("agent_id") or "") or None,
            project=str(agent.get("project") or "") or None,
            note=note,
        )
        return {
            "edit": edit,
            "note": note,
            "conflict": self.store.get_open_joplin_conflict_for_edit(
                str(edit["edit_id"])
            ),
        }

    def begin_edit_for_project(
        self,
        project: str,
        note_id: str,
        *,
        client_id: str,
    ) -> dict[str, Any]:
        note = self.service.get_note_for_project(project, note_id)
        self.service.assert_note_mutable(note)
        edit = self.store.begin_joplin_edit(
            client_id=client_id,
            scope_key=f"project:{project}",
            project=project,
            note=note,
        )
        return {
            "edit": edit,
            "note": note,
            "conflict": self.store.get_open_joplin_conflict_for_edit(
                str(edit["edit_id"])
            ),
        }

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
        base_revision: str | None = None,
        base_title: str | None = None,
        base_body: str | None = None,
        force: bool = False,
        edit_id: str | None = None,
    ) -> dict[str, Any]:
        edit = self._prepare_edit(
            edit_id,
            note_id=note_id,
            title=title,
            body=body,
            base_revision=base_revision,
            base_title=base_title,
            base_body=base_body,
        )
        try:
            update_kwargs: dict[str, Any] = {"title": title, "body": body}
            if edit is not None or base_revision is not None or force:
                update_kwargs.update(
                    base_revision=str(edit.get("base_revision")) if edit else base_revision,
                    base_title=str(edit.get("base_title")) if edit else base_title,
                    base_body=str(edit.get("base_body")) if edit else base_body,
                    force=force,
                )
            note = self.service.update_note_for_project(
                project,
                note_id,
                **update_kwargs,
            )
        except JoplinConflictError as exc:
            self._record_conflict(exc, edit, title=title, body=body)
            raise
        if title is not None or body is not None:
            self._enqueue_write_sync("project_note_update", note_id=note_id)
        if edit_id:
            self.store.complete_joplin_edit(edit_id, status="saved")
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
        base_revision: str | None = None,
        base_title: str | None = None,
        base_body: str | None = None,
        force: bool = False,
        edit_id: str | None = None,
    ) -> dict[str, Any]:
        edit = self._prepare_edit(
            edit_id,
            note_id=note_id,
            title=title,
            body=body,
            base_revision=base_revision,
            base_title=base_title,
            base_body=base_body,
        )
        try:
            update_kwargs = {"title": title, "body": body}
            if edit is not None or base_revision is not None or force:
                update_kwargs.update(
                    base_revision=str(edit.get("base_revision")) if edit else base_revision,
                    base_title=str(edit.get("base_title")) if edit else base_title,
                    base_body=str(edit.get("base_body")) if edit else base_body,
                    force=force,
                )
            note = self.service.update_note_for_agent(
                agent,
                note_id,
                **update_kwargs,
            )
        except JoplinConflictError as exc:
            self._record_conflict(exc, edit, title=title, body=body)
            raise
        if title is not None or body is not None:
            self._enqueue_write_sync("note_update", agent=agent, note_id=note_id)
        if edit_id:
            self.store.complete_joplin_edit(edit_id, status="saved")
        return note

    def resolve_edit_conflict(
        self,
        conflict_id: str,
        *,
        resolution: str,
        agent: dict[str, Any] | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        conflict = self.store.get_joplin_conflict(conflict_id)
        if conflict is None or conflict.get("status") != "open":
            raise ValueError("Joplin conflict is not open")
        edit = self.store.get_joplin_edit(str(conflict["edit_id"]))
        if edit is None:
            raise ValueError("Joplin edit session does not exist")
        note_id = str(conflict["note_id"])
        if agent is not None:
            current = self.service.get_note_for_agent(agent, note_id)
        elif project:
            current = self.service.get_note_for_project(project, note_id)
        else:
            raise ValueError("Joplin conflict resolution requires a scope")
        if resolution == "review_merge":
            self.store.rebase_joplin_edit(
                str(edit["edit_id"]),
                current_note=current,
                draft_title=str(conflict["merged_title"]),
                draft_body=str(conflict["merged_body"]),
            )
            self.store.resolve_joplin_conflict(conflict_id, resolution=resolution)
            return {
                "resolution": resolution,
                "note": current,
                "edit": self.store.get_joplin_edit(str(edit["edit_id"])),
                "draft": {
                    "title": str(conflict["merged_title"]),
                    "body": str(conflict["merged_body"]),
                },
            }
        if resolution == "keep_joplin":
            self.store.complete_joplin_edit(str(edit["edit_id"]), status="discarded")
            self.store.resolve_joplin_conflict(conflict_id, resolution=resolution)
            return {"resolution": resolution, "note": current}
        if resolution == "save_conflict_copy":
            conflict_title = f"{conflict['local_title']} (conflict copy)"
            if agent is not None:
                copy_note = self.create_note_for_agent(
                    agent,
                    event_type="CONFLICT",
                    title=conflict_title,
                    body=str(conflict["local_body"]),
                )
            else:
                copy_note = self.create_note_for_project(
                    str(project),
                    title=conflict_title,
                    body=str(conflict["local_body"]),
                )
            self.store.complete_joplin_edit(str(edit["edit_id"]), status="conflict_copy")
            self.store.resolve_joplin_conflict(conflict_id, resolution=resolution)
            return {"resolution": resolution, "note": current, "conflict_copy": copy_note}
        if resolution == "overwrite":
            if agent is not None:
                note = self.service.update_note_for_agent(
                    agent,
                    note_id,
                    title=str(conflict["local_title"]),
                    body=str(conflict["local_body"]),
                    force=True,
                )
                self._enqueue_write_sync("note_conflict_overwrite", agent=agent, note_id=note_id)
            else:
                note = self.service.update_note_for_project(
                    str(project),
                    note_id,
                    title=str(conflict["local_title"]),
                    body=str(conflict["local_body"]),
                    force=True,
                )
                self._enqueue_write_sync("project_note_conflict_overwrite", note_id=note_id)
            self.store.complete_joplin_edit(str(edit["edit_id"]), status="overwritten")
            self.store.resolve_joplin_conflict(conflict_id, resolution=resolution)
            self.store.append_event(
                "joplin_conflict_overwritten",
                {"conflict_id": conflict_id, "note_id": note_id},
                str(agent.get("agent_id") if agent else project or note_id),
            )
            return {"resolution": resolution, "note": note}
        raise ValueError(f"unsupported Joplin conflict resolution: {resolution}")

    def _prepare_edit(
        self,
        edit_id: str | None,
        *,
        note_id: str,
        title: str | None,
        body: str | None,
        base_revision: str | None,
        base_title: str | None,
        base_body: str | None,
    ) -> dict[str, Any] | None:
        if not edit_id:
            return None
        edit = self.store.get_joplin_edit(edit_id)
        if edit is None or str(edit.get("note_id")) != note_id:
            raise ValueError("Joplin edit session is stale or belongs to another note")
        if edit.get("status") not in {"editing", "conflict"}:
            raise ValueError("Joplin edit session is no longer active")
        local_title = title if title is not None else str(edit.get("draft_title") or "")
        local_body = body if body is not None else str(edit.get("draft_body") or "")
        self.store.update_joplin_edit_draft(
            edit_id,
            title=local_title,
            body=local_body,
        )
        return self.store.get_joplin_edit(edit_id)

    def _record_conflict(
        self,
        exc: JoplinConflictError,
        edit: dict[str, Any] | None,
        *,
        title: str | None,
        body: str | None,
    ) -> None:
        if edit is None:
            return
        local_title = title if title is not None else str(edit.get("draft_title") or "")
        local_body = body if body is not None else str(edit.get("draft_body") or "")
        merged_title, _ = merge_joplin_text(
            str(edit.get("base_title") or ""),
            local_title,
            str(exc.current.get("title") or ""),
        )
        merged_body, _ = merge_joplin_text(
            str(edit.get("base_body") or ""),
            local_body,
            str(exc.current.get("body") or ""),
        )
        conflict = self.store.record_joplin_conflict(
            edit_id=str(edit["edit_id"]),
            current_note=exc.current,
            local_title=local_title,
            local_body=local_body,
            merged_title=merged_title,
            merged_body=merged_body,
        )
        exc.conflict_id = str(conflict["conflict_id"])
        exc.merged_title = merged_title
        exc.merged_body = merged_body

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
    profile_owner_mode = (
        env_text_value(JOPLIN_PROFILE_OWNER_MODE_ENV) or "external"
    ).lower()
    if profile_owner_mode not in JOPLIN_PROFILE_OWNER_MODES:
        profile_owner_mode = "external"
    return JoplinConfig(
        api_url=env_text_value(JOPLIN_API_URL_ENV),
        token=env_text_value(JOPLIN_TOKEN_ENV),
        notebook=env_text_value(JOPLIN_NOTEBOOK_ENV) or DEFAULT_JOPLIN_NOTEBOOK,
        joplin_bin=env_path_value(JOPLIN_BIN_ENV),
        profile=env_path_value(JOPLIN_PROFILE_ENV),
        timeout_seconds=env_float(JOPLIN_TIMEOUT_ENV, DEFAULT_JOPLIN_TIMEOUT_SECONDS),
        sync_on_write=env_flag(JOPLIN_SYNC_ON_WRITE_ENV),
        profile_owner_mode=profile_owner_mode,
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
