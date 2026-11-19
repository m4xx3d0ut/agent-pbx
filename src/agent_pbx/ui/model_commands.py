from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Iterator

from textual.app import SystemCommand

from .screens import slugify


@dataclass(frozen=True)
class ModelCommand:
    title: str
    help: str
    callback_name: str
    profile_id: str = ""


MODEL_COMMANDS = (
    ModelCommand("/codex model", "Open Codex model presets", "palette_codex_model"),
    ModelCommand("/codex model inspect", "Inspect the live Codex /model selection", "palette_codex_model_inspect"),
    ModelCommand("/codex model adopt", "Adopt the live Codex model for this entity's resume", "palette_codex_model_adopt"),
    ModelCommand("/codex model return", "Return the selected entity to its PBX role default", "palette_codex_model_return"),
    ModelCommand("/codex model native", "Open the native Codex /model picker", "palette_codex_model_native"),
    ModelCommand("/codex model plan", "Plan model migration for the selected agent", "palette_codex_model_plan"),
    ModelCommand("/codex model cleanup plan", "Plan one-time starred/operator model cleanup", "palette_codex_model_cleanup_plan"),
    ModelCommand("/codex model terra-xhigh", "Restart selected Codex pane on Terra 6.1/xhigh", "palette_codex_model_restart", "terra-6.1-xhigh"),
    ModelCommand("/codex model sol-medium", "Restart selected Codex pane on Sol 6.2/medium", "palette_codex_model_restart", "sol-6.2-medium"),
    ModelCommand("/codex model sol-high", "Restart selected Codex pane on Sol 6.2/high", "palette_codex_model_restart", "sol-6.2-high"),
    ModelCommand("/codex model sol-xhigh", "Restart selected Codex pane on Sol 6.2/xhigh", "palette_codex_model_restart", "sol-6.2-xhigh"),
    ModelCommand("/codex model sol-max", "Restart selected Codex pane on Sol 6.2/max", "palette_codex_model_restart", "sol-6.2-max"),
    ModelCommand("/codex model sol-ultra", "Restart selected Codex pane on Sol 6.2/ultra", "palette_codex_model_restart", "sol-6.2-ultra"),
    ModelCommand("/codex model astra-xhigh", "Restart selected Codex pane on Astra 6.2/xhigh", "palette_codex_model_restart", "astra-6.2-xhigh"),
    ModelCommand("/codex model astra-max", "Restart selected Codex pane on Astra 6.2/max", "palette_codex_model_restart", "astra-6.2-max"),
    ModelCommand("/codex model astra-ultra", "Restart selected Codex pane on Astra 6.2/ultra", "palette_codex_model_restart", "astra-6.2-ultra"),
    ModelCommand("/codex model luna-high", "Restart selected Codex pane on Luna 6/high", "palette_codex_model_restart", "luna-6-high"),
    ModelCommand("/codex model legacy-5.5", "Restart selected Codex pane on Codex 5.5/xhigh", "palette_codex_model_restart", "legacy-5.5-xhigh"),
    ModelCommand("/codex model default terra-xhigh", "Save Terra 6.1/xhigh as global Codex default", "palette_codex_model_default", "terra-6.1-xhigh"),
    ModelCommand("/codex model default sol-medium", "Save Sol 6.2/medium as global Codex default", "palette_codex_model_default", "sol-6.2-medium"),
    ModelCommand("/codex model default sol-high", "Save Sol 6.2/high as global Codex default", "palette_codex_model_default", "sol-6.2-high"),
    ModelCommand("/codex model default luna-high", "Save Luna 6/high as global Codex default", "palette_codex_model_default", "luna-6-high"),
    ModelCommand("/codex model default sol-max", "Review Sol 6.2/max bounded elevation", "palette_codex_model_default", "sol-6.2-max"),
    ModelCommand("/codex model default sol-xhigh", "Save Sol 6.2/xhigh as global Codex default", "palette_codex_model_default", "sol-6.2-xhigh"),
)


def iter_restart_commands(app: Any, catalog: Any) -> Iterator[SystemCommand]:
    yield catalog.command(
        "/restart",
        "Restart the selected tmux Codex pane using its persisted model",
        app.palette_tmux_restart,
    )
    yield catalog.command("/codex restart", "Restart the selected tmux Codex pane", app.palette_tmux_restart)


def iter_model_commands(app: Any, catalog: Any) -> Iterator[SystemCommand]:
    for command in MODEL_COMMANDS:
        callback = getattr(app, command.callback_name)
        if command.profile_id:
            callback = partial(callback, command.profile_id)
        yield catalog.command(command.title, command.help, callback)


def palette_codex_model_native(app: Any) -> None:
    app.run_worker(
        app.open_native_codex_model_picker(),
        name="codex-native-model-picker",
        exclusive=True,
    )


def palette_codex_model_inspect(app: Any) -> None:
    app.run_worker(
        app.inspect_direct_codex_model_selection(),
        name="codex-model-inspect",
        exclusive=True,
    )


def palette_codex_model_adopt(app: Any) -> None:
    app.run_worker(
        app.adopt_direct_codex_model_selection(),
        name="codex-model-adopt",
        exclusive=True,
    )


def palette_codex_model_return(app: Any) -> None:
    agent_id = app.codex_model_target_agent_id()
    if agent_id is None:
        return
    preset = app.default_codex_model_preset_for_agent(agent_id)
    app.set_codex_model_preset_selector(preset)
    app.run_worker(
        app.request_codex_model_restart(agent_id, preset),
        name=f"codex-model-return-{slugify(agent_id)}",
        exclusive=True,
    )


__all__ = (
    "MODEL_COMMANDS",
    "ModelCommand",
    "iter_model_commands",
    "palette_codex_model_adopt",
    "palette_codex_model_inspect",
    "palette_codex_model_native",
    "palette_codex_model_return",
)
