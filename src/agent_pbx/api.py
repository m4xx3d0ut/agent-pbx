from __future__ import annotations

from fastapi import FastAPI

from .config import ServerConfig


def create_app(config: ServerConfig | None = None) -> FastAPI:
    app = FastAPI(title="Agent PBX", version="0.1.0")
    app.state.config = config or ServerConfig()

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx", "version": "0.1.0"}

    return app
