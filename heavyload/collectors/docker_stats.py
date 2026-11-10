"""Parsing for ``docker stats --no-stream --format '{{json .}}'``.

Kept as a pure function so the poller stays thin and the parser is unit
testable. NOTE: docker stats reports decimal-MB *strings*; those values stay in
``stats_*`` fields and are never mixed with the canonical cgroup byte counters.
"""

from __future__ import annotations

import json

from ..util import host_free_gb, split_io, to_mib
from .base import Collector


def _float_pct(value):
    try:
        return float((value or "0").rstrip("%") or 0)
    except Exception:
        return 0.0


def parse_stats_line(line, *, container="", t=0.0, wall="", phase="",
                     extra=None):
    """Return one stats sample dict, or ``None`` for a non-matching line."""
    if not line or not line.strip().startswith("{"):
        return None
    try:
        d = json.loads(line)
    except Exception:
        return None
    name = d.get("Name", "")
    if container and name != container:
        return None
    rx, tx = split_io(d.get("NetIO", ""))
    bi, bo = split_io(d.get("BlockIO", ""))
    memu = (d.get("MemUsage") or "0 / 0").split("/")
    rec = {
        "t": t, "wall": wall, "name": name, "phase": phase,
        "cpu_pct_stats": _float_pct(d.get("CPUPerc")),
        "stats_mem_mib": round(to_mib(memu[0]), 3),
        "mem_limit_mib": round(to_mib(memu[1]), 1) if len(memu) > 1 else 0.0,
        "mem_pct": _float_pct(d.get("MemPerc")),
        "net_rx_mib": round(rx, 3), "net_tx_mib": round(tx, 3),
        "blk_r_mib": round(bi, 3), "blk_w_mib": round(bo, 3),
        "pids": int(d.get("PIDs") or 0),
        "host_free_gb": host_free_gb(),
    }
    if extra:
        rec.update(extra)
    return rec


class DockerStatsCollector(Collector):
    """Thin tag for the poller; real work happens in ``samplers``."""

    name = "docker_stats"

    def __init__(self, container=""):
        self.container = container
        self.mode = "poll" if container else "none"

    def available(self):
        return bool(self.container)
