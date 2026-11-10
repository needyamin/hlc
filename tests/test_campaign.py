"""Unit tests for heavyload.campaign — batch runner validation and aggregation.

These exercise the pure, orchestration-free parts (validation, per-site
aggregation, comparison projection). Full ``run_campaign`` orchestration is
covered by ``selftest.py`` style end-to-end runs and a manual no-Docker smoke,
because it drives real HTTP load.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heavyload import campaign
from heavyload import compare
from tests.helpers import make_result

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_campaign(tmp, obj):
    p = os.path.join(tmp, "campaign.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return p


class TestLoadCampaign(unittest.TestCase):
    def test_valid(self):
        p = _write_campaign(tempfile.mkdtemp(), {
            "title": "x", "profile": "smoke",
            "sites": ["python-fastapi-hello", "node-express-hello"],
        })
        spec, err = campaign.load_campaign(p)
        self.assertIsNone(err, err)
        self.assertEqual(spec["profile"], "smoke")
        self.assertEqual(spec["warmup_runs"], 1)
        self.assertEqual(spec["repeat_count"], 3)
        self.assertEqual(len(spec["sites"]), 2)

    def test_bad_profile(self):
        p = _write_campaign(tempfile.mkdtemp(), {
            "profile": "nope", "sites": ["a"]})
        spec, err = campaign.load_campaign(p)
        self.assertIsNone(spec)
        self.assertIn("profile", err)

    def test_empty_sites(self):
        p = _write_campaign(tempfile.mkdtemp(), {
            "profile": "smoke", "sites": []})
        spec, err = campaign.load_campaign(p)
        self.assertIsNone(spec)
        self.assertIn("sites", err)

    def test_clamps(self):
        p = _write_campaign(tempfile.mkdtemp(), {
            "profile": "smoke", "warmup_runs": 99, "repeat_count": 99,
            "sites": ["a"]})
        spec, err = campaign.load_campaign(p)
        self.assertIsNone(err)
        self.assertEqual(spec["warmup_runs"], 10)
        self.assertEqual(spec["repeat_count"], 20)


class TestBuildSiteConfig(unittest.TestCase):
    def test_resolves_real_spec(self):
        # The real bundled example specs must resolve and fold into a config.
        spec = {"profile": "smoke", "sites": [], "warmup_runs": 0,
                "repeat_count": 1, "cooldown_between_repeats_s": 0}
        cfg, err = campaign._build_site_config(
            spec, "python-fastapi-hello", PROJECT_ROOT)
        self.assertIsNone(err, err)
        self.assertEqual(cfg["target_url"], "http://localhost:8090/")
        self.assertEqual(cfg["target"]["language"], "python")
        self.assertEqual(cfg["profile"], "smoke")

    def test_missing_spec_errors(self):
        spec = {"profile": "smoke", "sites": [], "warmup_runs": 0,
                "repeat_count": 1, "cooldown_between_repeats_s": 0}
        cfg, err = campaign._build_site_config(
            spec, "no-such-site", PROJECT_ROOT)
        self.assertIsNone(cfg)
        self.assertIn("not found", err)


class TestAggregateSite(unittest.TestCase):
    def test_median_across_repeats(self):
        members = [
            compare.extract_leaderboard_metrics(
                make_result(run_id="r%d" % i, overall_rps=100.0 + i,
                            error_pct=0.0 + i, cpu_peak=40.0 + i))
            for i in range(3)
        ]
        agg = campaign.aggregate_site(members)
        self.assertIn("overall_rps", agg)
        self.assertEqual(agg["overall_rps"]["n"], 3)
        self.assertAlmostEqual(agg["overall_rps"]["median"], 101.0, places=3)
        self.assertEqual(agg["error_pct"]["n"], 3)
        self.assertEqual(agg["cpu_peak_pct_net"]["n"], 3)

    def test_skips_failed_repeat(self):
        good = compare.extract_leaderboard_metrics(make_result(overall_rps=200.0))
        bad = {"site_id": "x"}  # missing numeric keys -> skipped
        agg = campaign.aggregate_site([good, bad])
        self.assertEqual(agg["overall_rps"]["n"], 1)
        self.assertAlmostEqual(agg["overall_rps"]["median"], 200.0, places=3)


class TestRunCampaignStop(unittest.TestCase):
    def test_should_stop_aborts_before_work(self):
        import shutil
        tmp = tempfile.mkdtemp()
        try:
            spec = {
                "schema_version": 2, "title": "stop test", "profile": "smoke",
                "warmup_runs": 1, "repeat_count": 3,
                "cooldown_between_repeats_s": 0,
                "sites": ["python-fastapi-hello", "node-express-hello"],
            }
            # Stop immediately -> must not run any benchmark, must flag stopped.
            result = campaign.run_campaign(
                tmp, spec, should_stop=lambda: True)
            self.assertTrue(result["stopped"])
            # No sites measured because we aborted at the very first site.
            self.assertEqual(len(result["sites"]), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCampaignAsComparison(unittest.TestCase):
    def test_projection(self):
        rec = {
            "site_id": "py", "language": "python", "stack_label": "py",
            "runtime_version": "3.13", "image_digest": "sha256:x",
            "profile": "smoke", "members": ["r1", "r2"], "repeat_count": 2,
            "aggregated": {
                "overall_rps": {"median": 120.0},
                "p95_ms": {"median": 12.5},
                "cpu_peak_pct_net": {"median": 40.0},
            },
        }
        cmp = campaign._campaign_as_comparison({
            "schema_version": 2, "generated_utc": "now",
            "comparable": True, "comparison_reasons": [],
            "sites": [rec], "leaderboard": [rec],
        })
        self.assertTrue(cmp["comparable"])
        self.assertEqual(len(cmp["sites"]), 1)
        self.assertAlmostEqual(cmp["sites"][0]["overall_rps"], 120.0, places=3)
        self.assertAlmostEqual(cmp["sites"][0]["p95_ms"], 12.5, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
