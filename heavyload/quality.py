#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Data-integrity and anomaly checks for a finished run.

Every check inspects MEASURED evidence (raw.jsonl aggregation counters,
timeline bins, phase windows, resource samples) and produces a structured
finding with status ok / warn / fail and a plain-language detail. Nothing
here fabricates data: a check with insufficient evidence reports
``insufficient_evidence`` and never invents a verdict.

Checks (all measured):
  * malformed_raw_lines   - unreadable records in raw.jsonl
  * timeline_gaps         - sampler/health-series gaps vs the schedule
  * percentile_monotonic  - p50 <= p95 <= p99 sanity per percentile set
  * runt_phases           - active phases with far fewer requests than the
                            schedule implies (target or generator stalled)
  * generator_saturation  - load-generator CPU near saturation or request
                            dispatch lag inflating latency percentiles
  * latency_impossible    - latency mean/percentiles exceeding the schedule
                            arithmetic (e.g. p99 > request timeout)
"""

from __future__ import annotations

from .util import safe_div


def check_run(results):
    """Run every check; returns {status, checks, warnings}. Never raises."""
    checks = []
    try:
        checks.append(_malformed_lines(results))
    except Exception as ex:
        checks.append(_error_check("malformed_raw_lines", ex))
    try:
        checks.append(_timeline_gaps(results))
    except Exception as ex:
        checks.append(_error_check("timeline_gaps", ex))
    try:
        checks.append(_percentile_monotonic(results))
    except Exception as ex:
        checks.append(_error_check("percentile_monotonic", ex))
    try:
        checks.append(_runt_phases(results))
    except Exception as ex:
        checks.append(_error_check("runt_phases", ex))
    try:
        checks.append(_generator_saturation(results))
    except Exception as ex:
        checks.append(_error_check("generator_saturation", ex))
    try:
        checks.append(_latency_bounds(results))
    except Exception as ex:
        checks.append(_error_check("latency_bounds", ex))

    n_warn = sum(1 for c in checks if c["status"] == "warn")
    n_fail = sum(1 for c in checks if c["status"] == "fail")
    status = "fail" if n_fail else ("warn" if n_warn else "ok")
    return {
        "status": status,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "warn": n_warn,
            "fail": n_fail,
        },
        "note": "All checks inspect measured evidence only; "
                "insufficient-evidence checks never guess.",
    }


def _error_check(name, ex):
    return {"name": name, "status": "warn",
            "detail": "check itself failed: %s: %s" % (type(ex).__name__, ex),
            "evidence": {}}


def _malformed_lines(results):
    meta = (results.get("aggregation_meta") or {})
    bad = int(meta.get("malformed_raw_lines") or 0)
    total = int(((results.get("summary") or {}).get("total_requests")) or 0)
    if total == 0:
        status, detail = "insufficient_evidence", "no requests recorded"
    elif bad == 0:
        status, detail = "ok", "all %d raw records parsed cleanly" % total
    elif bad <= max(1, total // 1000):
        status = "warn"
        detail = ("%d of %d raw records were malformed and excluded from "
                  "aggregation (<0.1%%)" % (bad, total))
    else:
        status = "fail"
        detail = ("%d of %d raw records were malformed and excluded - "
                  "aggregation may be unreliable" % (bad, total))
    return {"name": "malformed_raw_lines", "status": status,
            "detail": detail, "evidence": {"malformed": bad, "total": total}}


def _timeline_gaps(results):
    gen = (results.get("series") or {}).get("generator_series") or []
    phases = results.get("phases") or []
    schedule_s = float(((results.get("meta") or {}).get("schedule_s")) or 0)
    if not gen or schedule_s <= 0:
        return {"name": "timeline_gaps", "status": "insufficient_evidence",
                "detail": "no generator samples to check", "evidence": {}}
    span = max(0.0, gen[-1].get("t", 0) - gen[0].get("t", 0))
    # A healthy 1 Hz sampler covers ~the full schedule; allow 15% slack.
    coverage = safe_div(span, schedule_s)
    if coverage >= 0.85:
        status, detail = "ok", (
            "generator sampler covered %.0f%% of the %ds schedule"
            % (coverage * 100, schedule_s))
    elif coverage >= 0.6:
        status, detail = "warn", (
            "generator sampler covered only %.0f%% of the schedule - some "
            "windows lack utilization evidence" % (coverage * 100))
    else:
        status, detail = "warn", (
            "generator sampler covered only %.0f%% of the schedule - "
            "utilization evidence is sparse" % (coverage * 100))
    return {"name": "timeline_gaps", "status": status, "detail": detail,
            "evidence": {"span_s": round(span, 2),
                         "schedule_s": schedule_s,
                         "coverage_pct": round(coverage * 100, 1),
                         "samples": len(gen)}}


def _percentile_monotonic(results):
    summary = results.get("summary") or {}
    sets = []
    for key in ("PRIMARY_successful_200_only", "all_requests_latency",
                "as_scheduled_latency"):
        p = summary.get(key) or {}
        sets.append((key, p))
    checked = 0
    violations = []
    for key, p in sets:
        p50, p95, p99 = p.get("p50"), p.get("p95"), p.get("p99")
        if None in (p50, p95, p99):
            continue
        checked += 1
        if not (p50 <= p95 <= p99):
            violations.append("%s (p50=%s p95=%s p99=%s)"
                              % (key, p50, p95, p99))
    # Phase-level percentile sets as well.
    for ph in results.get("phases") or []:
        p = ph.get("latency_200") or {}
        p50, p95, p99 = p.get("p50"), p.get("p95"), p.get("p99")
        if None in (p50, p95, p99):
            continue
        checked += 1
        if not (p50 <= p95 <= p99):
            violations.append("phase %s (p50=%s p95=%s p99=%s)"
                              % (ph.get("name"), p50, p95, p99))
    if checked == 0:
        return {"name": "percentile_monotonic",
                "status": "insufficient_evidence",
                "detail": "no complete percentile sets found",
                "evidence": {}}
    if violations:
        return {"name": "percentile_monotonic", "status": "fail",
                "detail": "percentile ordering violated in %d set(s): %s"
                % (len(violations), "; ".join(violations[:3])),
                "evidence": {"violations": violations}}
    return {"name": "percentile_monotonic", "status": "ok",
            "detail": "all %d percentile sets are ordered p50<=p95<=p99"
            % checked, "evidence": {"checked": checked}}


def _runt_phases(results):
    phases = [p for p in (results.get("phases") or [])
              if p.get("role") in ("active", "sustained", "spike", "soak")
              and p.get("duration_s")]
    if not phases:
        return {"name": "runt_phases", "status": "insufficient_evidence",
                "detail": "no active phases to check", "evidence": {}}
    flagged = []
    for p in phases:
        dur = float(p["duration_s"])
        c = int(p.get("concurrency") or 0)
        n = int(p.get("requests") or 0)
        if c <= 0 or dur <= 0:
            continue
        # A healthy closed-loop worker completes at least a few requests per
        # second; far below that the phase degenerated (target stalled or
        # generator starved) and its percentiles describe a stall, not load.
        expected_floor = 3.0 * dur * c
        if n < expected_floor:
            flagged.append("%s: %d requests in %.0fs at C=%d (floor %.0f)"
                           % (p.get("name"), n, dur, c, expected_floor))
    if flagged:
        return {"name": "runt_phases", "status": "warn",
                "detail": "%d phase(s) completed far fewer requests than the "
                          "schedule implies: %s"
                % (len(flagged), "; ".join(flagged[:3])),
                "evidence": {"flagged": flagged}}
    return {"name": "runt_phases", "status": "ok",
            "detail": "all %d active phases completed a plausible request "
                      "volume for their duration and concurrency"
            % len(phases), "evidence": {"phases": len(phases)}}


def _generator_saturation(results):
    res = results.get("resources") or {}
    peak = res.get("gen_cpu_peak_pct")
    mean = res.get("gen_cpu_mean_pct")
    dsp = ((results.get("summary") or {}).get("dispatch_delay_ms") or {})
    dsp_p95 = dsp.get("p95")
    evidence = {"gen_cpu_peak_pct": peak, "gen_cpu_mean_pct": mean,
                "dispatch_delay_p95_ms": dsp_p95}
    if peak is None and dsp_p95 is None:
        return {"name": "generator_saturation",
                "status": "insufficient_evidence",
                "detail": "no generator CPU samples or dispatch lag recorded",
                "evidence": evidence}
    problems = []
    if peak is not None and peak >= 85.0:
        problems.append("generator CPU peaked at %.1f%% of one core" % peak)
    if dsp_p95 is not None and dsp_p95 >= 25.0:
        problems.append("request dispatch lag p95 was %.1f ms (generator "
                        "scheduling delay, not server time)" % dsp_p95)
    if problems:
        return {"name": "generator_saturation", "status": "warn",
                "detail": "load generator showed stress: " + "; ".join(problems)
                + ". Latency percentiles may be inflated by the client side.",
                "evidence": evidence}
    return {"name": "generator_saturation", "status": "ok",
            "detail": "generator stayed clear of saturation (peak %.1f%% core, "
                      "dispatch p95 %.1f ms)"
            % (peak if peak is not None else 0.0,
               dsp_p95 if dsp_p95 is not None else 0.0),
            "evidence": evidence}


def _latency_bounds(results):
    summary = results.get("summary") or {}
    timeout_s = 0.0
    cfg = results.get("config") or {}
    try:
        timeout_s = float(cfg.get("request_timeout_s") or 0)
    except (TypeError, ValueError):
        timeout_s = 0.0
    p99 = ((summary.get("PRIMARY_successful_200_only") or {}).get("p99"))
    all_max = ((summary.get("all_requests_latency") or {}).get("max"))
    if timeout_s <= 0 or (p99 is None and all_max is None):
        return {"name": "latency_bounds", "status": "insufficient_evidence",
                "detail": "no timeout reference or no latency data",
                "evidence": {}}
    timeout_ms = timeout_s * 1000.0
    over = []
    if all_max is not None and all_max > timeout_ms * 1.5:
        over.append("max latency %.1f ms exceeds 1.5x the request timeout "
                    "(%.0f ms)" % (all_max, timeout_ms))
    if p99 is not None and p99 > timeout_ms:
        over.append("successful-request p99 %.1f ms exceeds the request "
                    "timeout itself (%.0f ms)" % (p99, timeout_ms))
    if over:
        return {"name": "latency_bounds", "status": "warn",
                "detail": "; ".join(over) + " - check generator-side "
                          "scheduling lag and coordinated-omission fields",
                "evidence": {"timeout_ms": timeout_ms, "p99": p99,
                             "max": all_max}}
    return {"name": "latency_bounds", "status": "ok",
            "detail": "latencies are inside the request-timeout arithmetic",
            "evidence": {"timeout_ms": timeout_ms, "p99": p99,
                         "max": all_max}}
