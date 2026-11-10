"""Out-of-band availability probe (1..20 Hz, configurable)."""

from __future__ import annotations

from .. import http_client
from .base import Collector


def probe_once(cfg, *, timeout=8):
    """One GET of ``health_path``. Never raises."""
    d = cfg["_derived"]
    hp = cfg.get("health_path") or "/health"
    return http_client.probe(
        d["scheme"], d["host"], d["port"], hp, timeout=timeout,
        user_agent="heavy-load-checker/1.0")


class HealthCollector(Collector):
    name = "health"

    def __init__(self, cfg):
        self.cfg = cfg
        self.mode = "http"

    def available(self):
        return True
