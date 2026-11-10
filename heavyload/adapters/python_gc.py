"""Python/CPython runtime metrics — the reference adapter.

Two flavours:
  * ``LocalPythonAdapter`` introspects THIS process (works everywhere, used by
    tests and as a worked example of the normalisation contract).
  * ``PythonRuntimeAdapter`` reads the same shape from the target app's
    ``/__hlc/runtime`` JSON endpoint.

Example endpoint payload (what an instrumented Python app should return):
  {"rss_bytes": 42123264, "gc_count": 12, "threads": 4, "fds": 9,
   "heap_used_bytes": 0, "gc_pause_ms": 1.3}
"""

from __future__ import annotations

import gc
import os
import sys
import threading

from .base import RemoteRuntimeAdapter, RuntimeAdapter, normalize_runtime


class LocalPythonAdapter(RuntimeAdapter):
    name = "local_python"
    runtime_name = "CPython"
    note = "Reference adapter: introspects the local interpreter."

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.runtime_version = sys.version.split()[0]

    def probe(self):
        out = {
            "python": sys.version.split()[0],
            "gc_count": sum(g.get("collections", 0)
                            for g in gc.get_stats()),
            "threads": threading.active_count(),
        }
        try:
            import resource  # POSIX only
            ru = resource.getrusage(resource.RUSAGE_SELF)
            # ru_maxrss is KiB on Linux, bytes on macOS
            out["rss_bytes"] = int(ru.ru_maxrss) * (1024 if sys.platform.startswith("linux") else 1)
        except Exception:
            out["rss_bytes"] = None
        try:
            out["fds"] = len(os.listdir("/proc/self/fd"))
        except Exception:
            out["fds"] = None
        # CPython's gc.get_stats() exposes counts, not pause durations. Leave
        # this None rather than inventing a number; an instrumented app that
        # measures gc.callbacks timings can supply the real value.
        out["gc_pause_ms"] = None
        return out

    def available(self):
        return True

    def normalized(self, raw=None):
        return normalize_runtime(raw if raw is not None else self.probe())

    def fingerprint(self):
        d = super().fingerprint()
        d["runtime_version"] = self.runtime_version
        return d


class PythonRuntimeAdapter(RemoteRuntimeAdapter):
    name = "python_gc"
    runtime_name = "CPython"
    note = "Requires the target app to expose /__hlc/runtime as JSON."
