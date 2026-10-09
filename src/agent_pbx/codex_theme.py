from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import os
from pathlib import Path
import shutil
import stat
import tempfile
import time
from typing import Any, Mapping

from .codex_config import codex_home_from_env, patch_codex_config
from .ui.theme import codex_syntax_theme_xml

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py310 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


MANAGED_CODEX_THEME_NAME = "agent-pbx-1337"
MANAGED_CODEX_THEME_FILENAME = f"{MANAGED_CODEX_THEME_NAME}.tmTheme"


@dataclass(frozen=True, slots=True)
class ManagedCodexThemeStatus:
    codex_home: str
    path: str
    config_path: str
    configured_theme: str
    state: str
    expected_sha256: str
    actual_sha256: str
    selected: bool
    ready: bool
    changed: bool = False
    backup_path: str | None = None
    theme_backup_path: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def managed_codex_theme_status(
    *,
    codex_home: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ManagedCodexThemeStatus:
    root = (codex_home or codex_home_from_env(env)).expanduser()
    path = root / "themes" / MANAGED_CODEX_THEME_FILENAME
    config_path = root / "config.toml"
    expected = codex_syntax_theme_xml().encode("utf-8")
    expected_hash = hashlib.sha256(expected).hexdigest()
    configured_theme = _configured_theme(config_path)
    actual_hash = ""
    state = "missing"
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        state = "unreadable"
    else:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            state = "unsafe"
        elif info.st_uid != os.getuid():
            state = "foreign-owner"
        else:
            try:
                actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                state = "unreadable"
            else:
                state = "matched" if actual_hash == expected_hash else "drifted"
    selected = configured_theme == MANAGED_CODEX_THEME_NAME
    return ManagedCodexThemeStatus(
        codex_home=str(root),
        path=str(path),
        config_path=str(config_path),
        configured_theme=configured_theme,
        state=state,
        expected_sha256=expected_hash,
        actual_sha256=actual_hash,
        selected=selected,
        ready=state == "matched" and selected,
    )


def install_managed_codex_theme(
    *,
    codex_home: Path | None = None,
    env: Mapping[str, str] | None = None,
    force: bool = False,
) -> ManagedCodexThemeStatus:
    root = (codex_home or codex_home_from_env(env)).expanduser()
    themes = root / "themes"
    path = themes / MANAGED_CODEX_THEME_FILENAME
    before = managed_codex_theme_status(codex_home=root)
    if before.state in {"unsafe", "foreign-owner", "unreadable"}:
        raise PermissionError(
            f"Refusing to replace unsafe Codex theme path {path} ({before.state})."
        )
    if before.state == "drifted" and not force:
        raise ValueError(
            f"Managed Codex theme {path} has local changes; rerun with force after review."
        )

    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _validate_user_directory(root, label="Codex home")
    themes.mkdir(parents=True, exist_ok=True, mode=0o700)
    _validate_user_directory(themes, label="Codex themes directory")

    body = codex_syntax_theme_xml()
    theme_changed = before.state != "matched"
    theme_backup_path: str | None = None
    if theme_changed:
        if before.state == "drifted":
            timestamp = time.strftime("%Y%m%d%H%M%S")
            backup = path.with_name(f"{path.name}.agent-pbx.{timestamp}.bak")
            shutil.copy2(path, backup)
            backup.chmod(stat.S_IMODE(path.stat().st_mode))
            theme_backup_path = str(backup)
        _write_atomic(path, body, mode=0o600)
    config_result = patch_codex_config(
        config_path=root / "config.toml",
        tui_theme=MANAGED_CODEX_THEME_NAME,
    )
    after = managed_codex_theme_status(codex_home=root)
    return ManagedCodexThemeStatus(
        **{
            **after.public_dict(),
            "changed": theme_changed or bool(config_result.changed_paths),
            "backup_path": config_result.backup_path,
            "theme_backup_path": theme_backup_path,
        }
    )


def render_managed_codex_theme_status(status: ManagedCodexThemeStatus) -> str:
    selection = status.configured_theme or "unset"
    return "\n".join(
        (
            f"Codex theme: {status.state}",
            f"Path: {status.path}",
            f"Configured tui.theme: {selection}",
            f"Expected: {MANAGED_CODEX_THEME_NAME}",
            f"Ready: {'yes' if status.ready else 'no'}",
            *(
                (f"Config backup: {status.backup_path}",)
                if status.backup_path
                else ()
            ),
            *(
                (f"Theme backup: {status.theme_backup_path}",)
                if status.theme_backup_path
                else ()
            ),
        )
    )


def _configured_theme(config_path: Path) -> str:
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    tui = parsed.get("tui") if isinstance(parsed, dict) else None
    return str(tui.get("theme") or "").strip() if isinstance(tui, dict) else ""


def _validate_user_directory(path: Path, *, label: str) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PermissionError(f"{label} is not a real directory: {path}")
    if info.st_uid != os.getuid():
        raise PermissionError(f"{label} is not owned by the current user: {path}")


def _write_atomic(path: Path, body: str, *, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            if body and not body.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
