from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .security import constant_time_equal
from .store import Store, TokenRecord


bearer = HTTPBearer(auto_error=False)


def get_store(request: Request) -> Store:
    return request.app.state.store


def auth_required(request: Request, store: Store) -> bool:
    config = request.app.state.config
    if config.lan_bound and not config.allow_insecure_lan:
        return True
    if config.token:
        return True
    return store.has_tokens()


def require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    store: Annotated[Store, Depends(get_store)],
) -> TokenRecord | None:
    if not auth_required(request, store):
        return None
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="bearer token required",
        )

    raw_token = credentials.credentials
    config = request.app.state.config
    if config.token and constant_time_equal(raw_token, config.token):
        return TokenRecord(
            token_hash="runtime-token",
            kind="runtime",
            label="runtime",
            created_at=0.0,
            expires_at=None,
            revoked_at=None,
        )

    record = store.verify_token(raw_token)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid bearer token",
        )
    return record
