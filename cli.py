#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless command-line entry point for Heavy-Load Checker.

Examples:
  python cli.py --url http://localhost:8090/ --profile smoke
  python cli.py --url http://localhost:3000/ --container my-web --profile quick
  python cli.py --config my-config.json --profile standard
  python cli.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from heavyload import config as C  # noqa: E402
from heavyload import runner  # noqa: E402
from heavyload import campaign as CAM  # noqa: E402
from heavyload import compare as CMP  # noqa: E402
from heavyload import paths as HP  # noqa: E402
from heavyload.util import LogBuffer, stamp  # noqa: E402


def build_parser():
    ap = argparse.ArgumentParser(
        prog="cli.py",
        description="Check the heavy-load behavior of any HTTP website "
                    "(Docker cgroup instrumentation when available).")
    ap.add_argument("--url", help="target URL, e.g. http://localhost:8090/")
    ap.add_argument("--profile", choices=sorted(C.PRESETS),
                    help="schedule preset")
    ap.add_argument("--container", help="Docker container name (auto-detect "
                                        "from published port when omitted)")
    ap.add_argument("--mysql-container", help="optional MySQL container to sample")
    ap.add_argument("--mode", choices=("auto", "docker", "direct"),
                    help="instrumentation mode (container resolution)")
    ap.add_argument("--benchmark-mode",
                    choices=("auto", "docker_lab", "live_blackbox",
                             "instrumented_live"),
                    help="measurement vantage point; auto = docker_lab when a "
                         "container resolves, else client-only live_blackbox")
    ap.add_argument("--site", help="apply a targets/<site-id>.json spec "
                                   "(language, image, limits, routes, adapter)")
    ap.add_argument("--runtime-adapter",
                    help="collect runtime internals: local_python, python_gc, "
                         "node_v8, jvm, go, phpfpm, dotnet")
    ap.add_argument("--runtime-comparison", action="store_true",
                    help="run ONLY the cross-language runtime comparison "
                         "(no HTTP load test) and write runtime_comparison.*")
    ap.add_argument("--no-runtime-comparison", action="store_true",
                    help="skip the runtime comparison for this run")
    ap.add_argument("--runtime-repeats", type=int,
                    help="in-process repeats per variant (default 5, median)")
    ap.add_argument("--browser", action="store_true",
                    help="also collect browser page metrics via Chrome CDP")
    ap.add_argument("--sample-period", type=float,
                    help="cgroup collector period in seconds (0.05-10)")
    ap.add_argument("--health-path", help="health probe path, default /health")
    ap.add_argument("--paths", help="comma-separated GET route mix, e.g. /,/health,/cart")
    ap.add_argument("--path", action="append", dest="extra_paths",
                    help="add one GET route (repeatable; avoids Git-Bash "
                         "path conversion of comma-separated leading slashes)")
    ap.add_argument("--max-concurrency", type=int, help="hard cap on worker threads")
    ap.add_argument("--max-anon-mib", type=float, help="software memory guardrail")
    ap.add_argument("--min-host-free-gb", type=float,
                    help="host free RAM floor")
    ap.add_argument("--request-timeout", type=float, help="per-request timeout (s)")
    ap.add_argument("--config", help="load a saved JSON config file")
    ap.add_argument("--root", default=ROOT, help="tool root directory")
    ap.add_argument("--preflight-only", action="store_true",
                    help="run read-only checks and exit")
    ap.add_argument("--discover-routes", action="store_true",
                    help="probe common routes and print suggestions")
    ap.add_argument("--list", action="store_true", help="list previous runs")
    ap.add_argument("--include-archive", action="store_true",
                    help="with --list, also show runs archived in _archive/")
    ap.add_argument("--campaign", help="run a benchmarks/<name>.json campaign "
                                       "(batch of sites, repeated, compared)")
    ap.add_argument("--compare", help="compare existing runs: comma-separated "
                                      "run ids, e.g. run-1,run-2,run-3")
    ap.add_argument("--list-campaigns", action="store_true",
                    help="list previous campaigns under benchmarks/")
    ap.add_argument("--output", help="output directory/file for --compare "
                                     "or --campaign export")
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    cfg = {}
    if a.config:
        with open(a.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    elif os.path.isfile(os.path.join(a.root, "config.json")):
        cfg = C.load_config(os.path.join(a.root, "config.json"))

    if a.list:
        rows = runner.list_runs(a.root, include_archive=a.include_archive)
        if not rows:
            print("No runs yet.")
        for r in rows:
            print("%-22s %-9s %-8s %-42s %s"
                  % (r["run_id"], r["profile"], r["state"],
                     r["verdict"] or "-", r["target_url"]))
        return 0

    if a.list_campaigns:
        rows = CAM.list_campaigns(a.root)
        if not rows:
            print("No campaigns yet.")
        for c in rows:
            print("%-26s %-9s %-8s %-2s sites  %s"
                  % (c["campaign_id"], c["profile"], c["generated_utc"],
                     c["sites"], "comparable" if c["comparable"] else "MIXED"))
        return 0

    if a.campaign:
        spec, serr = CAM.load_campaign(a.campaign)
        if serr:
            print("Invalid campaign: %s" % serr, file=sys.stderr)
            return 2
        log = LogBuffer()
        done = threading.Event()

        def printer():
            seq = 0
            while not done.is_set():
                for row in log.since(seq):
                    seq = row["seq"]
                    print("[%s] %s" % (row["ts"][11:19], row["msg"]), flush=True)
                time.sleep(0.3)

        t = threading.Thread(target=printer, daemon=True)
        t.start()
        try:
            camp = CAM.run_campaign(a.root, spec, log=log,
                                    on_event=lambda ev: log.add(
                                        "[campaign] %s" % ev.get("type")))
        finally:
            done.set()
            t.join(timeout=1)
        print("\n" + "=" * 72)
        print("Campaign : %s" % camp["campaign_id"])
        print("Comparable: %s" % ("YES" if camp["comparable"] else "NO"))
        print("Report   : %s" % os.path.join(camp["campaign_dir"],
                                             "report.html"))
        for r in camp["leaderboard"]:
            print("  %-14s %-8s RPS=%-7s p95=%-6s cpu%%=%-6s mem=%-6s health=%s"
                  % (r["site_id"], r.get("language", ""),
                     r.get("overall_rps"), r.get("p95_ms"),
                     r.get("cpu_peak_pct_net"), r.get("mem_anon_mib_peak_net"),
                     r.get("health_score")))
        return 0

    if a.compare:
        rids = [x.strip() for x in a.compare.split(",") if x.strip()]
        cmp = CMP.compare_runs(a.root, rids)
        out_dir = a.output or os.path.join(
            a.root, "benchmarks", "compare-" + stamp())
        os.makedirs(out_dir, exist_ok=True)
        CMP.to_csv(cmp, os.path.join(out_dir, "leaderboard.csv"))
        CMP.to_json(cmp, os.path.join(out_dir, "leaderboard.json"))
        from heavyload import report_compare
        report_compare.build(cmp, os.path.join(out_dir, "report.html"))
        print("Comparable: %s" % ("YES" if cmp["comparable"] else "NO"))
        if cmp["comparison_reasons"]:
            for r in cmp["comparison_reasons"]:
                print("  WARN: %s" % r["reason"])
        print("Report : %s" % os.path.join(out_dir, "report.html"))
        for r in cmp["leaderboard"]:
            print("  %-14s RPS=%-7s p95=%-6s cpu%%=%-6s mem=%-6s health=%s"
                  % (r["site_id"], r.get("overall_rps"), r.get("p95_ms"),
                     r.get("cpu_peak_pct_net"), r.get("mem_anon_mib_peak_net"),
                     r.get("health_score")))
        return 0

    # -- standalone cross-language runtime comparison ----------------------
    # No HTTP target is contacted, so this also works while the store is down.
    if a.runtime_comparison:
        from heavyload import runtime_comparison as RC
        rc = dict(cfg.get("runtime_comparison") or {})
        rc["enabled"] = True
        if a.runtime_repeats:
            rc["repeats"] = a.runtime_repeats
        cfg["runtime_comparison"] = rc
        norm, err = C.validate(cfg)
        if err:
            print("Configuration error: %s" % err, file=sys.stderr)
            return 2
        base = HP.runs_dir(a.root, norm)
        out_dir = a.output or os.path.join(base, "runtime-comparison")
        os.makedirs(out_dir, exist_ok=True)
        data = RC.run(norm, out_dir, log=LogBuffer(),
                      container=rc.get("container") or norm.get("container", ""))
        if not a.output:
            import shutil
            archive = os.path.join(base, "runtime-" + stamp())
            shutil.copytree(out_dir, archive, dirs_exist_ok=True)
        RC.console_summary(data)
        print("\nLatest JSON : %s"
              % os.path.join(out_dir, "runtime_comparison.json"))
        print("Latest HTML : %s"
              % os.path.join(out_dir, "runtime_comparison.html"))
        if not a.output:
            print("Archive     : %s" % archive)
        return 0 if data["status"] in ("ok", "partial") else 1

    if a.no_runtime_comparison:
        cfg.setdefault("runtime_comparison", {})["enabled"] = False
    if a.runtime_repeats:
        cfg.setdefault("runtime_comparison", {})["repeats"] = a.runtime_repeats

    if a.site:
        from heavyload import targetspec
        p = (a.site if os.path.isfile(a.site)
             else targetspec.find_spec(a.site, root=a.root))
        if not p:
            print("Site spec not found: %s" % a.site, file=sys.stderr)
            return 2
        spec, serr = targetspec.load_spec(p)
        if serr:
            print("Invalid site spec: %s" % serr, file=sys.stderr)
            return 2
        cfg = targetspec.apply_to_config(cfg, spec)
        cfg.setdefault("runtime_adapter", {})["type"] = (
            spec.get("adapter", {}).get("type") or "")
        cfg.setdefault("runtime_adapter", {})["endpoint"] = (
            spec.get("adapter", {}).get("endpoint") or "/__hlc/runtime")

    if a.url:
        cfg["target_url"] = a.url
    if a.container:
        cfg["container"] = a.container
    if a.mysql_container:
        cfg["mysql_container"] = a.mysql_container
    if a.mode:
        cfg["mode"] = a.mode
    if a.benchmark_mode:
        cfg["benchmark_mode"] = a.benchmark_mode
    if a.runtime_adapter:
        cfg.setdefault("runtime_adapter", {})["type"] = a.runtime_adapter
    if a.browser:
        cfg.setdefault("browser", {})["enabled"] = True
    if a.sample_period:
        cfg["sample_period_s"] = a.sample_period
    if a.health_path:
        cfg["health_path"] = a.health_path
    if a.paths:
        cfg["paths"] = [{"path": p.strip(), "weight": 1}
                        for p in a.paths.split(",") if p.strip()]
    if a.extra_paths:
        cfg["paths"] = [{"path": p.strip(), "weight": 1}
                        for p in a.extra_paths if p.strip()]
    if a.max_concurrency:
        cfg["max_concurrency"] = a.max_concurrency
    if a.request_timeout:
        cfg["request_timeout_s"] = a.request_timeout
    if a.profile:
        cfg = C.apply_profile(cfg, a.profile)
    g = dict(cfg.get("guardrails") or {})
    if a.max_anon_mib:
        g["max_anon_mib"] = a.max_anon_mib
    if a.min_host_free_gb:
        g["min_host_free_gb"] = a.min_host_free_gb
    if g:
        cfg["guardrails"] = g

    norm, err = C.validate(cfg)
    if err:
        print("Configuration error: %s" % err, file=sys.stderr)
        return 2

    if a.preflight_only or a.discover_routes:
        pf = runner.preflight(norm)
        for c in pf["checks"]:
            print("[%s] %-34s %s" % ("OK" if c["ok"] else "WARN",
                                     c["name"], c["detail"]))
        if a.discover_routes:
            print("\nSuggested routes:")
            for r in runner.discover_routes(norm):
                print("  %-34s %-4s %-7s %6d B  %6.1f ms %s"
                      % (r["path"], r["status"], "INCLUDE" if r["include"]
                         else "skip", r["bytes"], r["latency_ms"], r["error"]))
        return 0

    log = LogBuffer()

    def printer():
        seq = 0
        while not done.is_set():
            for row in log.since(seq):
                seq = row["seq"]
                print("[%s] %s" % (row["ts"][11:19], row["msg"]), flush=True)
            time.sleep(0.3)

    done = threading.Event()
    t = threading.Thread(target=printer, daemon=True)
    t.start()
    try:
        info = runner.run_benchmark(norm, a.root, log=log)
        C.save_config(os.path.join(a.root, "config.json"), norm)
    finally:
        done.set()
        t.join(timeout=1)

    print("\n" + "=" * 72)
    print("Run ID     : %s" % info["run_id"])
    print("Verdict    : %s" % info["verdict"])
    print("Health     : %d/100" % info["health_score"])
    print("Report     : %s" % info["report_path"])
    print("Results    : %s" % info["results_path"])
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
