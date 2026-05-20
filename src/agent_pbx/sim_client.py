from __future__ import annotations

from pathlib import Path

import httpx

from .client import get_json, post_json
from .transcript import TranscriptRecorder, emit


async def run_sim_client(
    *,
    server: str,
    token: str | None,
    agent_id: str | None = None,
    message: str | None = None,
    transcript: Path | None = None,
) -> None:
    recorder = TranscriptRecorder(transcript, actor="sim-client")
    recorder.record("start", server=server, agent_id=agent_id, has_message=bool(message))
    async with httpx.AsyncClient(base_url=server, timeout=30) as client:
        agents = await get_json(client, "/v1/agents", token)
        emit(f"agents: {len(agents)}", recorder, "listed_agents", agents=agents)
        for agent in agents:
            emit(
                f"- {agent['agent_id']} {agent['status']} {agent['project']}",
                recorder,
                "listed_agent",
                agent=agent,
            )

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
            emit(
                f"queued {command['command_id']} for {agent_id}",
                recorder,
                "queued_command",
                command=command,
            )
    recorder.record("complete")
