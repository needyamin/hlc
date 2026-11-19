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


if __name__ == "__main__":
    unittest.main()
