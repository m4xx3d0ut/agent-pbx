from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Any, Mapping


PROJECT_SPAWN_MODES = {"empty", "clone_source"}
PROJECT_SPAWN_TERMINAL_STATUSES = {"launched", "failed", "canceled", "cancelled"}
PROJECT_SPAWN_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class ProjectSpawnPreflight:
    source_path: Path
    target_parent: Path
    target_path: Path
    target_slug: str
    mode: str
    source_repo_root: Path | None

    def public_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "target_parent": str(self.target_parent),
            "target_path": str(self.target_path),
            "target_slug": self.target_slug,
            "mode": self.mode,
            "source_repo_root": (
                str(self.source_repo_root) if self.source_repo_root is not None else None
            ),
        }


def normalize_project_slug(value: str, *, default: str = "project") -> str:
    slug = PROJECT_SPAWN_SLUG_PATTERN.sub("-", str(value or "").strip())
    slug = slug.strip(".-")
    if not slug or slug in {".", ".."}:
        slug = default
    return slug[:160].strip(".-") or default


def resolve_sibling_project_paths(
    source_cwd: str,
    project_name: str,
    *,
    target_slug: str | None = None,
) -> tuple[Path, Path, Path, str]:
    source_path = Path(source_cwd).expanduser().resolve(strict=False)
    parent_path = source_path.parent.resolve(strict=False)
    slug = normalize_project_slug(target_slug or project_name)
    target_path = (parent_path / slug).resolve(strict=False)
    validate_sibling_project_target(source_path, target_path, expected_parent=parent_path)
    return source_path, parent_path, target_path, slug


def validate_sibling_project_target(
    source_path: Path,
    target_path: Path,
    *,
    expected_parent: Path | None = None,
) -> None:
    source = source_path.expanduser().resolve(strict=False)
    target = target_path.expanduser().resolve(strict=False)
    parent = (
        expected_parent.expanduser().resolve(strict=False)
        if expected_parent is not None
        else source.parent.resolve(strict=False)
    )
    source_parent = source.parent.resolve(strict=False)
    if parent != source_parent:
        raise ValueError("target project parent must match the source project parent")
    if target.parent != parent:
        raise ValueError("target project must be a sibling of the source project")
    if target == source:
        raise ValueError("target project cannot be the source project")
    if target.name in {"", ".", ".."} or "/" in target.name or "\\" in target.name:
        raise ValueError("target project name is invalid")
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("target project cannot be inside the source project")


def canonical_git_repo_root(path: Path) -> Path:
    try:
        source = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"clone source is unavailable: {path}") from exc
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"clone source Git inspection failed: {source}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError(f"clone source is not a Git repository: {source}")
    root = Path(result.stdout.strip()).expanduser().resolve(strict=True)
    if root != source:
        raise ValueError(
            "clone source must be the canonical Git repository root; "
            f"source={source}, repo_root={root}"
        )
    return root


def validate_project_spawn_preflight(
    source_cwd: str,
    project_name: str,
    *,
    mode: str = "empty",
    target_slug: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProjectSpawnPreflight:
    """Validate a project-spawn request without creating or changing files."""

    normalized_mode = str(mode or "empty").strip().lower()
    if normalized_mode not in PROJECT_SPAWN_MODES:
        raise ValueError("mode must be empty or clone_source")
    source_path, parent, target_path, slug = resolve_sibling_project_paths(
        source_cwd,
        project_name,
        target_slug=target_slug,
    )
    if not source_path.is_dir():
        raise ValueError(f"source project directory is unavailable: {source_path}")

    requested_repo_root = str((metadata or {}).get("repo_root") or "").strip()
    if requested_repo_root:
        try:
            repo_root = Path(requested_repo_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError(
                f"metadata.repo_root is unavailable: {requested_repo_root}"
            ) from exc
        if repo_root != source_path:
            raise ValueError(
                "metadata.repo_root does not match the review fork source_cwd; "
                f"repo_root={repo_root}, source_cwd={source_path}"
            )

    source_repo_root: Path | None = None
    if normalized_mode == "clone_source":
        source_repo_root = canonical_git_repo_root(source_path)

    if target_path.exists():
        if not target_path.is_dir():
            raise ValueError(f"target path exists and is not a directory: {target_path}")
        try:
            has_entries = next(target_path.iterdir(), None) is not None
        except OSError as exc:
            raise ValueError(f"target project directory cannot be inspected: {exc}") from exc
        if has_entries:
            raise ValueError(
                f"target project directory is not empty: {target_path}. "
                "Choose a different empty sibling target, or launch the existing "
                "canonical Git workspace through the managed Agent launcher."
            )

    return ProjectSpawnPreflight(
        source_path=source_path,
        target_parent=parent,
        target_path=target_path,
        target_slug=slug,
        mode=normalized_mode,
        source_repo_root=source_repo_root,
    )


def validate_stored_project_spawn_preflight(
    request: Mapping[str, Any],
) -> ProjectSpawnPreflight:
    """Revalidate a stored request immediately before approval/launch."""

    metadata = request.get("metadata")
    return validate_project_spawn_preflight(
        str(request.get("source_cwd") or ""),
        str(request.get("project_name") or request.get("target_slug") or "project"),
        mode=str(request.get("mode") or "empty"),
        target_slug=str(request.get("target_slug") or "") or None,
        metadata=metadata if isinstance(metadata, Mapping) else None,
    )
