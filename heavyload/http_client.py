"""One HTTP connection factory + one-shot probe, shared by all call sites.

Previously ``runner._connect``, ``samplers._health_probe`` and
``engine._worker`` each built connections (and unverified TLS contexts)
separately. Centralising them keeps preflight, health probes and the load
generator behaviourally identical.

TLS verification stays off on purpose: this is a local load generator, not a
TLS validator. That fact is disclosed in every report.
"""

from __future__ import annotations

import http.client
import ssl

from . import latency


def make_connection(scheme, host, port, timeout):
    """Return a new HTTP(S)Connection (not yet connected)."""
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=timeout,
            context=ssl._create_unverified_context())
    return http.client.HTTPConnection(host, port, timeout=timeout)


def connection_kwargs(scheme, timeout):
    """For callers that need to construct a connection class themselves."""
    if scheme == "https":
        return {"timeout": timeout,
                "context": ssl._create_unverified_context()}
    return {"timeout": timeout}


def probe(scheme, host, port, path, *, method="GET", timeout=6,
          headers=None, user_agent="heavy-load-checker/preflight"):
    """One lightweight request; returns a dict and never raises.

    Adds ``ttfb_ms`` / ``body_ms`` / ``dispatch_delay_ms`` while preserving
    the original ``{path, method, status, error, latency_ms, bytes, headers}``
    shape used by preflight and route discovery.
    """
    out = {"path": path, "method": method, "status": 0, "error": "",
           "latency_ms": 0.0, "ttfb_ms": None, "connect_ms": None,
           "bytes": 0, "headers": {}}
    hdrs = {"User-Agent": user_agent, "Accept-Encoding": "identity",
            "Connection": "close"}
    if headers:
        hdrs.update({str(k): str(v) for k, v in headers.items()})

    sw = latency.Stopwatch()
    conn = None
    try:
        conn = make_connection(scheme, host, port, timeout)
        conn.connect()
        sw.mark_connect()
        conn.request(method, path, headers=hdrs)
        resp = conn.getresponse()
        sw.mark_ttfb()
        body = resp.read()
        sw.mark_total()
        out.update({
            "status": resp.status,
            "bytes": len(body),
            "headers": {k: v for k, v in dict(resp.getheaders()).items()
                        if k.lower() in ("content-type", "server",
                                         "cache-control", "location",
                                         "x-powered-by")},
        })
    except Exception as ex:
        out["error"] = type(ex).__name__
        sw.mark_total()
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    out.update(sw.as_dict())
    out["latency_ms"] = out["latency_ms"] or 0.0
    return out


def probe_cfg(cfg, path="/", method="GET", timeout=6):
    """Convenience wrapper around ``probe`` using a normalised config."""
    d = cfg["_derived"]
    return probe(d["scheme"], d["host"], d["port"], path, method=method,
                 timeout=timeout)
