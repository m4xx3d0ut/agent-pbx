from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    db_path: Path = Path("state/agent-pbx.sqlite")
    token: str | None = None
    allow_insecure_lan: bool = False

    @property
    def lan_bound(self) -> bool:
        return self.host not in {"127.0.0.1", "localhost", "::1"}
