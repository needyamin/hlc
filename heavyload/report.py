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
import pathlib
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
    out = ['<div class="tblwrap"><table class="%s"><thead><tr>' % cls]
    out += ["<th>%s</th>" % e(h) for h in headers]
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>")
        out += ["<td>%s</td>" % (c if isinstance(c, str) and
                                 c.startswith("<") else e(c)) for c in row]
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def wrap_tables(html_fragment):
    """Wrap every bare <table> in an overflow container.

    Used for embedded fragments (runtime comparison / suitability) whose
    tables are built directly; keeps wide tables from breaking the layout —
    they scroll horizontally inside their card instead.
    """
    import re
    return re.sub(r"<table>(.*?)</table>",
                  lambda m: '<div class="tblwrap"><table>%s</table></div>'
                  % m.group(1), html_fragment, flags=re.DOTALL)


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
/* Wide tables scroll inside their card instead of breaking the layout. */
.tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:6px 0 10px;
 border:1px solid %(line)s;border-radius:8px}
.tblwrap table{margin:0;min-width:100%%}
/* Embedded fragments (runtime comparison / suitability) re-use their own
 class vocabulary; scoped here so they never clash with the page styles. */
.rc-content .badge,.su-content .badge{display:inline-block;font-size:11px;
 font-weight:700;padding:3px 10px;border-radius:999px;margin:0 6px 6px 0;
 background:#eef2f7;color:%(muted)s}
.rc-content .badge.ok{background:#e8f7ee;color:%(green)s}
.rc-content .badge.warn{background:#fff4e0;color:%(amber)s}
.rc-content .badge.bad{background:#fdecec;color:%(red)s}
.rc-content .rc-status,.su-content .rc-status{margin:2px 0 8px}
.rc-content p.note,.su-content p.note{background:none;border:none;padding:0;
 color:%(muted)s;font-size:12px;margin:0 0 10px}
.rc-content .kpis,.su-content .kpis{display:grid;
 grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.rc-content .kpi{background:#f9fbfe;border:1px solid %(line)s;
 border-radius:9px;padding:12px 14px}
.rc-content .kpi .n{font-size:21px;font-weight:700}
.rc-content .kpi .l,.su-content .kpi .l{font-size:11px;color:%(muted)s}
.su-content .kpi{background:#f9fbfe;border:1px solid %(line)s;
 border-radius:9px;padding:10px 12px}
.su-content .kpi .v{font-size:19px;font-weight:800;margin:2px 0}
.su-content .kpi .s{font-size:10.5px;color:%(muted)s}
.rc-content .barrow{display:grid;grid-template-columns:250px 1fr 84px;gap:10px;
 align-items:center;margin-bottom:6px;font-size:12px}
.rc-content .barlabel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rc-content .bartrack{background:#eef1f6;border-radius:4px;height:15px;
 overflow:hidden;position:relative}
.rc-content .barrange{position:absolute;top:0;height:100%%;
 background:rgba(27,36,52,.14)}
.rc-content .barfill{position:absolute;top:0;left:0;height:100%%;
 border-radius:4px}
.rc-content .barval{text-align:right;font-family:ui-monospace,Consolas,monospace;
 color:%(muted)s}
.rc-content td.num,.rc-content th.num,.su-content td.num,.su-content th.num{
 text-align:right;font-family:ui-monospace,Consolas,monospace;white-space:nowrap}
.rc-content tr.spid3r td{background:#fffaf7}
.rc-content tr.base td{background:#f5f9ff}
.rc-content .qtag{font-size:10px;font-weight:700;border-radius:4px;
 padding:1px 6px;white-space:nowrap}
.rc-content .qok{color:#166534;background:#e8f7ee;border:1px solid #bfe8cf}
.rc-content .qwarn{color:#92400e;background:#fff4e0;border:1px solid #f3d19e}
.caption{font-size:12px;color:%(muted)s;margin:4px 0 16px}
.caption strong{color:%(ink)s}
.chart{border:1px solid %(line)s;border-radius:10px;overflow:hidden;
 margin:10px 0 2px;background:%(card)s}
.chart svg{display:block;width:100%%;height:auto}
.chart table{margin:0}
.chart .tblwrap{border:none}
.rc-content .srctag{font-size:10px;font-weight:700;color:#7c3aed;
 background:#f3e8ff;border:1px solid #ddd0fe;border-radius:4px;padding:1px 5px;
 margin-left:5px}
.rc-content .verdict{border-left:4px solid #f85606;background:#fff6f1;
 padding:12px 16px;border-radius:0 8px 8px 0;margin:12px 0}
.rc-content .barlabel,.su-content .barlabel{overflow:hidden;
 text-overflow:ellipsis;white-space:nowrap}
.su-content .tag{font-size:10px;font-weight:700;border-radius:4px;padding:1px 6px;
 white-space:nowrap}
.su-content .tm{color:#1d4ed8;background:#e8f0fe;border:1px solid #c3dbfe}
.su-content .td{color:#92400e;background:#fff4e0;border:1px solid #f3d19e}
.su-content .bartrack{background:#eef1f6;border-radius:4px;height:14px;
 overflow:hidden;position:relative;width:100%%}
.su-content .barfill{position:absolute;top:0;left:0;height:100%%;
 border-radius:4px;background:%(blue)s}
@media(max-width:720px){.rc-content .barrow{grid-template-columns:140px 1fr 70px;
 font-size:11px}}
""" % C


def _load_sibling(out_path, name):
    """Load a JSON artifact that sits next to the report (runtime_comparison
    / suitability full data). Returns None when absent or unreadable."""
    import json as _json
    p = pathlib.Path(os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(out_path)), name)))
    if not p.is_file():
        return None
    try:
        return _json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _suitability_digest(su):
    """Compact suitability block for reports whose full suitability JSON is
    absent (archived/foreign results documents)."""
    top = su.get("top") or []
    rows = [[t.get("rank"), t.get("language"),
             "%.1f / 100" % t.get("score") if t.get("score") is not None
             else "n/a",
             "%s%%" % fmt(t.get("measured_share"), 0)] for t in top]
    sp3 = su.get("spid3r") or {}
    sp_row = ""
    if sp3.get("rank"):
        sp_row = table(["Language", "Rank", "Overall score", "Measured share"],
                       [["Spid3r (subject language)", sp3.get("rank"),
                         "%.1f / 100" % sp3.get("score")
                         if sp3.get("score") is not None else "n/a",
                         "%s%%" % fmt(sp3.get("measured_share"), 0)]])
        if sp3.get("note"):
            sp_row += '<p class="note">%s</p>' % e(sp3["note"])
    art = su.get("artifacts") or {}
    link = ""
    if art.get("html"):
        link = ('<p><a href="/runs/%s/%s">Full suitability report '
                '&#8599;</a></p>' % (e(su.get("run_id", "")), e(art["html"])))
    return section(
        "16. Technology suitability assessment",
        table(["#", "Language", "Overall score", "Measured share"], rows)
        + sp_row + link,
        "Compact digest — the full per-criterion evidence is not embedded "
        "because this report has no suitability.json beside it.")


def _strengths_weaknesses(results):
    """Measured-only strengths / weaknesses / limitations lists.

    Every bullet is derived from a metric of THIS run and quotes it; when a
    claim has no supporting metric it is listed as a limitation instead.
    """
    a = results.get("adjudication") or {}
    s = results.get("summary") or {}
    r = results.get("resources") or {}
    lat = s.get("PRIMARY_successful_200_only") or {}
    slo_map = {x.get("id"): x for x in a.get("slo") or []}

    strengths, weaknesses = [], []

    def slo_ok(sid):
        x = slo_map.get(sid) or {}
        return x.get("grade") == "PASS"

    if slo_ok("S1"):
        strengths.append("Error discipline: %.4f%% of %s requests failed the "
                         "S1 definition (5xx/transport), inside the target "
                         "%.2f%%." % (s.get("s1_error_pct", 0),
                                      "{:,}".format(s.get("total_requests", 0)),
                                      (results.get("slo_targets") or {})
                                      .get("max_error_pct", 0)))
    if slo_ok("S2"):
        strengths.append("p95 latency %.1f ms (successful 200s) is inside the "
                         "%.0f ms target." % (lat.get("p95", 0),
                                              (results.get("slo_targets") or {})
                                              .get("max_p95_ms", 0)))
    if slo_ok("S7"):
        strengths.append("Availability probe held %s%% success across %d "
                         "probes." % (fmt(r.get("health_success_pct"), 2),
                                      r.get("health_probes") or 0))
    gen_peak = r.get("gen_cpu_peak_pct")
    if gen_peak is not None and gen_peak < 70:
        strengths.append("Load generator stayed clear of saturation (peak "
                         "%.1f%% of one core), so client-side scheduling lag "
                         "is unlikely to have inflated the percentiles."
                         % gen_peak)
    scal = results.get("scalability") or {}
    if scal.get("classification") in ("linear", "sublinear"):
        strengths.append("Throughput scaled %s across measured concurrency "
                         "levels; best observed %s RPS at C=%s."
                         % (scal["classification"], scal.get("best_rps"),
                            scal.get("best_throughput_concurrency")))

    for sid, text in (("S1", "error rate"), ("S2", "p95 latency"),
                      ("S3", "p99 latency"), ("S4", "sustained throughput"),
                      ("S7", "availability")):
        x = slo_map.get(sid) or {}
        if x.get("grade") == "FAIL":
            weaknesses.append("%s FAILED: %s" % (sid, x.get("note", "")))
        elif x.get("grade") == "WARN":
            weaknesses.append("%s WARN: %s" % (sid, x.get("note", "")))
    gen_mean = r.get("gen_cpu_mean_pct")
    if gen_mean is not None and gen_mean >= 60:
        weaknesses.append("Load generator averaged %.1f%% of one core - the "
                          "client may have been the bottleneck for part of "
                          "the schedule." % gen_mean)
    dsp = (s.get("dispatch_delay_ms") or {}).get("p95")
    if dsp is not None and dsp >= 10:
        weaknesses.append("Dispatch delay p95 reached %.1f ms - part of the "
                          "observed latency is generator scheduling, not "
                          "server time (see as-scheduled percentiles)." % dsp)
    if scal.get("classification") in ("plateau", "degraded"):
        weaknesses.append("Throughput %s at higher concurrency (knee near "
                          "C=%s)."
                          % (scal["classification"],
                             scal.get("knee_concurrency") or "n/a"))

    limitations = [
        "Client-only vantage: no server-side CPU/memory/PSI/OOM evidence "
        "exists for this run (S5/S6/S8 graded N/A).",
        "Closed-loop generator with keep-alive GETs only; authenticated "
        "flows, writes and browser rendering are out of scope.",
        "Single run = single sample; repeat runs (campaigns) before ranking "
        "any two systems.",
        "Target and generator share physical cores; full isolation was not "
        "enforced.",
    ]
    html_out = []
    for title, items, color in (("Strengths (measured)", strengths, C["green"]),
                                ("Weaknesses (measured)", weaknesses, C["amber"]),
                                ("Limitations", limitations, C["slate"])):
        if not items:
            items = ["No measured evidence qualified for this category in "
                     "this run."]
        html_out.append('<h3 style="color:%s">%s</h3><ul>%s</ul>'
                        % (color, e(title), "".join("<li>%s</li>" % e(x)
                                                    for x in items)))
    return "".join(html_out)


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
                    % (e(rc.get("runtime_environment")),
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
    return section("15. Language runtime comparison (compact digest)", "".join(body),
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
    P.append("<p>Target <strong>%s</strong> &nbsp;|&nbsp; Vantage "
             "<strong>client-only (host)</strong> &nbsp;|&nbsp; Profile "
             "<strong>%s</strong></p>"
             % (e(meta["target_url"]), e(meta["profile"])))
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

    # ---------------------------------------------------------- methodology --
    meth_rows = [
        ["Load model", "Closed-loop fixed worker pool; active concurrency "
                       "driven by the phase schedule (idle -> warm-up -> "
                       "ramp -> sustained -> spike -> soak -> cooldown -> "
                       "re-probe)."],
        ["Vantage point", "Client-only: latency/throughput/error-rate are "
                          "real observations; server-side CPU/memory are "
                          "NOT visible and are never estimated."],
        ["Timing", "Per-request connect / TTFB / full-body split on a "
                   "nanosecond monotonic clock; dispatch delay and "
                   "as-scheduled (coordinated-omission) latency published."],
        ["Fairness", "identical_workload=%s, seed=%s, aggregate=%s, "
                     "warmup=%ss, cooldown between repeats=%ss"
         % (cfg.get("fairness", {}).get("identical_workload"),
            cfg.get("fairness", {}).get("seed"),
            cfg.get("fairness", {}).get("aggregate"),
            cfg.get("fairness", {}).get("warmup_s"),
            cfg.get("fairness", {}).get("cooldown_between_repeats_s"))],
        ["Evaluation criteria", "SLOs S1-S9 below: error rate, p95/p99 "
                                "latency, sustained throughput, bounded "
                                "memory growth, memory release, availability "
                                "probe success, no OOM/restart, full schedule "
                                "completed. Memory/OOM SLOs are graded N/A in "
                                "client-only runs (insufficient evidence)."],
        ["Reproducibility", "config.json, preflight.json and results.json are "
                            "written beside this report with timestamps, "
                            "software versions and every parameter."],
    ]
    P.append(section("2. Methodology and evaluation criteria",
                     table(["Aspect", "Method"], meth_rows),
                     "The same schedule, fairness parameters and grading "
                     "apply to every run, so two runs of the same profile are "
                     "directly comparable when generated on the same host."))

    # --------------------------------------------------------------- KPIs --
    k = "".join([
        kpi("Requests", "{:,}".format(s["total_requests"]),
            "%.2f RPS overall" % s["overall_rps"]),
        kpi("Error rate", "%.4f%%" % s["s1_error_pct"],
            "%d failures / %d connect errors" % (s["s1_errors"], s["connect_errors"]),
            C["red"] if s["s1_error_pct"] > cfg["slo"]["max_error_pct"] else C["green"]),
        kpi("p95 latency", "%.1f ms" % lat["p95"], "p50 %.1f ms" % lat["p50"]),
        kpi("p99 latency", "%.1f ms" % lat["p99"], "max %.1f ms" % lat["max"]),
        kpi("Generator CPU peak", "%s%%" % fmt(r.get("gen_cpu_peak_pct"), 1),
            "mean %s%% (of one core; net of idle %s%%)"
            % (fmt(r.get("gen_cpu_mean_pct"), 1),
               fmt(r.get("gen_cpu_peak_pct_net"), 1))),
        kpi("Generator RSS", "%s MiB" % fmt(r.get("gen_rss_peak_mib"), 1),
            "net change %s MiB" % fmt(r.get("gen_rss_net_change_mib"), 1)),
        kpi("Health probes", "%d" % (r.get("health_probes") or 0),
            "%s%% success" % fmt(r.get("health_success_pct"), 1)),
        kpi("Health score", "%d / 100" % a["health_score"],
            a["health_score_formula"][:44], vcolor),
    ])
    P.append(section("3. Key measurements", '<div class="kpis">%s</div>' % k))

    # ---------------------------------------------------------- SLO table --
    slo_rows = [[x["id"], x["name"], x["target"], x["measured"],
                 grade_span(x["grade"]), x["note"]] for x in a["slo"]]
    P.append(section("4. SLO scorecard",
                     table(["ID", "SLO", "Target", "Measured", "Grade", "Note"],
                           slo_rows),
                     "Overall grade = min(all graded SLOs). Any FAIL => NO-GO. "
                     "N/A rows are explicitly evidence-gated."))

    # ------------------------------------------------------------- charts --
    tl = results["timeline"]
    xmax = max([x["t"] + 10 for x in tl] or [s["duration_s"]])
    gen = results["series"].get("generator_series") or []
    charts = []

    charts.append(("Throughput over time",
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
    if gen:
        gen_cpu = [(x["t"], x["cpu_pct"]) for x in gen
                   if x.get("cpu_pct") is not None]
        gen_rss = [(x["t"], x["rss_mib"]) for x in gen
                   if x.get("rss_mib") is not None]
        if gen_cpu:
            charts.append(("Load-generator CPU (measured client-side, "
                           "% of one core)",
                           line_chart([{"name": "gen CPU %",
                                        "color": C["violet"],
                                        "points": gen_cpu}],
                                      xmax, 0,
                                      max([v for _, v in gen_cpu]) * 1.15,
                                      "CPU", "(%)", 230)))
        if gen_rss:
            charts.append(("Load-generator working set (measured client-side)",
                           line_chart([{"name": "gen RSS", "color": C["teal"],
                                        "points": gen_rss}],
                                      xmax, 0,
                                      max([v for _, v in gen_rss]) * 1.15,
                                      "Memory", "(MiB)", 230)))
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
    P.append(section("5. Time-series evidence", ch,
                     "Inline SVG only; no scripts or external resources. "
                     "Series are downsampled to at most 900 points."))

    # -------------------------------------------------------- scalability --
    scal = results.get("scalability") or {}
    if (scal.get("points")):
        pts = scal["points"]
        scal_rows = [[p["concurrency"], "{:,}".format(p["requests"]),
                      fmt(p["rps"], 1), fmt(p["efficiency_rps_per_worker"], 2),
                      fmt(p["p50_ms"], 1), fmt(p["p95_ms"], 1),
                      fmt(p["p99_ms"], 1), fmt(p["error_pct"], 3, "%")]
                     for p in pts]
        rps_pts = [(p["concurrency"], p["rps"]) for p in pts]
        p95_pts = [(p["concurrency"], p["p95_ms"]) for p in pts
                   if p.get("p95_ms") is not None]
        xmax_c = max([p["concurrency"] for p in pts] or [1])
        scal_charts = '<div class="chart">%s</div><div class="chart">%s</div>' % (
            line_chart([{"name": "RPS at concurrency C", "color": C["blue"],
                         "points": rps_pts}],
                       xmax_c, 0, max([v for _, v in rps_pts] or [1]) * 1.15,
                       "Throughput vs concurrency", "(req/s)", 250),
            line_chart([{"name": "p95 vs concurrency", "color": C["amber"],
                         "points": p95_pts}],
                       xmax_c, 0, max([v for _, v in p95_pts] or [1]) * 1.15,
                       "p95 latency vs concurrency", "(ms)", 250))
        knee_note = ""
        if scal.get("knee_concurrency"):
            knee_note = ('<div class="note">Measured knee: throughput stops '
                         'earning its load around <strong>C=%s</strong> (last '
                         'level before marginal gain collapses or p95 inflates '
                         'beyond 2x the best level). Best throughput observed '
                         'at C=%s.</div>'
                         % (scal["knee_concurrency"],
                            scal.get("best_throughput_concurrency")))
        P.append(section("6. Scalability and concurrency analysis",
                         scal_charts
                         + table(["C", "Reqs", "RPS", "RPS/worker",
                                  "p50 ms", "p95 ms", "p99 ms", "Err %"],
                                 scal_rows)
                         + knee_note,
                         "Classification: %s. Every point is a measured "
                         "concurrency window (per-step in ramp/spike phases, "
                         "per-phase otherwise)."
                         % e(scal.get("classification", "unavailable"))))
    else:
        P.append(section("6. Scalability and concurrency analysis",
                         '<div class="note">Insufficient measured evidence: '
                         'this profile produced no multi-level concurrency '
                         'windows with requests.</div>',
                         "Profiles with ramp/spike step schedules populate "
                         "this section automatically."))

    # ------------------------------------------------------- phase table --
    ph_rows = [[p["name"], p["label"], fmt(p["start_s"], 1),
                fmt(p["end_s"], 1), p["concurrency"],
                "{:,}".format(p["requests"]), fmt(p["rps"], 1),
                fmt(p["latency_200"]["p95"], 1),
                fmt(p["latency_200"]["p99"], 1),
                fmt(p["error_pct"], 3, "%"),
                fmt(p["bytes_mib"], 1)]
               for p in results["phases"]]
    P.append(section("7. Phase breakdown",
                     table(["Phase", "Label", "Start s", "End s", "C",
                            "Reqs", "RPS", "p95 ms", "p99 ms", "Err %", "MiB"],
                           ph_rows)))

    pa_rows = [[p["path"], "{:,}".format(p["requests"]),
                fmt(p["latency"]["p50"], 1), fmt(p["latency"]["p95"], 1),
                fmt(p["latency"]["p99"], 1), fmt(p["error_pct"], 3, "%"),
                ", ".join("%s:%s" % (k, v) for k, v in
                          list(p["status_counts"].items())[:6])]
               for p in results["paths"]]
    P.append(section("8. Route breakdown",
                     table(["Path", "Reqs", "p50 ms", "p95 ms", "p99 ms",
                            "Err %", "Status counts"], pa_rows),
                     "Weighted GET route mix as configured in the interface."))

    # ----------------------------------------------------------- resources --
    res_rows = [
        ["Vantage", "client-only: the numbers below describe the LOAD "
                    "GENERATOR (measured) - the target's server-side "
                    "resources are not visible from here and are never "
                    "estimated"],
        ["Canonical server memory metric", r.get("canonical_metric")],
        ["Health probes / generator samples",
         "%d / %d" % (r.get("health_probes") or 0,
                      r.get("generator_samples") or 0)],
        ["Server-side anon/CPU/PSI/OOM evidence",
         "unavailable in client-only runs (S5/S6/S8 graded N/A - "
         "insufficient evidence, never guessed)"],
        ["Generator CPU peak / mean (% of one core)",
         "%s / %s (net of idle: %s)"
         % (fmt(r.get("gen_cpu_peak_pct"), 2),
            fmt(r.get("gen_cpu_mean_pct"), 2),
            fmt(r.get("gen_cpu_peak_pct_net"), 2))],
        ["Generator RSS start / peak / end (MiB)",
         "%s / %s / %s" % (fmt(r.get("gen_rss_start_mib"), 1),
                           fmt(r.get("gen_rss_peak_mib"), 1),
                           fmt(r.get("gen_rss_end_mib"), 1))],
        ["Generator RSS net change (MiB)",
         fmt(r.get("gen_rss_net_change_mib"), 1)],
        ["Host free RAM min (GB)", fmt(r.get("host_free_gb_min"), 2)],
    ]
    P.append(section("9. Resource and stability evidence",
                     table(["Metric", "Value"], res_rows)))

    # ------------------------------------------------- quality & accuracy --
    q = results.get("quality") or {}
    if q.get("checks"):
        q_rows = [[c.get("name"), {"ok": badge("OK", "ok"),
                                   "warn": badge("WARN", "warn"),
                                   "fail": badge("FAIL", "bad"),
                                   "insufficient_evidence":
                                       badge("N/A", "na"),
                                 }.get(c.get("status"), badge("?", "na")),
                   c.get("detail")]
                  for c in q["checks"]]
        q_body = table(["Check", "Status", "Detail"], q_rows)
    else:
        q_body = '<div class="note">No quality checks recorded.</div>'
    acc_notes = (
        '<p class="note"><strong>Accuracy considerations and known sources '
        'of error:</strong> (1) closed-loop load can mask server stalls '
        '(coordinated omission) - the as-scheduled percentiles quantify it; '
        '(2) generator scheduling lag is measured separately as '
        'dispatch_delay_ms and must not be blamed on the target; (3) the '
        'target and the generator share physical cores, so generator '
        'saturation would inflate latency - the generator CPU series above '
        'is the control; (4) 10 s timeline bins and ~1 Hz resource sampling '
        'bound the time resolution; (5) a single run is one sample - repeat '
        'runs and compare medians (fairness.repeats in campaigns). All '
        'values in this report are measured observations or explicitly '
        'labelled N/A - nothing is estimated.</p>')
    P.append(section("10. Data quality and accuracy considerations",
                     q_body + acc_notes,
                     "Quality checks are computed from the raw evidence of "
                     "THIS run."))

    # ------------------------------------- strengths / weaknesses / limits --
    sw = _strengths_weaknesses(results)
    P.append(section("11. Strengths, weaknesses and limitations",
                     sw, "Every statement cites a measured metric from this "
                         "run; no qualitative claim without a number."))

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
    P.append(section("12. Severity-ranked findings",
                     "".join(fhtml) or '<div class="note">No findings.</div>',
                     "%d findings; health formula: %s"
                     % (len(a["findings"]), a["health_score_formula"])))

    # ---------------------------------------------------------- conditions --
    cond = "<ol>" + "".join("<li>%s</li>" % e(x)
                            for x in a["go_conditions"]) + "</ol>"
    P.append(section("13. Go / No-Go conditions", cond))

    # ------------------------------------------------------- config & notes --
    conf_rows = [
        ["Target URL", meta["target_url"]],
        ["Profile / mode", "%s / %s" % (meta["profile"], cfg["mode"])],
        ["Benchmark mode / visibility",
         "%s / %s" % (results.get("benchmark_mode"),
                      results.get("visibility"))],
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
    fp = (env.get("fingerprint") or {})
    env_rows = [[str(k), str(v)] for k, v in env.items()
                if k != "fingerprint"]
    env_rows.append(["generator fingerprint",
                     fp.get("generator_fingerprint") or "unrecorded"])
    client_note = ""
    if results.get("visibility") == "client_only_same_host":
        client_note = ('<div class="note"><strong>Single-host client-side '
                       'run.</strong> The target answers on loopback, so '
                       'latency/throughput/error-rate are real end-to-end '
                       'observations under controlled conditions (same '
                       'machine, same profile). Server-side CPU/memory remain '
                       'invisible; two such runs may be ranked against each '
                       'other ONLY with the same profile and generator '
                       'fingerprint, and the comparison is a client-side '
                       'view - never a server-resource benchmark.</div>')
    else:
        client_note = ('<div class="note"><strong>Client-only run against a '
                       'remote target.</strong> Network conditions cannot be '
                       'held constant; this run is not comparable with other '
                       'runs and carries no server-side evidence.</div>')
    P.append(section("14. Configuration, environment and limitations",
                     table(["Setting", "Value"], conf_rows)
                     + (table(["Environment", "Value"], env_rows) if env_rows else "")
                     + "<h3>Disclosures</h3>" + disc
                     + client_note
                     + '<div class="note">%s</div>' % e(a["adjudication_note"]),
                     "Generated %s by Heavy-Load Checker %s."
                     % (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        meta["version"])))

    # ------------------------------------------------------------- abstract --
    # Journal-style abstract, generated strictly from this run's measured
    # data (no estimation, no invented claims).
    from . import journal as J
    rc_full = _load_sibling(out_path, "runtime_comparison.json")
    su_full = _load_sibling(out_path, "suitability.json")
    try:
        P.append(section("Abstract (auto-generated from this run's data)",
                         J.abstract(results, rc_full, su_full),
                         "Written for three audiences at once: the tables "
                         "and raw JSON serve data scientists, the per-"
                         "workload discussion serves programmers, the "
                         "figures serve every reader."))
    except Exception:
        pass

    # ------------------------------------------------ runtime comparison --
    # ONE self-contained report: the full runtime-comparison evidence and the
    # full suitability assessment are embedded here — no separate pages are
    # generated for a run. When the full JSON artifacts are absent (e.g. a
    # legacy/foreign results document), fall back to the compact digests.
    from . import runtime_comparison as RC
    from . import suitability as SU

    if rc_full and rc_full.get("workloads"):
        try:
            figs, _ = J.results_figures(rc_full, start=1)
        except Exception:
            figs = ""
        try:
            h2h, _ = J.head_to_head(rc_full)
        except Exception:
            h2h = ""
        body15 = ('<p class="sub">Journal-style figures with numbered '
                  'captions, a colour-coded head-to-head heatmap and '
                  'side-by-side tables — every value checksum-verified. '
                  'The detailed per-workload tables follow in the next '
                  'card.</p>')
        P.append(section("15. Language runtime comparison — figures and "
                         "side-by-side",
                         body15 + '<div class="rc-content">%s%s%s</div>'
                         % (wrap_tables(figs),
                            '<div class="chart"></div>', wrap_tables(h2h)),
                         "Fig. numbering is sequential within this report. "
                         "Orange = Spid3r engines, blue = interpreted "
                         "baselines, grey = compiled references."))
        rc_frag = wrap_tables(
            RC.build_body(rc_full, include_page_header=False))
        P.append(section("16. Language runtime comparison — detailed tables",
                         '<div class="rc-content">%s</div>' % rc_frag,
                         "The tabular layer of section 15: every variant, "
                         "every repeat statistic, every warning."))
    else:
        rc = results.get("runtime_comparison")
        if rc:
            P.append(_runtime_section(rc, meta.get("run_id", "")))
        else:
            P.append(section(
                "15. Language runtime comparison",
                '<div class="note">The runtime comparison was disabled for '
                'this run (runtime_comparison.enabled = false). Run a full '
                'benchmark or <code>cli.py --runtime-comparison</code> to '
                'produce it. Nothing is estimated.</div>'))

    # -------------------------------------------------------- suitability --
    if su_full and su_full.get("ranked"):
        try:
            su_figs, _ = J.suitability_figures(su_full)
        except Exception:
            su_figs = ""
        su_frag = wrap_tables(SU.build_body(su_full))
        body17 = ('%s<p class="sub">Weighted technology ranking from the '
                  'measured evidence above plus explicitly-labelled '
                  'documented criteria. Includes the dedicated Spid3r '
                  'subject-language report with every engine variant.</p>'
                  % ('<div class="su-content">%s</div>' % su_figs))
        P.append(section("17. Technology suitability assessment",
                         body17 + '<div class="su-content">%s</div>'
                         % su_frag,
                         "Measured criteria are backed by this run's "
                         "checksum-verified data; documented criteria are "
                         "judgements, always labelled."))
    else:
        su = results.get("suitability")
        if su and su.get("status") == "ok":
            P.append(_suitability_digest(su))
        elif su:
            P.append(section(
                "17. Technology suitability assessment",
                '<div class="note">Suitability assessment unavailable: %s'
                '</div>' % e("; ".join(su.get("warnings") or []) or
                             su.get("status", "unknown"))))

    P.append("<footer>Heavy-Load Checker %s &nbsp;|&nbsp; single-file report, "
             "no JavaScript, no external resources &nbsp;|&nbsp; run %s</footer>"
             % (e(meta["version"]), e(meta["run_id"])))
    P.append("</div></body></html>")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(P))
    return out_path
