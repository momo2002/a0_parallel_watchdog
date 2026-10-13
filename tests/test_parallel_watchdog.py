"""Unit tests for _parallel_watchdog (D19-A).

Oracle straight from brief D19-A plus the in-vivo review errata
(2026-10-11):
- the tick scans ``context.data['_parallel_jobs']`` of every context;
- a job in a terminal state without the ``wake_reported`` flag gets
  ``communicate`` exactly once:
  - ``error``/``timeout``/``cancelled``: job_id, error text, resume
    instruction ``call_subordinate context_id=... reset:false`` after
    inspecting ``git log``/``find`` in the worker tree;
  - ``success`` (errata): collect instruction ``parallel await/collect
    job_ids=[<id>]`` (report in result), WITHOUT any resume instruction;
- the ``wake_reported`` marker means one wake per incident (idempotency);
- missing ``_parallel_jobs`` / empty dict / garbage data = quiet noop;
- no exceptions when the parent context does not exist (fallback:
  communicate on the scanned (own) context — brief D19-A);
- ``Extension.execute`` never raises (live job_loop contract).

Determinism: in-memory stubs, no network, no sleep, and no touching of
the ``AgentContext._contexts`` registry (TH-D-08). The ``watchdog``
module is loaded via importlib from file — exactly the way
``helpers.modules.import_module`` does it in the live process (no
sys.modules registration, no collision with the PyPI ``watchdog``).
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
    """Fake AgentContext: data plus recorded communicate calls."""

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
    """Deterministic ParallelJob (synthetic data, TH-D-08)."""
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
    """TC-01: error job without marker -> one communicate, brief content."""
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
    assert "finished with error" in text
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
    """TC-02: idempotency — exactly one wake per incident."""
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
    """TC-03: missing _parallel_jobs key = noop, zero communicate calls."""
    parent = StubContext(id="parent1")  # no data at all

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.scanned_contexts == 1
    assert report.woken == 0


async def test_tc_d19a_04_empty_jobs_dict_is_noop():
    """TC-04: empty jobs dict = noop."""
    parent = context_with({})

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.woken == 0
    assert report.errors == 0


async def test_tc_d19a_05_non_dict_jobs_value_is_noop():
    """TC-05: garbage value under _parallel_jobs = noop, no exception."""
    parent = context_with("garbage-not-a-dict")  # type: ignore[arg-type]

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert parent.communicate_calls == []
    assert report.woken == 0
    assert report.errors == 0


async def test_tc_d19a_06_timeout_state_wakes_parent():
    """TC-06: the timeout state wakes as well (hidden from [EXTRAS])."""
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
    """TC-07: the cancelled state wakes as well (hidden from [EXTRAS])."""
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
    """TC-08 (errata): pending/running do not wake — job still running."""
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
    """TC-09: parent context missing -> no exception, communicate on the
    scanned (own) context — resilience per brief D19-A."""
    job = make_job(parent_context_id="ghost")
    parent = context_with({"j1": job})  # the registry lives in parent1

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=lambda cid: None
    )

    assert len(parent.communicate_calls) == 1
    assert getattr(job, "wake_reported", False) is True
    assert report.woken == 1
    assert report.errors == 0


async def test_tc_d19a_10_failed_communicate_retried_next_tick():
    """TC-10: communicate raises -> tick does not raise, no marker,
    retried next tick (marker only after a successful deliver)."""
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
    """TC-11: every context with a failed job gets its own wake."""
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
    """TC-12: per-tick wake cap (TH-C-04) — the rest next tick."""
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
    """TC-13: garbage registry entries are skipped without exception."""
    job = make_job()
    parent = context_with({"bad1": "garbage", "bad2": 123, "j1": job})

    report = await WATCHDOG.tick(
        contexts=[parent], context_get=registry_of(parent).get
    )

    assert report.errors == 0
    assert report.woken == 1
    assert len(parent.communicate_calls) == 1


async def test_tc_d19a_14_long_error_is_truncated():
    """TC-14: a long error is truncated (TH-D-07/TH-S-01, flood)."""
    job = make_job(error="X" * 5000)
    parent = context_with({"j1": job})

    await WATCHDOG.tick(contexts=[parent], context_get=registry_of(parent).get)

    text = parent.communicate_calls[0].message
    assert len(text) < 2500
    assert "X" * 100 in text
    assert "truncated" in text


async def test_tc_d19a_15_missing_error_gets_fallback_text():
    """TC-15: missing error details -> fallback text, no exception."""
    job = make_job(error=None)
    parent = context_with({"j1": job})

    await WATCHDOG.tick(contexts=[parent], context_get=registry_of(parent).get)

    text = parent.communicate_calls[0].message
    assert "no error details" in text
    assert job.id in text


async def test_tc_d19a_16_extension_class_contract():
    """TC-16: Extension subclasses helpers.extension.Extension and
    execute is a coroutine (brief D19-A requirement)."""
    assert issubclass(WATCHDOG.Extension, A0Extension)
    assert inspect.iscoroutinefunction(WATCHDOG.Extension.execute)


async def test_tc_d19a_17_execute_delegates_and_never_raises(monkeypatch):
    """TC-17: execute calls tick and swallows its exceptions (job_loop
    contract)."""
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
    await ext.execute()  # must not raise — the live loop has to survive


async def test_tc_d19a_18_missing_worker_context_fallback_in_message():
    """TC-18: missing worker_context_id -> explicit fallback in the
    resume instruction."""
    job = make_job(worker_context_id=None)
    parent = context_with({"j1": job})

    await WATCHDOG.tick(contexts=[parent], context_get=registry_of(parent).get)

    text = parent.communicate_calls[0].message
    assert "context_id=<none>" in text
    assert "reset:false" in text
    assert "call_subordinate" in text


async def test_tc_d19a_19_default_seams_noop_on_empty_registry():
    """TC-19: default seams (AgentContext.all/get) — empty registry =
    quiet noop (the test process has no live A0 contexts)."""
    report = await WATCHDOG.tick()

    assert report.woken == 0
    assert report.errors == 0


async def test_tc_d19a_20_success_job_wakes_parent_with_collect_instructions():
    """TC-20 (D19-A errata): success wakes with the collect instruction,
    WITHOUT any resume instruction; idempotency as for other states."""
    job = make_job(job_id="job-ok", state="success", error=None)
    parent = context_with({"ok": job})
    get = registry_of(parent).get

    report = await WATCHDOG.tick(contexts=[parent], context_get=get)

    assert len(parent.communicate_calls) == 1
    text = parent.communicate_calls[0].message
    assert "finished successfully" in text
    assert "Collect the result" in text
    assert "parallel await/collect" in text
    assert f"job_ids=[{job.id}]" in text
    assert "report in result" in text
    # NO resume instruction — the result is to be collected, not fixed
    assert "call_subordinate" not in text
    assert "reset:false" not in text
    assert "git log" not in text
    assert getattr(job, "wake_reported", False) is True
    assert report.woken == 1

    report2 = await WATCHDOG.tick(contexts=[parent], context_get=get)
    assert len(parent.communicate_calls) == 1
    assert report2.woken == 0
    assert report2.already_reported == 1

# ----------------------------------------------------------------------------
# system_prompt extension (_10_watchdog_prompt) — watchdog-first async dispatch
# ----------------------------------------------------------------------------

_PROMPT_PATH = (
    _REPO_ROOT / "extensions" / "python" / "system_prompt" / "_10_watchdog_prompt.py"
)

_PROMPT_SPEC = importlib.util.spec_from_file_location(
    "parallel_watchdog_prompt_ext", _PROMPT_PATH
)
assert _PROMPT_SPEC is not None and _PROMPT_SPEC.loader is not None
WATCHDOG_PROMPT: Any = importlib.util.module_from_spec(_PROMPT_SPEC)
_PROMPT_SPEC.loader.exec_module(WATCHDOG_PROMPT)


@dataclass
class StubAgent:
    """Fake Agent: configurable read_prompt, recorded no other state."""

    read_prompt_error: Exception | None = None
    read_prompt_result: str | None = None
    read_prompt_calls: list[str] = field(default_factory=list)

    def read_prompt(self, file: str, **kwargs: Any) -> str:
        self.read_prompt_calls.append(file)
        if self.read_prompt_error:
            raise self.read_prompt_error
        if self.read_prompt_result is not None:
            return self.read_prompt_result
        raise FileNotFoundError(f"no stub prompt for {file}")


async def test_tc_pw_01_extension_class_and_guard_contract():
    """TC-PW-01: Extension subclasses helpers.extension.Extension, execute
    is a coroutine, and a missing agent is a quiet no-op."""
    assert issubclass(WATCHDOG_PROMPT.Extension, A0Extension)
    assert inspect.iscoroutinefunction(WATCHDOG_PROMPT.Extension.execute)

    ext = WATCHDOG_PROMPT.Extension(agent=None)
    system_prompt: list[str] = []
    await ext.execute(system_prompt=system_prompt)
    assert system_prompt == []


async def test_tc_pw_02_appends_nonempty_section_with_read_prompt():
    """TC-PW-02: with a live agent the extension loads
    agent.system.watchdog.md via agent.read_prompt and appends a non-empty
    section to system_prompt."""
    agent = StubAgent(
        read_prompt_result="# Watchdog-first async dispatch\n\nStay idle after dispatch.\n"
    )
    ext = WATCHDOG_PROMPT.Extension(agent=agent)  # type: ignore[arg-type]
    system_prompt: list[str] = []

    await ext.execute(system_prompt=system_prompt)

    assert agent.read_prompt_calls == ["agent.system.watchdog.md"]
    assert len(system_prompt) == 1
    assert system_prompt[0].strip() != ""


async def test_tc_pw_03_section_contains_key_phrases():
    """TC-PW-03: the deployed prompt file contains the key rules: terminal
    state, collect results, no manual polling, no tree deletion after crash."""
    agent = StubAgent(
        read_prompt_result=(
            _REPO_ROOT / "prompts" / "agent.system.watchdog.md"
        ).read_text(encoding="utf-8")
    )
    ext = WATCHDOG_PROMPT.Extension(agent=agent)  # type: ignore[arg-type]
    system_prompt: list[str] = []

    await ext.execute(system_prompt=system_prompt)

    text = system_prompt[0]
    assert "terminal state" in text
    assert "collect" in text
    assert "poll" in text
    assert "git log" in text or "find" in text


async def test_tc_pw_04_read_prompt_failure_falls_back_and_never_raises():
    """TC-PW-04: a read_prompt failure never raises — the inline fallback
    constant is appended instead (hooks MUST NEVER raise)."""
    agent = StubAgent(read_prompt_error=RuntimeError("prompt fs boom"))
    ext = WATCHDOG_PROMPT.Extension(agent=agent)  # type: ignore[arg-type]
    system_prompt: list[str] = []

    await ext.execute(system_prompt=system_prompt)  # must not raise

    assert len(system_prompt) == 1
    assert system_prompt[0].strip() != ""
    assert "terminal state" in system_prompt[0]
