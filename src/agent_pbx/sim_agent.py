from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .client import auth_headers, post_json


async def run_sim_agent(
    *,
    server: str,
    agent_id: str,
    project: str,
    token: str | None,
    once: bool = False,
    poll_wait: float = 2,
) -> None:
    async with httpx.AsyncClient(base_url=server, timeout=30) as client:
        await post_json(
            client,
            "/v1/agents/register",
            {
                "agent_id": agent_id,
                "project": project,
                "name": f"sim-{agent_id}",
                "metadata": {"simulated": True},
            },
            token,
        )
        report = await post_json(
            client,
            f"/v1/agents/{agent_id}/reports",
            {
                "project": project,
                "status": "done",
                "summary": f"{agent_id} completed simulated work",
                "detail": f"Full simulated response detail from {agent_id}.",
                "needs_input": False,
                "plan_options": [],
            },
            token,
        )
        print(f"reported {report['report_id']}")

        while True:
            commands = await _poll_commands(client, agent_id, token, poll_wait)
            for command in commands:
                await post_json(
                    client,
                    f"/v1/commands/{command['command_id']}/ack",
                    {"result": {"handled_by": agent_id, "ok": True}},
                    token,
                )
                print(f"acked {command['command_id']} {command['type']}")
            if once:
                return
            await asyncio.sleep(0.5)


async def _poll_commands(
    client: httpx.AsyncClient, agent_id: str, token: str | None, poll_wait: float
) -> list[dict[str, Any]]:
    response = await client.get(
        f"/v1/agents/{agent_id}/commands",
        params={"wait_seconds": poll_wait},
        headers=auth_headers(token),
    )
    response.raise_for_status()
    return response.json()
