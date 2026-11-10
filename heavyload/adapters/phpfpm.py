"""PHP-FPM runtime metrics (FPM status page).

Target app should expose JSON built from the FPM status page plus per-pool RSS:
  {"rss_bytes": n, "threads": n, "active_processes": n}
"""

from .base import RemoteRuntimeAdapter


class PhpFpmAdapter(RemoteRuntimeAdapter):
    name = "phpfpm"
    runtime_name = "PHP-FPM"
    note = "Generic endpoint adapter; an FPM-status collector is Phase 2."
