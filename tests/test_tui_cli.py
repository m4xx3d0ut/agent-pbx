import os

from agent_pbx.envfile import parse_env_file
from agent_pbx import tui_cli


def test_parse_env_file_supports_exports_quotes_and_expansion(tmp_path) -> None:
    config = tmp_path / "tui.env"
    config.write_text(
        "\n".join(
            [
                "# comment",
                "export AGENT_PBX_HOST=pbx.local",
                "AGENT_PBX_PORT=8767",
                'AGENT_PBX_SERVER_URL="http://${AGENT_PBX_HOST}:${AGENT_PBX_PORT}"',
                "AGENT_PBX_TOKEN='token value'",
                "IGNORED_LINE",
                "",
            ]
        ),
        encoding="utf-8",
    )

    values = parse_env_file(config, base_env={})

    assert values["AGENT_PBX_HOST"] == "pbx.local"
    assert values["AGENT_PBX_PORT"] == "8767"
    assert values["AGENT_PBX_SERVER_URL"] == "http://pbx.local:8767"
    assert values["AGENT_PBX_TOKEN"] == "token value"


def test_tui_cli_init_config_writes_private_template(tmp_path) -> None:
    config = tmp_path / "agent-pbx" / "tui.env"

    result = tui_cli.main(["--config", str(config), "--init-config"])

    assert result == 0
    assert config.exists()
    assert config.stat().st_mode & 0o777 == 0o600
    text = config.read_text(encoding="utf-8")
    assert "AGENT_PBX_SERVER_URL=http://127.0.0.1:8767" in text
    assert "AGENT_PBX_TOKEN=change-me" in text


def test_tui_cli_loads_config_defaults(monkeypatch, tmp_path) -> None:
    config = tmp_path / "tui.env"
    config.write_text(
        "\n".join(
            [
                "AGENT_PBX_SERVER_URL=http://pbx.lan:8767",
                "AGENT_PBX_TOKEN=config-token",
                "AGENT_PBX_TUI_THEME=cyberpunk",
                "",
            ]
        ),
        encoding="utf-8",
    )
    calls = []

    monkeypatch.delenv("AGENT_PBX_SERVER_URL", raising=False)
    monkeypatch.delenv("AGENT_PBX_TOKEN", raising=False)
    monkeypatch.setattr(
        tui_cli,
        "run_tui",
        lambda *, server, token: calls.append((server, token)),
    )

    result = tui_cli.main(["--config", str(config)])

    assert result == 0
    assert calls == [("http://pbx.lan:8767", "config-token")]
    assert os.environ["AGENT_PBX_TUI_THEME"] == "cyberpunk"


def test_tui_cli_flags_override_config(monkeypatch, tmp_path) -> None:
    config = tmp_path / "tui.env"
    config.write_text(
        "AGENT_PBX_SERVER_URL=http://pbx.lan:8767\n"
        "AGENT_PBX_TOKEN=config-token\n",
        encoding="utf-8",
    )
    calls = []

    monkeypatch.delenv("AGENT_PBX_SERVER_URL", raising=False)
    monkeypatch.delenv("AGENT_PBX_TOKEN", raising=False)
    monkeypatch.setattr(
        tui_cli,
        "run_tui",
        lambda *, server, token: calls.append((server, token)),
    )

    result = tui_cli.main(
        [
            "--config",
            str(config),
            "--server",
            "http://override:8767",
            "--token",
            "override-token",
        ]
    )

    assert result == 0
    assert calls == [("http://override:8767", "override-token")]


def test_tui_cli_environment_overrides_config(monkeypatch, tmp_path) -> None:
    config = tmp_path / "tui.env"
    config.write_text(
        "AGENT_PBX_SERVER_URL=http://pbx.lan:8767\n"
        "AGENT_PBX_TOKEN=config-token\n",
        encoding="utf-8",
    )
    calls = []

    monkeypatch.setenv("AGENT_PBX_SERVER_URL", "http://env:8767")
    monkeypatch.setenv("AGENT_PBX_TOKEN", "env-token")
    monkeypatch.setattr(
        tui_cli,
        "run_tui",
        lambda *, server, token: calls.append((server, token)),
    )

    result = tui_cli.main(["--config", str(config)])

    assert result == 0
    assert calls == [("http://env:8767", "env-token")]


def test_tui_cli_explicit_missing_config_returns_error(tmp_path) -> None:
    missing = tmp_path / "missing.env"

    assert tui_cli.main(["--config", str(missing)]) == 2
