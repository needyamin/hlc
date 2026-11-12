#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-contained HTML report renderer.

Constraints (same as the original Spid3r report): single file, light theme,
inline CSS, inline SVG charts, NO JavaScript, NO CDN, NO external stylesheets,
NO external images. The report is safe to email or archive offline.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone

from .util import downsample

C = {
    "ink": "#1b2434", "muted": "#667085", "line": "#e4e9f0",
    "bg": "#f6f8fb", "card": "#ffffff", "blue": "#2563eb",
    "teal": "#0d9488", "amber": "#d97706", "red": "#dc2626",
    "green": "#16a34a", "violet": "#7c3aed", "slate": "#94a3b8",
}


def e(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def fmt(v, d=2, suffix=""):
    if v is None or v == "":
        return "n/a"
    if isinstance(v, (int, float)):
        return ("%.*f" % (d, v)) + suffix
    return e(v) + suffix


def badge(text, kind="info"):
    colors = {
        "ok": (C["green"], "#e8f7ee"), "bad": (C["red"], "#fdecec"),
        "warn": (C["amber"], "#fff4e0"), "info": (C["blue"], "#eaf1fe"),
        "na": (C["muted"], "#eef1f5"),
    }
    fg, bg = colors.get(kind, colors["info"])
    return ('<span style="display:inline-block;padding:2px 9px;border-radius:999px;'
            'font-size:11px;font-weight:700;color:%s;background:%s;'
            'white-space:nowrap">%s</span>' % (fg, bg, e(text)))


def grade_span(g):
    g = str(g or "N/A")
    if g.startswith("N/A"):
        k = "na"
    elif g == "PASS":
        k = "ok"
    elif g in ("FAIL", "CRITICAL"):
        k = "bad"
    elif g == "WARN":
        k = "warn"
    else:
        k = "info"
    return badge(g, k)


def sev_span(s):
    return badge(s, {"CRITICAL": "bad", "HIGH": "bad",
                     "MEDIUM": "warn", "LOW": "info"}.get(s, "na"))


def table(headers, rows, cls=""):
    out = ['<table class="%s"><thead><tr>' % cls]
    out += ["<th>%s</th>" % e(h) for h in headers]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        out += ["<td>%s</td>" % (c if isinstance(c, str) and
                                 c.startswith("<") else e(c)) for c in row]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


# ------------------------------------------------------------------- charts --
def line_chart(series, xmax, ymin, ymax, ylabel="", unit="", height=280,
               width=940, maxn=900):
    """series = [{'name','color','points':[(x,y)|None,...]}, ...]"""
    W, H = width, height
    L, R, T, B = 76, 18, 20, 36
    pw, ph = W - L - R, H - T - B
    if not xmax or xmax <= 0:
        xmax = 1.0
    if ymax <= ymin:
        ymax = ymin + 1.0
    pad = (ymax - ymin) * 0.08
    ymin, ymax = ymin - pad, ymax + pad

    def X(v):
        return L + (float(v) / xmax) * pw

    def Y(v):
        return T + ph - ((float(v) - ymin) / (ymax - ymin)) * ph

    o = ['<svg viewBox="0 0 %d %d" width="100%%" height="%d" role="img" '
         'style="display:block" xmlns="http://www.w3.org/2000/svg">' % (W, H, H)]
    o.append('<rect x="0" y="0" width="%d" height="%d" fill="%s"/>' % (W, H, C["card"]))
    for i in range(5):
        yv = ymin + (ymax - ymin) * i / 4.0
        y = Y(yv)
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                 'stroke-width="1"/>' % (L, y, W - R, y, C["line"]))
        o.append('<text x="%.1f" y="%.1f" text-anchor="end" font-size="11" '
                 'fill="%s">%s</text>' % (L - 8, y + 4, C["muted"],
                                          fmt(yv, 1 if ymax < 20 else 0)))
    for i in range(6):
        xv = xmax * i / 5.0
        x = X(xv)
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" '
                 'stroke-dasharray="3 4" stroke-width="1"/>'
                 % (x, T, x, T + ph, C["line"]))
        o.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="11" '
                 'fill="%s">%ss</text>' % (x, H - 12, C["muted"], fmt(xv, 0)))
    o.append('<text x="14" y="14" font-size="11" fill="%s">%s %s</text>'
             % (C["muted"], e(ylabel), e(unit)))
    lx = L + 8
    for s in series:
        pts = downsample([p for p in s["points"] if p is not None], maxn)
        if not pts:
            continue
        d = " ".join(("%.2f,%.2f" % (X(x), Y(y))) if i == 0
                     else ("L%.2f,%.2f" % (X(x), Y(y)))
                     for i, (x, y) in enumerate(pts))
        o.append('<path d="M%s" fill="none" stroke="%s" stroke-width="2" '
                 'stroke-linejoin="round" opacity="0.92"/>'
                 % (d, s.get("color", C["blue"])))
        o.append('<rect x="%.1f" y="%.1f" width="10" height="3" fill="%s"/>'
                 % (lx, T + 4, s.get("color", C["blue"])))
        o.append('<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text>'
                 % (lx + 14, T + 9, C["muted"], e(s["name"])))
        lx += 14 + 8 * len(s["name"]) + 26
    o.append("</svg>")
    return "".join(o)


def bar_chart(labels, values, colors=None, ylabel="", yunit="", height=250,
              width=940):
    W, H = width, height
    L, R, T, B = 76, 18, 20, 48
    pw, ph = W - L - R, H - T - B
    colors = colors or [C["blue"]] * len(values)
    vmax = max([float(v or 0) for v in values] or [1])
    if vmax <= 0:
        vmax = 1
    n = max(1, len(values))
    slot = pw / n
    bw = max(4, min(64, slot * 0.62))
    o = ['<svg viewBox="0 0 %d %d" width="100%%" height="%d" role="img" '
         'style="display:block" xmlns="http://www.w3.org/2000/svg">' % (W, H, H)]
    o.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, C["card"]))
    for i in range(5):
        yv = vmax * i / 4.0
        y = T + ph - (yv / vmax) * ph
        o.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s"/>'
                 % (L, y, W - R, y, C["line"]))
        o.append('<text x="%.1f" y="%.1f" text-anchor="end" font-size="11" '
                 'fill="%s">%s</text>' % (L - 8, y + 4, C["muted"], fmt(yv, 1)))
    for i, (lab, v) in enumerate(zip(labels, values)):
        h = (float(v or 0) / vmax) * ph
        x = L + slot * i + (slot - bw) / 2
        y = T + ph - h
        o.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" '
                 'fill="%s" opacity="0.9"/>'
                 % (x, y, bw, max(0.5, h), colors[i % len(colors)]))
        o.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="10" '
                 'fill="%s">%s</text>'
                 % (L + slot * i + slot / 2, H - 28, C["muted"], e(lab)))
        if float(v or 0) > 0:
            o.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="10" '
                     'fill="%s">%s</text>'
                     % (L + slot * i + slot / 2, y - 4, C["ink"], fmt(v, 1)))
    o.append('<text x="14" y="14" font-size="11" fill="%s">%s %s</text>'
             % (C["muted"], e(ylabel), e(yunit)))
    o.append("</svg>")
    return "".join(o)


def hbar(rows, width=560):
    """rows = [(label, value, color)]"""
    vmax = max([abs(float(v or 0)) for _, v, _ in rows] or [1]) or 1
    o = ['<svg viewBox="0 0 %d %d" width="100%%" height="%d" '
         'xmlns="http://www.w3.org/2000/svg">' % (width, 28 * len(rows) + 10,
                                                  28 * len(rows) + 10)]
    for i, (lab, v, col) in enumerate(rows):
        y = 8 + i * 28
        w = (abs(float(v or 0)) / vmax) * (width - 230)
        o.append('<text x="4" y="%d" font-size="11" fill="%s">%s</text>'
                 % (y + 13, C["ink"], e(lab)))
        o.append('<rect x="170" y="%d" width="%.1f" height="14" rx="3" '
                 'fill="%s" opacity="0.9"/>' % (y + 3, max(1, w), col))
        o.append('<text x="%.1f" y="%d" font-size="11" fill="%s">%s</text>'
                 % (176 + w, y + 14, C["muted"], fmt(v, 1)))
    o.append("</svg>")
    return "".join(o)


# ------------------------------------------------------------------- helpers --
def kpi(label, value, sub="", color=None):
    return ('<div class="kpi"><div class="kl">%s</div>'
            '<div class="kv" style="color:%s">%s</div>'
            '<div class="ks">%s</div></div>'
            % (e(label), color or C["ink"], e(value), e(sub)))


def section(title, body, sub=""):
    return ('<section class="card"><h2>%s</h2>%s%s</section>'
            % (e(title), ('<p class="sub">%s</p>' % e(sub)) if sub else "", body))


CSS = """
*{box-sizing:border-box}
body{margin:0;background:%(bg)s;color:%(ink)s;
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 22px 70px}
header.top{background:linear-gradient(120deg,#1e3a8a,#2563eb);color:#fff;
 border-radius:16px;padding:26px 30px;margin-bottom:22px;box-shadow:0 6px 24px rgba(37,99,235,.18)}
header.top h1{margin:0 0 6px;font-size:26px;letter-spacing:-.4px}
header.top p{margin:2px 0;opacity:.92;font-size:13px}
.vbox{margin-top:16px;background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.28);
 border-radius:10px;padding:12px 16px}
.vbox .v{font-size:19px;font-weight:800;letter-spacing:-.2px}
.card{background:%(card)s;border:1px solid %(line)s;border-radius:14px;padding:20px 22px;
 margin-bottom:18px;box-shadow:0 1px 3px rgba(16,24,40,.04)}
h2{font-size:17px;margin:0 0 14px;padding-bottom:10px;border-bottom:1px solid %(line)s;
 letter-spacing:-.2px}
h3{font-size:14px;margin:20px 0 8px;color:%(ink)s}
p.sub{color:%(muted)s;margin:-6px 0 14px;font-size:12.5px}
table{border-collapse:collapse;width:100%%;font-size:12.5px;margin:6px 0 4px}
th{background:#f8fafc;color:%(muted)s;text-align:left;font-weight:700;
 padding:8px 10px;border-bottom:2px solid %(line)s;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f0f3f8;vertical-align:top}
tr:hover td{background:#fbfcfe}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.kpi{background:#f9fbfe;border:1px solid %(line)s;border-radius:10px;padding:12px 14px}
.kl{font-size:11px;color:%(muted)s;text-transform:uppercase;letter-spacing:.5px}
.kv{font-size:21px;font-weight:800;margin:4px 0 1px;letter-spacing:-.5px}
.ks{font-size:11px;color:%(muted)s}
ul{margin:8px 0 4px;padding-left:20px}li{margin:5px 0}
.chart{border:1px solid %(line)s;border-radius:10px;overflow:hidden;margin:10px 0 4px}
.note{background:#f8fafc;border-left:3px solid %(blue)s;padding:10px 14px;
 border-radius:0 8px 8px 0;color:%(muted)s;font-size:12.5px;margin:10px 0}
.finding{border:1px solid %(line)s;border-left:4px solid %(slate)s;border-radius:0 10px 10px 0;
 padding:12px 16px;margin:10px 0;background:#fff}
.finding h4{margin:0 0 6px;font-size:13.5px}
.finding p{margin:4px 0;font-size:12.5px;color:%(muted)s}
.finding .lbl{display:inline-block;min-width:92px;color:%(ink)s;font-weight:600}
footer{color:%(muted)s;font-size:11.5px;text-align:center;padding:20px}
code{background:#f1f5f9;padding:1px 5px;border-radius:4px;font-size:12px}
""" % C


def _runtime_section(rc, run_id=""):
    """Optional section 11: cross-language runtime comparison summary.

    The full report (with charts, per-workload tables and raw data) lives next
    to this file as runtime_comparison.html; here we keep a compact digest so
    the main report still tells the whole story of a run at a glance.
    """
    status = rc.get("status") or "unknown"
    kind = "ok" if status == "ok" else ("warn" if status == "partial" else "bad")
    verdict = rc.get("verdict") or "No verdict."
    rng = rc.get("slowdown_range") or {}
    sd = rc.get("slowdown") or {}
    rows = []
    for key, ent in sorted(sd.items(), key=lambda kv: (
            (kv[1].get("vs_php") or {}).get("geomean") or 0)):
        g1 = (ent.get("vs_php") or {}).get("geomean")
        g2 = (ent.get("vs_python") or {}).get("geomean")
        rows.append([ent.get("label") or key,
                     "%.2fx" % g1 if g1 else "n/a",
                     "%.2fx" % g2 if g2 else "n/a"])
    body = ["<p>%s %s</p>" % (badge("status: %s" % status, kind), e(verdict))]
    if (rc.get("schema_version") in (None, 1)
            or not rc.get("runtime_environment")):
        body.append('<p class="note">Provenance not recorded for this archived '
                    'runtime comparison (schema v1); measurement sources are '
                    'unknown.</p>')
    else:
        body.append('<p class="note">Runtime environment: <strong>%s</strong>'
                    '%s</p>'
                    % (e(rc["runtime_environment"]),
                       " &middot; measured sources: %s"
                       % e(rc.get("comparison_sources"))
                       if rc.get("comparison_sources") else ""))
    body.append(
            '<p class="note">Language-runtime compute only (prime counting, '
            'JSON round-trip, map lookups). It is NOT an end-to-end HTTP '
            'framework comparison. Constants are pinned and any variant whose '
            'checksum differs from the verified value is rejected, not '
            'published.</p>')
    if rows:
        body.append(table(["Spid3r variant", "vs PHP (geomean)",
                           "vs Python (geomean)"], rows))
    if rng.get("min") and rng.get("max"):
        body.append('<p class="note">Measured range across all Spid3r '
                    'variants: <strong>%.2fx &ndash; %.2fx</strong> slower '
                    'than PHP / Python.</p>' % (rng["min"], rng["max"]))
    warns = [w for w in (rc.get("warnings") or []) if w]
    if warns:
        body.append("<h3>Warnings</h3><ul>" + "".join(
            "<li>%s</li>" % e(w) for w in warns[:6]) + "</ul>")
    art = rc.get("artifacts") or {}
    if art.get("html"):
        # Absolute artifact URLs resolve through the web UI's /runs/ endpoint.
        # When report.html is opened straight from disk, the sibling file
        # runtime_comparison.html is in the same folder.
        body.append('<p><a href="/runs/%s/%s">Full runtime comparison report '
                    '&#8599;</a> &nbsp;|&nbsp; <a href="/runs/%s/%s">raw JSON '
                    '&#8599;</a> &nbsp;|&nbsp; <span class="note">local file: '
                    '<code>runtime_comparison.html</code></span></p>'
                    % (e(run_id), e(art["html"]), e(run_id),
                       e(art.get("json", "runtime_comparison.json"))))
    return section("11. Language runtime comparison", "".join(body),
                   "Generated automatically on every run when "
                   "runtime_comparison.enabled is true.")


def build(results, out_path):
    a = results["adjudication"]
    s = results["summary"]
    r = results["resources"]
    meta = results["meta"]
    cfg = results["config"]
    lat = s["PRIMARY_successful_200_only"]
    abort = results["abort"]
    verdict = a["verdict"]
    no_go = ("NO-GO" in verdict or "ABORT" in verdict)
    vcolor = C["red"] if no_go else (C["amber"] if "CONDITIONS" in verdict
                                     else C["green"])

    P = []
    P.append("<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">")
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    P.append("<title>Heavy-Load Checker - %s</title>" % e(meta["run_id"]))
    P.append("<style>%s</style></head><body><div class=\"wrap\">" % CSS)

    # ------------------------------------------------------------- header --
    P.append('<header class="top">')
    P.append("<h1>Heavy-Load Behavior Report</h1>")
    P.append("<p>Target <strong>%s</strong> &nbsp;|&nbsp; Container "
             "<code>%s</code> &nbsp;|&nbsp; Profile <strong>%s</strong></p>"
             % (e(meta["target_url"]), e(meta["container"] or "none (direct mode)"),
                e(meta["profile"])))
    P.append("<p>Run %s &nbsp;|&nbsp; %s &nbsp;|&nbsp; %s</p>"
             % (e(meta["run_id"]), e(meta["started_utc"]),
                e(meta["platform"])))
    P.append('<div class="vbox"><div class="v" style="color:%s">%s</div>'
             '<div style="font-size:12px;margin-top:4px;opacity:.92">'
             'Overall grade %s &nbsp;|&nbsp; Health %d/100 &nbsp;|&nbsp; '
             '%d findings &nbsp;|&nbsp; %d of %d phases executed%s</div></div>'
             % (("#ffffff"), e(verdict), e(a["overall_grade"]),
                a["health_score"], len(a["findings"]),
                len(results["executed_phases"]), len(cfg["phases"]),
                (" &nbsp;|&nbsp; ABORT at t=%s s" % abort["at_s"]) if abort["flag"] else ""))
    P.append("</header>")

    # --------------------------------------------------------- exec summary --
    summary_html = "<ul>" + "".join("<li>%s</li>" % e(x)
                                    for x in a["executive_summary"]) + "</ul>"
    P.append(section("1. Executive summary", summary_html,
                     "Automated adjudication from measured series only."))

    # --------------------------------------------------------------- KPIs --
    k = "".join([
        kpi("Requests", "{:,}".format(s["total_requests"]),
            "%.2f RPS overall" % s["overall_rps"]),
        kpi("Error rate", "%.4f%%" % s["s1_error_pct"],
            "%d failures / %d connect errors" % (s["s1_errors"], s["connect_errors"]),
            C["red"] if s["s1_error_pct"] > cfg["slo"]["max_error_pct"] else C["green"]),
        kpi("p95 latency", "%.1f ms" % lat["p95"], "p50 %.1f ms" % lat["p50"]),
        kpi("p99 latency", "%.1f ms" % lat["p99"], "max %.1f ms" % lat["max"]),
        kpi("Anon peak", "%s MiB" % fmt(r.get("anon_peak_mib"), 1),
            "start %s -> end %s" % (fmt(r.get("anon_start_mib"), 1),
                                    fmt(r.get("anon_end_mib"), 1))),
        kpi("CPU peak", "%s%%" % fmt(r.get("cpu_peak_pct"), 1),
            "mean %s%%" % fmt(r.get("cpu_mean_pct"), 1)),
        kpi("Health probes", "%d" % (r.get("health_probes") or 0),
            "%s%% success" % fmt(r.get("health_success_pct"), 1)),
        kpi("Health score", "%d / 100" % a["health_score"],
            a["health_score_formula"][:44], vcolor),
    ])
    P.append(section("2. Key measurements", '<div class="kpis">%s</div>' % k))

    # ---------------------------------------------------------- SLO table --
    slo_rows = [[x["id"], x["name"], x["target"], x["measured"],
                 grade_span(x["grade"]), x["note"]] for x in a["slo"]]
    P.append(section("3. SLO scorecard",
                     table(["ID", "SLO", "Target", "Measured", "Grade", "Note"],
                           slo_rows),
                     "Overall grade = min(all graded SLOs). Any FAIL => NO-GO. "
                     "N/A rows are explicitly evidence-gated."))

    # ------------------------------------------------------------- charts --
    tl = results["timeline"]
    xmax = max([x["t"] + 10 for x in tl] or [s["duration_s"]])
    cg = results["series"].get("cgroup_series") or []
    charts = []

    charts.append(("Throughput and error rate over time",
                   line_chart([
                       {"name": "requests / s", "color": C["blue"],
                        "points": [(x["t"] + 5, x["rps"]) for x in tl]},
                   ], xmax, 0,
                       max([x["rps"] for x in tl] or [1]) * 1.15,
                       "Throughput", "(req/s)", 250)))
    charts.append(("Latency percentiles over 10 s bins",
                   line_chart([
                       {"name": "p50", "color": C["green"],
                        "points": [(x["t"] + 5, x["p50"]) for x in tl]},
                       {"name": "p95", "color": C["amber"],
                        "points": [(x["t"] + 5, x["p95"]) for x in tl]},
                       {"name": "p99", "color": C["red"],
                        "points": [(x["t"] + 5, x["p99"]) for x in tl]},
                   ], xmax, 0,
                       max([x["p99"] for x in tl] or [1]) * 1.15,
                       "Latency", "(ms)", 270)))
    if cg:
        anon_pts = [(x["t"], x["anon_mib"]) for x in cg
                    if x.get("anon_mib") is not None]
        cur_pts = [(x["t"], x["current_mib"]) for x in cg
                   if x.get("current_mib") is not None]
        vals = [v for _, v in anon_pts + cur_pts] or [1]
        charts.append(("Container memory (cgroup raw bytes -> MiB)",
                       line_chart([
                           {"name": "anon (canonical)", "color": C["violet"],
                            "points": anon_pts},
                           {"name": "memory.current", "color": C["teal"],
                            "points": cur_pts},
                       ], xmax, 0, max(vals) * 1.1, "Memory", "(MiB)", 270)))
        cpu_pts = [(x["t"], x["cpu_pct"]) for x in cg
                   if x.get("cpu_pct") is not None]
        if cpu_pts:
            charts.append(("Container CPU (cpu.stat usage_usec deltas)",
                           line_chart([{"name": "CPU %", "color": C["blue"],
                                        "points": cpu_pts}],
                                      xmax, 0, max([v for _, v in cpu_pts]) * 1.15,
                                      "CPU", "(%)", 230)))
    active_ph = [p for p in results["phases"] if p["requests"] > 0]
    if active_ph:
        labels = [p["name"].split("-")[-1][:9] for p in active_ph]
        charts.append(("p95 latency by phase",
                       bar_chart(labels, [p["latency_200"]["p95"]
                                          for p in active_ph],
                                 [C["blue"]] * len(active_ph),
                                 "p95", "(ms)", 230)))
        charts.append(("Requests by phase",
                       bar_chart(labels, [p["requests"] for p in active_ph],
                                 [C["teal"]] * len(active_ph),
                                 "Requests", "(count)", 230)))
    sc = s["status_counts"]
    if sc:
        keys = sorted(sc, key=lambda x: -sc[x])[:8]
        charts.append(("HTTP status distribution",
                       bar_chart(keys, [sc[k] for k in keys],
                                 [C["green"] if k.startswith("2") else
                                  (C["red"] if k.startswith("5") or k == "0"
                                   else C["amber"]) for k in keys],
                                 "Responses", "(count)", 230)))
    ch = "".join('<h3>%s</h3><div class="chart">%s</div>'
                 % (e(t), svg) for t, svg in charts)
    if not charts:
        ch = '<div class="note">No time-series evidence was captured.</div>'
    P.append(section("4. Time-series evidence", ch,
                     "Inline SVG only; no scripts or external resources. "
                     "Series are downsampled to at most 900 points."))

    # ------------------------------------------------------- phase table --
    ph_rows = [[p["name"], p["label"], fmt(p["start_s"], 1),
                fmt(p["end_s"], 1), p["concurrency"],
                "{:,}".format(p["requests"]), fmt(p["rps"], 1),
                fmt(p["latency_200"]["p95"], 1),
                fmt(p["latency_200"]["p99"], 1),
                fmt(p["error_pct"], 3, "%"),
                fmt(p["bytes_mib"], 1)]
               for p in results["phases"]]
    P.append(section("5. Phase breakdown",
                     table(["Phase", "Label", "Start s", "End s", "C",
                            "Reqs", "RPS", "p95 ms", "p99 ms", "Err %", "MiB"],
                           ph_rows)))

    pa_rows = [[p["path"], "{:,}".format(p["requests"]),
                fmt(p["latency"]["p50"], 1), fmt(p["latency"]["p95"], 1),
                fmt(p["latency"]["p99"], 1), fmt(p["error_pct"], 3, "%"),
                ", ".join("%s:%s" % (k, v) for k, v in
                          list(p["status_counts"].items())[:6])]
               for p in results["paths"]]
    P.append(section("6. Route breakdown",
                     table(["Path", "Reqs", "p50 ms", "p95 ms", "p99 ms",
                            "Err %", "Status counts"], pa_rows),
                     "Weighted GET route mix as configured in the interface."))

    # ----------------------------------------------------------- resources --
    res_rows = [
        ["Canonical memory metric", r.get("canonical_metric")],
        ["cgroup / stats / health samples",
         "%d / %d / %d" % (r.get("cgroup_samples") or 0,
                           r.get("stats_samples") or 0,
                           r.get("health_probes") or 0)],
        ["Anon start / peak / end (MiB)",
         "%s / %s / %s" % (fmt(r.get("anon_start_mib"), 1),
                           fmt(r.get("anon_peak_mib"), 1),
                           fmt(r.get("anon_end_mib"), 1))],
        ["Fitted active slope (MiB/s)",
         "%s (r2=%s, Theil-Sen=%s, Spearman=%s)"
         % (fmt(r.get("active_slope_mib_per_s"), 4),
            fmt(r.get("active_slope_r2"), 3),
            fmt(r.get("active_theil_sen_mib_per_s"), 4),
            fmt(r.get("active_spearman"), 3))],
        ["Gross monotonic fraction", fmt(r.get("gross_monotonic_fraction"), 3)],
        ["Cooldown retained", "%s%% (%s -> %s MiB)"
         % (fmt(r.get("cooldown_retained_pct"), 1),
            fmt(r.get("cooldown_start_mib"), 1),
            fmt(r.get("cooldown_end_mib"), 1))],
        ["Large unmap events (>100 MiB)", r.get("large_unmap_count")],
        ["pgscan / pgsteal / oom_kill / PSI max",
         "%s / %s / %s / %s"
         % (r.get("pgscan_max"), r.get("pgsteal_max"),
            r.get("oom_kill_events"), fmt(r.get("psi_max_avg10"), 2))],
        ["CPU peak / mean (%)", "%s / %s" % (fmt(r.get("cpu_peak_pct"), 2),
                                             fmt(r.get("cpu_mean_pct"), 2))],
        ["PIDs max / host free RAM min (GB)",
         "%s / %s" % (r.get("pids_max"), fmt(r.get("host_free_gb_min"), 2))],
        ["Container state before / after",
         "%s / %s" % ((results.get("inspect_before") or {}).get("state", "?"),
                      (results.get("inspect_after") or {}).get("state", "?"))],
    ]
    P.append(section("7. Resource and stability evidence",
                     table(["Metric", "Value"], res_rows)))

    # ------------------------------------------------------------ findings --
    fhtml = []
    for x in a["findings"]:
        border = {"CRITICAL": C["red"], "HIGH": C["red"],
                  "MEDIUM": C["amber"], "LOW": C["blue"]}.get(x["severity"],
                                                              C["slate"])
        fhtml.append(
            '<div class="finding" style="border-left-color:%s">'
            '<h4>%s %s</h4>'
            '<p><span class="lbl">Evidence</span>%s</p>'
            '<p><span class="lbl">Impact</span>%s</p>'
            '<p><span class="lbl">Recommendation</span>%s</p></div>'
            % (border, sev_span(x["severity"]), e(x["title"]),
               e(x["evidence"]), e(x["impact"]), e(x["recommendation"])))
    P.append(section("8. Severity-ranked findings",
                     "".join(fhtml) or '<div class="note">No findings.</div>',
                     "%d findings; health formula: %s"
                     % (len(a["findings"]), a["health_score_formula"])))

    # ---------------------------------------------------------- conditions --
    cond = "<ol>" + "".join("<li>%s</li>" % e(x)
                            for x in a["go_conditions"]) + "</ol>"
    P.append(section("9. Go / No-Go conditions", cond))

    # ------------------------------------------------------- config & notes --
    conf_rows = [
        ["Target URL", meta["target_url"]],
        ["Container / MySQL container",
         "%s / %s" % (meta["container"] or "(none)",
                      meta["mysql_container"] or "(none)")],
        ["Mode / profile", "%s / %s" % (cfg["mode"], meta["profile"])],
        ["Runtime environment / benchmark mode",
         "%s / %s" % (cfg.get("runtime_environment")
                      or "unavailable (legacy config)",
                      cfg.get("benchmark_mode", "auto"))],
        ["Schedule / peak concurrency",
         "%d s / %d workers" % (meta["schedule_s"], meta["peak_concurrency"])],
        ["Route mix", ", ".join("%s (w=%s)" % (p["path"], p["weight"])
                                for p in cfg["paths"])],
        ["Health path", cfg["health_path"]],
        ["Request timeout / keep-alive",
         "%s s / %s" % (cfg["request_timeout_s"], cfg["keep_alive"])],
        ["Guardrails", "; ".join("%s=%s" % (k, v)
                                 for k, v in cfg["guardrails"].items())],
        ["SLO targets", "; ".join("%s=%s" % (k, v)
                                  for k, v in cfg["slo"].items())],
        ["Python / host CPUs", "%s / %s" % (meta["python"], meta["cpu_count"])],
    ]
    disc = "<ul>" + "".join("<li>%s</li>" % e(x)
                            for x in meta["disclosures"]) + "</ul>"
    env = results.get("environment") or {}
    env_rows = [[str(k), str(v)] for k, v in env.items()]
    windows_note = ""
    if cfg.get("runtime_environment") == "windows":
        windows_note = ('<div class="note"><strong>Windows runtime mode '
                        '&mdash; client-only run.</strong> No server CPU, cgroup '
                        'memory, PSI or OOM metrics were collected. Latency, '
                        'throughput, error taxonomy and health are real; '
                        'memory-recovery SLOs are graded on insufficient '
                        'evidence, and this run must never be ranked against a '
                        'Docker lab run. Switch the Runtime environment to '
                        'Docker for full server visibility.</div>')
    P.append(section("10. Configuration, environment and limitations",
                     table(["Setting", "Value"], conf_rows)
                     + (table(["Environment", "Value"], env_rows) if env_rows else "")
                     + "<h3>Disclosures</h3>" + disc
                     + windows_note
                     + '<div class="note">%s</div>' % e(a["adjudication_note"]),
                     "Generated %s by Heavy-Load Checker %s."
                     % (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        meta["version"])))

    rc = results.get("runtime_comparison")
    if rc:
        P.append(_runtime_section(rc, meta.get("run_id", "")))

    P.append("<footer>Heavy-Load Checker %s &nbsp;|&nbsp; single-file report, "
             "no JavaScript, no external resources &nbsp;|&nbsp; run %s</footer>"
             % (e(meta["version"]), e(meta["run_id"])))
    P.append("</div></body></html>")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(P))
    return out_path
