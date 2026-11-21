#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for the runtime-comparison statistics engine.

The stats must be standards-aligned and deterministic: same raw runs in ->
same published numbers out, every time.
"""
from __future__ import annotations

import unittest

from heavyload import runtime_comparison as RC


class TestRowStats(unittest.TestCase):
    def test_median_mean_stdev(self):
        st = RC.row_stats([10.0, 10.5, 10.2, 9.9, 10.1], "baseline")
        self.assertEqual(st["n"], 5)
        self.assertEqual(st["median_ms"], 10.1)
        self.assertAlmostEqual(st["mean_ms"], 10.14, places=3)
        self.assertAlmostEqual(st["stdev_ms"], 0.23, places=3)
        self.assertAlmostEqual(st["cv_pct"], 2.31, places=0)

    def test_confidence_intervals_are_ordered(self):
        st = RC.row_stats([10.0, 10.5, 10.2, 9.9, 10.1], "baseline")
        self.assertIsNotNone(st["mean_ci95"])
        self.assertIsNotNone(st["median_ci95"])
        self.assertLessEqual(st["mean_ci95"][0], st["mean_ms"])
        self.assertGreaterEqual(st["mean_ci95"][1], st["mean_ms"])
        self.assertLessEqual(st["median_ci95"][0], st["median_ms"])
        self.assertGreaterEqual(st["median_ci95"][1], st["median_ms"])

    def test_deterministic_bootstrap(self):
        runs = [10.0, 10.5, 10.2, 9.9, 10.1]
        self.assertEqual(RC.row_stats(runs, "baseline"),
                         RC.row_stats(runs, "baseline"))

    def test_iqr_and_minmax(self):
        st = RC.row_stats([10.0, 10.5, 10.2, 9.9, 10.1], "baseline")
        self.assertEqual(st["min_ms"], 9.9)
        self.assertEqual(st["max_ms"], 10.5)
        self.assertGreaterEqual(st["iqr_ms"], 0)

    def test_single_sample_is_honest(self):
        st = RC.row_stats([42.0], "baseline")
        self.assertEqual(st["n"], 1)
        self.assertIsNone(st["mean_ci95"])
        self.assertIsNone(st["median_ci95"])

    def test_noisy_cv_is_flagged(self):
        st = RC.row_stats([10.0, 40.0, 12.0, 45.0, 9.0], "baseline")
        self.assertTrue(any("noisy" in f for f in st["flags"]))
        self.assertFalse(st["stable"])

    def test_timer_floor_flagged_for_spid3r_only(self):
        st_sp = RC.row_stats([3.0, 3.0, 3.0, 3.0, 3.0], "spid3r")
        st_py = RC.row_stats([3.0, 3.0, 3.0, 3.0, 3.0], "baseline")
        self.assertTrue(any("timer floor" in f for f in st_sp["flags"]))
        self.assertFalse(any("timer floor" in f for f in st_py["flags"]),
                         "us-resolution runtimes have no 1 ms floor")

    def test_few_repeats_flagged(self):
        st = RC.row_stats([10.0, 10.1], "baseline")
        self.assertTrue(any("few-repeats" in f for f in st["flags"]))

    def test_stable_when_clean(self):
        st = RC.row_stats([10.0, 10.1, 9.9, 10.0, 10.05], "baseline")
        self.assertEqual(st["flags"], [])
        self.assertTrue(st["stable"])


class TestNewWorkloadsRegistered(unittest.TestCase):
    def test_five_workloads_with_checksums(self):
        self.assertEqual(set(RC.EXPECTED),
                         {"prime", "json", "map", "text", "matmul"})
        for wid, exp in RC.EXPECTED.items():
            self.assertTrue(exp.startswith("RESULT "), wid)
            self.assertIn(wid, RC.WORKLOAD_INFO)

    def test_registry_covers_five_workloads_per_language(self):
        from heavyload.comparison_sources import LANG_MODULES
        for m in LANG_MODULES:
            self.assertTrue(m.SOURCES, "module %s ships no sources" % m.KEY)
            for wid in m.SOURCES:
                self.assertIn(wid, RC.WORKLOAD_INFO)
                self.assertIn(wid, RC.EXPECTED)

    def test_json_only_for_native_serializers(self):
        # Workload B exists ONLY where the language's standard library ships
        # a JSON serializer; the absent entries are documented per module.
        from heavyload.comparison_sources import module_by_key
        for key in ("python", "node", "typescript", "php", "ruby", "go",
                    "csharp", "swift", "matrix"):
            self.assertIn("json", module_by_key(key).SOURCES, key)
        for key in ("rust", "java", "kotlin", "c", "cpp"):
            self.assertNotIn("json", module_by_key(key).SOURCES, key)
            self.assertTrue(module_by_key(key).NOTE, key)

    def test_all_registry_checksums_verified_on_this_host(self):
        # Live verification for every DETECTED toolchain: each measured
        # variant must reproduce the pinned checksum. Missing toolchains
        # simply contribute nothing (skipped, never faked).
        from heavyload.comparison_sources import LANG_MODULES
        import tempfile, os, shutil, statistics
        opts = {"include_windows": True, "include_references": True}
        tools = RC.detect_tools({}, opts)
        mods = [m for m in LANG_MODULES if m.available(tools)]
        if not mods:
            self.skipTest("no language toolchains detected")
        work = tempfile.mkdtemp(prefix="hlc-test-verify-")
        try:
            for wid, expected in RC.EXPECTED.items():
                for m in mods:
                    src = m.SOURCES.get(wid)
                    if src is None:
                        continue
                    RC._write_source(work, m.filename(wid), src)
                    built = RC._build_for_module(wid, wid, m, work, tools,
                                                 opts, [], 600)
                    for var in RC._variants_for_module(wid, wid, m, work,
                                                       tools, built, opts, []):
                        meas = RC._measure(var["cmd"], cwd=work, timeout=120)
                        self.assertEqual(meas["rc"], 0,
                                         "%s/%s run failed" % (wid, var["key"]))
                        res, _ = RC._parse_output(meas["stdout"])
                        self.assertEqual(res, expected,
                                         "%s/%s checksum" % (wid, var["key"]))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_constants_pinned(self):
        self.assertEqual(RC.TEXT_ROWS, 50000)
        self.assertEqual(RC.MATMUL_N, 128)


class TestQualitySummary(unittest.TestCase):
    def test_quality_rollup(self):
        def row(key, label, ms, flags):
            return {"key": key, "runtime": label, "group": "spid3r",
                    "result": "RESULT x", "compute_ms": ms,
                    "compute_min_ms": ms, "compute_max_ms": ms,
                    "wall_ms": ms, "compute_runs_ms": [ms],
                    "stats": {"flags": flags, "cv_pct": 20.0 if flags else 1.0}}
        data = {"workloads": {"prime": {
            "id": "A", "title": "t", "verified": True, "rows": [
                row("spid3r_tw", "Spid3r", 10.0, ["noisy (CV 20.0% > 15%)"]),
                row("php", "PHP", 20.0, [])]}}}
        s = RC.summarize_data(data)
        self.assertEqual(s["quality"]["flagged_variants"], 1)
        self.assertEqual(s["quality"]["stable_variants"], 1)
        self.assertIn("Spid3r", s["quality"]["flag_detail"][0])
        self.assertEqual(s["quality"]["worst_cv_pct"], 20.0)


if __name__ == "__main__":
    unittest.main()
