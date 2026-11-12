from __future__ import annotations

from dataclasses import replace

import pytest

from agent_pbx.git_review import (
    GIT_REVIEW_MAX_PATH_DIFF_BYTES,
    GitReviewArtifact,
    GitReviewArtifactError,
    GitReviewContent,
    build_git_review_artifact,
)


HEAD = "a" * 40
TREE = "b" * 40
INDEX_SHA256 = "c" * 64


def _content(
    payload: bytes | None,
    *,
    mode: str | None = "100644",
    oid: str | None = "d" * 40,
    complete: bool = True,
) -> GitReviewContent:
    if payload is None:
        return GitReviewContent(
            mode=None,
            oid=None,
            size=0,
            prefix=b"",
            complete=True,
        )
    return GitReviewContent(
        mode=mode,
        oid=oid,
        size=len(payload),
        prefix=payload,
        complete=complete,
    )


def _artifact(
    paths: tuple[str, ...],
    old: dict[str, GitReviewContent],
    new: dict[str, GitReviewContent],
    *,
    parents: tuple[str, ...] = (HEAD,),
) -> GitReviewArtifact:
    return build_git_review_artifact(
        agent_id="operator-7-fork-test",
        branch_ref="refs/heads/pbx-operator-7/review",
        head=HEAD,
        head_tree=TREE,
        index_tree=TREE,
        index_sha256=INDEX_SHA256,
        parent_oids=parents,
        approved_paths=paths,
        old_content=old,
        new_content=new,
    )


def test_git_review_artifact_covers_added_deleted_mode_and_binary_paths() -> None:
    paths = ("added.txt", "binary.dat", "deleted.txt", "mode.sh")
    artifact = _artifact(
        paths,
        {
            "added.txt": _content(None),
            "binary.dat": _content(b"old\0bytes", oid="1" * 40),
            "deleted.txt": _content(b"gone\n", oid="2" * 40),
            "mode.sh": _content(b"echo ok\n", mode="100644", oid="3" * 40),
        },
        {
            "added.txt": _content(b"new\n", oid="4" * 40),
            "binary.dat": _content(b"new\0bytes", oid="5" * 40),
            "deleted.txt": _content(None),
            "mode.sh": _content(b"echo ok\n", mode="100755", oid="3" * 40),
        },
    )

    assert tuple(item.status for item in artifact.paths) == (
        "added",
        "modified",
        "deleted",
        "mode_changed",
    )
    assert artifact.binary_path_count == 1
    assert "+++ b/added.txt" in artifact.paths[0].unified_diff
    assert artifact.paths[1].unified_diff == ""
    assert artifact.total_additions == 1
    assert artifact.total_deletions == 1
    assert GitReviewArtifact.from_public_dict(artifact.public_dict()) == artifact


def test_git_review_artifact_binds_two_parents_without_changing_primary_diff() -> None:
    second_parent = "e" * 40
    artifact = _artifact(
        ("tracked.txt",),
        {"tracked.txt": _content(b"old\n", oid="1" * 40)},
        {"tracked.txt": _content(b"new\n", oid="2" * 40)},
        parents=(HEAD, second_parent),
    )

    assert artifact.parent_oids == (HEAD, second_parent)
    assert artifact.paths[0].old_oid == "1" * 40
    assert artifact.paths[0].new_oid == "2" * 40


def test_git_review_artifact_large_and_rendered_output_are_bounded() -> None:
    old = ("old line\n" * 20_000).encode()
    new = ("new line\n" * 20_000).encode()
    artifact = _artifact(
        ("large.txt",),
        {"large.txt": _content(old, oid="1" * 40)},
        {"large.txt": _content(new, oid="2" * 40)},
    )

    entry = artifact.paths[0]
    assert entry.truncated is True
    assert entry.truncation_reason == "rendered_diff_limit"
    assert len(entry.unified_diff.encode()) <= GIT_REVIEW_MAX_PATH_DIFF_BYTES
    assert artifact.truncated is True

    omitted = _artifact(
        ("oversize.txt",),
        {
            "oversize.txt": GitReviewContent(
                mode="100644",
                oid="3" * 40,
                size=2_000_000,
                prefix=b"",
                complete=False,
            )
        },
        {"oversize.txt": _content(b"small\n", oid="4" * 40)},
    )
    assert omitted.paths[0].content_kind == "large"
    assert omitted.paths[0].truncation_reason == "source_content_limit"


def test_git_review_artifact_rejects_digest_and_scope_drift() -> None:
    artifact = _artifact(
        ("tracked.txt",),
        {"tracked.txt": _content(b"old\n", oid="1" * 40)},
        {"tracked.txt": _content(b"new\n", oid="2" * 40)},
    )
    tampered = artifact.public_dict()
    tampered["paths"][0]["new_oid"] = "f" * 40
    with pytest.raises(GitReviewArtifactError, match="digest mismatch"):
        GitReviewArtifact.from_public_dict(tampered)

    with pytest.raises(GitReviewArtifactError, match="exact approval"):
        build_git_review_artifact(
            agent_id=artifact.agent_id,
            branch_ref=artifact.branch_ref,
            head=artifact.head,
            head_tree=artifact.head_tree,
            index_tree=artifact.index_tree,
            index_sha256=artifact.index_sha256,
            parent_oids=artifact.parent_oids,
            approved_paths=("tracked.txt",),
            old_content={"other.txt": _content(b"old\n")},
            new_content={"tracked.txt": _content(b"new\n")},
        )


def test_git_review_artifact_rejects_recomputed_but_inconsistent_counts() -> None:
    artifact = _artifact(
        ("tracked.txt",),
        {"tracked.txt": _content(b"old\n", oid="1" * 40)},
        {"tracked.txt": _content(b"new\n", oid="2" * 40)},
    )
    inconsistent = replace(artifact, total_additions=99, sha256="0" * 64)
    with pytest.raises(GitReviewArtifactError, match="addition count mismatch"):
        GitReviewArtifact.from_public_dict(inconsistent.public_dict())


def test_git_review_artifact_escapes_control_bearing_path_only_in_rendering() -> None:
    path = "tab\tnewline\n雪.txt"
    artifact = _artifact(
        (path,),
        {path: _content(b"old\n", oid="1" * 40)},
        {path: _content(b"new\n", oid="2" * 40)},
    )

    assert artifact.approved_paths == (path,)
    assert "tab\\tnewline\\n雪.txt" in artifact.paths[0].unified_diff
    assert "tab\tnewline\n雪.txt" not in artifact.paths[0].unified_diff
