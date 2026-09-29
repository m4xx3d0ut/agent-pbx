from agent_pbx.codex_cli import (
    codex_model_config_overrides,
    parse_codex_help_capabilities,
    parse_codex_model_catalog_json,
    parse_codex_version,
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
