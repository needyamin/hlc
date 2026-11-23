#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for dynamic local target resolution, auto-start, and cleanup."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import config as C  # noqa: E402
from heavyload import http_client  # noqa: E402
from heavyload import runner  # noqa: E402
from heavyload import target_manager as TM  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_temp_target_workspace(root: str, port: int, site_id: str = "tiny-py") -> None:
    os.makedirs(os.path.join(root, "apps", site_id), exist_ok=True)
    os.makedirs(os.path.join(root, "targets"), exist_ok=True)
    srv_code = """
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        body = json.dumps({"status": "ok", "path": self.path}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

port = int(os.environ.get("HLC_PORT", "0"))
ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
""".strip()
    with open(os.path.join(root, "apps", site_id, "server.py"), "w", encoding="utf-8") as f:
        f.write(srv_code)

    spec = {
        "schema_version": 2,
        "site_id": site_id,
        "language": "python",
        "runtime_version": "3.x",
        "framework": "stdlib",
        "host_port": port,
        "health_path": "/health",
        "routes": [{"path": "/", "weight": 50}, {"path": "/health", "weight": 50}],
        "native": {
            "start_cmd": "python server.py",
            "working_dir": "apps/" + site_id,
            "build_cmd": "",
            "ready_probe": True,
        },
    }
    with open(os.path.join(root, "targets", site_id + ".json"), "w", encoding="utf-8") as f:
        json.dump(spec, f)


class TestResolution(unittest.TestCase):
    def test_resolves_workspace_local_specs(self):
        for url, expected_id in (
            ("http://localhost:18091/", "spid3r-http"),
            ("http://127.0.0.1:18092/", "node-http"),
            ("http://127.0.0.1:18093/", "python-http"),
            ("http://localhost:8090/", "python-demo"),
        ):
            cfg = C.normalize({"target_url": url})
            sp = TM.resolve_target_for_cfg(cfg, ROOT)
            self.assertIsNotNone(sp, "expected local spec for %s" % url)
            self.assertEqual(sp["site_id"], expected_id)

    def test_port_change_overrides_stale_site_id(self):
        cfg = C.normalize({
            "target_url": "http://127.0.0.1:18093/",
            "target": {"site_id": "spid3r-http"},
        })
        sp = TM.resolve_target_for_cfg(cfg, ROOT)
        self.assertIsNotNone(sp)
        self.assertEqual(sp["site_id"], "python-http")

    def test_remote_and_unknown_loopback_never_resolve(self):
        for url in ("https://example.com/", "http://127.0.0.1:59997/"):
            cfg = C.normalize({"target_url": url})
            self.assertIsNone(TM.resolve_target_for_cfg(cfg, ROOT),
                              "must not substitute a target for %s" % url)

    def test_list_available_targets_skips_unlaunchable_examples(self):
        rows = TM.list_available_targets(ROOT, probe_live=False)
        ids = {r["site_id"] for r in rows}
        self.assertIn("spid3r-http", ids)
        self.assertIn("node-http", ids)
        self.assertIn("python-http", ids)
        self.assertIn("python-demo", ids)
        self.assertNotIn("python-fastapi-hello", ids)


class TestLifecycleAndAutoStart(unittest.TestCase):
    def test_ensure_start_idempotent_and_stop(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as root:
            _make_temp_target_workspace(root, port, "tiny-life")
            cfg = C.normalize({"target_url": "http://localhost:%d/" % port})
            try:
                r1 = TM.ensure_target_running(cfg, root, timeout_s=10)
                self.assertTrue(r1["started"])
                self.assertEqual(r1["site_id"], "tiny-life")
                self.assertGreater(r1["pid"], 0)

                # Second call must see it already running and not respawn.
                r2 = TM.ensure_target_running(cfg, root, timeout_s=5)
                self.assertFalse(r2["started"])
                self.assertTrue(r2["already_running"])
            finally:
                stopped = TM.stop_target("tiny-life", root=root)
                self.assertTrue(stopped)

            # After stop, port must no longer answer.
            p = http_client.probe("http", "127.0.0.1", port, "/health", timeout=1)
            self.assertEqual(p["status"], 0)

    def test_run_benchmark_auto_starts_and_cleans_up_local_target(self):
        port = _free_port()
        with tempfile.TemporaryDirectory() as root:
            _make_temp_target_workspace(root, port, "tiny-bench")
            cfg = C.normalize({
                "target_url": "http://localhost:%d/" % port,
                "profile": "smoke",
                "phases": [
                    {"name": "01-quick", "label": "Quick (C=2)",
                     "duration_s": 1, "concurrency": 2, "probe_hz": 1,
                     "role": "active"},
                ],
                "runtime_comparison": {"enabled": False},
                "suitability": {"enabled": False},
            })
            # Even with a stale failed preflight, run_benchmark must auto-start
            # the matching local spec and succeed.
            stale_pf = {
                "target_url": cfg["target_url"],
                "checks": [],
                "target_probe": {"status": 0, "error": "ConnectionRefusedError"},
            }
            info = runner.run_benchmark(
                cfg, root, preflight_result=stale_pf,
                auto_start_target=True, keep_target_alive=False)
            self.assertTrue(os.path.isfile(info["results_path"]))
            self.assertTrue(os.path.isfile(info["report_path"]))
            with open(info["results_path"], "r", encoding="utf-8") as f:
                res = json.load(f)
            self.assertGreater(res["summary"]["total_requests"], 0)
            self.assertEqual(res["summary"]["s1_errors"], 0)

            # Target was stopped cleanly because keep_target_alive=False.
            p = http_client.probe("http", "127.0.0.1", port, "/health", timeout=1)
            self.assertEqual(p["status"], 0)


if __name__ == "__main__":
    unittest.main()
