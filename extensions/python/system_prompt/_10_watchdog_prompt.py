"""[D19-A] system_prompt extension: watchdog-first async dispatch rule.

Appends a short English section to the system prompt of every agent running
the `_parallel_watchdog` plugin, so agents stop manually waiting/polling
for asynchronously dispatched subordinate jobs (``parallel`` tool,
``wait: false``): the watchdog wakes the parent at terminal state and the
agent collects the result on the wake/[EXTRAS] notice.

Prompt text lives in the editable file ``prompts/agent.system.watchdog.md``
and is loaded via ``self.agent.read_prompt(...)`` — the same resolution the
framework uses for plugin-local prompt files (the _promptinclude pattern).
An inline English fallback constant covers the case where the file cannot
be read: hooks MUST NEVER raise (the _roman_transport pattern contract).

Note: ``from __future__ import annotations`` is FORBIDDEN here — the framework
loader does not register this module in ``sys.modules`` (incident 2026-10-03,
commit 622a0fa _roman_transport).
"""

from helpers.extension import Extension as A0Extension
from helpers.print_style import PrintStyle

PROMPT_FILE = "agent.system.watchdog.md"

FALLBACK_PROMPT = """# Watchdog-first async dispatch (parallel jobs)

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
"""


class Extension(A0Extension):
    """system_prompt hook: append the watchdog-first section (never raises)."""

    async def execute(self, system_prompt: list[str] = [], **kwargs):
        """Append the watchdog-first async dispatch section to system_prompt."""
        if not self.agent:
            return

        try:
            prompt = self.agent.read_prompt(PROMPT_FILE) or FALLBACK_PROMPT
            if not prompt or not prompt.strip():
                prompt = FALLBACK_PROMPT
            system_prompt.append(prompt.rstrip("\n"))
        except Exception as exc:  # hooks MUST NEVER raise
            PrintStyle.error(f"parallel-watchdog prompt: {exc}")
            try:
                system_prompt.append(FALLBACK_PROMPT.rstrip("\n"))
            except Exception:  # noqa: BLE001 - last-resort silence
                pass
