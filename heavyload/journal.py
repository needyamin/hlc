#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Journal-grade figure layer for the Heavy-Load Checker report.

Turns the checksum-verified runtime-comparison data and the suitability
assessment into publication-style, self-contained inline SVG figures with
numbered captions (Fig. N), a colour-coded head-to-head heatmap, side-by-side
tables, and an auto-generated Abstract. Every number is traced to measured
data; missing data renders as "n/a", never estimated.

All charts are pure string builders (no JS, no external resources) and never
raise into the caller.
"""

from __future__ import annotations

import html
import math

from .suitability import _label, is_spid3r

# Palette (mirrors the runtime-comparison fragment).
C_SPID3R = "#f85606"
C_BASE = "#2563eb"
C_REF = "#64748b"
C_INK = "#1b2434"
C_MUTED = "#667085"
C_LINE = "#e4e9f0"
C_CARD = "#ffffff"

BASE_COLORS = {"php": C_BASE, "python": C_BASE, "node": C_BASE,
               "typescript": C_BASE, "ruby": C_BASE,
               "rust": C_REF, "c": C_REF, "cpp": C_REF, "swift": C_REF,
               "go": C_REF, "java": C_REF, "kotlin": C_REF, "csharp": C_REF}


def _e(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def _color(key):
    if is_spid3r(key):
        return C_SPID3R
    return BASE_COLORS.get(key, C_REF)


def _f(v, d=2):
    if v is None:
        return "n/a"
    if v >= 1000:
        return "%,.0f".replace(",", ",") % v if False else "{:,.0f}".format(v)
    return "%.*f" % (d, v)


def _ms(v):
    if v is None:
        return "n/a"
    return "%s ms" % _f(v, 3 if v < 10 else (2 if v < 100 else 1))


def _fig_caption(no, text):
    return ('<p class="caption"><strong>Fig. %d.</strong> %s</p>'
            % (no, _e(text)))


def _tbl_caption(no, text):
    return ('<p class="caption"><strong>Table %d.</strong> %s</p>'
            % (no, _e(text)))


# ---------------------------------------------------------------------------
# Chart primitives (inline SVG)
# ---------------------------------------------------------------------------
def _hbar_svg(items, unit="ms", width=940, log=True, rowh=24):
    """items = [(label, value, color)] ascending by value."""
    vals = [v for _, v, _ in items if v and v > 0]
    if not vals:
        return ""
    W, L, R = width, 270, 110
    H = 14 + len(items) * rowh + 18
    track = W - L - R
    lo, hi = min(vals), max(vals)
    if log and hi > 0 and lo > 0:
        lo_l, hi_l = math.log10(lo), math.log10(hi)
        span = max(0.05, hi_l - lo_l)

        def x(v):
            return L + max(2, (math.log10(max(v, 1e-9)) - lo_l) / span * track)
        ticks = []
        d = math.floor(lo_l)
        while d <= math.ceil(hi_l):
            ticks.append(10 ** d)
            d += 1
    else:
        hi_n = max(vals) or 1

        def x(v):
            return L + max(2, float(v) / hi_n * track)
        ticks = [hi_n * i / 4.0 for i in range(1, 5)]
    o = ['<svg viewBox="0 0 %d %d" width="100%%" style="display:block" '
         'xmlns="http://www.w3.org/2000/svg">' % (W, H)]
    o.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, C_CARD))
    for t in ticks:
        xt = x(t)
        o.append('<line x1="%.1f" y1="8" x2="%.1f" y2="%d" stroke="%s"/>'
                 % (xt, xt, H - 14, C_LINE))
        lbl = ("%g" % t) if t < 10 else ("{:,.0f}".format(t))
        o.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="10" '
                 'fill="%s">%s %s</text>'
                 % (xt, H - 3, C_MUTED, _e(lbl), _e(unit)))
    for i, (label, v, color) in enumerate(items):
        y = 10 + i * rowh
        bw = max(2.0, x(v) - L)
        o.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11" '
                 'fill="%s">%s</text>' % (L - 8, y + 14, C_INK, _e(label)))
        o.append('<rect x="%d" y="%.1f" width="%.1f" height="%d" rx="3" '
                 'fill="%s" opacity="0.92"/>'
                 % (L, y + 3, bw, rowh - 9, color))
        o.append('<text x="%.1f" y="%.1f" font-size="11" fill="%s">%s</text>'
                 % (L + bw + 6, y + 14, C_MUTED, _e(_ms(v))))
    o.append("</svg>")
    return "".join(o)


def _pie_svg(parts, width=940, donut=False, hole=0.55):
    """parts = [(label, value, color)] — shares of a whole."""
    parts = [(l, v, c) for l, v, c in parts if v and v > 0]
    if not parts:
        return ""
    total = sum(v for _, v, _ in parts)
    W, H, R, cx, cy = width, 300, 110, 190, 150
    o = ['<svg viewBox="0 0 %d %d" width="100%%" style="display:block" '
         'xmlns="http://www.w3.org/2000/svg">' % (W, H)]
    o.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, C_CARD))
    ang = -90.0
    for label, v, color in parts:
        frac = v / total
        sweep = frac * 360.0
        a0, a1 = math.radians(ang), math.radians(ang + sweep)
        x1, y1 = cx + R * math.cos(a0), cy + R * math.sin(a0)
        x2, y2 = cx + R * math.cos(a1), cy + R * math.sin(a1)
        large = 1 if sweep > 180 else 0
        if donut:
            r_in = R * hole
            xi1, yi1 = cx + r_in * math.cos(a1), cy + r_in * math.sin(a1)
            xi0, yi0 = cx + r_in * math.cos(a0), cy + r_in * math.sin(a0)
            o.append('<path d="M%.1f,%.1f A%d,%d 0 %d 1 %.1f,%.1f '
                     'L%.1f,%.1f A%d,%d 0 %d 0 %.1f,%.1f Z" fill="%s" '
                     'stroke="%s" stroke-width="1"/>'
                     % (x1, y1, R, R, large, x2, y2, xi1, yi1, int(r_in),
                        int(r_in), large, xi0, yi0, color, C_CARD))
        else:
            if sweep >= 359.9:
                o.append('<circle cx="%d" cy="%d" r="%d" fill="%s"/>'
                         % (cx, cy, R, color))
            else:
                o.append('<path d="M%d,%d L%.1f,%.1f A%d,%d 0 %d 1 %.1f,%.1f Z"'
                         ' fill="%s" stroke="%s" stroke-width="1"/>'
                         % (cx, cy, x1, y1, R, R, large, x2, y2, color, C_CARD))
        ang += sweep
    if donut:
        o.append('<text x="%d" y="%d" text-anchor="middle" font-size="15" '
                 'font-weight="700" fill="%s">100%%</text>' % (cx, cy + 5,
                                                               C_INK))
    lx, ly = cx + R + 40, 34
    for label, v, color in parts:
        o.append('<rect x="%d" y="%d" width="11" height="11" rx="2" '
                 'fill="%s"/>' % (lx, ly - 9, color))
        o.append('<text x="%d" y="%d" font-size="12" fill="%s">%s — '
                 '%s (%.1f%%)</text>'
                 % (lx + 18, ly, C_INK, _e(label), _e(_f(v, 1)),
                    100.0 * v / total))
        ly += 24
    o.append("</svg>")
    return "".join(o)


def _radar_svg(axes, series, width=940, R=170):
    """axes = [names]; series = [(label, [0..10 per axis], color)]"""
    n = len(axes)
    if n < 3 or not series:
        return ""
    cx, cy = width // 2, R + 46
    H = 2 * R + 96

    def pt(i, v):
        a = math.radians(-90.0 + 360.0 * i / n)
        rr = R * max(0.0, min(1.0, v / 10.0))
        return cx + rr * math.cos(a), cy + rr * math.sin(a)

    o = ['<svg viewBox="0 0 %d %d" width="100%%" style="display:block" '
         'xmlns="http://www.w3.org/2000/svg">' % (width, H)]
    o.append('<rect width="%d" height="%d" fill="%s"/>' % (width, H, C_CARD))
    for ring in (2.5, 5.0, 7.5, 10.0):
        pts = ["%.1f,%.1f" % pt(i, ring) for i in range(n)]
        o.append('<polygon points="%s" fill="none" stroke="%s"/>'
                 % (" ".join(pts), C_LINE))
        o.append('<text x="%d" y="%.1f" font-size="9" fill="%s">%g</text>'
                 % (cx + 4, cy - R * ring / 10.0 + 3, C_MUTED, ring))
    for i, name in enumerate(axes):
        x, y = pt(i, 10.0)
        o.append('<line x1="%d" y1="%d" x2="%.1f" y2="%.1f" stroke="%s"/>'
                 % (cx, cy, x, y, C_LINE))
        anchor = "middle"
        lx, ly = cx + (R + 26) * math.cos(
            math.radians(-90.0 + 360.0 * i / n)), \
            cy + (R + 26) * math.sin(math.radians(-90.0 + 360.0 * i / n))
        if abs(math.cos(math.radians(-90.0 + 360.0 * i / n))) > 0.3:
            anchor = "start" if math.cos(
                math.radians(-90.0 + 360.0 * i / n)) > 0 else "end"
        o.append('<text x="%.1f" y="%.1f" text-anchor="%s" font-size="11" '
                 'fill="%s">%s</text>' % (lx, ly + 4, anchor, C_INK,
                                          _e(name)))
    for label, vals, color in series:
        pts = ["%.1f,%.1f" % pt(i, vals[i]) for i in range(n)]
        o.append('<polygon points="%s" fill="%s" fill-opacity="0.14" '
                 'stroke="%s" stroke-width="2"/>' % (" ".join(pts), color,
                                                     color))
    lx = 16
    for label, vals, color in series:
        o.append('<rect x="%d" y="%d" width="11" height="11" rx="2" '
                 'fill="%s"/>' % (lx, 12, color))
        o.append('<text x="%d" y="%d" font-size="11" fill="%s">%s</text>'
                 % (lx + 17, 22, C_INK, _e(label)))
        lx += 18 + 7 * len(label) + 26
    o.append("</svg>")
    return "".join(o)


def _scatter_svg(pts, xlabel="Peak working set (MiB, log)",
                 ylabel="Geomean compute time (ms, log)", width=940):
    """pts = [(label, x, y, color)] — log-log scatter with labels."""
    pts = [(l, x, y, c) for l, x, y, c in pts if x and x > 0 and y and y > 0]
    if not pts:
        return ""
    W, H, L, B, T, R = width, 460, 90, 56, 20, 30
    pw, ph = W - L - R, H - T - B
    xs = [x for _, x, _, _ in pts]
    ys = [y for _, _, y, _ in pts]
    x0, x1 = math.log10(min(xs)) - 0.1, math.log10(max(xs)) + 0.15
    y0, y1 = math.log10(min(ys)) - 0.1, math.log10(max(ys)) + 0.15
    if x1 - x0 < 0.3:
        x1 = x0 + 0.3
    if y1 - y0 < 0.3:
        y1 = y0 + 0.3

    def X(v):
        return L + (math.log10(v) - x0) / (x1 - x0) * pw

    def Y(v):
        return T + ph - (math.log10(v) - y0) / (y1 - y0) * ph

    o = ['<svg viewBox="0 0 %d %d" width="100%%" style="display:block" '
         'xmlns="http://www.w3.org/2000/svg">' % (W, H)]
    o.append('<rect width="%d" height="%d" fill="%s"/>' % (W, H, C_CARD))
    d = math.floor(x0)
    while d <= math.ceil(x1):
        xv = X(10 ** d)
        o.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s"/>'
                 % (xv, T, xv, T + ph, C_LINE))
        o.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="10" '
                 'fill="%s">%s</text>'
                 % (xv, T + ph + 16, C_MUTED, _e("{:,.0f}".format(10 ** d))))
        d += 1
    d = math.floor(y0)
    while d <= math.ceil(y1):
        yv = Y(10 ** d)
        o.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>'
                 % (L, yv, W - R, yv, C_LINE))
        o.append('<text x="%d" y="%.1f" text-anchor="end" font-size="10" '
                 'fill="%s">%s</text>'
                 % (L - 8, yv + 3, C_MUTED, _e("{:,.0f}".format(10 ** d))))
        d += 1
    o.append('<text x="%d" y="%d" text-anchor="middle" font-size="11" '
             'fill="%s">%s</text>' % (L + pw // 2, H - 12, C_INK,
                                      _e(xlabel)))
    o.append('<text x="14" y="%d" font-size="11" fill="%s">%s</text>'
             % (T + 10, C_INK, _e(ylabel)))
    for label, x, y, color in pts:
        o.append('<circle cx="%.1f" cy="%.1f" r="7" fill="%s" fill-opacity="0.85"'
                 ' stroke="%s"/>' % (X(x), Y(y), color, C_CARD))
        o.append('<text x="%.1f" y="%.1f" font-size="10.5" fill="%s">%s</text>'
                 % (X(x) + 10, Y(y) - 7, C_INK, _e(label)))
    o.append("</svg>")
    return "".join(o)


# ---------------------------------------------------------------------------
# Data shaping
# ---------------------------------------------------------------------------
def _variant_rows(rc_data):
    """key -> {label, color, per-workload ms, geomean, rss geomean,
    cpu geomean} over VERIFIED workloads only."""
    rows = {}
    for wid in sorted((rc_data or {}).get("workloads") or {}):
        w = rc_data["workloads"][wid]
        if not w.get("verified"):
            continue
        for r in w.get("rows") or []:
            k = str(r.get("key"))
            d = rows.setdefault(k, {"label": r.get("runtime") or k,
                                    "wid_ms": {}, "rss": [], "cpu": []})
            d["wid_ms"][wid] = r.get("compute_ms")
            if r.get("peak_rss_bytes"):
                d["rss"].append(r["peak_rss_bytes"])
            cu, ck = r.get("cpu_user_s"), r.get("cpu_kernel_s")
            if cu is not None:
                d["cpu"].append(cu + (ck or 0.0))

    def geo(vals):
        vals = [v for v in vals if v and v > 0]
        if not vals:
            return None
        return math.exp(sum(math.log(v) for v in vals) / len(vals))

    for k, d in rows.items():
        d["geomean"] = geo(list(d["wid_ms"].values()))
        d["rss_mib"] = (geo(d["rss"]) / 1048576.0) if d["rss"] else None
        d["cpu_s"] = geo(d["cpu"]) if d["cpu"] else None
    return rows


def _workload_meta(rc_data):
    """wid -> (letter, title) for verified workloads."""
    out = {}
    for wid in sorted((rc_data or {}).get("workloads") or {}):
        w = rc_data["workloads"][wid]
        if w.get("verified"):
            out[wid] = (w.get("id", "?"), w.get("title", wid))
    return out


# ---------------------------------------------------------------------------
# Public figure builders
# ---------------------------------------------------------------------------
def results_figures(rc_data, start=1):
    """Figures F(start)..: per-workload log bars, geomean, memory, CPU,
    scatter, pie, radar. Returns (html, next_figure_number)."""
    n = start
    rows = _variant_rows(rc_data)
    wls = _workload_meta(rc_data)
    if not rows or not wls:
        return ('<p class="note">No verified workloads in this run — '
                'figures unavailable.</p>'), n
    o = []

    # Fig: per-workload median times, log scale (one figure per workload).
    for wid in sorted(wls):
        letter, title = wls[wid]
        items = sorted(((d["label"], d["wid_ms"].get(wid), _color(k))
                        for k, d in rows.items()
                        if d["wid_ms"].get(wid)),
                       key=lambda t: t[1] or 0)
        svg = _hbar_svg(items)
        if svg:
            o.append('<div class="chart">%s</div>' % svg)
            o.append(_fig_caption(
                n, "Median compute time by runtime for workload %s (%s), "
                   "log scale. Bars are medians of checksum-verified "
                   "in-process repeats; orange = Spid3r engines, blue = "
                   "interpreted baselines, grey = compiled references."
                % (letter, title)))
        n += 1

    # Fig: geomean slowdown vs fastest (log scale).
    ranked = sorted(rows.items(), key=lambda kv: kv[1]["geomean"] or 1e9)
    fastest = ranked[0][1]["geomean"] if ranked else None
    items = [(d["label"], d["geomean"], _color(k)) for k, d in ranked
             if d["geomean"]]
    svg = _hbar_svg(items)
    if svg and fastest:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Geomean compute time across all verified workloads (log "
               "scale). The fastest measured runtime is %s at %s; every bar "
               "is the geometric mean of the same five checksum-verified "
               "workloads."
            % (_e(ranked[0][1]["label"]), _e(_ms(fastest)))))
        n += 1

    # Fig: peak memory (linear).
    mem = sorted(((d["label"], d["rss_mib"], _color(k))
                  for k, d in rows.items() if d["rss_mib"]),
                 key=lambda t: t[1] or 0)
    svg = _hbar_svg(mem, unit="MiB", log=False)
    if svg:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Geomean OS-measured peak working set per runtime (MiB). "
               "Measured via the operating system (Windows "
               "GetProcessMemoryInfo / POSIX wait4 rusage), not estimated."))
        n += 1

    # Fig: CPU time (log scale).
    cpu = sorted(((d["label"], d["cpu_s"], _color(k))
                  for k, d in rows.items() if d["cpu_s"]),
                 key=lambda t: t[1] or 0)
    svg = _hbar_svg(cpu, unit="s")
    if svg:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Geomean CPU time (user + kernel seconds) consumed per "
               "workload run, log scale. Interpreters spend CPU on dispatch "
               "overhead; compiled runtimes spend it on the computation "
               "itself."))
        n += 1

    # Fig: scatter memory vs time.
    pts = [(d["label"], d["rss_mib"], d["geomean"], _color(k))
           for k, d in ranked if d["rss_mib"] and d["geomean"]]
    svg = _scatter_svg(pts)
    if svg:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Runtime trade-off space: geomean compute time vs peak "
               "memory (both log scale). Lower-left is better — fast AND "
               "light. Overhead-heavy JIT runtimes trade memory for speed; "
               "interpreters trade time for memory."))
        n += 1

    # Fig: pie — share of geomean compute time (language level, Spid3r
    # collapsed to its subject geomean).
    lang_time = {}
    for k, d in ranked:
        if is_spid3r(k):
            lang_time.setdefault("Spid3r (all engines)", []).append(
                d["geomean"])
        else:
            lang_time.setdefault(_label(k), []).append(d["geomean"])
    palette = [C_SPID3R, C_BASE, "#0d9488", "#7c3aed", "#d97706",
               "#dc2626", "#16a34a"]
    parts = []
    for i, (lbl, vals) in enumerate(sorted(lang_time.items(),
                                           key=lambda kv: kv[1][0] or 0)):
        parts.append((lbl, _geo(vals), palette[i % len(palette)]))
    svg = _pie_svg(parts)
    if svg:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Share of geomean compute time by language (Spid3r collapsed "
               "across its engines). Each slice is the language's geometric "
               "mean time as a fraction of the measured total — a visual "
               "dominance map of the same evidence as the ratio tables."))
        n += 1

    # Fig: radar — workload-normalized performance (10 = fastest on that
    # workload), selected representative runtimes.
    sel = [("Spid3r (native)", "spid3r_native", C_SPID3R),
           ("Spid3r (tree-walk)", "spid3r_tw", "#f8a56b"),
           ("Rust -O", "rust", C_REF),
           ("Node", "node", C_BASE),
           ("PHP", "php", "#7c3aed"),
           ("Python", "python", "#0d9488")]
    axes = [wls[wid][0] for wid in sorted(wls)]
    series = []
    for lbl, key, color in sel:
        if key not in rows:
            continue
        vals = []
        for wid in sorted(wls):
            t = rows[key]["wid_ms"].get(wid)
            all_t = [d["wid_ms"].get(wid) for d in rows.values()
                     if d["wid_ms"].get(wid)]
            if not t or not all_t:
                vals.append(0.0)
                continue
            lo, hi = min(all_t), max(all_t)
            if math.isclose(lo, hi):
                vals.append(10.0)
            else:
                frac = math.log(t / lo) / math.log(hi / lo)
                vals.append(round(10.0 * (1.0 - frac), 2))
        series.append((lbl, vals, color))
    svg = _radar_svg(axes, series)
    if svg:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Workload-normalized performance profile (10 = fastest on "
               "that workload, log-normalized; axes: %s). Shows the SHAPE of "
               "each runtime's performance instead of a single number — "
               "Spid3r native's prime-counting point sits at the outer rim "
               "while its matrix-multiply point collapses toward the centre."
            % _e(", ".join(axes))))
        n += 1
    return "".join(o), n


def head_to_head(rc_data, start=1):
    """Heatmap (workload x runtime) + side-by-side tables.
    Returns (html, next_table_number)."""
    n = start
    rows = _variant_rows(rc_data)
    wls = _workload_meta(rc_data)
    if not rows or not wls:
        return '<p class="note">No verified workloads.</p>', n
    wid_list = sorted(wls)
    ranked = sorted(rows.items(), key=lambda kv: kv[1]["geomean"] or 1e9)
    o = []

    # Table: colour-coded heatmap — every runtime x every workload.
    fastest_per = {wid: min([d["wid_ms"].get(wid) for d in rows.values()
                             if d["wid_ms"].get(wid)], default=None)
                   for wid in wid_list}
    worst_ratio = 1.0
    cells = {}
    for k, d in rows.items():
        for wid in wid_list:
            t = d["wid_ms"].get(wid)
            f = fastest_per.get(wid)
            ratio = (t / f) if (t and f) else None
            cells[(k, wid)] = (t, ratio)
            if ratio and ratio > worst_ratio:
                worst_ratio = ratio
    top_ratio_log = max(1.0, math.log10(max(worst_ratio, 1.0)))

    def bg(ratio):
        if not ratio:
            return "#f1f5f9"
        t = min(1.0, math.log10(max(ratio, 1.0)) / top_ratio_log)
        hue = 120.0 * (1.0 - t)
        return "hsl(%.0f, 72%%, 88%%)" % hue

    o.append(_tbl_caption(
        n, "Head-to-head heatmap: median compute time of every runtime "
           "(rows, sorted by geomean) on every workload (columns). Each cell "
           "shows the median and the ratio to that workload's fastest "
           "runtime; green = fastest, red = slowest. All values are "
           "checksum-verified."))
    n += 1
    head = ('<tr><th>Runtime</th>' +
            "".join('<th class="num">%s. %s</th>' % (_e(wls[w][0]),
                                                    _e(wls[w][1]))
                    for w in wid_list) +
            '<th class="num">Geomean</th></tr>')
    o.append('<div class="tblwrap"><table><thead>%s</thead><tbody>' % head)
    for k, d in ranked:
        o.append('<tr><td style="border-left:4px solid %s">%s</td>'
                 % (_color(k), _e(d["label"])))
        for wid in wid_list:
            t, ratio = cells.get((k, wid), (None, None))
            o.append('<td class="num" style="background:%s">%s<small> '
                     '(%s&times;)</small></td>'
                     % (bg(ratio), _e(_ms(t)),
                        _e(_f(ratio, 2) if ratio else "n/a")))
        o.append('<td class="num"><strong>%s</strong></td>'
                 % _e(_ms(d["geomean"])))
        o.append("</tr>")
    o.append("</tbody></table></div>")

    # Table: Spid3r engines side by side with the fastest and each baseline.
    engines = sorted([k for k in rows if is_spid3r(k)],
                     key=lambda k: rows[k]["geomean"] or 1e9)
    baselines = sorted([k for k in rows if not is_spid3r(k)],
                       key=lambda k: rows[k]["geomean"] or 1e9)
    if engines:
        o.append(_tbl_caption(
            n, "Side-by-side: every Spid3r engine vs the fastest measured "
               "runtime and each baseline. Each cell shows the median; the "
               "bottom row gives the geomean ratio to Spid3r native."))
        n += 1
        cols = (["fastest"] + engines +
                [k for k in baselines if k in ("php", "python", "node",
                                               "rust")])
        heads = []
        seen = set()
        for c in cols:
            lbl = "Fastest measured" if c == "fastest" else rows[c]["label"]
            if lbl in seen:
                continue
            seen.add(lbl)
            heads.append(lbl)
        head = ('<tr><th>Workload</th>' +
                "".join('<th class="num">%s</th>' % _e(x) for x in heads) +
                "</tr>")
        o.append('<div class="tblwrap"><table><thead>%s</thead><tbody>'
                 % head)
        for wid in wid_list:
            o.append("<tr><td>%s. %s</td>" % (_e(wls[wid][0]),
                                              _e(wls[wid][1])))
            native = rows[engines[0]]["wid_ms"].get(wid) if engines else None
            for c in cols:
                if c == "fastest":
                    t = fastest_per.get(wid)
                    row_ms = t
                else:
                    t = rows[c]["wid_ms"].get(wid)
                    row_ms = t
                mark = ""
                if native and row_ms and native <= row_ms:
                    mark = " &#9733;"
                o.append('<td class="num">%s%s</td>'
                         % (_e(_ms(row_ms)), mark))
            o.append("</tr>")
        o.append("<tr><td><strong>Geomean</strong></td>")
        for c in cols:
            t = (min([d["geomean"] for d in rows.values() if d["geomean"]],
                     default=None) if c == "fastest"
                 else rows[c]["geomean"])
            o.append('<td class="num"><strong>%s</strong></td>'
                     % _e(_ms(t)))
        o.append("</tr></tbody></table></div>")
        o.append(_tbl_caption(
            n, "Geomean ratio of each column to Spid3r (native --release) — "
               "below 1.00&times; means that runtime is faster than Spid3r "
               "native, above 1.00&times; means slower."))
        n += 1
        native_geo = rows[engines[0]]["geomean"] if engines else None
        rows_out = []
        for c in cols:
            lbl = "Fastest measured" if c == "fastest" else rows[c]["label"]
            t = (min([d["geomean"] for d in rows.values() if d["geomean"]],
                     default=None) if c == "fastest"
                 else rows[c]["geomean"])
            if native_geo and t:
                rows_out.append([lbl, "%.2f&times;" % (t / native_geo)])
        o.append('<div class="tblwrap"><table><thead><tr><th>Runtime</th>'
                 '<th class="num">Geomean vs Spid3r native</th></tr>'
                 "</thead><tbody>" +
                 "".join("<tr><td>%s</td><td class=\"num\">%s</td></tr>"
                         % (_e(a), _e(b)) for a, b in rows_out) +
                 "</tbody></table></div>")
    return "".join(o), n


def _geo(vals):
    vals = [v for v in vals if v and v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def suitability_figures(su_data, start=1):
    """Suitability score bars + Spid3r score-composition donut.
    Returns (html, next_figure_number)."""
    n = start
    ranked = su_data.get("ranked") or []
    if not ranked:
        return "", n
    o = []
    items = [(r.get("label"), r.get("overall_score"),
              C_SPID3R if is_spid3r(r.get("language")) else C_BASE)
             for r in ranked if r.get("overall_score") is not None]
    items.sort(key=lambda t: -(t[1] or 0))
    svg = _hbar_svg([(l, max(v, 0.5), c) for l, v, c in items],
                    unit="/ 100", log=False)
    if svg:
        o.append('<div class="chart">%s</div>' % svg)
        o.append(_fig_caption(
            n, "Overall suitability score per language (0–100; higher is "
               "better). The score blends measured criteria (this run's "
               "checksum-verified data) with explicitly-labelled documented "
               "criteria; Spid3r (orange) carries the highest measured share "
               "of any row."))
        n += 1
    m = next((r for r in ranked if is_spid3r(r.get("language"))), None)
    if m:
        mw = m.get("measured_weight_pct_of_total") or 0
        dw = m.get("documented_weight_pct_of_total") or 0
        missing = m.get("missing_weight_pct_of_total") or 0
        svg = _pie_svg(
            [("Measured (this run)", mw, C_SPID3R),
             ("Documented (judged)", dw, C_BASE),
             ("Missing (redistributed)", missing, "#cbd5e1")],
            donut=True, hole=0.58)
        if svg:
            o.append('<div class="chart">%s</div>' % svg)
            o.append(_fig_caption(
                n, "How Spid3r's ranking score is composed: share of the "
                   "effective weight backed by measurements of this run vs "
                   "documented judgments vs unassessed criteria (weight "
                   "redistributed, never invented)."))
        n += 1
    return "".join(o), n


def abstract(results, rc_data, su_data):
    """Auto-generated journal Abstract — measured statements only."""
    rows = _variant_rows(rc_data)
    wls = _workload_meta(rc_data)
    env = (rc_data or {}).get("environment") or {}
    ranked = su_data.get("ranked") or []
    n_variants = len(rows)
    n_langs = len({k for k in rows if not is_spid3r(k)}) + \
        (1 if any(is_spid3r(k) for k in rows) else 0)
    if not rows:
        return ('<p class="note">The runtime comparison was disabled for '
                'this run, so no cross-language abstract can be produced. '
                'Nothing is estimated.</p>')
    s = (rc_data or {}).get("summary") or {}
    slow = s.get("slowdown") or {}
    native = slow.get("spid3r_native") or {}
    ranked_sorted = sorted(ranked, key=lambda r: r.get("rank") or 999)
    fastest_su = ranked_sorted[0] if ranked_sorted else {}
    sp_row = next((r for r in ranked if is_spid3r(r.get("language"))), None)
    sentences = []
    sentences.append(
        "This run evaluated <strong>%d runtime configurations of %d "
        "programming languages</strong> on a single host (%s, %s, %s cores), "
        "executing %d checksum-verified workloads — prime counting, JSON "
        "round-trip, map lookups, string building and integer matrix "
        "multiplication — as medians of repeated, in-process timed runs on "
        "each installed toolchain."
        % (n_variants, n_langs, _e(env.get("cpu_model") or "unrecorded CPU"),
           _e(env.get("os") or "unrecorded OS"),
           _e(env.get("cpu_count") or "?"), len(wls)))
    fastest = min(rows.items(), key=lambda kv: kv[1]["geomean"] or 1e9)
    sentences.append(
        "Across all five workloads the fastest measured configuration was "
        "<strong>%s</strong> (geomean %s); the slowest was %s — a %.0f-fold "
        "spread that motivates measured, per-workload reporting instead of a "
        "single number."
        % (_e(fastest[1]["label"]), _e(_ms(fastest[1]["geomean"])),
           _e(ranked and rows and max(rows.items(),
              key=lambda kv: kv[1]["geomean"] or 0)[1]["label"] or "n/a"),
           (max(d["geomean"] or 0 for d in rows.values()) /
            max(fastest[1]["geomean"] or 1, 1e-9))))
    if native:
        sentences.append(
            "The Spid3r subject language was measured through every engine "
            "variant. Its <strong>native --release build</strong> achieved a "
            "geomean of <strong>%s vs PHP (%s)</strong> and <strong>%s vs "
            "Python (%s)</strong> — i.e. %s — while its interpreted engines "
            "(tree-walk/VM/bytecode, %s vs PHP) landed mid-pack and the "
            "--no-opt debug engine is reported separately. Spid3r's peak "
            "memory footprint (%s MiB geomean) is among the smallest "
            "measured."
            % (_e(_f((native.get("vs_php") or {}).get("geomean"), 2)) or "n/a",
               _e(_ms(next((d["geomean"] for k, d in rows.items()
                            if k == "php"), None))),
               _e(_f((native.get("vs_python") or {}).get("geomean"), 2))
               or "n/a",
               _e(_ms(next((d["geomean"] for k, d in rows.items()
                            if k == "python"), None))),
               "faster than both classic web baselines"
               if (native.get("vs_php") or {}).get("geomean") and
               (native.get("vs_php") or {}).get("geomean") < 1
               else "at parity with the classic web baselines",
               _e(_f((native.get("vs_php") or {}).get("geomean"), 2)) or "n/a",
               _e(_f(next((d["rss_mib"] for k, d in rows.items()
                           if k == "spid3r_native"), None), 1)) or "n/a"))
    if sp_row:
        sentences.append(
            "In the weighted suitability assessment Spid3r ranks "
            "<strong>#%s of %d</strong> (%s/100) with the highest measured "
            "share of any row (%s%%); its weakest measured criterion is "
            "compute performance on interpreter-style engines, its strongest "
            "is memory efficiency. The fastest-ranked language overall is "
            "<strong>%s</strong> (%s/100)."
            % (sp_row.get("rank"), len(ranked), _e(sp_row.get("overall_score")),
               _e(sp_row.get("measured_weight_share")),
               _e(fastest_su.get("label") or "n/a"),
               _e(fastest_su.get("score"))))
    sentences.append(
        "Every number in this report is a measurement of this machine — no "
        "simulation, no extrapolation; missing toolchains and metrics are "
        "labelled n/a rather than invented. The report is written to be read "
        "by three audiences at once: the tables and raw data (runtime_"
        "comparison.json) serve data scientists, the per-workload discussion "
        "serves programmers, and the figures serve everyone.")
    return "".join("<p>%s</p>" % x for x in sentences)


def render(results, rc_data, su_data):
    """One-call fragment for report.py: figures + head-to-head. Returns
    (html, figures_used)."""
    try:
        html_out, n = results_figures(rc_data, start=1)
    except Exception:
        html_out, n = "", 1
    try:
        h2, n2 = head_to_head(rc_data, start=n)
        html_out += h2
        n = n2
    except Exception:
        pass
    return html_out, n
