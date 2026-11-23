#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Honesty gates: no SLO may PASS without evidence, dead targets refuse to run.

Regression tests for the fix of the run-20261120-030132 defect: a benchmark
against an unreachable target graded latency SLOs "PASS 0.00 ms" and stability
"PASS" with zero observations. Published numbers must be either measured or
explicitly N/A — never a fake PASS.
"""
from __future__ import annotations

import glob
import json
import os
import tempfile
import unittest
from unittest import mock

from heavyload import config as C
from heavyload import grading, runner


def _result(n_200=0, env="windows", container="", inspect=None,
            cgroup_samples=0):
    return {
        "config": {"runtime_environment": env,
                   "mode": "direct" if env == "windows" else "auto",
                   "benchmark_mode": ("live_blackbox" if env == "windows"
                                      else "docker_lab"),
                   "runtime_comparison": {}, "phases": [], "slo": {},
                   "guardrails": {}},
        "slo_targets": {"max_error_pct": 0.1, "max_p95_ms": 500,
                        "max_p99_ms": 2000, "min_rps": 0,
                        "max_memory_growth_mib": 256, "max_retained_pct": 120,
                        "min_health_success_pct": 99},
        "summary": {"PRIMARY_successful_200_only":
                    {"p95": 0 if not n_200 else 10, "p50": 0 if not n_200 else 5,
                     "p99": 0 if not n_200 else 20, "max": 0 if not n_200 else 30,
                     "n": n_200},
                    "s1_error_pct": 100.0 if not n_200 else 0.0,
                    "s1_errors": 49 if not n_200 else 0,
                    "total_requests": 49 if not n_200 else n_200,
                    "connect_errors": 49 if not n_200 else 0,
                    "overall_rps": 1.38 if not n_200 else 10,
                    "status_counts": {}, "duration_s": 35.5},
        "resources": {"cgroup_samples": cgroup_samples,
                      "health_success_pct": 0.0 if not n_200 else 100.0,
                      "health_probes": 6},
        "abort": {"flag": False},
        "meta": {"cpu_count": 4, "container": container,
                 "target_url": "http://localhost/", "profile": "smoke",
                 "schedule_s": 33, "peak_concurrency": 10},
        "inspect_before": (inspect or {}).get("before", {}),
        "inspect_after": (inspect or {}).get("after", {}),
        "executed_phases": [], "adjudication": {},
    }


class TestLatencySloNeedsEvidence(unittest.TestCase):
    def test_s2_s3_are_na_when_zero_successful_responses(self):
        v = grading.grade(_result(n_200=0))
        rows = {x["id"]: x for x in v["slo"]}
        self.assertEqual(rows["S2"]["grade"], "N/A",
                         "0.00 ms over zero samples must not grade PASS")
        self.assertEqual(rows["S3"]["grade"], "N/A")
        self.assertIn("n=0", rows["S2"]["measured"])
        self.assertIn("N/A, not zero",
                      " ".join(v["executive_summary"]),
                      "executive summary must not imply real latency")

    def test_s2_s3_grade_from_real_data_when_present(self):
        v = grading.grade(_result(n_200=100))
        rows = {x["id"]: x["grade"] for x in v["slo"]}
        self.assertEqual(rows["S2"], "PASS")
        self.assertEqual(rows["S3"], "PASS")


class TestStabilitySloNeedsInstrumentation(unittest.TestCase):
    def test_s8_is_na_without_any_instrumentation(self):
        v = grading.grade(_result(n_200=100, env="windows", container=""))
        rows = {x["id"]: x for x in v["slo"]}
        self.assertEqual(rows["S8"]["grade"], "N/A",
                         "no OOM events observed is not evidence of zero OOM")
        self.assertIn("not observable", rows["S8"]["measured"])

    def test_s8_passes_only_with_instrumentation(self):
        v = grading.grade(_result(n_200=100, env="docker", container="web",
                                  inspect={"before": {"restart_count": 0},
                                           "after": {"restart_count": 0,
                                                     "state": "running"}},
                                  cgroup_samples=50))
        rows = {x["id"]: x["grade"] for x in v["slo"]}
        self.assertEqual(rows["S8"], "PASS")


class TestDeadTargetRefusesToRun(unittest.TestCase):
    def test_connection_refused_refuses_before_creating_run(self):
        cfg = C.normalize({"runtime_environment": "windows",
                           "target_url": "http://localhost:8090/",
                           "runtime_comparison": {"enabled": False}})
        pf = {"target_url": "http://localhost:8090/",
              "runtime_environment": "windows", "container": "",
              "checks": [], "cgroup": {},
              "target_probe": {"status": 0,
                               "error": "ConnectionRefusedError"}}
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(runner, "Benchmark") as mb:
                with self.assertRaises(RuntimeError) as ctx:
                    runner.run_benchmark(cfg, root, log=None,
                                         preflight_result=pf)
            mb.assert_not_called()
            self.assertEqual(glob.glob(os.path.join(root, "runs", "run-*")), [],
                             "no run directory may be created for a dead target")
            self.assertIn("unreachable", str(ctx.exception))

    def test_dns_failure_refuses_too(self):
        cfg = C.normalize({"runtime_environment": "windows",
                           "target_url": "http://no-such-host.invalid/",
                           "runtime_comparison": {"enabled": False}})
        pf = {"target_url": "http://no-such-host.invalid/",
              "runtime_environment": "windows", "container": "",
              "checks": [], "cgroup": {},
              "target_probe": {"status": 0, "error": "getaddrinfo failed"}}
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(runner, "Benchmark"):
                with self.assertRaises(RuntimeError):
                    runner.run_benchmark(cfg, root, log=None,
                                         preflight_result=pf)


class TestReportOverflowAndHonesty(unittest.TestCase):
    def test_runtime_comparison_html_wraps_all_tables_and_never_fakes_na(self):
        import re
        from heavyload import runtime_comparison as RC

        data = {
            "schema_version": 2,
            "generated_utc": "2026-11-23T00:00:00Z",
            "status": "ok",
            "config_meta": {"repeats": 3},
            "environment": {"runtime_environment": "Host", "python": "3.15"},
            "workloads": {
                "prime": {
                    "id": "A", "title": "Prime", "constants": "N=50000",
                    "description": "prime test", "expected_checksum": RC.EXPECTED["prime"],
                    "verified": True, "variants": 2,
                    "rows": [
                        {"key": "python", "runtime": "Python 3.15", "group": "baseline",
                         "source": "host", "compute_ms": 20.0},
                        {"key": "spid3r_vm", "runtime": "Spid3r VM", "group": "spid3r",
                         "source": "host", "compute_ms": 60.0},
                    ],
                },
            },
            "summary": {
                "workloads_verified": 1, "variants": 2,
                "slowdown_range": {"min": None, "max": None},
                "slowdown": {
                    "spid3r_vm": {
                        "label": "Spid3r VM",
                        "vs_php": {"workloads": {}, "geomean": None},
                        "vs_python": {"workloads": {"prime": 3.0}, "geomean": 3.0},
                        "vs_fastest": {"workloads": {"prime": 3.0}, "geomean": 3.0},
                    },
                },
                "matrix": {
                    "python": {"label": "Python", "geomeans": {"spid3r_vm": 0.33}},
                    "spid3r_vm": {"label": "Spid3r VM", "geomeans": {"python": 3.0}},
                },
                "quality": {"stable_variants": 0, "flagged_variants": 0,
                            "worst_cv_pct": None, "thresholds": {}},
                "verdict": "Measured only", "note": "",
            },
            "warnings": [],
        }
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "runtime_comparison.html")
            RC.build_html(data, out)
            with open(out, "r", encoding="utf-8") as f:
                html_txt = f.read()
        self.assertIn(".tblwrap{overflow-x:auto", html_txt)
        n_tables = len(re.findall(r"<table\b", html_txt))
        n_wrapped = len(re.findall(r'<div class="tblwrap"><table\b', html_txt))
        self.assertGreaterEqual(n_tables, 4)
        self.assertEqual(n_tables, n_wrapped,
                         "every table in runtime_comparison.html must be inside .tblwrap")
        self.assertNotIn("n/a&times;", html_txt)
        self.assertNotIn("n/a%", html_txt)


if __name__ == "__main__":
    unittest.main()
