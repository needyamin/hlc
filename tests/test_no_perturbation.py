"""Guard against the sampler contaminating the target's CPU numbers.

Static checks always run. The live Docker check is opt-in: set
HLC_TEST_CONTAINER=<name> with Docker Desktop running, otherwise it is skipped
(and reported as SKIP, never as a pass).

Historical bug: orphaned in-container shell loops inflated idle CPU from
~0.85% to 8-15%. The v2 collector must stay a single process with no shell
forks and a deterministic shutdown handshake.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import docker_probe as dp  # noqa: E402
from heavyload.collectors.cgroup import (  # noqa: E402
    CG_COLLECTOR_PY, CgroupCollector,
)
from heavyload.samplers import parse_cg_line  # noqa: E402


class StaticChecks(unittest.TestCase):
    def test_collector_spawns_no_subprocesses(self):
        for forbidden in ("subprocess", "os.system", "os.popen", "Popen(",
                          "grep ", "awk ", "sed "):
            self.assertNotIn(forbidden, CG_COLLECTOR_PY)

    def test_collector_has_deterministic_shutdown(self):
        self.assertIn("select.select", CG_COLLECTOR_PY)
        self.assertIn('"quit"', CG_COLLECTOR_PY)
        self.assertIn("sys.stdout.flush()", CG_COLLECTOR_PY)

    def test_collector_emits_compatible_protocol(self):
        for token in ("@@S", "CGVER v", "usage_usec", "PROCJSON "):
            self.assertIn(token, CG_COLLECTOR_PY)

    def test_stop_is_safe_when_never_started(self):
        CgroupCollector("no-such-container").stop()

    def test_legacy_shell_is_kept_only_as_fallback(self):
        self.assertIn("FALLBACK", dp.__doc__)
        self.assertIn("HLC_CG_LOOP_7C41B2", dp.CG_LOOP)

    def test_probe_line_roundtrip(self):
        rec = {}
        parse_cg_line(rec, "anon 1048576")
        self.assertEqual(rec["anon_bytes"], 1048576)
        self.assertEqual(rec["anon_mib"], 1.0)


@unittest.skipUnless(os.environ.get("HLC_TEST_CONTAINER"),
                     "set HLC_TEST_CONTAINER=<name> and start Docker Desktop")
class LiveIdleCheck(unittest.TestCase):
    def test_idle_cpu_stays_below_1_5_percent(self):
        container = os.environ["HLC_TEST_CONTAINER"]
        col = CgroupCollector(container, period=0.2)
        self.assertTrue(col.start(), "collector could not start")
        self.assertEqual(col.mode, "python_v2",
                         "expected the low-perturbation collector, got %s"
                         % col.mode)
        samples, procs, started = [], [], time.monotonic()
        try:
            rec = None
            for line in col.lines():
                line = line.strip()
                if line == "@@S":
                    if rec:
                        samples.append(rec)
                    rec = {}
                elif line.startswith("PROCJSON "):
                    procs.append(line)
                elif rec is not None:
                    parse_cg_line(rec, line)
                if time.monotonic() - started > 10:
                    break
        finally:
            col.stop()
        self.assertGreaterEqual(len(samples), 5)
        self.assertTrue(procs, "no per-process samples emitted")
        usages = [(s.get("usage_usec"), i) for i, s in enumerate(samples)
                  if s.get("usage_usec") is not None]
        if len(usages) >= 2:
            (u0, _), (u1, _) = usages[0], usages[-1]
            # Rough but sufficient guard: <1.5% of one core over the window.
            cpu_pct = (u1 - u0) / (10.0 * 1e6) * 100.0
            self.assertLess(cpu_pct, 1.5,
                            "idle CPU %.2f%% - collector perturbs the target"
                            % cpu_pct)
        self.assertIsNone(col.proc, "collector process handle was not cleared")


if __name__ == "__main__":
    unittest.main()
