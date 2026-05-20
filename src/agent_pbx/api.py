from __future__ import annotations

from collections.abc import Callable

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, status

from .auth import require_token
from .config import ServerConfig
from .pairing import PairRequest, PairResponse, issue_pairing_token
from .security import generate_pairing_code
from .store import Store


def create_app(config: ServerConfig | None = None) -> FastAPI:
    app = FastAPI(title="Agent PBX", version="0.1.0")
    app.state.config = config or ServerConfig()
    app.state.store = Store(app.state.config.db_path)
    app.state.store.init()

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx", "version": "0.1.0"}

    @app.get("/v1/auth/check", dependencies=[Depends(require_token)])
    async def auth_check() -> dict[str, object]:
        return {"ok": True}

    return app


def create_token_helper_app(
    store: Store,
    *,
    pairing_code: str | None = None,
    ttl_seconds: int = 120,
    on_issued: Callable[[], None] | None = None,
) -> tuple[FastAPI, str]:
    store.init()
    code = pairing_code or generate_pairing_code()
    store.create_pairing_code(code, ttl_seconds=ttl_seconds)
    app = FastAPI(title="Agent PBX Token Helper", version="0.1.0")
    app.state.store = store

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "service": "agent-pbx-token-helper"}

    @app.post("/pair", response_model=PairResponse)
    async def pair(
        request: PairRequest, background_tasks: BackgroundTasks
    ) -> PairResponse:
        response = issue_pairing_token(store, request)
        if response is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid or expired pairing code",
            )
        if on_issued is not None:
            background_tasks.add_task(on_issued)
        return response

    return app, code
