from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from typing import Mapping


COMPATIBILITY_API_VERSION = "agent-pbx.compatibility/v2"
POLLING_ENV = "AGENT_PBX_COMPAT_POLLING"
THREAD_ENV = "AGENT_PBX_TUI_COMPAT_THREAD"
TERMINAL_CAPTURE_ENV = "AGENT_PBX_TUI_COMPAT_TERMINAL_CAPTURE"
TRUE_VALUES = {"1", "true", "yes", "on", "y", "enabled"}
FALSE_VALUES = {"0", "false", "no", "off", "n", "disabled"}


def compatibility_flag(
    name: str,
    *,
    default: bool = True,
    environ: Mapping[str, str] | None = None,
) -> bool:
    value = (environ or os.environ).get(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


@dataclass(frozen=True, slots=True)
class CompatibilityPosture:
    polling: bool = True
    thread_tab: bool = True
    terminal_capture: bool = True
    native_tmux_default: bool = True
    event_stream_default: bool = True
    retention: str = "retained_through_v2_0"
    removal_policy: str = (
        "Removal requires measured parity, a migration path, published criteria, "
        "and a release after v2.0."
    )

    def public_dict(self) -> dict[str, object]:
        return {
            "api_version": COMPATIBILITY_API_VERSION,
            **asdict(self),
        }


def compatibility_posture(
    *,
    polling: bool | None = None,
    thread_tab: bool | None = None,
    terminal_capture: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> CompatibilityPosture:
    return CompatibilityPosture(
        polling=(
            compatibility_flag(POLLING_ENV, environ=environ)
            if polling is None
            else bool(polling)
        ),
        thread_tab=(
            compatibility_flag(THREAD_ENV, environ=environ)
            if thread_tab is None
            else bool(thread_tab)
        ),
        terminal_capture=(
            compatibility_flag(TERMINAL_CAPTURE_ENV, environ=environ)
            if terminal_capture is None
            else bool(terminal_capture)
        ),
    )
