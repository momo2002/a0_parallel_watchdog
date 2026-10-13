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
