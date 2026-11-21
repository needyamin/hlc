"""Unified v2 metric schema for Heavy Load Checker.

Unit conventions (apply to every collector and adapter):
  * bytes      -> int
  * durations  -> float, milliseconds (raw timer fields may use ``_ns``)
  * percent    -> float 0..100
  * rates      -> float (requests/second, MiB/second)
  * counts     -> int
  * timestamps -> float seconds since run start (``t``), ISO-8601 UTC for wall

The schema deliberately separates *what was measured* from *where the number
came from*. ``visibility`` and ``per_metric_visibility`` stop a client-only
black-box run from ever being compared with a Docker lab run.
"""

from __future__ import annotations

import copy

SCHEMA_VERSION = 2

BENCHMARK_MODES = ("docker_lab", "live_blackbox", "instrumented_live")
VISIBILITY_LEVELS = ("full_server", "client_only", "client_only_same_host",
                     "server_via_adapter")
VERDICT_CODES = ("GO", "NO_GO", "NO_GO_ABORT", "INSUFFICIENT")

_UNAVAILABLE = "unavailable"


def empty_target():
    return {
        "site_id": "",
        "group": "",
        "language": "",
        "runtime_version": "",
        "framework": "",
        "framework_version": "",
        "image": "",
        "image_digest": "",
        "build_cmd": "",
        "start_cmd": "",
        "stack_label": "",
    }


def empty_fingerprint():
    return {
        "image_digest": "",
        "runtime_version": "",
        "cgroup_version": "",
        "docker_version": "",
        "kernel": "",
        "cpu_model": "",
        "pinned_cpus": [],
        "host_shared_cpu": False,
        "generator_pinned": False,
        "seed": None,
        # Single-host comparability (client-only runs): stable identity of the
        # load generator (hostname|platform|machine|cpu_count) and of the
        # target host. Two client-only runs may only be ranked when BOTH match.
        "generator_fingerprint": "",
        "target_host": "",
    }


def empty_http():
    return {
        "rps": None,
        "error_pct": None,
        "connect_ms": None,
        "ttfb_ms": None,
        "total_ms": None,
        "latency_ms": {k: None for k in
                       ("p50", "p90", "p95", "p99", "max", "mean")},
        "bytes_total": None,
    }


def empty_resources():
    return {
        "cpu_pct_core": None,
        "mem_anon_bytes": None,
        "mem_anon_mib": None,
        "mem_rss_bytes": None,
        "mem_peak_bytes": None,
        "io_read_bytes": None,
        "io_write_bytes": None,
        "pids_current": None,
        "oom_kill_events": None,
        "psi_avg10": None,
    }


def empty_runtime():
    return {
        "heap_used_bytes": None,
        "heap_total_bytes": None,
        "gc_pause_ms": None,
        "gc_count": None,
        "goroutines": None,
        "threads": None,
        "fds": None,
        "rss_bytes": None,
    }


def empty_browser():
    return {
        "ttfb_ms": None,
        "fcp_ms": None,
        "lcp_ms": None,
        "dom_content_loaded_ms": None,
        "tbt_ms": None,
        "cls": None,
    }


def empty_metrics():
    return {
        "http": empty_http(),
        "resources": empty_resources(),
        "runtime": empty_runtime(),
        "browser": empty_browser(),
    }


def visibility_map(mode, *, has_server=False, has_runtime=False,
                   has_browser=False):
    """Which side of the wire each metric group came from."""
    server = ("full_server" if mode == "docker_lab"
              else "server_via_adapter" if mode == "instrumented_live"
              else _UNAVAILABLE)
    if not has_server:
        server = _UNAVAILABLE
    return {
        "summary.*": "client_only",
        "metrics.http.*": "client_only",
        "metrics.resources.*": server,
        "metrics.runtime.*": ("server_via_adapter" if has_runtime
                              else _UNAVAILABLE),
        "metrics.browser.*": ("client_only" if has_browser
                              else _UNAVAILABLE),
        "series.cgroup_series": server,
        "series.proc_series": server,
        "series.runtime_series": ("server_via_adapter" if has_runtime
                                  else _UNAVAILABLE),
        "series.browser_series": ("client_only" if has_browser
                                  else _UNAVAILABLE),
    }


def verdict_code(verdict_text):
    """Normalise the free-text v1 verdict into a stable enum."""
    v = str(verdict_text or "").upper()
    if "ABORT" in v or "INSUFFICIENT" in v:
        return "NO_GO_ABORT"
    if "CONDITIONS" in v:
        return "GO"
    if "NO-GO" in v or "NO GO" in v:
        return "NO_GO"
    if v.startswith("GO"):
        return "GO"
    return "INSUFFICIENT"


def can_compare(a, b):
    """Hard rule: never compare runs measured from different vantage points.

    Returns ``(bool, reason)``. Called by the campaign/comparison layer.

    Comparability rules (v2):
      * ``docker_lab`` / ``instrumented_live`` runs compare as before (server
        evidence present, same mode/schema/workload class).
      * Plain ``client_only`` runs (REMOTE targets, or archived runs without
        the generator fingerprint) are never comparable: network conditions
        cannot be held constant and no server evidence exists.
      * ``client_only_same_host`` runs (loopback targets, same machine, same
        profile) ARE rankable as a single-host client-side comparison — with
        the explicit caveat that this measures the full stack through one
        network path, not server-side resource usage.
    """
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False, "not a result object"
    for key in ("benchmark_mode", "visibility", "schema_version"):
        av = a.get(key) or (a.get("meta") or {}).get(key)
        bv = b.get(key) or (b.get("meta") or {}).get(key)
        if av != bv:
            return False, "%s differs (%r vs %r)" % (key, av, bv)
    am = (a.get("meta") or {}).get("workload_class") or "http_loadtest"
    bm = (b.get("meta") or {}).get("workload_class") or "http_loadtest"
    if am != bm:
        return False, "workload_class differs (%r vs %r)" % (am, bm)
    vis = a.get("visibility")
    if vis == "client_only":
        return False, "client-only runs against remote targets have no " \
                      "server metrics and uncontrolled network conditions"
    if vis == "client_only_same_host":
        afp = (a.get("environment") or {}).get("fingerprint") or {}
        bfp = (b.get("environment") or {}).get("fingerprint") or {}
        agen = afp.get("generator_fingerprint") or ""
        bgen = bfp.get("generator_fingerprint") or ""
        if not agen or not bgen:
            return False, "generator fingerprint missing (archived run?)"
        if agen != bgen:
            return False, "runs were generated on different machines (%r vs %r)" \
                % (agen, bgen)
        if afp.get("target_host") != bfp.get("target_host"):
            return False, "target host differs (%r vs %r)" % (
                afp.get("target_host"), bfp.get("target_host"))
        return True, "comparable (single-host client-side comparison)"
    return True, "comparable"


def clone(obj):
    return copy.deepcopy(obj)
