#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instrumentation samplers: health probes + load-generator self-monitoring.

There is no server-side instrumentation channel: the load generator only ever
speaks HTTP to the target, so every run is labelled ``client_only`` and the
report says so. What IS measured on the generator side (and published as
``generator_series``) is the cost of measuring: this process's CPU time and
working set, sampled at ``sample_period_s``. That data answers "was the load
generator itself saturated?" - a question that must be answered before any
latency percentile can be trusted.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time

from .collectors.health import probe_once
from .latency import ms, now_ns
from .util import host_free_gb, utc

PAGE = 4096


def _win_self_memory():
    """Current-process memory counters via GetProcessMemoryInfo (Windows).

    Returns (rss_bytes, peak_rss_bytes) or (None, None) when unavailable.
    PeakWorkingSetSize is tracked by the kernel, so no polling is missed.
    """
    class PMEWS(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    try:
        psapi = ctypes.WinDLL("psapi")
        kernel32 = ctypes.WinDLL("kernel32")
        m = PMEWS()
        m.cb = ctypes.sizeof(m)
        # GetCurrentProcess() returns the pseudo handle -1; querying memory
        # info on it needs PROCESS_MEMORY_COUNTERS access only.
        if not psapi.GetProcessMemoryInfo(
                ctypes.c_void_p(-1), ctypes.byref(m), m.cb):
            return None, None
        return int(m.WorkingSetSize), int(m.PeakWorkingSetSize)
    except Exception:
        return None, None


def _self_memory():
    """(rss_bytes, peak_rss_bytes) for this process, or (None, None)."""
    if sys.platform == "win32":
        return _win_self_memory()
    try:
        rss = peak = None
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) * 1024
                elif line.startswith("VmHWM:"):
                    peak = int(line.split()[1]) * 1024
        return rss, peak
    except Exception:
        return None, None


class SamplerSet:
    """Owns every background sampler thread and the shared sample lists."""

    def __init__(self, cfg, log=None, t0=None):
        self.cfg = cfg or {}
        self.log = log
        # Every run is client-only: the generator never sees server-side CPU
        # or memory. Kept as an explicit attribute so the report and grading
        # can state the vantage without inferring it.
        self.client_only = True
        self.t0 = t0 if t0 is not None else time.perf_counter()
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.data = {"health": [], "generator": []}
        self.threads = []
        self.phase_ref = ["preflight"]
        self.probe_hz = [1.0]
        self.proc_summary = {}

    def _say(self, msg):
        if self.log:
            self.log.add(msg)

    def set_phase(self, name):
        self.phase_ref[0] = name

    def set_probe_hz(self, hz):
        self.probe_hz[0] = float(hz)

    def _t(self):
        return round(time.perf_counter() - self.t0, 3)

    # ------------------------------------------------------------- health --
    def _health_probe(self):
        hp = self.cfg["health_path"]
        while not self.stop_evt.is_set():
            hz = self.probe_hz[0]
            if hz <= 0:
                self.stop_evt.wait(0.25)
                continue
            t = now_ns()
            r = probe_once(self.cfg, timeout=8)
            self._push("health", {
                "t": self._t(), "wall": utc(), "phase": self.phase_ref[0],
                "path": hp, "status": r["status"], "error": r["error"],
                "latency_ms": round(ms(now_ns() - t), 2),
                "ttfb_ms": r.get("ttfb_ms"),
            })
            self.stop_evt.wait(1.0 / hz)

    # ---------------------------------------------------------- generator --
    def _generator_sampler(self):
        """Measure the load generator itself: CPU time + working set.

        A saturated generator inflates latency percentiles; these samples are
        the evidence the report uses to show it did not happen.
        """
        period = max(0.5, float(self.cfg.get("sample_period_s") or 1.0))
        prev_cpu = None
        prev_t = None
        while not self.stop_evt.is_set():
            rec = {
                "t": self._t(), "wall": utc(),
                "phase": self.phase_ref[0],
                "cpu_time_s": round(time.process_time(), 4),
                "host_free_gb": host_free_gb(),
            }
            rss, peak = _self_memory()
            if rss is not None:
                rec["rss_mib"] = round(rss / 1048576.0, 2)
            if peak is not None:
                rec["peak_rss_mib"] = round(peak / 1048576.0, 2)
            if prev_cpu is not None and prev_t is not None:
                dt = rec["t"] - prev_t
                du = rec["cpu_time_s"] - prev_cpu
                if dt > 0 and du >= 0:
                    # Fraction of ONE core (process_time sums all threads),
                    # matching how target CPU% is normalised elsewhere.
                    rec["cpu_pct"] = round(du / dt * 100.0, 2)
            prev_cpu, prev_t = rec["cpu_time_s"], rec["t"]
            self._push("generator", rec)
            self.stop_evt.wait(period)

    # ------------------------------------------------------------ plumbing --
    def _push(self, key, rec):
        with self.lock:
            self.data[key].append(rec)

    def start(self):
        targets = [
            ("hlc-health", self._health_probe),
            ("hlc-generator", self._generator_sampler),
        ]
        for name, fn in targets:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self):
        self.stop_evt.set()
        for t in self.threads:
            t.join(timeout=6)

    def snapshot(self):
        with self.lock:
            gen = self.data["generator"][-1] if self.data["generator"] else {}
            hp = list(self.data["health"][-20:])
        ok = sum(1 for x in hp if 200 <= x.get("status", 0) < 400)
        return {
            "gen_rss_mib": gen.get("rss_mib"),
            "gen_cpu_pct": gen.get("cpu_pct"),
            "host_free_gb": gen.get("host_free_gb", host_free_gb()),
            "probes": len(self.data["health"]),
            "recent_probe_ok_pct": round(100.0 * ok / len(hp), 1) if hp else None,
            "generator_samples": len(self.data["generator"]),
        }

    def finalize(self):
        """Return all series; generator CPU% is derived while sampling."""
        with self.lock:
            self.proc_summary = {}
        return self.data
