#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Suitability scoring: normalization math, provenance separation, weight
redistribution, ranking determinism, and the honesty guarantees."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import suitability as SU  # noqa: E402
from heavyload import config as C  # noqa: E402


def _write_rc(tmp):
    """Persist synthetic runtime-comparison evidence into ``tmp``."""
    data = {"workloads": {"prime": {"verified": True, "id": "A",
                                    "title": "Prime counting",
                                    "expected_checksum": "RESULT x", "rows": [
        {"key": "python", "runtime": "Py", "group": "baseline",
         "compute_ms": 100.0, "peak_rss_bytes": 12_000_000,
         "cpu_user_s": 0.05, "cpu_kernel_s": 0.01},
        {"key": "php", "runtime": "PHP", "group": "baseline",
         "compute_ms": 50.0, "peak_rss_bytes": 20_000_000,
         "cpu_user_s": 0.03, "cpu_kernel_s": 0.005},
        {"key": "rust", "runtime": "Rust", "group": "reference",
         "compute_ms": 2.0, "peak_rss_bytes": 3_000_000,
         "cpu_user_s": 0.002, "cpu_kernel_s": 0.001},
        {"key": "spid3r_native", "runtime": "Spid3r native",
         "group": "spid3r",
         "compute_ms": 8.0, "peak_rss_bytes": 5_000_000,
         "cpu_user_s": 0.004, "cpu_kernel_s": 0.001},
    ]}},
        "summary": {"slowdown": {
            "spid3r_native": {"label": "Spid3r native",
                              "vs_php": {"workloads": {"prime": 0.16},
                                         "geomean": 0.16},
                              "vs_python": {"workloads": {"prime": 0.08},
                                            "geomean": 0.08},
                              "vs_fastest": {"workloads": {"prime": 4.0},
                                             "geomean": 4.0}}}}}
    pathlib.Path(tmp, "runtime_comparison.json").write_text(
        json.dumps(data), encoding="utf-8")


class TestSpid3rSubject(unittest.TestCase):
    """Spid3r is the language this tool exists to measure: it must ALWAYS
    appear in the ranking and get its own report section."""

    def _build(self, tmp):
        _write_rc(tmp)
        cfg = C.normalize({})
        return SU.build({"target": {}}, cfg, tmp)

    def test_subject_row_always_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            rows = [r for r in d["ranked"] if r["language"] == "matrix"]
            self.assertEqual(len(rows), 1, "exactly one Spid3r subject row")
            self.assertEqual(rows[0]["label"], "Spid3r (subject language)")

    def test_engine_variants_collapsed_not_duplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            self.assertFalse(any(str(r["language"]).startswith("spid3r")
                                 for r in d["ranked"]),
                             "engine variants must not become rows")

    def test_measured_data_merged_into_subject_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            m = [r for r in d["ranked"] if r["language"] == "matrix"][0]
            self.assertGreater(m["measured_weight_share"], 0.0)
            self.assertIsNotNone(
                m["criteria"]["compute_performance"]["score"])

    def test_unassessed_documented_criteria_are_disclosed(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            m = [r for r in d["ranked"] if r["language"] == "matrix"][0]
            c = m["criteria"]["concurrency_model"]
            self.assertIsNone(c["score"])
            self.assertIn("unassessed", c["evidence"])

    def test_subject_report_block_with_variant_ratios(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            sp3 = d["spid3r"]
            self.assertTrue(sp3["has_comparison"])
            self.assertEqual(sp3["subject"]["language"] if "language"
                             in sp3["subject"] else "matrix", "matrix")
            self.assertEqual(len(sp3["variants"]), 1)
            self.assertAlmostEqual(sp3["variants"][0]["vs_fastest"], 4.0)
            self.assertEqual(len(sp3["workloads"]), 1)
            self.assertIn("spid3r_native", sp3["workloads"][0]["spid3r_ms"])

    def test_subject_present_even_without_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = C.normalize({})
            d = SU.build({"target": {}}, cfg, tmp)
            rows = [r for r in d["ranked"] if r["language"] == "matrix"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["measured_weight_share"], 0.0)
            self.assertFalse(d["spid3r"]["has_comparison"])
            self.assertIn("--runtime-comparison", d["spid3r"]["note"])

    def test_html_has_dedicated_spid3r_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            html = pathlib.Path(tmp, "suitability.html").read_text(
                encoding="utf-8")
            self.assertIn("subject language report", html)
            self.assertIn("Engine variants", html)
            self.assertIn("Per-workload medians", html)

    def test_summary_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            s = SU.summarize(d)
            self.assertEqual(s["spid3r"]["label"],
                             "Spid3r (subject language)")
            self.assertIsNotNone(s["spid3r"]["rank"])
            self.assertTrue(s["spid3r"]["has_comparison"])


class TestNormalization(unittest.TestCase):
    def test_best_gets_ten_worst_gets_zero(self):
        s = SU._normalize({"a": 1.0, "b": 100.0}, lower_is_better=True)
        self.assertEqual(s["a"], 10.0)
        self.assertEqual(s["b"], 0.0)

    def test_higher_is_better_flips(self):
        s = SU._normalize({"a": 1.0, "b": 100.0}, lower_is_better=False)
        self.assertEqual(s["a"], 0.0)
        self.assertEqual(s["b"], 10.0)

    def test_equal_values_all_ten(self):
        s = SU._normalize({"a": 5.0, "b": 5.0}, lower_is_better=True)
        self.assertEqual(s["a"], 10.0)
        self.assertEqual(s["b"], 10.0)

    def test_nonpositive_values_excluded(self):
        s = SU._normalize({"a": 1.0, "b": 0, "c": None}, lower_is_better=True)
        self.assertNotIn("b", s)
        self.assertNotIn("c", s)
        self.assertEqual(s["a"], 10.0)


class TestBuild(unittest.TestCase):
    def _build(self, tmp):
        _write_rc(tmp)
        cfg = C.normalize({})
        return SU.build({"target": {}}, cfg, tmp)

    def test_ranked_with_measured_and_documented(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            self.assertEqual(d["status"], "ok")
            labels = {r["language"] for r in d["ranked"]}
            # Rust (measured) and Java (documented-only) must both appear.
            self.assertIn("rust", labels)
            self.assertIn("java", labels)
            rust = [r for r in d["ranked"] if r["language"] == "rust"][0]
            java = [r for r in d["ranked"] if r["language"] == "java"][0]
            self.assertGreater(rust["measured_weight_share"], 0.0)
            self.assertEqual(java["measured_weight_share"], 0.0)
            # Every documented criterion carries its justification.
            c = rust["criteria"]["ecosystem_maturity"]
            self.assertEqual(c["provenance"], "documented")
            self.assertTrue(c["evidence"])

    def test_fastest_measured_language_wins_compute(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            rust = [r for r in d["ranked"] if r["language"] == "rust"][0]
            py = [r for r in d["ranked"] if r["language"] == "python"][0]
            self.assertGreater(rust["criteria"]["compute_performance"]["score"],
                               py["criteria"]["compute_performance"]["score"])

    def test_weight_redistribution_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = self._build(tmp)
            java = [r for r in d["ranked"] if r["language"] == "java"][0]
            # HTTP criteria never contribute (no campaign data): the missing
            # share must be visible, never hidden.
            self.assertGreater(java["missing_weight_pct_of_total"], 0.0)

    def test_artifacts_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._build(tmp)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "suitability.json")))
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "suitability.html")))
            with open(os.path.join(tmp, "suitability.json"),
                      encoding="utf-8") as f:
                d = json.load(f)
            self.assertIn("disclaimer", d)
            self.assertIn("provenance", d["criteria"]["ecosystem_maturity"])

    def test_deterministic_ranking(self):
        with tempfile.TemporaryDirectory() as t1, \
                tempfile.TemporaryDirectory() as t2:
            a = self._build(t1)
            b = self._build(t2)
            self.assertEqual([r["language"] for r in a["ranked"]],
                             [r["language"] for r in b["ranked"]])
            self.assertEqual([r["overall_score"] for r in a["ranked"]],
                             [r["overall_score"] for r in b["ranked"]])

    def test_empty_run_dir_still_ranks_documented(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = C.normalize({})
            d = SU.build({"target": {}}, cfg, tmp)
            self.assertEqual(d["status"], "ok")
            for r in d["ranked"]:
                self.assertEqual(r["measured_weight_share"], 0.0)

    def test_no_data_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = C.normalize({})
            d = SU.build(None, cfg, tmp)
            self.assertIn(d["status"], ("ok", "unavailable"))


class TestSummarize(unittest.TestCase):
    def test_summary_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_rc(tmp)
            cfg = C.normalize({})
            d = SU.build({"target": {}}, cfg, tmp)
            s = SU.summarize(d)
            self.assertEqual(s["status"], "ok")
            self.assertLessEqual(len(s["top"]), 8)
            self.assertIn("artifacts", s)
            self.assertIn("measured_share_note", s)


if __name__ == "__main__":
    unittest.main()
