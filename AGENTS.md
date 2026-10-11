# AGENTS.md — _parallel_watchdog

## Purpose

Wake-on-error dla jobów `parallel` (D19-A, wariant A: plugin-only). Gdy job async
(`wait:false`) kończy się stanem terminalnym `error`/`timeout`/`cancelled`,
bezczynny rodzic dowiaduje się z [EXTRAS] dopiero po ~15 min — ten plugin budzi go
w ticku `job_loop` (≤60 s) z instrukcją wznowienia.

## Ownership

- `extensions/python/job_loop/watchdog.py` — cała logika: `tick()` (seamy
  `contexts`/`context_get` wstrzykiwalne dla testów), `build_wake_message()`,
  `TickReport`, klasa `Extension` (wejście `job_loop`).
- `tests/test_parallel_watchdog.py` — 19 TC (TC-D19A-01..19), oracle wprost z briefu.
- `tests/conftest.py` — bootstrap `sys.path` (katalog główny A0).
- `plugin.yaml` — manifest (loader frameworka czyta **plugin.yaml**, nie plugin.json —
  `META_FILE_NAME` w `helpers/plugins.py`).
## Local Contracts

- Budzenie: `AgentContext.get(job.parent_context_id).communicate(UserMessage(...))`;
  gdy parent nie istnieje — communicate na własnym (skanowanym) kontekście (brief D19-A).
- Marker `wake_reported` ustawiany **dopiero po udanym communicate** — padnięcie
  dostarczenia = retry w następnym ticku; jeden incydent = jedno budzenie.
- `WAKE_STATES = TERMINAL_STATES - {success}` — sukces jest zbierany kanonicznie
  przez `parallel await`; `timeout`/`cancelled` są dodatkowo ukryte z [EXTRAS]
  (`build_parallel_jobs_extras` je wyklucza), więc ich budzenie jest obligatoryjne.
- `Extension.execute` **nigdy nie rzuca** — działa w żywej pętli `job_loop`
  (kontrakt wzorca _roman_transport).
- Budżet `MAX_WAKES_PER_TICK = 8` (TH-C-04 — burza wybudzeń); błąd truncowany do
  `MAX_ERROR_CHARS = 1000` (TH-D-07/TH-S-01 — flood/injection).
- **Bez** `from __future__ import annotations` — loader frameworka
  (`helpers.modules.import_module`) nie rejestruje modułu w `sys.modules`, odroczone
  adnotacje wywalają `@dataclass` (incydent 2026-10-03, commit 622a0fa _roman_transport).
- Zero zależności poza frameworkiem; zero zmian w `helpers/parallel_tools.py`,
  `helpers/job_loop.py`, `agent.py` i całym drzewie core.

## Work Guidance

- Zmiany zachowania zaczynają się od testu (TDD: RED → GREEN → REFACTOR).
- Nowe stany/branch'e: najpierw TC z oracle wprost z briefu/decyzji, potem kod.
- Kolejność ticka: scan → filtr stanu → filtr markera → budżet → deliver → marker.
- Każda zmiana `build_wake_message` musi odświeżyć asercje TC-01 (treść budzenia).

## Verification

Runtime weryfikacji: `/opt/venv-a0/bin/python` (framework), NIE `/opt/venv`.

```
cd /a0/usr/repos/parallel-watchdog
/opt/venv-a0/bin/python -m pytest tests/ -q                                       # 19/19
MYPYPATH=/a0 /opt/venv-a0/bin/python -m mypy --explicit-package-bases \n  extensions/python/job_loop/watchdog.py                                        # plugin-owned: 0
/opt/venv-a0/bin/python -m pycodestyle --max-line-length=120 extensions/ tests/  # 0
```

Uwaga mypy: importy frameworka powodują ~149 błędów w drzewie core A0
(`helpers/`, `plugins/`, `agent.py`) — poza zakresem tego pluginu; liczy się
liczba błędów **w plikach pluginu** (filtr `^extensions/|^tests/`), wzorzec
raportowania _roman_transport.

## Child DOX Index

Brak dzieci — plugin jednomodułowy.
