#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run orchestration, preflight checks and route discovery."""
from __future__ import annotations

import json
import os
import platform
import sys

from . import config as C
from . import docker_probe as dp
from . import adapters
from . import grading, report
from . import http_client
from . import paths
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


def preflight(cfg):
    """Read-only checks: URL reachability, Docker, container, cgroup, host."""
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

    avail = dp.docker_available()
    containers, cerr = dp.list_containers()
    checks.append({
        "name": "Docker daemon",
        "ok": avail["available"],
        "detail": ("server %s, %d running containers"
                   % (avail["server_version"], len(containers))
                   if avail["available"] else
                   "not reachable: %s" % (cerr or "docker ps failed")),
    })
    container, method, note = dp.resolve_container(cfg, containers)
    checks.append({
        "name": "Container resolution",
        "ok": bool(container) or cfg["mode"] == "direct",
        "detail": "%s [%s]" % (note, method),
    })
    caps = dp.cgroup_caps(container) if container else {
        "available": False, "reason": "no container selected"}
    checks.append({
        "name": "cgroup memory instrumentation",
        "ok": caps.get("available", False),
        "detail": ("%s; %s" % (caps.get("version"), caps.get("canonical_metric"))
                   if caps.get("available") else
                   caps.get("reason", "unavailable")),
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
        "checks": checks,
        "container": container,
        "container_method": method,
        "containers": containers,
        "cgroup": caps,
        "target_probe": p,
        "health_probe": hp,
        "host_free_gb": free,
        "checked_utc": utc(),
    }


def environment_info(cfg, pf=None):
    pf = pf or {}
    docker_ver = dp.docker_available().get("server_version") or "not available"
    cg_ver = (pf.get("cgroup") or {}).get("version") or "none"
    target = cfg.get("target") or {}
    fairness = cfg.get("fairness") or {}
    return {
        "docker_server_version": docker_ver,
        "cgroup_interface": cg_ver,
        "canonical_memory_metric": ((pf.get("cgroup") or {}).get("canonical_metric")
                                    or "unavailable"),
        "container_resolution": pf.get("container_method", "unknown"),
        "host_available_ram_gb": pf.get("host_free_gb", host_free_gb()),
        "host_cpu_count": os.cpu_count(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "generated_utc": utc(),
        "fingerprint": {
            "image_digest": target.get("image_digest", ""),
            "runtime_version": target.get("runtime_version", ""),
            "cgroup_version": cg_ver,
            "docker_version": docker_ver,
            "kernel": "",
            "cpu_model": cpu_model(),
            "pinned_cpus": parse_cpuset(fairness.get("cpuset_target", "")),
            "host_shared_cpu": True,
            "generator_pinned": False,
            "seed": fairness.get("seed"),
        },
    }


def run_benchmark(cfg, root_dir, log=None, preflight_result=None,
                  bench_holder=None):
    """Execute a full run and write config.json, results.json, report.html.

    bench_holder, when supplied, receives the live Benchmark instance under
    the key "bench" so a UI can request a graceful stop mid-run.
    """
    cfg = C.normalize(cfg)
    # Never reuse a cached preflight that belongs to a different target:
    # it would instrument the wrong container.
    if (preflight_result
            and preflight_result.get("target_url") == cfg["target_url"]
            and (not cfg.get("container")
                 or preflight_result.get("container") == cfg["container"])):
        pf = preflight_result
    else:
        pf = preflight(cfg)
    container = pf.get("container") or ""
    pf["target_url"] = cfg["target_url"]       # repair older cached preflights
    mysql_container = str(cfg.get("mysql_container") or "")
    requested_mode = cfg.get("benchmark_mode", "auto")
    if requested_mode == "auto":
        mode = "docker_lab" if container else "live_blackbox"
    else:
        mode = requested_mode
    run_id = "run-" + stamp()
    run_dir = os.path.join(paths.runs_dir(root_dir, cfg), run_id)
    os.makedirs(run_dir, exist_ok=True)

    C.save_config(os.path.join(run_dir, "config.json"), cfg)
    atomic_json(os.path.join(run_dir, "preflight.json"), pf)
    if log:
        log.add("Preflight: %s" % "; ".join(
            "%s=%s" % (x["name"], "OK" if x["ok"] else "WARN")
            for x in pf["checks"]))

    bench = Benchmark(cfg, run_dir, log=log, container=container,
                      mysql_container=mysql_container,
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
        fp["collector_mode"] = bench.samplers.collector_mode
    ensure_current(
        results, mode=mode, target=cfg.get("target") or {},
        fingerprint=fp,
        has_runtime=bool(series.get("runtime_series")),
        has_browser=bool(series.get("browser_series")))
    s = results.get("summary") or {}
    http = results["metrics"]["http"]
    http["ttfb_p95_ms"] = (s.get("ttfb_all") or {}).get("p95")
    http["dispatch_delay_p95_ms"] = (s.get("dispatch_delay_ms") or {}).get("p95")
    http["as_scheduled_p95_ms"] = (s.get("as_scheduled_latency") or {}).get("p95")
    if log:
        log.add("Mode=%s visibility=%s schema=v%d collector=%s"
                % (results["benchmark_mode"], results["visibility"],
                   results["schema_version"], fp.get("collector_mode", "n/a")))

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
            rc_data = RC.run(cfg, run_dir, log=log, container=container)
            results["runtime_comparison"] = RC.summarize(rc_data)
        except Exception as ex:
            results["runtime_comparison"] = {
                "status": "error",
                "verdict": "Comparison failed: %s" % ex,
                "slowdown": {}, "warnings": [str(ex)], "artifacts": {},
            }
            if log:
                log.add("Runtime comparison failed: %s" % ex, "warn")

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
    return {"run_id": run_id, "run_dir": run_dir,
            "results_path": results_path, "report_path": report_path,
            "status_path": os.path.join(run_dir, "status.json"),
            "runtime_comparison": bool(results.get("runtime_comparison")),
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
