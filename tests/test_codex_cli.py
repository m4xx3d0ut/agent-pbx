from agent_pbx.codex_cli import (
    codex_model_config_overrides,
    configured_codex_model,
    parse_codex_help_capabilities,
    parse_codex_doctor_posture,
    parse_codex_model_catalog_json,
    parse_codex_version,
    validate_codex_model_slug,
)


def test_parse_codex_version() -> None:
    assert parse_codex_version("codex-cli 0.157.1\n") == "0.157.1"


def test_parse_codex_help_capabilities() -> None:
    capabilities = parse_codex_help_capabilities(
        """
Usage: codex [OPTIONS] [PROMPT]

Commands:
  exec      Run Codex non-interactively
  queue     Submit commands to a running Codex session
  fork      Fork a Codex session

Options:
  -m, --model <MODEL>        Model to use
  -c, --config <key=value>   Override a configuration value
""",
        version="0.157.1",
    )

    assert capabilities.version == "0.157.1"
    assert capabilities.supports_queue
    assert capabilities.supports_fork
    assert capabilities.supports_model_flag
    assert capabilities.supports_config_overrides


def test_parse_codex_model_catalog_json() -> None:
    models = parse_codex_model_catalog_json(
        """
{
  "models": [
    {
      "slug": "gpt-6-astra",
      "displayName": "GPT-6 Astra",
      "defaultReasoningLevel": "xhigh",
      "supportedReasoningLevels": [{"effort": "low"}, {"effort": "xhigh"}],
      "serviceTiers": [{"value": "auto"}, {"value": "priority"}],
      "defaultServiceTier": "auto",
      "visibility": "show",
      "shellType": "unified_exec",
      "supportedInApi": true
    }
  ]
}
"""
    )

    assert len(models) == 1
    assert models[0].slug == "gpt-6-astra"
    assert models[0].supported_reasoning_levels == ("low", "xhigh")
    assert models[0].service_tiers == ("auto", "priority")
    assert not models[0].hidden


def test_parse_codex_model_catalog_json_accepts_current_snake_case() -> None:
    models = parse_codex_model_catalog_json(
        """
{
  "models": [
    {
      "slug": "gpt-5.6-sol",
      "display_name": "GPT-5.6 Sol",
      "default_reasoning_level": "high",
      "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
      "service_tiers": [{"id": "priority", "name": "Fast"}],
      "default_service_tier": "priority",
      "visibility": "list",
      "shell_type": "unified_exec",
      "supported_in_api": true
    }
  ]
}
"""
    )

    assert len(models) == 1
    assert models[0].slug == "gpt-5.6-sol"
    assert models[0].display_name == "GPT-5.6 Sol"
    assert models[0].service_tiers == ("priority",)


def test_parse_codex_doctor_posture() -> None:
    fields = parse_codex_doctor_posture(
        """
Configuration
  ⚠ config       config loaded
      model                    gpt-5.5 · openai
Updates
      latest version           0.158.0
Background Server
      app-server version       0.158.0
"""
    )

    assert fields == {
        "configured_model": "gpt-5.5",
        "latest_stable_version": "0.158.0",
        "app_server_version": "0.158.0",
    }


def test_configured_codex_model_prefers_agent_pbx_env() -> None:
    model, source = configured_codex_model(
        env={"AGENT_PBX_TUI_CODEX_MODEL": "gpt-5.6-sol"},
        doctor_fields={"configured_model": "gpt-5.5"},
    )

    assert (model, source) == ("gpt-5.6-sol", "AGENT_PBX_TUI_CODEX_MODEL")


def test_validate_codex_model_slug_warns_for_unknown_model() -> None:
    catalog = parse_codex_model_catalog_json(
        '{"models": [{"slug": "gpt-5.6-sol"}]}'
    )

    known, warnings = validate_codex_model_slug("codex-5.6", catalog)

    assert known is False
    assert warnings == (
        "Configured Codex model 'codex-5.6' is not in `codex debug models`.",
    )


def test_codex_model_config_overrides_skips_empty_values() -> None:
    assert codex_model_config_overrides(
        model="gpt-6-sol",
        reasoning_effort="high",
        service_tier="priority",
    ) == [
        'model="gpt-6-sol"',
        'model_reasoning_effort="high"',
        'service_tier="priority"',
    ]
    assert codex_model_config_overrides(model="", reasoning_effort=" ", service_tier=None) == []


def test_validate_codex_update_target_rejects_shell_text() -> None:
    from agent_pbx.codex_cli import validate_codex_update_target

    assert validate_codex_update_target("0.159.0") == "0.159.0"
    assert validate_codex_update_target("latest") == "latest"

    import pytest

    with pytest.raises(ValueError):
        validate_codex_update_target("latest; rm -rf /tmp/nope")


def test_update_codex_cli_package_runs_allowlisted_npm_command(monkeypatch) -> None:
    from agent_pbx.codex_cli import CodexCliPosture, update_codex_cli_package

    postures = iter(
        [
            CodexCliPosture(
                shell_version="0.158.0",
                app_server_version="0.159.0",
                latest_stable_version="0.159.0",
                configured_model="gpt-5.5",
                configured_model_source="codex doctor",
                model_catalog_count=1,
                model_known=True,
                warnings=(),
                install_method="npm",
                npm_managed=True,
            ),
            CodexCliPosture(
                shell_version="0.159.0",
                app_server_version="0.159.0",
                latest_stable_version="0.159.0",
                configured_model="gpt-5.5",
                configured_model_source="codex doctor",
                model_catalog_count=1,
                model_known=True,
                warnings=(),
                install_method="npm",
                npm_managed=True,
            ),
        ]
    )
    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stdout = "updated"
        stderr = ""

    monkeypatch.setattr("agent_pbx.codex_cli.shutil.which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(
        "agent_pbx.codex_cli.inspect_codex_posture",
        lambda *args, **kwargs: next(postures),
    )

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Completed()

    monkeypatch.setattr("agent_pbx.codex_cli.subprocess.run", fake_run)

    result = update_codex_cli_package(target="0.159.0")

    assert result["ok"] is True
    assert calls == [["npm", "install", "-g", "@openai/codex@0.159.0"]]
    assert result["before"]["shell_version"] == "0.158.0"
    assert result["after"]["shell_version"] == "0.159.0"
