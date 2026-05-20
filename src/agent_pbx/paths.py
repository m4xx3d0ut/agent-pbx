from __future__ import annotations

import os
from pathlib import Path


def default_state_root() -> Path:
    override = os.getenv("AGENT_PBX_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
    if xdg_data_home:
        return (Path(xdg_data_home).expanduser() / "agent-pbx").resolve()
    return (Path.home() / ".local" / "share" / "agent-pbx").resolve()
