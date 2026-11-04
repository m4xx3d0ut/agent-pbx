from __future__ import annotations

import copy
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
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


class _BrokerRequiredProfile(LinkedWorktreeQualificationError):
    """Codex cannot safely compose this split policy with direct Git grants."""


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
    permission_profile_definition: Mapping[str, Any] | None = None
    write_strategy: str = "direct_permission_profile"

    def public_dict(self) -> dict[str, Any]:
        direct = self.write_strategy == "direct_permission_profile"
        return {
            "kind": "linked_worktree_edit",
            "worktree_root": str(self.worktree_root),
            "git_dir": str(self.git_dir),
            "common_dir": str(self.common_dir),
            "object_dir": str(self.object_dir),
            "branch_ref": self.branch_ref,
            "base_permission_profile": self.base_permission_profile,
            "permission_profile": self.permission_profile,
            "write_strategy": self.write_strategy,
            "grant_scope": (
                "sandbox_filesystem_only" if direct else "tui_root_commit_broker"
            ),
            "additional_workspace_roots": [],
            "active_write_grants": (
                [str(path) for path in self.writable_paths] if direct else []
            ),
            "qualified_git_admin_paths": [str(path) for path in self.writable_paths],
            "writable_paths": (
                [str(path) for path in self.writable_paths] if direct else []
            ),
        }

    def config_overrides(self) -> tuple[tuple[str, object], ...]:
        profile = copy.deepcopy(self.permission_profile_definition)
        if profile is None:
            return ()
        return (
            (
                f"permissions.{self.permission_profile}",
                profile,
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

    # Resolve and inspect the administration topology without repository-aware
    # Git first. Repository commands are allowed only after local/worktree
    # configuration is proven not to include another file.
    commondir_file = git_dir / "commondir"
    raw_common_dir, common_dir = _parse_single_path_file(
        commondir_file,
        label="commondir",
    )
    _require_within(raw_common_dir, approved_root, label="raw common gitdir")
    _require_no_symlink_components(raw_common_dir, approved_root)
    _require_within(common_dir, approved_root, label="common gitdir")
    _require_no_symlink_components(common_dir, approved_root)
    if common_dir.name != ".git" or common_dir.parent / ".git" != common_dir:
        raise LinkedWorktreeQualificationError(
            "ambiguous common Git directory; expected a non-bare repository .git directory"
        )
    _reject_git_config_includes(common_dir / "config", label="local Git config")
    _reject_git_config_includes(
        git_dir / "config.worktree",
        label="worktree Git config",
        allow_absent=True,
    )
    alternates = common_dir / "objects" / "info" / "alternates"
    if os.path.lexists(alternates):
        raise LinkedWorktreeQualificationError(
            "Git object alternates are unsupported for linked edit-fork qualification"
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

    base_permission_profile, permission_profile_definition, write_strategy = (
        _validate_permission_profile_compatibility(
            codex_home=codex_home,
            profile_name=LINKED_EDIT_PERMISSION_PROFILE,
            common_dir=common_dir,
            writable_paths=writable_paths,
        )
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
        permission_profile_definition=permission_profile_definition,
        write_strategy=write_strategy,
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
    git_binary = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
    if not git_binary:
        raise LinkedWorktreeQualificationError("an absolute Git executable is required")
    result = subprocess.run(
        [str(Path(git_binary).resolve(strict=True)), "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
        env=_qualification_git_env(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise LinkedWorktreeQualificationError(
            f"unable to qualify linked worktree ({' '.join(args)}): {detail}"
        )
    return result.stdout.strip()


def _reject_git_config_includes(
    path: Path,
    *,
    label: str,
    allow_absent: bool = False,
) -> None:
    """Reject include/includeIf without following another config path."""

    if not path.exists():
        if allow_absent:
            return
        raise LinkedWorktreeQualificationError(f"{label} is unavailable: {path}")
    if path.is_symlink() or not path.is_file():
        raise LinkedWorktreeQualificationError(
            f"{label} must be a regular non-symlink file: {path}"
        )
    git_binary = shutil.which("git", path="/usr/local/bin:/usr/bin:/bin")
    if not git_binary:
        raise LinkedWorktreeQualificationError("an absolute Git executable is required")
    result = subprocess.run(
        [
            str(Path(git_binary).resolve(strict=True)),
            "config",
            "--file",
            str(path),
            "--no-includes",
            "--get-regexp",
            r"^include.*\.path$",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
        cwd=os.sep,
        env=_qualification_git_env(),
    )
    if result.returncode == 0:
        raise LinkedWorktreeQualificationError(
            f"{label} contains include/includeIf directives; external config reads are unsupported"
        )
    if result.returncode != 1:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise LinkedWorktreeQualificationError(
            f"unable to inspect {label} without includes: {detail}"
        )
    promisor = subprocess.run(
        [
            str(Path(git_binary).resolve(strict=True)),
            "config",
            "--file",
            str(path),
            "--no-includes",
            "--get-regexp",
            r"^(extensions\.partialClone|remote\..*\.promisor)$",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10.0,
        cwd=os.sep,
        env=_qualification_git_env(),
    )
    if promisor.returncode == 0:
        raise LinkedWorktreeQualificationError(
            f"{label} configures a partial-clone/promisor repository; lazy object fetch is unsupported"
        )
    if promisor.returncode != 1:
        detail = (promisor.stderr or promisor.stdout).strip() or f"exit code {promisor.returncode}"
        raise LinkedWorktreeQualificationError(
            f"unable to inspect {label} promisor posture: {detail}"
        )


def _qualification_git_env() -> dict[str, str]:
    """Return the fixed no-network/no-replacement Git qualification environment."""

    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/nonexistent-agent-pbx-git-qualification-home",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_LITERAL_PATHSPECS": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    }


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
    writable_paths: tuple[Path, ...],
) -> tuple[str, Mapping[str, Any] | None, str]:
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
                    existing = profile_definitions.get(str(name), {})
                    profile_definitions[str(name)] = _deep_merge_mapping(
                        existing,
                        definition,
                    )
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
    resolved_base = _validate_base_permission_profile(
        base_profile,
        definitions=profile_definitions,
        common_dir=common_dir,
    )
    try:
        profile = _materialize_linked_edit_permission_profile(
            resolved_base,
            writable_paths=writable_paths,
        )
    except _BrokerRequiredProfile:
        # Codex 0.166 cannot compose split read/deny policy with these external
        # writes without metadata bootstrap pollution or a broader grant. Keep
        # the paths as qualification evidence for the local root-approved
        # broker, but never emit them into the runtime profile.
        return base_profile, None, "tui_root_commit_broker"
    return base_profile, profile, "direct_permission_profile"


def _validate_base_permission_profile(
    profile_name: str,
    *,
    definitions: Mapping[str, Mapping[str, Any]],
    common_dir: Path,
) -> Mapping[str, Any]:
    current = profile_name
    visited: set[str] = set()
    chain: list[Mapping[str, Any]] = []
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
        if definition.get("workspace_roots"):
            raise LinkedWorktreeQualificationError(
                f"permission profile {current!r} defines configured workspace_roots; "
                "linked Git administration grants require one runtime cwd only"
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
        chain.append(definition)
        current = str(definition.get("extends") or "").strip()
        if not current:
            raise LinkedWorktreeQualificationError(
                f"permission profile {profile_name!r} does not inherit from :workspace"
            )
    if current != ":workspace":
        raise LinkedWorktreeQualificationError(
            f"permission profile {profile_name!r} must ultimately extend :workspace, not {current!r}"
        )
    resolved: Mapping[str, Any] = {}
    for definition in reversed(chain):
        resolved = _deep_merge_mapping(resolved, definition)
    return resolved


def _deep_merge_mapping(
    base: Mapping[str, Any],
    overlay: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        current = merged.get(str(key))
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[str(key)] = _deep_merge_mapping(current, value)
        else:
            merged[str(key)] = copy.deepcopy(value)
    return merged


def _materialize_linked_edit_permission_profile(
    base_profile: Mapping[str, Any],
    *,
    writable_paths: tuple[Path, ...],
) -> Mapping[str, Any]:
    """Flatten one base profile without promoting Git grants to workspaces.

    Codex protects metadata-like children below every writable sandbox root.
    In 0.166, combining split read/deny policy with external Git writes routes
    through bubblewrap and creates protected mount targets below those Git
    administration directories. Preserve only base semantics already supplied
    by ``:workspace``; fail closed on split rules instead of weakening them.
    """

    allowed_keys = {"description", "extends", "filesystem", "network"}
    unknown = sorted(str(key) for key in base_profile if str(key) not in allowed_keys)
    if unknown:
        raise LinkedWorktreeQualificationError(
            "base permission profile has unsupported fields for safe materialization: "
            + ", ".join(unknown)
        )
    filesystem: dict[str, Any] = {}
    raw_filesystem = base_profile.get("filesystem")
    if raw_filesystem is not None and not isinstance(raw_filesystem, Mapping):
        raise LinkedWorktreeQualificationError(
            "base permission profile filesystem policy is malformed"
        )
    for raw_path, raw_permission in (raw_filesystem or {}).items():
        path_text = str(raw_path).strip()
        if path_text == ":workspace_roots":
            if not isinstance(raw_permission, Mapping):
                raise LinkedWorktreeQualificationError(
                    "base :workspace_roots policy must be a scoped table"
                )
            _validate_workspace_permissions_for_git_admin_grants(raw_permission)
            continue
        if path_text == "glob_scan_max_depth" or path_text.startswith(":"):
            if path_text != "glob_scan_max_depth":
                raise _BrokerRequiredProfile(
                    "base permission profile requires split filesystem enforcement "
                    f"through {path_text!r}; Codex 0.166 cannot combine that policy "
                    "with external Git administration writes without creating "
                    "protected metadata mount targets"
                )
            filesystem[path_text] = copy.deepcopy(raw_permission)
            continue
        if not Path(path_text).expanduser().is_absolute():
            raise LinkedWorktreeQualificationError(
                f"base filesystem permission is neither absolute nor scoped to "
                f":workspace_roots: {path_text!r}"
            )
        raise _BrokerRequiredProfile(
            "base permission profile contains an absolute filesystem rule; Codex "
            "0.166 cannot preserve split secret/read policy while adding external "
            "Git administration writes without creating protected metadata mount "
            f"targets ({path_text})"
        )

    for path in writable_paths:
        key = str(path)
        existing = filesystem.get(key)
        if existing is not None and str(existing).strip().casefold() != "write":
            raise LinkedWorktreeQualificationError(
                f"base permission profile conflicts with required Git administration "
                f"write path {path}"
            )
        filesystem[key] = "write"

    profile: dict[str, Any] = {
        "description": "Agent PBX linked edit fork with sandbox-only Git administration grants",
        "extends": ":workspace",
        "filesystem": filesystem,
    }
    network = base_profile.get("network")
    if network is not None:
        if not isinstance(network, Mapping):
            raise LinkedWorktreeQualificationError(
                "base permission profile network policy is malformed"
            )
        profile["network"] = copy.deepcopy(dict(network))
    return profile


def _validate_workspace_permissions_for_git_admin_grants(
    scoped: Mapping[str, Any],
) -> None:
    for raw_subpath, raw_permission in scoped.items():
        subpath = str(raw_subpath).strip()
        mode = str(raw_permission).strip().casefold()
        if mode not in {"read", "write", "deny", "none"}:
            raise LinkedWorktreeQualificationError(
                f"unsupported :workspace_roots permission for {subpath!r}"
            )
        if subpath == ".":
            if mode != "write":
                raise LinkedWorktreeQualificationError(
                    "linked edit forks require the workspace root to remain writable"
                )
            continue
        if subpath == ".git" and mode in {"read", "write"}:
            # The built-in workspace profile already keeps the linked .git
            # marker readable. Exact qualified administration roots below are
            # granted separately; the pointer file itself must not be writable.
            continue
        raise _BrokerRequiredProfile(
            "base permission profile contains a scoped workspace filesystem rule; "
            "Codex 0.166 cannot preserve that split policy while adding external Git "
            "administration writes without creating protected metadata mount targets "
            f"({subpath!r})"
        )
