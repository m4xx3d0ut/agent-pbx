# Test execution and qualification

Agent PBX keeps bare pytest sequential so failures remain easy to reproduce:

```bash
python -m pytest
```

The bounded full runner distributes tests that do not hold the `serial` mark,
then runs the serial tail in the controller process:

```bash
scripts/test_full.sh
AGENT_PBX_TEST_WORKERS=4 scripts/test_full.sh
scripts/test_full.sh --serial
```

`scripts/test_full.sh --parallel-only` and `--serial-only` expose either lane
for diagnosis. Worker restarts are disabled so a worker crash fails the run
instead of silently retrying a test in a fresh process.

## Parallel-safety contract

Parallel tests must own all mutable resources they use. Prefer `tmp_path`,
worker-specific tmux sockets and names, ephemeral ports, and monkeypatched home
or configuration directories. Mark a test `serial` when it must touch the live
outer tmux server, a fixed port, a shared daemon PID file, shared user
configuration, or another host resource that cannot be isolated. Session-level
fixtures run once per xdist worker unless they implement explicit interprocess
coordination.

The outer-tmux conformance test is both opt-in through
`AGENT_PBX_TEST_OUTER_TMUX` and serial. Most real tmux tests that create unique
private sockets remain eligible for parallel execution. The retained-pane
respawn rollback test stays in the serial tail because a heavily loaded tmux
server can delay publishing `pane_dead_status` even after `pane_dead` changes.

## 2026-10-06 local qualification

The workstation baseline was 839 passed and 1 skipped in 664.51 seconds using
one process. After this change the suite contains 843 tests, with one opt-in
outer-tmux test in the serial lane.

| Workers | Scheduler | Result | Wall time | Decision |
|---:|---|---|---:|---|
| 4 | `worksteal` | 842 parallel-safe tests passed | 189.14 s | Qualified default |
| 8 | `worksteal` | Worker crashed in Textual/GC while Codex posture probing ran on another thread | 146.24 s to failure | Rejected |
| 12 | `worksteal` | Not run after the lower 8-worker count failed | — | Rejected pending root-cause work |

Four workers produced a 3.5x wall-time improvement over the prior sequential
baseline without retries. Eight workers are not an accepted configuration on
Python 3.10/Textual 0.89.1. Requalify the ceiling after a Python or Textual
runtime change rather than raising the default based only on available CPU or
memory.

CI uses two workers because hosted runners have fewer cores and less predictable
resource limits. Release qualification retains a complete sequential parity
run where the release process requires it.

## Documentation checks

Run the repository-local documentation gate with:

```bash
python scripts/check_docs.py
python -m pytest -q tests/test_docs.py tests/test_readme.py
```

The gate checks local Markdown links, required wiki version/review markers,
public-source hygiene, and the `--help` surface of every Agent PBX command path
shown in shell examples. It does not start, stop, attach, migrate, publish, or
otherwise mutate a live service. Package README tests separately enforce
PyPI-safe public image URLs and retained architecture source.
