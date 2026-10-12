"""Bootstrap for _parallel_watchdog tests (D19-A, _roman_transport pattern).

The A0 framework root goes on ``sys.path`` before pytest imports the test
modules — the tests import framework classes (ParallelJob, UserMessage,
Extension) exactly the way the live agent process does.

Determinism: no network, no sleep; the watchdog.py module is loaded via
importlib the same way the framework loader does it (no sys.modules
registration) — see tests/test_parallel_watchdog.py. The
``AgentContext._contexts`` registry stays untouched (TH-D-08).
"""

import sys

_A0_ROOT = "/a0"

if _A0_ROOT not in sys.path:
    sys.path.insert(0, _A0_ROOT)
