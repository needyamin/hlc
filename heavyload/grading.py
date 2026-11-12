#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SLO adjudication, severity-ranked findings, health score and verdict.

Rubric (adapted from the frozen Spid3r Rev 10-17 QA rubric, generalized):
  * overall grade = min(all SLO grades), any FAIL => NO-GO
  * an early guardrail ABORT is NO-GO-equivalent and right-censors the series
  * SLOs requiring the full schedule are graded N/A - insufficient evidence
  * health = 100 - 25/critical - 10/high - 4/medium - 1/low, floored at 0,
    capped at 40 on any NO-GO
"""
from __future__ import annotations

from .util import clamp, safe_div

SEV_WEIGHT = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1}
SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _grade_fail_warn(value, target, warn_fraction=0.8):
    if value is None:
        return "N/A"
    if value > target:
        return "FAIL"
    if value > target * warn_fraction:
        return "WARN"
    return "PASS"


def _finding(sev, title, evidence, impact, recommendation):
    return {"severity": sev, "title": title, "evidence": evidence,
            "impact": impact, "recommendation": recommendation}


def grade(results):
    cfg = results["config"]
    # Config is the primary signal: grade() runs before ensure_current() has
    # populated results["visibility"] / results["benchmark_mode"].
    client_only = (str(cfg.get("runtime_environment") or "").strip().lower() == "windows"
                   or cfg.get("benchmark_mode") == "live_blackbox"
                   or results.get("visibility") == "client_only")
    slo_cfg = results["slo_targets"]
    s = results["summary"]
    r = results["resources"]
    abort = results["abort"]
    aborted = bool(abort.get("flag"))
    lat = s["PRIMARY_successful_200_only"]
    err_pct = s["s1_error_pct"]
    active_bins = len(r.get("retained_floor_bins") or [])
    active_points = r.get("active_points") or 0
    enough = (not aborted and active_bins >= 10 and active_points >= 50)
    censored = "N/A - right-censored (schedule aborted early)"
    rows = []

    def add(id_, name, target, measured, g, note=""):
        rows.append({"id": id_, "name": name, "target": target,
                     "measured": measured, "grade": g, "note": note})

    # S1 - error rate -------------------------------------------------------
    add("S1", "Request error rate", "<= %.3f%%" % slo_cfg["max_error_pct"],
        "%.4f%% (%d of %d; %d connect errors; 3xx/4xx excluded)"
        % (err_pct, s["s1_errors"], s["total_requests"], s["connect_errors"]),
        "FAIL" if err_pct > slo_cfg["max_error_pct"] else "PASS",
        "Errors = HTTP 5xx + transport failures. Intentional 3xx/4xx are "
        "classified separately and excluded.")

    # S2 / S3 - tail latency ------------------------------------------------
    g2 = _grade_fail_warn(lat["p95"], slo_cfg["max_p95_ms"])
    add("S2", "Successful-200 p95 latency",
        "<= %.0f ms" % slo_cfg["max_p95_ms"],
        "%.2f ms (n=%d, p50=%.2f ms)" % (lat["p95"], lat["n"], lat["p50"]),
        g2, "Measured on HTTP 200 responses only.")
    g3 = _grade_fail_warn(lat["p99"], slo_cfg["max_p99_ms"])
    add("S3", "Successful-200 p99 latency",
        "<= %.0f ms" % slo_cfg["max_p99_ms"],
        "%.2f ms (max=%.2f ms)" % (lat["p99"], lat["max"]), g3,
        "Tail saturation is the primary capacity signal under heavy load.")

    # S4 - throughput -------------------------------------------------------
    min_rps = float(slo_cfg.get("min_rps") or 0)
    if min_rps <= 0:
        add("S4", "Sustained throughput", "informational (min_rps not set)",
            "%.2f RPS overall, %d requests in %.1f s"
            % (s["overall_rps"], s["total_requests"], s["duration_s"]),
            "N/A", "Set an min_rps SLO in the interface to grade throughput.")
    elif aborted:
        add("S4", "Sustained throughput", ">= %.1f RPS" % min_rps,
            "%.2f RPS observed before abort" % s["overall_rps"], "N/A", censored)
    else:
        add("S4", "Sustained throughput", ">= %.1f RPS" % min_rps,
            "%.2f RPS overall" % s["overall_rps"],
            "PASS" if s["overall_rps"] >= min_rps else "FAIL")

    # S5 - memory growth ----------------------------------------------------
    slope = r.get("active_slope_mib_per_s")
    net = r.get("anon_net_change_mib")
    growth = None
    if not client_only and slope is not None and r.get("active_window_s") and \
            None not in r["active_window_s"]:
        span = (r["active_window_s"][1] or 0) - (r["active_window_s"][0] or 0)
        growth = slope * span
    if client_only:
        add("S5", "Bounded anonymous-memory growth",
            "<= %.0f MiB net active growth" % slo_cfg["max_memory_growth_mib"],
            "client-only run: no server memory visibility", "N/A",
            "Windows runtime mode collects no cgroup memory; switch the "
            "Runtime environment to Docker to grade memory SLOs.")
    elif aborted:
        add("S5", "Bounded anonymous-memory growth",
            "<= %.0f MiB net active growth" % slo_cfg["max_memory_growth_mib"],
            "peak anon %.1f MiB at abort; net %s MiB"
            % (r.get("anon_peak_mib") or 0, net), "N/A", censored)
    elif not enough:
        add("S5", "Bounded anonymous-memory growth",
            "<= %.0f MiB net active growth" % slo_cfg["max_memory_growth_mib"],
            "active bins=%d, active points=%d" % (active_bins, active_points),
            "N/A", "Insufficient evidence (>=10 active 10 s bins and >=50 "
                   "cgroup points required).")
    elif growth is None:
        add("S5", "Bounded anonymous-memory growth",
            "<= %.0f MiB net active growth" % slo_cfg["max_memory_growth_mib"],
            "no cgroup anon series", "N/A",
            "No container/cgroup instrumentation: memory not graded.")
    else:
        add("S5", "Bounded anonymous-memory growth",
            "<= %.0f MiB net active growth" % slo_cfg["max_memory_growth_mib"],
            "%.1f MiB fitted active growth (slope %.4f MiB/s, r2=%s, "
            "Spearman=%s, gross monotonic=%.2f)"
            % (growth, slope, r.get("active_slope_r2"), r.get("active_spearman"),
               r.get("gross_monotonic_fraction") or 0),
            "FAIL" if growth > slo_cfg["max_memory_growth_mib"] else "PASS",
            "Fitted over active phases from cgroup raw-byte anon. Sawtooth "
            "gross series is diagnostic only.")

    # S6 - cooldown retention ----------------------------------------------
    ret = None if client_only else r.get("cooldown_retained_pct")
    if client_only:
        add("S6", "Memory released after zero-load cooldown",
            "<= %.0f%% retained" % slo_cfg["max_retained_pct"],
            "client-only run: no server memory visibility", "N/A",
            "Windows runtime mode collects no cgroup memory; switch the "
            "Runtime environment to Docker to grade memory SLOs.")
    elif aborted:
        add("S6", "Memory released after zero-load cooldown",
            "<= %.0f%% retained" % slo_cfg["max_retained_pct"],
            "cooldown start %s MiB / end %s MiB"
            % (r.get("cooldown_start_mib"), r.get("cooldown_end_mib")),
            "N/A", censored + "; cooldown is additive observation only.")
    elif ret is None:
        add("S6", "Memory released after zero-load cooldown",
            "<= %.0f%% retained" % slo_cfg["max_retained_pct"],
            "no cooldown anon series", "N/A",
            "No cooldown phase or no cgroup instrumentation.")
    else:
        add("S6", "Memory released after zero-load cooldown",
            "<= %.0f%% retained" % slo_cfg["max_retained_pct"],
            "%.1f%% retained (%s -> %s MiB, %d large unmap events)"
            % (ret, r.get("cooldown_start_mib"), r.get("cooldown_end_mib"),
               r.get("large_unmap_count") or 0),
            "FAIL" if ret > slo_cfg["max_retained_pct"] else "PASS",
            "A retained high-water mark with voluntary munmap() is an "
            "allocator/GC behavior, NOT proof of an unbounded kernel leak.")

    # S7 - availability -----------------------------------------------------
    hs = r.get("health_success_pct")
    probed = r.get("health_probes") or 0
    if probed < 5:
        add("S7", "Availability probe success",
            ">= %.1f%%" % slo_cfg["min_health_success_pct"],
            "%d probes" % probed, "N/A", "Too few probes to grade.")
    else:
        add("S7", "Availability probe success",
            ">= %.1f%%" % slo_cfg["min_health_success_pct"],
            "%.2f%% of %d probes (p95 %.2f ms)"
            % (hs, probed, (r.get("health_probe_latency") or {}).get("p95", 0)),
            "PASS" if hs >= slo_cfg["min_health_success_pct"] else "FAIL")

    # S8 - process / container stability ------------------------------------
    before = results.get("inspect_before") or {}
    after = results.get("inspect_after") or {}
    def _as_int(v):
        try:
            return int(v)
        except Exception:
            return 0
    restarts = _as_int(after.get("restart_count")) - _as_int(before.get("restart_count"))
    oom = r.get("oom_kill_events") or 0
    unstable = bool(oom or restarts > 0 or str(after.get("oom_killed")) == "true")
    add("S8", "No OOM kill / restart",
        "0 OOM kills, 0 restarts",
        "oom_kill=%d, restart delta=%d, state=%s, PSI max=%s"
        % (oom, restarts, after.get("state", "?"), r.get("psi_max_avg10")),
        "FAIL" if unstable else "PASS")

    # S9 - schedule completion ----------------------------------------------
    add("S9", "Full schedule completed", "no guardrail abort",
        ("ABORT at t=%s s in %s: %s" % (abort.get("at_s"),
                                        abort.get("phase"), abort.get("reason")))
        if aborted else "completed %d/%d phases"
        % (len(results.get("executed_phases") or []), len(cfg.get("phases") or [])),
        "FAIL" if aborted else "PASS",
        "Guardrails are SAFETY limits, not SLOs; an abort is NO-GO-equivalent.")

    graded = [x["grade"] for x in rows if not x["grade"].startswith("N/A")]
    if "FAIL" in graded:
        overall = "FAIL"
    elif "WARN" in graded:
        overall = "WARN"
    else:
        overall = "PASS"

    # ----------------------------------------------------------- findings --
    f = []
    if unstable:
        f.append(_finding("CRITICAL", "Container OOM-killed or restarted",
                          "oom_kill=%d, restart delta=%d" % (oom, restarts),
                          "Requests in flight were lost; memory evidence is "
                          "invalidated by the restart.",
                          "Inspect docker logs and set an appropriate memory "
                          "limit before re-running."))
    if aborted:
        f.append(_finding("HIGH", "Guardrail abort at t=%s s — run is right-censored"
                          % abort.get("at_s"),
                          abort.get("reason") or "unknown",
                          "Sustained, spike, soak and cooldown phases never ran; "
                          "all schedule-dependent conclusions are N/A.",
                          "Re-run with a lower peak concurrency or a higher "
                          "guardrail ceiling after fixing the underlying cause."))
    if err_pct > slo_cfg["max_error_pct"]:
        sev = "HIGH" if err_pct > max(1.0, slo_cfg["max_error_pct"] * 10) else "MEDIUM"
        f.append(_finding(sev, "Error rate %.4f%% exceeds the %.3f%% SLO"
                          % (err_pct, slo_cfg["max_error_pct"]),
                          "%d HTTP 5xx / transport failures in %d requests; "
                          "%d connect errors" % (s["s1_errors"],
                                                 s["total_requests"],
                                                 s["connect_errors"]),
                          "Users see failed requests under heavy load.",
                          "Correlate the failing timestamps with application "
                          "logs and DB saturation."))
    if lat["p99"] > slo_cfg["max_p99_ms"]:
        mult = safe_div(lat["p99"], slo_cfg["max_p99_ms"], 1)
        f.append(_finding("HIGH" if mult > 2 else "MEDIUM",
                          "p99 latency %.0f ms is %.1fx the %.0f ms target"
                          % (lat["p99"], mult, slo_cfg["max_p99_ms"]),
                          "p50=%.2f ms, p95=%.2f ms, max=%.2f ms"
                          % (lat["p50"], lat["p95"], lat["max"]),
                          "Severe tail latency indicates queueing or resource "
                          "contention at peak concurrency.",
                          "Profile the slowest routes and check DB connection "
                          "pooling, CPU throttling and listen-queue overflows."))
    if growth is not None and not aborted and enough and \
            growth > slo_cfg["max_memory_growth_mib"]:
        f.append(_finding("HIGH",
                          "Anonymous memory grew %.0f MiB across active phases"
                          % growth,
                          "slope %.4f MiB/s (r2=%s), gross monotonic %.2f, "
                          "peak %.1f MiB, %d large unmap events"
                          % (slope, r.get("active_slope_r2"),
                             r.get("gross_monotonic_fraction") or 0,
                             r.get("anon_peak_mib") or 0,
                             r.get("large_unmap_count") or 0),
                          "Sustained growth can exhaust the container/host "
                          "memory ceiling under a longer soak.",
                          "Distinguish unbounded leak from retained high-water "
                          "allocator behavior using the cooldown series and "
                          "pgscan/pgsteal/PSI counters before claiming a leak."))
    if not client_only and (r.get("large_unmap_count") or 0) > 0 and \
            (r.get("pgscan_max") or 0) == 0 \
            and (r.get("psi_max_avg10") or 0) == 0:
        f.append(_finding("MEDIUM",
                          "Retained high-water with voluntary process-level unmap",
                          "%d unmap events >100 MiB, pgscan=%d, pgsteal=%d, "
                          "PSI max=%s, file cache flat"
                          % (r["large_unmap_count"], r.get("pgscan_max") or 0,
                             r.get("pgsteal_max") or 0, r.get("psi_max_avg10")),
                          "Memory sawtooth comes from allocator/GC returning "
                          "pages, not kernel reclaim; an unbounded leak must "
                          "NOT be concluded from this evidence.",
                          "Tune allocator/GC high-water settings or cap per-worker "
                          "memory if the retained floor is operationally costly."))
    cpu = r.get("cpu_peak_pct")
    cores = results["meta"].get("cpu_count") or 1
    # cgroup CPU% is normalized to ONE core: 100% == one full core.
    # Only call saturation when most host cores are consumed.
    if cpu is not None and cpu >= 0.85 * cores * 100:
        f.append(_finding("MEDIUM",
                          "CPU saturated at %.1f%% of a %d-core host (%.1f cores)"
                          % (cpu, cores, cpu / 100.0),
                          "peak %.1f%%, mean %.1f%% (from cpu.stat usage_usec "
                          "deltas)" % (cpu, r.get("cpu_mean_pct") or 0),
                          "CPU-bound saturation amplifies tail latency.",
                          "Check cpu.max / nr_throttled and scale horizontally "
                          "if needed."))
    if probed >= 5 and hs is not None and hs < slo_cfg["min_health_success_pct"]:
        f.append(_finding("HIGH", "Availability probes failed %.2f%% of the time" % (100 - hs),
                          "%d probes, %.2f%% success" % (probed, hs),
                          "The service is not reliably reachable under load.",
                          "Check listen queue overflows, connection limits and "
                          "container health."))
    if min_rps > 0 and not aborted and s["overall_rps"] < min_rps:
        f.append(_finding("MEDIUM", "Throughput %.2f RPS below the %.1f RPS target"
                          % (s["overall_rps"], min_rps),
                          "%d requests in %.1f s" % (s["total_requests"], s["duration_s"]),
                          "Capacity does not meet the declared SLO.",
                          "Review route mix, keep-alive and upstream latency."))
    statuses = s.get("status_counts") or {}
    redirects = sum(v for k, v in statuses.items() if k.startswith("3"))
    notfound = statuses.get("404", 0)
    if redirects + notfound > 0:
        f.append(_finding("LOW", "Non-200 responses are part of the configured route mix",
                          "%d 3xx, %d 404 of %d requests"
                          % (redirects, notfound, s["total_requests"]),
                          "Excluded from the error SLO but they still consume "
                          "connections and application work.",
                          "Verify the intended route mix and cookie/session "
                          "behavior before interpreting QPS figures."))
    if not r.get("cgroup_samples"):
        cgroup_rec = ("Switch the Runtime environment to Docker for "
                      "server-side memory visibility." if client_only else
                      "Select the correct container in the interface, or "
                      "confirm the site really runs outside Docker.")
        f.append(_finding("LOW", "No cgroup instrumentation for this run",
                          "container=%r, mode=%s"
                          % (results["meta"].get("container"), cfg.get("mode")),
                          "Memory SLOs (S5/S6) cannot be graded; only HTTP-level "
                          "evidence is available.",
                          cgroup_rec))
    if aborted:
        f.append(_finding("LOW", "Attribution and repeat-pass decisions are withheld",
                          "evidence floors: active bins=%d, active points=%d, "
                          "schedule aborted=%s" % (active_bins, active_points, aborted),
                          "Root-cause attribution requires a non-aborted schedule.",
                          "Re-run the full profile after remediation."))

    f.sort(key=lambda x: SEV_ORDER.get(x["severity"], 9))
    deductions = sum(SEV_WEIGHT.get(x["severity"], 0) for x in f)
    health = clamp(100 - deductions, 0, 100)
    no_go = aborted or overall == "FAIL"
    if no_go:
        health = min(health, 40)
    health = int(clamp(health, 0, 100))

    if aborted:
        verdict = "NO-GO - ABORT (right-censored, partial data)"
    elif overall == "FAIL":
        verdict = "NO-GO"
    elif overall == "WARN":
        verdict = "GO WITH CONDITIONS"
    else:
        verdict = "GO"

    # ------------------------------------------------------- executive summary
    ex = [
        "Target %s checked with the %s profile (%d phases, %d s schedule, "
        "peak concurrency %d)."
        % (results["meta"]["target_url"], results["meta"]["profile"],
           len(cfg["phases"]), results["meta"]["schedule_s"],
           results["meta"]["peak_concurrency"]),
        "%d requests issued at %.2f RPS overall; %.4f%% error rate (%d HTTP 5xx "
        "/ transport failures, %d connect errors)."
        % (s["total_requests"], s["overall_rps"], err_pct, s["s1_errors"],
           s["connect_errors"]),
        "Successful-200 latency p50 %.2f ms / p95 %.2f ms / p99 %.2f ms / "
        "max %.2f ms." % (lat["p50"], lat["p95"], lat["p99"], lat["max"]),
    ]
    if r.get("anon_peak_mib") is not None:
        ex.append("Canonical cgroup %s: start %.1f MiB, peak %.1f MiB, end %.1f "
                  "MiB (net %+.1f MiB) over %d samples."
                  % (r.get("canonical_metric"), r.get("anon_start_mib") or 0,
                     r.get("anon_peak_mib"), r.get("anon_end_mib") or 0,
                     r.get("anon_net_change_mib") or 0,
                     r.get("cgroup_samples") or 0))
    else:
        ex.append("No cgroup memory series was captured; memory SLOs are N/A.")
    ex.append("Overall SLO grade %s; %d findings (%s); health score %d/100."
              % (overall, len(f),
                 ", ".join("%d %s" % (sum(1 for x in f if x["severity"] == sev), sev)
                           for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
                           if any(x["severity"] == sev for x in f)) or "none",
                 health))
    if aborted:
        ex.append("RIGHT-CENSORED: the guardrail aborted the schedule at t=%s s "
                  "during %s. Evidence is valid for peak memory at abort, "
                  "CPU/PSI up to abort, error/latency observations and the "
                  "trigger itself, but NOT for sustained growth, cooldown "
                  "retention, attribution or repeat-pass decisions."
                  % (abort.get("at_s"), abort.get("phase")))
    else:
        ex.append("Full schedule completed; sustained growth, cooldown retention "
                  "and availability evidence are usable.")
    ex.append("Verdict: %s." % verdict)

    # ------------------------------------------------------- go conditions --
    cond = []
    if aborted:
        cond.append("Re-run the full non-aborted profile after remediation; an "
                    "ABORT is treated as NO-GO-equivalent.")
    if err_pct > slo_cfg["max_error_pct"]:
        cond.append("Drive HTTP 5xx / transport failures to <= %.3f%%."
                    % slo_cfg["max_error_pct"])
    if lat["p95"] > slo_cfg["max_p95_ms"] or lat["p99"] > slo_cfg["max_p99_ms"]:
        cond.append("Bring p95 <= %.0f ms and p99 <= %.0f ms at peak concurrency."
                    % (slo_cfg["max_p95_ms"], slo_cfg["max_p99_ms"]))
    if growth is not None and not aborted and enough:
        cond.append("Bound fitted active anon growth to <= %.0f MiB."
                    % slo_cfg["max_memory_growth_mib"])
    if ret is not None and not aborted:
        cond.append("Keep post-cooldown retained anon <= %.0f%% of the "
                    "pre-cooldown level." % slo_cfg["max_retained_pct"])
    if probed >= 5 and hs is not None:
        cond.append("Maintain health-probe success >= %.1f%%."
                    % slo_cfg["min_health_success_pct"])
    cond.append("Confirm zero OOM kills and zero container restarts across the "
                "entire run.")
    if min_rps > 0:
        cond.append("Sustain >= %.1f RPS without a guardrail abort." % min_rps)
    cond.append("Re-run twice and require consistent verdicts before treating "
                "the result as a capacity certificate.")

    return {
        "verdict": verdict,
        "verdict_label": verdict,
        "overall_grade": overall,
        "health_score": health,
        "health_score_formula": "100 - 25/CRITICAL - 10/HIGH - 4/MEDIUM - "
                                "1/LOW, floored at 0, capped at 40 on NO-GO",
        "executive_summary": ex,
        "slo": rows,
        "findings": f,
        "go_conditions": cond,
        "right_censored": aborted,
        "evidence": {"active_10s_bins": active_bins,
                     "active_cgroup_points": active_points,
                     "sufficient_for_growth_slo": enough,
                     "health_probes": probed,
                     "cgroup_samples": r.get("cgroup_samples") or 0},
        "adjudication_note": "Automated adjudication from measured series only. "
                             "No claim of an unbounded memory leak is made "
                             "without a non-aborted schedule, retained-floor "
                             "evidence and exclusion of bounded alternatives.",
    }
