from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .schemas import AgentRegisterRequest, ReportCreateRequest
from .store import Store


logger = logging.getLogger("agent_pbx.debug_smoke")

DEBUG_SMOKE_AGENT_IDS = (
    "sun-tzu-smoke-1",
    "sun-tzu-smoke-2",
    "sun-tzu-smoke-3",
)
SUN_TZU_QUOTES = (
    "All warfare is based on deception.",
    "In the midst of chaos, there is also opportunity.",
    "Attack where he is unprepared.",
    "Appear where you are not expected.",
    "Opportunities multiply as they are seized.",
    "The line between disorder and order lies in logistics.",
    "Know yourself and you will win all battles.",
    "The wise warrior avoids the battle.",
)


@dataclass(frozen=True)
class DebugSmokeConfig:
    project: str = "agent-pbx"
    duration_seconds: float = 300.0
    min_interval_seconds: float = 30.0
    max_interval_seconds: float = 60.0


SleepFunc = Callable[[float], Awaitable[None]]
ClockFunc = Callable[[], float]


def ensure_debug_smoke_agents(store: Store, *, project: str) -> None:
    for agent_id in DEBUG_SMOKE_AGENT_IDS:
        if store.get_agent(agent_id) is not None:
            continue
        store.register_agent(
            AgentRegisterRequest(
                agent_id=agent_id,
                project=project,
                name=agent_id,
                metadata={"debug_smoke": True, "source": "agent-pbx"},
            )
        )
        store.append_event(
            "agent_registered",
            {"agent_id": agent_id, "project": project, "debug_smoke": True},
            agent_id,
        )


def emit_debug_smoke_batch(
    store: Store,
    *,
    project: str = "agent-pbx",
    rng: random.Random | None = None,
) -> list[dict[str, Any]]:
    randomizer = rng or random.Random()
    ensure_debug_smoke_agents(store, project=project)
    reports: list[dict[str, Any]] = []
    for _ in range(randomizer.randint(1, 2)):
        agent_id = randomizer.choice(DEBUG_SMOKE_AGENT_IDS)
        quote = randomizer.choice(SUN_TZU_QUOTES)
        report = store.create_report(
            agent_id,
            ReportCreateRequest(
                project=project,
                status="smoke",
                summary=quote,
                detail=(
                    f"{quote}\n\n"
                    f"Debug smoke report from {agent_id}. "
                    "Use this to verify TUI refresh, unseen markers, and alerts."
                ),
                needs_input=False,
                plan_options=[],
            ),
        )
        store.append_event(
            "report_created",
            {
                "report_id": report["report_id"],
                "agent_id": agent_id,
                "status": "smoke",
                "summary": quote,
                "needs_input": False,
                "debug_smoke": True,
            },
            report["report_id"],
        )
        reports.append(report)
    return reports


async def run_debug_smoke_reports(
    store: Store,
    config: DebugSmokeConfig | None = None,
    *,
    rng: random.Random | None = None,
    sleep: SleepFunc = asyncio.sleep,
    clock: ClockFunc | None = None,
) -> None:
    smoke_config = config or DebugSmokeConfig()
    randomizer = rng or random.Random()
    active_clock = clock or asyncio.get_running_loop().time
    deadline = active_clock() + max(0.0, smoke_config.duration_seconds)
    min_interval = max(0.0, smoke_config.min_interval_seconds)
    max_interval = max(min_interval, smoke_config.max_interval_seconds)

    logger.info(
        "debug_smoke.start project=%s duration=%ss interval=%s-%ss agents=%s",
        smoke_config.project,
        smoke_config.duration_seconds,
        min_interval,
        max_interval,
        ",".join(DEBUG_SMOKE_AGENT_IDS),
    )
    reports = emit_debug_smoke_batch(
        store, project=smoke_config.project, rng=randomizer
    )
    logger.debug("debug_smoke.batch reports=%s", len(reports))

    while True:
        remaining = deadline - active_clock()
        if remaining <= 0:
            break
        interval = min(randomizer.uniform(min_interval, max_interval), remaining)
        await sleep(interval)
        if active_clock() >= deadline:
            break
        reports = emit_debug_smoke_batch(
            store, project=smoke_config.project, rng=randomizer
        )
        logger.debug("debug_smoke.batch reports=%s", len(reports))

    logger.info("debug_smoke.finish project=%s", smoke_config.project)
