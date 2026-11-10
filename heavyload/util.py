#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Statistics, formatting, IO and OS helpers. Standard library only."""
from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone


# ------------------------------------------------------------------- basics --
def utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def pct(sorted_vals, p):
    """Nearest-rank / linearly interpolated percentile on a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    if p >= 100:
        return float(sorted_vals[-1])
    if p <= 0:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def percentiles(vals):
    s = sorted(vals)
    if not s:
        return {"n": 0, "p50": 0.0, "p90": 0.0, "p95": 0.0,
                "p99": 0.0, "max": 0.0, "mean": 0.0}
    return {
        "n": len(s),
        "p50": round(pct(s, 50), 2),
        "p90": round(pct(s, 90), 2),
        "p95": round(pct(s, 95), 2),
        "p99": round(pct(s, 99), 2),
        "max": round(s[-1], 2),
        "mean": round(sum(s) / len(s), 2),
    }


def to_mib(s):
    m = re.match(r"\s*([\d.]+)\s*([kKmMgGtT]?i?[bB])\s*$", str(s or ""))
    if not m:
        return 0.0
    v, u = float(m.group(1)), m.group(2).lower()
    mult = {"b": 1 / 1048576, "kb": 1 / 1024, "kib": 1 / 1024,
            "mb": 1.0, "mib": 1.0, "gb": 1024.0, "gib": 1024.0,
            "tb": 1048576.0, "tib": 1048576.0}
    return v * mult.get(u, 1.0)


def split_io(s):
    try:
        a, b = str(s or "0 / 0").split("/")
        return to_mib(a), to_mib(b)
    except Exception:
        return 0.0, 0.0


def linfit(xs, ys):
    """Least squares. Returns (slope, r2), or (None, None) if degenerate."""
    n = len(xs)
    if n < 3 or n != len(ys):
        return None, None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None, None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    return b, (1 - sse / sst if sst > 0 else None)


def theil_sen(xs, ys):
    """Robust slope, used as a cross-check against least squares."""
    slopes = []
    n = min(len(xs), len(ys))
    for i in range(n):
        for j in range(i + 1, n):
            if xs[j] != xs[i]:
                slopes.append((ys[j] - ys[i]) / (xs[j] - xs[i]))
    if not slopes:
        return None
    slopes.sort()
    return slopes[len(slopes) // 2]


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(xs, ys):
    if len(xs) < 5 or len(xs) != len(ys):
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx > 0 and dy > 0 else None


def monotonic_fraction(v):
    if len(v) < 2:
        return None
    return round(sum(1 for a, b in zip(v, v[1:]) if b >= a) / (len(v) - 1), 4)


def host_free_gb():
    """Available physical RAM in GiB. -1 when the platform is unsupported."""
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMORYSTATUSEX()
            m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return round(m.ullAvailPhys / 1073741824, 3)
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return round(int(line.split()[1]) / 1048576.0, 3)
    except Exception:
        pass
    return -1.0


def parse_cpuset(text):
    """Parse '0-3,6' -> [0,1,2,3,6]; validates against the local CPU count."""
    out = []
    ncpu = os.cpu_count() or 1
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                out.extend(range(int(a), int(b) + 1))
            else:
                out.append(int(part))
        except Exception:
            return []
    return sorted({c for c in out if 0 <= c < ncpu})


def pin_current_process(cpuset):
    """Pin the load generator to explicit cores so it does not steal CPU from
    the target. Returns True on success; failure is non-fatal.

    On Windows/Docker Desktop the target container, the WSL2 VM and this
    process all share physical cores, so pinning (plus idle-baseline
    subtraction) is what makes lab CPU numbers meaningful.
    """
    cpus = parse_cpuset(cpuset)
    if not cpus:
        return False
    try:
        if os.name == "nt":
            mask = sum(1 << c for c in cpus if c < 63)
            h = ctypes.windll.kernel32.GetCurrentProcess()
            return bool(ctypes.windll.kernel32.SetProcessAffinityMask(
                h, ctypes.c_size_t(mask)))
        os.sched_setaffinity(0, cpus)
        return True
    except Exception:
        return False


def cpu_model():
    try:
        return (platform.processor() or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------- IO --
def atomic_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


class LogBuffer:
    """Thread-safe bounded log used by both the CLI and the web UI."""

    def __init__(self, limit=800):
        self._d = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, msg, level="info"):
        with self._lock:
            self._seq += 1
            self._d.append({"seq": self._seq, "ts": utc(), "level": level,
                            "msg": str(msg)})

    def since(self, seq=0, limit=300):
        with self._lock:
            rows = [x for x in self._d if x["seq"] > seq]
        return rows[-limit:]

    def last_seq(self):
        with self._lock:
            return self._seq


def downsample(points, maxn=900):
    """Keep at most maxn points, preserving the first and last."""
    if len(points) <= maxn:
        return points
    step = len(points) / float(maxn)
    out = [points[int(i * step)] for i in range(maxn)]
    if out[-1] is not points[-1]:
        out[-1] = points[-1]
    return out


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def safe_div(a, b, default=0.0):
    try:
        return a / b if b else default
    except Exception:
        return default
