from __future__ import annotations

import random
from pathlib import Path

import pytest

from agent_pbx.cli import build_parser
from agent_pbx.debug_smoke import (
    DEBUG_SMOKE_AGENT_IDS,
    SUN_TZU_QUOTES,
    DebugSmokeConfig,
    emit_debug_smoke_batch,
    run_debug_smoke_reports,
)
from agent_pbx.store import Store


class FixedRandom(random.Random):
    def randint(self, a: int, b: int) -> int:
        return a

    def choice(self, seq):  # type: ignore[no-untyped-def]
        return seq[0]

    def uniform(self, a: float, b: float) -> float:
        return a


def test_debug_smoke_batch_registers_agents_and_reports(tmp_path: Path) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()

    reports = emit_debug_smoke_batch(store, project="demo", rng=random.Random(1))

    agents = store.list_agents()
    events = store.list_events()
    assert {agent["agent_id"] for agent in agents} == set(DEBUG_SMOKE_AGENT_IDS)
    assert 1 <= len(reports) <= 2
    assert all(report["agent_id"] in DEBUG_SMOKE_AGENT_IDS for report in reports)
    assert all(report["summary"] in SUN_TZU_QUOTES for report in reports)
    assert [event["type"] for event in events[:3]] == [
        "agent_registered",
        "agent_registered",
        "agent_registered",
    ]
    assert all(event["type"] == "report_created" for event in events[3:])


@pytest.mark.asyncio
async def test_debug_smoke_runner_emits_initial_and_bounded_batches(
    tmp_path: Path,
) -> None:
    store = Store(tmp_path / "pbx.sqlite")
    store.init()
    now = 0.0
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        nonlocal now
        sleeps.append(delay)
        now += delay

    await run_debug_smoke_reports(
        store,
        DebugSmokeConfig(
            project="demo",
            duration_seconds=61.0,
            min_interval_seconds=30.0,
            max_interval_seconds=60.0,
        ),
        rng=FixedRandom(),
        sleep=fake_sleep,
        clock=lambda: now,
    )

    reports = store.list_reports(limit=100)
    assert len(reports) == 3
    assert sleeps == [30.0, 30.0, 1.0]


def test_serve_parser_accepts_debug_smoke() -> None:
    args = build_parser().parse_args(["serve", "--debug-smoke"])

    assert args.debug_smoke is True
