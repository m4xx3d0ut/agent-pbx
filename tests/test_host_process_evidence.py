from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_pbx.host_process_evidence import (
    MAX_CANDIDATE_PID,
    MAX_CANDIDATE_PIDS,
    HostProcessAttestor,
    HostProcessExpectation,
)


EXPECTATION = HostProcessExpectation(
    executable="/usr/bin/keeper",
    argv0="keeper",
    user_namespace="user:[4026531837]",
    mount_namespace="mnt:[4026531841]",
)
RESULT_KEYS = {
    "match_count",
    "unique_host_pid",
    "executable_equal",
    "argv0_equal",
    "user_namespace_equal",
    "mount_namespace_equal",
    "timestamp",
    "fail_closed_reason",
}


def write_process(
    proc_root: Path,
    pid: int,
    *,
    executable: str = EXPECTATION.executable,
    argv0: str = EXPECTATION.argv0,
    later_argv: bytes = b"--private-body",
    user_namespace: str = EXPECTATION.user_namespace,
    mount_namespace: str = EXPECTATION.mount_namespace,
) -> Path:
    process_root = proc_root / str(pid)
    namespace_root = process_root / "ns"
    namespace_root.mkdir(parents=True)
    (process_root / "exe").symlink_to(executable)
    (process_root / "cmdline").write_bytes(
        argv0.encode("utf-8") + b"\0" + later_argv + b"\0"
    )
    (namespace_root / "user").symlink_to(user_namespace)
    (namespace_root / "mnt").symlink_to(mount_namespace)
    return process_root


def fake_attestor(proc_root: Path) -> HostProcessAttestor:
    return HostProcessAttestor(
        proc_root,
        require_host_proc=False,
        clock=lambda: 1_793_853_600.25,
    )


def test_attest_returns_only_bounded_metadata_for_one_exact_match(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    write_process(proc_root, 101)

    result = fake_attestor(proc_root).attest(
        candidate_pids=[101], expectation=EXPECTATION
    ).as_dict()

    assert set(result) == RESULT_KEYS
    assert result == {
        "match_count": 1,
        "unique_host_pid": 101,
        "executable_equal": True,
        "argv0_equal": True,
        "user_namespace_equal": True,
        "mount_namespace_equal": True,
        "timestamp": 1_793_853_600.25,
        "fail_closed_reason": None,
    }
    assert "private-body" not in repr(result)


def test_attest_reads_only_first_cmdline_field_and_fixed_proc_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc_root = tmp_path / "proc"
    process_root = write_process(proc_root, 102, later_argv=b"SECRET-LATER-ARGV")
    opened: list[Path] = []
    linked: list[Path] = []
    read_sizes: list[int] = []
    real_open = os.open
    real_read = os.read
    real_readlink = os.readlink

    def recording_open(path: object, flags: int, *args: object) -> int:
        opened.append(Path(path))
        return real_open(path, flags, *args)  # type: ignore[arg-type]

    def recording_read(descriptor: int, size: int) -> bytes:
        read_sizes.append(size)
        return real_read(descriptor, size)

    def recording_readlink(path: object, *args: object) -> str:
        linked.append(Path(path))
        return real_readlink(path, *args)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr("agent_pbx.host_process_evidence.os.open", recording_open)
    monkeypatch.setattr("agent_pbx.host_process_evidence.os.read", recording_read)
    monkeypatch.setattr(
        "agent_pbx.host_process_evidence.os.readlink", recording_readlink
    )

    result = fake_attestor(proc_root).attest(
        candidate_pids=[102], expectation=EXPECTATION
    )

    assert result.unique_host_pid == 102
    assert opened == [process_root / "cmdline", process_root / "cmdline"]
    assert set(linked) == {
        process_root / "exe",
        process_root / "ns" / "user",
        process_root / "ns" / "mnt",
    }
    assert read_sizes == [1] * (2 * (len(EXPECTATION.argv0.encode("utf-8")) + 1))


def test_attest_fails_closed_for_no_match_or_ambiguous_matches(
    tmp_path: Path,
) -> None:
    proc_root = tmp_path / "proc"
    write_process(proc_root, 103, argv0="other")
    write_process(proc_root, 104)
    write_process(proc_root, 105)
    attestor = fake_attestor(proc_root)

    no_match = attestor.attest(candidate_pids=[103], expectation=EXPECTATION)
    ambiguous = attestor.attest(candidate_pids=[104, 105], expectation=EXPECTATION)

    assert no_match.match_count == 0
    assert no_match.unique_host_pid is None
    assert no_match.fail_closed_reason == "no_exact_match"
    assert ambiguous.match_count == 2
    assert ambiguous.unique_host_pid is None
    assert ambiguous.fail_closed_reason == "ambiguous_exact_matches"
    assert not ambiguous.executable_equal


@pytest.mark.parametrize(
    ("candidate_pids", "reason"),
    [
        ([], "invalid_candidate_set"),
        ([1, 1], "invalid_candidate_set"),
        ([True], "invalid_candidate_set"),
        ([MAX_CANDIDATE_PID + 1], "invalid_candidate_set"),
        (list(range(1, MAX_CANDIDATE_PIDS + 2)), "invalid_candidate_set"),
    ],
)
def test_attest_rejects_invalid_or_unbounded_candidate_sets(
    tmp_path: Path,
    candidate_pids: list[int],
    reason: str,
) -> None:
    result = fake_attestor(tmp_path / "proc").attest(
        candidate_pids=candidate_pids, expectation=EXPECTATION
    )

    assert result.fail_closed_reason == reason
    assert result.unique_host_pid is None


def test_attest_rejects_non_host_proc_for_production_mode(tmp_path: Path) -> None:
    result = HostProcessAttestor(tmp_path / "proc").attest(
        candidate_pids=[1], expectation=EXPECTATION
    )

    assert result.fail_closed_reason == "unsupported_proc_root"


@pytest.mark.parametrize(
    "mutation",
    ["missing", "unterminated_cmdline", "empty_cmdline", "malformed_user_ns"],
)
def test_attest_fails_closed_for_unreadable_or_malformed_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    proc_root = tmp_path / "proc"
    if mutation != "missing":
        process_root = write_process(proc_root, 106)
        if mutation == "unterminated_cmdline":
            (process_root / "cmdline").write_bytes(b"keeper")
        elif mutation == "empty_cmdline":
            (process_root / "cmdline").write_bytes(b"\0ignored")
        elif mutation == "malformed_user_ns":
            (process_root / "ns" / "user").unlink()
            (process_root / "ns" / "user").symlink_to("user:not-an-inode")

    result = fake_attestor(proc_root).attest(
        candidate_pids=[106], expectation=EXPECTATION
    )

    expected = (
        "unreadable_process_evidence"
        if mutation == "missing"
        else "malformed_process_evidence"
    )
    assert result.fail_closed_reason == expected
    assert result.unique_host_pid is None


def test_attest_rejects_changed_identity_between_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc_root = tmp_path / "proc"
    process_root = write_process(proc_root, 107)
    real_readlink = os.readlink
    exe_reads = 0

    def changing_readlink(path: object, *args: object) -> str:
        nonlocal exe_reads
        if Path(path) == process_root / "exe":
            exe_reads += 1
            if exe_reads == 2:
                return "/usr/bin/replaced"
        return real_readlink(path, *args)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(
        "agent_pbx.host_process_evidence.os.readlink", changing_readlink
    )

    result = fake_attestor(proc_root).attest(
        candidate_pids=[107], expectation=EXPECTATION
    )

    assert result.fail_closed_reason == "process_identity_changed"
    assert result.unique_host_pid is None


def test_attest_rejects_deleted_executable_and_cmdline_symlink(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    deleted = write_process(proc_root, 108, executable="/usr/bin/keeper (deleted)")
    symlinked = write_process(proc_root, 109)
    (symlinked / "cmdline").unlink()
    outside = tmp_path / "outside-cmdline"
    outside.write_bytes(b"keeper\0secret\0")
    (symlinked / "cmdline").symlink_to(outside)

    deleted_result = fake_attestor(proc_root).attest(
        candidate_pids=[108], expectation=EXPECTATION
    )
    symlink_result = fake_attestor(proc_root).attest(
        candidate_pids=[109], expectation=EXPECTATION
    )

    assert deleted_result.fail_closed_reason == "changed_process_evidence"
    assert symlink_result.fail_closed_reason == "unreadable_process_evidence"


@pytest.mark.parametrize(
    "expectation",
    [
        HostProcessExpectation(
            executable="relative",
            argv0=EXPECTATION.argv0,
            user_namespace=EXPECTATION.user_namespace,
            mount_namespace=EXPECTATION.mount_namespace,
        ),
        HostProcessExpectation(
            executable=EXPECTATION.executable,
            argv0="keeper\n--inject",
            user_namespace=EXPECTATION.user_namespace,
            mount_namespace=EXPECTATION.mount_namespace,
        ),
        HostProcessExpectation(
            executable=EXPECTATION.executable,
            argv0=EXPECTATION.argv0,
            user_namespace="mnt:[1]",
            mount_namespace=EXPECTATION.mount_namespace,
        ),
    ],
)
def test_attest_rejects_invalid_expected_predicates(
    tmp_path: Path,
    expectation: HostProcessExpectation,
) -> None:
    result = fake_attestor(tmp_path / "proc").attest(
        candidate_pids=[110], expectation=expectation
    )

    assert result.fail_closed_reason == "invalid_expectation"
