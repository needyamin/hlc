#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-quality checks: anomaly detection on synthetic measured evidence."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import quality as Q  # noqa: E402


def _result(**over):
    res = {
        "summary": {
            "total_requests": over.get("total", 5000),
            "PRIMARY_successful_200_only": over.get("lat200", {
                "p50": 5.0, "p95": 10.0, "p99": 20.0, "max": 40.0}),
            "all_requests_latency": over.get("lat_all", {
                "p50": 5.0, "p95": 11.0, "p99": 21.0, "max": 45.0}),
            "dispatch_delay_ms": over.get("dsp", {"p95": 2.0}),
        },
        "aggregation_meta": {"malformed_raw_lines": over.get("bad", 0)},
        "series": {"generator_series": over.get("gen", [
            {"t": 0.0, "cpu_pct": 10.0, "rss_mib": 40.0},
            {"t": 15.0, "cpu_pct": 12.0, "rss_mib": 41.0},
            {"t": 30.0, "cpu_pct": 11.0, "rss_mib": 41.0},
        ])},
        "config": {"request_timeout_s": 12},
        "phases": over.get("phases", []),
        "resources": over.get("resources", {"gen_cpu_peak_pct": 12.0}),
        "meta": {"schedule_s": over.get("schedule", 30)},
    }
    return res


class TestChecks(unittest.TestCase):
    def test_clean_run_is_ok(self):
        q = Q.check_run(_result())
        self.assertEqual(q["status"], "ok")
        names = {c["name"] for c in q["checks"]}
        self.assertEqual(names, {"malformed_raw_lines", "timeline_gaps",
                                 "percentile_monotonic", "runt_phases",
                                 "generator_saturation", "latency_bounds"})

    def test_malformed_lines_flagged(self):
        q = Q.check_run(_result(bad=250))
        c = [x for x in q["checks"] if x["name"] == "malformed_raw_lines"][0]
        self.assertEqual(c["status"], "fail")
        self.assertIn("malformed", c["detail"])

    def test_percentile_violation_fails(self):
        q = Q.check_run(_result(lat200={"p50": 30.0, "p95": 10.0, "p99": 20.0,
                                        "max": 40.0}))
        c = [x for x in q["checks"] if x["name"] == "percentile_monotonic"][0]
        self.assertEqual(c["status"], "fail")

    def test_generator_saturation_warns(self):
        q = Q.check_run(_result(resources={"gen_cpu_peak_pct": 96.0},
                                dsp={"p95": 40.0}))
        c = [x for x in q["checks"] if x["name"] == "generator_saturation"][0]
        self.assertEqual(c["status"], "warn")
        self.assertIn("generator", c["detail"])

    def test_runt_phase_detected(self):
        phases = [{"name": "04-sustained", "role": "sustained",
                   "duration_s": 45, "concurrency": 50, "requests": 30}]
        q = Q.check_run(_result(phases=phases))
        c = [x for x in q["checks"] if x["name"] == "runt_phases"][0]
        self.assertEqual(c["status"], "warn")
        self.assertIn("04-sustained", c["detail"])

    def test_latency_over_timeout_warns(self):
        q = Q.check_run(_result(lat_all={"p50": 5, "p95": 11, "p99": 21,
                                         "max": 20000.0}))
        c = [x for x in q["checks"] if x["name"] == "latency_bounds"][0]
        self.assertEqual(c["status"], "warn")

    def test_no_evidence_is_insufficient_never_guessed(self):
        q = Q.check_run(_result(gen=[], total=0, lat200={}, lat_all={},
                                resources={"gen_cpu_peak_pct": None},
                                dsp={"p95": None}))
        for c in q["checks"]:
            self.assertIn(c["status"],
                          ("ok", "warn", "fail", "insufficient_evidence"))
        sat = [x for x in q["checks"]
               if x["name"] == "generator_saturation"][0]
        self.assertEqual(sat["status"], "insufficient_evidence")

    def test_never_raises_on_empty_results(self):
        q = Q.check_run({})
        self.assertIn(q["status"], ("ok", "warn", "fail"))


if __name__ == "__main__":
    unittest.main()
