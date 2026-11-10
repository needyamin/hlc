"""Comparison / leaderboard layer for Heavy Load Checker (Phase 2).

This is the honest cross-language comparison core. It loads existing v2 run
results, extracts a *normalised* per-site metric view, enforces the
``schema.can_compare`` hard rule (never compare runs measured from different
vantage points) and produces a comparison object that the report builder, the
JSON/CSV exporters and the web UI all consume.

No network, no Docker, no mutable globals — every function is a pure transform
of its inputs so it is trivially unit-testable.
"""

from __future__ import annotations

import csv
import json
import os
import statistics

from . import paths
from . import schema
from .schema import can_compare
from .util import utc


# ------------------------------------------------------------------ loading --
def find_results_path(root, run_id):
    """Locate ``run_id/results.json`` in runs/ or _archive/runs/."""
    for base in (paths.runs_dir(root), paths.archive_runs_dir(root)):
        p = os.path.join(base, run_id, "results.json")
        if os.path.isfile(p):
            return p
    return None


def load_results(root, run_id):
    """Load a run's results.json and upgrade it to the current schema."""
    p = find_results_path(root, run_id)
    if not p:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    schema.ensure_current(data)
    return data


# ------------------------------------------------------------- extraction --
def extract_leaderboard_metrics(results):
    """Reduce one v2 result to a flat, comparable per-site metric dict."""
    summary = results.get("summary") or {}
    resources = results.get("resources") or {}
    adj = results.get("adjudication") or {}
    target = results.get("target") or {}
    meta = results.get("meta") or {}
    fp = (results.get("environment") or {}).get("fingerprint") or {}

    total_requests = summary.get("total_requests") or 0
    anon_net = resources.get("anon_net_change_mib")
    mb_per_1k = (anon_net / (total_requests / 1000.0)) if (
        total_requests and anon_net is not None) else None

    phases = results.get("phases") or []
    active = [p for p in phases if (p.get("requests") or 0) > 0]
    peak_phase_rps = max([float(p.get("rps") or 0) for p in active] or [0.0])

    as_sched = (summary.get("as_scheduled_latency") or {}).get("p95")
    all_p95 = (summary.get("all_requests_latency") or {}).get("p95")
    p95 = as_sched if as_sched is not None else all_p95

    return {
        "run_id": meta.get("run_id") or results.get("meta", {}).get("run_id"),
        "site_id": target.get("site_id") or meta.get("target_url", ""),
        "group": target.get("group", ""),
        "language": target.get("language", ""),
        "stack_label": target.get("stack_label")
        or "%s %s" % (target.get("language", ""),
                      target.get("framework", "") or ""),
        "runtime_version": target.get("runtime_version")
        or fp.get("runtime_version", ""),
        "image_digest": target.get("image_digest")
        or fp.get("image_digest", ""),
        "benchmark_mode": results.get("benchmark_mode", ""),
        "visibility": results.get("visibility", ""),
        "profile": meta.get("profile", ""),
        "started_utc": meta.get("started_utc", ""),
        "duration_s": summary.get("duration_s"),
        "total_requests": total_requests,
        "overall_rps": summary.get("overall_rps"),
        "peak_phase_rps": round(peak_phase_rps, 2) if peak_phase_rps else None,
        "p95_ms": p95,
        "p95_as_scheduled_ms": as_sched,
        "error_pct": summary.get("s1_error_pct"),
        "cpu_peak_pct_net": resources.get("cpu_peak_pct_net"),
        "cpu_mean_pct_net": resources.get("cpu_mean_pct_net"),
        "mem_anon_mib_peak_net": resources.get("anon_peak_mib_net"),
        "mem_net_change_mib": anon_net,
        "mb_per_1k_req": round(mb_per_1k, 3) if mb_per_1k is not None else None,
        "pids_max": resources.get("pids_max"),
        "health_success_pct": resources.get("health_success_pct"),
        "health_score": adj.get("health_score"),
        "verdict": adj.get("verdict", ""),
        "verdict_code": results.get("verdict_code")
        or schema.verdict_code(adj.get("verdict")),
    }


# Display + export column contract. ``kind`` drives formatting.
LEADERBOARD_COLUMNS = [
    ("site_id", "Site", "str"),
    ("language", "Lang", "str"),
    ("stack_label", "Stack", "str"),
    ("runtime_version", "Runtime", "str"),
    ("overall_rps", "RPS (overall)", "num1"),
    ("peak_phase_rps", "RPS (peak phase)", "num1"),
    ("p95_ms", "p95 ms (as-sched)", "num1"),
    ("error_pct", "Error %", "pct"),
    ("cpu_peak_pct_net", "CPU peak % (net)", "num2"),
    ("cpu_mean_pct_net", "CPU mean % (net)", "num2"),
    ("mem_anon_mib_peak_net", "Mem peak MiB (net)", "num1"),
    ("mem_net_change_mib", "Mem Δ MiB", "num1"),
    ("mb_per_1k_req", "MB / 1k req", "num3"),
    ("health_score", "Health /100", "num0"),
    ("verdict_code", "Verdict", "str"),
    ("image_digest", "Image digest", "str"),
]


def _fmt(value, kind):
    if value is None or value == "":
        return "n/a"
    try:
        if kind == "num0":
            return "%.0f" % float(value)
        if kind == "num1":
            return "%.1f" % float(value)
        if kind == "num2":
            return "%.2f" % float(value)
        if kind == "num3":
            return "%.3f" % float(value)
        if kind == "pct":
            return "%.3f" % float(value)
        if kind == "int":
            return "{:,}".format(int(value))
    except (TypeError, ValueError):
        return "n/a"
    return str(value)


# ----------------------------------------------------------- aggregation --
def aggregate_scalars(maps, key):
    """Across a list of dicts, summarise the numeric series for ``key``.

    Returns ``{median, mean, stddev, min, max, n}``; ``None`` when there is no
    data. Missing / non-numeric entries are skipped so a single failed repeat
    does not poison the campaign aggregate.
    """
    vals = []
    for m in maps:
        v = m.get(key)
        if isinstance(v, (int, float)) and v is not None:
            vals.append(float(v))
    if not vals:
        return None
    out = {
        "median": round(statistics.median(vals), 4),
        "mean": round(statistics.mean(vals), 4),
        "min": round(min(vals), 4),
        "max": round(max(vals), 4),
        "n": len(vals),
    }
    out["stddev"] = round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0
    return out


# ------------------------------------------------------------- comparison --
def compare_runs(root, run_ids, sort_by="overall_rps"):
    """Build a comparison object from existing runs.

    Enforces ``can_compare`` across every pair; if any pair is incomparable the
    result is flagged ``comparable=False`` with a reason. The raw per-site rows
    are still returned so the UI can show the warning instead of a misleading
    ranking.
    """
    loaded = []
    missing = []
    for rid in run_ids:
        res = load_results(root, rid)
        if res is None:
            missing.append(rid)
        else:
            loaded.append(res)

    sites = [extract_leaderboard_metrics(r) for r in loaded]

    comparable = True
    reasons = []
    if len(loaded) >= 2:
        ref = loaded[0]
        for other in loaded[1:]:
            ok, why = can_compare(ref, other)
            if not ok:
                comparable = False
                reasons.append({
                    "run_id": (other.get("meta") or {}).get("run_id")
                    or other.get("run_id"),
                    "reason": why,
                })

    def _sort_key(s):
        v = s.get(sort_by)
        return -(float(v) if isinstance(v, (int, float)) else -1e18)

    leaderboard = sorted(sites, key=_sort_key)
    return {
        "schema_version": schema.SCHEMA_VERSION,
        "generated_utc": utc(),
        "run_ids": list(run_ids),
        "missing": missing,
        "comparable": comparable,
        "comparison_reasons": reasons,
        "sort_by": sort_by,
        "columns": LEADERBOARD_COLUMNS,
        "sites": sites,
        "leaderboard": leaderboard,
    }


# --------------------------------------------------------------- export ---
def to_json(comparison, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(comparison, f, indent=2, sort_keys=True, default=str)
    return path


def to_csv(comparison, path):
    cols = comparison.get("columns") or LEADERBOARD_COLUMNS
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow([label for _, label, _ in cols])
        for site in comparison["leaderboard"]:
            w.writerow([_fmt(site.get(k), kind) for k, _, kind in cols])
    return path
