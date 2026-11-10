"""Common collector contract."""

from __future__ import annotations


class Collector:
    """Minimal interface shared by present and future collectors.

    A collector owns exactly one background process/thread. ``start`` must be
    safe to call when the underlying facility is unavailable (returns False),
    and ``stop`` must always be idempotent. Collectors never raise into a
    benchmark run: a missing collector degrades the result, it does not fail it.
    """

    name = "base"
    mode = "none"

    def available(self):
        return False

    def start(self):
        return False

    def stop(self):
        return None

    def samples(self):
        return []


class NullCollector(Collector):
    """Inert collector used when instrumentation is unavailable."""

    def __init__(self, name="null", reason="unavailable"):
        self.name = name
        self.reason = reason
        self.mode = "none"

    def available(self):
        return False
