from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from agent_pbx.wiki_publish import (
    check_wiki_source,
    publish_wiki_stage,
    stage_wiki,
)


def source_tree(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "repo"
    source = repository / "docs" / "wiki"
    source.mkdir(parents=True)
    (repository / "README.md").write_text("# Repository\n", encoding="utf-8")
    (repository / "docs" / "architecture.md").write_text(
        "# Architecture\n", encoding="utf-8"
    )
    (source / "Home.md").write_text(
        "# Home\n\n[Getting Started](Getting-Started.md)\n\n"
        "[Architecture](../architecture.md)\n",
        encoding="utf-8",
    )
    (source / "Getting-Started.md").write_text(
        "# Getting Started\n\n[Repository](../../README.md)\n",
        encoding="utf-8",
    )
    return repository, source


def test_wiki_check_and_stage_are_deterministic(tmp_path: Path) -> None:
    _repository, source = source_tree(tmp_path)
    output = tmp_path / "stage"

    check = check_wiki_source(source)
    first = stage_wiki(source, output, revision="abc123")
    archive_digest = hashlib.sha256(Path(first["archive"]).read_bytes()).hexdigest()
    second = stage_wiki(source, output, revision="abc123")

    assert check.ready is True
    assert first["stage_digest"] == second["stage_digest"]
    assert hashlib.sha256(Path(second["archive"]).read_bytes()).hexdigest() == archive_digest
    home = (output / "pages" / "Home.md").read_text(encoding="utf-8")
    getting_started = (output / "pages" / "Getting-Started.md").read_text(
        encoding="utf-8"
    )
    assert "(Getting-Started)" in home
    assert (
        "https://github.com/m4xx3d0ut/agent-pbx/blob/abc123/docs/architecture.md"
        in home
    )
    assert "https://github.com/m4xx3d0ut/agent-pbx/blob/abc123/README.md" in getting_started
    assert "[Agent PBX Home](Home)" in (output / "pages" / "_Sidebar.md").read_text()
    assert json.loads((output / "manifest.json").read_text())["unknown_remote_pages_preserved"] is True


def test_wiki_check_rejects_private_material_and_broken_links(tmp_path: Path) -> None:
    _repository, source = source_tree(tmp_path)
    (source / "Home.md").write_text(
        "# Home\n\npassword=hunter2\n\n[Missing](Missing.md)\n",
        encoding="utf-8",
    )

    result = check_wiki_source(source)

    assert result.ready is False
    assert any("credential assignment" in error for error in result.errors)
    assert any("missing local link target" in error for error in result.errors)


def test_wiki_stage_refuses_unowned_destination(tmp_path: Path) -> None:
    _repository, source = source_tree(tmp_path)
    output = tmp_path / "stage"
    output.mkdir()
    (output / "operator-file").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="does not own"):
        stage_wiki(source, output)

    assert (output / "operator-file").read_text() == "keep"


def test_wiki_publish_requires_digest_and_preserves_unknown_remote_pages(
    tmp_path: Path,
) -> None:
    _repository, source = source_tree(tmp_path)
    stage = tmp_path / "stage"
    manifest = stage_wiki(source, stage, revision="abc123")
    remote = tmp_path / "project.wiki.git"
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=master", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "init", "--initial-branch=master", str(seed)],
        check=True,
        capture_output=True,
    )
    (seed / "Operator-Page.md").write_text("# Preserve me\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "seed"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(seed), "push", "origin", "master"], check=True, capture_output=True)

    with pytest.raises(ValueError, match="exactly match"):
        publish_wiki_stage(stage, remote=str(remote), confirm="wrong")

    result = publish_wiki_stage(
        stage,
        remote=str(remote),
        confirm=manifest["stage_digest"],
        work_root=tmp_path / "publisher",
    )
    verify = tmp_path / "verify"
    subprocess.run(["git", "clone", "--quiet", str(remote), str(verify)], check=True)

    assert result["published"] is True
    assert (verify / "Home.md").is_file()
    assert (verify / "_Sidebar.md").is_file()
    assert (verify / "Operator-Page.md").read_text() == "# Preserve me\n"


def test_wiki_publish_detects_stage_tampering(tmp_path: Path) -> None:
    _repository, source = source_tree(tmp_path)
    stage = tmp_path / "stage"
    manifest = stage_wiki(source, stage)
    (stage / "pages" / "Home.md").write_text("tampered\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checksum changed"):
        publish_wiki_stage(
            stage,
            remote=str(tmp_path / "unused.wiki.git"),
            confirm=manifest["stage_digest"],
        )
