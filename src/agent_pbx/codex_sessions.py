from __future__ import annotations

from dataclasses import dataclass, replace
import fcntl
import json
import os
from pathlib import Path
import threading
import time
from typing import Any


_ASSISTANT_MESSAGE_TYPES = {"message"}
_ASSISTANT_TEXT_TYPES = {"output_text", "text"}
DEFAULT_SESSION_PATH_CACHE_TTL_SECONDS = 5.0


@dataclass(frozen=True)
class CodexTranscriptResult:
    text: str
    session_id: str
    path: Path
    phase: str
    line_index: int
    mtime: float
    prompt: str = ""


@dataclass(frozen=True)
class CodexTranscriptBoundary:
    session_id: str
    path: Path
    line_index: int
    mtime: float


@dataclass
class _TranscriptTailEntry:
    path: Path
    inode: int
    offset: int
    line_index: int
    mtime_ns: int
    session_id: str
    preferred: CodexTranscriptResult | None
    fallback: CodexTranscriptResult | None
    latest_user_prompt: str


class CodexTranscriptTailCache:
    """Incrementally retain the latest assistant output for active JSONL files.

    Session-path lookup and transcript parsing have distinct costs.  This cache
    avoids repeatedly reading an already-known active rollout from byte zero while
    leaving path discovery to :class:`CodexSessionPathCache`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[Path, _TranscriptTailEntry] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries = {}

    def latest(
        self,
        path: str | Path,
        *,
        session_id: str | None = None,
        preferred_phases: tuple[str, ...] = ("final_answer",),
        fallback_to_any: bool = True,
    ) -> CodexTranscriptResult | None:
        transcript_path = Path(path)
        try:
            stat = transcript_path.stat()
        except OSError:
            return None
        resolved_session_id = str(session_id or "").strip()
        with self._lock:
            entry = self._entries.get(transcript_path)
            requires_full_scan = (
                entry is None
                or entry.inode != stat.st_ino
                or stat.st_size < entry.offset
                or (
                    stat.st_size == entry.offset
                    and stat.st_mtime_ns != entry.mtime_ns
                )
            )
            if requires_full_scan:
                entry = _scan_transcript_tail(
                    transcript_path,
                    stat=stat,
                    session_id=resolved_session_id,
                    preferred_phases=preferred_phases,
                )
            elif stat.st_size > entry.offset:
                entry = _scan_transcript_tail(
                    transcript_path,
                    stat=stat,
                    session_id=resolved_session_id or entry.session_id,
                    preferred_phases=preferred_phases,
                    entry=entry,
                )
            else:
                entry = replace(entry, mtime_ns=stat.st_mtime_ns)
            self._entries[transcript_path] = entry
            result = entry.preferred or (entry.fallback if fallback_to_any else None)
            if result is None:
                return None
            effective_session_id = resolved_session_id or result.session_id
            return replace(
                result,
                session_id=effective_session_id,
                mtime=stat.st_mtime,
            )


def _scan_transcript_tail(
    path: Path,
    *,
    stat: os.stat_result,
    session_id: str,
    preferred_phases: tuple[str, ...],
    entry: _TranscriptTailEntry | None = None,
) -> _TranscriptTailEntry:
    offset = entry.offset if entry is not None else 0
    line_index = entry.line_index if entry is not None else -1
    file_session_id = session_id or (entry.session_id if entry is not None else "")
    preferred = entry.preferred if entry is not None else None
    fallback = entry.fallback if entry is not None else None
    latest_user_prompt = entry.latest_user_prompt if entry is not None else ""
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            line_offset = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            # A live Codex rollout may be observed while its final JSONL record is
            # still being written.  Leave that partial record for the next read.
            if not raw_line.endswith(b"\n"):
                offset = line_offset
                break
            offset = handle.tell()
            line_index += 1
            try:
                record = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "session_meta":
                payload = record.get("payload")
                if isinstance(payload, dict) and not file_session_id:
                    file_session_id = str(payload.get("id") or "").strip()
                continue
            user_prompt = _user_message_text(record)
            if user_prompt.strip():
                latest_user_prompt = user_prompt
                continue
            text = _assistant_message_text(record)
            if not text.strip():
                continue
            result = CodexTranscriptResult(
                text=text,
                session_id=file_session_id,
                path=path,
                phase=_record_phase(record),
                line_index=line_index,
                mtime=stat.st_mtime,
                prompt=latest_user_prompt,
            )
            if result.phase in preferred_phases:
                preferred = result
            else:
                fallback = result
    return _TranscriptTailEntry(
        path=path,
        inode=stat.st_ino,
        offset=offset,
        line_index=line_index,
        mtime_ns=stat.st_mtime_ns,
        session_id=file_session_id,
        preferred=preferred,
        fallback=fallback,
        latest_user_prompt=latest_user_prompt,
    )


class CodexSessionPathCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_SESSION_PATH_CACHE_TTL_SECONDS,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._root: Path | None = None
        self._max_files = 0
        self._scanned_at = 0.0
        self._paths_by_session_id: dict[str, Path] = {}
        self._pinned_paths_by_session_id: dict[str, Path] = {}
        self._files: list[tuple[float, Path]] = []

    def find(
        self,
        session_id: str,
        *,
        codex_home: str | Path | None = None,
        max_files: int = 2000,
    ) -> Path | None:
        cleaned = str(session_id or "").strip()
        if not cleaned:
            return None
        root = Path(codex_home).expanduser() if codex_home else default_codex_home()
        sessions_dir = root / "sessions"
        if not sessions_dir.is_dir():
            return None
        now = time.monotonic()
        with self._lock:
            pinned = self._pinned_paths_by_session_id.get(cleaned)
            if pinned is not None:
                if pinned.is_file():
                    return pinned
                self._pinned_paths_by_session_id.pop(cleaned, None)
            if self._cache_stale(root, max_files, now):
                self._refresh(sessions_dir, root=root, max_files=max_files, now=now)
            path = self._paths_by_session_id.get(cleaned)
            if path is not None:
                return path
            for _mtime, candidate in self._files:
                if cleaned in candidate.name:
                    return candidate
            self._refresh(sessions_dir, root=root, max_files=max_files, now=now)
            path = self._paths_by_session_id.get(cleaned)
            if path is not None:
                return path
            for _mtime, candidate in self._files:
                if cleaned in candidate.name:
                    return candidate
        return None

    def pin(self, session_id: str, path: str | Path) -> bool:
        """Pin a live session path so steady-state reads avoid global discovery."""
        cleaned = str(session_id or "").strip()
        candidate = Path(path).expanduser()
        if not cleaned or not candidate.is_file():
            return False
        with self._lock:
            self._pinned_paths_by_session_id[cleaned] = candidate
            self._paths_by_session_id[cleaned] = candidate
        return True

    def unpin(self, session_id: str) -> None:
        cleaned = str(session_id or "").strip()
        if not cleaned:
            return
        with self._lock:
            self._pinned_paths_by_session_id.pop(cleaned, None)

    def clear(self) -> None:
        with self._lock:
            self._root = None
            self._max_files = 0
            self._scanned_at = 0.0
            self._paths_by_session_id = {}
            self._pinned_paths_by_session_id = {}
            self._files = []

    def _cache_stale(self, root: Path, max_files: int, now: float) -> bool:
        return (
            self._root != root
            or self._max_files != max_files
            or not self._files
            or now - self._scanned_at > self.ttl_seconds
        )

    def _refresh(
        self,
        sessions_dir: Path,
        *,
        root: Path,
        max_files: int,
        now: float,
    ) -> None:
        candidates: list[tuple[float, Path]] = []
        for path in sessions_dir.rglob("*.jsonl"):
            try:
                candidates.append((path.stat().st_mtime, path))
            except OSError:
                continue
        self._root = root
        self._max_files = max_files
        self._scanned_at = now
        self._files = sorted(candidates, key=lambda item: item[0], reverse=True)[
            :max_files
        ]
        paths_by_session_id: dict[str, Path] = {}
        for _mtime, path in self._files:
            session_id = str(_read_session_meta(path).get("id") or "").strip()
            if session_id and session_id not in paths_by_session_id:
                paths_by_session_id[session_id] = path
        self._paths_by_session_id = paths_by_session_id


_DEFAULT_SESSION_PATH_CACHE = CodexSessionPathCache()


def default_codex_home() -> Path:
    configured = os.getenv("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def codex_thread_writer_lock_path(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
) -> Path:
    """Return Codex's process-coordination lock for one thread."""

    cleaned = str(session_id or "").strip()
    if not cleaned:
        raise ValueError("Codex session ID is required")
    root = Path(codex_home).expanduser() if codex_home else default_codex_home()
    return root / "thread-writer-locks" / f"{cleaned}.lock"


def codex_thread_writer_lock_available(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
) -> bool:
    """Return whether Codex's advisory writer lock can be acquired now.

    The lock file may persist after a process exits, so existence alone is not a
    lease signal.  Open the existing inode without creating or replacing it and
    probe the same exclusive ``flock`` used by Codex.  A missing file is free;
    an inode created immediately afterwards will still be caught by the stable
    wait helper before migration proceeds.
    """

    path = codex_thread_writer_lock_path(session_id, codex_home=codex_home)
    try:
        descriptor = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except FileNotFoundError:
        return True
    try:
        acquired = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            return False
        finally:
            if acquired:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(descriptor)
    return True


def wait_for_codex_thread_writer_lock_release(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
    timeout_seconds: float = 30.0,
    stable_seconds: float = 2.0,
    poll_seconds: float = 0.25,
) -> bool:
    """Wait until the Codex writer lock remains free for a stable interval."""

    timeout = max(0.0, float(timeout_seconds))
    stable = max(0.0, float(stable_seconds))
    poll = max(0.01, float(poll_seconds))
    deadline = time.monotonic() + timeout
    available_since: float | None = None
    while True:
        now = time.monotonic()
        if codex_thread_writer_lock_available(session_id, codex_home=codex_home):
            if available_since is None:
                available_since = now
            if now - available_since >= stable:
                return True
        else:
            available_since = None
        if now >= deadline:
            return False
        time.sleep(min(poll, max(0.0, deadline - now)))


def enrich_codex_session_metadata(
    metadata: dict[str, Any],
    *,
    codex_home: str | Path | None = None,
) -> dict[str, Any]:
    enriched = dict(metadata)
    if enriched.get("codex_session_id") and enriched.get("codex_thread_id"):
        return enriched
    cwd = str(enriched.get("cwd") or "").strip()
    if not cwd:
        return enriched
    inferred = infer_latest_codex_session_metadata(cwd, codex_home=codex_home)
    if not inferred:
        return enriched
    if not enriched.get("codex_session_id"):
        enriched["codex_session_id"] = inferred["codex_session_id"]
    if not enriched.get("codex_thread_id"):
        enriched["codex_thread_id"] = inferred["codex_thread_id"]
    return enriched


def infer_latest_codex_session_metadata(
    cwd: str,
    *,
    codex_home: str | Path | None = None,
    max_files: int = 1000,
) -> dict[str, str]:
    target = _normalized_path(cwd)
    if not target:
        return {}
    root = Path(codex_home).expanduser() if codex_home else default_codex_home()
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return {}
    candidates: list[tuple[float, Path]] = []
    for path in sessions_dir.rglob("*.jsonl"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_files]:
        session = _read_session_meta(path)
        if not session:
            continue
        if _normalized_path(str(session.get("cwd") or "")) != target:
            continue
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            continue
        return {
            "codex_session_id": session_id,
            "codex_thread_id": session_id,
        }
    return {}


def find_codex_session_file(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
    max_files: int = 2000,
    path_cache: CodexSessionPathCache | None = None,
) -> Path | None:
    cleaned = str(session_id or "").strip()
    if not cleaned:
        return None
    cache = path_cache or _DEFAULT_SESSION_PATH_CACHE
    if cache is not None:
        return cache.find(cleaned, codex_home=codex_home, max_files=max_files)
    root = Path(codex_home).expanduser() if codex_home else default_codex_home()
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for path in sessions_dir.rglob("*.jsonl"):
        if cleaned not in path.name:
            continue
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _, path in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_files]:
        return path
    return None


def latest_assistant_output_from_session_file(
    path: str | Path,
    *,
    preferred_phases: tuple[str, ...] = ("final_answer",),
    fallback_to_any: bool = True,
) -> str:
    result = latest_assistant_transcript_from_session_file(
        path,
        preferred_phases=preferred_phases,
        fallback_to_any=fallback_to_any,
    )
    return result.text if result is not None else ""


def latest_assistant_transcript_from_session_file(
    path: str | Path,
    *,
    session_id: str | None = None,
    preferred_phases: tuple[str, ...] = ("final_answer",),
    fallback_to_any: bool = True,
    tail_cache: CodexTranscriptTailCache | None = None,
) -> CodexTranscriptResult | None:
    transcript_path = Path(path)
    if tail_cache is not None:
        return tail_cache.latest(
            transcript_path,
            session_id=session_id,
            preferred_phases=preferred_phases,
            fallback_to_any=fallback_to_any,
        )
    try:
        mtime = transcript_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    file_session_id = str(session_id or "").strip()
    preferred: list[CodexTranscriptResult] = []
    fallback: list[CodexTranscriptResult] = []
    latest_user_prompt = ""
    try:
        handle = transcript_path.open("r", encoding="utf-8")
    except OSError:
        return None
    with handle:
        for index, line in enumerate(handle):
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            if record.get("type") == "session_meta":
                session = record.get("payload")
                if isinstance(session, dict) and not file_session_id:
                    file_session_id = str(session.get("id") or "").strip()
                continue
            user_prompt = _user_message_text(record)
            if user_prompt.strip():
                latest_user_prompt = user_prompt
                continue
            text = _assistant_message_text(record)
            if not text.strip():
                continue
            phase = _record_phase(record)
            result = CodexTranscriptResult(
                text=text,
                session_id=file_session_id,
                path=transcript_path,
                phase=phase,
                line_index=index,
                mtime=mtime,
                prompt=latest_user_prompt,
            )
            if phase in preferred_phases:
                preferred.append(result)
            else:
                fallback.append(result)
    if preferred:
        return preferred[-1]
    if fallback_to_any and fallback:
        return fallback[-1]
    return None


def latest_assistant_output_for_session(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
    path_cache: CodexSessionPathCache | None = None,
) -> tuple[str, Path] | None:
    result = latest_assistant_transcript_for_session(
        session_id,
        codex_home=codex_home,
        path_cache=path_cache,
    )
    if result is None:
        return None
    return result.text, result.path


def latest_assistant_transcript_for_session(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
    path_cache: CodexSessionPathCache | None = None,
    tail_cache: CodexTranscriptTailCache | None = None,
) -> CodexTranscriptResult | None:
    path = find_codex_session_file(
        session_id,
        codex_home=codex_home,
        path_cache=path_cache,
    )
    if path is None:
        return None
    result = latest_assistant_transcript_from_session_file(
        path,
        session_id=session_id,
        tail_cache=tail_cache,
    )
    if result is None or not result.text.strip():
        return None
    if not result.session_id:
        result = replace(result, session_id=session_id)
    return result


def codex_session_transcript_boundary(
    session_id: str,
    *,
    codex_home: str | Path | None = None,
    path_cache: CodexSessionPathCache | None = None,
) -> CodexTranscriptBoundary | None:
    path = find_codex_session_file(
        session_id,
        codex_home=codex_home,
        path_cache=path_cache,
    )
    if path is None:
        return None
    try:
        mtime = path.stat().st_mtime
        with path.open("r", encoding="utf-8") as handle:
            line_index = -1
            for line_index, _line in enumerate(handle):
                pass
    except OSError:
        return None
    return CodexTranscriptBoundary(
        session_id=str(session_id).strip(),
        path=path,
        line_index=line_index,
        mtime=mtime,
    )


def _read_session_meta(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first_line = handle.readline()
    except OSError:
        return {}
    if not first_line:
        return {}
    try:
        payload = json.loads(first_line)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("type") != "session_meta":
        return {}
    session = payload.get("payload")
    return session if isinstance(session, dict) else {}


def codex_session_metadata_from_file(path: str | Path) -> dict[str, Any]:
    """Return the rollout's session metadata without reading response content."""
    return dict(_read_session_meta(Path(path)))


def codex_session_id_from_file(path: str | Path) -> str:
    return str(codex_session_metadata_from_file(path).get("id") or "").strip()


def _record_phase(record: dict[str, Any]) -> str:
    phase = str(record.get("phase") or "").strip()
    if phase:
        return phase
    payload = record.get("payload")
    if isinstance(payload, dict):
        return str(payload.get("phase") or "").strip()
    return ""


def _assistant_message_text(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    if str(record.get("type") or "") != "response_item":
        return ""
    if str(payload.get("type") or "") not in _ASSISTANT_MESSAGE_TYPES:
        return ""
    if str(payload.get("role") or "") != "assistant":
        return ""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "") not in _ASSISTANT_TEXT_TYPES:
            continue
        text = str(item.get("text") or "")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _user_message_text(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return ""
    record_type = str(record.get("type") or "")
    payload_type = str(payload.get("type") or "")
    if record_type == "event_msg" and payload_type == "user_message":
        return str(payload.get("message") or "").strip()
    if (
        record_type != "response_item"
        or payload_type not in _ASSISTANT_MESSAGE_TYPES
        or str(payload.get("role") or "") != "user"
    ):
        return ""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type not in {"input_text", "text"}:
            continue
        text = str(item.get("text") or "")
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _normalized_path(value: str) -> str:
    if not value.strip():
        return ""
    return str(Path(value).expanduser().resolve(strict=False))
