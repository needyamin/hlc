#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scalability analysis: knee detection and classification on synthetic
measured curves, plus honest insufficient-evidence handling."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import scalability as SC  # noqa: E402


def _step(c, rps, p95, err=0.0, n=None):
    n = n if n is not None else int(rps * 10)
    return {"phase": "ramp", "role": "active", "concurrency": c,
            "start_s": 0, "end_s": 10, "duration_s": 10,
            "requests": n, "rps": rps, "errors": int(err / 100.0 * n),
            "error_pct": err,
            "latency_200": {"p50": p95 / 3, "p95": p95, "p99": p95 * 2},
            "latency_all": {"p50": p95 / 3, "p95": p95, "p99": p95 * 2}}


class TestClassification(unittest.TestCase):
    def test_linear_scaling(self):
        pts = [_step(10, 1000, 5), _step(20, 2000, 5.4),
               _step(40, 3900, 6.2)]
        r = SC.analyze({"step_metrics": pts})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["classification"], "linear")

    def test_sublinear_scaling(self):
        pts = [_step(10, 1000, 5), _step(20, 1500, 8),
               _step(40, 2000, 14)]
        r = SC.analyze({"step_metrics": pts})
        self.assertEqual(r["classification"], "sublinear")

    def test_plateau(self):
        pts = [_step(10, 1000, 5), _step(25, 1900, 6),
               _step(50, 1950, 6.5), _step(100, 1900, 7)]
        r = SC.analyze({"step_metrics": pts})
        self.assertEqual(r["classification"], "plateau")

    def test_degraded_on_errors(self):
        pts = [_step(10, 1000, 5), _step(50, 1200, 40, err=9.0)]
        r = SC.analyze({"step_metrics": pts})
        self.assertEqual(r["classification"], "degraded")

    def test_insufficient_evidence_with_no_points(self):
        r = SC.analyze({"step_metrics": []})
        self.assertEqual(r["status"], "insufficient_evidence")
        self.assertEqual(r["classification"], "unavailable")

    def test_single_point_is_honest(self):
        r = SC.analyze({"step_metrics": [_step(50, 2000, 6)]})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["classification"], "insufficient_evidence")
        self.assertIn("single", r["note"])


class TestKnee(unittest.TestCase):
    def test_knee_before_efficiency_collapse(self):
        pts = [_step(10, 1000, 5), _step(20, 1900, 5.5),
               _step(50, 1950, 12), _step(100, 1960, 60)]
        r = SC.analyze({"step_metrics": pts})
        self.assertIsNotNone(r["knee_concurrency"])
        self.assertLess(r["knee_concurrency"], 100)

    def test_no_knee_on_clean_linear(self):
        pts = [_step(10, 1000, 5), _step(20, 2000, 5.2),
               _step(40, 4000, 5.6)]
        r = SC.analyze({"step_metrics": pts})
        self.assertIsNone(r["knee_concurrency"])

    def test_levels_merge_by_concurrency(self):
        pts = [_step(25, 1000, 5), _step(25, 1000, 5.2),
               _step(50, 1600, 7)]
        r = SC.analyze({"step_metrics": pts})
        c25 = [p for p in r["points"] if p["concurrency"] == 25]
        self.assertEqual(len(c25), 1)
        self.assertEqual(c25[0]["windows"], 2)
        # Two 10 s windows of 1000 RPS each merge into one 20000-request /
        # 20 s aggregate.
        self.assertAlmostEqual(c25[0]["rps"], 1000.0, delta=0.1)

    def test_never_raises_on_garbage(self):
        r = SC.analyze({"step_metrics": [{"concurrency": "x"}]})
        self.assertIn(r["status"], ("insufficient_evidence", "error"))


if __name__ == "__main__":
    unittest.main()
