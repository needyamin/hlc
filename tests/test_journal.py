#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Journal layer: figures, heatmap, side-by-side tables and the abstract —
rendered from synthetic checksum-verified data, never raising."""
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

from heavyload import journal as J  # noqa: E402


def _rc(rows_extra=None):
    data = {"workloads": {
        "prime": {"verified": True, "id": "A", "title": "Prime counting",
                  "rows": [
            {"key": "python", "runtime": "Python", "group": "baseline",
             "compute_ms": 100.0, "peak_rss_bytes": 12e6,
             "cpu_user_s": 0.05, "cpu_kernel_s": 0.01},
            {"key": "php", "runtime": "PHP", "group": "baseline",
             "compute_ms": 50.0, "peak_rss_bytes": 20e6,
             "cpu_user_s": 0.03, "cpu_kernel_s": 0.005},
            {"key": "spid3r_native", "runtime": "Spid3r native",
             "group": "spid3r", "compute_ms": 8.0,
             "peak_rss_bytes": 5e6, "cpu_user_s": 0.004,
             "cpu_kernel_s": 0.001},
        ]},
        "map": {"verified": True, "id": "C", "title": "Map lookups",
                "rows": [
            {"key": "python", "runtime": "Python", "group": "baseline",
             "compute_ms": 30.0, "peak_rss_bytes": 11e6,
             "cpu_user_s": 0.02, "cpu_kernel_s": 0.003},
            {"key": "php", "runtime": "PHP", "group": "baseline",
             "compute_ms": 40.0, "peak_rss_bytes": 21e6,
             "cpu_user_s": 0.025, "cpu_kernel_s": 0.004},
            {"key": "spid3r_native", "runtime": "Spid3r native",
             "group": "spid3r", "compute_ms": 3.0,
             "peak_rss_bytes": 5e6, "cpu_user_s": 0.003,
             "cpu_kernel_s": 0.0005},
        ]}}}
    return data


def _su():
    return {"ranked": [
        {"language": "python", "label": "Python", "rank": 1,
         "overall_score": 78.0, "measured_weight_share": 50.0,
         "measured_weight_pct_of_total": 35.0,
         "documented_weight_pct_of_total": 35.0,
         "missing_weight_pct_of_total": 30.0, "criteria": {}},
        {"language": "matrix", "label": "Spid3r (subject language)",
         "rank": 2, "overall_score": 52.0, "measured_weight_share": 70.0,
         "measured_weight_pct_of_total": 70.0,
         "documented_weight_pct_of_total": 15.0,
         "missing_weight_pct_of_total": 15.0, "criteria": {}},
    ]}


class TestFigures(unittest.TestCase):
    def test_all_charts_render_as_inline_svg(self):
        rc = _rc()
        figs, n = J.results_figures(rc, 1)
        self.assertGreaterEqual(figs.count("<svg"), 7,
                                "per-workload + geomean + memory + cpu + "
                                "scatter + pie + radar")
        self.assertEqual(figs.count("Fig. "), n - 1)
        self.assertIn("log scale", figs)

    def test_captions_are_numbered_sequentially(self):
        figs, n = J.results_figures(_rc(), 1)
        for i in range(1, n):
            self.assertIn("Fig. %d." % i, figs)

    def test_spid3r_highlighted(self):
        figs, _ = J.results_figures(_rc(), 1)
        self.assertIn("#f85606", figs)

    def test_no_data_is_honest(self):
        out, n = J.results_figures({"workloads": {}}, 1)
        self.assertIn("No verified workloads", out)

    def test_never_raises_on_garbage(self):
        out, _ = J.results_figures({"workloads": {"x": {"verified": True}}}, 1)
        self.assertIsInstance(out, str)


class TestHeadToHead(unittest.TestCase):
    def test_heatmap_and_side_by_side(self):
        h, n = J.head_to_head(_rc(), 1)
        self.assertIn("Head-to-head heatmap", h)
        self.assertIn("Side-by-side: every Spid3r engine", h)
        self.assertIn("tblwrap", h)
        self.assertEqual(h.count("Table "), n - 1)

    def test_ratio_cells_present(self):
        h, _ = J.head_to_head(_rc(), 1)
        self.assertRegex(h, r"\(\d+\.\d+&times;\)")


class TestSuitabilityFigures(unittest.TestCase):
    def test_score_bars_and_composition_donut(self):
        html_out, n = J.suitability_figures(_su(), 1)
        self.assertGreaterEqual(html_out.count("<svg"), 2)
        self.assertIn("Spid3r", html_out)
        self.assertGreaterEqual(n, 3)


class TestAbstract(unittest.TestCase):
    def test_abstract_states_measured_facts(self):
        results = {"meta": {}}
        ab = J.abstract(results, _rc(), _su())
        self.assertIn("runtime configurations", ab)
        self.assertIn("checksum-verified", ab)
        self.assertIn("Spid3r", ab)

    def test_abstract_honest_when_disabled(self):
        ab = J.abstract({}, {"workloads": {}}, _su())
        self.assertIn("disabled", ab)

    def test_render_never_raises(self):
        html_out, n = J.render({}, _rc(), _su())
        self.assertGreater(len(html_out), 0)
        self.assertGreater(n, 1)


if __name__ == "__main__":
    unittest.main()
