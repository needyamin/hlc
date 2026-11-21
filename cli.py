#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless command-line entry point for Heavy-Load Checker.

Examples:
  python cli.py --url http://localhost:8090/ --profile smoke
  python cli.py --profile heavy                  # heavy load, ramp to C=200
  python cli.py --profile stress                 # stress conditions
  python cli.py --config my-config.json --profile standard
  python cli.py --runtime-comparison             # language benchmark only
  python cli.py --verify-sources                 # checksum-verify every
                                                 # detected toolchain
  python cli.py --list

Every run is client-only from the host (vantage=client_only): latency,
throughput and error-rate are real observations; server-side CPU/memory are
never visible from outside and are never estimated.
"""
from __future__ import annotations

import argparse
import pathlib
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
        description="Check the heavy-load behavior of any HTTP website. "
                    "Every run is a client-only measurement from this host; "
                    "the cross-language runtime comparison automatically "
                    "covers every installed toolchain.")
    ap.add_argument("--url", help="target URL, e.g. http://localhost:8090/")
    ap.add_argument("--profile", choices=sorted(C.PRESETS),
                    help="schedule preset: smoke, quick, heavy, stress, "
                         "standard")
    ap.add_argument("--site", help="apply a targets/<site-id>.json spec "
                                   "(language, routes, adapter, native start)")
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
    ap.add_argument("--verify-sources", action="store_true",
                    help="checksum-verify every DETECTED language toolchain "
                         "against the pinned EXPECTED results and print a "
                         "verification table (no HTTP load test)")
    ap.add_argument("--browser", action="store_true",
                    help="also collect browser page metrics via Chrome CDP")
    ap.add_argument("--sample-period", type=float,
                    help="generator/health sampler period in seconds (0.05-10)")
    ap.add_argument("--health-path", help="health probe path, default /health")
    ap.add_argument("--paths", help="comma-separated GET route mix, e.g. /,/health,/cart")
    ap.add_argument("--path", action="append", dest="extra_paths",
                    help="add one GET route (repeatable; avoids Git-Bash "
                         "path conversion of comma-separated leading slashes)")
    ap.add_argument("--max-concurrency", type=int, help="hard cap on worker threads")
    ap.add_argument("--max-anon-mib", type=float,
                    help="generator working-set guardrail (MiB)")
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


def _verify_sources(cfg):
    """Checksum-verify every detected toolchain; prints a table.

    Exit 0 iff every measured variant matched (missing toolchains and
    not-applicable workloads do not fail the check; a REJECTED checksum does).
    """
    from heavyload import runtime_comparison as RC
    import shutil as _sh

    norm, err = C.validate(cfg)
    if err:
        print("Configuration error: %s" % err, file=sys.stderr)
        return 2
    opts = dict(norm.get("runtime_comparison") or {})
    tools = RC.detect_tools(norm, opts)
    mods = RC._available_modules(tools, opts)
    work = tempfile_workdir()
    print("Detected language modules: %s" % (", ".join(m.KEY for m in mods)
                                             or "none"))
    print("Workdir: %s\n" % work)
    n_pass = n_fail = n_skip = 0
    try:
        for wid, info in RC.WORKLOAD_INFO.items():
            print("%s. %s" % (info["id"], info["title"]))
            print("  expected: %s" % info["expected"])
            for mod in mods:
                src = mod.SOURCES.get(wid)
                if src is None:
                    note = (mod.NOTE or "no source").split(".")[0][:46]
                    print("  %-30s not applicable - %s" % (mod.KEY, note))
                    n_skip += 1
                    continue
                RC._write_source(work, mod.filename(wid), src)
                built = RC._build_for_module(wid, wid, mod, work, tools,
                                             dict(opts), [], 900)
                variants = RC._variants_for_module(wid, wid, mod, work, tools,
                                                   built, {}, [])
                for var in variants:
                    m = RC._measure(var["cmd"], cwd=work, timeout=120)
                    if m["rc"] != 0:
                        print("  %-30s FAIL rc=%s (%s)"
                              % (var["label"][:30], m["rc"],
                                 RC._first_line(m["stderr"])[:60]))
                        n_fail += 1
                        continue
                    res, cms = RC._parse_output(m["stdout"])
                    if res == info["expected"]:
                        rss = "n/a"
                        if m.get("peak_rss_bytes"):
                            rss = "%d MiB" % (m["peak_rss_bytes"] // 1048576)
                        print("  %-30s PASS  %8.1f ms  rss=%s"
                              % (var["label"][:30], cms, rss))
                        n_pass += 1
                    else:
                        print("  %-30s REJECTED checksum %r != %r"
                              % (var["label"][:30], res, info["expected"]))
                        n_fail += 1
            print("")
    finally:
        _sh.rmtree(work, ignore_errors=True)
    print("Verify summary: %d PASS, %d FAIL, %d not-applicable/skipped"
          % (n_pass, n_fail, n_skip))
    return 0 if (n_fail == 0 and n_pass > 0) else 1


def tempfile_workdir():
    import tempfile
    return tempfile.mkdtemp(prefix="hlc-verify-")


def main(argv=None):
    a = build_parser().parse_args(argv)
    cfg = {}
    if a.config:
        cfg = json.loads(pathlib.Path(a.config).read_text(encoding="utf-8"))
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

    if a.verify_sources:
        return _verify_sources(cfg)

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
    # No HTTP target is contacted, so this also works while the target is down.
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
        # ONE page even in standalone mode: the comparison report embeds the
        # suitability assessment (Spid3r subject-language section included).
        data = RC.run(norm, out_dir, log=LogBuffer(), write_html=False)
        extra = ""
        try:
            from heavyload import suitability as SU
            su = SU.build({"target": {}}, norm, out_dir, write_html=False)
            extra = SU.build_body(su)
        except Exception as ex:
            print("Suitability unavailable: %s" % ex, file=sys.stderr)
        if not a.output:
            import shutil
            archive = os.path.join(base, "runtime-" + stamp())
            shutil.copytree(out_dir, archive, dirs_exist_ok=True)
        RC.build_html(data, os.path.join(out_dir, "runtime_comparison.html"),
                      extra_sections=extra)
        RC.console_summary(data)
        print("\nLatest JSON : %s"
              % os.path.join(out_dir, "runtime_comparison.json"))
        print("Latest HTML : %s  (comparison + suitability, one page)"
              % os.path.join(out_dir, "runtime_comparison.html"))
        if not a.output:
            print("Archive     : %s" % archive)
        return 0 if data["status"] in ("ok", "partial") else 1

    if a.no_runtime_comparison:
        cfg.setdefault("runtime_comparison", {})["enabled"] = False
    if a.runtime_repeats:
        rc = cfg.setdefault("runtime_comparison", {})
        rc["repeats"] = a.runtime_repeats
        # Tuning repeats is an explicit request for the comparison; never let
        # a persisted enabled=false from an earlier --no-runtime-comparison
        # run silently swallow it.
        rc["enabled"] = True

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
    except RuntimeError as ex:
        done.set()
        t.join(timeout=1)
        print("\nRUN REFUSED: %s" % ex, file=sys.stderr)
        return 3
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
