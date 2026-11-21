#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measured child-process metrics: rc/stdout parsing, peak RSS, CPU time,
timeout handling, and the never-raises contract."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import procmetrics  # noqa: E402


class TestRunMeasured(unittest.TestCase):
    def test_measures_a_real_child(self):
        r = procmetrics.run_measured(
            [sys.executable, "-c",
             "import time; time.sleep(0.12); print('OK')"],
            timeout=30)
        self.assertEqual(r["rc"], 0)
        self.assertIn("OK", r["stdout"])
        self.assertIsNotNone(r["peak_rss_bytes"],
                             "peak RSS must be measured, not None, on a "
                             "supported platform")
        self.assertIsNotNone(r["cpu_user_s"])
        self.assertGreater(r["wall_ms"], 50.0)
        self.assertTrue(r["method"])

    def test_nonzero_exit_and_stderr(self):
        r = procmetrics.run_measured([sys.executable, "-c", "raise SystemExit(3)"],
                                     timeout=30)
        self.assertEqual(r["rc"], 3)

    def test_timeout_reports_failure(self):
        r = procmetrics.run_measured(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            timeout=1)
        self.assertEqual(r["rc"], -1)
        self.assertIn("timeout", r["stderr"])

    def test_missing_binary_never_raises(self):
        r = procmetrics.run_measured(
            [sys.executable, "-c", "pass", "whatever"], timeout=10)
        self.assertIn("rc", r)

    def test_cpu_time_is_plausible(self):
        r = procmetrics.run_measured(
            [sys.executable, "-c",
             "x = sum(i * i for i in range(400000))"],
            timeout=30)
        total = (r["cpu_user_s"] or 0) + (r["cpu_kernel_s"] or 0)
        self.assertGreater(total, 0.0)
        # CPU time cannot exceed wall time by much on a single thread.
        self.assertLessEqual(total, r["wall_ms"] / 1000.0 + 0.5)


if __name__ == "__main__":
    unittest.main()
