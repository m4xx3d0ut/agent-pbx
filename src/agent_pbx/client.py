from __future__ import annotations

from typing import Any

import httpx


def auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


async def post_json(
    client: httpx.AsyncClient,
    path: str,
    payload: dict[str, Any],
    token: str | None = None,
) -> dict[str, Any]:
    response = await client.post(path, json=payload, headers=auth_headers(token))
    response.raise_for_status()
    return response.json()


async def get_json(
    client: httpx.AsyncClient, path: str, token: str | None = None
) -> Any:
    response = await client.get(path, headers=auth_headers(token))
    response.raise_for_status()
    return response.json()
