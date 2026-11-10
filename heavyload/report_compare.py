"""Self-contained HTML comparison / leaderboard report (Phase 2).

Renders a campaign or an ad-hoc comparison of runs as one HTML file with
inline SVG (no scripts, no external resources) reusing the single-run report's
chart helpers. The numbers come already aggregated by ``campaign.run_campaign``
(median across repeats) or straight from ``compare.compare_runs``.
"""

from __future__ import annotations

from .report import (C, CSS, bar_chart, e, fmt, kpi, section, table)


_PALETTE = [C["blue"], C["violet"], C["teal"], C["amber"], C["green"],
            C["red"], C["slate"], "#0ea5e9"]

# Each chart: (title, metric_key, label, unit)
_CHARTS = [
    ("Throughput — RPS (median across repeats)", "overall_rps", "RPS", "(req/s)"),
    ("Latency — p95 ms (as-scheduled, median)", "p95_ms", "p95", "(ms)"),
    ("CPU — peak % net of idle (median)", "cpu_peak_pct_net", "CPU", "(%)"),
    ("Memory — peak anon MiB net of idle (median)", "mem_anon_mib_peak_net",
     "Mem", "(MiB)"),
    ("Efficiency — MB added per 1k requests (median)", "mb_per_1k_req",
     "MB/1k", "(MB)"),
    ("Health score /100 (median)", "health_score", "Health", ""),
]


def _leaderboard_rows(campaign):
    rows = []
    for r in campaign.get("leaderboard", []):
        rows.append([
            e(r.get("site_id", "")),
            e(r.get("language", "")),
            e(r.get("stack_label", "")),
            fmt(r.get("runtime_version") or "", 0) or "n/a",
            fmt(r.get("overall_rps"), 1),
            fmt(r.get("peak_phase_rps"), 1),
            fmt(r.get("p95_ms"), 1),
            fmt(r.get("error_pct"), 3, "%"),
            fmt(r.get("cpu_peak_pct_net"), 2),
            fmt(r.get("mem_anon_mib_peak_net"), 1),
            fmt(r.get("mb_per_1k_req"), 3),
            fmt(r.get("health_score"), 0),
            e(r.get("verdict_code") or ""),
        ])
    return rows


def render(campaign):
    """Return the comparison report HTML as a string (no file write)."""
    title = campaign.get("title") or "Benchmark comparison"
    profile = campaign.get("profile", "")
    comparable = campaign.get("comparable", True)
    reasons = campaign.get("comparison_reasons", [])
    lb = campaign.get("leaderboard", [])

    P = []
    P.append("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    P.append("<title>Heavy-Load Checker - %s</title>" % e(title))
    P.append("<style>%s</style></head><body><div class=\"wrap\">" % CSS)

    # ----------------------------------------------- header + comparability
    badge = ("FAIR COMPARISON" if comparable else "NOT COMPARABLE")
    bcolor = C["green"] if comparable else C["red"]
    P.append('<header class="top">')
    P.append("<h1>%s</h1>" % e(title))
    P.append("<p>Profile <strong>%s</strong> &nbsp;|&nbsp; %d sites "
             "&nbsp;|&nbsp; generated %s</p>"
             % (e(profile), len(lb), e(campaign.get("generated_utc", ""))))
    P.append('<div class="vbox"><div class="v" style="color:#fff">%s</div>'
             '<div style="font-size:12px;margin-top:4px;opacity:.92">'
             'Metrics are medians across repeats. A red badge means the runs '
             'were measured from different vantage points and must NOT be '
             'ranked against each other.</div></div>' % e(badge))
    P.append("</header>")

    if not comparable and reasons:
        items = "".join("<li>%s: %s</li>"
                        % (e(r.get("site_id") or r.get("run_id") or ""),
                           e(r.get("reason"))) for r in reasons)
        P.append(section("Comparability warning",
                         '<div class="note"><ul>%s</ul></div>' % items,
                         "Hard rule enforced by schema.can_compare."))
    elif not comparable:
        P.append(section("Comparability warning",
                         '<div class="note">These runs are not comparable '
                         '(different benchmark_mode / visibility / workload). '
                         'Do not rank them.</div>'))

    # ------------------------------------------------------------- KPIs
    if lb:
        best = lb[0]
        k = "".join([
            kpi("Best throughput", "%s RPS" % fmt(best.get("overall_rps"), 1),
                best.get("site_id", "")),
            kpi("Lowest p95", "%s ms" % fmt(best.get("p95_ms"), 1),
                best.get("site_id", "")),
            kpi("Lowest CPU", "%s%%" % fmt(best.get("cpu_peak_pct_net"), 2),
                best.get("site_id", "")),
            kpi("Lowest mem", "%s MiB"
                % fmt(best.get("mem_anon_mib_peak_net"), 1),
                best.get("site_id", "")),
            kpi("Sites compared", "%d" % len(lb),
                "comparable" if comparable else "NOT comparable"),
        ])
        P.append(section("Summary", '<div class="kpis">%s</div>' % k))

    # ------------------------------------------------------- leaderboard
    headers = ["Site", "Lang", "Stack", "Runtime", "RPS", "RPS peak",
               "p95 ms", "Err %", "CPU% net", "Mem MiB net", "MB/1k",
               "Health", "Verdict"]
    P.append(section("Leaderboard (median across repeats)",
                     table(headers, _leaderboard_rows(campaign)),
                     "Sorted by overall RPS. Net = idle baseline subtracted."))

    # ------------------------------------------------------------- charts
    charts = []
    for t, key, lab, unit in _CHARTS:
        labels = [r.get("site_id", "?") for r in lb]
        vals = [r.get(key) for r in lb]
        if not any(v is not None for v in vals):
            continue
        colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(labels))]
        charts.append((t, bar_chart(labels, vals, colors, lab, unit, 260)))
    ch = "".join('<h3>%s</h3><div class="chart">%s</div>' % (e(t), svg)
                 for t, svg in charts)
    if not charts:
        ch = '<div class="note">No comparable numeric series to chart.</div>'
    P.append(section("Side-by-side charts", ch,
                     "Inline SVG only; one bar per site, value = median."))

    # ----------------------------------------------- per-site repeats (var)
    rep_rows = []
    for rec in campaign.get("sites", []):
        if "error" in rec:
            continue
        repeats = rec.get("repeats") or []
        if not isinstance(repeats, list):
            repeats = []
        for i, m in enumerate(repeats, 1):
            rep_rows.append([
                e(rec.get("site_id", "")),
                "repeat %d" % i,
                fmt(m.get("overall_rps"), 1),
                fmt(m.get("p95_ms"), 1),
                fmt(m.get("cpu_peak_pct_net"), 2),
                fmt(m.get("mem_anon_mib_peak_net"), 1),
                fmt(m.get("health_score"), 0),
            ])
    P.append(section("Per-repeat detail (variance check)",
                     table(["Site", "Run", "RPS", "p95 ms", "CPU% net",
                            "Mem MiB net", "Health"], rep_rows),
                     "Confirms each site's repeats are tight before the "
                     "median is trusted."))

    # ---------------------------------------------------- campaign metadata
    meta_rows = [
        ["Campaign id", campaign.get("campaign_id", "")],
        ["Profile", profile],
        ["Warm-up runs (discarded)", campaign.get("warmup_runs", "")],
        ["Measured repeats", campaign.get("repeat_count", "")],
        ["Comparable", "yes" if comparable else "NO"],
        ["Generated", campaign.get("generated_utc", "")],
    ]
    P.append(section("Campaign metadata", table(["Field", "Value"], meta_rows)))

    P.append('<footer>Heavy-Load Checker — comparison report. Generated '
             '%s. Only GET requests; this is a software guardrail benchmark, '
             'not a production capacity certificate.</footer>'
             % e(campaign.get("generated_utc", "")))
    P.append("</div></body></html>")
    return "\n".join(P)


def build(campaign, out_path):
    """Render and write the comparison report to ``out_path``."""
    html = render(campaign)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path
