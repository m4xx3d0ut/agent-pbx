from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.app import SystemCommand

from agent_pbx.ui.button_router import (
    ButtonRouteDescriptor,
    KNOWN_BUTTON_IDS,
    route_button_pressed,
    validate_button_routes,
)
from agent_pbx.ui.command_catalog import (
    CommandCatalog,
    REVIEWED_COMMAND_TITLES,
    validate_command_titles,
)


def test_reviewed_command_and_button_catalogs_are_exact_and_unique() -> None:
    assert len(REVIEWED_COMMAND_TITLES) == 215
    assert len(set(REVIEWED_COMMAND_TITLES)) == 215
    assert len(KNOWN_BUTTON_IDS) == 123
    assert "send" in KNOWN_BUTTON_IDS
    assert "purge-agent" in KNOWN_BUTTON_IDS


def test_catalog_validators_reject_duplicate_identifiers() -> None:
    with pytest.raises(ValueError, match="unique"):
        validate_command_titles(("/duplicate", "/duplicate"))
    with pytest.raises(ValueError, match="unique"):
        validate_button_routes(
            (
                ButtonRouteDescriptor("duplicate", "test"),
                ButtonRouteDescriptor("duplicate", "test"),
            )
        )


def test_dynamic_command_adoption_uses_the_same_duplicate_guard() -> None:
    catalog = CommandCatalog()
    catalog.command("/same", "first", lambda: None)

    with pytest.raises(ValueError, match="duplicate"):
        catalog.adopt(SystemCommand("/same", "second", lambda: None))


@pytest.mark.asyncio
async def test_button_router_dispatches_known_id_and_fails_closed_for_unknown() -> None:
    calls: list[str] = []

    async def send_input() -> None:
        calls.append("send")

    app = SimpleNamespace(send_input=send_input)

    assert await route_button_pressed(app, "unknown-button") is False
    assert calls == []
    assert await route_button_pressed(app, "send") is True
    assert calls == ["send"]
