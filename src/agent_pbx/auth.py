from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .security import constant_time_equal
from .store import Store, TokenRecord


bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class ClientPrincipal:
    token_id: str | None
    token_hash: str | None
    role: str
    audience: str | None
    client_id: str | None
    scopes: tuple[str, ...]
    allowed_agent_ids: tuple[str, ...]
    record: TokenRecord | None = None

    def public_dict(self) -> dict[str, object]:
        return {
            "token_id": self.token_id,
            "role": self.role,
            "audience": self.audience,
            "client_id": self.client_id,
            "scopes": list(self.scopes),
            "allowed_agent_ids": list(self.allowed_agent_ids),
        }


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
    principal = authenticate_request(request, credentials, store)
    if principal is None:
        return None
    if principal.role == "observer" and not observer_request_allowed(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="observer token is not permitted for this endpoint",
        )
    if (
        principal.role == "controller"
        and principal.allowed_agent_ids
        and not scoped_controller_request_allowed(request)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="scoped controller must use Agent-scoped v2 endpoints",
        )
    return principal.record


def require_client_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    store: Annotated[Store, Depends(get_store)],
) -> ClientPrincipal:
    principal = authenticate_request(request, credentials, store)
    if principal is None:
        return ClientPrincipal(None, None, "local", None, None, (), ())
    return principal


def require_controller(
    principal: Annotated[ClientPrincipal, Depends(require_client_principal)],
) -> ClientPrincipal:
    if principal.role not in {"local", "controller"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="controller role required",
        )
    return principal


def require_global_controller(
    principal: Annotated[ClientPrincipal, Depends(require_controller)],
) -> ClientPrincipal:
    if principal.allowed_agent_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="global controller role required",
        )
    return principal


def authenticate_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None,
    store: Store,
) -> ClientPrincipal | None:
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
        record = TokenRecord(
            token_hash="runtime-token",
            token_id="runtime",
            kind="runtime",
            role="controller",
            label="runtime",
            audience=config.remote_audience,
            client_id=None,
            scopes=("*",),
            allowed_agent_ids=(),
            created_at=0.0,
            expires_at=None,
            revoked_at=None,
            last_used_at=None,
        )
        return principal_from_record(record)

    record = store.verify_token(raw_token)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid bearer token",
        )
    if record.audience and record.audience != config.remote_audience:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="bearer token audience does not match this Agent PBX server",
        )
    store.touch_token(record.token_id)
    return principal_from_record(record)


def observer_request_allowed(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    if path == "/healthz" and method == "GET":
        return True
    if path == "/v2/events/snapshot" and method == "GET":
        return True
    if path == "/v2/remote/me" and method == "GET":
        return True
    if path.startswith("/v2/remote/clients/") and method in {"GET", "PUT"}:
        return True
    if path.startswith("/v2/remote/terminal/") and method == "GET":
        return True
    return False


def scoped_controller_request_allowed(request: Request) -> bool:
    path = request.url.path.rstrip("/") or "/"
    method = request.method.upper()
    if observer_request_allowed(request):
        return True
    if path.startswith("/v2/remote/control/") and method == "POST":
        return True
    return False


def principal_from_record(record: TokenRecord) -> ClientPrincipal:
    role = str(record.role or "").strip().lower()
    if role not in {"observer", "controller"}:
        role = "observer" if str(record.kind).lower() == "observer" else "controller"
    return ClientPrincipal(
        token_id=record.token_id,
        token_hash=record.token_hash,
        role=role,
        audience=record.audience,
        client_id=record.client_id,
        scopes=record.scopes,
        allowed_agent_ids=record.allowed_agent_ids,
        record=record,
    )
