from pathlib import Path

import pytest

from agent_pbx.codex_config import load_codex_config_view, patch_codex_config


def test_codex_config_view_redacts_sensitive_sections(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                'model = "gpt-5.6-sol"',
                'approval_policy = "on-request"',
                "",
                "[model_providers.custom]",
                'name = "Custom"',
                'base_url = "https://example.invalid"',
                "",
                '[mcp_servers."agent-pbx"]',
                'url = "http://127.0.0.1:8767/mcp"',
                'bearer_token_env_var = "AGENT_PBX_TOKEN"',
                'http_headers = { Authorization = "Bearer secret" }',
                "",
                "[otel]",
                'headers = { Authorization = "secret" }',
                "",
                "[custom_table]",
                'enabled = "yes"',
            ]
        ),
        encoding="utf-8",
    )

    view = load_codex_config_view(config_path=config)

    model = next(field for field in view["fields"] if field["key"] == "model")
    assert model["value"] == "gpt-5.6-sol"
    hidden = {item["key"]: item for item in view["hidden_items"]}
    assert hidden["model_providers"]["configured"] is True
    assert hidden["provider_secrets"]["configured"] is True
    assert hidden["telemetry_keys"]["configured"] is True
    assert hidden["auth_credentials"]["configured"] is True
    assert hidden["arbitrary_nested_tables"]["detail"]["table_names"] == ["custom_table"]
    server = view["mcp_servers"][0]
    assert server["name"] == "agent-pbx"
    assert server["bearer_token_env_var"] == "AGENT_PBX_TOKEN"
    assert server["http_headers"] == {"Authorization": "configured"}
    assert "Bearer secret" not in str(view)


def test_codex_config_patch_updates_safe_fields_and_preserves_unknown(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            [
                '# managed by operator',
                'model = "gpt-5.5" # keep comment',
                "",
                "[custom_table]",
                'value = "preserve"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = patch_codex_config(
        config_path=config,
        updates={"model": "gpt-5.6-sol", "service_tier": "priority"},
    )

    text = config.read_text(encoding="utf-8")
    assert 'model = "gpt-5.6-sol"  # keep comment' in text
    assert 'service_tier = "priority"' in text
    assert "[custom_table]" in text
    assert 'value = "preserve"' in text
    assert result.backup_path is not None
    assert Path(result.backup_path).exists()
    assert set(result.changed_paths) == {"model", "service_tier"}


def test_codex_config_patch_blocks_model_providers(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5.5"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="hidden/read-only"):
        patch_codex_config(
            config_path=config,
            updates={"model_providers": {"custom": {"base_url": "x"}}},
        )


@pytest.mark.parametrize(
    "path",
    [
        "mcp_servers.agent-pbx.env.API_TOKEN",
        "mcp_servers.agent-pbx.env_http_headers.Authorization",
        "mcp_servers.agent-pbx.http_headers.Authorization",
    ],
)
def test_codex_config_secret_patch_never_returns_value(tmp_path: Path, path: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[mcp_servers.agent-pbx]\nurl = "http://pbx/mcp"\n', encoding="utf-8")

    result = patch_codex_config(
        config_path=config,
        secret_updates=[{"path": path, "value": "super-secret"}],
    )

    assert "super-secret" in config.read_text(encoding="utf-8")
    assert "super-secret" not in str(result.view)
    assert path in result.changed_paths


def test_codex_config_agent_pbx_mcp_patch(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"

    result = patch_codex_config(
        config_path=config,
        agent_pbx_mcp={
            "url": "http://127.0.0.1:8767/mcp",
            "bearer_token_env_var": "AGENT_PBX_TOKEN",
            "default_tools_approval_mode": "approve",
        },
    )

    text = config.read_text(encoding="utf-8")
    assert "[mcp_servers.agent-pbx]" in text
    assert 'url = "http://127.0.0.1:8767/mcp"' in text
    assert 'bearer_token_env_var = "AGENT_PBX_TOKEN"' in text
    assert "super-secret" not in str(result.view)
    assert result.backup_path is None


def test_codex_config_patch_blocks_requirement_conflict(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    codex_home.mkdir()
    config = codex_home / "config.toml"
    config.write_text('sandbox_mode = "read-only"\n', encoding="utf-8")
    (codex_home / "managed_config.toml").write_text(
        'allowed_sandbox_modes = ["workspace-write"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="allowed_sandbox_modes"):
        patch_codex_config(
            config_path=config,
            updates={"sandbox_mode": "danger-full-access"},
        )
