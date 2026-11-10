"""Go runtime metrics (expvar / runtime.MemStats).

Target app should expose JSON built from ``runtime.ReadMemStats`` +
``runtime.NumGoroutine`` + ``expvar``:
  {"HeapAlloc": n, "HeapSys": n, "NumGC": n, "NumGoroutine": n}
"""

from .base import RemoteRuntimeAdapter


class GoAdapter(RemoteRuntimeAdapter):
    name = "go"
    runtime_name = "Go"
    note = "Generic endpoint adapter; a /debug/pprof collector is Phase 2."
