#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end self test. No Docker container is created or modified.

Starts a throw-away stdlib HTTP server on an ephemeral localhost port, runs a
full smoke-profile benchmark in direct mode, then verifies the generated
results.json / report.html and exercises the cgroup-backed SLO grading path
with synthetic evidence.

    python selftest.py
"""
from __future__ import annotations

import http.server
import json
import os
import re
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from heavyload import config as C  # noqa: E402
from heavyload import grading, runner, report  # noqa: E402
from heavyload import runtime_comparison as RC  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %-56s %s" % ("OK" if cond else "FAIL", name, detail))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/slow":
            time.sleep(0.12)
            body, code = b"slow-ok", 200
        elif self.path == "/health":
            body, code = b'{"ok":true}', 200
        elif self.path == "/":
            body, code = b"<html><body>heavy-load-checker selftest</body></html>", 200
        elif self.path == "/cart":
            body, code = b"cart", 200
        else:
            body, code = b"not found", 404
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    print("\n=== Heavy-Load Checker self test ===")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    print("Synthetic target: http://127.0.0.1:%d/ (direct mode, no Docker)" % port)

    # -- config / schema checks -------------------------------------------
    for name, sched, peak in (("smoke", 33, 20), ("quick", 210, 100),
                              ("standard", 1435, 300)):
        c = C.normalize({"target_url": "http://127.0.0.1:%d/" % port,
                         "profile": name})
        check("preset %s schedule/peak" % name,
              c["_derived"]["schedule_s"] == sched and
              c["_derived"]["peak_concurrency"] == peak,
              "%ds / C=%d" % (c["_derived"]["schedule_s"],
                              c["_derived"]["peak_concurrency"]))
    _, err = C.validate({"target_url": "http://x/",
                         "paths": [{"path": "C:/Program Files/Git/", "weight": 1}]})
    check("reject filesystem-like route", err is not None,
          (err or "")[:52])
    _, err2 = C.validate({"target_url": "ftp://x/"})
    check("reject non-HTTP scheme", err2 is not None)

    # -- runtime comparison (fast, no external toolchains) ----------------
    rc_def = C.normalize({})["runtime_comparison"]
    check("runtime comparison enabled by default",
          rc_def["enabled"] is True, "status=%s" % rc_def["enabled"])
    check("runtime repeats clamped to >=1",
          C.normalize({"runtime_comparison": {"repeats": 0}})
          ["runtime_comparison"]["repeats"] == 1)
    check("unknown workload names dropped",
          C.normalize({"runtime_comparison": {"workloads": ["bogus"]}})
          ["runtime_comparison"]["workloads"] == ["prime", "json", "map"])
    check("pinned checksums present for all workloads",
          set(RC.EXPECTED) == {"prime", "json", "map"} and
          all(v.startswith("RESULT ") for v in RC.EXPECTED.values()))
    res_line, ms = RC._parse_output("RESULT 5133 121013308\nTIME_US 115000")
    check("runtime output parser", res_line == "RESULT 5133 121013308"
          and ms == 115.0, "%s / %s ms" % (res_line, ms))
    check("runtime output parser rejects garbage",
          RC._parse_output("hello") == (None, None))
    check("geometric mean helper", abs(RC.geo([2.0, 8.0]) - 4.0) < 1e-9)

    def rc_row(key, label, group, ms_value):
        return {"key": key, "runtime": label, "group": group,
                "result": "RESULT synthetic", "compute_ms": ms_value,
                "compute_min_ms": ms_value, "compute_max_ms": ms_value,
                "wall_ms": ms_value, "compute_runs_ms": [ms_value]}

    wl = {
        "prime": {"id": "A", "title": "Prime counting",
                  "constants": "N = 50,000", "description": "synthetic",
                  "expected_checksum": RC.EXPECTED["prime"],
                  "verified": True, "variants": 3, "rows": [
                      rc_row("php", "PHP 8.3", "baseline", 10.0),
                      rc_row("python", "Python 3.13", "baseline", 20.0),
                      rc_row("spid3r_vm", "Spid3r VM", "spid3r", 70.0)]},
        "json": {"id": "B", "title": "JSON round-trip",
                 "constants": "200 x 600", "description": "synthetic",
                 "expected_checksum": RC.EXPECTED["json"],
                 "verified": True, "variants": 3, "rows": [
                     rc_row("php", "PHP 8.3", "baseline", 50.0),
                     rc_row("python", "Python 3.13", "baseline", 50.0),
                     rc_row("spid3r_vm", "Spid3r VM", "spid3r", 100.0)]},
        "map": {"id": "C", "title": "Map lookups", "constants": "n/a",
                "description": "synthetic", "expected_checksum": RC.EXPECTED["map"],
                "verified": False, "variants": 0, "rows": []},
    }
    fake_rc = {"workloads": wl, "warnings": []}
    s_rc = RC.summarize_data(fake_rc)
    check("slowdown ignores unverified workloads",
          s_rc["workloads_verified"] == 2 and
          set(s_rc["slowdown"]["spid3r_vm"]["vs_php"]["workloads"])
          == {"prime", "json"})
    check("slowdown ratio computed correctly",
          abs(s_rc["slowdown"]["spid3r_vm"]["vs_php"]
              ["workloads"]["prime"] - 7.0) < 1e-9)
    check("geomean slowdown computed correctly",
          abs(s_rc["slowdown"]["spid3r_vm"]["vs_php"]["geomean"]
              - 3.742) < 0.001,
          "%.3fx" % s_rc["slowdown"]["spid3r_vm"]["vs_php"]["geomean"])
    check("verdict reports slower, not faster",
          "slower" in s_rc["verdict"], s_rc["verdict"][:48])
    fake_full = {
        "schema_version": RC.SCHEMA_VERSION,
        "generated_utc": "2026-11-10T00:00:00Z", "status": "partial",
        "config_meta": {"repeats": 5},
        "environment": {"php": "8.3", "python": "3.13",
                        "spid3r_host": "1.0.1"},
        "workloads": wl, "summary": s_rc,
        "warnings": ["synthetic warning"], "artifacts": {
            "json": "runtime_comparison.json",
            "html": "runtime_comparison.html", "sources": "sources/"},
    }
    rc_dir = tempfile.mkdtemp(prefix="hlc_selftest_rc_")
    rc_html = os.path.join(rc_dir, "runtime_comparison.html")
    RC.build_html(fake_full, rc_html)
    with open(rc_html, "r", encoding="utf-8") as f:
        rc_h = f.read()
    check("runtime HTML is self-contained",
          all(x not in rc_h.lower() for x in
              ("<script", "<link", "src=", "@import")))
    check("runtime HTML embeds verdict + checksum + warning",
          "slower" in rc_h and RC.EXPECTED["prime"] in rc_h
          and "synthetic warning" in rc_h)
    rc_compact = RC.summarize(fake_full)
    check("compact summary keeps verdict and slowdown",
          rc_compact["verdict"] == s_rc["verdict"] and
          bool(rc_compact["slowdown"]))
    rc_section = report._runtime_section(rc_compact, "run-test")
    check("main report gains section 11",
          "<h2>" in rc_section and
          "/runs/run-test/runtime_comparison.html" in rc_section)

    # -- full run ----------------------------------------------------------
    cfg = C.normalize({
        "target_url": "http://127.0.0.1:%d/" % port,
        "mode": "direct",
        "profile": "smoke",
        # The cross-language comparison is deliberately disabled here: this
        # test must stay fast and must not need Spid3r/PHP/Docker installed.
        "runtime_comparison": {"enabled": False},
        "health_path": "/health",
        "paths": [{"path": "/", "weight": 4},
                  {"path": "/health", "weight": 2},
                  {"path": "/cart", "weight": 1},
                  {"path": "/slow", "weight": 1}],
    })
    print("\nRunning smoke benchmark (~33 s) ...")
    info = runner.run_benchmark(cfg, ROOT)
    rd = info["run_dir"]

    with open(info["results_path"], "r", encoding="utf-8") as f:
        r = json.load(f)
    with open(info["report_path"], "r", encoding="utf-8") as f:
        h = f.read()

    s = r["summary"]
    check("run produced requests", s["total_requests"] > 100,
          "%d requests" % s["total_requests"])
    check("no guardrail abort", not r["abort"]["flag"],
          r["abort"]["reason"] or "full schedule completed")
    check("all 8 phases executed", len(r["executed_phases"]) == 8,
          ", ".join(r["executed_phases"]))
    check("health probes recorded", (r["resources"]["health_probes"] or 0) > 5,
          "%d probes" % r["resources"]["health_probes"])
    check("latency percentiles computed",
          s["PRIMARY_successful_200_only"]["p99"] > 0,
          "p99=%.2f ms" % s["PRIMARY_successful_200_only"]["p99"])
    check("timeline bins produced", len(r["timeline"]) >= 3,
          "%d bins" % len(r["timeline"]))
    check("verdict produced", bool(r["adjudication"]["verdict"]),
          r["adjudication"]["verdict"])
    check("S7 availability graded (not N/A)",
          [x for x in r["adjudication"]["slo"]
           if x["id"] == "S7"][0]["grade"] in ("PASS", "FAIL", "WARN"))

    check("report has no <script>", "<script" not in h.lower())
    check("report has no <link>", "<link" not in h.lower())
    check("report has no src=", "src=" not in h.lower())
    check("report has no @import", "@import" not in h.lower())
    check("report has inline SVG charts", h.count("<svg") >= 3,
          "%d charts" % h.count("<svg"))
    check("report has 10 sections", h.count("<h2>") == 10,
          "%d sections" % h.count("<h2>"))
    check("report embeds verdict", r["adjudication"]["verdict"] in h)
    check("report size sane", len(h) > 15000, "%d bytes" % len(h))

    # A real run WITH the comparison must gain exactly one extra section and
    # a working link to the artifact (no external resources introduced).
    with_rc = json.loads(json.dumps(r))
    with_rc["runtime_comparison"] = rc_compact
    rc_report = os.path.join(rc_dir, "full-report.html")
    report.build(with_rc, rc_report)
    with open(rc_report, "r", encoding="utf-8") as f:
        h_rc = f.read()
    check("full report has 11 sections with comparison",
          h_rc.count("<h2>") == 11, "%d sections" % h_rc.count("<h2>"))
    check("full report links runtime artifact",
          ("/runs/" + r["meta"]["run_id"] + "/runtime_comparison.html")
          in h_rc)
    check("full report still has no external resources",
          all(x not in h_rc.lower() for x in
              ("<script", "<link", "src=", "@import")))

    check("all artifacts present",
          all(os.path.isfile(os.path.join(rd, x)) for x in
              ("config.json", "preflight.json", "raw.jsonl", "results.json",
               "report.html", "stage2_verdict.json", "status.json")))

    # -- cgroup-backed grading path (synthetic evidence) -------------------
    fake = json.loads(json.dumps(r))
    fake["abort"] = {"flag": False, "reason": "", "at_s": None, "phase": ""}
    fake["meta"]["container"] = "synthetic-app"
    fake["config"]["slo"]["max_memory_growth_mib"] = 256.0
    fake["config"]["slo"]["max_retained_pct"] = 120.0
    fake["slo_targets"]["max_memory_growth_mib"] = 256.0
    fake["slo_targets"]["max_retained_pct"] = 120.0
    fake["resources"].update({
        "cgroup_samples": 150,
        "canonical_metric": "cgroup v2 memory.stat anon (raw bytes -> MiB)",
        "anon_start_mib": 100.0, "anon_peak_mib": 620.0,
        "anon_end_mib": 590.0, "anon_net_change_mib": 490.0,
        "active_points": 120,
        "active_window_s": [20.0, 200.0],
        "active_slope_mib_per_s": 2.5, "active_slope_r2": 0.94,
        "active_theil_sen_mib_per_s": 2.4, "active_spearman": 0.96,
        "gross_monotonic_fraction": 0.93,
        "cooldown_start_mib": 600.0, "cooldown_end_mib": 580.0,
        "cooldown_retained_pct": 96.7,
        "large_unmap_count": 0, "pgscan_max": 0, "pgsteal_max": 0,
        "retained_floor_bins": [{"t": i * 10, "anon_mib": 100 + i * 3}
                                for i in range(15)],
    })
    v = grading.grade(fake)
    s5 = [x for x in v["slo"] if x["id"] == "S5"][0]
    s6 = [x for x in v["slo"] if x["id"] == "S6"][0]
    check("cgroup memory S5 graded (not N/A)",
          s5["grade"] in ("PASS", "FAIL", "WARN"), s5["grade"])
    check("S5 FAIL on 450 MiB fitted growth", s5["grade"] == "FAIL")
    check("cooldown S6 graded (not N/A)",
          s6["grade"] in ("PASS", "FAIL", "WARN"), s6["grade"])
    check("health score capped at 40 on NO-GO", v["health_score"] <= 40,
          "%d/100" % v["health_score"])
    check("direct mode grades memory SLOs N/A",
          all(x["grade"].startswith("N/A") for x in r["adjudication"]["slo"]
              if x["id"] in ("S5", "S6")))

    srv.shutdown()
    srv.server_close()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for x in FAIL:
            print("  FAILED: %s" % x)
        return 1
    print("Self test PASSED. Example report: %s" % info["report_path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
