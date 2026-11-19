#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Demo target: a small, real HTTP server for load-testing practice.

Serves the route mix the default config expects (/, /catalog, /health) with
genuine (if light) work per request, so every number HLC publishes about it is
a real measurement. Stdlib only, no dependencies.

Usage:
  python scripts/demo_target.py                 # listen on 127.0.0.1:8090
  python scripts/demo_target.py --port 3000     # custom port
  python scripts/demo_target.py --work 200      # CPU loops per request
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _busy(loops: int) -> int:
    """Real CPU work: a small hash-sum loop so latency is nonzero and honest."""
    acc = 0
    for i in range(loops):
        acc = (acc * 31 + i) & 0xFFFFFFFF
    return acc


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HLC-DemoTarget/1.0"

    work_loops = 200  # overridden by --work

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        t0 = time.perf_counter()
        path = self.path.split("?", 1)[0]
        if path == "/health":
            _busy(Handler.work_loops // 4)
            self._send(200, {"status": "ok", "ts": time.time()})
        elif path == "/catalog":
            _busy(Handler.work_loops * 3)
            items = [{"id": i, "name": "item-%d" % i, "price": 9.99 + i % 7}
                     for i in range(20)]
            self._send(200, {"items": items,
                             "compute_ms": round(
                                 (time.perf_counter() - t0) * 1000, 3)})
        elif path == "/":
            _busy(Handler.work_loops)
            self._send(200, {"message": "Hello from the HLC demo target",
                             "compute_ms": round(
                                 (time.perf_counter() - t0) * 1000, 3)})
        else:
            self._send(404, {"error": "not found", "path": path})

    def log_message(self, fmt, *args) -> None:  # quiet unless verbose
        if "-v" in sys.argv:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> int:
    ap = argparse.ArgumentParser(description="HLC demo load-test target")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--work", type=int, default=200,
                    help="CPU loop iterations per request unit")
    a = ap.parse_args()
    Handler.work_loops = max(1, a.work)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("Demo target listening on http://%s:%d/  (routes: /, /catalog, "
          "/health) - Ctrl+C to stop" % (a.host, a.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
