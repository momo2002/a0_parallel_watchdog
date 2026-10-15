# Watchdog-first async dispatch (parallel jobs)

- After dispatching subordinate/background jobs asynchronously (e.g. the
  `parallel` tool with `wait: false`), do NOT run manual await/poll cycles
  or busy-wait for results — end your turn and stay idle.
- The `_parallel_watchdog` plugin monitors running jobs and wakes this
  agent when a job reaches a terminal state (success, error, timeout,
  cancelled) — no manual vigilance is needed.
- collect results when the wake notice or the `[EXTRAS]` reminder arrives,
  or when the task naturally returns in conversation.
- If a single extra poll is genuinely needed after a wake, space it out
  reasonably — never loop in tight polling.
- Never delete a subordinate's working tree after a crash or failure:
  inspect `git log` and `find` first (one diagnostic pass is enough)
  before deciding on cleanup or resume.

### Continue subordinate threads with `parallel`

`parallel` is the **preferred** way to dispatch and continue subordinate work: only `parallel`
jobs are registered in the parent job registry, which is the **only** thing the watchdog
monitors. A direct `call_subordinate` leaves no record, so a crash or hang goes unnoticed.

**Preferred path:** start and continue subordinate threads through `parallel` so the watchdog
can watch them. Dispatch the first task with `reset: true`; continue the same child thread
by passing its `context_id` with `reset: false`:

```
parallel(tool_calls=[call_subordinate(message="<follow-up>", context_id="<child id>", reset=false)])
```

Get the child `context_id` from the previous result (`job.worker_context_id` in parallel mode,
or `additional.context_id` from a direct call). Never pass `reset: true` together with a
`context_id` - it discards the child thread.
