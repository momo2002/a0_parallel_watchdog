# AGENTS.md — _parallel_watchdog

## Purpose

Wake-on-error for `parallel` jobs (D19-A, variant A: plugin-only). When an
async job (`wait:false`) finishes in a terminal state — `error`/`timeout`/
`cancelled`, and after the D19-A errata also `success` — the idle parent only
learns about it from [EXTRAS] after ~15 min; this plugin wakes it on the
`job_loop` tick (≤60 s) with the state-appropriate instruction.

## Ownership

- `extensions/python/job_loop/watchdog.py` — all logic: `tick()` (the
  `contexts`/`context_get` seams are injectable for tests),
  `build_wake_message()`, `TickReport`, the `Extension` class (the `job_loop`
  entry point).
- `tests/test_parallel_watchdog.py` — 20 TCs (TC-D19A-01..20), the oracle
  straight from the brief and its errata.
- `tests/conftest.py` — `sys.path` bootstrap (A0 framework root).
- `plugin.yaml` — the manifest (the framework loader reads **plugin.yaml**,
  not plugin.json — `META_FILE_NAME` in `helpers/plugins.py`).

## Local Contracts

- Wake: `AgentContext.get(job.parent_context_id).communicate(UserMessage(...))`;
  when the parent does not exist — communicate on the scanned (own) context
  (brief D19-A).
- The `wake_reported` marker is set **only after a successful communicate** —
  a failed deliver means a retry on the next tick; one incident = one wake.
- `WAKE_STATES = set(TERMINAL_STATES)` (D19-A errata after the in-vivo review:
  wake on EVERY terminal state) — an idle parent after a `wait:false` dispatch
  has no turns, so without the watchdog even a `success` result sits
  uncollected until a human writes. Content PER STATE: `success` -> collect
  instruction (`parallel await/collect job_ids=[<id>]`), WITHOUT a resume
  instruction; `error`/`timeout`/`cancelled` -> resume instruction
  (`call_subordinate` with the worker context, `reset:false`, after inspecting
  `git log`/`find`).
- `Extension.execute` **never raises** — it runs in the live `job_loop`
  (the _roman_transport pattern contract).
- Budget `MAX_WAKES_PER_TICK = 8` (TH-C-04 — wake storms); errors truncated to
  `MAX_ERROR_CHARS = 1000` (TH-D-07/TH-S-01 — flood/injection).
- **No** `from __future__ import annotations` — the framework loader
  (`helpers.modules.import_module`) does not register the module in
  `sys.modules`; postponed annotations crash `@dataclass` (incident
  2026-10-03, commit 622a0fa _roman_transport).
- Zero dependencies beyond the framework; zero changes to
  `helpers/parallel_tools.py`, `helpers/job_loop.py`, `agent.py` or any other
  part of the core tree.

## Work Guidance

- Behavior changes start with a test (TDD: RED -> GREEN -> REFACTOR).
- New states/branches: first a TC with the oracle straight from the
  brief/decision, then the code.
- Tick order: scan -> state filter -> marker filter -> budget -> deliver ->
  marker.
- Every change to `build_wake_message` must refresh the assertions in TC-01
  (error/resume content) and TC-20 (success/collect content — no resume
  instruction).

## Verification

Verification runtime: `/opt/venv-a0/bin/python` (framework), NOT `/opt/venv`.

```
cd /a0/usr/repos/parallel-watchdog
/opt/venv-a0/bin/python -m pytest tests/ -q                                       # 20/20
MYPYPATH=/a0 /opt/venv-a0/bin/python -m mypy --explicit-package-bases \
  extensions/python/job_loop/watchdog.py                                        # plugin-owned: 0
/opt/venv-a0/bin/python -m pycodestyle --max-line-length=120 extensions/ tests/  # 0
```

mypy note: the framework imports produce ~149 errors in the core A0 tree
(`helpers/`, `plugins/`, `agent.py`) — out of scope for this plugin; what
counts is the number of errors **in the plugin's files** (filter
`^extensions/|^tests/`), the _roman_transport reporting pattern.

## Child DOX Index

No children — a single-module plugin.
