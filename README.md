# _parallel_watchdog (D19-A)

Wake-on-error dla jobów `parallel`: gdy job async (`wait:false`) kończy się stanem
terminalnym `error`/`timeout`/`cancelled`, bezczynny rodzic dowiaduje się z
`[EXTRAS]` dopiero po ~15 min. Ten plugin budzi go w ≤60 s (tick `job_loop`).

## Jak działa
- Extension `job_loop` (seam `helpers.job_loop.scheduler_tick`, ~60 s) skanuje
  `context.data["_parallel_jobs"]` każdego żywego kontekstu.
- Job w stanie terminalnym (bez `success`) bez flagi `wake_reported` →
  `AgentContext.get(parent_context_id).communicate(UserMessage(...))` z treścią:
  job_id, błąd, instrukcja wznowienia (`call_subordinate context_id=<worker> reset:false`
  po inspekcji `git log`/`find`).
- Marker `wake_reported` ustawiany dopiero PO udanym `communicate` — padnięcie
  dostarczenia = retry w następnym ticku; jeden incydent = jedno budzenie.
- Budżet 8 budzeń/tick (TH-C-04); błąd truncowany do 1000 znaków (TH-D-07/S-01).
- Stany `success`/`pending`/`running` nie budzą (kanoniczny kanał zbierania).
- `Extension.execute` nigdy nie rzuca (kontrakt żywej pętli, wzorzec _roman_transport).

## Struktura
```
plugin.yaml                              # manifest (loader A0 czyta plugin.yaml, nie plugin.json)
extensions/python/job_loop/watchdog.py  # tick + Extension
tests/test_parallel_watchdog.py          # 19 TC (TC-D19A-01..19)
```

## Weryfikacja (runtime /opt/venv-a0)
```
cd /a0/usr/repos/parallel-watchdog
/opt/venv-a0/bin/python -m pytest tests/ -q                      # 19/19
MYPYPATH=/a0 /opt/venv-a0/bin/python -m mypy --explicit-package-bases extensions/python/job_loop/watchdog.py  # plugin-owned: 0
/opt/venv-a0/bin/python -m pycodestyle --max-line-length=120 extensions/ tests/  # 0
```

## Wdrożenie
DEV: `/a0/usr/repos/parallel-watchdog` → REPO: Gitea `agent0/_parallel_watchdog` →
LIVE: `/a0/usr/plugins/_parallel_watchdog` (wyłącznie `git pull`). Push po review Cezarego.
