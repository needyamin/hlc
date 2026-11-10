"""Node.js / V8 runtime metrics.

Target app should expose a JSON endpoint (see targets/_schema.json) built from
``process.memoryUsage()`` + ``v8.getHeapStatistics()``:
  {"heapUsed": n, "heapTotal": n, "rss": n, "gc_pause_ms": n, "threads": n}
"""

from .base import RemoteRuntimeAdapter


class NodeV8Adapter(RemoteRuntimeAdapter):
    name = "node_v8"
    runtime_name = "Node.js / V8"
    note = "Generic endpoint adapter; a dedicated V8 agent is Phase 2."
