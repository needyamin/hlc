"""High-resolution timing helpers for the load engine.

Everything uses ``time.perf_counter_ns()`` (integer nanoseconds) internally;
milliseconds are only produced at the point of serialisation. This removes
float rounding from short local requests, where sub-millisecond resolution
matters when comparing language runtimes.
"""

from __future__ import annotations

import time

NS_PER_MS = 1_000_000.0


def now_ns():
    return time.perf_counter_ns()


def since_ns(start_ns):
    return time.perf_counter_ns() - start_ns


def ms(delta_ns):
    return delta_ns / NS_PER_MS


def since_ms(start_ns):
    return ms(since_ns(start_ns))


def r2(value):
    return None if value is None else round(value, 2)


class Stopwatch:
    """Capture connect / TTFB / full-body boundaries for one request."""

    __slots__ = ("start_ns", "connect_ns", "ttfb_ns", "total_ns", "scheduled_ns")

    def __init__(self, scheduled_ns=None):
        self.scheduled_ns = scheduled_ns
        self.start_ns = time.perf_counter_ns()
        self.connect_ns = None
        self.ttfb_ns = None
        self.total_ns = None

    def mark_connect(self):
        self.connect_ns = time.perf_counter_ns()

    def mark_ttfb(self):
        self.ttfb_ns = time.perf_counter_ns()

    def mark_total(self):
        self.total_ns = time.perf_counter_ns()

    def as_dict(self):
        start = self.start_ns
        return {
            "connect_ms": r2(ms(self.connect_ns - start))
            if self.connect_ns else 0.0,
            "ttfb_ms": r2(ms(self.ttfb_ns - start))
            if self.ttfb_ns else None,
            "body_ms": r2(ms(self.total_ns - self.ttfb_ns))
            if (self.total_ns and self.ttfb_ns) else None,
            "latency_ms": r2(ms(self.total_ns - start))
            if self.total_ns else None,
            # Coordinated omission: how long the request waited behind an
            # overloaded generator before it could even be dispatched.
            "dispatch_delay_ms": r2(ms(start - self.scheduled_ns))
            if self.scheduled_ns else 0.0,
        }
