"""JVM runtime metrics (JMX / jcmd / Spring Boot Actuator).

Target app should expose JSON built from JMX MemoryMXBean, GarbageCollectorMXBean
and ThreadMXBean:
  {"HeapUsed": n, "HeapTotal": n, "GCCount": n, "gc_pause_ms": n, "Threads": n}
"""

from .base import RemoteRuntimeAdapter


class JvmAdapter(RemoteRuntimeAdapter):
    name = "jvm"
    runtime_name = "JVM"
    note = "Generic endpoint adapter; a JMX/Actuator collector is Phase 2."
