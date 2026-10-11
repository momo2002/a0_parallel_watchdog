"""Bootstrap testów _parallel_watchdog (D19-A, wzorzec _roman_transport).

Katalog główny frameworka A0 trafia na ``sys.path``, zanim pytest zaimportuje
moduły testowe — test importuje klasy frameworka (ParallelJob, UserMessage,
Extension) dokładnie tak, jak żywy proces agenta.

Deterministyczność: zero sieci, zero sleep; moduł watchdog.py jest ładowany
przez importlib tak, jak robi to loader frameworka (bez rejestracji w
sys.modules) — patrz tests/test_parallel_watchdog.py. Rejestr
``AgentContext._contexts`` pozostaje nietknięty (TH-D-08).
"""

import sys

_A0_ROOT = "/a0"

if _A0_ROOT not in sys.path:
    sys.path.insert(0, _A0_ROOT)
