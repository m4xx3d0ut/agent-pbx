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
    debug: bool = False
    debug_smoke: bool = False
    debug_smoke_duration_seconds: float = 300.0
    debug_smoke_min_interval_seconds: float = 30.0
    debug_smoke_max_interval_seconds: float = 60.0
    workerbee_bin: Path | None = None
    workerbee_timeout_seconds: float = 20.0
    workerbee_cache_seconds: float = 10.0

    @property
    def lan_bound(self) -> bool:
        return self.host not in {"127.0.0.1", "localhost", "::1"}
