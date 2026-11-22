#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os


class BenchHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            body = json.dumps({
                "ok": True,
                "service": "python-hlc",
                "routes": ["/", "/health", "/catalog", "/cart"],
            }).encode("utf-8")
        elif path == "/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
        elif path == "/catalog":
            body = json.dumps({
                "items": [
                    {"id": 1, "name": "Widget", "price": 1999},
                    {"id": 2, "name": "Gadget", "price": 3499},
                    {"id": 3, "name": "Doohickey", "price": 999},
                ]
            }).encode("utf-8")
        elif path == "/cart":
            body = json.dumps({
                "items": [{"id": 1, "qty": 2}],
                "subtotal": 3998,
                "currency": "USD",
            }).encode("utf-8")
        else:
            msg = b"404 Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("HLC_PORT", "18093"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), BenchHandler)
    httpd.daemon_threads = True
    httpd.serve_forever()


if __name__ == "__main__":
    main()
