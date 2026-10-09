from __future__ import annotations

from pathlib import Path

import pytest

from agent_pbx.codex_theme import (
    MANAGED_CODEX_THEME_NAME,
    install_managed_codex_theme,
    managed_codex_theme_status,
)


def test_managed_codex_theme_install_is_atomic_selected_and_idempotent(
    tmp_path: Path,
) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('[tui]\nalternate_screen = "never"\n', encoding="utf-8")

    installed = install_managed_codex_theme(codex_home=codex_home)

    assert installed.ready is True
    assert installed.state == "matched"
    assert installed.selected is True
    assert installed.changed is True
    assert installed.backup_path is not None
    theme_path = Path(installed.path)
    assert theme_path.stat().st_mode & 0o777 == 0o600
    assert "Agent PBX 1337 Accessible" in theme_path.read_text(encoding="utf-8")
    config_text = config.read_text(encoding="utf-8")
    assert 'alternate_screen = "never"' in config_text
    assert f'theme = "{MANAGED_CODEX_THEME_NAME}"' in config_text

    unchanged = install_managed_codex_theme(codex_home=codex_home)
    assert unchanged.ready is True
    assert unchanged.changed is False
    assert unchanged.backup_path is None


def test_managed_codex_theme_refuses_drift_without_force(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    installed = install_managed_codex_theme(codex_home=codex_home)
    path = Path(installed.path)
    path.write_text("locally modified", encoding="utf-8")

    status = managed_codex_theme_status(codex_home=codex_home)
    assert status.state == "drifted"
    with pytest.raises(ValueError, match="local changes"):
        install_managed_codex_theme(codex_home=codex_home)

    repaired = install_managed_codex_theme(codex_home=codex_home, force=True)
    assert repaired.ready is True
    assert repaired.state == "matched"
    assert repaired.theme_backup_path is not None
    assert Path(repaired.theme_backup_path).read_text(encoding="utf-8") == "locally modified"


def test_managed_codex_theme_refuses_symlink_target(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    themes = codex_home / "themes"
    themes.mkdir(parents=True)
    target = tmp_path / "foreign-theme"
    target.write_text("do not replace", encoding="utf-8")
    path = themes / "agent-pbx-1337.tmTheme"
    path.symlink_to(target)

    assert managed_codex_theme_status(codex_home=codex_home).state == "unsafe"
    with pytest.raises(PermissionError, match="unsafe Codex theme path"):
        install_managed_codex_theme(codex_home=codex_home, force=True)
    assert target.read_text(encoding="utf-8") == "do not replace"
