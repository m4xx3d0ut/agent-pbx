from __future__ import annotations

from pathlib import Path
import re


PROJECT_SPAWN_MODES = {"empty", "clone_source"}
PROJECT_SPAWN_TERMINAL_STATUSES = {"launched", "failed", "canceled", "cancelled"}
PROJECT_SPAWN_SLUG_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


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
