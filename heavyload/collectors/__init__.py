"""Collector helpers.

Every collector follows the same contract (``collectors.base.Collector``) so a
future sidecar / Prometheus / APM collector can be dropped in without touching
``samplers.py``. The core stays standard-library-only. Container-based
collectors (cgroup / docker stats / MySQL) were removed in v2 along with the
Docker integration; ``procfs`` parsing is kept so archived runs still render.
"""

from .base import Collector, NullCollector  # noqa: F401
from .procfs import parse_proc_line, summarize_proc_series  # noqa: F401
from .health import probe_once  # noqa: F401

__all__ = [
    "Collector", "NullCollector",
    "parse_proc_line", "summarize_proc_series",
    "probe_once",
]
