"""Optional browser-side metrics via the Chrome DevTools Protocol.

Standard-library only: a tiny RFC-6455 WebSocket client over ``socket`` plus
``http.client`` for ``/json/list``. No Playwright / Selenium install is needed;
if those happen to be installed they can replace this adapter later without
changing the ``metrics.browser`` schema.

Failure is always non-fatal: ``collect()`` returns
``{"available": False, "error": ...}`` and the run continues with
``browser_series = []`` / ``per_metric_visibility["metrics.browser.*"] =
"unavailable"``.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import struct
import time
from urllib.parse import urlparse


class CDPError(Exception):
    pass


class CDPClient:
    """Minimal WebSocket client sufficient for CDP ``*.evaluate`` calls."""

    def __init__(self, ws_url, timeout=5.0):
        u = urlparse(ws_url)
        self.host = u.hostname or "127.0.0.1"
        self.port = u.port or 9222
        self.path = u.path or "/"
        self.timeout = timeout
        self.sock = None
        self._id = 0
        self._buf = b""

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
            % (self.path, self.host, self.port, key))
        self.sock.sendall(req.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CDPError("websocket handshake closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise CDPError("websocket upgrade rejected")
        # Any bytes after the header belong to the first frame.
        self._buf = head.split(b"\r\n\r\n", 1)[1]
        return self

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CDPError("socket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, payload, opcode=1):
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(head + mask + masked)

    def _recv_frame(self):
        b0, b1 = self._recv_exact(2)
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._recv_exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else None
        data = self._recv_exact(n)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 9:                    # ping -> pong
            self._send_frame(data, opcode=10)
            return None
        if opcode == 8:                    # close
            raise CDPError("websocket closed by peer")
        return data.decode("utf-8", "replace") if opcode == 1 else b""

    def call(self, method, params=None, timeout=None):
        self._id += 1
        ident = self._id
        self._send_frame(json.dumps({"id": ident, "method": method,
                                     "params": params or {}}))
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            text = self._recv_frame()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except Exception:
                continue
            if msg.get("id") == ident:
                if "error" in msg:
                    raise CDPError(str(msg["error"]))
                return msg.get("result") or {}
        raise CDPError("timeout waiting for %s" % method)

    def evaluate(self, expression, timeout=None):
        res = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, timeout=timeout)
        return (res.get("result") or {}).get("value")

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *_exc):
        self.close()


def list_targets(cdp_url="http://127.0.0.1:9222", timeout=3.0):
    """Return the browser's page targets from ``/json/list``."""
    u = urlparse(cdp_url)
    conn = http.client.HTTPConnection(u.hostname or "127.0.0.1",
                                      u.port or 9222, timeout=timeout)
    try:
        conn.request("GET", "/json/list")
        r = conn.getresponse()
        data = json.loads(r.read().decode("utf-8", "replace"))
        return [t for t in data if t.get("type") == "page"] if isinstance(data, list) else []
    finally:
        conn.close()


_METRICS_JS = r"""
(() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paints = performance.getEntriesByType('paint') || [];
  const fcp = paints.find(p => p.name === 'first-contentful-paint');
  const longtasks = performance.getEntriesByType('longtask') || [];
  let tbt = 0;
  for (const t of longtasks) tbt += Math.max(0, t.duration - 50);
  const mem = performance.memory || null;
  return {
    ttfb_ms: (nav.responseStart != null && nav.requestStart != null)
             ? +(nav.responseStart - nav.requestStart).toFixed(2) : null,
    dom_content_loaded_ms: (nav.domContentLoadedEventEnd != null)
             ? +(nav.domContentLoadedEventEnd - (nav.startTime||0)).toFixed(2) : null,
    fcp_ms: fcp ? +fcp.startTime.toFixed(2) : null,
    lcp_ms: (window.__hlcLcp != null) ? +window.__hlcLcp.toFixed(2) : null,
    tbt_ms: +tbt.toFixed(2),
    cls: (window.__hlcCls != null) ? window.__hlcCls : null,
    js_heap_bytes: mem ? mem.usedJSHeapSize : null
  };
})()
"""


def collect(cdp_url, target_url, timeout=15.0, settle_s=2.0):
    """Navigate a headless Chrome page and return ``metrics.browser``.

    Returns ``{"available": False, "error": ...}`` on any failure so callers
    can simply skip browser metrics without aborting a benchmark.
    """
    try:
        targets = list_targets(cdp_url, timeout=min(5.0, timeout))
        if not targets:
            return {"available": False, "error": "no CDP page target"}
        ws = targets[0].get("webSocketDebuggerUrl")
        if not ws:
            return {"available": False, "error": "no webSocketDebuggerUrl"}
        with CDPClient(ws, timeout=min(10.0, timeout)) as c:
            c.call("Page.enable")
            c.call("Runtime.enable")
            c.call("Page.navigate", {"url": target_url})
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                state = c.evaluate("document.readyState", timeout=3.0)
                if state == "complete":
                    break
                time.sleep(0.25)
            time.sleep(settle_s)
            raw = c.evaluate(_METRICS_JS, timeout=5.0) or {}
        out = {k: raw.get(k) for k in
               ("ttfb_ms", "fcp_ms", "lcp_ms",
                "dom_content_loaded_ms", "tbt_ms", "cls")}
        out["js_heap_bytes"] = raw.get("js_heap_bytes")
        out["available"] = True
        out["source"] = "chrome-devtools-protocol"
        return out
    except Exception as ex:
        return {"available": False, "error": "%s: %s"
                % (type(ex).__name__, ex)}
