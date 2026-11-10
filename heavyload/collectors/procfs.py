"""Per-process attribution from the collector's ``PROCJSON`` lines.

cgroup counters describe the whole container (app + runtime + helpers + web
server). For a language-runtime benchmark we also want to know which process
actually burned the CPU and held the heap. This module turns the collector's
per-process snapshots into a compact series plus a per-run summary.
"""

from __future__ import annotations

import json

from .base import Collector


def parse_proc_line(line):
    """Return a list of process dicts, or ``None`` if not a PROCJSON line."""
    if not line or not line.startswith("PROCJSON "):
        return None
    try:
        rows = json.loads(line[len("PROCJSON "):])
        return rows if isinstance(rows, list) else None
    except Exception:
        return None


def summarize_proc_series(series):
    """Reduce [{t, phase, processes:[...]}] into a comparable summary."""
    if not series:
        return {"samples": 0, "peak_total_rss_bytes": None,
                "peak_processes": [], "top_by_cpu": []}
    peak = 0
    peak_rows = []
    cpu_totals = {}
    for point in series:
        rows = point.get("processes") or []
        total = sum(int(r.get("rss_bytes") or 0) for r in rows)
        if total > peak:
            peak, peak_rows = total, rows
        for r in rows:
            key = r.get("comm") or "pid:%s" % r.get("pid")
            cpu = int(r.get("utime") or 0) + int(r.get("stime") or 0)
            cur = cpu_totals.setdefault(key, {"comm": key, "cpu_ticks": 0,
                                              "rss_bytes": 0})
            cur["cpu_ticks"] = max(cur["cpu_ticks"], cpu)
            cur["rss_bytes"] = max(cur["rss_bytes"],
                                   int(r.get("rss_bytes") or 0))
    top = sorted(cpu_totals.values(), key=lambda x: x["cpu_ticks"],
                 reverse=True)[:8]
    return {
        "samples": len(series),
        "peak_total_rss_bytes": peak,
        "peak_processes": sorted(peak_rows,
                                 key=lambda r: r.get("rss_bytes") or 0,
                                 reverse=True)[:8],
        "top_by_cpu": top,
    }


class ProcCollector(Collector):
    """Passive: PROCJSON lines arrive on the cgroup collector's stdout."""

    name = "procfs"

    def __init__(self):
        self.series = []
        self.mode = "piggyback"

    def add(self, t, phase, rows):
        if rows:
            self.series.append({"t": t, "phase": phase, "processes": rows})

    def samples(self):
        return self.series

    def summary(self):
        return summarize_proc_series(self.series)
