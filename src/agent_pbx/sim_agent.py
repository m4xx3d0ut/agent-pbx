from __future__ import annotations

import asyncio
from typing import Any
from pathlib import Path

import httpx

from .client import auth_headers, post_json
from .transcript import TranscriptRecorder, emit


async def run_sim_agent(
    *,
    server: str,
    agent_id: str,
    project: str,
    token: str | None,
    once: bool = False,
    poll_wait: float = 2,
    transcript: Path | None = None,
) -> None:
    recorder = TranscriptRecorder(transcript, actor=f"sim-agent:{agent_id}")
    recorder.record(
        "start", server=server, agent_id=agent_id, project=project, once=once
    )
    async with httpx.AsyncClient(base_url=server, timeout=30) as client:
        agent = await post_json(
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
        recorder.record("registered", agent=agent)
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
        emit(
            f"reported {report['report_id']}",
            recorder,
            "reported",
            report=report,
        )

        while True:
            commands = await _poll_commands(client, agent_id, token, poll_wait)
            recorder.record("polled", command_count=len(commands), commands=commands)
            for command in commands:
                ack = await post_json(
                    client,
                    f"/v1/commands/{command['command_id']}/ack",
                    {"result": {"handled_by": agent_id, "ok": True}},
                    token,
                )
                emit(
                    f"acked {command['command_id']} {command['type']}",
                    recorder,
                    "acked",
                    command=command,
                    ack=ack,
                )
            if once:
                recorder.record("complete")
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
