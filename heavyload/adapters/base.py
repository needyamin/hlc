"""Common runtime-adapter contract and the alias-based normaliser."""

from __future__ import annotations

import http.client
import json
import ssl

RUNTIME_FIELDS = ("heap_used_bytes", "heap_total_bytes", "gc_pause_ms",
                  "gc_count", "goroutines", "threads", "fds", "rss_bytes")

# Source-side aliases seen in the wild. Normalisation is deliberately tolerant:
# each runtime exposes slightly different names for the same concept.
ALIASES = {
    "heap_used_bytes": ("heapUsed", "heap_used", "heap_used_bytes",
                        "HeapAlloc", "heapAlloc", "used_heap", "HeapUsed"),
    "heap_total_bytes": ("heapTotal", "heap_total", "heap_total_bytes",
                         "total_heap", "HeapSys", "heapSys", "HeapTotal"),
    "gc_pause_ms": ("gc_pause_ms", "gcPauseMs", "PauseTotalMs",
                    "pause_ns", "last_gc_pause_ms"),
    "gc_count": ("gc_count", "gcCount", "NumGC", "collections",
                 "gen0_collections", "GCCount"),
    "goroutines": ("goroutines", "NumGoroutine", "num_goroutine"),
    "threads": ("threads", "Threads", "thread_count", "num_threads",
                "active_threads"),
    "fds": ("fds", "open_fds", "NumFD", "file_descriptors"),
    "rss_bytes": ("rss", "rss_bytes", "RSS", "VmRSS", "resident_set_bytes"),
}


def _flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = "%s.%s" % (prefix, k) if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.update(_flatten(v, key))
            else:
                out[key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.update(_flatten(v, "%s.%d" % (prefix, i)))
    return out


def normalize_runtime(raw):
    """Map arbitrary runtime JSON into ``metrics.runtime`` field names."""
    flat = _flatten(raw or {})
    lower = {str(k).lower(): v for k, v in flat.items()}
    short = {str(k).split(".")[-1].lower(): v for k, v in flat.items()}
    out = {k: None for k in RUNTIME_FIELDS}
    for field, names in ALIASES.items():
        for name in names:
            key = name.lower()
            val = lower.get(key, short.get(key))
            if val is None:
                continue
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            # pause_ns is nanoseconds; everything else is already ms/bytes/count
            if field == "gc_pause_ms" and name == "pause_ns":
                val /= 1e6
            out[field] = int(val) if val.is_integer() else round(val, 3)
            break
    return out


class RuntimeAdapter:
    """Interface every language adapter implements."""

    name = "base"
    runtime_name = "unknown"
    note = ""

    def __init__(self, endpoint="/__hlc/runtime", host="127.0.0.1",
                 port=8090, scheme="http", timeout=2.0, **_ignored):
        self.endpoint = endpoint or "/__hlc/runtime"
        self.host = host
        self.port = int(port)
        self.scheme = scheme
        self.timeout = float(timeout)

    def available(self):
        return False

    def probe(self):
        return {}

    def normalized(self, raw=None):
        return normalize_runtime(raw if raw is not None else self.probe())

    def fingerprint(self):
        return {"adapter": self.name, "runtime": self.runtime_name,
                "endpoint": self.endpoint, "note": self.note}


class RemoteRuntimeAdapter(RuntimeAdapter):
    """Fetch runtime internals from an app-side JSON endpoint.

    Apps opt in by exposing ``/__hlc/runtime`` (or a configured path). No
    third-party packages are needed on either side. Absence of the endpoint is
    a normal condition, not an error.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_error = ""

    def probe(self):
        conn = None
        try:
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(
                    self.host, self.port, timeout=self.timeout,
                    context=ssl._create_unverified_context())
            else:
                conn = http.client.HTTPConnection(
                    self.host, self.port, timeout=self.timeout)
            conn.request("GET", self.endpoint,
                         headers={"Accept": "application/json",
                                  "Connection": "close"})
            resp = conn.getresponse()
            body = resp.read()
            if not 200 <= resp.status < 300:
                self._last_error = "HTTP %d" % resp.status
                return {}
            data = json.loads(body.decode("utf-8", "replace"))
            self._last_error = ""
            return data if isinstance(data, dict) else {"items": data}
        except Exception as ex:
            self._last_error = type(ex).__name__
            return {}
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def available(self):
        return bool(self.probe())

    def fingerprint(self):
        d = super().fingerprint()
        d["last_error"] = self._last_error
        return d
