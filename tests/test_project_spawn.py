from pathlib import Path
import subprocess

import pytest

from agent_pbx.project_spawn import (
    normalize_project_slug,
    resolve_sibling_project_paths,
    validate_project_spawn_preflight,
    validate_stored_project_spawn_preflight,
    validate_sibling_project_target,
)


def test_project_spawn_slug_and_sibling_paths(tmp_path: Path) -> None:
    source = tmp_path / "source repo"
    source.mkdir()

    resolved_source, parent, target, slug = resolve_sibling_project_paths(
        str(source),
        "Next Demo!",
    )

    assert resolved_source == source.resolve()
    assert parent == tmp_path.resolve()
    assert target == (tmp_path / "Next-Demo").resolve()
    assert slug == "Next-Demo"
    assert normalize_project_slug("../../bad name") == "bad-name"


def test_project_spawn_validator_rejects_non_sibling_parent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside_parent = tmp_path / "outside"
    outside_parent.mkdir()

    with pytest.raises(ValueError, match="parent"):
        validate_sibling_project_target(
            source,
            outside_parent / "next",
            expected_parent=outside_parent,
        )


def test_project_spawn_preflight_rejects_nonempty_target_with_guidance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "existing"
    target.mkdir()
    (target / "README.md").write_text("existing work", encoding="utf-8")

    with pytest.raises(ValueError, match="managed Agent launcher"):
        validate_project_spawn_preflight(
            str(source),
            "existing",
            mode="empty",
        )


def test_project_spawn_clone_requires_canonical_git_root_and_matching_metadata(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    nested = source / "nested"
    nested.mkdir()

    with pytest.raises(ValueError, match="canonical Git repository root"):
        validate_project_spawn_preflight(
            str(nested),
            "clone-a",
            mode="clone_source",
        )

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="repo_root does not match"):
        validate_project_spawn_preflight(
            str(source),
            "clone-b",
            mode="clone_source",
            metadata={"repo_root": str(other)},
        )

    with pytest.raises(ValueError, match="metadata.repo_root is unavailable"):
        validate_project_spawn_preflight(
            str(source),
            "clone-c",
            mode="clone_source",
            metadata={"repo_root": str(tmp_path / "missing")},
        )


def test_stored_project_spawn_preflight_is_nonmutating_and_repeatable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    request = {
        "source_cwd": str(source),
        "project_name": "new-project",
        "target_slug": "new-project",
        "mode": "empty",
        "metadata": {},
    }

    first = validate_stored_project_spawn_preflight(request)
    second = validate_stored_project_spawn_preflight(request)

    assert first == second
    assert first.target_path == tmp_path / "new-project"
    assert first.target_path.exists() is False
