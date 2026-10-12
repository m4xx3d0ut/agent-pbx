from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_pbx import __version__
from agent_pbx.demo.contract import (
    DEMO_CONTRACT_VERSION,
    DEMO_SCENARIO_ID,
    public_demo_contract,
)
from agent_pbx.demo.policy import (
    PublicDemoPolicyError,
    build_public_demo_environment,
    ensure_private_directory,
    sensitive_environment_names,
    validate_public_demo_environment,
    validate_web_binding,
)


def test_public_demo_contract_is_versioned_and_site_consumable() -> None:
    contract = public_demo_contract()

    assert contract["api_version"] == DEMO_CONTRACT_VERSION
    assert contract["scenario"] == {
        "id": DEMO_SCENARIO_ID,
        "version": 1,
        "agent_pbx_version": __version__,
        "deterministic": True,
    }
    assert contract["transport"]["contract_path"] == "/demo-contract.json"
    assert contract["policy"]["live_credentials"] is False
    assert contract["navigation"]["F10"] == "Launch project workspace"


def test_public_demo_environment_uses_allowlist_and_private_roots(
    tmp_path: Path,
) -> None:
    home = ensure_private_directory(tmp_path / "home")
    runtime = ensure_private_directory(tmp_path / "runtime")
    fixture_bin = tmp_path / "bin"
    source = {
        "PATH": "/usr/bin",
        "LANG": "en_US.UTF-8",
        "OPENAI_API_KEY": "must-not-leak",
        "AGENT_PBX_TOKEN": "must-not-leak",
        "SSH_AUTH_SOCK": "/tmp/agent.sock",
        "TMUX": "/tmp/tmux,1,0",
        "UNRELATED_VALUE": "also-not-inherited",
    }

    result = build_public_demo_environment(
        source,
        home=home,
        runtime_dir=runtime,
        path_prefix=fixture_bin,
    )

    assert result["HOME"] == str(home)
    assert result["XDG_RUNTIME_DIR"] == str(runtime)
    assert result["PATH"] == f"{fixture_bin}{os.pathsep}/usr/bin"
    assert result["AGENT_PBX_PUBLIC_DEMO"] == "1"
    assert not sensitive_environment_names(result)
    assert "UNRELATED_VALUE" not in result


def test_public_demo_environment_rejects_credentials_and_wrong_roots(
    tmp_path: Path,
) -> None:
    home = ensure_private_directory(tmp_path / "home")
    runtime = ensure_private_directory(tmp_path / "runtime")
    with pytest.raises(PublicDemoPolicyError, match="forbidden values"):
        validate_public_demo_environment(
            {
                "HOME": str(home),
                "XDG_RUNTIME_DIR": str(runtime),
                "GITHUB_TOKEN": "secret",
            },
            home=home,
            runtime_dir=runtime,
        )
    with pytest.raises(PublicDemoPolicyError, match="HOME"):
        validate_public_demo_environment(
            {"HOME": "/home/operator", "XDG_RUNTIME_DIR": str(runtime)},
            home=home,
            runtime_dir=runtime,
        )


def test_public_demo_directory_is_private(tmp_path: Path) -> None:
    target = ensure_private_directory(tmp_path / "session")
    assert target.stat().st_mode & 0o777 == 0o700


def test_web_binding_is_local_by_default_and_explicit_when_public() -> None:
    public_url, origins = validate_web_binding(
        host="127.0.0.1",
        public_url="http://127.0.0.1:8000",
        allowed_origins=(),
        allow_public_bind=False,
    )
    assert public_url == "http://127.0.0.1:8000"
    assert origins == (public_url,)

    with pytest.raises(PublicDemoPolicyError, match="allow-public-bind"):
        validate_web_binding(
            host="0.0.0.0",
            public_url="https://demo.example",
            allowed_origins=("https://www.example",),
            allow_public_bind=False,
        )
    with pytest.raises(PublicDemoPolicyError, match="HTTPS"):
        validate_web_binding(
            host="0.0.0.0",
            public_url="http://demo.example",
            allowed_origins=("https://www.example",),
            allow_public_bind=True,
        )

    assert validate_web_binding(
        host="0.0.0.0",
        public_url="https://demo.example/",
        allowed_origins=("https://www.example/",),
        allow_public_bind=True,
    ) == (
        "https://demo.example",
        ("https://demo.example", "https://www.example"),
    )
