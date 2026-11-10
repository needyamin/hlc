"""v1 -> v2 migration must be lossless and must not break report/grading."""

from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import grading, report  # noqa: E402
from heavyload.schema import (  # noqa: E402
    SCHEMA_VERSION, can_compare, ensure_current, is_current,
)


def archived_results():
    return sorted(glob.glob(os.path.join(ROOT, "_archive", "runs",
                                        "*", "results.json")))


class SchemaRoundTrip(unittest.TestCase):
    def test_archived_v1_runs_are_present(self):
        self.assertTrue(archived_results(),
                        "run scripts/archive_legacy.py backup first")

    def test_migration_is_lossless_and_idempotent(self):
        for path in archived_results():
            with self.subTest(path=os.path.basename(os.path.dirname(path))):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                before = set(data.keys())
                ensure_current(data)
                self.assertTrue(before.issubset(set(data.keys())))
                self.assertTrue(is_current(data))
                self.assertEqual(data["schema_version"], SCHEMA_VERSION)
                self.assertIn(data["visibility"],
                              ("full_server", "client_only",
                               "server_via_adapter"))
                self.assertIn(data["benchmark_mode"],
                              ("docker_lab", "live_blackbox",
                               "instrumented_live"))
                self.assertIn("metrics.http.*",
                              data["per_metric_visibility"])
                for group in ("http", "resources", "runtime", "browser"):
                    self.assertIn(group, data["metrics"])
                for key in ("proc_series", "runtime_series",
                            "browser_series", "repeats"):
                    self.assertIn(key, data["series"])
                snapshot = json.dumps(data, sort_keys=True, default=str)
                ensure_current(data)       # second call must be a no-op
                self.assertEqual(snapshot,
                                 json.dumps(data, sort_keys=True, default=str))

    def test_report_and_grading_still_work_on_migrated_results(self):
        for path in archived_results():
            with self.subTest(path=os.path.basename(os.path.dirname(path))):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ensure_current(data)
                verdict = grading.grade(data)
                self.assertIn("verdict", verdict)
                self.assertIn("health_score", verdict)
                with tempfile.TemporaryDirectory() as td:
                    out = os.path.join(td, "report.html")
                    report.build(data, out)
                    self.assertTrue(os.path.getsize(out) > 1000)

    def test_comparability_guard(self):
        lab = {"schema_version": SCHEMA_VERSION,
               "benchmark_mode": "docker_lab", "visibility": "full_server",
               "meta": {"workload_class": "http_loadtest"}}
        live = dict(lab, benchmark_mode="live_blackbox",
                    visibility="client_only")
        self.assertFalse(can_compare(lab, live)[0])
        self.assertTrue(can_compare(lab, dict(lab))[0])

    def test_empty_mysql_series_is_labelled_unavailable(self):
        data = {"meta": {"target_url": "http://localhost/"},
                "resources": {}, "summary": {}, "series": {}}
        ensure_current(data, mode="live_blackbox")
        self.assertEqual(
            data["per_metric_visibility"].get("series.mysql_series"),
            "unavailable")


if __name__ == "__main__":
    unittest.main()
