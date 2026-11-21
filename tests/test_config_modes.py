#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Config normalization suite (v2: single client-only runtime environment).

Covers:
  A. defaults, legacy Docker-era config migration (mapped to the host
     environment with a recorded note, keys stripped - never published)
  B. schedule presets including the new heavy / stress profiles
  C. suitability weight coercion
  D. server-side comparability policy (schema.can_compare)
"""
from __future__ import annotations

import sys
import os
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from heavyload import config as C  # noqa: E402
from heavyload.schema import can_compare, empty_fingerprint  # noqa: E402
from tests.helpers import make_result, write_run  # noqa: E402


class TestDefaultsAndLegacyMigration(unittest.TestCase):
    def test_fresh_config_defaults_to_host_client_only(self):
        cfg = C.normalize({})
        self.assertEqual(cfg["runtime_environment"], "windows")
        self.assertEqual(cfg["mode"], "direct")
        self.assertEqual(cfg["benchmark_mode"], "live_blackbox")
        self.assertNotIn("container", cfg)
        self.assertNotIn("mysql_container", cfg)
        self.assertNotIn("include_container",
                         cfg["runtime_comparison"])

    def test_legacy_docker_config_maps_to_host_with_note(self):
        cfg = C.normalize({
            "runtime_environment": "docker",
            "container": "web1",
            "mysql_container": "db1",
            "benchmark_mode": "docker_lab",
            "runtime_comparison": {"include_container": True},
        })
        self.assertEqual(cfg["runtime_environment"], "windows")
        self.assertEqual(cfg["benchmark_mode"], "live_blackbox")
        self.assertNotIn("container", cfg)
        note = cfg["_derived"].get("legacy_migration_note", "")
        self.assertIn("Docker-era", note)
        self.assertIn("container", note)

    def test_legacy_keys_never_leak_into_saved_config(self):
        cfg = C.normalize({"container": "web", "stats_period_s": 4})
        self.assertNotIn("container", cfg)
        self.assertNotIn("stats_period_s", cfg)

    def test_invalid_runtime_values_self_heal_to_windows(self):
        for v in (None, "", "   ", "WINDOWS", "nonsense", "docker-lab",
                  "auto", 123, [], {}, True):
            cfg = C.normalize({"runtime_environment": v})
            self.assertEqual(cfg["runtime_environment"], "windows")

    def test_mode_is_pinned(self):
        # Any legacy mode value coerces to the single direct mode; a Docker-
        # era mode additionally records the migration note.
        cfg = C.normalize({"mode": "auto"})
        self.assertEqual(cfg["mode"], "direct")
        cfg = C.normalize({"mode": "docker"})
        self.assertEqual(cfg["mode"], "direct")
        self.assertIn("Docker-era", cfg["_derived"].get(
            "legacy_migration_note", ""))


class TestPresets(unittest.TestCase):
    def test_all_presets_present_with_new_profiles(self):
        for name in ("smoke", "quick", "heavy", "stress", "standard"):
            self.assertIn(name, C.PRESETS)
            phases = C.preset_phases(name)
            self.assertEqual(len(phases), 8)
            self.assertEqual({p["role"] for p in phases} & {"idle", "cooldown",
                                                            "reprobe"},
                             {"idle", "cooldown", "reprobe"})

    def test_heavy_profile_reaches_c200(self):
        phases = C.preset_phases("heavy")
        peak = max(max(p.get("steps") or [p["concurrency"]]) for p in phases)
        self.assertEqual(peak, 200)
        self.assertEqual(C.PRESETS["heavy"]["max_concurrency"], 200)

    def test_stress_profile_reaches_c400(self):
        phases = C.preset_phases("stress")
        peak = max(max(p.get("steps") or [p["concurrency"]]) for p in phases)
        self.assertEqual(peak, 400)

    def test_peak_concurrency_capped_by_max(self):
        cfg = C.normalize({"profile": "stress"})
        self.assertLessEqual(cfg["_derived"]["peak_concurrency"],
                             cfg["max_concurrency"])


class TestSuitabilityConfig(unittest.TestCase):
    def test_weights_coerced_and_unknown_ignored(self):
        # config.normalize coerces numeric weights; unknown criteria keys are
        # ignored by the suitability module itself (never scored).
        from heavyload import suitability as SU
        cfg = C.normalize({"suitability": {"weights": {
            "compute_performance": "20", "bogus": 5, "negative": -1}}})
        w = cfg["suitability"]["weights"]
        self.assertEqual(w.get("compute_performance"), 20.0)
        self.assertNotIn("negative", w)
        self.assertTrue(cfg["suitability"]["enabled"])
        # The module filters unknown keys when assembling weights.
        with tempfile.TemporaryDirectory() as tmp:
            d = SU.build({"target": {}}, cfg, tmp)
            self.assertNotIn("bogus", d["weights"])
            self.assertIn("compute_performance", d["weights"])


class TestComparability(unittest.TestCase):
    def _fp(self, gen="hostA", tgt="127.0.0.1"):
        fp = empty_fingerprint()
        fp["generator_fingerprint"] = gen
        fp["target_host"] = tgt
        return fp

    def _result(self, run_id, gen="hostA", tgt="127.0.0.1"):
        return make_result(run_id=run_id, mode="live_blackbox",
                           visibility="client_only_same_host",
                           fingerprint=self._fp(gen, tgt))

    def test_same_host_client_only_pair_is_comparable(self):
        a = self._result("r1")
        b = self._result("r2")
        ok, why = can_compare(a, b)
        self.assertTrue(ok, why)
        self.assertIn("single-host", why)

    def test_different_generator_hosts_never_compare(self):
        ok, why = can_compare(self._result("r1", gen="hostA"),
                              self._result("r2", gen="hostB"))
        self.assertFalse(ok)
        self.assertIn("different machines", why)

    def test_different_target_hosts_never_compare(self):
        ok, why = can_compare(self._result("r1", tgt="127.0.0.1"),
                              self._result("r2", tgt="10.0.0.5"))
        self.assertFalse(ok)

    def test_plain_client_only_still_refused(self):
        a = make_result(run_id="r1", mode="live_blackbox",
                        visibility="client_only")
        ok, why = can_compare(a, make_result(
            run_id="r2", mode="live_blackbox", visibility="client_only"))
        self.assertFalse(ok)
        self.assertIn("remote", why)

    def test_archived_docker_pair_still_comparable(self):
        a = make_result(run_id="d1", mode="docker_lab",
                        visibility="full_server")
        b = make_result(run_id="d2", mode="docker_lab",
                        visibility="full_server")
        ok, _ = can_compare(a, b)
        self.assertTrue(ok)

    def test_mixed_modes_refused(self):
        ok, why = can_compare(
            make_result(run_id="d1", mode="docker_lab",
                        visibility="full_server"),
            self._result("r1"))
        self.assertFalse(ok)
        self.assertIn("benchmark_mode", why)


if __name__ == "__main__":
    unittest.main()
