#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
from datetime import date
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time
from typing import Any, BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile


PROJECT_NAME = "agent-pbx"
NORMALIZED_PROJECT_NAME = "agent_pbx"
STABLE_TAG_PATTERN = re.compile(r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))$")
PRERELEASE_TAG_PATTERN = re.compile(
    r"^v(?P<version>(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)rc[1-9]\d*)$"
)
FORBIDDEN_PARTS = {
    ".codex",
    ".git",
    ".venv",
    "__pycache__",
    "artifacts",
    "runs",
    "state",
}
FORBIDDEN_NAMES = {".env", "local.env"}
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


class ReleaseValidationError(ValueError):
    """A release candidate violates the public release contract."""


def _run_git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=check,
        capture_output=True,
        text=True,
    )


def package_version(root: Path) -> str:
    path = root / "src" / "agent_pbx" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    raise ReleaseValidationError(f"could not read __version__ from {path}")


def version_from_tag(tag: str, *, channel: str) -> str:
    pattern = STABLE_TAG_PATTERN if channel == "production" else PRERELEASE_TAG_PATTERN
    match = pattern.fullmatch(tag)
    if match is None:
        expected = "vMAJOR.MINOR.PATCH" if channel == "production" else "vMAJOR.MINOR.PATCHrcN"
        raise ReleaseValidationError(
            f"{channel} release tag {tag!r} must use {expected}"
        )
    return match.group("version")


def validate_release_documents(root: Path, *, tag: str, version: str) -> None:
    changelog = root / "CHANGELOG.md"
    changelog_text = changelog.read_text(encoding="utf-8")
    heading = re.compile(
        rf"^## {re.escape(tag)} - (?P<date>\d{{4}}-\d{{2}}-\d{{2}})$",
        re.MULTILINE,
    )
    match = heading.search(changelog_text)
    if match is None:
        raise ReleaseValidationError(
            f"{changelog} must contain a heading like '## {tag} - YYYY-MM-DD'"
        )
    try:
        date.fromisoformat(match.group("date"))
    except ValueError as exc:
        raise ReleaseValidationError(f"{changelog} contains an invalid release date") from exc
    release_note = root / "docs" / "releases" / f"v{version}.md"
    if not release_note.is_file() or not release_note.read_text(encoding="utf-8").strip():
        raise ReleaseValidationError(f"release note is missing or empty: {release_note}")


def validate_git_identity(root: Path, *, tag: str, branch: str) -> str:
    try:
        tag_type = _run_git(root, "cat-file", "-t", f"refs/tags/{tag}")
        tag_commit = _run_git(root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}")
        head_commit = _run_git(root, "rev-parse", "HEAD")
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise ReleaseValidationError(f"could not resolve release Git identity: {detail}") from exc
    if tag_type.stdout.strip() != "tag":
        raise ReleaseValidationError(f"release tag {tag} must be an annotated tag")
    tag_sha = tag_commit.stdout.strip()
    head_sha = head_commit.stdout.strip()
    if tag_sha != head_sha:
        raise ReleaseValidationError(
            f"checked-out commit {head_sha} does not match {tag} commit {tag_sha}"
        )
    contained = _run_git(root, "merge-base", "--is-ancestor", tag_sha, branch, check=False)
    if contained.returncode != 0:
        raise ReleaseValidationError(f"{tag} commit {tag_sha} is not contained in {branch}")
    return tag_sha


def _load_index_json(
    url: str,
    *,
    attempts: int = 3,
    opener: Callable[..., BinaryIO] | None = None,
) -> dict[str, Any] | None:
    open_url = opener or urlopen
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "agent-pbx-release-validator"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with open_url(request, timeout=20) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ReleaseValidationError(f"package index returned an invalid document: {url}")
            return payload
        except HTTPError as exc:
            if exc.code == 404:
                return None
            last_error = exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(attempt)
    raise ReleaseValidationError(f"could not query package index {url}: {last_error}")


def ensure_version_is_new(
    *,
    index_json_url: str,
    version: str,
    opener: Callable[..., BinaryIO] | None = None,
) -> None:
    payload = _load_index_json(index_json_url, opener=opener)
    releases = payload.get("releases", {}) if payload else {}
    if not isinstance(releases, dict):
        raise ReleaseValidationError("package index response has no valid releases mapping")
    if version in releases:
        raise ReleaseValidationError(
            f"{PROJECT_NAME} {version} already exists at {index_json_url}; package releases are immutable"
        )


def validate_metadata(
    root: Path,
    *,
    tag: str,
    channel: str,
    branch: str,
    index_json_url: str | None,
    check_git: bool = True,
) -> dict[str, str]:
    version = version_from_tag(tag, channel=channel)
    configured_version = package_version(root)
    if configured_version != version:
        raise ReleaseValidationError(
            f"tag version {version} does not match agent_pbx.__version__ {configured_version}"
        )
    validate_release_documents(root, tag=tag, version=version)
    commit = validate_git_identity(root, tag=tag, branch=branch) if check_git else "not-checked"
    if index_json_url:
        ensure_version_is_new(index_json_url=index_json_url, version=version)
    return {
        "channel": channel,
        "commit": commit,
        "project": PROJECT_NAME,
        "tag": tag,
        "version": version,
    }


def _safe_archive_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ReleaseValidationError(f"archive member escapes its package root: {name}")
    lowered = tuple(part.lower() for part in path.parts)
    if FORBIDDEN_PARTS.intersection(lowered):
        raise ReleaseValidationError(f"archive contains local/generated path: {name}")
    basename = path.name.lower()
    if basename in FORBIDDEN_NAMES or PurePosixPath(basename).suffix in FORBIDDEN_SUFFIXES:
        raise ReleaseValidationError(f"archive contains private/runtime file: {name}")
    return path


def _metadata_fields(payload: bytes, *, archive: Path) -> tuple[str, str]:
    message = BytesParser(policy=email_policy).parsebytes(payload)
    name = message.get("Name")
    version = message.get("Version")
    if not name or not version:
        raise ReleaseValidationError(f"{archive.name} package metadata is missing Name or Version")
    return name, version


def _inspect_wheel(path: Path) -> tuple[list[str], tuple[str, str]]:
    names: list[str] = []
    metadata_payload: bytes | None = None
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            _safe_archive_name(info.filename)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise ReleaseValidationError(f"wheel contains a symbolic link: {info.filename}")
            names.append(info.filename)
            if info.filename.endswith(".dist-info/METADATA"):
                metadata_payload = archive.read(info)
    if metadata_payload is None:
        raise ReleaseValidationError(f"wheel has no dist-info/METADATA: {path.name}")
    return names, _metadata_fields(metadata_payload, archive=path)


def _inspect_sdist(path: Path) -> tuple[list[str], tuple[str, str]]:
    names: list[str] = []
    metadata_payload: bytes | None = None
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            _safe_archive_name(member.name)
            if not (member.isfile() or member.isdir()):
                raise ReleaseValidationError(
                    f"source distribution contains a link or special file: {member.name}"
                )
            names.append(member.name)
            member_path = PurePosixPath(member.name)
            if (
                member.isfile()
                and member_path.name == "PKG-INFO"
                and len(member_path.parts) == 2
            ):
                extracted = archive.extractfile(member)
                if extracted is not None:
                    metadata_payload = extracted.read()
    if metadata_payload is None:
        raise ReleaseValidationError(f"source distribution has no root PKG-INFO: {path.name}")
    return names, _metadata_fields(metadata_payload, archive=path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_archives(dist: Path, *, version: str) -> dict[str, Any]:
    dist = dist.resolve()
    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseValidationError(
            f"expected exactly one wheel and one source distribution, found {len(wheels)} and {len(sdists)}"
        )
    expected_wheel = f"{NORMALIZED_PROJECT_NAME}-{version}-py3-none-any.whl"
    expected_sdist = f"{NORMALIZED_PROJECT_NAME}-{version}.tar.gz"
    if wheels[0].name != expected_wheel:
        raise ReleaseValidationError(f"unexpected wheel filename: {wheels[0].name}; expected {expected_wheel}")
    if sdists[0].name != expected_sdist:
        raise ReleaseValidationError(f"unexpected sdist filename: {sdists[0].name}; expected {expected_sdist}")
    wheel_names, wheel_metadata = _inspect_wheel(wheels[0])
    sdist_names, sdist_metadata = _inspect_sdist(sdists[0])
    for archive, metadata in ((wheels[0], wheel_metadata), (sdists[0], sdist_metadata)):
        name, archive_version = metadata
        if name != PROJECT_NAME or archive_version != version:
            raise ReleaseValidationError(
                f"{archive.name} metadata is {name} {archive_version}; expected {PROJECT_NAME} {version}"
            )
    return {
        "project": PROJECT_NAME,
        "version": version,
        "artifacts": [
            {
                "entries": len(wheel_names),
                "file": wheels[0].name,
                "sha256": sha256(wheels[0]),
                "size": wheels[0].stat().st_size,
            },
            {
                "entries": len(sdist_names),
                "file": sdists[0].name,
                "sha256": sha256(sdists[0]),
                "size": sdists[0].stat().st_size,
            },
        ],
    }


def _append_github_output(result: dict[str, Any]) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for key in ("channel", "commit", "project", "tag", "version"):
            if key in result:
                handle.write(f"{key}={result[key]}\n")


def _append_github_summary(result: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("## Agent PBX release validation\n\n")
        handle.write("```json\n")
        handle.write(json.dumps(result, indent=2, sort_keys=True))
        handle.write("\n```\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate Agent PBX release identity and artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata = subparsers.add_parser("metadata", help="validate tag, Git, version, and release documents")
    metadata.add_argument("--root", type=Path, default=Path.cwd())
    metadata.add_argument("--tag", required=True)
    metadata.add_argument("--channel", choices=("production", "testpypi"), required=True)
    metadata.add_argument("--branch", default="origin/dev")
    metadata.add_argument("--index-json-url")
    metadata.add_argument("--skip-git-check", action="store_true")

    archives = subparsers.add_parser("archives", help="validate built wheel and source distribution")
    archives.add_argument("--dist", type=Path, required=True)
    archives.add_argument("--version", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "metadata":
            result = validate_metadata(
                args.root.resolve(),
                tag=args.tag,
                channel=args.channel,
                branch=args.branch,
                index_json_url=args.index_json_url,
                check_git=not args.skip_git_check,
            )
        else:
            result = validate_archives(args.dist, version=args.version)
    except (OSError, ReleaseValidationError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _append_github_output(result)
    _append_github_summary(result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
