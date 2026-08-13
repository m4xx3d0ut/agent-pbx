from pathlib import Path

import pytest

from agent_pbx.project_spawn import (
    normalize_project_slug,
    resolve_sibling_project_paths,
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
