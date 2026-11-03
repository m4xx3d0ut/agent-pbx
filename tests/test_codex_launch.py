from pathlib import Path

from agent_pbx.codex_launch import (
    MANAGED_CALLER_PBX_TOOLS,
    managed_caller_bootstrap_id,
    managed_caller_bootstrap_prompt,
    managed_caller_codex_overrides,
)


def test_managed_caller_overrides_are_narrow_and_noninteractive() -> None:
    overrides = managed_caller_codex_overrides("http://127.0.0.1:9876")

    assert overrides[0] == "check_for_update_on_startup=false"
    mcp = overrides[1]
    assert 'url = "http://127.0.0.1:9876/mcp"' in mcp
    assert 'bearer_token_env_var = "AGENT_PBX_TOKEN"' in mcp
    assert 'default_tools_approval_mode = "approve"' in mcp
    for tool in MANAGED_CALLER_PBX_TOOLS:
        assert tool in mcp
        assert f'{tool} = {{approval_mode = "approve"}}' in mcp
    assert "pbx_queue_command" not in mcp
    assert "pbx_operator_start_campaign" not in mcp
    assert "workerbee" not in mcp


def test_managed_caller_bootstrap_is_audited_and_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "demo"
    repo.mkdir()
    kwargs = {
        "agent_id": "codex-demo",
        "project_path": repo,
        "profile_id": "sol-high",
        "runtime_server_mode": "dedicated",
        "source_codex_session_id": "source-session-1",
    }

    first = managed_caller_bootstrap_id(**kwargs)
    second = managed_caller_bootstrap_id(**kwargs)
    changed = managed_caller_bootstrap_id(
        **{**kwargs, "source_codex_session_id": "source-session-2"}
    )
    prompt = managed_caller_bootstrap_prompt(
        agent_id="codex-demo",
        project="demo",
        cwd=repo,
        source_codex_session_id="source-session-1",
    )

    assert first == second
    assert changed != first
    assert "pbx_register_agent" in prompt
    assert "metadata.codex_session_id: the current Codex session id" in prompt
    assert "metadata.source_codex_session_id: source-session-1" in prompt
    assert 'status="waiting"' in prompt
    assert "Do not begin project work" in prompt
