"""[D19-A] Watchdog terminalnych stanów jobów ``parallel`` (wake-on-error).

Seam frameworka: ``helpers.job_loop.scheduler_tick`` (~60 s) woła
``call_extensions_async("job_loop")``. To rozszerzenie skanuje wtedy
``context.data["_parallel_jobs"]`` każdego żywego kontekstu (parenta) i budzi
bezczynnego parenta, gdy job zakończył się stanem terminalnym (errata D19-A: KAŻDYM —
``success``/``error``/``timeout``/``cancelled``) bez flagi ``wake_reported`` —
bez tego parent dowiaduje się dopiero z [EXTRAS] przy ręcznym await
(~15 min); wynik ``success`` leży nieodebrany, dopóki człowiek nie napisze.

Budzenie: ``AgentContext.get(job.parent_context_id).communicate(UserMessage(...))``
z treścią: job_id, błąd, instrukcja wznowienia (``call_subordinate`` z
``context_id`` workera, ``reset:false``, po inspekcji ``git log``/``find``).
Marker ``wake_reported`` ustawiany DOPIERO po udanym ``communicate`` —
padnięcie dostarczenia oznacza retry w następnym ticku (idempotentność:
jeden incydent = jedno budzenie).

HARD CONTRACT (wzorzec _roman_transport): ``Extension.execute`` działa w żywym
procesie agenta — nigdy nie rzuca; każda awaria ticka jest logowana i wraca
w następnym ticku.

Uwaga: ``from __future__ import annotations`` jest ZAKAZANE — loader frameworka
(helpers.modules.import_module) nie rejestruje modułu w sys.modules, więc
odroczone adnotacje (PEP 563) wywalają ``@dataclass`` (incydent 2026-10-03,
commit 622a0fa _roman_transport).
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

# Errata D19-A (review in vivo 2026-10-11): budzimy przy KAZDYM stanie
# terminalnym — idle parent po dispatchu (wait:false) nie odbierze tez
# wyniku success, dopoki czlowiek nie napisze.
WAKE_STATES = set(TERMINAL_STATES)
MARKER_ATTR = "wake_reported"
MAX_WAKES_PER_TICK = 8
MAX_ERROR_CHARS = 1000

STATE_LABELS = {
    "error": "zakończony błędem",
    "timeout": "zakończony timeoutem (timeout)",
    "cancelled": "anulowany (cancelled)",
    "success": "zakończony sukcesem",
}


@dataclass
class TickReport:
    """Wynik jednego ticku (oracle dla testów; źródło logów)."""

    scanned_contexts: int = 0
    woken: int = 0
    already_reported: int = 0
    deferred: int = 0
    errors: int = 0


def _default_contexts() -> list[Any]:
    """Domyślny seam: wszystkie żywe konteksty (lazy import, lekki moduł)."""
    from agent import AgentContext

    return list(AgentContext.all())


def _default_context_get(context_id: str) -> Any:
    """Domyślny seam: lookup kontekstu parenta po id."""
    from agent import AgentContext

    return AgentContext.get(context_id)


def _clip_error(error: str) -> str:
    """Utnij nadmiernie długi błąd (TH-D-07/TH-S-01: flood, injection)."""
    if len(error) <= MAX_ERROR_CHARS:
        return error
    cut = len(error) - MAX_ERROR_CHARS
    return error[:MAX_ERROR_CHARS] + f" ...[ucięto {cut} znaków]"


def build_wake_message(job: ParallelJob) -> str:
    """Treść budzenia per stan (errata D19-A: success -> odbiór wyniku).

    ``error``/``timeout``/``cancelled``: job_id, błąd, instrukcja wznowienia
    (``call_subordinate`` z kontekstem workera, ``reset:false``). ``success``:
    instrukcja odbioru wyniku przez ``parallel await/collect`` — BEZ
    instrukcji wznowienia (nic nie jest zepsute, wynik czeka nieodebrany).
    """
    state = str(job.state)
    if state == "success":
        # treść 1:1 z erraty D19-A (bez tool_name — komunikat krótki,
        # narzędzie i tak widać w rekordzie joba)
        return (
            f"[parallel-watchdog] Job {job.id} zakończony sukcesem. "
            "Odbierz wynik przez "
            f"parallel await/collect job_ids=[{job.id}] (raport w result)."
        )
    label = STATE_LABELS.get(state, f"stan terminalny {state}")
    error = str(job.error or "").strip() or "brak szczegółów błędu"
    error = _clip_error(error)
    worker = str(job.worker_context_id or "").strip() or "<brak>"
    lines = [
        f"[parallel-watchdog] Job {job.id} ({job.tool_name}) {label}: {error}.",
        "Wznów przez call_subordinate "
        f"context_id={worker} reset:false, po inspekcji git log/find "
        "w drzewie workera (jeśli kontekst workera nie istnieje, nie wznawiaj).",
    ]
    return "\n".join(lines)


def _wake(context: Any, job: ParallelJob) -> None:
    """Wyślij komunikat budzący do kontekstu (UserMessage jak z API)."""
    from agent import UserMessage

    context.communicate(UserMessage(message=build_wake_message(job)))


async def tick(
    *,
    contexts: Iterable[Any] | None = None,
    context_get: Callable[[str], Any] | None = None,
) -> TickReport:
    """Jeden tick watchdoga (seamy wstrzykiwalne dla testów).

    Flow per kontekst: ``context.data['_parallel_jobs']`` (brak/pusty/
    nie-słownik = cicho dalej) -> job w stanie terminalnym (``WAKE_STATES``,
    errata D19-A) bez markera
    -> ``communicate`` na kontekście parenta; gdy parent nie istnieje,
    na własnym (skanowanym) kontekście wg briefu D19-A. Marker ustawiany
    po udanym dostarczeniu; budżet ``MAX_WAKES_PER_TICK`` chroni przed
    burzą wybudzeń (TH-C-04) — nadmiar w następnym ticku.
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
    """Punkt wejścia job_loop (nigdy nie rzuca — kontrakt żywej pętli)."""

    async def execute(self, **kwargs: Any) -> None:
        """Jeden tick watchdoga; każda awaria logowana, nie rzucana."""
        del kwargs  # job_loop nie przekazuje argumentów
        try:
            report = await tick()
            if report.woken or report.errors:
                PrintStyle.debug(
                    f"parallel-watchdog: woken={report.woken} "
                    f"already={report.already_reported} "
                    f"deferred={report.deferred} errors={report.errors}"
                )
        except Exception as exc:  # noqa: BLE001 — żywa pętla musi przeżyć
            PrintStyle.error(f"parallel-watchdog tick error: {exc}")
