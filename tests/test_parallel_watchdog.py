"""Testy jednostkowe _parallel_watchdog (D19-A) — faza RED (TDD).

Oracle wprost z briefu D19-A + erraty po review in vivo (2026-10-11):
- tick skanuje ``context.data['_parallel_jobs']`` każdego kontekstu;
- job w stanie terminalnym bez flagi ``wake_reported`` -> ``communicate`` raz:
  - ``error``/``timeout``/``cancelled``: job_id, błąd, instrukcja wznowienia
    ``call_subordinate context_id=... reset:false`` po inspekcji
    ``git log``/``find`` w drzewie workera;
  - ``success`` (errata): instrukcja odbioru wyniku ``parallel await/collect
    job_ids=[<id>]`` (raport w result), BEZ instrukcji wznowienia;
- marker ``wake_reported`` = jedno budzenie per incydent (idempotentność);
- brak ``_parallel_jobs`` / pusty słownik / śmieciowe dane = noop;
- brak wyjątków, gdy kontekst parenta nie istnieje (fallback: communicate
  na własny, skanowany kontekst — brief D19-A);
- ``Extension.execute`` nigdy nie rzuca (kontrakt żywej pętli job_loop).

Deterministyczność: stuby w pamięci, zero sieci, zero sleep, zero
dotykania rejestru ``AgentContext._contexts`` (TH-D-08). Moduł
``watchdog`` ładowany importlib z pliku — dokładnie jak
``helpers.modules.import_module`` w żywym procesie (bez rejestracji w
``sys.modules``, bez kolizji z PyPI ``watchdog``).
"""

import importlib.util
import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent import UserMessage
from helpers.extension import Extension as A0Extension
from helpers.parallel_tools import PARALLEL_JOBS_KEY, JobState, ParallelJob

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WATCHDOG_PATH = _REPO_ROOT / "extensions" / "python" / "job_loop" / "watchdog.py"

_SPEC = importlib.util.spec_from_file_location(
    "parallel_watchdog_ext", _WATCHDOG_PATH
)
assert _SPEC is not None and _SPEC.loader is not None
WATCHDOG: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(WATCHDOG)


@dataclass
class StubContext:
    """Fałszywy AgentContext: dane + rejestrowane wywołania communicate."""

    id: str
    data: dict[str, Any] = field(default_factory=dict)
    communicate_calls: list[Any] = field(default_factory=list)
    communicate_errors: list[Exception] = field(default_factory=list)

    def get_data(self, key: str, recursive: bool = True) -> Any:
        return self.data.get(key, None)

    def set_data(self, key: str, value: Any, recursive: bool = True) -> None:
        self.data[key] = value

    def communicate(self, msg: Any, broadcast_level: int = 1) -> None:
        if self.communicate_errors:
            raise self.communicate_errors.pop(0)
        self.communicate_calls.append(msg)
        return None


def make_job(
    job_id: str = "callsubordin-abcd0001",
    state: JobState = "error",
    error: str | None = "RuntimeError: simulated API failure",
    worker_context_id: str | None = "workerctx1",
    parent_context_id: str = "parent1",
) -> ParallelJob:
    """Deterministyczny ParallelJob (dane syntetyczne, TH-D-08)."""
    return ParallelJob(
        id=job_id,
        parent_context_id=parent_context_id,
        index=0,
        tool_name="call_subordinate",
        tool_args={"message": "test"},
        kind="subordinate",
        state=state,
        error=error,
        worker_context_id=worker_context_id,
    )


def context_with(
    jobs: dict[str, Any] | None, context_id: str = "parent1"
) -> StubContext:
    ctx = StubContext(id=context_id)
    if jobs is not None:
        ctx.data[PARALLEL_JOBS_KEY] = jobs
    return ctx


def registry_of(*contexts: StubContext) -> dict[str, StubContext]:
    return {ctx.id: ctx for ctx in contexts}


async def test_tc_d19a_01_error_job_wakes_parent_once_with_resume_instructions():
    """TC-01: job error bez markera -> communicate raz, treść wg briefu."""
    job = make_job()
    parent = context_with({"j1": job})

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert len(parent.communicate_calls) == 1
    msg = parent.communicate_calls[0]
    assert isinstance(msg, UserMessage)
    text = msg.message
    assert job.id in text
    assert "zakończony błędem" in text
    assert "RuntimeError: simulated API failure" in text
    assert "call_subordinate" in text
    assert "context_id=workerctx1" in text
    assert "reset:false" in text
    assert "git log" in text
    assert "find" in text
    assert getattr(job, "wake_reported", False) is True
    assert report.woken == 1
    assert report.already_reported == 0
    assert report.errors == 0


async def test_tc_d19a_02_marker_prevents_second_wake():
    """TC-02: idempotentność — jedno budzenie per incydent."""
    job = make_job()
    parent = context_with({"j1": job})
    get = registry_of(parent).get

    await WATCHDOG.tick(contexts=[parent], context_get=get)
    report2 = await WATCHDOG.tick(contexts=[parent], context_get=get)

    assert len(parent.communicate_calls) == 1
    assert report2.woken == 0
    assert report2.already_reported == 1
    assert report2.errors == 0


async def test_tc_d19a_03_missing_parallel_jobs_key_is_noop():
    """TC-03: brak klucza _parallel_jobs = noop, zero communicate."""
    parent = StubContext(id="parent1")  # brak danych

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.scanned_contexts == 1
    assert report.woken == 0


async def test_tc_d19a_04_empty_jobs_dict_is_noop():
    """TC-04: pusty słownik jobów = noop."""
    parent = context_with({})

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.woken == 0
    assert report.errors == 0


async def test_tc_d19a_05_non_dict_jobs_value_is_noop():
    """TC-05: śmieciowa wartość pod _parallel_jobs = noop bez wyjątku."""
    parent = context_with("garbage-not-a-dict")  # type: ignore[arg-type]

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.woken == 0
    assert report.errors == 0


async def test_tc_d19a_06_timeout_state_wakes_parent():
    """TC-06: stan timeout tak samo budzi (jest ukryty z [EXTRAS])."""
    job = make_job(state="timeout", error="await timeout", parent_context_id="p1")
    parent = context_with({"j1": job}, context_id="p1")

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert len(parent.communicate_calls) == 1
    text = parent.communicate_calls[0].message
    assert "timeout" in text
    assert job.id in text
    assert report.woken == 1


async def test_tc_d19a_07_cancelled_state_wakes_parent():
    """TC-07: stan cancelled tak samo budzi (ukryty z [EXTRAS])."""
    job = make_job(state="cancelled", error="killed", parent_context_id="p1")
    parent = context_with({"j1": job}, context_id="p1")

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert len(parent.communicate_calls) == 1
    text = parent.communicate_calls[0].message
    assert "cancelled" in text
    assert report.woken == 1


async def test_tc_d19a_08_pending_running_do_not_wake():
    """TC-08 (errata): pending/running nie budzą — job jeszcze trwa."""
    jobs = {
        "pend": make_job(job_id="job-pend", state="pending", error=None),
        "run": make_job(job_id="job-run", state="running", error=None),
    }
    parent = context_with(jobs)

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.woken == 0
    assert report.already_reported == 0


async def test_tc_d19a_09_missing_parent_context_falls_back_to_own_context():
    """TC-09: kontekst parenta nie istnieje -> brak wyjątku, communicate
    na własny (skanowany) kontekst — odporność wg briefu D19-A."""
    job = make_job(parent_context_id="ghost")
    parent = context_with({"j1": job})  # rejestr żyje w parent1

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=lambda cid: None
    )

    assert len(parent.communicate_calls) == 1
    assert getattr(job, "wake_reported", False) is True
    assert report.woken == 1
    assert report.errors == 0


async def test_tc_d19a_10_failed_communicate_retried_next_tick():
    """TC-10: communicate rzuca -> tick nie rzuca, brak markera, retry
    w następnym ticku (marker dopiero po udanym deliverze)."""
    job = make_job()
    parent = context_with({"j1": job})
    get = registry_of(parent).get

    parent.communicate_errors.append(RuntimeError("communicate boom"))
    report1 = await WATCHDOG.tick(contexts=[parent], context_get=get)

    assert report1.errors == 1
    assert report1.woken == 0
    assert getattr(job, "wake_reported", False) is False

    report2 = await WATCHDOG.tick(contexts=[parent], context_get=get)

    assert report2.errors == 0
    assert report2.woken == 1
    assert len(parent.communicate_calls) == 1
    assert getattr(job, "wake_reported", False) is True


async def test_tc_d19a_11_multiple_contexts_all_woken():
    """TC-11: każdy kontekst z padniętym jobem dostaje swoje budzenie."""
    job_a = make_job(job_id="job-a", parent_context_id="ctx-a")
    job_b = make_job(job_id="job-b", parent_context_id="ctx-b")
    parent_a = context_with({"a": job_a}, context_id="ctx-a")
    parent_b = context_with({"b": job_b}, context_id="ctx-b")

    report = await WATCHDOG.tick(
        contexts=[parent_a, parent_b],
        context_get=registry_of(parent_a, parent_b).get,
    )

    assert len(parent_a.communicate_calls) == 1
    assert len(parent_b.communicate_calls) == 1
    assert report.woken == 2


async def test_tc_d19a_12_wake_cap_defers_rest_to_next_tick():
    """TC-12: cap budzeń na tick (TH-C-04) — reszta w następnym ticku."""
    jobs = {
        f"job-{i:02d}": make_job(job_id=f"job-{i:02d}") for i in range(10)
    }
    parent = context_with(jobs)
    get = registry_of(parent).get

    report1 = await WATCHDOG.tick(contexts=[parent], context_get=get)
    assert report1.woken == 8
    assert report1.deferred == 2
    assert len(parent.communicate_calls) == 8

    report2 = await WATCHDOG.tick(contexts=[parent], context_get=get)
    assert report2.woken == 2
    assert report2.deferred == 0
    assert len(parent.communicate_calls) == 10
    assert report2.already_reported == 8


async def test_tc_d19a_13_garbage_registry_entries_skipped():
    """TC-13: śmieciowe wpisy rejestru są pomijane bez wyjątku."""
    job = make_job()
    parent = context_with({"bad1": "garbage", "bad2": 123, "j1": job})

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert report.errors == 0
    assert report.woken == 1
    assert len(parent.communicate_calls) == 1


async def test_tc_d19a_14_long_error_is_truncated():
    """TC-14: długi błąd jest ucinany (TH-D-07/TH-S-01, flood)."""
    job = make_job(error="X" * 5000)
    parent = context_with({"j1": job})

    await WATCHDOG.tick(contexts=[parent], context_get=registry_of(parent).get)

    text = parent.communicate_calls[0].message
    assert len(text) < 2500
    assert "X" * 100 in text
    assert "ucięto" in text


async def test_tc_d19a_15_missing_error_gets_fallback_text():
    """TC-15: brak szczegółów błędu -> tekst zastępczy, bez wyjątku."""
    job = make_job(error=None)
    parent = context_with({"j1": job})

    await WATCHDOG.tick(contexts=[parent], context_get=registry_of(parent).get)

    text = parent.communicate_calls[0].message
    assert "brak szczegółów" in text
    assert job.id in text


async def test_tc_d19a_16_extension_class_contract():
    """TC-16: klasa Extension dziedziczy po helpers.extension.Extension,
    execute jest korutyną (wymóg briefu D19-A)."""
    assert issubclass(WATCHDOG.Extension, A0Extension)
    assert inspect.iscoroutinefunction(WATCHDOG.Extension.execute)


async def test_tc_d19a_17_execute_delegates_and_never_raises(monkeypatch):
    """TC-17: execute woła tick i połyka jego wyjątki (kontrakt job_loop)."""
    calls: list[dict[str, Any]] = []

    async def fake_tick(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return None

    monkeypatch.setattr(WATCHDOG, "tick", fake_tick)
    ext = WATCHDOG.Extension(agent=None)
    await ext.execute()
    assert calls == [{}]

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("tick boom")

    monkeypatch.setattr(WATCHDOG, "tick", boom)
    await ext.execute()  # nie rzuca — żywa pętla musi przeżyć


async def test_tc_d19a_18_missing_worker_context_fallback_in_message():
    """TC-18: brak worker_context_id -> jawny fallback w instrukcji."""
    job = make_job(worker_context_id=None)
    parent = context_with({"j1": job})

    await WATCHDOG.tick(contexts=[parent], context_get=registry_of(parent).get)

    text = parent.communicate_calls[0].message
    assert "context_id=<brak>" in text
    assert "reset:false" in text
    assert "call_subordinate" in text


async def test_tc_d19a_19_default_seams_noop_on_empty_registry():
    """TC-19: domyślne seamy (AgentContext.all/get) — pusty rejestr = noop
    bez wyjątku (proces testowy nie ma żywych kontekstów A0)."""
    report = await WATCHDOG.tick()

    assert report.woken == 0
    assert report.errors == 0


async def test_tc_d19a_20_success_job_wakes_parent_with_collect_instructions():
    """TC-20 (errata D19-A): success budzi z instrukcją odbioru wyniku,
    BEZ instrukcji wznowienia; idempotentność jak dla pozostałych stanów."""
    job = make_job(job_id="job-ok", state="success", error=None)
    parent = context_with({"ok": job})
    get = registry_of(parent).get

    report = await WATCHDOG.tick(contexts=[parent], context_get=get)

    assert len(parent.communicate_calls) == 1
    text = parent.communicate_calls[0].message
    assert "zakończony sukcesem" in text
    assert "Odbierz wynik" in text
    assert "parallel await/collect" in text
    assert f"job_ids=[{job.id}]" in text
    assert "raport w result" in text
    # BEZ instrukcji wznowienia — wynik do odebrania, nie do poprawiania
    assert "call_subordinate" not in text
    assert "reset:false" not in text
    assert "git log" not in text
    assert getattr(job, "wake_reported", False) is True
    assert report.woken == 1

    report2 = await WATCHDOG.tick(contexts=[parent], context_get=get)
    assert len(parent.communicate_calls) == 1
    assert report2.woken == 0
    assert report2.already_reported == 1
