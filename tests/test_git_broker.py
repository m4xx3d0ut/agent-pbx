from __future__ import annotations

import json
import fcntl
import hashlib
import multiprocessing
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import textwrap

import pytest

import agent_pbx.git_broker as broker
from agent_pbx.git_broker import (
    LinkedGitBrokerError,
    execute_linked_git_commit,
    load_pending_linked_git_commits,
    prepare_linked_git_commit,
    recover_linked_git_commit,
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _repo_receipt_paths(linked: Path) -> tuple[Path, Path, Path, Path]:
    git_dir = Path(_git(linked, "rev-parse", "--absolute-git-dir"))
    common = Path(_git(linked, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (linked / common).resolve()
    branch_ref = _git(linked, "symbolic-ref", "HEAD")
    ref = common.joinpath(*branch_ref.split("/"))
    reflog = common.joinpath("logs", *branch_ref.split("/"))
    return git_dir, common, ref, reflog


def _object_fingerprint(common: Path) -> tuple[tuple[str, str], ...]:
    objects = common / "objects"
    return tuple(
        sorted(
            (
                str(path.relative_to(objects)),
                _sha256(path.read_bytes()),
            )
            for path in objects.rglob("*")
            if path.is_file()
        )
    )


def _crash_execute(
    receipt,
    approved_root: Path,
    journal_root: Path,
    codex_home: Path,
    phase: str,
) -> None:
    execute_linked_git_commit(
        receipt,
        approved_roots=(approved_root,),
        journal_root=journal_root,
        codex_home=codex_home,
        _test_crash_at=phase,
    )


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_AUTHOR_NAME": "Broker Test",
            "GIT_AUTHOR_EMAIL": "broker@example.invalid",
            "GIT_COMMITTER_NAME": "Broker Test",
            "GIT_COMMITTER_EMAIL": "broker@example.invalid",
        },
    )
    return result.stdout.strip()


def _strace_python(
    tmp_path: Path,
    *,
    name: str,
    script: str,
    arguments: tuple[str, ...],
) -> str:
    """Run one local Python proof under Linux file-syscall tracing."""

    strace = shutil.which("strace")
    if sys.platform != "linux" or strace is None:
        pytest.skip("requires Linux and strace for child-process file observation")
    project_root = Path(__file__).resolve().parents[1]
    trace = tmp_path / f"{name}.strace"
    environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "trace-home"),
        "LC_ALL": "C",
        "PYTHONPATH": str(project_root / "src"),
    }
    Path(environment["HOME"]).mkdir(mode=0o700)
    result = subprocess.run(
        [
            strace,
            "-f",
            "-qq",
            "-s",
            "4096",
            "-e",
            (
                "trace=open,openat,openat2,stat,lstat,newfstatat,statx,"
                "readlink,readlinkat"
            ),
            "-o",
            str(trace),
            sys.executable,
            "-c",
            textwrap.dedent(script),
            *arguments,
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=project_root,
        env=environment,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"straced proof failed ({result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
        f"trace:\n{trace.read_text(encoding='utf-8', errors='replace')}"
    )
    return trace.read_text(encoding="utf-8", errors="replace")


def _fixture(tmp_path: Path, *, branch: str = "pbx-operator-7/broker-test") -> tuple[Path, Path]:
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    main.mkdir()
    _git(main, "init", "-b", "main")
    (main / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(main, "add", "tracked.txt")
    _git(main, "commit", "-m", "baseline")
    _git(main, "worktree", "add", "-b", branch, str(linked))
    _git(linked, "branch", "--set-upstream-to=main")
    return main, linked


def _split_codex_home(tmp_path: Path) -> Path:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "config.toml").write_text(
        "\n".join(
            (
                'default_permissions = "trusted-split"',
                "",
                "[permissions.trusted-split]",
                'extends = ":workspace"',
                "",
                "[permissions.trusted-split.filesystem]",
                '"/tmp/broker-global-secret" = "deny"',
                "",
                '[permissions.trusted-split.filesystem.":workspace_roots"]',
                '"." = "write"',
                '".git" = "write"',
                '"**/.env" = "deny"',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return codex_home


def _prepare(
    tmp_path: Path,
    *,
    branch: str = "pbx-operator-7/broker-test",
    stage_paths: tuple[str, ...] = ("new.txt", "tracked.txt"),
    commit_subject: str | None = None,
):
    main, linked = _fixture(tmp_path, branch=branch)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (linked / "new.txt").write_text("new\n", encoding="utf-8")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=stage_paths,
        codex_home=codex_home,
        authored_at=1_700_000_000,
        commit_subject=commit_subject,
    )
    return main, linked, codex_home, receipt


def _git_index_fixture(
    paths: tuple[bytes, ...],
    *,
    version: int = 2,
    object_format: str = "sha1",
    extensions: tuple[tuple[bytes, bytes], ...] = (),
) -> bytes:
    width = {"sha1": 20, "sha256": 32}[object_format]
    body = bytearray(b"DIRC")
    body.extend(version.to_bytes(4, "big"))
    body.extend(len(paths).to_bytes(4, "big"))
    previous = b""
    for ordinal, path in enumerate(paths):
        start = len(body)
        body.extend(b"\0" * 40)
        body.extend(bytes([ordinal + 1]) * width)
        body.extend(min(len(path), 0x0FFF).to_bytes(2, "big"))
        if version == 4:
            common = 0
            while (
                common < len(previous)
                and common < len(path)
                and previous[common] == path[common]
            ):
                common += 1
            remove_count = len(previous) - common
            assert remove_count < 0x80
            body.append(remove_count)
            body.extend(path[common:])
            body.append(0)
        else:
            body.extend(path)
            body.append(0)
            body.extend(b"\0" * ((-(len(body) - start)) % 8))
        previous = path
    for signature, payload in extensions:
        assert len(signature) == 4
        body.extend(signature)
        body.extend(len(payload).to_bytes(4, "big"))
        body.extend(payload)
    body.extend(hashlib.new(object_format, body).digest())
    return bytes(body)


def _rehash_git_index(index: bytes, *, object_format: str = "sha1") -> bytes:
    width = {"sha1": 20, "sha256": 32}[object_format]
    body = index[:-width]
    return body + hashlib.new(object_format, body).digest()


def test_git_index_parser_accepts_marker_pathnames_and_safe_extensions() -> None:
    index = _git_index_fixture(
        (b"docs/link-guide.md", b"src/sdir-helper.py"),
        extensions=((b"TREE", b"safe-cache"),),
    )

    assert broker._git_index_extension_signatures(  # noqa: SLF001
        index,
        object_format="sha1",
    ) == (b"TREE",)


def test_git_index_parser_accepts_v4_and_sha256_layouts() -> None:
    v4 = _git_index_fixture(
        (b"docs/link-one.md", b"docs/link-two.md"),
        version=4,
    )
    sha256 = _git_index_fixture(
        (b"src/sdir-name.py",),
        object_format="sha256",
        extensions=((b"TREE", b"sha256-cache"),),
    )

    assert broker._git_index_extension_signatures(  # noqa: SLF001
        v4,
        object_format="sha1",
    ) == ()
    assert broker._git_index_extension_signatures(  # noqa: SLF001
        sha256,
        object_format="sha256",
    ) == (b"TREE",)


@pytest.mark.parametrize("signature", (b"link", b"sdir"))
def test_git_index_parser_exposes_actual_unsafe_extensions(signature: bytes) -> None:
    index = _git_index_fixture(
        (b"tracked.txt",),
        extensions=((signature, b"unsafe"),),
    )

    assert broker._git_index_extension_signatures(  # noqa: SLF001
        index,
        object_format="sha1",
    ) == (signature,)


@pytest.mark.parametrize("signature", (b"link", b"sdir"))
def test_plain_clean_index_rejects_structural_unsafe_extension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signature: bytes,
) -> None:
    index = _git_index_fixture(
        (b"tracked.txt",),
        extensions=((signature, b"unsafe"),),
    )
    plan = type(
        "Plan",
        (),
        {"worktree_root": tmp_path, "git_dir": tmp_path},
    )()
    monkeypatch.setattr(broker, "_git_optional_text", lambda *_args: "")
    monkeypatch.setattr(broker, "_git_text", lambda *_args: "sha1")
    monkeypatch.setattr(broker, "_read_regular_bytes", lambda *_args: index)

    with pytest.raises(LinkedGitBrokerError, match="split or sparse index extensions"):
        broker._require_plain_clean_index(plan)  # noqa: SLF001


def test_plain_clean_index_rejects_unknown_mandatory_extension(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    index = _git_index_fixture(
        (b"tracked.txt",),
        extensions=((b"abcd", b"mandatory"),),
    )
    plan = type(
        "Plan",
        (),
        {"worktree_root": tmp_path, "git_dir": tmp_path},
    )()
    monkeypatch.setattr(broker, "_git_optional_text", lambda *_args: "")
    monkeypatch.setattr(broker, "_git_text", lambda *_args: "sha1")
    monkeypatch.setattr(broker, "_read_regular_bytes", lambda *_args: index)

    with pytest.raises(LinkedGitBrokerError, match="mandatory index extension.*abcd"):
        broker._require_plain_clean_index(plan)  # noqa: SLF001


def test_git_index_parser_rejects_malformed_boundaries_and_checksum() -> None:
    valid = _git_index_fixture((b"ab",), extensions=((b"TREE", b"cache"),))

    unsupported = bytearray(valid)
    unsupported[4:8] = (5).to_bytes(4, "big")
    unsupported = bytearray(_rehash_git_index(bytes(unsupported)))
    with pytest.raises(LinkedGitBrokerError, match="index version 5"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(unsupported), object_format="sha1"
        )

    impossible_count = bytearray(valid)
    impossible_count[8:12] = (1000).to_bytes(4, "big")
    impossible_count = bytearray(_rehash_git_index(bytes(impossible_count)))
    with pytest.raises(LinkedGitBrokerError, match="entry count cannot fit"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(impossible_count), object_format="sha1"
        )

    non_nul_padding = bytearray(valid)
    non_nul_padding[77] = 1
    non_nul_padding = bytearray(_rehash_git_index(bytes(non_nul_padding)))
    with pytest.raises(LinkedGitBrokerError, match="non-NUL padding"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(non_nul_padding), object_format="sha1"
        )

    oversized_extension = bytearray(valid)
    tree_offset = oversized_extension.find(b"TREE", 12)
    assert tree_offset > 0
    oversized_extension[tree_offset + 4 : tree_offset + 8] = (2**32 - 1).to_bytes(
        4, "big"
    )
    oversized_extension = bytearray(_rehash_git_index(bytes(oversized_extension)))
    with pytest.raises(LinkedGitBrokerError, match="payload crosses"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(oversized_extension), object_format="sha1"
        )

    checksum_mismatch = bytearray(valid)
    checksum_mismatch[-1] ^= 1
    with pytest.raises(LinkedGitBrokerError, match="checksum"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(checksum_mismatch), object_format="sha1"
        )


def test_git_index_parser_rejects_truncated_duplicate_and_invalid_extensions() -> None:
    valid = _git_index_fixture((b"tracked.txt",))
    checksum_width = 20

    truncated_header_body = valid[:-checksum_width] + b"ABC"
    truncated_header = truncated_header_body + hashlib.sha1(
        truncated_header_body
    ).digest()
    with pytest.raises(LinkedGitBrokerError, match="extension header is truncated"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            truncated_header, object_format="sha1"
        )

    invalid_signature_body = valid[:-checksum_width]
    invalid_signature_body += b"1BAD" + (0).to_bytes(4, "big")
    invalid_signature = invalid_signature_body + hashlib.sha1(
        invalid_signature_body
    ).digest()
    with pytest.raises(LinkedGitBrokerError, match="four ASCII letters"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            invalid_signature, object_format="sha1"
        )

    duplicate = _git_index_fixture(
        (b"tracked.txt",),
        extensions=((b"TREE", b"one"), (b"TREE", b"two")),
    )
    with pytest.raises(LinkedGitBrokerError, match="duplicate extension"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            duplicate, object_format="sha1"
        )

    with pytest.raises(LinkedGitBrokerError, match="truncated before its checksum"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            b"DIRC", object_format="sha1"
        )


def test_git_index_parser_rejects_entry_flag_and_extended_layout_ambiguity() -> None:
    valid = _git_index_fixture((b"tracked.txt",))

    wrong_length = bytearray(valid)
    flags_offset = 12 + 40 + 20
    wrong_length[flags_offset : flags_offset + 2] = (1).to_bytes(2, "big")
    wrong_length = bytearray(_rehash_git_index(bytes(wrong_length)))
    with pytest.raises(LinkedGitBrokerError, match="pathname length"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(wrong_length), object_format="sha1"
        )

    extended_v2 = bytearray(valid)
    original_flags = int.from_bytes(
        extended_v2[flags_offset : flags_offset + 2], "big"
    )
    extended_v2[flags_offset : flags_offset + 2] = (
        original_flags | broker.GIT_INDEX_EXTENDED_FLAG
    ).to_bytes(2, "big")
    extended_v2 = bytearray(_rehash_git_index(bytes(extended_v2)))
    with pytest.raises(LinkedGitBrokerError, match="v2 entry.*extended flags"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(extended_v2), object_format="sha1"
        )


def test_git_index_parser_rejects_unterminated_and_v4_invalid_paths() -> None:
    fixed = b"\0" * 40 + b"\1" * 20 + (1).to_bytes(2, "big")
    unterminated_body = b"DIRC" + (2).to_bytes(4, "big") + (1).to_bytes(4, "big")
    unterminated_body += fixed + b"x"
    unterminated = unterminated_body + hashlib.sha1(unterminated_body).digest()
    with pytest.raises(LinkedGitBrokerError, match="pathname is not terminated"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            unterminated, object_format="sha1"
        )

    invalid_v4 = bytearray(_git_index_fixture((b"first.txt",), version=4))
    invalid_v4[12 + 62] = 1
    invalid_v4 = bytearray(_rehash_git_index(bytes(invalid_v4)))
    with pytest.raises(LinkedGitBrokerError, match="removes beyond"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            bytes(invalid_v4), object_format="sha1"
        )

    with pytest.raises(LinkedGitBrokerError, match="object format"):
        broker._git_index_extension_signatures(  # noqa: SLF001
            _git_index_fixture((b"tracked.txt",)), object_format="sha512"
        )


def test_prepare_accepts_link_marker_in_tracked_pathname(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    marker_path = linked / "docs" / "release-links.md"
    marker_path.parent.mkdir()
    marker_path.write_text("baseline\n", encoding="utf-8")
    _git(linked, "add", "docs/release-links.md")
    _git(linked, "commit", "-m", "add link marker fixture")
    marker_path.write_text("changed\n", encoding="utf-8")

    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("docs/release-links.md",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )

    assert receipt.approved_stage_paths == ("docs/release-links.md",)
    assert receipt.path_identities[0].path == "docs/release-links.md"


def test_prepare_binds_exact_descriptive_subject_into_receipt(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")

    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("tracked.txt",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
        commit_subject="fix: preserve broker evidence",
    )

    assert receipt.commit_subject == "fix: preserve broker evidence"
    assert "commit_parents" not in receipt.public_dict()
    assert "expected_tree" not in receipt.public_dict()


def test_broker_creates_exact_ordered_two_parent_commit(tmp_path: Path) -> None:
    main, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    first_parent = _git(linked, "rev-parse", "HEAD")

    (main / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (main / "new.txt").write_text("new\n", encoding="utf-8")
    _git(main, "add", "tracked.txt", "new.txt")
    _git(main, "commit", "-m", "candidate")
    second_parent = _git(main, "rev-parse", "HEAD")
    expected_tree = _git(main, "rev-parse", "HEAD^{tree}")

    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (linked / "new.txt").write_text("new\n", encoding="utf-8")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("new.txt", "tracked.txt"),
        codex_home=codex_home,
        authored_at=1_700_000_000,
        commit_subject="Merge exact candidate lineage",
        commit_parents=(first_parent, second_parent),
        expected_tree=expected_tree,
    )

    result = execute_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=tmp_path / "journal",
        codex_home=codex_home,
    )

    assert receipt.commit_parents == (first_parent, second_parent)
    assert result.commit_parents == (first_parent, second_parent)
    assert _git(linked, "show", "-s", "--format=%P", result.new_head) == (
        f"{first_parent} {second_parent}"
    )
    assert _git(linked, "rev-parse", "HEAD^{tree}") == expected_tree


def test_broker_rejects_exact_merge_when_reviewed_tree_does_not_match(
    tmp_path: Path,
) -> None:
    main, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    first_parent = _git(linked, "rev-parse", "HEAD")
    expected_tree = _git(linked, "rev-parse", "HEAD^{tree}")
    (main / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    _git(main, "add", "candidate.txt")
    _git(main, "commit", "-m", "candidate")
    second_parent = _git(main, "rev-parse", "HEAD")
    (linked / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    original_index = (Path(_git(linked, "rev-parse", "--absolute-git-dir")) / "index").read_bytes()

    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("candidate.txt",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
        commit_subject="Merge exact candidate lineage",
        commit_parents=(first_parent, second_parent),
        expected_tree=expected_tree,
    )

    with pytest.raises(LinkedGitBrokerError, match="exact expected Git tree"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )

    assert _git(linked, "rev-parse", "HEAD") == first_parent
    assert (
        Path(_git(linked, "rev-parse", "--absolute-git-dir")) / "index"
    ).read_bytes() == original_index


def test_broker_rejects_ordered_parents_when_head_is_not_first(
    tmp_path: Path,
) -> None:
    main, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    head = _git(linked, "rev-parse", "HEAD")
    candidate = _git(main, "commit-tree", f"{head}^{{tree}}", "-p", head, "-m", "candidate")
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(LinkedGitBrokerError, match="first commit parent"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
            authored_at=1_700_000_000,
            commit_parents=(candidate, head),
            expected_tree=_git(main, "rev-parse", f"{head}^{{tree}}"),
        )


def test_broker_rejects_ambient_merge_state_before_capture(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    git_dir = Path(_git(linked, "rev-parse", "--absolute-git-dir"))
    (git_dir / "MERGE_HEAD").write_text("a" * 40 + "\n", encoding="ascii")

    with pytest.raises(LinkedGitBrokerError, match="active Git operation state"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
            authored_at=1_700_000_000,
        )


@pytest.mark.parametrize("subject", ["", "bad\nsubject", "x" * 161])
def test_prepare_rejects_invalid_exact_subject(
    tmp_path: Path,
    subject: str,
) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")

    with pytest.raises(LinkedGitBrokerError, match="subject"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
            commit_subject=subject,
        )


def test_prepare_rejects_actual_split_index_extension(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(linked, "update-index", "--split-index")

    with pytest.raises(LinkedGitBrokerError, match="split or sparse index extensions"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
        )


def test_prepare_accepts_real_git_index_v4(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(linked, "update-index", "--index-version", "4")

    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("tracked.txt",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )

    assert receipt.approved_stage_paths == ("tracked.txt",)


def test_broker_commits_exact_paths_and_is_idempotent(tmp_path: Path) -> None:
    main, linked, codex_home, receipt = _prepare(
        tmp_path,
        stage_paths=("tracked.txt",),
    )
    hook_marker = tmp_path / "hook-ran"
    hook = main / ".git" / "hooks" / "reference-transaction"
    hook.write_text(f"#!/bin/sh\ntouch {hook_marker}\n", encoding="utf-8")
    hook.chmod(0o755)

    result = execute_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=tmp_path / "journal",
        codex_home=codex_home,
    )

    assert result.status == "committed"
    assert result.idempotent is False
    assert result.committed_paths == ("tracked.txt",)
    assert _git(linked, "status", "--short") == "?? new.txt"
    assert _git(linked, "rev-parse", "HEAD") == result.new_head
    assert _git(linked, "show", "--format=%an <%ae>", "--no-patch") == (
        "Agent PBX Operator <agent-pbx-operator@localhost.invalid>"
    )
    assert _git(linked, "show", "--format=%s", "--no-patch") == (
        "Agent PBX approved commit for operator-7-fork-test"
    )
    assert not hook_marker.exists()
    assert not (main / ".git" / "refs" / "heads" / "shared-canary").exists()
    state = json.loads(
        (tmp_path / "journal" / receipt.transaction_id / "state.json").read_text()
    )
    assert state["phase"] == "done"

    repeated = execute_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=tmp_path / "journal",
        codex_home=codex_home,
    )
    assert repeated.idempotent is True
    assert repeated.new_head == result.new_head


def test_broker_rejects_preapproval_drift_without_mutation(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path)
    old_head = _git(linked, "rev-parse", "HEAD")
    old_index = (Path(_git(linked, "rev-parse", "--absolute-git-dir")) / "index").read_bytes()
    (linked / "tracked.txt").write_text("drifted\n", encoding="utf-8")

    with pytest.raises(LinkedGitBrokerError, match="approval receipt drifted"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )

    assert _git(linked, "rev-parse", "HEAD") == old_head
    assert (Path(_git(linked, "rev-parse", "--absolute-git-dir")) / "index").read_bytes() == old_index
    assert _git(linked, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("phase", ("prepared", "ready", "index_installed"))
def test_broker_fault_before_ref_update_restores_exact_prestate(
    tmp_path: Path,
    phase: str,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path)
    old_head = _git(linked, "rev-parse", "HEAD")
    git_dir = Path(_git(linked, "rev-parse", "--absolute-git-dir"))
    old_index = (git_dir / "index").read_bytes()

    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
            fault_at=phase,
        )
    with pytest.raises(LinkedGitBrokerError, match="prepare a new approval"):
        recover_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )

    assert _git(linked, "rev-parse", "HEAD") == old_head
    assert (git_dir / "index").read_bytes() == old_index
    assert _git(linked, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("phase", ("ref_updated", "done"))
def test_broker_fault_after_ref_update_completes_idempotently(
    tmp_path: Path,
    phase: str,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path)

    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
            fault_at=phase,
        )
    recovered = recover_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=tmp_path / "journal",
        codex_home=codex_home,
    )

    assert recovered is not None
    assert recovered.idempotent is True
    assert recovered.new_head == _git(linked, "rev-parse", "HEAD")
    assert _git(linked, "status", "--short") == ""


def test_broker_rejects_staged_sparse_split_and_wrong_namespace(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path, branch="pbx-operator-7/test")
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    _git(linked, "add", "tracked.txt")
    with pytest.raises(LinkedGitBrokerError, match="empty staged"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
        )

    _git(linked, "reset")
    _git(linked, "config", "core.splitIndex", "true")
    with pytest.raises(LinkedGitBrokerError, match="core.splitIndex"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
        )

    other_root = tmp_path / "other"
    other_root.mkdir()
    _, other = _fixture(other_root, branch="pbx/not-operator")
    (other / "tracked.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(LinkedGitBrokerError, match="pbx-operator-N"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=other,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
        )


def test_broker_uses_explicit_raw_bytes_without_attributes_and_rejects_special_file(
    tmp_path: Path,
) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
    (linked / "tracked.txt").write_bytes(b"changed\r\n")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=(".gitattributes", "tracked.txt"),
        codex_home=codex_home,
    )
    tracked = next(
        identity for identity in receipt.path_identities if identity.path == "tracked.txt"
    )
    assert tracked.raw_sha256 == _sha256(b"changed\r\n")
    assert tracked.content_policy_sha256 == broker.RAW_BYTE_CONTENT_POLICY_SHA256

    _git(linked, "checkout", "--", "tracked.txt")
    (linked / ".gitattributes").write_text("*.txt filter=external\n", encoding="utf-8")
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=(".gitattributes", "tracked.txt"),
        codex_home=codex_home,
    )
    assert all(
        identity.content_policy_sha256 == broker.RAW_BYTE_CONTENT_POLICY_SHA256
        for identity in receipt.path_identities
    )

    (linked / ".gitattributes").unlink()
    (linked / "tracked.txt").unlink()
    os.mkfifo(linked / "tracked.txt")
    try:
        with pytest.raises(LinkedGitBrokerError, match="unsupported broker file type"):
            prepare_linked_git_commit(
                agent_id="operator-7-fork-test",
                work_root=linked,
                approved_roots=(tmp_path,),
                approved_stage_paths=("tracked.txt",),
                codex_home=codex_home,
            )
    finally:
        (linked / "tracked.txt").unlink()


def test_broker_rejects_gitlink_and_ambiguous_recovery(tmp_path: Path) -> None:
    main, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    _git(child, "init", "-b", "main")
    (child / "file").write_text("child\n", encoding="utf-8")
    _git(child, "add", "file")
    _git(child, "commit", "-m", "child")
    _git(linked, "-c", "protocol.file.allow=always", "submodule", "add", str(child), "module")
    _git(linked, "commit", "-m", "add module")
    _git(child, "commit", "--allow-empty", "-m", "next")
    _git(linked / "module", "fetch", str(child), "main")
    _git(linked / "module", "checkout", "FETCH_HEAD")
    with pytest.raises(LinkedGitBrokerError, match="gitlinks/submodules"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("module",),
            codex_home=codex_home,
        )

    # A separate clean fixture proves an unknown old/new-ref/index combination
    # is never guessed during recovery.
    ambiguous = tmp_path / "ambiguous"
    ambiguous.mkdir()
    _, linked, codex_home, receipt = _prepare(ambiguous)
    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(ambiguous,),
            journal_root=ambiguous / "journal",
            codex_home=codex_home,
            fault_at="index_installed",
        )
    _git(linked, "commit", "--allow-empty", "-m", "foreign drift")
    with pytest.raises(LinkedGitBrokerError, match="manual review required"):
        recover_linked_git_commit(
            receipt,
            approved_roots=(ambiguous,),
            journal_root=ambiguous / "journal",
            codex_home=codex_home,
        )


def test_prepare_and_candidates_are_repository_read_only(tmp_path: Path) -> None:
    main, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    git_dir, common, ref, reflog = _repo_receipt_paths(linked)
    index = git_dir / "index"
    before = {
        "index": _sha256(index.read_bytes()),
        "ref": _sha256(ref.read_bytes()),
        "reflog": _sha256(reflog.read_bytes()),
        "objects": _object_fingerprint(common),
    }
    lock_paths = (git_dir / "index.lock", ref.with_name(ref.name + ".lock"))
    assert not any(path.exists() for path in lock_paths)

    assert broker.list_linked_git_commit_candidates(
        work_root=linked,
        approved_roots=(tmp_path,),
        proposed_paths=("tracked.txt",),
        codex_home=codex_home,
    ) == ("tracked.txt",)
    prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("tracked.txt",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )

    assert _sha256(index.read_bytes()) == before["index"]
    assert _sha256(ref.read_bytes()) == before["ref"]
    assert _sha256(reflog.read_bytes()) == before["reflog"]
    assert _object_fingerprint(common) == before["objects"]
    assert not any(path.exists() for path in lock_paths)
    assert not any(
        path.name.endswith(".lock")
        for path in (common / "refs").rglob("*")
        if path.is_file()
    )


def test_object_manifest_is_durable_before_any_shared_object_install(
    tmp_path: Path,
) -> None:
    _, linked, codex_home, receipt = _prepare(
        tmp_path,
        stage_paths=("tracked.txt",),
    )
    _, common, _, _ = _repo_receipt_paths(linked)
    before = _object_fingerprint(common)
    journal_root = tmp_path / "journal"

    with pytest.raises(LinkedGitBrokerError, match="objects_prepared"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=journal_root,
            codex_home=codex_home,
            fault_at="objects_prepared",
        )

    assert _object_fingerprint(common) == before
    state = json.loads(
        (journal_root / receipt.transaction_id / "state.json").read_text()
    )
    assert state["phase"] == "objects_prepared"
    assert state["installed_object_oids"] == []
    assert state["expected_object_oids"] == [
        entry["oid"] for entry in state["object_manifest"]
    ]
    assert state["created_object_oids"] == [
        entry["oid"]
        for entry in state["object_manifest"]
        if entry["prepared_in_quarantine"]
    ]
    assert {entry["type"] for entry in state["object_manifest"]} >= {
        "blob",
        "tree",
        "commit",
    }
    assert load_pending_linked_git_commits(journal_root)[0].phase == (
        "objects_prepared"
    )


def test_true_death_during_object_install_has_complete_bounded_manifest(
    tmp_path: Path,
) -> None:
    _, linked, codex_home, receipt = _prepare(
        tmp_path,
        stage_paths=("tracked.txt",),
    )
    journal_root = tmp_path / "journal"
    old_head = _git(linked, "rev-parse", "HEAD")
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=_crash_execute,
        args=(receipt, tmp_path, journal_root, codex_home, "objects_installing"),
    )
    process.start()
    process.join(20)
    assert process.exitcode == 97

    state_path = journal_root / receipt.transaction_id / "state.json"
    state = json.loads(state_path.read_text())
    assert state["phase"] == "objects_installing"
    assert len(state["installed_object_oids"]) == 1
    assert set(state["installed_object_oids"]) <= set(state["created_object_oids"])
    assert set(state["created_object_oids"]) <= set(state["expected_object_oids"])
    with pytest.raises(LinkedGitBrokerError, match="normal Git GC") as restored:
        recover_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=journal_root,
            codex_home=codex_home,
        )
    assert "shared objects were not deleted" in str(restored.value)
    assert _git(linked, "rev-parse", "HEAD") == old_head
    assert _git(linked, "diff", "--cached", "--name-only") == ""
    assert json.loads(state_path.read_text())["phase"] == "rolled_back"


def test_broker_never_observes_excluded_protected_dirt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    approved = linked / "tracked.txt"
    approved.write_text("approved\n", encoding="utf-8")
    protected = linked / "ops" / "systemd" / "ae-ha-core.env"
    protected.parent.mkdir(parents=True)
    protected.write_text("PROTECTED=changed\n", encoding="utf-8")
    protected.chmod(0)

    seen_commands: list[tuple[str, ...]] = []
    original_process = broker._git_process
    original_read_bytes = Path.read_bytes
    original_lstat = Path.lstat

    def guarded_process(root: Path, *args: str, **kwargs):
        seen_commands.append(tuple(args))
        assert str(protected.relative_to(linked)) not in args
        assert args[0] not in {"status", "diff"}
        return original_process(root, *args, **kwargs)

    def guarded_read_bytes(path: Path) -> bytes:
        assert path != protected
        return original_read_bytes(path)

    def guarded_lstat(path: Path):
        assert path != protected
        return original_lstat(path)

    monkeypatch.setattr(broker, "_git_process", guarded_process)
    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    monkeypatch.setattr(Path, "lstat", guarded_lstat)
    try:
        receipt = prepare_linked_git_commit(
            agent_id="operator-8-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
            codex_home=codex_home,
            authored_at=1_700_000_000,
        )
    finally:
        protected.chmod(0o600)

    assert receipt.approved_stage_paths == ("tracked.txt",)
    assert all(command[0] not in {"status", "diff", "write-tree"} for command in seen_commands)
    assert all("check-attr" not in command for command in seen_commands)
    assert not any(str(protected.relative_to(linked)) in command for command in seen_commands)


def test_strace_prepare_and_candidate_review_do_not_observe_protected_paths(
    tmp_path: Path,
) -> None:
    """Prove Git children never open worktree policy or protected content."""

    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    zone = linked / "operator-8-sentinel-zone"
    zone.mkdir()
    approved = zone / "approved.txt"
    approved.write_text("approved raw bytes\n", encoding="utf-8")
    attributes = zone / ".gitattributes"
    attributes.write_text(
        "approved.txt filter=must-not-run text eol=crlf\n",
        encoding="utf-8",
    )
    protected = zone / "ae-ha-core.env"
    protected.write_text("PROTECTED=must-not-open\n", encoding="utf-8")
    protected.chmod(0)
    relative = str(approved.relative_to(linked))
    try:
        trace = _strace_python(
            tmp_path,
            name="broker-protected-non-observation",
            script="""
                import sys
                from pathlib import Path
                from agent_pbx.git_broker import (
                    list_linked_git_commit_candidates,
                    prepare_linked_git_commit,
                )

                work_root, approved_root, codex_home, approved_path = sys.argv[1:]
                candidates = list_linked_git_commit_candidates(
                    work_root=Path(work_root),
                    approved_roots=(Path(approved_root),),
                    proposed_paths=(approved_path,),
                    codex_home=Path(codex_home),
                )
                assert candidates == (approved_path,)
                receipt = prepare_linked_git_commit(
                    agent_id="operator-8-fork-strace-proof",
                    work_root=Path(work_root),
                    approved_roots=(Path(approved_root),),
                    approved_stage_paths=(approved_path,),
                    codex_home=Path(codex_home),
                    authored_at=1_700_000_000,
                )
                assert receipt.approved_stage_paths == (approved_path,)
            """,
            arguments=(str(linked), str(tmp_path), str(codex_home), relative),
        )
    finally:
        protected.chmod(0o600)

    for forbidden in (attributes.resolve(), protected.resolve()):
        assert str(forbidden) not in trace
        assert str(forbidden.relative_to(linked)) not in trace


def test_strace_include_rejection_never_opens_external_target(
    tmp_path: Path,
) -> None:
    """Reject include configuration before a Git child can follow its target."""

    fixture = _fixture(tmp_path)
    linked = fixture[1]
    codex_home = _split_codex_home(tmp_path)
    common = Path(_git(linked, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (linked / common).resolve()
    external = tmp_path / "operator-8-external-include-sentinel.cfg"
    external.write_text(
        "[core]\n\tbare = true\n[credential]\n\thelper = must-not-run\n",
        encoding="utf-8",
    )
    with (common / "config").open("a", encoding="utf-8") as handle:
        handle.write(f"\n[include]\n\tpath = {external}\n")

    trace = _strace_python(
        tmp_path,
        name="broker-include-non-observation",
        script="""
            import sys
            from pathlib import Path
            from agent_pbx.git_broker import (
                LinkedGitBrokerError,
                list_linked_git_commit_candidates,
            )

            work_root, approved_root, codex_home = sys.argv[1:]
            try:
                list_linked_git_commit_candidates(
                    work_root=Path(work_root),
                    approved_roots=(Path(approved_root),),
                    proposed_paths=("tracked.txt",),
                    codex_home=Path(codex_home),
                )
            except LinkedGitBrokerError as exc:
                assert "include/includeIf" in str(exc)
            else:
                raise AssertionError("include-bearing Git config was not rejected")
        """,
        arguments=(str(linked), str(tmp_path), str(codex_home)),
    )

    assert str(external.resolve()) not in trace
    assert external.name not in trace


def test_approved_regular_leaf_replacement_is_rejected(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(
        tmp_path,
        stage_paths=("tracked.txt",),
    )
    identity = receipt.path_identities[0]
    replacement = linked / ".replacement"
    replacement.write_bytes((linked / "tracked.txt").read_bytes())
    os.replace(replacement, linked / "tracked.txt")

    with pytest.raises(LinkedGitBrokerError, match="approved file identity drifted"):
        broker._identity_bytes(linked, identity)


def test_approved_parent_symlink_swap_is_rejected_without_escape_read(
    tmp_path: Path,
) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    zone = linked / "approved-zone"
    zone.mkdir()
    leaf = zone / "approved.txt"
    leaf.write_text("approved\n", encoding="utf-8")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("approved-zone/approved.txt",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )
    original = linked / "approved-zone-original"
    zone.rename(original)
    escape = tmp_path / "escape-zone"
    escape.mkdir()
    (escape / "approved.txt").write_text("must not read\n", encoding="utf-8")
    zone.symlink_to(escape, target_is_directory=True)

    with pytest.raises(
        LinkedGitBrokerError,
        match="approved parent chain drifted|without symlinks",
    ):
        broker._identity_bytes(linked, receipt.path_identities[0])


def test_approved_symlink_target_swap_is_rejected(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    leaf = linked / "approved-link"
    leaf.symlink_to("first-target")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("approved-link",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )
    leaf.unlink()
    leaf.symlink_to("second-target")

    with pytest.raises(
        LinkedGitBrokerError,
        match="approved (file identity|bytes) drifted",
    ):
        broker._identity_bytes(linked, receipt.path_identities[0])


def test_regular_leaf_write_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "read-race"
    root.mkdir()
    leaf = root / "large.bin"
    leaf.write_bytes(b"A" * (2 * 1024 * 1024))
    original_read = broker.os.read
    raced = False

    def racing_read(descriptor: int, size: int) -> bytes:
        nonlocal raced
        result = original_read(descriptor, size)
        if result and not raced:
            raced = True
            leaf.write_bytes(b"B" * (2 * 1024 * 1024))
        return result

    monkeypatch.setattr(broker.os, "read", racing_read)
    with pytest.raises(LinkedGitBrokerError, match="changed during read"):
        broker._read_approved_leaf(root, "large.bin")
    assert raced is True


def test_deleted_leaf_parent_symlink_swap_is_rejected(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    zone = linked / "deleted-zone"
    zone.mkdir()
    leaf = zone / "tracked-delete.txt"
    leaf.write_text("delete me\n", encoding="utf-8")
    _git(linked, "add", "deleted-zone/tracked-delete.txt")
    _git(linked, "commit", "-m", "add deletion target")
    leaf.unlink()
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=("deleted-zone/tracked-delete.txt",),
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )
    original = linked / "deleted-zone-original"
    zone.rename(original)
    escape = tmp_path / "deleted-escape"
    escape.mkdir()
    (escape / "tracked-delete.txt").write_text(
        "must not inspect\n",
        encoding="utf-8",
    )
    zone.symlink_to(escape, target_is_directory=True)

    with pytest.raises(
        LinkedGitBrokerError,
        match="approved parent chain|without symlinks",
    ):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )


def test_broker_rejects_direct_strategy_and_control_agent_ids(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    (linked / "tracked.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(LinkedGitBrokerError, match="broker-required split profiles"):
        prepare_linked_git_commit(
            agent_id="operator-7-fork-test",
            work_root=linked,
            approved_roots=(tmp_path,),
            approved_stage_paths=("tracked.txt",),
        )

    codex_home = _split_codex_home(tmp_path)
    for value in ("bad\nname", "badname\n", "bad\rname", "bad\tname", "bad\0name"):
        with pytest.raises(LinkedGitBrokerError, match="control characters"):
            prepare_linked_git_commit(
                agent_id=value,
                work_root=linked,
                approved_roots=(tmp_path,),
                approved_stage_paths=("tracked.txt",),
                codex_home=codex_home,
            )


def test_broker_handles_z_safe_unusual_utf8_paths(tmp_path: Path) -> None:
    _, linked = _fixture(tmp_path)
    codex_home = _split_codex_home(tmp_path)
    paths = (
        "comma,name.txt",
        "tab\tname.txt",
        "line\nname.txt",
        ":leading-colon.txt",
        "naïve-蜂.txt",
    )
    for name in paths:
        (linked / name).write_text(f"{name}\n", encoding="utf-8")
    receipt = prepare_linked_git_commit(
        agent_id="operator-7-fork-test",
        work_root=linked,
        approved_roots=(tmp_path,),
        approved_stage_paths=paths,
        codex_home=codex_home,
        authored_at=1_700_000_000,
    )
    result = execute_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=tmp_path / "journal",
        codex_home=codex_home,
    )

    assert result.committed_paths == tuple(sorted(paths))
    committed = _git(linked, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD")
    assert tuple(sorted(item for item in committed.split("\0") if item)) == tuple(sorted(paths))


@pytest.mark.parametrize(
    ("phase", "committed"),
    (
        ("prepared", False),
        ("objects_prepared", False),
        ("objects_installing", False),
        ("objects_installed", False),
        ("ready", False),
        ("index_replaced", False),
        ("index_installed", False),
        ("ref_replaced", True),
        ("ref_updated", True),
        ("done", True),
    ),
)
def test_true_process_death_is_decidable_at_every_boundary(
    tmp_path: Path,
    phase: str,
    committed: bool,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    old_head = _git(linked, "rev-parse", "HEAD")
    journal = tmp_path / "journal"
    context = multiprocessing.get_context("fork")
    process = context.Process(
        target=_crash_execute,
        args=(receipt, tmp_path, journal, codex_home, phase),
    )
    process.start()
    process.join(20)
    assert process.exitcode == 97

    if committed:
        recovered = recover_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=journal,
            codex_home=codex_home,
        )
        assert recovered is not None and recovered.idempotent is True
        assert _git(linked, "rev-parse", "HEAD") != old_head
    else:
        with pytest.raises(LinkedGitBrokerError, match="prepare a new approval"):
            recover_linked_git_commit(
                receipt,
                approved_roots=(tmp_path,),
                journal_root=journal,
                codex_home=codex_home,
            )
        assert _git(linked, "rev-parse", "HEAD") == old_head
        assert _git(linked, "diff", "--cached", "--name-only") == ""


def test_recovery_matrix_old_new_ref_and_index_identities(tmp_path: Path) -> None:
    combinations = (
        ("old", "original", "prepare a new approval"),
        ("old", "prepared", "prepare a new approval"),
        ("old", "unknown", "manual review required"),
        ("new", "original", "manual review required"),
        ("new", "prepared", None),
        ("new", "unknown", "manual review required"),
    )
    for ref_state, index_state, error in combinations:
        case = tmp_path / f"{ref_state}-{index_state}"
        case.mkdir()
        _, linked, codex_home, receipt = _prepare(case, stage_paths=("tracked.txt",))
        with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
            execute_linked_git_commit(
                receipt,
                approved_roots=(case,),
                journal_root=case / "journal",
                codex_home=codex_home,
                fault_at="ref_updated",
            )
        git_dir, common, ref, reflog = _repo_receipt_paths(linked)
        txn = case / "journal" / receipt.transaction_id
        if ref_state == "old":
            ref.write_bytes((txn / "original.ref").read_bytes())
            reflog.write_bytes((txn / "original.reflog").read_bytes())
        if index_state == "original":
            (git_dir / "index").write_bytes((txn / "original.index").read_bytes())
        elif index_state == "unknown":
            (git_dir / "index").write_bytes((git_dir / "index").read_bytes() + b"unknown")
        if error is None:
            recovered = recover_linked_git_commit(
                receipt,
                approved_roots=(case,),
                journal_root=case / "journal",
                codex_home=codex_home,
            )
            assert recovered is not None and recovered.idempotent is True
        else:
            with pytest.raises(LinkedGitBrokerError, match=error):
                recover_linked_git_commit(
                    receipt,
                    approved_roots=(case,),
                    journal_root=case / "journal",
                    codex_home=codex_home,
                )


def test_expected_reflog_bytes_match_fixed_update_ref(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
            fault_at="ref_updated",
        )
    _, _, ref, reflog = _repo_receipt_paths(linked)
    state = json.loads(
        (tmp_path / "journal" / receipt.transaction_id / "state.json").read_text()
    )
    assert _sha256(ref.read_bytes()) == state["updated_ref_sha256"]
    assert _sha256(reflog.read_bytes()) == state["updated_reflog_sha256"]
    expected_tail = (
        f"{receipt.head} {state['new_head']} {receipt.author_name} "
        f"<{receipt.author_email}> {receipt.authored_at} +0000\t"
        f"agent-pbx broker {receipt.transaction_id[:12]}\n"
    ).encode()
    assert reflog.read_bytes().endswith(expected_tail)


def test_postverify_failure_performs_exact_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    git_dir, _, ref, reflog = _repo_receipt_paths(linked)
    before = (git_dir / "index").read_bytes(), ref.read_bytes(), reflog.read_bytes()

    def fail_postverify(*args, **kwargs):
        raise LinkedGitBrokerError("forced postverify failure")

    monkeypatch.setattr(broker, "_postverify", fail_postverify)
    with pytest.raises(LinkedGitBrokerError, match="exact rollback completed"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )

    assert _git(linked, "rev-parse", "HEAD") == receipt.head
    assert ((git_dir / "index").read_bytes(), ref.read_bytes(), reflog.read_bytes()) == before
    state = json.loads(
        (tmp_path / "journal" / receipt.transaction_id / "state.json").read_text()
    )
    assert state["phase"] == "rolled_back"


def test_update_ref_failure_restores_index_and_verifies_original_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    git_dir, _, ref, reflog = _repo_receipt_paths(linked)
    before = (git_dir / "index").read_bytes(), ref.read_bytes(), reflog.read_bytes()
    original_run = broker._git_run

    def fail_update_ref(root: Path, *args: str, **kwargs):
        if args and args[0] == "update-ref":
            raise LinkedGitBrokerError("forced update-ref failure")
        return original_run(root, *args, **kwargs)

    monkeypatch.setattr(broker, "_git_run", fail_update_ref)
    with pytest.raises(LinkedGitBrokerError, match="forced update-ref failure"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )
    assert ((git_dir / "index").read_bytes(), ref.read_bytes(), reflog.read_bytes()) == before


def test_index_lock_and_concurrent_index_race_preserve_external_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    git_dir, _, _, _ = _repo_receipt_paths(linked)
    index_lock = git_dir / "index.lock"
    index_lock.write_bytes(b"stale")
    with pytest.raises(LinkedGitBrokerError, match="index is locked"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal-stale",
            codex_home=codex_home,
        )
    assert index_lock.read_bytes() == b"stale"
    index_lock.unlink()

    race_root = tmp_path / "race"
    race_root.mkdir()
    _, race_linked, race_home, race_receipt = _prepare(
        race_root,
        stage_paths=("tracked.txt",),
    )
    original_replace = broker._replace_index_locked

    def race_replace(*, source: Path, target: Path, expected_current_sha256: str):
        _git(race_linked, "add", "new.txt")
        return original_replace(
            source=source,
            target=target,
            expected_current_sha256=expected_current_sha256,
        )

    monkeypatch.setattr(broker, "_replace_index_locked", race_replace)
    with pytest.raises(LinkedGitBrokerError, match="concurrent staging was preserved"):
        execute_linked_git_commit(
            race_receipt,
            approved_roots=(race_root,),
            journal_root=race_root / "journal",
            codex_home=race_home,
        )
    assert _git(race_linked, "diff", "--cached", "--name-only") == "new.txt"
    assert _git(race_linked, "rev-parse", "HEAD") == race_receipt.head


def test_concurrent_ref_race_is_not_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    foreign = _git(
        main,
        "commit-tree",
        receipt.head_tree,
        "-p",
        receipt.head,
        "-m",
        "foreign race",
    )
    original_run = broker._git_run
    raced = False

    def race_update(root: Path, *args: str, **kwargs):
        nonlocal raced
        if args and args[0] == "update-ref" and not raced:
            raced = True
            _git(linked, "update-ref", receipt.branch_ref, foreign, receipt.head)
        return original_run(root, *args, **kwargs)

    monkeypatch.setattr(broker, "_git_run", race_update)
    with pytest.raises(LinkedGitBrokerError):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )
    assert _git(linked, "rev-parse", receipt.branch_ref) == foreign
    assert _git(linked, "diff", "--cached", "--name-only") == ""


def test_authority_config_branch_and_upstream_drift_fail_before_mutation(tmp_path: Path) -> None:
    main, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    with pytest.raises(LinkedGitBrokerError, match="approved_roots differ"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path, linked),
            journal_root=tmp_path / "journal-roots",
            codex_home=codex_home,
        )
    (codex_home / "config.toml").write_text("# drift\n", encoding="utf-8")
    with pytest.raises(LinkedGitBrokerError, match="configuration identity drifted"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal-config",
            codex_home=codex_home,
        )

    drift_root = tmp_path / "upstream"
    drift_root.mkdir()
    upstream_main, upstream_linked, upstream_home, upstream_receipt = _prepare(
        drift_root,
        stage_paths=("tracked.txt",),
    )
    (upstream_main / "upstream.txt").write_text("advance\n", encoding="utf-8")
    _git(upstream_main, "add", "upstream.txt")
    _git(upstream_main, "commit", "-m", "advance upstream")
    with pytest.raises(LinkedGitBrokerError, match="approval receipt drifted"):
        execute_linked_git_commit(
            upstream_receipt,
            approved_roots=(drift_root,),
            journal_root=drift_root / "journal",
            codex_home=upstream_home,
        )
    assert _git(upstream_linked, "rev-parse", "HEAD") == upstream_receipt.head


def test_journal_root_symlink_mode_owner_stale_lock_and_orphan_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    symlink_root = tmp_path / "journal-link"
    symlink_root.symlink_to(victim, target_is_directory=True)
    with pytest.raises(LinkedGitBrokerError, match="unsafe broker journal root|symlink"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=symlink_root,
            codex_home=codex_home,
        )
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755

    bad_mode = tmp_path / "journal-mode"
    bad_mode.mkdir(mode=0o755)
    with pytest.raises(LinkedGitBrokerError, match="unsafe broker journal root"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=bad_mode,
            codex_home=codex_home,
        )
    assert stat.S_IMODE(bad_mode.stat().st_mode) == 0o755

    owner_root = tmp_path / "journal-owner"
    owner_root.mkdir(mode=0o700)
    real_uid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_uid + 1)
    with pytest.raises(LinkedGitBrokerError, match="unsafe broker journal root"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=owner_root,
            codex_home=codex_home,
        )
    monkeypatch.undo()

    stale_root = tmp_path / "journal-stale-flock"
    stale_root.mkdir(mode=0o700)
    stale = stale_root / f"{receipt.transaction_id}.lock"
    stale.write_bytes(b"old pid")
    stale.chmod(0o600)
    result = execute_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=stale_root,
        codex_home=codex_home,
    )
    assert result.status == "committed"

    orphan_root = tmp_path / "orphan-case"
    orphan_root.mkdir()
    _, orphan_linked, orphan_home, orphan_receipt = _prepare(
        orphan_root,
        stage_paths=("tracked.txt",),
    )
    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            orphan_receipt,
            approved_roots=(orphan_root,),
            journal_root=orphan_root / "journal",
            codex_home=orphan_home,
            fault_at="prepared",
        )
    txn = orphan_root / "journal" / orphan_receipt.transaction_id
    os.replace(txn / "state.json", txn / "state.tmp")
    with pytest.raises(LinkedGitBrokerError, match="prepare a new approval"):
        recover_linked_git_commit(
            orphan_receipt,
            approved_roots=(orphan_root,),
            journal_root=orphan_root / "journal",
            codex_home=orphan_home,
        )
    assert _git(orphan_linked, "rev-parse", "HEAD") == orphan_receipt.head


def test_done_idempotency_reruns_complete_approved_path_verification(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    execute_linked_git_commit(
        receipt,
        approved_roots=(tmp_path,),
        journal_root=tmp_path / "journal",
        codex_home=codex_home,
    )
    (linked / "tracked.txt").write_text("post-commit drift\n", encoding="utf-8")

    with pytest.raises(LinkedGitBrokerError, match="approved (file identity|bytes) drifted"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )


def test_journal_canonical_path_active_flock_and_partial_temp_fail_closed(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    with pytest.raises(LinkedGitBrokerError, match="canonical path"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "child" / ".." / "journal",
            codex_home=codex_home,
        )

    journal = tmp_path / "locked-journal"
    journal.mkdir(mode=0o700)
    lock = journal / f"{receipt.transaction_id}.lock"
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(LinkedGitBrokerError, match="already locked"):
            execute_linked_git_commit(
                receipt,
                approved_roots=(tmp_path,),
                journal_root=journal,
                codex_home=codex_home,
            )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert _git(linked, "rev-parse", "HEAD") == receipt.head

    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    _, partial_linked, partial_home, partial_receipt = _prepare(
        partial_root,
        stage_paths=("tracked.txt",),
    )
    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            partial_receipt,
            approved_roots=(partial_root,),
            journal_root=partial_root / "journal",
            codex_home=partial_home,
            fault_at="prepared",
        )
    txn = partial_root / "journal" / partial_receipt.transaction_id
    (txn / "state.tmp").write_bytes(b"{partial")
    (txn / "state.tmp").chmod(0o600)
    with pytest.raises(LinkedGitBrokerError, match="unable to read broker journal"):
        recover_linked_git_commit(
            partial_receipt,
            approved_roots=(partial_root,),
            journal_root=partial_root / "journal",
            codex_home=partial_home,
        )
    assert _git(partial_linked, "rev-parse", "HEAD") == partial_receipt.head


def test_receipt_and_ref_reflog_drift_are_rejected_without_overwrite(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    wrong_home = tmp_path / "wrong-codex-home"
    wrong_home.mkdir()
    (wrong_home / "config.toml").write_text("", encoding="utf-8")
    with pytest.raises(LinkedGitBrokerError, match="Codex home differs"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal-home",
            codex_home=wrong_home,
        )

    _, _, ref, reflog = _repo_receipt_paths(linked)
    original_ref = ref.read_bytes()
    reflog.write_bytes(reflog.read_bytes() + b"foreign reflog drift\n")
    with pytest.raises(LinkedGitBrokerError, match="approval receipt drifted"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal-ref",
            codex_home=codex_home,
        )
    assert ref.read_bytes() == original_ref


def test_journal_receipt_byte_tamper_is_rejected(tmp_path: Path) -> None:
    _, linked, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    with pytest.raises(LinkedGitBrokerError, match="injected broker fault"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
            fault_at="prepared",
        )
    state_path = tmp_path / "journal" / receipt.transaction_id / "state.json"
    state = json.loads(state_path.read_text())
    state["receipt"]["agent_id"] = "tampered"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_path.chmod(0o600)
    with pytest.raises(LinkedGitBrokerError, match="receipt bytes"):
        recover_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=tmp_path / "journal",
            codex_home=codex_home,
        )
    assert _git(linked, "rev-parse", "HEAD") == receipt.head


def test_pending_loader_accepts_exact_pending_and_excludes_terminal_states(
    tmp_path: Path,
) -> None:
    pending_case = tmp_path / "pending"
    pending_case.mkdir()
    _, linked, codex_home, receipt = _prepare(
        pending_case,
        stage_paths=("tracked.txt",),
    )
    journal = pending_case / "journal"
    with pytest.raises(LinkedGitBrokerError, match="objects_prepared"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(pending_case,),
            journal_root=journal,
            codex_home=codex_home,
            fault_at="objects_prepared",
        )
    loaded = load_pending_linked_git_commits(journal)
    assert len(loaded) == 1
    assert loaded[0].receipt == receipt
    assert loaded[0].phase == "objects_prepared"
    with pytest.raises(LinkedGitBrokerError, match="prepare a new approval"):
        recover_linked_git_commit(
            receipt,
            approved_roots=(pending_case,),
            journal_root=journal,
            codex_home=codex_home,
        )
    assert load_pending_linked_git_commits(journal) == ()
    assert _git(linked, "rev-parse", "HEAD") == receipt.head

    done_case = tmp_path / "done"
    done_case.mkdir()
    _, _, done_home, done_receipt = _prepare(
        done_case,
        stage_paths=("tracked.txt",),
    )
    done_journal = done_case / "journal"
    execute_linked_git_commit(
        done_receipt,
        approved_roots=(done_case,),
        journal_root=done_journal,
        codex_home=done_home,
    )
    assert load_pending_linked_git_commits(done_journal) == ()


def test_pending_loader_accepts_explicit_reviewed_subjects(
    tmp_path: Path,
) -> None:
    pending_case = tmp_path / "pending-custom-subject"
    pending_case.mkdir()
    _, _, codex_home, receipt = _prepare(
        pending_case,
        stage_paths=("tracked.txt",),
        commit_subject="docs: preserve the reviewed evidence boundary",
    )
    journal = pending_case / "journal"
    with pytest.raises(LinkedGitBrokerError, match="objects_prepared"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(pending_case,),
            journal_root=journal,
            codex_home=codex_home,
            fault_at="objects_prepared",
        )

    loaded = load_pending_linked_git_commits(journal)
    assert len(loaded) == 1
    assert loaded[0].receipt.commit_subject == receipt.commit_subject

    done_case = tmp_path / "done-custom-subject"
    done_case.mkdir()
    _, _, done_home, done_receipt = _prepare(
        done_case,
        stage_paths=("tracked.txt",),
        commit_subject="ci: retain the exact reviewed release scope",
    )
    done_journal = done_case / "journal"
    execute_linked_git_commit(
        done_receipt,
        approved_roots=(done_case,),
        journal_root=done_journal,
        codex_home=done_home,
    )
    assert load_pending_linked_git_commits(done_journal) == ()


@pytest.mark.parametrize(
    "tamper",
    (
        "unknown_phase",
        "extra_field",
        "missing_field",
        "wrong_type",
        "invalid_digest",
        "invalid_object_id",
        "reordered_progress",
        "prepared_with_progress",
        "installing_with_complete_progress",
        "ready_with_partial_progress",
    ),
)
def test_corrupt_journal_state_never_reinterprets_exact_old_repository(
    tmp_path: Path,
    tamper: str,
) -> None:
    _, linked, codex_home, receipt = _prepare(
        tmp_path,
        stage_paths=("tracked.txt",),
    )
    journal = tmp_path / "journal"
    old_head = _git(linked, "rev-parse", "HEAD")
    git_dir = Path(_git(linked, "rev-parse", "--absolute-git-dir"))
    old_index = (git_dir / "index").read_bytes()
    with pytest.raises(LinkedGitBrokerError, match="objects_installing"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=journal,
            codex_home=codex_home,
            fault_at="objects_installing",
        )
    state_path = journal / receipt.transaction_id / "state.json"
    state = json.loads(state_path.read_text())
    assert state["phase"] == "objects_installing"
    assert len(state["installed_object_oids"]) == 1
    if tamper == "unknown_phase":
        state["phase"] = "future_guess"
    elif tamper == "extra_field":
        state["unexpected"] = "value"
    elif tamper == "missing_field":
        state.pop("tree")
    elif tamper == "wrong_type":
        state["phase"] = ["objects_installing"]
    elif tamper == "invalid_digest":
        state["original_index_sha256"] = "not-a-digest"
    elif tamper == "invalid_object_id":
        state["tree"] = "not-an-object"
    elif tamper == "reordered_progress":
        state["installed_object_oids"] = [state["created_object_oids"][1]]
    elif tamper == "prepared_with_progress":
        state["phase"] = "objects_prepared"
    elif tamper == "installing_with_complete_progress":
        state["installed_object_oids"] = list(state["created_object_oids"])
    else:
        state["phase"] = "ready"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_path.chmod(0o600)
    corrupt_bytes = state_path.read_bytes()

    with pytest.raises(LinkedGitBrokerError):
        load_pending_linked_git_commits(journal)
    with pytest.raises(LinkedGitBrokerError):
        recover_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=journal,
            codex_home=codex_home,
        )

    assert state_path.read_bytes() == corrupt_bytes
    assert _git(linked, "rev-parse", "HEAD") == old_head
    assert (git_dir / "index").read_bytes() == old_index


@pytest.mark.parametrize(
    "tamper",
    (
        "missing",
        "extra",
        "wrong_type",
        "control",
        "subject_control",
        "subject_too_long",
        "transaction",
    ),
)
def test_pending_loader_rejects_noncanonical_receipt_schema(
    tmp_path: Path,
    tamper: str,
) -> None:
    _, _, codex_home, receipt = _prepare(tmp_path, stage_paths=("tracked.txt",))
    journal = tmp_path / "journal"
    with pytest.raises(LinkedGitBrokerError, match="objects_prepared"):
        execute_linked_git_commit(
            receipt,
            approved_roots=(tmp_path,),
            journal_root=journal,
            codex_home=codex_home,
            fault_at="objects_prepared",
        )
    state_path = journal / receipt.transaction_id / "state.json"
    state = json.loads(state_path.read_text())
    if tamper == "missing":
        state["receipt"].pop("upstream")
    elif tamper == "extra":
        state["receipt"]["unexpected"] = "value"
    elif tamper == "wrong_type":
        state["receipt"]["approved_roots"] = {"bad": "type"}
    elif tamper == "control":
        state["receipt"]["agent_id"] = "operator-7\nforged"
    elif tamper == "subject_control":
        state["receipt"]["commit_subject"] = "fix: reviewed\nforged"
    elif tamper == "subject_too_long":
        state["receipt"]["commit_subject"] = "x" * 161
    else:
        state["receipt"]["transaction_id"] = "0" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_path.chmod(0o600)

    with pytest.raises(LinkedGitBrokerError):
        load_pending_linked_git_commits(journal)


def test_pending_loader_rejects_unsafe_root_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    symlink_root = tmp_path / "symlink-root"
    symlink_root.mkdir(mode=0o700)
    target = tmp_path / "target"
    target.write_text("target", encoding="utf-8")
    (symlink_root / f"{'a' * 64}.lock").symlink_to(target)
    with pytest.raises(LinkedGitBrokerError, match="lock entry"):
        load_pending_linked_git_commits(symlink_root)

    mode_root = tmp_path / "mode-root"
    mode_root.mkdir(mode=0o700)
    bad_mode = mode_root / f"{'b' * 64}.lock"
    bad_mode.write_text("", encoding="utf-8")
    bad_mode.chmod(0o644)
    with pytest.raises(LinkedGitBrokerError, match="lock entry"):
        load_pending_linked_git_commits(mode_root)

    type_root = tmp_path / "type-root"
    type_root.mkdir(mode=0o700)
    (type_root / f"{'c' * 64}.lock").mkdir(mode=0o700)
    with pytest.raises(LinkedGitBrokerError, match="lock entry"):
        load_pending_linked_git_commits(type_root)

    foreign_root = tmp_path / "foreign-root"
    foreign_root.mkdir(mode=0o700)
    (foreign_root / "foreign").write_text("unexpected", encoding="utf-8")
    (foreign_root / "foreign").chmod(0o600)
    with pytest.raises(LinkedGitBrokerError, match="unexpected.*entry"):
        load_pending_linked_git_commits(foreign_root)

    owner_root = tmp_path / "owner-root"
    owner_root.mkdir(mode=0o700)
    owner_lock = owner_root / f"{'d' * 64}.lock"
    owner_lock.write_text("", encoding="utf-8")
    owner_lock.chmod(0o600)
    original_lstat = Path.lstat

    def foreign_owner(path: Path):
        result = original_lstat(path)
        if path == owner_lock:
            values = list(result)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "lstat", foreign_owner)
    with pytest.raises(LinkedGitBrokerError, match="lock entry"):
        load_pending_linked_git_commits(owner_root)
