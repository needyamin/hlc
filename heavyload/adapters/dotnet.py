""".NET runtime metrics (EventCounters / dotnet-counters).

Target app should expose JSON built from EventCounters:
  {"heapUsed": n, "heapTotal": n, "GCCount": n, "gc_pause_ms": n, "Threads": n}
"""

from .base import RemoteRuntimeAdapter


class DotNetAdapter(RemoteRuntimeAdapter):
    name = "dotnet"
    runtime_name = ".NET"
    note = "Generic endpoint adapter; an EventCounters collector is Phase 2."
