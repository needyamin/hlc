"""Guard against the instrumentation perturbing the measurements it takes.

The v2 sampler stack is deliberately tiny (one health-probe thread + one
generator self-monitor thread). These static checks pin the properties that
keep the observer cheap and honest:

  * no sampler thread busy-loops or shells out (nothing to contaminate)
  * the generator sampler derives CPU% from process_time deltas and keeps
    its own working set bounded
  * measurement provenance is recorded, never implied
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import samplers  # noqa: E402


class StaticChecks(unittest.TestCase):
    def test_sampler_module_spawns_no_subprocesses(self):
        import inspect
        src = inspect.getsource(samplers)
        for forbidden in ("subprocess", "os.system", "os.popen", "Popen(",
                          "docker", "shell"):
            self.assertNotIn(forbidden, src,
                             "sampler must never shell out (found %r)"
                             % forbidden)

    def test_only_expected_background_threads(self):
        import inspect
        src = inspect.getsource(samplers.SamplerSet.start)
        self.assertIn("hlc-health", src)
        self.assertIn("hlc-generator", src)
        self.assertNotIn("hlc-cgroup", src)
        self.assertNotIn("hlc-stats", src)
        self.assertNotIn("hlc-mysql", src)

    def test_client_only_is_always_true(self):
        s = samplers.SamplerSet({})
        self.assertTrue(s.client_only)

    def test_generator_sampler_derives_cpu_pct(self):
        s = samplers.SamplerSet({"sample_period_s": 0.05}, t0=None)
        import threading
        s.stop_evt = threading.Event()

        def fake_wait(_):
            return True

        s.stop_evt.wait = fake_wait
        recs = []
        ticks = iter(range(1, 100))

        def fake_push(key, rec):
            recs.append(rec)
            if len(recs) >= 3:
                s.stop_evt.set()
        s._push = fake_push
        # Advance the fake clock 50 ms on every sample so the CPU% delta
        # derivation has a real time base.
        s._t = lambda: round(next(ticks) * 0.05, 3)
        s._generator_sampler()
        self.assertGreaterEqual(len(recs), 3)
        for r in recs:
            self.assertIn("cpu_time_s", r)
            self.assertIn("phase", r)
        # Second sample onward must carry the derived cpu_pct.
        self.assertIsNotNone(recs[1].get("cpu_pct"))
        self.assertGreaterEqual(recs[1]["cpu_pct"], 0.0)

    def test_self_memory_never_raises(self):
        rss, peak = samplers._self_memory()
        # Either real numbers or honest None; never an exception.
        self.assertTrue(rss is None or rss > 0)
        self.assertTrue(peak is None or peak >= (rss or 0))

    def test_snapshot_keys_are_client_side_only(self):
        s = samplers.SamplerSet({})
        snap = s.snapshot()
        for key in ("gen_rss_mib", "gen_cpu_pct", "probes",
                    "generator_samples"):
            self.assertIn(key, snap)
        for gone in ("anon_mib", "cgroup_samples", "pids"):
            self.assertNotIn(gone, snap)


if __name__ == "__main__":
    unittest.main()
