from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py310 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


LINKED_EDIT_PERMISSION_PROFILE = "agent-pbx-linked-edit"
EDIT_FORK_PURPOSE = "edit"
EDIT_FORK_ACCESS_MODE = "edit"


class LinkedWorktreeQualificationError(ValueError):
    """The linked-worktree topology is not safe for a managed write grant."""


@dataclass(frozen=True)
class LinkedWorktreeGitAdminPlan:
    worktree_root: Path
    git_file: Path
    git_dir: Path
    common_dir: Path
    object_dir: Path
    branch_ref: str
    ref_namespace: Path
    reflog_namespace: Path
    writable_paths: tuple[Path, ...]
    base_permission_profile: str = ":workspace"
    permission_profile: str = LINKED_EDIT_PERMISSION_PROFILE

    def public_dict(self) -> dict[str, Any]:
        return {
            "kind": "linked_worktree_edit",
            "worktree_root": str(self.worktree_root),
            "git_dir": str(self.git_dir),
            "common_dir": str(self.common_dir),
            "branch_ref": self.branch_ref,
            "base_permission_profile": self.base_permission_profile,
            "permission_profile": self.permission_profile,
            "writable_paths": [str(path) for path in self.writable_paths],
        }

    def config_overrides(self) -> tuple[tuple[str, object], ...]:
        filesystem = {str(path): "write" for path in self.writable_paths}
        return (
            (
                f"permissions.{self.permission_profile}",
                {"extends": self.base_permission_profile, "filesystem": filesystem},
            ),
            ("default_permissions", self.permission_profile),
        )


def qualify_linked_worktree_git_admin(
    work_root: str | Path,
    *,
    approved_roots: Iterable[Path],
    fork_purpose: str,
    access_mode: str,
    codex_home: Path | None = None,
    expected_uid: int | None = None,
) -> LinkedWorktreeGitAdminPlan | None:
    """Return a minimal linked-worktree write plan for a trusted edit fork.

    Standalone repositories and non-Git projects retain their existing launch
    behavior and return ``None``. Review/read-only roles are deliberately
    ignored before any repository inspection.
    """

    if fork_purpose != EDIT_FORK_PURPOSE or access_mode != EDIT_FORK_ACCESS_MODE:
        return None

    root_input = Path(work_root).expanduser()
    try:
        root = root_input.resolve(strict=True)
    except OSError as exc:
        raise LinkedWorktreeQualificationError(
            f"edit-fork work root is unavailable: {root_input}: {exc}"
        ) from exc
    if not root.is_dir():
        raise LinkedWorktreeQualificationError(
            f"edit-fork work root is not a directory: {root}"
        )
    if root_input.absolute() != root:
        raise LinkedWorktreeQualificationError(
            "linked edit-fork work roots must be canonical paths, not symlinks"
        )

    roots = _canonical_approved_roots(approved_roots)
    approved_root = _containing_root(root, roots)
    if approved_root is None:
        raise LinkedWorktreeQualificationError(
            f"linked edit-fork work root is outside approved project roots: {root}"
        )

    git_marker = root / ".git"
    if git_marker.is_symlink():
        raise LinkedWorktreeQualificationError(".git symlinks are not supported")
    if git_marker.is_dir():
        return None
    if not git_marker.exists():
        bare = _git_optional(root, "rev-parse", "--is-bare-repository")
        if bare == "true":
            raise LinkedWorktreeQualificationError("bare repositories are not supported")
        top = _git_optional(root, "rev-parse", "--show-toplevel")
        if top:
            raise LinkedWorktreeQualificationError(
                "edit-fork work root must be the canonical Git toplevel; nested repository paths are unsupported"
            )
        return None
    if not git_marker.is_file():
        raise LinkedWorktreeQualificationError(".git must be a directory or regular indirection file")

    raw_git_dir, git_dir = _parse_gitdir_file(git_marker)
    _require_within(raw_git_dir, approved_root, label="raw per-worktree gitdir")
    _require_no_symlink_components(raw_git_dir, approved_root)
    _require_within(git_dir, approved_root, label="per-worktree gitdir")
    _require_no_symlink_components(git_dir, approved_root)

    gitdir_backpointer_file = git_dir / "gitdir"
    raw_gitdir_backpointer, gitdir_backpointer = _parse_single_path_file(
        gitdir_backpointer_file,
        label="per-worktree gitdir backpointer",
    )
    _require_within(
        raw_gitdir_backpointer,
        approved_root,
        label="raw per-worktree gitdir backpointer",
    )
    _require_no_symlink_components(raw_gitdir_backpointer, approved_root)
    if gitdir_backpointer != git_marker:
        raise LinkedWorktreeQualificationError(
            "per-worktree gitdir backpointer does not resolve to this worktree's .git marker"
        )

    top = Path(_git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top != root:
        raise LinkedWorktreeQualificationError(
            f"Git toplevel mismatch: expected {root}, resolved {top}"
        )
    reported_git_dir = Path(
        _git(root, "rev-parse", "--absolute-git-dir")
    ).resolve(strict=True)
    if reported_git_dir != git_dir:
        raise LinkedWorktreeQualificationError(
            ".git indirection does not match Git's absolute gitdir"
        )
    if _git(root, "rev-parse", "--is-bare-repository") != "false":
        raise LinkedWorktreeQualificationError("bare repositories are not supported")
    if _git(root, "rev-parse", "--is-inside-work-tree") != "true":
        raise LinkedWorktreeQualificationError("repository is not an attached worktree")

    commondir_file = git_dir / "commondir"
    raw_common_dir, common_dir = _parse_single_path_file(
        commondir_file,
        label="commondir",
    )
    _require_within(raw_common_dir, approved_root, label="raw common gitdir")
    _require_no_symlink_components(raw_common_dir, approved_root)
    reported_common = Path(
        _git(root, "rev-parse", "--git-common-dir")
    )
    if not reported_common.is_absolute():
        reported_common = root / reported_common
    reported_common = reported_common.resolve(strict=True)
    if common_dir != reported_common:
        raise LinkedWorktreeQualificationError(
            "per-worktree commondir does not match Git's common directory"
        )
    _require_within(common_dir, approved_root, label="common gitdir")
    _require_no_symlink_components(common_dir, approved_root)
    if common_dir.name != ".git" or common_dir.parent / ".git" != common_dir:
        raise LinkedWorktreeQualificationError(
            "ambiguous common Git directory; expected a non-bare repository .git directory"
        )

    worktrees_dir = common_dir / "worktrees"
    if git_dir.parent != worktrees_dir or git_dir.parent.parent != common_dir:
        raise LinkedWorktreeQualificationError(
            "per-worktree gitdir must be one direct child of the common worktrees directory"
        )

    branch_ref = _git_optional(root, "symbolic-ref", "-q", "HEAD")
    if not branch_ref:
        raise LinkedWorktreeQualificationError(
            "linked edit forks require an attached local branch"
        )
    prefix = "refs/heads/"
    if not branch_ref.startswith(prefix):
        raise LinkedWorktreeQualificationError(
            "linked edit forks require an attached local branch"
        )
    branch_parts = tuple(part for part in branch_ref[len(prefix) :].split("/") if part)
    if len(branch_parts) < 2 or any(part in {".", ".."} for part in branch_parts):
        raise LinkedWorktreeQualificationError(
            "linked edit-fork branches must use a dedicated namespace, for example pbx/<fork-id>"
        )

    literal_object_dir = common_dir / "objects"
    literal_branch_path = common_dir.joinpath(*branch_ref.split("/"))
    literal_branch_log = common_dir.joinpath("logs", *branch_ref.split("/"))
    for path in (literal_object_dir, literal_branch_path, literal_branch_log):
        _require_no_symlink_components(path, approved_root)
    object_dir = _git_path(root, "objects")
    branch_path = _git_path(root, branch_ref)
    branch_log = _git_path(root, f"logs/{branch_ref}")
    expected_object_dir = literal_object_dir.resolve(strict=True)
    if object_dir != expected_object_dir:
        raise LinkedWorktreeQualificationError("Git object database does not match the common repository")
    expected_branch_path = literal_branch_path
    expected_branch_log = literal_branch_log
    if branch_path != expected_branch_path.resolve(strict=True):
        raise LinkedWorktreeQualificationError("active branch ref escaped the common repository")
    if branch_log != expected_branch_log.resolve(strict=True):
        raise LinkedWorktreeQualificationError("active branch reflog escaped the common repository")

    ref_namespace = branch_path.parent
    reflog_namespace = branch_log.parent
    _require_dedicated_namespace(ref_namespace, branch_path.name, label="branch ref")
    _require_dedicated_namespace(reflog_namespace, branch_log.name, label="branch reflog")

    writable_paths = (git_dir, object_dir, ref_namespace, reflog_namespace)
    if common_dir in writable_paths or worktrees_dir in writable_paths:
        raise LinkedWorktreeQualificationError("writable Git paths broadened to a common administration root")
    for path in writable_paths:
        _require_within(path, approved_root, label="writable Git administration path")
        _require_no_symlink_components(path, approved_root)

    head_file = git_dir / "HEAD"
    index_file = git_dir / "index"
    critical_files = [
        git_marker,
        gitdir_backpointer_file,
        commondir_file,
        head_file,
        branch_path,
        branch_log,
    ]
    if index_file.exists():
        critical_files.append(index_file)
    for path in critical_files:
        _require_regular_file(path, label="critical linked-worktree Git file")
        _require_no_symlink_components(path, approved_root)

    uid = os.geteuid() if expected_uid is None else expected_uid
    for path in (root, git_dir, common_dir, *writable_paths, *critical_files):
        try:
            owner = path.stat(follow_symlinks=False).st_uid
        except OSError as exc:
            raise LinkedWorktreeQualificationError(f"unable to verify owner for {path}: {exc}") from exc
        if owner != uid:
            raise LinkedWorktreeQualificationError(
                f"owner mismatch for linked-worktree path {path}: uid {owner}, expected {uid}"
            )

    base_permission_profile = _validate_permission_profile_compatibility(
        codex_home=codex_home,
        profile_name=LINKED_EDIT_PERMISSION_PROFILE,
        common_dir=common_dir,
    )
    return LinkedWorktreeGitAdminPlan(
        worktree_root=root,
        git_file=git_marker,
        git_dir=git_dir,
        common_dir=common_dir,
        object_dir=object_dir,
        branch_ref=branch_ref,
        ref_namespace=ref_namespace,
        reflog_namespace=reflog_namespace,
        writable_paths=writable_paths,
        base_permission_profile=base_permission_profile,
    )


def _canonical_approved_roots(roots: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    for candidate in roots:
        try:
            resolved = Path(candidate).expanduser().resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and resolved not in result:
            result.append(resolved)
    if not result:
        raise LinkedWorktreeQualificationError("no approved project roots are available")
    return tuple(result)


def _containing_root(path: Path, roots: Iterable[Path]) -> Path | None:
    matches = [root for root in roots if path == root or path.is_relative_to(root)]
    return max(matches, key=lambda item: len(item.parts), default=None)


def _parse_gitdir_file(path: Path) -> tuple[Path, Path]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LinkedWorktreeQualificationError(f"unable to read .git indirection: {exc}") from exc
    if len(lines) != 1 or not lines[0].startswith("gitdir: "):
        raise LinkedWorktreeQualificationError("malformed .git indirection file")
    raw = lines[0][len("gitdir: ") :].strip()
    if not raw:
        raise LinkedWorktreeQualificationError("empty .git indirection target")
    target = Path(raw)
    if not target.is_absolute():
        target = path.parent / target
    raw_target = Path(os.path.abspath(target))
    try:
        return raw_target, raw_target.resolve(strict=True)
    except OSError as exc:
        raise LinkedWorktreeQualificationError(f"invalid .git indirection target: {exc}") from exc


def _parse_single_path_file(path: Path, *, label: str) -> tuple[Path, Path]:
    if path.is_symlink() or not path.is_file():
        raise LinkedWorktreeQualificationError(f"{label} must be a regular path file")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LinkedWorktreeQualificationError(f"unable to read {label}: {exc}") from exc
    if len(lines) != 1 or not lines[0].strip():
        raise LinkedWorktreeQualificationError(f"malformed {label} file")
    target = Path(lines[0].strip())
    if not target.is_absolute():
        target = path.parent / target
    raw_target = Path(os.path.abspath(target))
    try:
        return raw_target, raw_target.resolve(strict=True)
    except OSError as exc:
        raise LinkedWorktreeQualificationError(f"invalid {label} target: {exc}") from exc


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise LinkedWorktreeQualificationError(
            f"unable to qualify linked worktree ({' '.join(args)}): {detail}"
        )
    return result.stdout.strip()


def _git_optional(root: Path, *args: str) -> str | None:
    try:
        return _git(root, *args)
    except LinkedWorktreeQualificationError:
        return None


def _git_path(root: Path, relative: str) -> Path:
    raw = Path(_git(root, "rev-parse", "--git-path", relative))
    if not raw.is_absolute():
        raw = root / raw
    try:
        return raw.resolve(strict=True)
    except OSError as exc:
        raise LinkedWorktreeQualificationError(
            f"required Git administration path is unavailable ({relative}): {exc}"
        ) from exc


def _require_within(path: Path, root: Path, *, label: str) -> None:
    if path != root and not path.is_relative_to(root):
        raise LinkedWorktreeQualificationError(f"{label} escaped approved root {root}: {path}")


def _require_no_symlink_components(path: Path, root: Path) -> None:
    _require_within(path, root, label="validated path")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise LinkedWorktreeQualificationError(
                f"symlinked Git administration path is not supported: {current}"
            )


def _require_dedicated_namespace(path: Path, active_name: str, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise LinkedWorktreeQualificationError(f"{label} namespace is unavailable: {path}")
    try:
        entries = {item.name for item in path.iterdir()}
    except OSError as exc:
        raise LinkedWorktreeQualificationError(f"unable to inspect {label} namespace: {exc}") from exc
    if entries != {active_name}:
        raise LinkedWorktreeQualificationError(
            f"{label} namespace is not dedicated to this fork: {path}; found {sorted(entries)}"
        )


def _require_regular_file(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise LinkedWorktreeQualificationError(
            f"{label} must be a regular file: {path}"
        )


def _validate_permission_profile_compatibility(
    *,
    codex_home: Path | None,
    profile_name: str,
    common_dir: Path,
) -> str:
    paths: list[tuple[str, Path]] = []
    if codex_home is not None:
        paths.append(("Codex user config", codex_home / "config.toml"))
    paths.extend(
        (
            ("Codex managed config", Path("/etc/codex/managed_config.toml")),
            ("Codex requirements", Path("/etc/codex/requirements.toml")),
        )
    )
    base_profile = ":workspace"
    profile_definitions: dict[str, Mapping[str, Any]] = {}
    for label, path in paths:
        if not path.exists():
            continue
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise LinkedWorktreeQualificationError(f"unable to validate {label}: {exc}") from exc
        if "sandbox_mode" in parsed:
            raise LinkedWorktreeQualificationError(
                f"{label} defines legacy sandbox_mode, which overrides managed permission profiles; migrate it before linked edit-fork launch"
            )
        selected_profile = str(parsed.get("profile") or "").strip()
        profiles = parsed.get("profiles")
        if selected_profile and isinstance(profiles, Mapping):
            selected = profiles.get(selected_profile)
            if isinstance(selected, Mapping) and "sandbox_mode" in selected:
                raise LinkedWorktreeQualificationError(
                    f"{label} profile {selected_profile!r} defines legacy sandbox_mode; migrate it before linked edit-fork launch"
                )
        definitions = parsed.get("permissions")
        if isinstance(definitions, Mapping):
            for name, definition in definitions.items():
                if isinstance(definition, Mapping):
                    profile_definitions[str(name)] = definition
        configured_default = str(parsed.get("default_permissions") or "").strip()
        if configured_default:
            base_profile = configured_default
        if label == "Codex managed config":
            enforced = str(parsed.get("default_permissions") or "").strip()
            if enforced and enforced != profile_name:
                raise LinkedWorktreeQualificationError(
                    f"Codex managed config enforces permission profile {enforced!r}; cannot select {profile_name!r}"
                )
        if label == "Codex requirements":
            allowed = parsed.get("allowed_permission_profiles")
            if isinstance(allowed, list) and profile_name not in {
                str(item) for item in allowed
            }:
                raise LinkedWorktreeQualificationError(
                    f"Codex requirements do not allow permission profile {profile_name!r}"
                )
    _validate_base_permission_profile(
        base_profile,
        definitions=profile_definitions,
        common_dir=common_dir,
    )
    return base_profile


def _validate_base_permission_profile(
    profile_name: str,
    *,
    definitions: Mapping[str, Mapping[str, Any]],
    common_dir: Path,
) -> None:
    current = profile_name
    visited: set[str] = set()
    while not current.startswith(":"):
        if current in visited:
            raise LinkedWorktreeQualificationError(
                f"permission profile inheritance cycle includes {current!r}"
            )
        visited.add(current)
        definition = definitions.get(current)
        if not isinstance(definition, Mapping):
            raise LinkedWorktreeQualificationError(
                f"permission profile {current!r} is not defined in validated Codex configuration"
            )
        filesystem = definition.get("filesystem")
        if isinstance(filesystem, Mapping):
            for raw_path, raw_mode in filesystem.items():
                if str(raw_mode).strip().casefold() != "write":
                    continue
                path_text = str(raw_path).strip()
                if not path_text.startswith("/"):
                    continue
                candidate = Path(path_text).expanduser().resolve(strict=False)
                if candidate == common_dir or candidate.is_relative_to(common_dir):
                    raise LinkedWorktreeQualificationError(
                        f"base permission profile {current!r} already grants Git administration write access at {candidate}"
                    )
        current = str(definition.get("extends") or "").strip()
        if not current:
            raise LinkedWorktreeQualificationError(
                f"permission profile {profile_name!r} does not inherit from :workspace"
            )
    if current != ":workspace":
        raise LinkedWorktreeQualificationError(
            f"permission profile {profile_name!r} must ultimately extend :workspace, not {current!r}"
        )
