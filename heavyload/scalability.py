#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scalability and concurrency analysis - measured evidence only.

The engine records one time window per concurrency level (per step inside
ramp/spike phases and one window per fixed-concurrency phase). This module
merges those windows into a concurrency -> throughput curve and derives:

  * the scaling curve itself (C -> RPS, p95, error rate, efficiency RPS/C)
  * the knee point: the last concurrency level before throughput stops
    improving or latency inflates beyond a factor threshold
  * a conservative classification: linear / sublinear / plateau / degraded

Everything below is computed from ``step_metrics`` the engine already
measured; nothing is estimated. With fewer than two measured levels the
analysis reports ``insufficient_evidence`` instead of guessing a curve.
"""

from __future__ import annotations

from .util import safe_div

# p95 inflation beyond this factor (vs the best measured level) marks the
# degradation knee, together with the marginal-throughput collapse check.
_KNEE_LATENCY_FACTOR = 2.0
# Marginal throughput gain per extra unit of concurrency below which the
# curve is considered flat (fraction of the best level's efficiency).
_KNEE_EFFICIENCY_DROP = 0.5


def analyze(results):
    """Build the scalability block from engine step metrics. Never raises."""
    try:
        return _analyze(results or {})
    except Exception as ex:
        return {"status": "error", "classification": "unavailable",
                "warnings": ["%s: %s" % (type(ex).__name__, ex)]}


def _analyze(results):
    steps = results.get("step_metrics") or []
    # Only load-bearing windows: idle/cooldown windows have C=0 and no
    # requests; they say nothing about scaling.
    measured = [s for s in steps
                if s.get("concurrency") and int(s["concurrency"]) > 0
                and s.get("requests")]
    if len(measured) < 1:
        return {
            "status": "insufficient_evidence",
            "classification": "unavailable",
            "note": "no measured concurrency windows (phases with steps>1 "
                    "and requests provide the evidence)",
            "points": [],
        }
    # Merge windows that share a concurrency level (e.g. ramp step C=50 and
    # a sustained C=50 phase): throughput and percentiles are aggregated by
    # total requests so longer windows weigh proportionally.
    by_c = {}
    for s in measured:
        c = int(s["concurrency"])
        acc = by_c.setdefault(c, {
            "concurrency": c, "requests": 0, "errors": 0, "duration_s": 0.0,
            "windows": 0,
            "lat": {"p50": [], "p95": [], "p99": []},
        })
        acc["requests"] += s.get("requests", 0)
        acc["errors"] += s.get("errors", 0)
        acc["duration_s"] += float(s.get("duration_s") or 0.0)
        acc["windows"] += 1
        lat200 = (s.get("latency_200") or {})
        for k in ("p50", "p95", "p99"):
            v = lat200.get(k)
            if v is not None:
                acc["lat"][k].append(v)
    points = []
    for c in sorted(by_c):
        acc = by_c[c]
        dur = acc["duration_s"] or 1.0
        rps = round(acc["requests"] / dur, 2)
        lat = {}
        for k in ("p50", "p95", "p99"):
            vals = acc["lat"][k]
            lat[k] = round(sum(vals) / len(vals), 2) if vals else None
        points.append({
            "concurrency": c,
            "windows": acc["windows"],
            "requests": acc["requests"],
            "duration_s": round(dur, 2),
            "rps": rps,
            "efficiency_rps_per_worker": round(safe_div(rps, c), 3),
            "error_pct": round(100.0 * safe_div(acc["errors"],
                                                acc["requests"]), 3),
            "p50_ms": lat["p50"], "p95_ms": lat["p95"], "p99_ms": lat["p99"],
        })

    best = max(points, key=lambda p: p["rps"])
    classification = "insufficient_evidence"
    knee = None
    if len(points) >= 2:
        classification = _classify(points, best)
        knee = _find_knee(points, best)

    return {
        "status": "ok",
        "classification": classification,
        "knee_concurrency": knee,
        "best_throughput_concurrency": best["concurrency"],
        "best_rps": best["rps"],
        "points": points,
        "note": ("Classification from measured throughput-vs-concurrency "
                 "windows only; a single fixed-concurrency phase cannot "
                 "distinguish linear from sublinear scaling."
                 if len(points) < 2 else
                 "Derived from the measured concurrency windows above; "
                 "knee = last level before marginal throughput gain collapses "
                 "or p95 inflates beyond %.1fx the best level." %
                 _KNEE_LATENCY_FACTOR),
    }


def _classify(points, best):
    """Conservative scaling classification from measured points only."""
    cs = [p["concurrency"] for p in points]
    if len(points) < 2:
        return "insufficient_evidence"
    # Throughput slope over the ascending curve.
    first, last = points[0], points[-1]
    if last["rps"] <= 0:
        return "degraded"
    gain = last["rps"] / max(0.001, first["rps"])
    c_gain = last["concurrency"] / max(1, first["concurrency"])
    err_last = max(p["error_pct"] for p in points[-2:])
    if err_last > 5.0:
        return "degraded"
    if gain < 0.8 * c_gain and last["rps"] < 1.1 * best["rps"] \
            and last["concurrency"] > best["concurrency"]:
        return "plateau"      # more load, no more throughput
    if gain >= 0.85 * c_gain:
        return "linear"
    return "sublinear"


def _find_knee(points, best):
    """Last concurrency level before the curve stops earning its load.

    A level is past the knee when EITHER (a) its marginal throughput gain
    per unit concurrency falls below half the best level's efficiency, or
    (b) p95 inflates beyond _KNEE_LATENCY_FACTOR x the lowest measured p95.
    """
    knee = None
    baseline_p95 = min((p["p95_ms"] for p in points
                        if p.get("p95_ms")), default=None)
    best_eff = best["efficiency_rps_per_worker"] or 0
    for i, p in enumerate(points):
        if p["concurrency"] == best["concurrency"]:
            continue
        if best_eff > 0 and \
                p["efficiency_rps_per_worker"] < _KNEE_EFFICIENCY_DROP * best_eff:
            knee = p["concurrency"]
        if baseline_p95 and p.get("p95_ms") and \
                p["p95_ms"] > _KNEE_LATENCY_FACTOR * baseline_p95:
            knee = p["concurrency"]
    # The knee is reported as the last GOOD level: the one before the first
    # flagged point (so the value always remains a useful recommendation).
    if knee is None:
        return None
    for i, p in enumerate(points):
        if p["concurrency"] == knee and i > 0:
            return points[i - 1]["concurrency"]
        if p["concurrency"] == knee:
            return None
    return knee
