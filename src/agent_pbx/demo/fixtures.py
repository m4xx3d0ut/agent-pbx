from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .policy import ensure_private_directory


@dataclass(frozen=True)
class DemoFixturePaths:
    root: Path
    bin_dir: Path
    project_root: Path
    platform_project: Path
    release_project: Path
    codex_bin: Path
    workerbee_bin: Path
    github_bin: Path


def _write_executable(path: Path, module: str) -> None:
    path.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                f"from {module} import main",
                "raise SystemExit(main())",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o700)


def _install_executable_alias(path: Path, *, entrypoint: str, module: str) -> None:
    """Install a fixture command that also works below a noexec session root.

    The public container deliberately mounts its disposable ``/tmp`` tree with
    ``noexec``.  A symlink below that tree may still invoke an immutable entry
    point installed in the image, while a generated script cannot execute.
    Source checkouts without installed demo entry points retain the generated
    wrapper used by local development and tests.
    """

    installed = shutil.which(entrypoint)
    if installed:
        path.symlink_to(Path(installed).resolve())
        return
    _write_executable(path, module)


def _initialize_repository(path: Path, files: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for relative, body in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("the public demo requires git")
    subprocess.run(
        [git, "-C", str(path), "init", "-q", "-b", "dev"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [git, "-C", str(path), "add", "."],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            git,
            "-C",
            str(path),
            "-c",
            "user.name=Agent PBX Demo",
            "-c",
            "user.email=demo@agent-pbx.invalid",
            "commit",
            "-q",
            "-m",
            "Create isolated Agent PBX demo project",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_demo_fixtures(root: Path) -> DemoFixturePaths:
    """Create deterministic integrations and Git projects below one private root."""

    resolved = ensure_private_directory(root)
    bin_dir = ensure_private_directory(resolved / "bin")
    project_root = ensure_private_directory(resolved / "projects")
    platform_project = project_root / "platform-console"
    release_project = project_root / "release-evidence"

    _initialize_repository(
        platform_project,
        {
            "README.md": (
                "# Platform Console\n\n"
                "A deterministic full-stack project for the Agent PBX browser demo.\n"
            ),
            "AGENTS.md": "# Demo agent instructions\n\nUse Agent PBX report mode.\n",
            "src/runtime.py": (
                "def runtime_status() -> str:\n"
                "    return \"ready\"\n"
            ),
            "ops/runtime.yaml": (
                "profile: public-demo\n"
                "runtime: healthy\n"
                "replicas: 2\n"
            ),
            "tests/test_runtime.py": (
                "from src.runtime import runtime_status\n\n"
                "def test_runtime() -> None:\n"
                "    assert runtime_status() == \"ready\"\n"
            ),
        },
    )
    _initialize_repository(
        release_project,
        {
            "README.md": "# Release Evidence\n\nReview-ready public documentation.\n",
            "docs/acceptance.md": (
                "# Acceptance\n\n"
                "- Native terminal attached\n"
                "- Runtime identity verified\n"
                "- Integrations isolated\n"
            ),
            "CHANGELOG.md": "# Changelog\n\n## Next\n\n- Add interactive demo.\n",
        },
    )

    codex_bin = bin_dir / "codex"
    workerbee_bin = bin_dir / "workerbee"
    github_bin = bin_dir / "gh"
    _install_executable_alias(
        codex_bin,
        entrypoint="agent-pbx-demo-codex",
        module="agent_pbx.demo.fake_codex",
    )
    _install_executable_alias(
        workerbee_bin,
        entrypoint="agent-pbx-demo-workerbee",
        module="agent_pbx.demo.fake_workerbee",
    )
    _install_executable_alias(
        github_bin,
        entrypoint="agent-pbx-demo-gh",
        module="agent_pbx.demo.fake_github",
    )
    return DemoFixturePaths(
        root=resolved,
        bin_dir=bin_dir,
        project_root=project_root,
        platform_project=platform_project,
        release_project=release_project,
        codex_bin=codex_bin,
        workerbee_bin=workerbee_bin,
        github_bin=github_bin,
    )
