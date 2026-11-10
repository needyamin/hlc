"""Server-side collectors.

Every collector follows the same contract (``collectors.base.Collector``) so a
future sidecar / Prometheus / APM collector can be dropped in without touching
``samplers.py``. The core stays standard-library-only; transport is either the
``docker`` CLI or plain ``http.client``.
"""

from .base import Collector, NullCollector  # noqa: F401
from .cgroup import CgroupCollector, CG_COLLECTOR_PY  # noqa: F401
from .procfs import parse_proc_line, summarize_proc_series  # noqa: F401
from .docker_stats import parse_stats_line  # noqa: F401
from .health import probe_once  # noqa: F401
from .mysql import loop_script as mysql_loop_script  # noqa: F401

__all__ = [
    "Collector", "NullCollector", "CgroupCollector", "CG_COLLECTOR_PY",
    "parse_proc_line", "summarize_proc_series", "parse_stats_line",
    "probe_once", "mysql_loop_script",
]
