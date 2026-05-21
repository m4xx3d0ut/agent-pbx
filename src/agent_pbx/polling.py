from __future__ import annotations

import asyncio
from typing import Any

from .store import Store


MAX_LONG_POLL_SECONDS = 30.0
MAX_REPEAT_POLL_SECONDS = 300.0
MAX_POLL_INTERVAL_SECONDS = 60.0


async def poll_commands(
    store: Store,
    agent_id: str,
    *,
    wait_seconds: float,
    limit: int,
    max_wait_seconds: float | None = None,
    interval_seconds: float = 5.0,
) -> list[dict[str, Any]]:
    per_wait = min(max(wait_seconds, 0.0), MAX_LONG_POLL_SECONDS)
    total_wait = (
        per_wait
        if max_wait_seconds is None
        else min(max(max_wait_seconds, 0.0), MAX_REPEAT_POLL_SECONDS)
    )
    interval = min(max(interval_seconds, 0.0), MAX_POLL_INTERVAL_SECONDS)
    safe_limit = min(max(limit, 1), 50)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + total_wait

    while True:
        commands = await _poll_once(
            store,
            agent_id,
            limit=safe_limit,
            wait_seconds=per_wait,
            deadline=deadline,
        )
        if commands or loop.time() >= deadline:
            return commands
        if interval:
            await asyncio.sleep(min(interval, max(0.0, deadline - loop.time())))


async def _poll_once(
    store: Store,
    agent_id: str,
    *,
    limit: int,
    wait_seconds: float,
    deadline: float,
) -> list[dict[str, Any]]:
    loop = asyncio.get_running_loop()
    cycle_deadline = min(loop.time() + wait_seconds, deadline)
    while True:
        commands = store.claim_commands(agent_id, limit=limit)
        if commands:
            return commands
        now = loop.time()
        if wait_seconds <= 0 or now >= cycle_deadline or now >= deadline:
            return []
        await asyncio.sleep(min(0.5, max(0.0, cycle_deadline - now)))
