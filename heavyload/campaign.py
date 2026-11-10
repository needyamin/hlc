"""Batch campaign runner for multi-language benchmark campaigns (Phase 2).

A campaign runs the SAME workload against N sites (one targets/*.json spec
each), each with warm-up passes (discarded) followed by repeat_count measured
passes. Per-site metrics are aggregated (median / mean / stddev) across the
measured repeats so one noisy run does not decide the ranking. The campaign
document records every member run id, the aggregation and an honest
comparability verdict.

Orchestration is sequential and reuses ``runner.run_benchmark`` as-is, so every
repeat is a real, inspectable run under runs/. A campaign never silently
averages unequal vantage points — comparability is checked with
``schema.can_compare``.
"""

from __future__ import annotations

import copy
import json
import os
import time

from . import config as C
from . import compare
from . import runner
from . import schema
from . import targetspec
from .util import atomic_json, stamp, utc


# Metrics that get a per-site aggregate across repeats.
_AGG_KEYS = [
    "overall_rps", "peak_phase_rps", "p95_ms", "p95_as_scheduled_ms",
    "error_pct", "cpu_peak_pct_net", "cpu_mean_pct_net",
    "mem_anon_mib_peak_net", "mem_net_change_mib", "mb_per_1k_req",
    "health_score", "health_success_pct", "pids_max", "duration_s",
    "total_requests",
]


def load_campaign(path):
    """Load and lightly validate a campaign spec. Returns (spec, None|error)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as ex:
        return None, "cannot read %s: %s" % (path, ex)
    if not isinstance(raw, dict):
        return None, "campaign spec must be a JSON object"
    spec = copy.deepcopy(raw)
    spec.setdefault("schema_version", 2)
    spec.setdefault("title", "Untitled campaign")
    spec["profile"] = str(spec.get("profile") or "quick")
    if spec["profile"] not in C.PRESETS:
        return None, "profile must be one of %s" % ", ".join(C.PRESETS)
    spec["warmup_runs"] = max(0, min(10, int(spec.get("warmup_runs", 1))))
    spec["repeat_count"] = max(1, min(20, int(spec.get("repeat_count", 3))))
    spec["cooldown_between_repeats_s"] = max(
        0, int(spec.get("cooldown_between_repeats_s", 15)))
    sites = spec.get("sites") or []
    if not isinstance(sites, list) or not sites:
        return None, "campaign.sites must be a non-empty list of site ids"
    spec["sites"] = [str(s) for s in sites]
    spec.setdefault("fairness", {})
    return spec, None


def _build_site_config(spec, site_id, root):
    """One shared-workload config for a single site, ready to run."""
    p = targetspec.find_spec(site_id, root=root)
    if not p:
        return None, "site spec not found: %s" % site_id
    sp, serr = targetspec.load_spec(p)
    if serr:
        return None, "invalid site spec %s: %s" % (site_id, serr)
    cfg = C.apply_profile(C.normalize({}), spec["profile"])
    cfg = targetspec.apply_to_config(cfg, sp)
    f = cfg.setdefault("fairness", {})
    f["repeats"] = spec["repeat_count"]
    f["warmup_s"] = sp.get("warmup", {}).get("duration_s", 0)
    f["cooldown_between_repeats_s"] = spec["cooldown_between_repeats_s"]
    # Campaign-level fairness overrides (CPU pinning, seed, aggregate).
    for k, v in (spec.get("fairness") or {}).items():
        if k in ("warmup_s", "idle_baseline_s", "repeats", "seed", "aggregate",
                 "cooldown_between_repeats_s", "identical_workload",
                 "cpuset_target", "cpuset_generator", "pin_host_generator"):
            f[k] = v
    return cfg, None


def _one_pass(cfg, root, log, on_event):
    info = runner.run_benchmark(cfg, root, log=log)
    if on_event:
        on_event({"type": "run_done", "run_id": info["run_id"]})
    return info["run_id"]


def aggregate_site(member_metrics):
    """Collapse per-repeat metric dicts into aggregated stats per key."""
    agg = {}
    for key in _AGG_KEYS:
        a = compare.aggregate_scalars(member_metrics, key)
        if a:
            agg[key] = a
    return agg


def _campaign_comparable(site_first_results):
    """Check can_compare across the first measured member of each site."""
    comparable = True
    reasons = []
    if len(site_first_results) >= 2:
        ref = site_first_results[0]
        for other in site_first_results[1:]:
            ok, why = schema.can_compare(ref, other)
            if not ok:
                comparable = False
                reasons.append({
                    "site_id": (other.get("target") or {}).get("site_id"),
                    "reason": why,
                })
    return comparable, reasons


def run_campaign(root, spec, log=None, on_event=None, should_stop=None):
    """Execute a campaign; write campaign.json + campaign_report.html.

    ``spec`` may be a path (loaded/validated) or an already-loaded dict.
    Returns the campaign result dict. ``on_event`` receives progress dicts.
    ``should_stop`` is an optional zero-arg callable; when it returns True the
    campaign aborts after the current pass / site (graceful stop, never mid-run
    corruption), finalising whatever sites already completed.
    """
    if isinstance(spec, str):
        spec, err = load_campaign(spec)
        if err:
            raise ValueError(err)

    campaign_id = spec.get("campaign_id") or ("campaign-" + stamp())
    out_dir = os.path.join(paths_benchmarks_dir(root), campaign_id)
    os.makedirs(out_dir, exist_ok=True)

    if on_event:
        on_event({"type": "campaign_start", "campaign_id": campaign_id,
                  "sites": list(spec["sites"])})

    def _stopped():
        return bool(should_stop and should_stop())

    stopped = False
    site_records = []
    first_member_results = []
    for site_id in spec["sites"]:
        if _stopped():
            stopped = True
            break
        if on_event:
            on_event({"type": "site_start", "site_id": site_id})
        cfg, cerr = _build_site_config(spec, site_id, root)
        if cerr:
            site_records.append({"site_id": site_id, "error": cerr})
            if on_event:
                on_event({"type": "site_error", "site_id": site_id,
                          "error": cerr})
            continue

        warmup_ids, measured_ids = [], []
        for i in range(spec["warmup_runs"]):
            if _stopped():
                stopped = True
                break
            if log:
                log.add("[%s] warm-up pass %d/%d"
                        % (site_id, i + 1, spec["warmup_runs"]))
            warmup_ids.append(_one_pass(cfg, root, log, on_event))
        if _stopped():
            stopped = True
        for i in range(spec["repeat_count"]):
            if _stopped():
                stopped = True
                break
            if log:
                log.add("[%s] measured pass %d/%d"
                        % (site_id, i + 1, spec["repeat_count"]))
            measured_ids.append(_one_pass(cfg, root, log, on_event))
            if i < spec["repeat_count"] - 1 and spec["cooldown_between_repeats_s"]:
                if log:
                    log.add("[%s] cooldown %ds before next repeat"
                            % (site_id, spec["cooldown_between_repeats_s"]))
                time.sleep(spec["cooldown_between_repeats_s"])

        if _stopped():
            stopped = True

        member_metrics, member_results = [], []
        for rid in measured_ids:
            res = compare.load_results(root, rid)
            if res is None:
                continue
            member_results.append(res)
            member_metrics.append(compare.extract_leaderboard_metrics(res))
        if member_results:
            first_member_results.append(member_results[0])

        agg = aggregate_site(member_metrics) if member_metrics else {}
        # Identity from the spec / first member.
        ident = (member_metrics[0] if member_metrics
                 else {"site_id": site_id})
        rec = {
            "site_id": ident.get("site_id", site_id),
            "language": ident.get("language", ""),
            "stack_label": ident.get("stack_label", ""),
            "runtime_version": ident.get("runtime_version", ""),
            "group": ident.get("group", ""),
            "image_digest": ident.get("image_digest", ""),
            "profile": spec["profile"],
            "warmup_runs": spec["warmup_runs"],
            "repeat_count": spec["repeat_count"],
            "members": measured_ids,
            "warmup_members": warmup_ids,
            "aggregated": agg,
            "repeats": member_metrics,
        }
        site_records.append(rec)
        if on_event:
            on_event({"type": "site_done", "site_id": site_id,
                      "members": measured_ids, "aggregated": agg})

    comparable, reasons = _campaign_comparable(first_member_results)

    # Build an aggregated leaderboard (median across repeats per site).
    leaderboard = []
    for rec in site_records:
        if "error" in rec:
            continue
        row = {
            "site_id": rec["site_id"],
            "language": rec["language"],
            "stack_label": rec["stack_label"],
            "runtime_version": rec["runtime_version"],
            "image_digest": rec["image_digest"],
            "profile": rec["profile"],
            "members": rec["members"],
            "repeats": rec["repeat_count"],
        }
        for key, a in rec["aggregated"].items():
            row[key] = a["median"]
        leaderboard.append(row)
    leaderboard.sort(key=lambda s: -(float(s.get("overall_rps") or 0)))

    campaign = {
        "schema_version": schema.SCHEMA_VERSION,
        "campaign_id": campaign_id,
        "title": spec.get("title", ""),
        "profile": spec["profile"],
        "generated_utc": utc(),
        "warmup_runs": spec["warmup_runs"],
        "repeat_count": spec["repeat_count"],
        "stopped": stopped,
        "comparable": comparable,
        "comparison_reasons": reasons,
        "columns": compare.LEADERBOARD_COLUMNS,
        "sites": site_records,
        "leaderboard": leaderboard,
    }
    atomic_json(os.path.join(out_dir, "campaign.json"), campaign)
    if on_event:
        on_event({"type": "campaign_done", "campaign_id": campaign_id})

    # Report + exports (best-effort; never block the result on them).
    try:
        from . import report_compare
        report_compare.build(campaign, os.path.join(out_dir, "report.html"))
    except Exception as ex:
        if log:
            log.add("Campaign report build failed: %s" % ex, "warn")
    try:
        compare.to_csv(_campaign_as_comparison(campaign),
                       os.path.join(out_dir, "leaderboard.csv"))
        compare.to_json(_campaign_as_comparison(campaign),
                        os.path.join(out_dir, "leaderboard.json"))
    except Exception as ex:
        if log:
            log.add("Campaign export failed: %s" % ex, "warn")

    campaign["campaign_dir"] = out_dir
    return campaign


def _campaign_as_comparison(campaign):
    """Project a campaign doc into the comparison shape for export/reuse."""
    sites = []
    for rec in campaign["sites"]:
        if "error" in rec:
            continue
        row = {k: rec.get(k, "") for k, _, _ in compare.LEADERBOARD_COLUMNS}
        for key, a in rec["aggregated"].items():
            row[key] = a["median"]
        sites.append(row)
    return {
        "schema_version": campaign["schema_version"],
        "generated_utc": campaign["generated_utc"],
        "run_ids": [],
        "missing": [],
        "comparable": campaign["comparable"],
        "comparison_reasons": campaign["comparison_reasons"],
        "sort_by": "overall_rps",
        "columns": compare.LEADERBOARD_COLUMNS,
        "sites": sites,
        "leaderboard": campaign["leaderboard"],
    }


def paths_benchmarks_dir(root=None):
    from . import paths
    return paths.BENCHMARKS_DIR if root is None else os.path.join(
        root, "benchmarks")


def list_campaigns(root=None):
    base = paths_benchmarks_dir(root)
    out = []
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base), reverse=True):
        d = os.path.join(base, name)
        j = os.path.join(d, "campaign.json")
        if os.path.isdir(d) and os.path.isfile(j):
            try:
                with open(j, "r", encoding="utf-8") as f:
                    c = json.load(f)
            except Exception:
                continue
            out.append({
                "campaign_id": name,
                "title": c.get("title", ""),
                "profile": c.get("profile", ""),
                "generated_utc": c.get("generated_utc", ""),
                "sites": len([s for s in c.get("sites", []) if "error" not in s]),
                "comparable": c.get("comparable"),
                "has_report": os.path.isfile(os.path.join(d, "report.html")),
            })
    return out
