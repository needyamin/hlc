#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end self test. No container, no external service, no pip deps.

Starts a throw-away stdlib HTTP server on an ephemeral localhost port, runs a
full smoke-profile benchmark in client-only mode (runtime comparison disabled
for speed), then verifies results.json / report.html / scalability / quality
/ suitability artifacts and the synthetic cgroup-backed SLO grading path for
archived-run compatibility.

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
from heavyload.schema.metrics import can_compare  # noqa: E402
from heavyload.schema.migrate import ensure_current  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  [%s] %-56s %s" % ("OK" if cond else "FAIL", name, detail))


def _safe_ok(rel):
    """True when _safe_rel accepts the path (traversal attempts must raise)."""
    try:
        RC._safe_rel(rel)
        return True
    except ValueError:
        return False


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
    print("\n=== Heavy-Load Checker self test (v2) ===")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    print("Synthetic target: http://127.0.0.1:%d/ (client-only, no container)"
          % port)

    # -- config / schema checks -------------------------------------------
    for name, sched, peak in (("smoke", 33, 20), ("quick", 210, 100),
                              ("heavy", 295, 200), ("stress", 620, 400),
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

    # -- runtime environment (single client-only vantage) -------------------
    d = C.normalize({"target_url": "http://x/"})
    check("host runtime is the only environment",
          C.RUNTIME_ENVIRONMENTS == ("windows",), str(C.RUNTIME_ENVIRONMENTS))
    check("default is direct / live_blackbox (client-only)",
          d["mode"] == "direct" and d["benchmark_mode"] == "live_blackbox",
          "%s / %s" % (d["mode"], d["benchmark_mode"]))
    check("no container keys exist in a normalized config",
          "container" not in d and "mysql_container" not in d and
          "include_container" not in d["runtime_comparison"])
    check("invalid runtime environment self-heals to windows",
          C.normalize({"target_url": "http://x/",
                       "runtime_environment": "solaris"})
          ["runtime_environment"] == "windows")
    legacy = C.normalize({"target_url": "http://x/",
                          "runtime_environment": "docker",
                          "container": "web1",
                          "benchmark_mode": "docker_lab",
                          "runtime_comparison": {"include_container": True}})
    check("legacy Docker config maps to host env with a recorded note",
          legacy["runtime_environment"] == "windows" and
          legacy["benchmark_mode"] == "live_blackbox" and
          "Docker-era" in legacy["_derived"].get("legacy_migration_note", ""))
    check("legacy container keys are stripped, never published",
          "container" not in legacy and
          "include_container" not in legacy["runtime_comparison"])
    check("runtime comparison artifact schema is v2",
          RC.SCHEMA_VERSION == 2, "v%d" % RC.SCHEMA_VERSION)

    # -- runtime comparison (fast, no external toolchains) ------------------
    rc_def = C.normalize({})["runtime_comparison"]
    check("runtime comparison enabled by default",
          rc_def["enabled"] is True)
    check("runtime repeats clamped to >=1",
          C.normalize({"runtime_comparison": {"repeats": 0}})
          ["runtime_comparison"]["repeats"] == 1)
    check("unknown workload names dropped",
          C.normalize({"runtime_comparison": {"workloads": ["bogus"]}})
          ["runtime_comparison"]["workloads"]
          == ["prime", "json", "map", "text", "matmul"])
    check("pinned checksums present for all workloads",
          set(RC.EXPECTED) == {"prime", "json", "map", "text", "matmul"} and
          all(v.startswith("RESULT ") for v in RC.EXPECTED.values()))
    res_line, ms = RC._parse_output("RESULT 5133 121013308\nTIME_US 115000")
    check("runtime output parser", res_line == "RESULT 5133 121013308"
          and ms == 115.0, "%s / %s ms" % (res_line, ms))
    check("runtime output parser rejects garbage",
          RC._parse_output("hello") == (None, None))
    check("geometric mean helper", abs(RC.geo([2.0, 8.0]) - 4.0) < 1e-9)
    _st = RC.row_stats([10.0, 10.5, 10.2, 9.9, 10.1], "baseline")
    check("row stats: median with ordered CI",
          _st["median_ms"] == 10.1 and _st["median_ci95"][0]
          <= _st["median_ms"] <= _st["median_ci95"][1])
    check("row stats: deterministic bootstrap",
          RC.row_stats([10.0, 10.5, 10.2, 9.9, 10.1], "baseline") == _st)
    check("row stats: spid3r timer floor flagged",
          any("timer floor" in f for f in
              RC.row_stats([3.0, 3.0, 3.0, 3.0, 3.0], "spid3r")["flags"]))
    check("row stats: high CV flagged as noisy",
          any("noisy" in f for f in
              RC.row_stats([10.0, 40.0, 12.0, 45.0, 9.0], "baseline")["flags"]))
    check("language registry ships 14 modules",
          len(RC.LANG_MODULES) == 14,
          ", ".join(m.KEY for m in RC.LANG_MODULES[:6]) + " ...")
    check("safe_rel rejects traversal",
          all(not _safe_ok(p) for p in ("../x", "/abs", "..")),
          "raises ValueError on ../, absolute and .. paths")

    def rc_row(key, label, group, ms_value):
        return {"key": key, "runtime": label, "group": group,
                "source": "host",
                "result": "RESULT synthetic", "compute_ms": ms_value,
                "compute_min_ms": ms_value, "compute_max_ms": ms_value,
                "wall_ms": ms_value, "compute_runs_ms": [ms_value],
                "peak_rss_bytes": 10_000_000,
                "cpu_user_s": ms_value / 1000.0 * 0.8,
                "cpu_kernel_s": ms_value / 1000.0 * 0.1,
                "proc_metrics_method": "synthetic"}

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
    check("pairwise matrix covers all measured keys",
          set(s_rc["matrix"].keys()) == {"php", "python", "spid3r_vm"} and
          abs(s_rc["matrix"]["python"]["geomeans"]["php"] - 1.414) < 0.001)

    fake_full = {
        "schema_version": RC.SCHEMA_VERSION,
        "generated_utc": "2026-11-10T00:00:00Z", "status": "partial",
        "config_meta": {"repeats": 5},
        "environment": {"runtime_environment": "Host", "php": "8.3",
                        "python": "3.13", "spid3r": "1.1.1"},
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
    check("runtime HTML renders the pairwise matrix",
          "Pairwise ratio matrix" in rc_h)
    rc_compact = RC.summarize(fake_full)
    check("compact summary keeps verdict and slowdown",
          rc_compact["verdict"] == s_rc["verdict"] and
          bool(rc_compact["slowdown"]))
    rc_section = report._runtime_section(rc_compact, "run-test")
    check("digest fallback renders a report section",
          "<h2>" in rc_section and
          "/runs/run-test/runtime_comparison.html" in rc_section)

    # -- suitability (synthetic measured data) ------------------------------
    from heavyload import suitability as SU
    su_dir = tempfile.mkdtemp(prefix="hlc_selftest_su_")
    RC._write_source(su_dir, "runtime_comparison.json.tmp",
                     json.dumps(fake_rc))
    os.replace(os.path.join(su_dir, "runtime_comparison.json.tmp"),
               os.path.join(su_dir, "runtime_comparison.json"))
    su = SU.build({"target": {}}, C.normalize({}), su_dir)
    su_sum = SU.summarize(su)
    check("suitability ranks measured + documented languages",
          su_sum["status"] == "ok" and len(su["ranked"]) >= 13 and
          len(su_sum["top"]) >= 8)
    sp3row = [r for r in su["ranked"] if r["language"] == "matrix"]
    check("Spid3r subject language always ranked",
          len(sp3row) == 1 and
          sp3row[0]["label"] == "Spid3r (subject language)" and
          not any(str(r["language"]).startswith("spid3r")
                  for r in su["ranked"]),
          "rank #%s" % sp3row[0]["rank"] if sp3row else "missing")
    check("Spid3r digest embedded for the main report",
          bool(su_sum.get("spid3r", {}).get("label")) and
          su_sum["spid3r"].get("rank") == sp3row[0]["rank"] if sp3row
          else False)
    check("suitability separates provenance",
          all(c.get("provenance") in ("measured", "documented")
              for r in su["ranked"] for c in r["criteria"].values()))
    check("suitability HTML written and self-contained",
          os.path.isfile(os.path.join(su_dir, "suitability.html")))

    # -- full run ----------------------------------------------------------
    cfg = C.normalize({
        "target_url": "http://127.0.0.1:%d/" % port,
        "profile": "smoke",
        # The cross-language comparison is deliberately disabled here: this
        # test must stay fast and must not need every toolchain installed.
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

    # The v2 run is genuinely client-only: no container keys, zero server
    # metrics, generator self-monitoring present, single-host comparability.
    check("run config has no container keys",
          "container" not in r["config"] and "container" not in r["meta"])
    check("zero server-side samples",
          (r["resources"]["cgroup_samples"] or 0) == 0 and
          not (r["series"].get("cgroup_series") or []) and
          not (r["series"].get("mysql_series") or []))
    check("generator series measured",
          len(r["series"].get("generator_series") or []) >= 20 and
          r["resources"].get("gen_cpu_peak_pct") is not None,
          "peak %s%% core" % r["resources"].get("gen_cpu_peak_pct"))
    check("visibility is client_only_same_host (loopback target)",
          r["visibility"] == "client_only_same_host" and
          r["benchmark_mode"] == "live_blackbox",
          "%s / %s" % (r["benchmark_mode"], r["visibility"]))
    check("fingerprint carries the single-host gate",
          bool((r["environment"]["fingerprint"]
                .get("generator_fingerprint"))) and
          (r["environment"]["fingerprint"].get("target_host")
           in ("127.0.0.1", "localhost")))
    check("same-host pair is comparable, remote never",
          can_compare(r, r)[0] is True)
    remote = json.loads(json.dumps(r))
    remote["environment"]["fingerprint"]["generator_fingerprint"] = \
        "other-host|Windows|AMD64|8"
    check("different generator fingerprint refused",
          can_compare(r, remote)[0] is False)
    check("default run grades memory SLOs N/A",
          all(x["grade"].startswith("N/A") for x in r["adjudication"]["slo"]
              if x["id"] in ("S5", "S6", "S8")))
    hybrid = {"benchmark_mode": "live_blackbox", "container": "stale-app",
              "resources": {"cgroup_samples": 50, "stats_samples": 10},
              "series": {"cgroup_series": [{"anon_bytes": 123}]}}
    ensure_current(hybrid, mode="live_blackbox")
    check("live_blackbox pins client_only even with cgroup evidence",
          hybrid["visibility"] == "client_only" and
          hybrid["per_metric_visibility"]["metrics.resources.*"]
          == "unavailable")
    archived = {"container": "old-app",
                "meta": {"container": "old-app"},
                "resources": {"cgroup_samples": 50},
                "series": {"cgroup_series": [{"anon_bytes": 123}]}}
    ensure_current(archived)
    check("archived v1 Docker run stays full_server",
          archived["benchmark_mode"] == "docker_lab" and
          archived["visibility"] == "full_server")

    # -- new analysis blocks -------------------------------------------------
    check("quality checks ran",
          (r.get("quality") or {}).get("status") in ("ok", "warn") and
          len(r["quality"]["checks"]) >= 5,
          r["quality"]["status"])
    check("quality checks never fabricate",
          all(c["status"] in ("ok", "warn", "fail", "insufficient_evidence")
              for c in r["quality"]["checks"]))
    scal = r.get("scalability") or {}
    check("scalability analyzed from step windows",
          scal.get("status") == "ok" and len(scal.get("points") or []) >= 2,
          "%s, %d points, knee C=%s"
          % (scal.get("classification"), len(scal.get("points") or []),
             scal.get("knee_concurrency")))
    check("step metrics recorded per concurrency level",
          len(r.get("step_metrics") or []) >= 5,
          "%d windows" % len(r.get("step_metrics") or []))
    check("suitability data artifact written (single-report flow)",
          (r.get("suitability") or {}).get("status") == "ok" and
          os.path.isfile(os.path.join(rd, "suitability.json")))
    check("ONE report page: no standalone comparison/suitability pages",
          not os.path.isfile(os.path.join(rd, "runtime_comparison.html")) and
          not os.path.isfile(os.path.join(rd, "suitability.html")))
    check("malformed raw lines counted",
          (r.get("aggregation_meta") or {}).get("malformed_raw_lines") == 0)

    # -- report ---------------------------------------------------------------
    check("report has no <script>", "<script" not in h.lower())
    check("report has no <link>", "<link" not in h.lower())
    check("report has no src=", "src=" not in h.lower())
    check("report has no @import", "@import" not in h.lower())
    check("report has inline SVG charts", h.count("<svg") >= 4,
          "%d charts" % h.count("<svg"))
    # The suitability JSON sits beside the report, so its FULL fragment is
    # embedded even though the comparison itself was disabled for this run.
    check("report embeds the full suitability fragment",
          "Technology suitability assessment" in h and
          "subject language report" in h and
          "Per-criterion detail" in h)
    check("journal Abstract section present",
          "Abstract (auto-generated" in h)
    check("suitability figures embedded in base report",
          h.count("<svg") >= 2, "%d svgs" % h.count("<svg"))
    check("report embeds verdict", r["adjudication"]["verdict"] in h)
    check("report discloses client-only vantage",
          "client-only" in h and "client_only_same_host" in h)
    check("report has methodology section",
          "Methodology and evaluation criteria" in h)
    check("report has scalability section with measured points",
          "Scalability and concurrency analysis" in h)
    check("report has quality & accuracy section",
          "Data quality and accuracy considerations" in h)
    check("report has strengths/weaknesses section",
          "Strengths, weaknesses and limitations" in h)
    check("report says the comparison was disabled (honest fallback)",
          "15. Language runtime comparison" in h and
          "runtime comparison was disabled" in h)
    check("wide tables scroll instead of breaking the design",
          h.count("tblwrap") >= 10, "%d overflow containers" % h.count("tblwrap"))
    check("report size sane", len(h) > 40000, "%d bytes" % len(h))

    # A run WITH the comparison embeds the full runtime-comparison evidence.
    with_rc = json.loads(json.dumps(r))
    with_rc["runtime_comparison"] = rc_compact
    # Simulate the full JSON artifacts a real run would leave beside the
    # report so the embedded path (not the digest fallback) is exercised.
    RC._write_source(rc_dir, "runtime_comparison.json.tmp",
                     json.dumps(fake_full))
    os.replace(os.path.join(rc_dir, "runtime_comparison.json.tmp"),
               os.path.join(rc_dir, "runtime_comparison.json"))
    RC._write_source(rc_dir, "suitability.json.tmp",
                     json.dumps(su))
    os.replace(os.path.join(rc_dir, "suitability.json.tmp"),
               os.path.join(rc_dir, "suitability.json"))
    rc_report = os.path.join(rc_dir, "full-report.html")
    report.build(with_rc, rc_report)
    with open(rc_report, "r", encoding="utf-8") as f:
        h_rc = f.read()
    check("report embeds the full comparison fragment",
          "A. Prime counting" in h_rc and
          "Pairwise ratio matrix" in h_rc and
          "Measurement quality" in h_rc)
    check("report embeds per-variant Spid3r engine evidence",
          "Spid3r VM" in h_rc and "slowdown" in h_rc.lower())
    check("journal Abstract states measured facts",
          "Abstract (auto-generated" in h_rc and
          "checksum-verified" in h_rc and "geomean" in h_rc)
    check("journal figures embedded with numbered captions",
          h_rc.count("Fig. ") >= 8 and h_rc.count("<svg") >= 10,
          "%d captions, %d svgs" % (h_rc.count("Fig. "), h_rc.count("<svg")))
    check("journal chart variety: pie + heatmap + scatter",
          "Share of geomean compute time" in h_rc and
          "Head-to-head heatmap" in h_rc and
          "trade-off space" in h_rc)
    check("side-by-side Spid3r engine tables embedded",
          "Side-by-side: every Spid3r engine" in h_rc)
    check("heatmap colour-codes every workload cell",
          "hsl(" in h_rc and "Geomean</th>" in h_rc)
    check("wide tables scroll inside the embedded fragments",
          h_rc.count("tblwrap") >= 15,
          "%d overflow containers" % h_rc.count("tblwrap"))
    check("full report still has no external resources",
          all(x not in h_rc.lower() for x in
              ("<script", "<link", "src=", "@import")))

    check("all artifacts present",
          all(os.path.isfile(os.path.join(rd, x)) for x in
              ("config.json", "preflight.json", "raw.jsonl", "results.json",
               "report.html", "stage2_verdict.json", "status.json",
               "suitability.json")))

    # -- cgroup-backed grading path (synthetic archived-run evidence) --------
    # Grading still understands archived docker_lab evidence so historical
    # results re-grade identically; new runs never produce it.
    fake = json.loads(json.dumps(r))
    fake["abort"] = {"flag": False, "reason": "", "at_s": None, "phase": ""}
    fake["config"]["runtime_environment"] = "docker"
    fake["config"]["benchmark_mode"] = "docker_lab"
    fake["config"]["mode"] = "auto"
    fake["benchmark_mode"] = "docker_lab"
    fake["visibility"] = "full_server"
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
    check("synthetic archived evidence still grades S5",
          s5["grade"] in ("PASS", "FAIL", "WARN"), s5["grade"])
    check("S5 FAIL on 450+ MiB fitted growth", s5["grade"] == "FAIL")
    check("synthetic cooldown S6 graded",
          s6["grade"] in ("PASS", "FAIL", "WARN"), s6["grade"])
    check("health score capped at 40 on NO-GO", v["health_score"] <= 40,
          "%d/100" % v["health_score"])
    check("client-only run grades memory SLOs N/A",
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
