from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile

import pytest


SCRIPTS = Path(__file__).parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_release  # noqa: E402
import verify_pypi_release  # noqa: E402


def write_release_tree(root: Path, *, version: str = "2.1.1") -> None:
    package = root / "src" / "agent_pbx"
    package.mkdir(parents=True)
    package.joinpath("__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    root.joinpath("CHANGELOG.md").write_text(
        f"# Changelog\n\n## v{version} - 2026-10-09\n\n- Release.\n",
        encoding="utf-8",
    )
    notes = root / "docs" / "releases"
    notes.mkdir(parents=True)
    notes.joinpath(f"v{version}.md").write_text(
        f"# Agent PBX {version}\n", encoding="utf-8"
    )


def build_fixture_distributions(
    dist: Path,
    *,
    version: str = "2.1.1",
    extra_member: str | None = None,
) -> tuple[Path, Path]:
    dist.mkdir()
    wheel = dist / f"agent_pbx-{version}-py3-none-any.whl"
    wheel_metadata = f"Metadata-Version: 2.4\nName: agent-pbx\nVersion: {version}\n\n"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"agent_pbx-{version}.dist-info/METADATA", wheel_metadata)
        archive.writestr("agent_pbx/__init__.py", f'__version__ = "{version}"\n')
        if extra_member:
            archive.writestr(extra_member, "private")

    sdist = dist / f"agent_pbx-{version}.tar.gz"
    root = f"agent_pbx-{version}"
    with tarfile.open(sdist, "w:gz") as archive:
        payloads = {
            f"{root}/PKG-INFO": wheel_metadata.encode(),
            f"{root}/src/agent_pbx/__init__.py": f'__version__ = "{version}"\n'.encode(),
        }
        if extra_member:
            payloads[f"{root}/{extra_member}"] = b"private"
        for name, payload in payloads.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, BytesIO(payload))
    return wheel, sdist


def test_release_tag_channels_are_strict() -> None:
    assert validate_release.version_from_tag("v2.1.1", channel="production") == "2.1.1"
    assert validate_release.version_from_tag("v2.1.1rc1", channel="testpypi") == "2.1.1rc1"
    with pytest.raises(validate_release.ReleaseValidationError, match="vMAJOR.MINOR.PATCH"):
        validate_release.version_from_tag("v2.1.1rc1", channel="production")
    with pytest.raises(validate_release.ReleaseValidationError, match="vMAJOR.MINOR.PATCHrcN"):
        validate_release.version_from_tag("v2.1.1", channel="testpypi")
    with pytest.raises(validate_release.ReleaseValidationError):
        validate_release.version_from_tag("v2.1.1-rc1", channel="testpypi")
    with pytest.raises(validate_release.ReleaseValidationError):
        validate_release.version_from_tag("v2.1.1rc0", channel="testpypi")


def test_metadata_validation_matches_tag_version_and_release_documents(tmp_path: Path) -> None:
    write_release_tree(tmp_path)
    result = validate_release.validate_metadata(
        tmp_path,
        tag="v2.1.1",
        channel="production",
        branch="origin/dev",
        index_json_url=None,
        check_git=False,
    )
    assert result == {
        "channel": "production",
        "commit": "not-checked",
        "project": "agent-pbx",
        "tag": "v2.1.1",
        "version": "2.1.1",
    }


def test_metadata_validation_rejects_version_mismatch(tmp_path: Path) -> None:
    write_release_tree(tmp_path, version="2.1.0")
    with pytest.raises(validate_release.ReleaseValidationError, match="does not match"):
        validate_release.validate_metadata(
            tmp_path,
            tag="v2.1.1",
            channel="production",
            branch="origin/dev",
            index_json_url=None,
            check_git=False,
        )


def test_metadata_validation_requires_release_note(tmp_path: Path) -> None:
    write_release_tree(tmp_path)
    (tmp_path / "docs" / "releases" / "v2.1.1.md").unlink()
    with pytest.raises(validate_release.ReleaseValidationError, match="release note"):
        validate_release.validate_release_documents(
            tmp_path, tag="v2.1.1", version="2.1.1"
        )


def test_package_index_duplicate_is_rejected() -> None:
    def opener(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return BytesIO(json.dumps({"releases": {"2.1.1": [{}]}}).encode())

    with pytest.raises(validate_release.ReleaseValidationError, match="already exists"):
        validate_release.ensure_version_is_new(
            index_json_url="https://example.invalid/pypi/agent-pbx/json",
            version="2.1.1",
            opener=opener,
        )


def test_git_identity_requires_tagged_head_contained_in_branch(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    tmp_path.joinpath("file.txt").write_text("release\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Release"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "-a", "v2.1.1", "-m", "v2.1.1"], cwd=tmp_path, check=True)

    commit = validate_release.validate_git_identity(
        tmp_path, tag="v2.1.1", branch="dev"
    )
    assert len(commit) == 40

    tmp_path.joinpath("file.txt").write_text("after\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "After"], cwd=tmp_path, check=True)
    with pytest.raises(validate_release.ReleaseValidationError, match="does not match"):
        validate_release.validate_git_identity(tmp_path, tag="v2.1.1", branch="dev")


def test_git_identity_rejects_lightweight_tag(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    tmp_path.joinpath("file.txt").write_text("release\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Release"], cwd=tmp_path, check=True)
    subprocess.run(["git", "tag", "v2.1.1"], cwd=tmp_path, check=True)

    with pytest.raises(validate_release.ReleaseValidationError, match="annotated"):
        validate_release.validate_git_identity(tmp_path, tag="v2.1.1", branch="dev")


def test_archive_validation_accepts_expected_wheel_and_sdist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    build_fixture_distributions(dist)

    result = validate_release.validate_archives(dist, version="2.1.1")

    assert result["project"] == "agent-pbx"
    assert result["version"] == "2.1.1"
    assert [item["file"] for item in result["artifacts"]] == [
        "agent_pbx-2.1.1-py3-none-any.whl",
        "agent_pbx-2.1.1.tar.gz",
    ]


@pytest.mark.parametrize("private_path", [".env", "local.env", "state/agent-pbx.db", ".codex/config.toml"])
def test_archive_validation_rejects_private_runtime_content(
    tmp_path: Path, private_path: str
) -> None:
    dist = tmp_path / "dist"
    build_fixture_distributions(dist, extra_member=private_path)

    with pytest.raises(validate_release.ReleaseValidationError, match="local/generated|private/runtime"):
        validate_release.validate_archives(dist, version="2.1.1")


def test_pypi_verification_matches_exact_local_distribution_hashes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    wheel, sdist = build_fixture_distributions(dist)
    metadata = {
        "urls": [
            {"filename": wheel.name, "digests": {"sha256": validate_release.sha256(wheel)}},
            {"filename": sdist.name, "digests": {"sha256": validate_release.sha256(sdist)}},
        ]
    }

    verified = verify_pypi_release.verify_distribution_hashes(metadata, dist)

    assert {item["file"] for item in verified} == {wheel.name, sdist.name}


def test_pypi_verification_rejects_unexpected_remote_distribution(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    wheel, sdist = build_fixture_distributions(dist)
    metadata = {
        "urls": [
            {"filename": wheel.name, "digests": {"sha256": validate_release.sha256(wheel)}},
            {"filename": sdist.name, "digests": {"sha256": validate_release.sha256(sdist)}},
            {"filename": "unexpected.whl", "digests": {"sha256": "0" * 64}},
        ]
    }

    with pytest.raises(validate_release.ReleaseValidationError, match="unexpected files"):
        verify_pypi_release.verify_distribution_hashes(metadata, dist)


def test_production_workflow_has_narrow_trusted_publishing_boundary() -> None:
    root = Path(__file__).parents[1]
    workflow = root.joinpath(".github/workflows/publish-pypi.yml").read_text(
        encoding="utf-8"
    )

    assert "release:\n    types: [published]" in workflow
    assert "workflow_dispatch" not in workflow
    assert "name: pypi" in workflow
    assert workflow.count("id-token: write") == 1
    assert "skip-existing" not in workflow
    assert "password:" not in workflow
    assert "user:" not in workflow


def test_testpypi_workflow_is_manual_tag_only_and_cannot_select_production() -> None:
    root = Path(__file__).parents[1]
    workflow = root.joinpath(".github/workflows/publish-testpypi.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert 'RELEASE_REF_TYPE" != "tag"' in workflow
    assert "name: testpypi" in workflow
    assert "https://test.pypi.org/legacy/" in workflow
    assert "environment:\n      name: pypi" not in workflow
    assert workflow.count("id-token: write") == 1


def test_release_workflows_pin_every_external_action_to_commit_sha() -> None:
    root = Path(__file__).parents[1]
    workflows = [
        root / ".github/workflows/publish-pypi.yml",
        root / ".github/workflows/publish-testpypi.yml",
        root / ".github/workflows/release-quality.yml",
        root / ".github/workflows/v2-release-gate.yml",
    ]
    external_action = re.compile(r"^\s*uses:\s+([^./][^@]+)@([^\s#]+)", re.MULTILINE)

    discovered = []
    for path in workflows:
        for action, reference in external_action.findall(path.read_text(encoding="utf-8")):
            discovered.append((action, reference))
            assert re.fullmatch(r"[0-9a-f]{40}", reference), (path, action, reference)
    assert discovered
