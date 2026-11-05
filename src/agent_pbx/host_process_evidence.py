from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import re
import time
from typing import Callable, Sequence


MAX_CANDIDATE_PIDS = 32
MAX_CANDIDATE_PID = 2_147_483_647
MAX_EXECUTABLE_LENGTH = 4096
MAX_ARGV0_BYTES = 4096
_NAMESPACE_LINK_RE = re.compile(r"^(?:user|mnt):\[[0-9]+\]$")


@dataclass(frozen=True)
class HostProcessExpectation:
    executable: str
    argv0: str
    user_namespace: str
    mount_namespace: str


@dataclass(frozen=True)
class HostProcessEvidence:
    match_count: int
    unique_host_pid: int | None
    executable_equal: bool
    argv0_equal: bool
    user_namespace_equal: bool
    mount_namespace_equal: bool
    timestamp: float
    fail_closed_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _ProcessSnapshot:
    executable: str
    argv0: str
    user_namespace: str
    mount_namespace: str


class _EvidenceReadError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class HostProcessAttestor:
    """Read a fixed, bounded process identity from one procfs instance."""

    def __init__(
        self,
        proc_root: Path = Path("/proc"),
        *,
        require_host_proc: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.proc_root = proc_root
        self.require_host_proc = require_host_proc
        self.clock = clock

    def attest(
        self,
        *,
        candidate_pids: Sequence[int],
        expectation: HostProcessExpectation,
    ) -> HostProcessEvidence:
        observed_at = float(self.clock())
        invalid_reason = self._validate_request(candidate_pids, expectation)
        if invalid_reason is not None:
            return self._failure(observed_at, invalid_reason)

        snapshots: list[tuple[int, _ProcessSnapshot]] = []
        for pid in candidate_pids:
            try:
                first = self._read_snapshot(pid)
                second = self._read_snapshot(pid)
            except _EvidenceReadError as exc:
                return self._failure(observed_at, exc.reason)
            if first != second:
                return self._failure(observed_at, "process_identity_changed")
            snapshots.append((pid, first))

        matches = [
            pid
            for pid, snapshot in snapshots
            if snapshot
            == _ProcessSnapshot(
                executable=expectation.executable,
                argv0=expectation.argv0,
                user_namespace=expectation.user_namespace,
                mount_namespace=expectation.mount_namespace,
            )
        ]
        if len(matches) == 1:
            return HostProcessEvidence(
                match_count=1,
                unique_host_pid=matches[0],
                executable_equal=True,
                argv0_equal=True,
                user_namespace_equal=True,
                mount_namespace_equal=True,
                timestamp=observed_at,
                fail_closed_reason=None,
            )
        reason = "no_exact_match" if not matches else "ambiguous_exact_matches"
        return HostProcessEvidence(
            match_count=len(matches),
            unique_host_pid=None,
            executable_equal=False,
            argv0_equal=False,
            user_namespace_equal=False,
            mount_namespace_equal=False,
            timestamp=observed_at,
            fail_closed_reason=reason,
        )

    def _validate_request(
        self,
        candidate_pids: Sequence[int],
        expectation: HostProcessExpectation,
    ) -> str | None:
        if self.require_host_proc and self.proc_root != Path("/proc"):
            return "unsupported_proc_root"
        if not candidate_pids or len(candidate_pids) > MAX_CANDIDATE_PIDS:
            return "invalid_candidate_set"
        if any(
            type(pid) is not int or pid <= 0 or pid > MAX_CANDIDATE_PID
            for pid in candidate_pids
        ):
            return "invalid_candidate_set"
        if len(set(candidate_pids)) != len(candidate_pids):
            return "invalid_candidate_set"
        if not self._valid_text(expectation.executable, MAX_EXECUTABLE_LENGTH):
            return "invalid_expectation"
        if not os.path.isabs(expectation.executable):
            return "invalid_expectation"
        if os.path.normpath(expectation.executable) != expectation.executable:
            return "invalid_expectation"
        if not self._valid_text(expectation.argv0, MAX_ARGV0_BYTES):
            return "invalid_expectation"
        if not _NAMESPACE_LINK_RE.fullmatch(expectation.user_namespace):
            return "invalid_expectation"
        if not expectation.user_namespace.startswith("user:"):
            return "invalid_expectation"
        if not _NAMESPACE_LINK_RE.fullmatch(expectation.mount_namespace):
            return "invalid_expectation"
        if not expectation.mount_namespace.startswith("mnt:"):
            return "invalid_expectation"
        return None

    @staticmethod
    def _valid_text(value: object, maximum: int) -> bool:
        if not isinstance(value, str) or not value or len(value) > maximum:
            return False
        return not any(
            ord(character) < 32 or ord(character) == 127 for character in value
        )

    def _read_snapshot(self, pid: int) -> _ProcessSnapshot:
        process_root = self.proc_root / str(pid)
        executable = self._read_link(process_root / "exe", kind="executable")
        argv0 = self._read_first_cmdline_field(process_root / "cmdline")
        user_namespace = self._read_link(
            process_root / "ns" / "user", kind="user_namespace"
        )
        mount_namespace = self._read_link(
            process_root / "ns" / "mnt", kind="mount_namespace"
        )
        if not os.path.isabs(executable) or not self._valid_text(
            executable, MAX_EXECUTABLE_LENGTH
        ):
            raise _EvidenceReadError("malformed_process_evidence")
        if not self._valid_text(argv0, MAX_ARGV0_BYTES):
            raise _EvidenceReadError("malformed_process_evidence")
        if not _NAMESPACE_LINK_RE.fullmatch(
            user_namespace
        ) or not user_namespace.startswith("user:"):
            raise _EvidenceReadError("malformed_process_evidence")
        if not _NAMESPACE_LINK_RE.fullmatch(
            mount_namespace
        ) or not mount_namespace.startswith("mnt:"):
            raise _EvidenceReadError("malformed_process_evidence")
        return _ProcessSnapshot(
            executable=executable,
            argv0=argv0,
            user_namespace=user_namespace,
            mount_namespace=mount_namespace,
        )

    @staticmethod
    def _read_link(path: Path, *, kind: str) -> str:
        try:
            target = os.readlink(path)
        except (OSError, ValueError) as exc:
            raise _EvidenceReadError("unreadable_process_evidence") from exc
        if not isinstance(target, str) or not target:
            raise _EvidenceReadError("malformed_process_evidence")
        if kind == "executable" and target.endswith(" (deleted)"):
            raise _EvidenceReadError("changed_process_evidence")
        return target

    @staticmethod
    def _read_first_cmdline_field(path: Path) -> str:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise _EvidenceReadError("unreadable_process_evidence") from exc
        value = bytearray()
        try:
            for _ in range(MAX_ARGV0_BYTES + 1):
                try:
                    byte = os.read(descriptor, 1)
                except OSError as exc:
                    raise _EvidenceReadError("unreadable_process_evidence") from exc
                if not byte:
                    raise _EvidenceReadError("malformed_process_evidence")
                if byte == b"\0":
                    break
                value.extend(byte)
            else:
                raise _EvidenceReadError("malformed_process_evidence")
        finally:
            os.close(descriptor)
        if not value:
            raise _EvidenceReadError("malformed_process_evidence")
        try:
            return bytes(value).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise _EvidenceReadError("malformed_process_evidence") from exc

    @staticmethod
    def _failure(timestamp: float, reason: str) -> HostProcessEvidence:
        return HostProcessEvidence(
            match_count=0,
            unique_host_pid=None,
            executable_equal=False,
            argv0_equal=False,
            user_namespace_equal=False,
            mount_namespace_equal=False,
            timestamp=timestamp,
            fail_closed_reason=reason,
        )
