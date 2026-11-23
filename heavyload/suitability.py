#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Technology suitability assessment: measured evidence + disclosed judgments.

The ranking combines TWO kinds of criteria, and every report shows which is
which - mixing them silently would be misleading:

  measured    computed from this run's own data (runtime comparison rows,
              campaign results when available). Values trace to artifacts.
  documented  curated qualitative judgments (ecosystem maturity, library
              depth, maintainability, reliability track record, concurrency
              model) with a one-line justification each. They are OPINIONS
              from widely known ecosystem characteristics, never measurements
              of this machine, and are labelled as such everywhere.

Rules:
  * A criterion with no evidence gets score None and its weight is
    redistributed proportionally over the criteria that DID produce a score;
    the report shows the resulting measured/documented contribution split.
  * Normalization is log-linear between the best and worst measured value of
    a criterion (rank-preserving, bounded 0..10) and is documented in-code.
  * Weights come from config.suitability.weights, normalized to sum 1.
  * The module never raises into the caller; problems become warnings.
"""

from __future__ import annotations

import html
import json
import math
import os
import pathlib

from .util import atomic_json, utc

# ---------------------------------------------------------------------------
# Criteria registry
# ---------------------------------------------------------------------------
# provenance: "measured" (this run) or "documented" (curated judgment).
CRITERIA = {
    "compute_performance": {
        "label": "Compute performance",
        "provenance": "measured",
        "lower_is_better": True,
        "desc": "Geomean compute time across checksum-verified workloads "
                "(runtime comparison).",
    },
    "memory_efficiency": {
        "label": "Memory efficiency",
        "provenance": "measured",
        "lower_is_better": True,
        "desc": "Geomean OS-measured peak working set across workloads.",
    },
    "cpu_efficiency": {
        "label": "CPU efficiency",
        "provenance": "measured",
        "lower_is_better": True,
        "desc": "Geomean OS-measured CPU time (user+kernel) per workload.",
    },
    "http_throughput": {
        "label": "HTTP throughput",
        "provenance": "measured",
        "lower_is_better": False,
        "desc": "From multi-language campaigns only (identical workload, "
                "identical host). A single-run HTTP result describes ONE "
                "target, not a language, so it is excluded from per-language "
                "scores here.",
    },
    "http_latency": {
        "label": "HTTP latency",
        "provenance": "measured",
        "lower_is_better": True,
        "desc": "From multi-language campaigns only (identical workload, "
                "identical host).",
    },
    "http_stability": {
        "label": "HTTP stability",
        "provenance": "measured",
        "lower_is_better": True,
        "desc": "From multi-language campaigns only: error rate and "
                "run-to-run dispersion.",
    },
    "ecosystem_maturity": {
        "label": "Ecosystem maturity",
        "provenance": "documented",
        "lower_is_better": False,
        "desc": "Age, release stability, backwards-compatibility culture, "
                "size of the production install base (documented judgment).",
    },
    "ecosystem_libraries": {
        "label": "Library depth",
        "provenance": "documented",
        "lower_is_better": False,
        "desc": "Breadth and depth of production-grade libraries and tooling "
                "(documented judgment).",
    },
    "maintainability": {
        "label": "Maintainability",
        "provenance": "documented",
        "lower_is_better": False,
        "desc": "Static discipline, tooling, refactorability at team scale "
                "(documented judgment).",
    },
    "reliability": {
        "label": "Reliability track record",
        "provenance": "documented",
        "lower_is_better": False,
        "desc": "Observed production failure modes and memory/CPU safety "
                "(documented judgment).",
    },
    "concurrency_model": {
        "label": "Concurrency model",
        "provenance": "documented",
        "lower_is_better": False,
        "desc": "Fitness of the standard concurrency primitives for server "
                "workloads (documented judgment).",
    },
}

MEASURED_HTTP = ("http_throughput", "http_latency", "http_stability")

# ---------------------------------------------------------------------------
# Documented knowledge base (0..10 per criterion + one-line justification).
# Deliberately conservative, deliberately disclosed. These are judgments
# about widely known ecosystem characteristics - NOT measurements.
# ---------------------------------------------------------------------------
KB = {
    "python": {
        "ecosystem_maturity": (9, "30+ years old, stable release process, huge install base"),
        "ecosystem_libraries": (10, "PyPI covers virtually every domain; batteries-included stdlib"),
        "maintainability": (7, "Readable but dynamic typing needs discipline; typing tools help"),
        "reliability": (7, "Mature runtime; GIL constrains CPU parallelism, memory safety solid"),
        "concurrency_model": (6, "asyncio + threads; GIL limits CPU-bound parallelism"),
    },
    "node": {
        "ecosystem_maturity": (8, "Extremely widely deployed; predictable release cadence"),
        "ecosystem_libraries": (10, "npm is the largest package registry"),
        "maintainability": (6, "Dynamic JS at scale needs TS/tooling discipline"),
        "reliability": (7, "V8 is robust; callback-heavy designs leak complexity"),
        "concurrency_model": (8, "Event loop excels at I/O concurrency; CPU-bound work needs workers"),
    },
    "typescript": {
        "ecosystem_maturity": (8, "Industry-standard JS superset, backed by a major vendor"),
        "ecosystem_libraries": (10, "Full npm access plus types for most packages"),
        "maintainability": (9, "Static types dramatically improve large-codebase refactoring"),
        "reliability": (7, "Inherits V8 runtime behaviour; types catch whole error classes"),
        "concurrency_model": (8, "Same event-loop strengths and limits as JS"),
    },
    "php": {
        "ecosystem_maturity": (9, "30 years of web production use; the modern PHP 8 VM is solid"),
        "ecosystem_libraries": (9, "Composer + Packagist cover web development broadly"),
        "maintainability": (6, "Improved hugely in PHP 7/8; legacy style risk in old codebases"),
        "reliability": (7, "Request-scoped execution isolates leaks; shared-nothing FPM model"),
        "concurrency_model": (5, "Traditional shared-nothing model; async options are younger"),
    },
    "ruby": {
        "ecosystem_maturity": (8, "Long-established, mature Rails ecosystem"),
        "ecosystem_libraries": (8, "RubyGems strong on web tooling; narrower elsewhere"),
        "maintainability": (6, "Expressive but metaprogramming-heavy code can be hard to follow"),
        "reliability": (7, "Stable interpreter; memory footprint historically heavier"),
        "concurrency_model": (6, "Ractor/GVL limits parallel threads; fiber-based async maturing"),
    },
    "java": {
        "ecosystem_maturity": (10, "25+ years; strongest backwards-compatibility culture in industry"),
        "ecosystem_libraries": (10, "Maven Central: unmatched enterprise library depth"),
        "maintainability": (8, "Static typing + mature IDE/tooling; verbosity trade-off"),
        "reliability": (9, "Decades of hot-production hardening; predictable GC options"),
        "concurrency_model": (9, "Mature threading, executors, virtual threads"),
    },
    "kotlin": {
        "ecosystem_maturity": (7, "Younger JVM citizen but vendor-backed; stable 2.x releases"),
        "ecosystem_libraries": (9, "Full JVM/Maven access plus Kotlin-first libraries"),
        "maintainability": (9, "Concise, null-safe, static; widely praised for large codebases"),
        "reliability": (8, "JVM runtime characteristics with fewer NPE failure modes"),
        "concurrency_model": (9, "Coroutines are among the best structured-concurrency designs"),
    },
    "go": {
        "ecosystem_maturity": (8, "Proven at hyperscale since 2012; predictable 1.x/2.x discipline"),
        "ecosystem_libraries": (8, "Strong stdlib; smaller but focused third-party ecosystem"),
        "maintainability": (9, "Small language, gofmt, built-in tooling discipline"),
        "reliability": (9, "GC'd, memory-safe, single static binaries; simple failure modes"),
        "concurrency_model": (10, "Goroutines + channels; the reference lightweight-concurrency model"),
    },
    "csharp": {
        "ecosystem_maturity": (9, "20+ years, vendor-backed with strong LTS cadence"),
        "ecosystem_libraries": (9, "NuGet broad and deep; excellent first-party libraries"),
        "maintainability": (9, "Modern static typing, analyzers, first-class tooling"),
        "reliability": (8, "Managed runtime, solid GC; long production record"),
        "concurrency_model": (9, "async/await originator; TPL mature"),
    },
    "swift": {
        "ecosystem_maturity": (6, "Mature on Apple platforms; server-side Swift younger and smaller"),
        "ecosystem_libraries": (7, "Swift Package Manager growing; server libraries fewer"),
        "maintainability": (8, "Strong static typing, value semantics; language evolves fast"),
        "reliability": (8, "Memory-safe by design; Windows/Linux ports less battle-tested"),
        "concurrency_model": (8, "Structured async/await + actors built into the language"),
    },
    "rust": {
        "ecosystem_maturity": (6, "Younger (2015 1.0) but exceptionally stable semver discipline"),
        "ecosystem_libraries": (8, "crates.io strong and rising; some domains still thin"),
        "maintainability": (8, "Ownership model prevents whole bug classes; steeper learning curve"),
        "reliability": (10, "Memory safety without GC; no data races by construction"),
        "concurrency_model": (9, "Fearless concurrency enforced by the type system"),
    },
    "c": {
        "ecosystem_maturity": (10, "50 years; the foundation everything else builds on"),
        "ecosystem_libraries": (8, "Vast but aging; no package manager; quality varies"),
        "maintainability": (4, "Manual memory + weak typing make large-team maintenance costly"),
        "reliability": (4, "UB, buffer overflows and leaks are endemic without extreme discipline"),
        "concurrency_model": (5, "Raw threads + primitives; everything is manual"),
    },
    "cpp": {
        "ecosystem_maturity": (9, "40 years; modern C++11/17/20 revitalized the standard"),
        "ecosystem_libraries": (9, "Enormous depth; fragmented tooling"),
        "maintainability": (5, "Huge feature surface + manual lifetime management"),
        "reliability": (5, "Safety-critical footguns remain despite modern idioms"),
        "concurrency_model": (7, "std::thread/atomics; executors still maturing"),
    },
    # Spid3r is the SUBJECT language of this tool and always appears in the
    # ranking. Only criteria with a defensible factual basis are assessed;
    # the rest stay honestly unassessed (None) instead of invented.
    "matrix": {
        "ecosystem_maturity": (3, "Young single-vendor language (ANSNEW TECH); "
                                 "small production record so far"),
        "ecosystem_libraries": (5, "26 embedded stdlib modules and 254 "
                                   "built-in native functions (HTTP/WS/TLS, "
                                   "SQLite/PG/MySQL, JSON/TOML/YAML/XML/"
                                   "CBOR/Protobuf, regex, Unicode/tz); young "
                                   "third-party package registry"),
        "maintainability": (6, "Static type checker, nullability narrowing, "
                               "generics, canonical formatter (spid3r fmt), "
                               "LSP, and published docs/LANGUAGE_SPEC.md; "
                               "limited large-team track record"),
        # reliability / concurrency_model: deliberately unassessed until
        # multi-hour production soak evidence is established - never invented.
    },
}

# Registry key -> KB key (identity; 'node' covers JavaScript).
KB_KEY = {}

# Production-first representative order so Spid3r's subject row is compared
# apples-to-apples against release/JIT baselines (rustc -O, Node, PHP, Python)
# rather than diluted by unoptimized debug (--no-opt) or tree-walk modes.
SPID3R_PRODUCTION_ORDER = (
    "spid3r_native",
    "spid3r_vm",
    "spid3r_bytecode",
    "spid3r_tw",
    "spid3r_vm_noopt",
)

_LANG_ALIAS = {
    "spid3r": "matrix",
    "matrix": "matrix",
    "javascript": "node",
    "js": "node",
    "nodejs": "node",
    "node": "node",
    "c#": "csharp",
    ".net": "csharp",
    "dotnet": "csharp",
    "c++": "cpp",
}


def kb_for(key):
    k = str(key)
    if k == "matrix" or k.startswith("spid3r"):
        return KB.get("matrix")          # every engine variant shares the KB
    return KB.get(KB_KEY.get(k, k))


# Friendly display labels for the Spid3r engine-variant row keys produced by
# the runtime comparison (tree-walk / VM / VM --no-opt / bytecode / native).
SPID3R_VARIANT_LABELS = {
    "spid3r_tw": "Spid3r (tree-walk)",
    "spid3r_vm": "Spid3r (VM)",
    "spid3r_vm_noopt": "Spid3r (VM --no-opt)",
    "spid3r_bytecode": "Spid3r (bytecode exec)",
    "spid3r_native": "Spid3r (native --release)",
}


def is_spid3r(key):
    return str(key) == "matrix" or str(key).startswith("spid3r")


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def _normalize(values, lower_is_better):
    """Log-linear normalization to 0..10 (10 = best in set).

    Rank-preserving and scale-free: t_best -> 10, t_worst -> 0, geometric
    midpoints map proportionally. All values must be > 0.
    """
    vals = {k: float(v) for k, v in values.items() if v and float(v) > 0}
    if not vals:
        return {}
    best_t, worst_t = min(vals.values()), max(vals.values())
    if math.isclose(best_t, worst_t):
        return {k: 10.0 for k in vals}
    span = math.log(worst_t / best_t)
    out = {}
    for k, v in vals.items():
        frac = math.log(v / best_t) / span          # 0 at best, 1 at worst
        out[k] = round(10.0 * ((1.0 - frac) if lower_is_better else frac), 2)
    return out


def _measured_scores(rc_data):
    """Per-language measured criterion values from runtime-comparison rows."""
    comp, mem, cpu = {}, {}, {}
    for wid, w in ((rc_data or {}).get("workloads") or {}).items():
        if not w.get("verified"):
            continue
        for r in w.get("rows") or []:
            k = r.get("key")
            if not k:
                continue
            comp.setdefault(k, []).append(r.get("compute_ms"))
            if r.get("peak_rss_bytes"):
                mem.setdefault(k, []).append(r["peak_rss_bytes"])
            cu = r.get("cpu_user_s")
            ck = r.get("cpu_kernel_s")
            if cu is not None:
                cpu.setdefault(k, []).append(cu + (ck or 0.0))

    def geomean(vals):
        vals = [v for v in vals if v and v > 0]
        if not vals:
            return None
        return math.exp(sum(math.log(v) for v in vals) / len(vals))

    return {
        "compute_performance": {k: geomean(v) for k, v in comp.items()},
        "memory_efficiency": {k: geomean(v) for k, v in mem.items()},
        "cpu_efficiency": {k: geomean(v) for k, v in cpu.items()},
    }


def _pick_spid3r_representative(raw_measured):
    """Select one production-priority Spid3r variant consistently."""
    avail = set()
    for k, v in ((raw_measured or {}).get("compute_performance") or {}).items():
        if str(k).startswith("spid3r") and v and v > 0:
            avail.add(k)
    if not avail:
        for vals in (raw_measured or {}).values():
            for k, v in (vals or {}).items():
                if str(k).startswith("spid3r") and v and v > 0:
                    avail.add(k)
    for cand in SPID3R_PRODUCTION_ORDER:
        if cand in avail:
            return cand
    return sorted(avail)[0] if avail else None


def _measured_http_scores(campaign_data):
    """Extract per-language HTTP metrics from a comparable multi-language campaign.

    Single-target runs and non-comparable campaigns yield {} so single-run HTTP
    numbers are never extrapolated into cross-language rankings.
    """
    if not isinstance(campaign_data, dict) or campaign_data.get("comparable") is not True:
        return {}
    lb = campaign_data.get("leaderboard") or []
    if len(lb) < 2:
        return {}

    agg_by_site = {}
    for site in campaign_data.get("sites") or []:
        sid = site.get("site_id")
        if sid:
            agg_by_site[sid] = site.get("aggregated") or {}

    best_by_lang = {}
    for row in lb:
        raw_lang = str(row.get("language") or "").strip().lower()
        lang = _LANG_ALIAS.get(raw_lang, raw_lang)
        rps = row.get("overall_rps")
        if not lang or not rps or float(rps) <= 0:
            continue
        rps = float(rps)
        prev = best_by_lang.get(lang)
        if prev is not None and rps <= prev["rps"]:
            continue
        sid = row.get("site_id")
        agg_rps = (agg_by_site.get(sid) or {}).get("overall_rps") or {}
        med = agg_rps.get("median") or rps
        sd = agg_rps.get("stddev") or 0.0
        cv_pct = (
            (100.0 * float(sd) / float(med))
            if (med and float(med) > 0 and float(sd) >= 0)
            else 0.0
        )
        p95 = row.get("p95_ms")
        err_pct = max(0.0, float(row.get("error_pct") or 0.0))
        best_by_lang[lang] = {
            "rps": rps,
            "p95": float(p95) if (p95 is not None and float(p95) > 0) else None,
            "instability": 1.0 + err_pct + cv_pct,
        }

    if len(best_by_lang) < 2:
        return {}
    out = {
        "http_throughput": {k: v["rps"] for k, v in best_by_lang.items()},
        "http_latency": {k: v["p95"] for k, v in best_by_lang.items() if v["p95"] is not None},
        "http_stability": {k: v["instability"] for k, v in best_by_lang.items()},
    }
    return {k: v for k, v in out.items() if len(v) >= 2}


def _fmt_metric(crit, value):
    if value is None:
        return "n/a"
    if crit == "memory_efficiency":
        return "%.1f MiB" % (value / 1048576.0)
    if crit == "http_throughput":
        return "%.0f RPS" % value
    if crit == "http_stability":
        return "%.2f instability" % value
    return _fmt(value)


# ---------------------------------------------------------------------------
# Assessment assembly
# ---------------------------------------------------------------------------
def build(results, cfg, out_dir, log=None, write_html=True):
    """Produce the suitability assessment and write its artifacts.

    Reads the run dir's full ``runtime_comparison.json`` for measured rows
    and writes ``suitability.json`` into ``out_dir`` (plus the standalone
    ``suitability.html`` page when ``write_html`` is set — full benchmark
    runs embed ``build_body`` in report.html instead). Returns the full
    suitability data dict. Never raises into the caller.
    """
    weights_cfg = dict(((cfg.get("suitability") or {}).get("weights")) or {})
    weights = {}
    for k, w in weights_cfg.items():
        if k in CRITERIA and float(w) > 0:
            weights[k] = float(w)
    if not weights:
        weights = {k: 1.0 for k in CRITERIA}
    total_w = sum(weights.values())
    weights = {k: w / total_w for k, w in weights.items()}

    # Measured evidence: the run dir holds the full runtime comparison doc.
    rc_data = {}
    rc_path = os.path.join(out_dir, "runtime_comparison.json")
    if os.path.isfile(rc_path):
        try:
            with open(rc_path, "r", encoding="utf-8") as f:
                rc_data = json.load(f)
        except Exception:
            rc_data = {}

    campaign_data = {}
    camp_cfg_path = ((cfg.get("suitability") or {}).get("campaign_json")) or ""
    for cp in (os.path.join(out_dir, "campaign.json"), camp_cfg_path):
        if cp and os.path.isfile(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    campaign_data = json.load(f)
                break
            except Exception:
                campaign_data = {}

    raw_measured = _measured_scores(rc_data)
    rep_key = _pick_spid3r_representative(raw_measured)

    # Use ONE consistent production Spid3r variant (spid3r_native first) rather
    # than averaging debug --no-opt / tree-walk timings into AOT results. All
    # individual variants remain disclosed in data["spid3r"]["variants"].
    def _merge_spid3r(values):
        rest = {
            k: v for k, v in (values or {}).items()
            if not str(k).startswith("spid3r")
        }
        if rep_key and rep_key in (values or {}) and values[rep_key] and values[rep_key] > 0:
            rest["matrix"] = values[rep_key]
        else:
            for cand in SPID3R_PRODUCTION_ORDER:
                v = (values or {}).get(cand)
                if v and v > 0:
                    rest["matrix"] = v
                    break
        return rest

    raw_measured = {crit: _merge_spid3r(v) for crit, v in raw_measured.items()}
    http_measured = _measured_http_scores(campaign_data)
    for crit in MEASURED_HTTP:
        if crit in http_measured:
            raw_measured[crit] = http_measured[crit]

    normalized = {}
    for crit, spec in CRITERIA.items():
        if spec["provenance"] != "measured":
            continue
        vals = raw_measured.get(crit) or {}
        if crit in MEASURED_HTTP and not vals:
            continue
        normalized[crit] = _normalize(vals, spec["lower_is_better"])

    # Assemble rows for every language with measured data or a KB entry.
    langs = set(normalized.get("compute_performance") or {})
    for crit in MEASURED_HTTP:
        langs |= set(normalized.get(crit) or {})
    langs |= set(KB.keys())
    rows = []
    for key in sorted(langs):
        criteria = {}
        for crit, spec in CRITERIA.items():
            if spec["provenance"] == "measured":
                score = (normalized.get(crit) or {}).get(key)
                value = (raw_measured.get(crit) or {}).get(key)
                evidence = ("geomean %s" % _fmt_metric(crit, value)
                            if value is not None else "no measured data")
            else:
                kb = kb_for(key)
                if kb and crit in kb:
                    score, just = kb[crit]
                    evidence = just
                elif kb:
                    score, just = None, None
                    evidence = ("documented criterion deliberately "
                                "unassessed - no defensible basis yet, "
                                "never invented")
                else:
                    score, just = None, None
                    evidence = "no documented assessment for this language"
            criteria[crit] = {
                "score": score,
                "provenance": spec["provenance"],
                "evidence": evidence,
            }
        # Weighted overall with redistribution over available criteria.
        got_w = sum(w for c, w in weights.items()
                    if criteria.get(c, {}).get("score") is not None)
        measured_w = sum(w for c, w in weights.items()
                         if criteria.get(c, {}).get("score") is not None
                         and CRITERIA[c]["provenance"] == "measured")
        documented_w = got_w - measured_w
        if got_w > 0:
            overall = sum(
                w * criteria[c]["score"] for c, w in weights.items()
                if criteria.get(c, {}).get("score") is not None) / got_w * 10.0
        else:
            overall = None
        rows.append({
            "language": key,
            "label": _label(key),
            "overall_score": round(overall, 1) if overall is not None else None,
            "measured_weight_share": round(100.0 * safe_div0(measured_w, got_w),
                                           1) if got_w else None,
            "measured_weight_pct_of_total": round(100.0 * measured_w, 1),
            "documented_weight_pct_of_total": round(100.0 * documented_w, 1),
            "missing_weight_pct_of_total": round(100.0 * (1.0 - got_w), 1),
            "criteria": criteria,
        })
    ranked = [r for r in rows if r["overall_score"] is not None]
    has_any_measured = any((r.get("measured_weight_share") or 0) > 0 for r in ranked)
    if has_any_measured:
        # Never let 0%-measured KB-only rows outrank actually measured languages.
        ranked.sort(key=lambda r: (
            0 if (r.get("measured_weight_share") or 0) > 0 else 1,
            -r["overall_score"],
            r["language"],
        ))
    else:
        ranked.sort(key=lambda r: (-r["overall_score"], r["language"]))
    for i, r in enumerate(ranked):
        r["rank"] = i + 1

    measured_ranked = [r for r in ranked if (r.get("measured_weight_share") or 0) > 0]
    for i, r in enumerate(measured_ranked):
        r["measured_rank"] = i + 1
    documented_only = [r for r in ranked if (r.get("measured_weight_share") or 0) == 0]

    data = {
        "spid3r": _spid3r_report(
            rc_data, ranked, rep_key=rep_key, measured_total=len(measured_ranked)
        ),
        "schema_version": 2,
        "generated_utc": utc(),
        "status": "ok" if ranked else "unavailable",
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "criteria": {k: {"label": v["label"], "provenance": v["provenance"],
                         "desc": v["desc"]} for k, v in CRITERIA.items()},
        "ranked": ranked,
        "measured_ranked": measured_ranked,
        "documented_only": documented_only,
        "unranked": [r for r in rows if r["overall_score"] is None],
        "disclaimer": ("Documented criteria are curated qualitative "
                       "judgments about ecosystem characteristics, clearly "
                       "separated from measured values. Measured values come "
                       "from this run's checksum-verified runtime comparison "
                       "and comparable multi-language campaigns on THIS machine. "
                       "Unmeasured KB-only reference rows are listed separately "
                       "so 0%-measured opinions never outrank measured languages."),
        "warnings": [],
    }
    try:
        atomic_json(os.path.join(out_dir, "suitability.json"), data)
        if write_html:
            build_html(data, os.path.join(out_dir, "suitability.html"))
        data["artifacts"] = {"json": "suitability.json",
                             "html": ("suitability.html" if write_html
                                      else "")}
    except Exception as ex:
        data["warnings"].append("artifact write failed: %s" % ex)
    return data


def summarize(data):
    """Compact dict embedded into results.json."""
    ranked = (data or {}).get("ranked") or []
    sp3 = (data or {}).get("spid3r") or {}
    subj = sp3.get("subject") or {}
    return {
        "schema_version": (data or {}).get("schema_version", 1),
        "status": (data or {}).get("status"),
        "generated_utc": (data or {}).get("generated_utc"),
        "top": [{"rank": r["rank"], "language": r["label"],
                 "score": r["overall_score"],
                 "measured_share": r["measured_weight_share"]}
                for r in ranked[:8]],
        "spid3r": {
            "label": "Spid3r (subject language)",
            "rank": subj.get("rank"),
            "score": subj.get("score"),
            "measured_share": subj.get("measured_share"),
            "has_comparison": sp3.get("has_comparison", False),
            "variants": [v.get("label") for v in sp3.get("variants") or []],
            "note": sp3.get("note"),
        },
        "measured_share_note": "share of effective weight backed by this "
                               "run's measurements (after redistribution)",
        "artifacts": ((data or {}).get("artifacts")
                      or {"json": "suitability.json",
                          "html": "suitability.html"}),
        "warnings": (data or {}).get("warnings", []),
    }


def _label(key):
    if key in SPID3R_VARIANT_LABELS:
        return SPID3R_VARIANT_LABELS[key]
    return {
        "node": "JavaScript (Node)", "typescript": "TypeScript",
        "python": "Python", "php": "PHP", "ruby": "Ruby", "java": "Java",
        "kotlin": "Kotlin", "go": "Go", "csharp": "C# (.NET)",
        "swift": "Swift", "rust": "Rust", "c": "C", "cpp": "C++",
        "matrix": "Spid3r (subject language)",
    }.get(key, key)


def _spid3r_report(rc_data, ranked, rep_key=None, measured_total=0):
    """Dedicated Spid3r (subject language) evidence block.

    Aggregates, from the run's runtime comparison:
      * the subject row's rank / score / provenance shares
      * per-ENGINE-VARIANT geomean slowdowns vs PHP / Python / fastest
      * per-workload absolute medians for every variant against the
        checksum-verified baselines
    All numbers come from checksum-verified rows; when the comparison was
    disabled for this run the block says so instead of inventing values.
    """
    s = (rc_data or {}).get("summary") or {}
    sd = s.get("slowdown") or {}
    variants = []
    for key in sorted(sd):
        if not str(key).startswith("spid3r"):
            continue
        e = sd[key]
        variants.append({
            "key": key,
            "label": _label(key),
            "vs_php": (e.get("vs_php") or {}).get("geomean"),
            "vs_python": (e.get("vs_python") or {}).get("geomean"),
            "vs_fastest": (e.get("vs_fastest") or {}).get("geomean"),
            "per_workload": {
                "vs_php": (e.get("vs_php") or {}).get("workloads") or {},
                "vs_python": (e.get("vs_python") or {}).get("workloads") or {},
                "vs_fastest": (e.get("vs_fastest") or {}).get("workloads") or {},
            },
        })
    workloads = []
    for wid in sorted((rc_data or {}).get("workloads") or {}):
        w = rc_data["workloads"][wid]
        if not w.get("verified"):
            continue
        ms = {}
        for r in w.get("rows") or []:
            ms.setdefault(str(r.get("key")), r.get("compute_ms"))
        workloads.append({
            "wid": wid, "id": w.get("id"), "title": w.get("title"),
            "expected": w.get("expected_checksum"),
            "baselines": {
                "php": ms.get("php"), "python": ms.get("python"),
                "fastest": min([v for v in ms.values() if v], default=None),
            },
            "spid3r_ms": {k: v for k, v in ms.items()
                          if str(k).startswith("spid3r")},
        })
    subject = None
    for r in ranked:
        if r.get("language") == "matrix":
            subject = {
                "rank": r.get("rank"),
                "measured_rank": r.get("measured_rank"),
                "measured_total": measured_total,
                "score": r.get("overall_score"),
                "measured_share": r.get("measured_weight_share"),
                "documented_pct": r.get("documented_weight_pct_of_total"),
                "missing_pct": r.get("missing_weight_pct_of_total"),
                "representative_variant": rep_key,
            }
            break
    has_comparison = bool(variants or workloads)
    return {
        "has_comparison": has_comparison,
        "representative_variant": rep_key,
        "representative_label": _label(rep_key) if rep_key else None,
        "subject": subject,
        "variants": variants,
        "workloads": workloads,
        "note": ("All Spid3r numbers come from checksum-verified, in-process "
                 "timings of the pinned workloads; the overall ranking uses the "
                 "production representative (%s) for apples-to-apples comparison "
                 "while every engine variant is disclosed below."
                 % (_label(rep_key) if rep_key else "production")
                 if has_comparison else
                 "This run disabled the runtime comparison "
                 "(--no-runtime-comparison), so no measured Spid3r data "
                 "exists here - run a full benchmark or `cli.py "
                 "--runtime-comparison` to populate this section. Nothing "
                 "is estimated."),
    }


def _fmt(v):
    if v is None:
        return "n/a"
    if v >= 1048576:
        return "%.1f MiB" % (v / 1048576.0)
    if v >= 10000:
        return "%.1f ms" % (v / 1000.0)
    if v >= 1000:
        return "%.2f ms" % (v / 1000.0)
    return "%.1f" % v


def safe_div0(a, b):
    return (a / b) if b else 0.0


# ---------------------------------------------------------------------------
# Self-contained HTML report
# ---------------------------------------------------------------------------
_CSS = """
:root{--bg:#f6f8fb;--card:#fff;--ink:#1b2434;--muted:#667085;--line:#e4e9f0;
--blue:#2563eb;--orange:#f85606;--green:#16a34a;--amber:#d97706;--red:#dc2626}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 22px 70px}
header.top{background:var(--card);border:1px solid var(--line);border-top:4px
solid var(--blue);border-radius:12px;padding:24px 28px}
h1{margin:0 0 6px;font-size:24px}.sub{margin:0;color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;
border-radius:999px;margin:10px 6px 0 0;background:#eef2f7;color:var(--muted)}
.badge.m{background:#e8f0fe;color:var(--blue)}.badge.d{background:#fff4e0;color:var(--amber)}
section.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 24px;margin-top:20px;min-width:0}
h2{font-size:17px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;margin:10px 0}
.tblwrap table{margin:0}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}
th{background:#f8fafc;font-size:11px;text-transform:uppercase;letter-spacing:.4px;
color:var(--muted)}
td.num,th.num{text-align:right;font-family:ui-monospace,Consolas,monospace;white-space:nowrap}
.bartrack{background:#eef1f6;border-radius:4px;height:14px;overflow:hidden;
position:relative;width:100%}.barfill{position:absolute;top:0;left:0;height:100%;
border-radius:4px;background:var(--blue)}
.tag{font-size:10px;font-weight:700;border-radius:4px;padding:1px 6px;white-space:nowrap}
.tm{color:#1d4ed8;background:#e8f0fe;border:1px solid #c3dbfe}
.td{color:#92400e;background:#fff4e0;border:1px solid #f3d19e}
.note{font-size:12px;color:var(--muted)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}
.kpi{background:#f9fbfe;border:1px solid var(--line);border-radius:9px;padding:10px 12px}
.kpi .l{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px}
.kpi .v{font-size:19px;font-weight:800;margin:2px 0}
.kpi .s{font-size:10.5px;color:var(--muted)}
ul{margin:6px 0 10px;padding-left:20px}li{margin-bottom:4px}
footer{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);
font-size:11.5px;color:var(--muted)}
"""


def _e(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def build_body(data):
    """Return the suitability report BODY as an HTML fragment.

    All sections (method, ranking, Spid3r subject report, per-criterion
    detail, weights, limitations, disclaimer) without a page shell. Embedded
    verbatim by report.build() into the single run report; build_html() wraps
    it for the standalone page.
    """
    s = data or {}
    P = []
    P.append('<div class="rc-status">')
    P.append('<span class="badge m">measured criteria: blue</span>')
    P.append('<span class="badge d">documented criteria: amber</span>')
    P.append('</div>')

    P.append('<section class="card"><h2>Method &amp; honesty rules</h2>')
    P.append('<ul>')
    P.append('<li><strong>Measured criteria</strong> come from this run: '
             'checksum-verified compute times plus OS-measured peak working '
             'set and CPU time per variant, normalized log-linearly between '
             'the best and worst measured language (10 = best, 0 = worst; '
             'rank-preserving and scale-free).</li>')
    P.append('<li><strong>Documented criteria</strong> are curated '
             'qualitative judgments with a published one-line justification '
             'each. They are NOT measurements of this machine.</li>')
    P.append('<li>A criterion without evidence is excluded and its weight is '
             'redistributed proportionally; every row shows how much of its '
             'effective weight was measured vs documented vs missing.</li>')
    P.append('<li>HTTP criteria require multi-language campaign data '
             '(identical workload, identical host); a single-run HTTP result '
             'describes one target application, not a language, so it is '
             'deliberately excluded here.</li>')
    P.append('</ul></section>')

    ranked = s.get("ranked") or []
    measured_ranked = s.get("measured_ranked") or [
        r for r in ranked if (r.get("measured_weight_share") or 0) > 0
    ]
    documented_only = s.get("documented_only") or [
        r for r in ranked if (r.get("measured_weight_share") or 0) == 0
    ]

    def _ranking_rows_html(rows_list, rank_key="rank"):
        out = []
        for r in rows_list:
            pct = max(2.0, float(r["overall_score"]))
            out.append(
                '<tr><td>%s</td><td>%s</td>'
                '<td><div class="bartrack"><div class="barfill" '
                'style="width:%.0f%%"></div></div></td>'
                '<td class="num"><strong>%.1f</strong></td>'
                '<td class="num">%s%%</td><td class="num">%s%%</td>'
                '<td class="num">%s%%</td></tr>'
                % (_e(r.get(rank_key) or r.get("rank")), _e(r.get("label")), pct,
                   r["overall_score"],
                   _e(r.get("measured_weight_share")),
                   _e(r.get("documented_weight_pct_of_total")),
                   _e(r.get("missing_weight_pct_of_total"))))
        return "".join(out)

    if measured_ranked:
        P.append('<section class="card"><h2>Measured languages ranking (this machine)</h2>')
        P.append('<div class="tblwrap"><table><thead><tr><th>#</th><th>Language</th>'
                 '<th style="width:34%">Score / 100</th>'
                 '<th class="num">Score</th><th class="num">Measured '
                 'share</th><th class="num">Documented</th>'
                 '<th class="num">Missing</th></tr></thead><tbody>')
        P.append(_ranking_rows_html(measured_ranked, "measured_rank"))
        P.append('</tbody></table></div>')
        P.append('<p class="note">Only languages actually benchmarked on this '
                 'machine appear above; unmeasured reference languages are '
                 'listed separately below so 0%-measured entries never '
                 'distort measured rankings.</p></section>')

    if documented_only:
        title = ('Unmeasured reference languages (0% measured &mdash; documented KB only)'
                 if measured_ranked else
                 'Overall ranking (documented KB only &mdash; 0% measured)')
        P.append('<section class="card"><h2>%s</h2>' % title)
        P.append('<div class="tblwrap"><table><thead><tr><th>#</th><th>Language</th>'
                 '<th style="width:34%">Score / 100</th>'
                 '<th class="num">Score</th><th class="num">Measured '
                 'share</th><th class="num">Documented</th>'
                 '<th class="num">Missing</th></tr></thead><tbody>')
        P.append(_ranking_rows_html(documented_only, "rank"))
        P.append('</tbody></table></div>')
        P.append('<p class="note">"Measured share" = percentage of the '
                 'effective weight that this run\'s own measurements back, '
                 'after redistribution. Install toolchains or run a campaign '
                 'to promote entries into the measured ranking.</p>')
        P.append('</section>')

    sp3 = s.get("spid3r") or {}
    rep_lbl = sp3.get("representative_label") or "production representative"
    P.append('<section class="card" id="spid3r">'
             '<h2>Spid3r &mdash; subject language report</h2>')
    P.append('<p class="note">Spid3r is the language this tool exists to '
             'measure. Its ranking row uses its production representative '
             '(<code>%s</code>) for apples-to-apples comparison against '
             'release/JIT baselines; all checksum-verified engine variants '
             '(tree-walk, VM, VM&nbsp;<code>--no-opt</code>, bytecode, '
             'native&nbsp;<code>--release</code>) remain transparently '
             'disclosed in the tables below and in '
             '<code>runtime_comparison.html</code>.</p>' % _e(rep_lbl))
    subj = sp3.get("subject")
    if subj:
        P.append('<div class="kpis">')
        m_rank = subj.get("measured_rank")
        m_tot = subj.get("measured_total") or len(measured_ranked)
        rank_str = "#%s" % _e(m_rank if m_rank else subj.get("rank"))
        rank_sub = ("of %d measured languages (overall #%s of %d)" % (
            m_tot, _e(subj.get("rank")), len(ranked))
            if m_rank else "of %d reference languages (0%% measured)" % len(ranked))
        P.append('<div class="kpi"><div class="l">Subject rank</div>'
                 '<div class="v">%s</div><div class="s">%s</div></div>'
                 % (rank_str, rank_sub))
        P.append('<div class="kpi"><div class="l">Overall score</div>'
                 '<div class="v">%s</div><div class="s">/ 100</div></div>'
                 % _e(subj.get("score")))
        P.append('<div class="kpi"><div class="l">Measured share</div>'
                 '<div class="v">%s%%</div><div class="s">effective weight '
                 'backed by measured data</div></div>'
                 % _e(subj.get("measured_share")))
        P.append('<div class="kpi"><div class="l">Documented weight</div>'
                 '<div class="v">%s%%</div><div class="s">%s%% missing '
                 '(unassessed criteria)</div></div>'
                 % (_e(subj.get("documented_pct")),
                    _e(subj.get("missing_pct"))))
        P.append('</div>')
    if sp3.get("variants"):
        P.append('<h3>Engine variants &mdash; geomean slowdown '
                 '(checksum-verified)</h3>')
        P.append('<div class="tblwrap"><table><thead><tr><th>Spid3r variant</th>'
                 '<th class="num">vs PHP</th><th class="num">vs Python</th>'
                 '<th class="num">vs fastest measured</th>'
                 '</tr></thead><tbody>')
        for v in sp3["variants"]:
            def _x(g):
                return '%.2f&times;' % g if g else 'n/a'
            P.append('<tr><td>%s</td><td class="num">%s</td>'
                     '<td class="num">%s</td><td class="num">%s</td></tr>'
                     % (_e(v.get("label")), _x(v.get("vs_php")),
                        _x(v.get("vs_python")), _x(v.get("vs_fastest"))))
        P.append('</tbody></table></div>')
        # Per-workload absolute medians: Spid3r variants vs the verified
        # baselines, so the per-workload picture is visible without opening
        # the runtime comparison report.
        wls = sp3.get("workloads") or []
        if wls:
            var_keys = sorted({k for w in wls
                               for k in (w.get("spid3r_ms") or {})})
            P.append('<h3>Per-workload medians (ms, lower is better)</h3>')
            head = '<tr><th>Workload</th><th class="num">Fastest</th>' \
                   '<th class="num">PHP</th><th class="num">Python</th>'
            for k in var_keys:
                head += '<th class="num">%s</th>' % _e(_label(k))
            head += '</tr>'
            P.append('<div class="tblwrap"><table><thead>%s</thead><tbody>' % head)
            for w in wls:
                b = w.get("baselines") or {}
                row = '<tr><td>%s. %s</td><td class="num">%s</td>' \
                      '<td class="num">%s</td><td class="num">%s</td>' % (
                          _e(w.get("id")), _e(w.get("title")),
                          _e(_fmt(b.get("fastest"))),
                          _e(_fmt(b.get("php"))), _e(_fmt(b.get("python"))))
                for k in var_keys:
                    row += '<td class="num">%s</td>' % _e(_fmt(
                        (w.get("spid3r_ms") or {}).get(k)))
                row += '</tr>'
                P.append(row)
            P.append('</tbody></table></div>')
            P.append('<p class="note">Workload B (JSON round-trip) has no '
                     'compiled-native reference by design; languages whose '
                     'standard library ships no JSON serializer are '
                     'documented <code>not applicable</code>, never '
                     'simulated.</p>')
    else:
        P.append('<p class="note">%s</p>' % _e(sp3.get("note") or
                                               "no comparison data"))
    P.append('<p class="note"><strong>Honesty note:</strong> Spid3r is '
             'assessed with the same rules as every other language. '
             'Documented criteria without a defensible basis (maintainability '
             'at team scale, reliability track record, concurrency model) '
             'are deliberately <code>unassessed</code> and their weight is '
             'redistributed - they will be filled in as the ecosystem '
             'matures, never invented.</p>')
    P.append('</section>')

    if ranked:
        P.append('<section class="card"><h2>Per-criterion detail</h2>')
        crits = s.get("criteria") or {}
        P.append('<div class="tblwrap"><table><thead><tr><th>Language</th>')
        for ck, cs in crits.items():
            P.append('<th class="num">%s</th>' % _e(cs.get("label", ck)))
        P.append('</tr></thead><tbody>')
        for r in ranked:
            P.append('<tr><td>%s</td>' % _e(r.get("label")))
            for ck in crits:
                c = (r.get("criteria") or {}).get(ck) or {}
                sc = c.get("score")
                prov = c.get("provenance")
                if sc is None:
                    P.append('<td class="num" title="%s">n/a</td>'
                             % _e(c.get("evidence", "")))
                else:
                    cls = "tm" if prov == "measured" else "td"
                    P.append('<td class="num"><span class="tag %s" title="%s">'
                             '%s</span></td>'
                             % (cls, _e("%s: %s" % (prov, c.get("evidence"))),
                                _e(sc)))
            P.append('</tr>')
        P.append('</tbody></table></div>')
        P.append('<p class="note">Hover a score for its evidence. Blue tags = '
                 'measured this run; amber tags = documented judgment.</p>')
        P.append('</section>')

    unranked = s.get("unranked") or []
    if unranked:
        P.append('<section class="card"><h2>No score available</h2><ul>')
        for r in unranked:
            P.append('<li>%s - no measured evidence and no documented '
                     'assessment; excluded from the ranking rather than '
                     'guessed.</li>' % _e(r.get("label")))
        P.append('</ul></section>')

    P.append('<section class="card"><h2>Criteria &amp; weights</h2>')
    P.append('<div class="tblwrap"><table><thead><tr><th>Criterion</th><th>Provenance</th>'
             '<th class="num">Weight</th><th>What it means</th></tr>'
             '</thead><tbody>')
    for ck, cs in (s.get("criteria") or {}).items():
        w = (s.get("weights") or {}).get(ck)
        P.append('<tr><td>%s</td><td>%s</td><td class="num">%s</td>'
                 '<td>%s</td></tr>'
                 % (_e(cs.get("label", ck)),
                    _e(cs.get("provenance")),
                    _e(("%s%%" % round(100 * w, 1)) if w is not None else "n/a"),
                    _e(cs.get("desc", ""))))
    P.append('</tbody></table></div></section>')

    P.append('<section class="card"><h2>Limitations &amp; framework context'
             '</h2><ul>')
    P.append('<li>Compute/memory/CPU scores reflect THIS machine, THIS OS and '
             'the pinned workloads only - they are not vendor-independent '
             'spec numbers.</li>')
    P.append('<li>Documented scores are judgments, included for completeness '
             'and always labelled; treat them as hypotheses to validate '
             'against your own requirements.</li>')
    P.append('<li>Framework choice, team experience, operations tooling and '
             'hiring markets routinely dominate language choice in practice; '
             'this table intentionally does not weigh those for you.</li>')
    P.append('</ul>')
    P.append('<p class="note"><strong>Framework context (documented, not '
             'measured):</strong> typical high-productivity HTTP stacks are '
             'FastAPI/Litestar (Python), Express/Fastify/Nest (Node+TS), '
             'Spring Boot/Quarkus (Java/JVM), ASP.NET Core (C#), '
             'Gin/Echo/Fiber (Go), Rails/Sinatra (Ruby), Laravel/Symfony '
             '(PHP), Actix/Axum (Rust), Vapor (Swift). Measure YOUR candidate '
             'stack with campaigns; never substitute this list for evidence.'
             '</p>')
    P.append('</section>')

    P.append('<section class="card"><h2>Disclaimer</h2>')
    P.append('<p class="note">%s</p>' % _e(s.get("disclaimer")))
    P.append('</section>')

    return "".join(P)


def build_html(data, out_path):
    """Render the standalone suitability page (single file, no JS)."""
    s = data or {}
    P = ['<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         "<title>Technology Suitability Assessment - %s</title>"
         % _e(s.get("generated_utc")),
         "<style>%s</style></head><body><div class=\"wrap\">" % _CSS,
         '<header class="top"><h1>Technology Suitability Assessment</h1>',
         '<p class="sub">Measured evidence + disclosed judgments '
         '&middot; generated %s</p>' % _e(s.get("generated_utc")),
         "</header>",
         build_body(data),
         "<footer>Heavy-Load Checker suitability assessment &middot; "
         "single-file report, no JavaScript, no external resources."
         "</footer>",
         "</div></body></html>"]

    dest = pathlib.Path(os.path.normpath(out_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(P), encoding="utf-8", newline="\n")
    return str(dest)
