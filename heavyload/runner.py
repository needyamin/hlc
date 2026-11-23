#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run orchestration, preflight checks and route discovery."""
from __future__ import annotations

import json
import os
import platform
import socket
import sys

from . import config as C
from . import adapters
from . import grading, report
from . import http_client
from . import paths
from . import target_manager
from .browser import cdp
from .engine import Benchmark
from .schema import ensure_current
from .util import (atomic_json, cpu_model, host_free_gb, parse_cpuset,
                   stamp, utc)


def http_probe(cfg, path="/", method="GET", timeout=6):
    """One lightweight request. Returns a dict; never raises.

    Delegates to ``http_client.probe`` so preflight, health probes and the
    load engine share one connection factory and one high-resolution timer.
    """
    return http_client.probe_cfg(cfg, path=path, method=method,
                                 timeout=timeout)


def discover_routes(cfg, candidates=None, limit=16):
    """Probe common routes and suggest a route mix. Read-only GETs only."""
    cands = list(dict.fromkeys(
        list(candidates or []) + C.ROUTE_CANDIDATES + [cfg["health_path"]]))
    found = []
    for p in cands[:40]:
        r = http_probe(cfg, p)
        found.append({
            "path": p, "status": r["status"], "error": r["error"],
            "bytes": r["bytes"], "latency_ms": r["latency_ms"],
            "include": 200 <= r["status"] < 400 and not r["error"],
        })
        if len([x for x in found if x["include"]]) >= limit:
            break
    return found


def generator_fingerprint():
    """Stable identity of the machine generating the load.

    Two runs may only be ranked against each other when this fingerprint
    matches: same host, same platform, same CPU resources. It is the
    client-only replacement for the instrumented-lab's environment gate.
    """
    return "|".join(filter(None, [
        socket.gethostname() or "",
        platform.system(),
        platform.machine(),
        str(os.cpu_count() or ""),
    ]))


def is_loopback(cfg):
    host = str((cfg.get("_derived") or {}).get("host") or "")
    return host in ("127.0.0.1", "::1", "localhost")


def preflight(cfg):
    """Read-only checks: URL reachability, health route, host headroom."""
    checks = []
    p = http_probe(cfg, cfg["_derived"]["base_path"])
    checks.append({
        "name": "Target reachable",
        "ok": p["status"] > 0 and not p["error"],
        "detail": ("HTTP %s in %.1f ms, %d bytes, %s"
                   % (p["status"], p["latency_ms"], p["bytes"],
                      p["headers"].get("Content-Type", "no content-type"))
                   if p["status"] else "connection failed: %s" % p["error"]),
    })
    hp = http_probe(cfg, cfg["health_path"])
    checks.append({
        "name": "Health endpoint",
        "ok": 200 <= hp["status"] < 400,
        "detail": ("%s -> HTTP %s in %.1f ms"
                   % (cfg["health_path"], hp["status"], hp["latency_ms"])
                   if hp["status"] else
                   "%s -> %s (set health_path to a real 2xx route if needed)"
                   % (cfg["health_path"], hp["error"])),
    })
    loop = is_loopback(cfg)
    checks.append({
        "name": "Vantage point",
        "ok": True,
        "detail": ("client-only run against a loopback target: latency, "
                   "throughput and error-rate metrics are real server "
                   "responses observed from this machine; server-side CPU/"
                   "memory is not visible and is never estimated"
                   if loop else
                   "client-only run against a REMOTE target: cross-run "
                   "comparisons are disabled because network conditions "
                   "cannot be held constant"),
    })
    free = host_free_gb()
    checks.append({
        "name": "Host headroom",
        "ok": free < 0 or free >= cfg["guardrails"]["min_host_free_gb"],
        "detail": "%.2f GB available physical RAM (floor %.1f GB)"
                  % (free, cfg["guardrails"]["min_host_free_gb"]),
    })
    return {
        "ok": all(x["ok"] for x in checks[:2]),
        "target_url": cfg["target_url"],
        "loopback_target": loop,
        "generator_fingerprint": generator_fingerprint(),
        "checks": checks,
        "target_probe": p,
        "health_probe": hp,
        "host_free_gb": free,
        "checked_utc": utc(),
    }


def environment_info(cfg, pf=None):
    pf = pf or {}
    target = cfg.get("target") or {}
    fairness = cfg.get("fairness") or {}
    return {
        "host_available_ram_gb": pf.get("host_free_gb", host_free_gb()),
        "host_cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "generated_utc": utc(),
        "vantage": "client_only",
        "fingerprint": {
            "image_digest": target.get("image_digest", ""),
            "runtime_version": target.get("runtime_version", ""),
            # Historical v2 fingerprint fields; always empty in client-only
            # runs (kept so archived instrumented runs stay schema-equal).
            "cgroup_version": "",
            "docker_version": "",
            "kernel": "",
            "cpu_model": cpu_model(),
            "pinned_cpus": parse_cpuset(fairness.get("cpuset_target", "")),
            "host_shared_cpu": True,
            "generator_pinned": False,
            "seed": fairness.get("seed"),
            # Single-host comparability gate: both sides must match for two
            # client-only runs to be ranked.
            "generator_fingerprint": pf.get("generator_fingerprint",
                                            generator_fingerprint()),
            "target_host": (cfg.get("_derived") or {}).get("host") or "",
        },
    }


def run_benchmark(cfg, root_dir, log=None, preflight_result=None,
                  bench_holder=None, auto_start_target=True,
                  keep_target_alive=False):
    """Execute a full run and write config.json, results.json, report.html.

    bench_holder, when supplied, receives the live Benchmark instance under
    the key "bench" so a UI can request a graceful stop mid-run.

    When ``auto_start_target`` is True (the default) and the configured target
    is a loopback URL that refuses connections AND matches a launchable local
    spec under ``root_dir/targets/``, that local target is started
    automatically before running preflight again. If ``keep_target_alive`` is
    False, any target started by this call is stopped cleanly when the run
    finishes.
    """
    cfg = C.normalize(cfg)
    # Never reuse a cached preflight that belongs to a different target OR a
    # different generator host: the fingerprint is the comparability gate.
    if (preflight_result
            and preflight_result.get("target_url") == cfg["target_url"]
            and preflight_result.get("generator_fingerprint",
                                     generator_fingerprint())
            == generator_fingerprint()):
        pf = preflight_result
    else:
        pf = preflight(cfg)
    pf["target_url"] = cfg["target_url"]       # repair older cached preflights
    mode = "live_blackbox"
    run_id = "run-" + stamp()

    # ---- dynamic auto-start for matching local native targets --------------
    # Only activates when the target is loopback, currently refusing
    # connections, AND a launchable local spec exists under root_dir/targets/.
    # Remote targets and unknown loopback ports are never substituted.
    auto_started_site = ""
    tp = pf.get("target_probe") or {}
    _tp_err = str(tp.get("error") or "")
    if (auto_start_target
            and int(tp.get("status") or 0) == 0
            and "ConnectionRefused" in _tp_err
            and is_loopback(cfg)
            and target_manager.resolve_target_for_cfg(cfg, root_dir) is not None):
        try:
            started_info = target_manager.ensure_target_running(
                cfg, root_dir, log=log)
            if started_info.get("started"):
                auto_started_site = started_info.get("site_id") or ""
            if started_info.get("started") or started_info.get("already_running"):
                pf = preflight(cfg)
                pf["target_url"] = cfg["target_url"]
                tp = pf.get("target_probe") or {}
                _tp_err = str(tp.get("error") or "")
        except Exception as ex:
            if log:
                log.add("Auto-start failed for %s: %s"
                        % (cfg["target_url"], ex), "error")
            raise RuntimeError(
                "Preflight failed: target %s is unreachable and auto-start "
                "failed (%s). No run directory or report was generated."
                % (cfg["target_url"], ex)) from ex

    # ---- fail fast on a dead target ----------------------------------------
    # A connection-refused preflight means nothing is listening: every request
    # in the run would be a transport failure and the report would contain no
    # measurable evidence (0.00 ms latency, 0 bytes). Refuse the run instead
    # of burning 30+ seconds on a useless report. DNS failures are treated the
    # same way; timeouts are NOT — a slow-but-alive target is a real result.
    if int(tp.get("status") or 0) == 0 and (
            "ConnectionRefused" in _tp_err
            or "getaddrinfo failed" in _tp_err
            or "nodename nor servname" in _tp_err
            or "Name or service not known" in _tp_err):
        if auto_started_site and not keep_target_alive:
            target_manager.stop_target(auto_started_site, root=root_dir)
        msg = ("Preflight failed: target %s is unreachable (%s). Start the "
               "target web application first, then re-run — e.g. this tool's "
               "practice target: python scripts/demo_target.py --port 8090, "
               "or a spec-defined target: python scripts/native_target.py "
               "<site-id> start. No run directory or report was generated "
               "for a dead target."
               % (cfg["target_url"], _tp_err or "connection failed"))
        if log:
            log.add(msg, "error")
        raise RuntimeError(msg)

    try:
        return _execute_benchmark(cfg, root_dir, pf, mode, run_id,
                                  log=log, bench_holder=bench_holder)
    finally:
        if auto_started_site and not keep_target_alive:
            if log:
                log.add("Stopping auto-started local target '%s'"
                        % auto_started_site)
            target_manager.stop_target(auto_started_site, root=root_dir)


def _execute_benchmark(cfg, root_dir, pf, mode, run_id, log=None,
                       bench_holder=None):
    run_dir = os.path.join(paths.runs_dir(root_dir, cfg), run_id)
    os.makedirs(run_dir, exist_ok=True)

    C.save_config(os.path.join(run_dir, "config.json"), cfg)
    atomic_json(os.path.join(run_dir, "preflight.json"), pf)
    if log:
        log.add("Vantage: client-only (mode=%s) | profile=%s"
                % (mode, cfg["profile"]))
        log.add("Preflight: %s" % "; ".join(
            "%s=%s" % (x["name"], "OK" if x["ok"] else "WARN")
            for x in pf["checks"]))

    bench = Benchmark(cfg, run_dir, log=log,
                      env_info=environment_info(cfg, pf), run_id=run_id)
    if bench_holder is not None:
        bench_holder["bench"] = bench
        bench_holder["run_id"] = run_id
    results = bench.run()
    results["adjudication"] = grading.grade(results)
    results["preflight"] = pf
    series = results.setdefault("series", {})

    # ---- optional, failure-tolerant extras ---------------------------------
    # Runtime internals (heap / GC / threads / goroutines / fds).
    ra = cfg.get("runtime_adapter") or {}
    if ra.get("type"):
        d = cfg["_derived"]
        ad = adapters.get(ra["type"], host=d["host"], port=d["port"],
                          scheme=d["scheme"], endpoint=ra["endpoint"])
        if ad is None:
            if log:
                log.add("Unknown runtime adapter %r (ignored)" % ra["type"],
                        "warn")
        elif ad.available():
            norm = ad.normalized()
            results.setdefault("metrics", {}).setdefault("runtime", {}).update(
                {k: v for k, v in norm.items() if v is not None})
            series.setdefault("runtime_series", []).append({
                "t": 0.0, "wall": utc(), "adapter": ad.name,
                "runtime": ad.runtime_name, "metrics": norm,
                "fingerprint": ad.fingerprint(),
            })
            if log:
                log.add("Runtime adapter %s collected metrics" % ad.name)
        elif log:
            log.add("Runtime adapter %s unavailable (%s)"
                    % (ad.name, ad.fingerprint().get("last_error", "n/a")))
    # Browser-side page metrics (headless Chrome CDP, no pip dependencies).
    bcfg = cfg.get("browser") or {}
    if bcfg.get("enabled"):
        br = cdp.collect(bcfg.get("cdp_url"), cfg["target_url"])
        if br.get("available"):
            series.setdefault("browser_series", []).append({
                "t": 0.0, "wall": utc(), "source": br.get("source"),
                "target_url": cfg["target_url"], "metrics": br,
            })
            results.setdefault("metrics", {}).setdefault("browser", {}).update(
                {k: br.get(k) for k in
                 ("ttfb_ms", "fcp_ms", "lcp_ms", "dom_content_loaded_ms",
                  "tbt_ms", "cls") if br.get(k) is not None})
            if log:
                log.add("Browser metrics collected via CDP")
        elif log:
            log.add("Browser metrics unavailable: %s" % br.get("error"),
                    "warn")

    # ---- v2 normalisation: mode labels, visibility, fingerprint, metrics ----
    env = results.setdefault("environment", {})
    fp = env.setdefault("fingerprint", {})
    fp["generator_pinned"] = getattr(bench, "generator_pinned", False)
    if getattr(bench, "samplers", None) is not None:
        fp["collector_mode"] = bench.samplers.client_only and "health+generator"
    # Loopback targets share one machine: identical profiles across languages
    # are rankable with explicit caveats; remote targets are never ranked.
    visibility = "client_only_same_host" if pf.get("loopback_target") \
        else "client_only"
    ensure_current(
        results, mode=mode, target=cfg.get("target") or {},
        fingerprint=fp, visibility=visibility,
        has_runtime=bool(series.get("runtime_series")),
        has_browser=bool(series.get("browser_series")))
    s = results.get("summary") or {}
    http = results["metrics"]["http"]
    http["ttfb_p95_ms"] = (s.get("ttfb_all") or {}).get("p95")
    http["dispatch_delay_p95_ms"] = (s.get("dispatch_delay_ms") or {}).get("p95")
    http["as_scheduled_p95_ms"] = (s.get("as_scheduled_latency") or {}).get("p95")
    if log:
        log.add("Mode=%s visibility=%s schema=v%d"
                % (results["benchmark_mode"], results["visibility"],
                   results["schema_version"]))

    # ---- data-quality checks (measured evidence integrity) -----------------
    try:
        from . import quality as Q
        results["quality"] = Q.check_run(results)
        if log:
            n_bad = len([c for c in results["quality"].get("checks", [])
                         if c.get("status") == "warn"])
            log.add("Quality checks: %d total, %d warnings"
                    % (len(results["quality"].get("checks", [])), n_bad))
    except Exception as ex:
        results["quality"] = {"status": "error", "checks": [],
                              "warnings": [str(ex)]}

    # ---- scalability & concurrency analysis --------------------------------
    try:
        from . import scalability as SC
        results["scalability"] = SC.analyze(results)
        if log:
            log.add("Scalability: %s" % (
                (results["scalability"] or {}).get("classification")
                or "insufficient step evidence"))
    except Exception as ex:
        results["scalability"] = {"status": "error", "warnings": [str(ex)]}

    # ---- cross-language runtime comparison (on by default, failure-safe) --
    # Runs after the HTTP schedule so a comparison problem can never lose the
    # load-test result. Both the CLI and the web UI go through this function.
    rcfg = cfg.get("runtime_comparison") or {}
    if rcfg.get("enabled", True):
        try:
            from . import runtime_comparison as RC
            if log:
                log.add("Running cross-language runtime comparison "
                        "(%s repeats per variant, pinned checksums)"
                        % rcfg.get("repeats", 5))
            rc_data = RC.run(cfg, run_dir, log=log, write_html=True)
            results["runtime_comparison"] = RC.summarize(rc_data)
        except Exception as ex:
            results["runtime_comparison"] = {
                "status": "error",
                "verdict": "Comparison failed: %s" % ex,
                "slowdown": {}, "warnings": [str(ex)], "artifacts": {},
            }
            if log:
                log.add("Runtime comparison failed: %s" % ex, "warn")

    # ---- technology suitability assessment ---------------------------------
    if (cfg.get("suitability") or {}).get("enabled", True):
        try:
            from . import suitability as SU
            su = SU.build(results, cfg, run_dir, log=log, write_html=False)
            results["suitability"] = SU.summarize(su)
        except Exception as ex:
            results["suitability"] = {"status": "error",
                                      "warnings": [str(ex)]}
            if log:
                log.add("Suitability analysis failed: %s" % ex, "warn")

    results_path = os.path.join(run_dir, "results.json")
    atomic_json(results_path, results)
    verdict_path = os.path.join(run_dir, "stage2_verdict.json")
    atomic_json(verdict_path, results["adjudication"])
    report_path = report.build(results, os.path.join(run_dir, "report.html"))
    if log:
        log.add("Report written: %s" % report_path)
        log.add("Verdict: %s (health %d/100)"
                % (results["adjudication"]["verdict"],
                   results["adjudication"]["health_score"]),
                "error" if "NO-GO" in results["adjudication"]["verdict"]
                else "info")
    rc_html_path = os.path.join(run_dir, "runtime_comparison.html")
    return {"run_id": run_id, "run_dir": run_dir,
            "results_path": results_path, "report_path": report_path,
            "status_path": os.path.join(run_dir, "status.json"),
            "runtime_comparison": bool(results.get("runtime_comparison")),
            "has_runtime_comparison_html": os.path.isfile(rc_html_path),
            "verdict": results["adjudication"]["verdict"],
            "health_score": results["adjudication"]["health_score"]}


def _load_json(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _run_row(d, name):
    st = _load_json(os.path.join(d, "status.json"))
    cfg = _load_json(os.path.join(d, "config.json"))
    v = _load_json(os.path.join(d, "stage2_verdict.json"))
    res = _load_json(os.path.join(d, "results.json"))
    has_report = os.path.isfile(os.path.join(d, "report.html"))
    state = st.get("state", "unknown")
    if not has_report and state in ("running", "starting", "complete"):
        state = "incomplete"       # stale/crashed run, no deliverable
    return {
        "run_id": name,
        "target_url": cfg.get("target_url", ""),
        "profile": cfg.get("profile", ""),
        "state": state,
        "requests": st.get("requests", 0),
        "rps": st.get("rps", 0),
        "updated": st.get("updated", ""),
        "verdict": v.get("verdict", ""),
        "health_score": v.get("health_score"),
        "has_report": has_report,
        "has_results": bool(res),
        "has_runtime_comparison_html": os.path.isfile(
            os.path.join(d, "runtime_comparison.html")),
        # v2 dimensions (absent on v1 runs -> empty string / None)
        "schema_version": res.get("schema_version"),
        "benchmark_mode": res.get("benchmark_mode", ""),
        "visibility": res.get("visibility", ""),
        "language": (res.get("target") or {}).get("language", ""),
        "site_id": (res.get("target") or {}).get("site_id", ""),
        "verdict_code": res.get("verdict_code"),
    }


def list_runs(root_dir, cfg=None, include_archive=False):
    """List runs newest-first. ``output_dir`` is honoured when cfg is given."""
    bases = [paths.runs_dir(root_dir, cfg)]
    if include_archive:
        bases.append(paths.archive_runs_dir(root_dir))
    out = []
    for base in bases:
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base), reverse=True):
            d = os.path.join(base, name)
            if os.path.isdir(d) and name.startswith("run-"):
                out.append(_run_row(d, name))
    out.sort(key=lambda r: r["run_id"], reverse=True)
    return out
