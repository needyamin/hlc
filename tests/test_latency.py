"""High-resolution timing, TTFB splitting and coordinated-omission fields."""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import http_client  # noqa: E402
from heavyload.latency import Stopwatch, ms, now_ns, since_ms  # noqa: E402
from heavyload.collectors.procfs import (  # noqa: E402
    parse_proc_line, summarize_proc_series,
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class StopwatchTests(unittest.TestCase):
    def test_ns_helpers(self):
        t = now_ns()
        time.sleep(0.002)
        self.assertGreaterEqual(since_ms(t), 1.0)
        self.assertAlmostEqual(ms(2_500_000), 2.5)

    def test_boundaries_are_ordered(self):
        scheduled = now_ns()
        sw = Stopwatch(scheduled_ns=scheduled)
        sw.mark_connect()
        time.sleep(0.001)
        sw.mark_ttfb()
        time.sleep(0.001)
        sw.mark_total()
        d = sw.as_dict()
        self.assertGreaterEqual(d["ttfb_ms"], 0.0)
        self.assertGreaterEqual(d["latency_ms"], d["ttfb_ms"])
        self.assertGreaterEqual(d["body_ms"], 0.0)
        self.assertGreaterEqual(d["dispatch_delay_ms"], 0.0)
        self.assertGreaterEqual(d["connect_ms"], 0.0)


class ProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.srv.server_address[1]
        cls.th = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.th.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_probe_reports_ttfb_and_body(self):
        r = http_client.probe("http", "127.0.0.1", self.port, "/",
                              timeout=3)
        self.assertEqual(r["status"], 200)
        self.assertEqual(r["bytes"], 2)
        self.assertIsNotNone(r["ttfb_ms"])
        self.assertLessEqual(r["ttfb_ms"], r["latency_ms"])
        self.assertIsNotNone(r["body_ms"])
        self.assertIn("Server", r["headers"])

    def test_probe_failure_never_raises(self):
        r = http_client.probe("http", "127.0.0.1", 1, "/", timeout=1)
        self.assertEqual(r["status"], 0)
        self.assertTrue(r["error"])


class ProcfsTests(unittest.TestCase):
    def test_parse_and_summarize(self):
        rows = [{"pid": 1, "comm": "python", "rss_bytes": 4096,
                 "utime": 10, "stime": 5, "threads": 2}]
        line = 'PROCJSON ' + __import__("json").dumps(rows)
        self.assertEqual(parse_proc_line(line), rows)
        self.assertIsNone(parse_proc_line("anon 123"))
        self.assertIsNone(parse_proc_line("PROCJSON not-json"))
        series = [{"t": 1.0, "phase": "idle", "processes": rows}]
        s = summarize_proc_series(series)
        self.assertEqual(s["samples"], 1)
        self.assertEqual(s["peak_total_rss_bytes"], 4096)


if __name__ == "__main__":
    unittest.main()
