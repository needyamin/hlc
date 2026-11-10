"""Additive v1 -> v2 migration and the single write-path normaliser.

Design rule: **never delete, rename or coerce an existing key**. v1 runs on
disk and in ``_archive/runs/`` stay readable; every new run is written as v2.
``ensure_current`` is idempotent and mutates the passed dict in place (it also
returns it) so callers can use it as either a helper or a normaliser.
"""

from __future__ import annotations

from .metrics import (
    SCHEMA_VERSION,
    empty_fingerprint,
    empty_metrics,
    empty_target,
    verdict_code,
    visibility_map,
)


def _first(*values):
    for v in values:
        if v is not None:
            return v
    return None


def _fill(target, source):
    """Fill only keys that are absent or currently None."""
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _fill(target[k], v)
        elif target.get(k) is None:
            target[k] = v


def infer_mode(results):
    """Best-effort classification of legacy runs that carried no mode label."""
    meta = results.get("meta") or {}
    res = results.get("resources") or {}
    series = results.get("series") or {}
    cgroup_n = res.get("cgroup_samples") or 0
    stats_n = res.get("stats_samples") or 0
    has_samples = (cgroup_n > 0 or stats_n > 0
                   or bool(series.get("cgroup_series")))
    has_container = bool(meta.get("container"))
    if has_samples and has_container:
        return "docker_lab", True
    return "live_blackbox", False


def ensure_current(results, *, mode=None, target=None, fingerprint=None,
                   has_runtime=None, has_browser=None):
    """Return ``results`` upgraded to v2 (mutated in place, idempotent)."""
    if not isinstance(results, dict):
        return results

    inferred_mode, inferred_server = infer_mode(results)
    explicit_mode = mode is not None
    if mode is None:
        mode = (results.get("benchmark_mode")
                or (results.get("meta") or {}).get("benchmark_mode")
                or inferred_mode)
    if mode not in ("docker_lab", "live_blackbox", "instrumented_live"):
        mode = inferred_mode

    series = results.setdefault("series", {}) or {}
    runtime_series = series.setdefault("runtime_series", [])
    browser_series = series.setdefault("browser_series", [])
    if has_runtime is None:
        has_runtime = bool(runtime_series)
    if has_browser is None:
        has_browser = bool(browser_series)

    res = results.get("resources") or {}
    cgroup_n = res.get("cgroup_samples") or 0
    stats_n = res.get("stats_samples") or 0
    has_server = bool(results.get("container")
                      or (results.get("meta") or {}).get("container")) \
        and (cgroup_n > 0 or stats_n > 0 or bool(series.get("cgroup_series")))
    if mode == "instrumented_live":
        has_server = has_server or bool(runtime_series)
    if not has_server and mode == "docker_lab" and inferred_server:
        has_server = True

    visibility = ("full_server" if has_server
                  else "server_via_adapter" if mode == "instrumented_live"
                  else "client_only")

    # -- top level -----------------------------------------------------------
    results.setdefault("schema_version", SCHEMA_VERSION)
    results.setdefault("benchmark_mode", mode)
    results.setdefault("visibility", visibility)
    results.setdefault("target", empty_target())
    results.setdefault("metrics", empty_metrics())
    results.setdefault("per_metric_visibility", {})
    results.setdefault("verdict_code", None)

    meta = results.setdefault("meta", {})
    meta.setdefault("schema_version", SCHEMA_VERSION)
    meta.setdefault("benchmark_mode", mode)
    meta.setdefault("workload_class", "http_loadtest")
    meta.setdefault("benchmark_type", "http_loadtest")
    meta.setdefault("mode_inferred", not explicit_mode)
    meta.setdefault("mode_note",
                    "mode inferred from archived v1 evidence"
                    if meta.get("mode_inferred") else "")

    # -- target identity -----------------------------------------------------
    tgt = results["target"]
    _fill(tgt, empty_target())
    if not tgt.get("site_id"):
        tgt["site_id"] = meta.get("target_url") or ""
    if target:
        for k, v in target.items():
            if v not in (None, "", [], {}):
                tgt[k] = v

    # -- environment fingerprint --------------------------------------------
    env = results.setdefault("environment", {})
    fp = env.setdefault("fingerprint", empty_fingerprint())
    _fill(fp, empty_fingerprint())
    if not fp.get("docker_version"):
        fp["docker_version"] = env.get("docker_server_version") or ""
    if not fp.get("cgroup_version"):
        fp["cgroup_version"] = env.get("cgroup_interface") or ""
    if not fp.get("runtime_version"):
        fp["runtime_version"] = tgt.get("runtime_version") or ""
    if fingerprint:
        for k, v in fingerprint.items():
            if v not in (None, "", [], {}):
                fp[k] = v

    # -- normalised metric view (derived, never overwrites real data) --------
    metrics = results["metrics"]
    _fill(metrics, empty_metrics())
    summary = results.get("summary") or {}
    latency = summary.get("PRIMARY_successful_200_only") or {}
    all_lat = summary.get("all_requests_latency") or {}
    cg = (series.get("cgroup_series") or [])
    last_cg = cg[-1] if cg else {}

    _fill(metrics["http"], {
        "rps": summary.get("overall_rps"),
        "error_pct": summary.get("s1_error_pct"),
        "total_ms": latency.get("mean") or all_lat.get("mean"),
        "bytes_total": summary.get("bytes_total"),
        "latency_ms": {
            k: _first(latency.get(k), all_lat.get(k)) for k in
            ("p50", "p90", "p95", "p99", "max", "mean")
        },
    })
    _fill(metrics["resources"], {
        "cpu_pct_core": _first(res.get("cpu_mean_pct"), res.get("cpu_peak_pct")),
        "mem_anon_mib": res.get("anon_peak_mib"),
        "mem_anon_bytes": last_cg.get("anon_bytes"),
        "mem_peak_bytes": last_cg.get("peak_bytes"),
        "io_read_bytes": last_cg.get("io_rbytes"),
        "io_write_bytes": last_cg.get("io_wbytes"),
        "pids_current": res.get("pids_max"),
        "oom_kill_events": res.get("oom_kill_events"),
        "psi_avg10": res.get("psi_max_avg10"),
    })

    # -- extra series (additive) ---------------------------------------------
    for key in ("proc_series", "runtime_series", "browser_series", "repeats"):
        series.setdefault(key, [])
    if not series.get("mysql_series"):
        series.setdefault("mysql_series", [])

    # -- visibility ----------------------------------------------------------
    if not results["per_metric_visibility"]:
        results["per_metric_visibility"] = visibility_map(
            mode, has_server=has_server, has_runtime=has_runtime,
            has_browser=has_browser)
    else:
        merged = visibility_map(mode, has_server=has_server,
                                has_runtime=has_runtime,
                                has_browser=has_browser)
        for k, v in merged.items():
            results["per_metric_visibility"].setdefault(k, v)
    if not series.get("mysql_series"):
        results["per_metric_visibility"]["series.mysql_series"] = "unavailable"

    # -- verdict -------------------------------------------------------------
    adj = results.get("adjudication") or {}
    if results["verdict_code"] is None:
        results["verdict_code"] = verdict_code(adj.get("verdict"))
    if adj and "verdict_code" not in adj:
        adj["verdict_code"] = results["verdict_code"]

    # -- schema-drift repairs that ADD information only ----------------------
    pf = results.get("preflight")
    if isinstance(pf, dict) and not pf.get("target_url"):
        pf["target_url"] = meta.get("target_url") or ""
    if not isinstance(res.get("canonical_metric"), str) or \
            not res.get("canonical_metric"):
        res["canonical_metric"] = (
            "unavailable (client-only / live black-box mode)"
            if not has_server else "cgroup v2 memory.stat anon (bytes)")

    return results


def is_current(results):
    return (isinstance(results, dict)
            and results.get("schema_version") == SCHEMA_VERSION
            and (results.get("meta") or {}).get("schema_version")
            == SCHEMA_VERSION
            and bool(results.get("visibility")))


def upgrade_file(path, write=False):
    """Read a JSON result, migrate it, optionally write back atomically."""
    import json
    import os

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    ensure_current(data)
    if write:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    return data
