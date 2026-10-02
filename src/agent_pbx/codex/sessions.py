from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import RuntimeIdentity


@dataclass
class CodexSessionAssociation:
    identity: RuntimeIdentity
    verified_sources: set[str] = field(default_factory=set)
    app_server_thread_id: str | None = None

    def bind_app_server(self, thread_id: str, *, source: str) -> bool:
        expected = (self.identity.codex_session_id or "").strip()
        candidate = str(thread_id or "").strip()
        if not expected or not candidate or candidate != expected:
            return False
        self.app_server_thread_id = candidate
        self.verified_sources.add(source)
        return True

    @property
    def app_server_verified(self) -> bool:
        return bool(self.app_server_thread_id and self.verified_sources)

