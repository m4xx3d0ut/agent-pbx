#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
import venv

from validate_release import ReleaseValidationError, sha256


def release_metadata(
    *,
    index_json_base: str,
    project: str,
    version: str,
    attempts: int = 12,
    wait_seconds: float = 10.0,
) -> dict[str, Any]:
    url = f"{index_json_base.rstrip('/')}/{quote(project)}/{quote(version)}/json"
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "agent-pbx-release-verifier"})
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ReleaseValidationError(f"package index returned an invalid document: {url}")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(wait_seconds)
    raise ReleaseValidationError(f"release did not become available at {url}: {last_error}")


def verify_distribution_hashes(metadata: dict[str, Any], dist: Path) -> list[dict[str, str]]:
    urls = metadata.get("urls")
    if not isinstance(urls, list):
        raise ReleaseValidationError("package index response has no distribution URL list")
    remote: dict[str, str] = {}
    for item in urls:
        if not isinstance(item, dict):
            continue
        filename = item.get("filename")
        digests = item.get("digests")
        digest = digests.get("sha256") if isinstance(digests, dict) else None
        if isinstance(filename, str) and isinstance(digest, str):
            remote[filename] = digest
    local = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if len(local) != 2:
        raise ReleaseValidationError(f"expected two local distributions, found {len(local)}")
    verified: list[dict[str, str]] = []
    for path in local:
        local_digest = sha256(path)
        remote_digest = remote.get(path.name)
        if remote_digest != local_digest:
            raise ReleaseValidationError(
                f"published SHA-256 mismatch for {path.name}: local {local_digest}, remote {remote_digest}"
            )
        verified.append({"file": path.name, "sha256": local_digest})
    unexpected = sorted(set(remote).difference(path.name for path in local))
    if unexpected:
        raise ReleaseValidationError(
            f"package index contains unexpected files for this release: {', '.join(unexpected)}"
        )
    return verified


def _venv_executable(root: Path, name: str) -> Path:
    directory = "Scripts" if sys.platform == "win32" else "bin"
    suffix = ".exe" if sys.platform == "win32" else ""
    return root / directory / f"{name}{suffix}"


def verify_index_install(
    *,
    project: str,
    version: str,
    simple_index_url: str,
    extra_index_url: str | None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="agent-pbx-index-verify-") as temporary:
        environment = Path(temporary) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        pip = _venv_executable(environment, "pip")
        python = _venv_executable(environment, "python")
        agent_pbx = _venv_executable(environment, "agent-pbx")
        command = [
            str(pip),
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--index-url",
            simple_index_url,
        ]
        if extra_index_url:
            command.extend(["--extra-index-url", extra_index_url])
        command.append(f"{project}=={version}")
        subprocess.run(command, check=True)
        subprocess.run(
            [
                str(python),
                "-c",
                "import agent_pbx,sys; sys.exit(0 if agent_pbx.__version__ == %r else 1)" % version,
            ],
            check=True,
        )
        result = subprocess.run(
            [str(agent_pbx), "--version"], check=True, capture_output=True, text=True
        )
        if version not in result.stdout:
            raise ReleaseValidationError(
                f"installed agent-pbx --version returned {result.stdout.strip()!r}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an Agent PBX package-index release")
    parser.add_argument("--project", default="agent-pbx")
    parser.add_argument("--version", required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--index-json-base", default="https://pypi.org/pypi")
    parser.add_argument("--simple-index-url", default="https://pypi.org/simple")
    parser.add_argument("--extra-index-url")
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--wait-seconds", type=float, default=10.0)
    parser.add_argument("--skip-install", action="store_true")
    args = parser.parse_args(argv)
    try:
        metadata = release_metadata(
            index_json_base=args.index_json_base,
            project=args.project,
            version=args.version,
            attempts=args.attempts,
            wait_seconds=args.wait_seconds,
        )
        verified = verify_distribution_hashes(metadata, args.dist)
        if not args.skip_install:
            verify_index_install(
                project=args.project,
                version=args.version,
                simple_index_url=args.simple_index_url,
                extra_index_url=args.extra_index_url,
            )
    except (OSError, ReleaseValidationError, subprocess.SubprocessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"project": args.project, "version": args.version, "verified": verified}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
