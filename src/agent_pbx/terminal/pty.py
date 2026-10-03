from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
import select
import signal
import struct
import subprocess
import termios
import time
from typing import Mapping, Sequence


class PtyProcess:
    """Small PTY owner used for disposable terminal clients."""

    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | str | None = None,
        env: Mapping[str, str] | None = None,
        columns: int = 80,
        rows: int = 24,
    ) -> None:
        if not argv:
            raise ValueError("PTY argv cannot be empty")
        self.argv = tuple(str(item) for item in argv)
        self.cwd = str(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.columns = max(2, int(columns))
        self.rows = max(2, int(rows))
        self.master_fd: int | None = None
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> "PtyProcess":
        if self.process is not None:
            return self
        master_fd, slave_fd = os.openpty()
        try:
            self._set_winsize(slave_fd, self.columns, self.rows)
            process = subprocess.Popen(
                self.argv,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=self.cwd,
                env=self.env,
                close_fds=True,
                preexec_fn=os.setsid,
            )
        except Exception:
            os.close(master_fd)
            os.close(slave_fd)
            raise
        os.close(slave_fd)
        os.set_blocking(master_fd, False)
        self.master_fd = master_fd
        self.process = process
        return self

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def write(self, data: bytes) -> None:
        if self.master_fd is None:
            raise RuntimeError("PTY is not started")
        view = memoryview(data)
        while view:
            try:
                written = os.write(self.master_fd, view)
            except BlockingIOError:
                select.select([], [self.master_fd], [], 0.25)
                continue
            view = view[written:]

    def read_available(self, *, timeout: float = 0.0, limit: int = 1_048_576) -> bytes:
        if self.master_fd is None:
            return b""
        deadline = time.monotonic() + max(0.0, timeout)
        chunks: list[bytes] = []
        size = 0
        while size < limit:
            wait = max(0.0, deadline - time.monotonic())
            if not chunks and timeout > 0:
                ready, _, _ = select.select([self.master_fd], [], [], wait)
                if not ready:
                    break
            try:
                chunk = os.read(self.master_fd, min(65536, limit - size))
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        return b"".join(chunks)

    def resize(self, columns: int, rows: int) -> None:
        self.columns = max(2, int(columns))
        self.rows = max(2, int(rows))
        if self.master_fd is not None:
            self._set_winsize(self.master_fd, self.columns, self.rows)

    def close(self, *, terminate: bool = True, timeout: float = 2.0) -> None:
        # Closing the PTY master first lets terminal-aware children (including
        # tmux clients on macOS) observe a normal hangup and detach cleanly.
        # Explicit signals remain a bounded fallback for children that ignore
        # the closed terminal.
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        process = self.process
        self.process = None
        if process is not None and terminate and process.poll() is None:
            try:
                process.wait(timeout=min(0.25, max(0.1, timeout)))
            except subprocess.TimeoutExpired:
                self._signal(process, signal.SIGHUP)
                try:
                    process.wait(timeout=max(0.1, timeout))
                except subprocess.TimeoutExpired:
                    self._signal(process, signal.SIGKILL)
                    try:
                        process.wait(timeout=max(0.1, timeout))
                    except subprocess.TimeoutExpired:
                        pass

    def __enter__(self) -> "PtyProcess":
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _set_winsize(fd: int, columns: int, rows: int) -> None:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    @staticmethod
    def _signal(process: subprocess.Popen[bytes], value: int) -> None:
        try:
            os.killpg(process.pid, value)
            return
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.send_signal(value)
        except (ProcessLookupError, PermissionError):
            pass
