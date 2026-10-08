from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def restore_agent_pbx_environment() -> None:
    """Keep CLI config loading from leaking host settings between tests."""

    original = {
        key: value
        for key, value in os.environ.items()
        if key.startswith("AGENT_PBX_")
    }
    try:
        yield
    finally:
        for key in tuple(os.environ):
            if key.startswith("AGENT_PBX_") and key not in original:
                os.environ.pop(key, None)
        os.environ.update(original)
