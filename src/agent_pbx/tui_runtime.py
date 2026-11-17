from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
import traceback
from typing import Iterator, TextIO
from urllib.parse import urlparse
import uuid

from .paths import default_state_root


TUI_RUNTIME_ROOT_ENV = "AGENT_PBX_TUI_RUNTIME_ROOT"
TUI_RUNTIME_JOURNAL_MAX_BYTES_ENV = "AGENT_PBX_TUI_RUNTIME_JOURNAL_MAX_BYTES"
TUI_RUNTIME_JOURNAL_BACKUPS_ENV = "AGENT_PBX_TUI_RUNTIME_JOURNAL_BACKUPS"
TUI_RUNTIME_STDERR_MAX_BYTES_ENV = "AGENT_PBX_TUI_STDERR_MAX_BYTES"
TUI_RUNTIME_STDERR_RETAIN_ENV = "AGENT_PBX_TUI_STDERR_RETAIN"

DEFAULT_TUI_RUNTIME_JOURNAL_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_TUI_RUNTIME_JOURNAL_BACKUPS = 3
DEFAULT_TUI_RUNTIME_STDERR_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_TUI_RUNTIME_STDERR_RETAIN = 20

_BEARER_PATTERN = re.compile(
    r"(?i)(authorization\s*[:=]\s*bearer\s+)([^\s\"']+)"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:token|access_token|api_key|key)=)([^&\s]+)"
)
_LABELED_SECRET_PATTERN = re.compile(
    r"(?i)((?:token|access_token|api_key)\s*[:=]\s*[\"']?)([^\s\"']+)"
)


def _bounded_env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(str(os.getenv(name) or "").strip())
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def default_tui_runtime_root() -> Path:
    override = str(os.getenv(TUI_RUNTIME_ROOT_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return default_state_root() / "tui-runtime"


def _process_start_ticks(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = raw.rsplit(")", maxsplit=1)[1].strip().split()
        return int(fields[19])
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def _process_identity_is_live(pid: int, start_ticks: int | None) -> bool:
    current = _process_start_ticks(pid)
    if current is None:
        return False
    return start_ticks is None or current == start_ticks


def _private_mkdir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    _private_mkdir(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _rotate_file(path: Path, *, max_bytes: int, backups: int) -> None:
    try:
        if path.stat().st_size < max_bytes:
            return
    except FileNotFoundError:
        return
    if backups <= 0:
        path.unlink(missing_ok=True)
        return
    path.with_name(f"{path.name}.{backups}").unlink(missing_ok=True)
    for index in range(backups - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            source.replace(path.with_name(f"{path.name}.{index + 1}"))
    path.replace(path.with_name(f"{path.name}.1"))


def _append_private_jsonl(path: Path, payload: dict[str, object]) -> None:
    _private_mkdir(path.parent)
    data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, data)
    finally:
        os.close(descriptor)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _server_origin(server: str) -> str:
    parsed = urlparse(server)
    if not parsed.scheme or not parsed.hostname:
        return "configured"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{host}{port}"


def redact_tui_runtime_text(text: str, *, secrets: tuple[str, ...] = ()) -> str:
    redacted = str(text)
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    redacted = _BEARER_PATTERN.sub(r"\1<redacted>", redacted)
    redacted = _QUERY_SECRET_PATTERN.sub(r"\1<redacted>", redacted)
    return _LABELED_SECRET_PATTERN.sub(r"\1<redacted>", redacted)


class _RedactingStderr:
    def __init__(
        self,
        original: TextIO,
        destination: TextIO,
        *,
        secrets: tuple[str, ...],
        max_bytes: int,
    ) -> None:
        self.original = original
        self.destination = destination
        self.secrets = secrets
        self.max_bytes = max_bytes
        self.pending = ""
        self.written = 0
        self.truncated = False

    @property
    def encoding(self) -> str:
        return str(getattr(self.original, "encoding", None) or "utf-8")

    @property
    def errors(self) -> str:
        return str(getattr(self.original, "errors", None) or "replace")

    def fileno(self) -> int:
        return self.original.fileno()

    def isatty(self) -> bool:
        return self.original.isatty()

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        written = self.original.write(text)
        self.pending += str(text)
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", maxsplit=1)
            self._write_private(f"{line}\n")
        if len(self.pending) > 65536:
            self._write_private(self.pending)
            self.pending = ""
        return written

    def flush(self) -> None:
        self.original.flush()
        if self.pending:
            self._write_private(self.pending)
            self.pending = ""
        self.destination.flush()

    def close_private(self) -> None:
        self.flush()
        self.destination.close()

    def _write_private(self, text: str) -> None:
        if self.truncated:
            return
        redacted = redact_tui_runtime_text(text, secrets=self.secrets)
        encoded = redacted.encode("utf-8", errors="replace")
        remaining = self.max_bytes - self.written
        if remaining <= 0:
            self._mark_truncated()
            return
        if len(encoded) > remaining:
            bounded = encoded[:remaining].decode("utf-8", errors="ignore")
            self.destination.write(bounded)
            self.written += len(bounded.encode("utf-8"))
            self._mark_truncated()
            return
        self.destination.write(redacted)
        self.written += len(encoded)

    def _mark_truncated(self) -> None:
        if self.truncated:
            return
        self.destination.write("\n[agent-pbx: TUI stderr capture truncated]\n")
        self.truncated = True


@dataclass
class TuiRunRecorder:
    root: Path
    run_id: str
    pid: int
    process_start_ticks: int | None
    started_at: float
    server_origin: str
    marker_path: Path
    journal_path: Path
    stderr_path: Path
    secrets: tuple[str, ...]
    journal_max_bytes: int
    journal_backups: int
    stderr_max_bytes: int
    finished: bool = False

    @classmethod
    def start(
        cls,
        *,
        server: str,
        token: str | None,
        root: Path | None = None,
        pid: int | None = None,
        process_start_ticks: int | None = None,
    ) -> "TuiRunRecorder":
        resolved_root = (root or default_tui_runtime_root()).expanduser().resolve(
            strict=False
        )
        _private_mkdir(resolved_root)
        markers = resolved_root / "active"
        stderr_dir = resolved_root / "stderr"
        unclean_dir = resolved_root / "unclean"
        for directory in (markers, stderr_dir, unclean_dir):
            _private_mkdir(directory)

        journal_path = resolved_root / "events.jsonl"
        journal_max_bytes = _bounded_env_int(
            TUI_RUNTIME_JOURNAL_MAX_BYTES_ENV,
            DEFAULT_TUI_RUNTIME_JOURNAL_MAX_BYTES,
            minimum=64 * 1024,
            maximum=1024 * 1024 * 1024,
        )
        journal_backups = _bounded_env_int(
            TUI_RUNTIME_JOURNAL_BACKUPS_ENV,
            DEFAULT_TUI_RUNTIME_JOURNAL_BACKUPS,
            minimum=0,
            maximum=20,
        )
        _rotate_file(
            journal_path,
            max_bytes=journal_max_bytes,
            backups=journal_backups,
        )

        current_pid = int(pid if pid is not None else os.getpid())
        current_ticks = (
            process_start_ticks
            if process_start_ticks is not None
            else _process_start_ticks(current_pid)
        )
        now = time.time()
        origin = _server_origin(server)
        cls._reap_unclean_markers(
            markers,
            unclean_dir,
            journal_path,
            observed_at=now,
        )

        run_id = uuid.uuid4().hex
        marker_path = markers / f"{current_pid}-{current_ticks or 0}-{run_id}.json"
        stderr_path = stderr_dir / f"{run_id}.log"
        recorder = cls(
            root=resolved_root,
            run_id=run_id,
            pid=current_pid,
            process_start_ticks=current_ticks,
            started_at=now,
            server_origin=origin,
            marker_path=marker_path,
            journal_path=journal_path,
            stderr_path=stderr_path,
            secrets=(str(token),) if token else (),
            journal_max_bytes=journal_max_bytes,
            journal_backups=journal_backups,
            stderr_max_bytes=_bounded_env_int(
                TUI_RUNTIME_STDERR_MAX_BYTES_ENV,
                DEFAULT_TUI_RUNTIME_STDERR_MAX_BYTES,
                minimum=64 * 1024,
                maximum=1024 * 1024 * 1024,
            ),
        )
        marker = recorder._base_event("tui_running")
        _write_private_json(marker_path, marker)
        recorder.record("tui_started", stderr_path=str(stderr_path))
        recorder._prune_retained_files(
            stderr_dir,
            retain=_bounded_env_int(
                TUI_RUNTIME_STDERR_RETAIN_ENV,
                DEFAULT_TUI_RUNTIME_STDERR_RETAIN,
                minimum=2,
                maximum=200,
            ),
            exclude={stderr_path},
        )
        recorder._prune_retained_files(unclean_dir, retain=20)
        return recorder

    @staticmethod
    def _reap_unclean_markers(
        markers: Path,
        unclean_dir: Path,
        journal_path: Path,
        *,
        observed_at: float,
    ) -> None:
        for marker_path in sorted(markers.glob("*.json")):
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                pid = int(marker.get("pid"))
                raw_ticks = marker.get("process_start_ticks")
                ticks = int(raw_ticks) if raw_ticks is not None else None
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                marker = {"marker_path": str(marker_path), "marker_invalid": True}
                pid = -1
                ticks = None
            if pid > 0 and _process_identity_is_live(pid, ticks):
                continue
            event = {
                **marker,
                "event": "tui_unclean_exit_detected",
                "observed_at": observed_at,
                "observed_at_iso": datetime.fromtimestamp(
                    observed_at, timezone.utc
                ).isoformat(),
            }
            _append_private_jsonl(journal_path, event)
            destination = unclean_dir / marker_path.name
            try:
                marker_path.replace(destination)
                destination.chmod(0o600)
            except OSError:
                marker_path.unlink(missing_ok=True)

    @staticmethod
    def _prune_retained_files(
        directory: Path,
        *,
        retain: int,
        exclude: set[Path] | None = None,
    ) -> None:
        excluded = exclude or set()
        candidates = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path not in excluded
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in candidates[retain:]:
            path.unlink(missing_ok=True)

    def _base_event(self, event: str) -> dict[str, object]:
        return {
            "event": event,
            "run_id": self.run_id,
            "pid": self.pid,
            "process_start_ticks": self.process_start_ticks,
            "started_at": self.started_at,
            "started_at_iso": datetime.fromtimestamp(
                self.started_at, timezone.utc
            ).isoformat(),
            "server_origin": self.server_origin,
        }

    def record(self, event: str, **detail: object) -> None:
        _rotate_file(
            self.journal_path,
            max_bytes=self.journal_max_bytes,
            backups=self.journal_backups,
        )
        _append_private_jsonl(
            self.journal_path,
            {**self._base_event(event), "recorded_at": time.time(), **detail},
        )

    @contextmanager
    def capture_stderr(self) -> Iterator[None]:
        descriptor = os.open(
            self.stderr_path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        destination = os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)
        tee = _RedactingStderr(
            sys.stderr,
            destination,
            secrets=self.secrets,
            max_bytes=self.stderr_max_bytes,
        )
        previous = sys.stderr
        sys.stderr = tee  # type: ignore[assignment]
        try:
            yield
        finally:
            sys.stderr = previous
            tee.close_private()
            try:
                self.stderr_path.chmod(0o600)
            except OSError:
                pass

    def finish(
        self,
        status: str,
        *,
        exception: BaseException | None = None,
    ) -> None:
        if self.finished:
            return
        self.finished = True
        detail: dict[str, object] = {
            "status": status,
            "finished_at": time.time(),
            "duration_seconds": max(0.0, time.time() - self.started_at),
            "stderr_path": str(self.stderr_path),
        }
        if exception is not None:
            detail.update(
                {
                    "exception_type": type(exception).__name__,
                    "traceback": redact_tui_runtime_text(
                        "".join(
                            traceback.format_exception(
                                type(exception), exception, exception.__traceback__
                            )
                        ),
                        secrets=self.secrets,
                    ),
                }
            )
        try:
            self.record("tui_exited", **detail)
        finally:
            self.marker_path.unlink(missing_ok=True)


@contextmanager
def record_tui_run(*, server: str, token: str | None) -> Iterator[TuiRunRecorder | None]:
    try:
        recorder = TuiRunRecorder.start(server=server, token=token)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never block the TUI
        print(
            f"agent-pbx: unable to initialize durable TUI diagnostics: {exc}",
            file=sys.stderr,
        )
        yield None
        return

    try:
        with recorder.capture_stderr():
            yield recorder
    except KeyboardInterrupt:
        recorder.finish("interrupted")
        raise
    except BaseException as exc:
        recorder.finish("crashed", exception=exc)
        print(
            f"agent-pbx: TUI failure recorded in {recorder.journal_path}",
            file=sys.stderr,
        )
        raise
    else:
        recorder.finish("clean")


def run_tui_recorded(*, server: str, token: str | None = None) -> None:
    """Launch the Textual client with fail-open durable run diagnostics."""

    from .tui import AgentPBXTUI

    with record_tui_run(server=server, token=token):
        AgentPBXTUI(
            server=server,
            token=token,
            v2_defaults=True,
        ).run()
