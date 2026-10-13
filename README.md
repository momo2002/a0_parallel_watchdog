# a0_parallel_watchdog

**Parallel Watchdog** — wake idle parent agents when `parallel` jobs finish.

When you dispatch background jobs with `parallel` (`wait: false`) and then end your
turn, you go **idle**: your agent loop has no turns of its own, so nothing tells
you a job finished. The `[EXTRAS]` extras feed only appears on *your next turn* —
which may never come. This plugin closes that gap: it wakes the parent agent with
a short message whenever a parallel job reaches a terminal state.

## How it works

- Extension on the `job_loop` hook point (tick ~60 s) scans
  `context.data["_parallel_jobs"]` of every live context.
- A job in a **terminal state** (`success`, `error`, `timeout`, `cancelled`)\  without the `wake_reported` marker wakes its parent via
  `AgentContext.get(parent_context_id).communicate(UserMessage(...))`.
- Message per state:
  - `success` → "job finished, collect the result via `parallel` await/collect"
    (no resume instructions);
  - `error` / `timeout` / `cancelled` → includes the job id, the (truncated)
    error, and a resume hint (`call_subordinate context_id=<worker> reset:false`
    after inspecting the worker tree).
- `pending` / `running` jobs never wake (the job is still going).
- Idempotent: the `wake_reported` marker is set **only after a successful
  `communicate`** — a failed delivery retries on the next tick; one incident
  means one wake.
- Budget of 8 wakes per tick protects against wake storms; errors are
  truncated to 1000 chars against flooding/injection.
- `Extension.execute` never raises (live-loop contract) — every tick failure
  is logged and retried on the next tick.
- Note: jobs cancelled *on purpose* via `parallel action:"cancel"` are removed
  from the registry by the framework before the next tick, so they do not
  produce a wake — intentional.

## System prompt extension (v0.2.0)

A second extension, on the `system_prompt` hook point, appends a short
"watchdog-first async dispatch" section to the system prompt of every agent
running this plugin, so agents stop manually waiting/polling for async
subordinate jobs:

- after a `wait: false` dispatch, do NOT run manual await/poll cycles or
  busy-wait — the watchdog wakes the agent at terminal state;
- collect results on the wake / `[EXTRAS]` notice (a single spaced-out extra
  poll is allowed if genuinely needed);
- never delete a subordinate's working tree after a crash — inspect
  `git log` / `find` first.

The section text lives in the editable file `prompts/agent.system.watchdog.md`
(loaded via `agent.read_prompt`, the standard plugin-local prompt resolution);
an inline fallback constant covers a missing or unreadable file —
`Extension.execute` never raises.

## Install

Clone or copy this directory into your Agent Zero plugins folder:

```
usr/plugins/a0_parallel_watchdog/
```

then restart the WebUI — `job_loop` extensions load at process start. No settings,
no configuration.

## Files

```
plugin.yaml                                 # manifest (loader reads plugin.yaml)
extensions/python/job_loop/watchdog.py      # tick + Extension (wake-on-terminal-state)
extensions/python/system_prompt/_10_watchdog_prompt.py  # system_prompt extension
prompts/agent.system.watchdog.md            # editable prompt text (+ inline fallback)
tests/test_parallel_watchdog.py             # unit tests (in-memory stubs)
```

## Tests

Run with the Agent Zero framework runtime:

```
python -m pytest tests/ -q
```

## Requirements

- Agent Zero framework with `helpers/job_loop.py` scheduler ticks and
  `helpers.parallel_tools.PARALLEL_JOBS_KEY` / `ParallelJob` registry.

## License

MIT