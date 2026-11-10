"""Unit tests for heavyload.compare — the honest comparison / leaderboard core.

These tests never touch Docker, the network or the filesystem beyond a temp dir.
"""

import csv
import json
import os
import sys
import tempfile
import unittest

# Make the package importable regardless of how the suite is invoked.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heavyload import compare
from tests.helpers import make_result, write_run


class TestExtractLeaderboardMetrics(unittest.TestCase):
    def test_extract_flattens_expected_keys(self):
        res = make_result(run_id="run-1", language="python", site_id="py-1")
        m = compare.extract_leaderboard_metrics(res)
        for key in ("site_id", "language", "stack_label", "overall_rps",
                    "peak_phase_rps", "p95_ms", "error_pct",
                    "cpu_peak_pct_net", "mem_anon_mib_peak_net",
                    "mem_net_change_mib", "mb_per_1k_req", "health_score",
                    "verdict_code"):
            self.assertIn(key, m, "missing leaderboard key %r" % key)
        self.assertEqual(m["site_id"], "py-1")
        self.assertEqual(m["language"], "python")
        # p95 comes from as_scheduled_latency first.
        self.assertEqual(m["p95_ms"], 12.5)
        # Peak phase RPS is the max over active phases.
        self.assertAlmostEqual(m["peak_phase_rps"], 127.0, places=2)
        # MB / 1k requests: anon_net / (total_requests / 1000).
        self.assertAlmostEqual(m["mb_per_1k_req"], 12.3 / 3.0, places=3)

    def test_p95_falls_back_to_all_requests(self):
        # p95=None makes as_scheduled_latency.p95 None -> fall back to all.
        res = make_result(p95=None)
        m = compare.extract_leaderboard_metrics(res)
        self.assertEqual(m["p95_ms"], 14.0)

    def test_extract_handles_client_only_none_metrics(self):
        # Client-only (live black-box) runs have no server metrics at all.
        res = make_result(mode="live_blackbox", visibility="client_only")
        for k in ("anon_net_change_mib", "anon_peak_mib_net", "cpu_peak_pct_net",
                  "cpu_mean_pct_net", "pids_max", "health_success_pct"):
            res["resources"][k] = None
        res["summary"]["total_requests"] = 91
        m = compare.extract_leaderboard_metrics(res)
        self.assertIsNone(m["mem_net_change_mib"])
        self.assertIsNone(m["mb_per_1k_req"])
        self.assertIsNone(m["cpu_peak_pct_net"])
        self.assertEqual(m["total_requests"], 91)
        # Should not raise; visibility is preserved.
        self.assertEqual(m["visibility"], "client_only")

    def test_extract_survives_ensure_current(self):
        # load_results upgrades via schema.ensure_current; values must survive.
        tmp = tempfile.mkdtemp()
        try:
            write_run(tmp, "run-2", make_result(run_id="run-2", overall_rps=55.5,
                                                error_pct=1.1))
            loaded = compare.load_results(tmp, "run-2")
            m = compare.extract_leaderboard_metrics(loaded)
            self.assertAlmostEqual(m["overall_rps"], 55.5, places=3)
            self.assertAlmostEqual(m["error_pct"], 1.1, places=3)
        finally:
            _rmtree(tmp)


class TestAggregateScalars(unittest.TestCase):
    def test_median_mean_min_max_stddev(self):
        maps = [{"x": 10.0}, {"x": 20.0}, {"x": 30.0}]
        a = compare.aggregate_scalars(maps, "x")
        self.assertIsNotNone(a)
        self.assertEqual(a["n"], 3)
        self.assertAlmostEqual(a["median"], 20.0, places=4)
        self.assertAlmostEqual(a["mean"], 20.0, places=4)
        self.assertAlmostEqual(a["min"], 10.0, places=4)
        self.assertAlmostEqual(a["max"], 30.0, places=4)
        self.assertAlmostEqual(a["stddev"], (200.0 / 3.0) ** 0.5, places=4)

    def test_skips_missing_and_non_numeric(self):
        maps = [{"x": 10.0}, {"x": None}, {"y": 5}, {}]
        a = compare.aggregate_scalars(maps, "x")
        self.assertEqual(a["n"], 1)
        self.assertAlmostEqual(a["median"], 10.0, places=4)

    def test_empty_returns_none(self):
        self.assertIsNone(compare.aggregate_scalars([], "x"))
        self.assertIsNone(compare.aggregate_scalars([{"x": None}], "x"))


class TestCompareRuns(unittest.TestCase):
    def _two_docker_runs(self, tmp):
        write_run(tmp, "a", make_result(run_id="a", site_id="py", language="python",
                                        mode="docker_lab", visibility="full_server",
                                        overall_rps=100.0))
        write_run(tmp, "b", make_result(run_id="b", site_id="node", language="node",
                                        mode="docker_lab", visibility="full_server",
                                        overall_rps=140.0))
        return ["a", "b"]

    def test_comparable_docker_lab_pair(self):
        tmp = tempfile.mkdtemp()
        try:
            ids = self._two_docker_runs(tmp)
            cmp = compare.compare_runs(tmp, ids)
            self.assertTrue(cmp["comparable"],
                            "two docker_lab runs must be comparable")
            self.assertEqual(len(cmp["leaderboard"]), 2)
            # Sorted by overall_rps desc -> node (140) first.
            self.assertEqual(cmp["leaderboard"][0]["site_id"], "node")
        finally:
            _rmtree(tmp)

    def test_not_comparable_client_only(self):
        tmp = tempfile.mkdtemp()
        try:
            write_run(tmp, "c1", make_result(run_id="c1", site_id="py",
                                            mode="live_blackbox",
                                            visibility="client_only"))
            write_run(tmp, "c2", make_result(run_id="c2", site_id="node",
                                            mode="live_blackbox",
                                            visibility="client_only"))
            cmp = compare.compare_runs(tmp, ["c1", "c2"])
            self.assertFalse(cmp["comparable"])
            self.assertTrue(any("client-only" in r["reason"]
                                for r in cmp["comparison_reasons"]))
            # Rows are still returned (honest warning, not a silent ranking).
            self.assertEqual(len(cmp["leaderboard"]), 2)
        finally:
            _rmtree(tmp)

    def test_not_comparable_different_mode(self):
        tmp = tempfile.mkdtemp()
        try:
            write_run(tmp, "d1", make_result(run_id="d1", mode="docker_lab",
                                            visibility="full_server"))
            write_run(tmp, "d2", make_result(run_id="d2", mode="live_blackbox",
                                            visibility="client_only"))
            cmp = compare.compare_runs(tmp, ["d1", "d2"])
            self.assertFalse(cmp["comparable"])
            self.assertTrue(any("benchmark_mode differs" in r["reason"]
                                for r in cmp["comparison_reasons"]))
        finally:
            _rmtree(tmp)

    def test_missing_run_reported(self):
        tmp = tempfile.mkdtemp()
        try:
            write_run(tmp, "e1", make_result(run_id="e1"))
            cmp = compare.compare_runs(tmp, ["e1", "does-not-exist"])
            self.assertIn("does-not-exist", cmp["missing"])
        finally:
            _rmtree(tmp)


class TestExport(unittest.TestCase):
    def test_to_csv_and_json(self):
        tmp = tempfile.mkdtemp()
        try:
            ids = ["a", "b"]
            for rid, rps in (("a", 100.0), ("b", 140.0)):
                write_run(tmp, rid, make_result(run_id=rid, site_id=rid,
                                                mode="docker_lab",
                                                visibility="full_server",
                                                overall_rps=rps))
            cmp = compare.compare_runs(tmp, ids)
            csv_path = os.path.join(tmp, "lb.csv")
            json_path = os.path.join(tmp, "lb.json")
            compare.to_csv(cmp, csv_path)
            compare.to_json(cmp, json_path)
            self.assertTrue(os.path.isfile(csv_path))
            self.assertTrue(os.path.isfile(json_path))

            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                rows = list(csv.reader(f))
            # Header + 2 data rows.
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0][0], "Site")
            # b (140 rps) sorts first.
            self.assertEqual(rows[1][0], "b")

            with open(json_path, "r", encoding="utf-8") as f:
                j = json.load(f)
            self.assertTrue(j["comparable"])
            self.assertEqual(len(j["leaderboard"]), 2)
        finally:
            _rmtree(tmp)


def _rmtree(path):
    import shutil
    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
