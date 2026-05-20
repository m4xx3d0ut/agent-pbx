from __future__ import annotations

from pydantic import BaseModel, Field

from .security import generate_token
from .store import Store


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    label: str = Field(default="tui", max_length=80)


class PairResponse(BaseModel):
    token: str
    kind: str = "tui"


def issue_pairing_token(store: Store, request: PairRequest) -> PairResponse | None:
    if not store.consume_pairing_code(request.code):
        return None
    token = generate_token()
    store.add_token(token, kind="tui", label=request.label)
    return PairResponse(token=token)
