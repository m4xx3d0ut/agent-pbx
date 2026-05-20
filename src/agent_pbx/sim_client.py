from __future__ import annotations

import httpx

from .client import get_json, post_json


async def run_sim_client(
    *,
    server: str,
    token: str | None,
    agent_id: str | None = None,
    message: str | None = None,
) -> None:
    async with httpx.AsyncClient(base_url=server, timeout=30) as client:
        agents = await get_json(client, "/v1/agents", token)
        print(f"agents: {len(agents)}")
        for agent in agents:
            print(f"- {agent['agent_id']} {agent['status']} {agent['project']}")

        if agent_id and message:
            command = await post_json(
                client,
                "/v1/commands",
                {
                    "agent_id": agent_id,
                    "type": "send_input",
                    "payload": {"message": message},
                },
                token,
            )
            print(f"queued {command['command_id']} for {agent_id}")
