#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys


MANIFEST_VERSION = "agent-pbx.release-manifest/v2"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate(wheelhouse: Path) -> dict[str, object]:
    wheelhouse = wheelhouse.resolve()
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise ValueError("wheelhouse contains no wheels")
    packages = []
    checksum_lines = []
    for wheel in wheels:
        digest = sha256(wheel)
        checksum_lines.append(f"{digest}  {wheel.name}")
        parts = wheel.name.removesuffix(".whl").split("-")
        packages.append(
            {
                "file": wheel.name,
                "name": parts[0].replace("_", "-"),
                "version": parts[1] if len(parts) > 1 else "unknown",
                "sha256": digest,
                "size": wheel.stat().st_size,
            }
        )
    generated_at = datetime.now(timezone.utc).isoformat()
    manifest = {
        "api_version": MANIFEST_VERSION,
        "generated_at": generated_at,
        "builder": {
            "python": platform.python_version(),
            "system": platform.system(),
            "architecture": platform.machine(),
        },
        "packages": packages,
    }
    (wheelhouse / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n", encoding="utf-8"
    )
    (wheelhouse / "DEPENDENCIES.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sbom_packages = [
        {
            "SPDXID": f"SPDXRef-Package-{index}",
            "name": item["name"],
            "versionInfo": item["version"],
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "checksums": [
                {"algorithm": "SHA256", "checksumValue": item["sha256"]}
            ],
        }
        for index, item in enumerate(packages, start=1)
    ]
    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "agent-pbx-wheelhouse",
        "documentNamespace": (
            "https://github.com/m4xx3d0ut/agent-pbx/releases/"
            f"wheelhouse-{hashlib.sha256(generated_at.encode()).hexdigest()[:16]}"
        ),
        "creationInfo": {
            "created": generated_at,
            "creators": ["Tool: agent-pbx-release-manifest-v2"],
        },
        "packages": sbom_packages,
    }
    (wheelhouse / "SBOM.spdx.json").write_text(
        json.dumps(sbom, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def verify(wheelhouse: Path) -> list[str]:
    wheelhouse = wheelhouse.resolve()
    checksum_file = wheelhouse / "SHA256SUMS"
    if not checksum_file.is_file():
        raise ValueError("SHA256SUMS is missing")
    verified: list[str] = []
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        expected, separator, name = line.partition("  ")
        if not separator or not expected or not name:
            raise ValueError("SHA256SUMS contains an invalid line")
        path = (wheelhouse / name).resolve()
        if not path.is_relative_to(wheelhouse):
            raise ValueError(f"checksum path escapes wheelhouse: {name}")
        if not path.is_file() or sha256(path) != expected:
            raise ValueError(f"checksum mismatch: {name}")
        verified.append(name)
    return verified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("generate", "verify"))
    parser.add_argument("--wheelhouse", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "generate":
            result: object = generate(args.wheelhouse)
        else:
            result = {"verified": verify(args.wheelhouse)}
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
