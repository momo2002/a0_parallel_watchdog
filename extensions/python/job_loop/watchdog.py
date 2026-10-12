"""[D19-A] Watchdog for terminal states of ``parallel`` jobs (wake-on-error).

Framework seam: ``helpers.job_loop.scheduler_tick`` (~60 s) calls
``call_extensions_async("job_loop")``. This extension then scans
``context.data["_parallel_jobs"]`` of every live (parent) context and wakes
the idle parent when a job has finished in a terminal state (D19-A errata:
ANY terminal state — ``success``/``error``/``timeout``/``cancelled``) without
the ``wake_reported`` flag — otherwise the parent only learns about it from
[EXTRAS] on a manual await (~15 min), and a ``success`` result sits
uncollected until a human writes.

Wake: ``AgentContext.get(job.parent_context_id).communicate(UserMessage(...))``
with content: job_id, error, resume instruction (``call_subordinate`` with the
worker's ``context_id``, ``reset:false``, after inspecting ``git log``/``find``
in the worker tree). The ``wake_reported`` marker is set ONLY after a
successful ``communicate`` — a failed deliver means a retry on the next tick
(idempotency: one incident = one wake).

HARD CONTRACT (the _roman_transport pattern): ``Extension.execute`` runs in
the LIVE agent process — it must NEVER raise; every tick failure is logged
and comes back on the next tick.

Note: ``from __future__ import annotations`` is FORBIDDEN here — the framework
loader (helpers.modules.import_module) does not register this module in
sys.modules, so PEP 563 postponed annotations crash ``@dataclass``
(incident 2026-10-03, commit 622a0fa _roman_transport).
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from helpers import extension
from helpers.parallel_tools import (
    PARALLEL_JOBS_KEY,
    TERMINAL_STATES,
    ParallelJob,
)
from helpers.print_style import PrintStyle

# D19-A errata (in-vivo review 2026-10-11): wake on EVERY terminal state —
# an idle parent (after a wait:false dispatch) would never collect even a
# success result until a human writes.
WAKE_STATES = set(TERMINAL_STATES)
MARKER_ATTR = "wake_reported"
MAX_WAKES_PER_TICK = 8
MAX_ERROR_CHARS = 1000

STATE_LABELS = {
    "error": "finished with error",
    "timeout": "timed out (timeout)",
    "cancelled": "cancelled",
    "success": "finished successfully",
}


@dataclass
class TickReport:
    """One tick outcome (oracle for tests; source for log lines)."""

    scanned_contexts: int = 0
    woken: int = 0
    already_reported: int = 0
    deferred: int = 0
    errors: int = 0


def _default_contexts() -> list[Any]:
    """Default seam: all live contexts (lazy import, lightweight module)."""
    from agent import AgentContext

    return list(AgentContext.all())


def _default_context_get(context_id: str) -> Any:
    """Default seam: parent context lookup by id."""
    from agent import AgentContext

    return AgentContext.get(context_id)


def _clip_error(error: str) -> str:
    """Truncate an overly long error (TH-D-07/TH-S-01: flood, injection)."""
    if len(error) <= MAX_ERROR_CHARS:
        return error
    cut = len(error) - MAX_ERROR_CHARS
    return error[:MAX_ERROR_CHARS] + f" ...[truncated {cut} chars]"


def build_wake_message(job: ParallelJob) -> str:
    """Wake content per state (D19-A errata: success -> collect the result).

    ``error``/``timeout``/``cancelled``: job_id, error, resume instruction
    (``call_subordinate`` with the worker context, ``reset:false``).
    ``success``: collect instruction via ``parallel await/collect`` — WITHOUT
    any resume instruction (nothing is broken, the result sits uncollected).
    """
    state = str(job.state)
    if state == "success":
        # content 1:1 from the D19-A errata (no tool_name — keep the message
        # short; the tool is visible in the job record anyway)
        return (
            f"[parallel-watchdog] Job {job.id} finished successfully. "
            "Collect the result via "
            f"parallel await/collect job_ids=[{job.id}] (report in result)."
        )
    label = STATE_LABELS.get(state, f"terminal state {state}")
    error = str(job.error or "").strip() or "no error details available"
    error = _clip_error(error)
    worker = str(job.worker_context_id or "").strip() or "<none>"
    lines = [
        f"[parallel-watchdog] Job {job.id} ({job.tool_name}) {label}: {error}.",
        "Resume via call_subordinate "
        f"context_id={worker} reset:false, after inspecting git log/find "
        "in the worker tree (if the worker context does not exist, do not resume).",
    ]
    return "\n".join(lines)


def _wake(context: Any, job: ParallelJob) -> None:
    """Send the wake message to the context (UserMessage like the API)."""
    from agent import UserMessage

    context.communicate(UserMessage(message=build_wake_message(job)))


async def tick(
    *,
    contexts: Iterable[Any] | None = None,
    context_get: Callable[[str], Any] | None = None,
) -> TickReport:
    """One watchdog tick (seams injectable for tests).

    Flow per context: ``context.data['_parallel_jobs']`` (missing/empty/
    non-dict = quietly skip) -> a job in a terminal state (``WAKE_STATES``,
    D19-A errata) without the marker
    -> ``communicate`` on the parent context; when the parent does not exist,
    on the scanned (own) context per brief D19-A. The marker is set only
    after a successful deliver; the ``MAX_WAKES_PER_TICK`` budget guards
    against wake storms (TH-C-04) — the rest goes to the next tick.
    """
    if contexts is None:
        contexts = _default_contexts()
    if context_get is None:
        context_get = _default_context_get

    report = TickReport()
    budget = MAX_WAKES_PER_TICK

    for context in contexts:
        report.scanned_contexts += 1
        try:
            jobs = context.get_data(PARALLEL_JOBS_KEY)
        except Exception:
            continue
        if not isinstance(jobs, dict):
            continue

        for job in list(jobs.values()):
            if not isinstance(job, ParallelJob):
                continue
            if job.state not in WAKE_STATES:
                continue
            if getattr(job, MARKER_ATTR, False):
                report.already_reported += 1
                continue
            if budget <= 0:
                report.deferred += 1
                continue

            try:
                target = context_get(str(job.parent_context_id))
            except Exception:
                report.errors += 1
                continue
            if target is None:
                target = context

            try:
                _wake(target, job)
            except Exception as exc:
                report.errors += 1
                PrintStyle.error(
                    f"parallel-watchdog: wake failed for {job.id}: {exc}"
                )
                continue

            setattr(job, MARKER_ATTR, True)
            report.woken += 1
            budget -= 1

    return report


class Extension(extension.Extension):
    """job_loop entry point (never raises — live-loop contract)."""

    async def execute(self, **kwargs: Any) -> None:
        """One watchdog tick; every failure is logged, never raised."""
        del kwargs  # job_loop passes no arguments
        try:
            report = await tick()
            if report.woken or report.errors:
                PrintStyle.debug(
                    f"parallel-watchdog: woken={report.woken} "
                    f"already={report.already_reported} "
                    f"deferred={report.deferred} errors={report.errors}"
                )
        except Exception as exc:  # noqa: BLE001 — the live loop must survive
            PrintStyle.error(f"parallel-watchdog tick error: {exc}")
