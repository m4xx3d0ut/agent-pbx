from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


def release_manifest_module():  # type: ignore[no-untyped-def]
    path = Path(__file__).parents[1] / "scripts" / "release_manifest.py"
    spec = importlib.util.spec_from_file_location("release_manifest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_manifest_generates_checksums_dependency_manifest_and_sbom(
    tmp_path: Path,
) -> None:
    module = release_manifest_module()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheelhouse.joinpath("agent_pbx-2.0.0-py3-none-any.whl").write_bytes(b"agent-pbx")
    wheelhouse.joinpath("textual-0.89.1-py3-none-any.whl").write_bytes(b"textual")

    manifest = module.generate(wheelhouse)
    verified = module.verify(wheelhouse)
    dependencies = json.loads(
        wheelhouse.joinpath("DEPENDENCIES.json").read_text(encoding="utf-8")
    )
    sbom = json.loads(wheelhouse.joinpath("SBOM.spdx.json").read_text(encoding="utf-8"))

    assert manifest["api_version"] == "agent-pbx.release-manifest/v2"
    assert verified == [
        "agent_pbx-2.0.0-py3-none-any.whl",
        "textual-0.89.1-py3-none-any.whl",
    ]
    assert dependencies["builder"]["python"]
    assert sbom["spdxVersion"] == "SPDX-2.3"
    assert len(sbom["packages"]) == 2


def test_release_manifest_rejects_tampered_wheel(tmp_path: Path) -> None:
    module = release_manifest_module()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheel = wheelhouse / "agent_pbx-2.0.0-py3-none-any.whl"
    wheel.write_bytes(b"before")
    module.generate(wheelhouse)
    wheel.write_bytes(b"after")

    with pytest.raises(ValueError, match="checksum mismatch"):
        module.verify(wheelhouse)


def test_release_manifest_rejects_path_outside_wheelhouse(tmp_path: Path) -> None:
    module = release_manifest_module()
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheelhouse.joinpath("SHA256SUMS").write_text(
        f"{'0' * 64}  ../outside.whl\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes wheelhouse"):
        module.verify(wheelhouse)


def test_release_shell_scripts_are_syntactically_valid() -> None:
    root = Path(__file__).parents[1]
    for script in ("scripts/build_wheelhouse.sh", "scripts/install-agent-pbx.sh"):
        result = subprocess.run(
            ["sh", "-n", str(root / script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_python_310_declares_tomllib_fallback() -> None:
    root = Path(__file__).parents[1]
    project = root.joinpath("pyproject.toml").read_text(encoding="utf-8")

    assert '"tomli>=2,<3; python_version < \'3.11\'"' in project


def test_release_manifest_cli_verifies_wheelhouse(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    wheelhouse.joinpath("agent_pbx-2.0.0-py3-none-any.whl").write_bytes(b"fixture")
    command = [
        sys.executable,
        str(root / "scripts" / "release_manifest.py"),
        "generate",
        "--wheelhouse",
        str(wheelhouse),
    ]
    generated = subprocess.run(command, capture_output=True, text=True)
    verified = subprocess.run(
        [*command[:2], "verify", "--wheelhouse", str(wheelhouse)],
        capture_output=True,
        text=True,
    )

    assert generated.returncode == 0
    assert verified.returncode == 0
    assert "agent_pbx-2.0.0-py3-none-any.whl" in verified.stdout
