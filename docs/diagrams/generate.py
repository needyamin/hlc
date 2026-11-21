#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the project's core architecture diagrams as standalone SVGs.

    python docs/diagrams/generate.py

Why a generator instead of hand-edited SVG: every box, arrow and colour is
declared once here, so the six diagrams stay visually consistent and can be
updated when the code changes. Standard library only -- no pip install, no
Mermaid CLI, no network.

v2 reality reflected here: every run is a CLIENT-ONLY measurement from the
host (no container instrumentation of any kind), with a 14-language runtime
comparison, scalability analysis, data-quality checks and a technology
suitability assessment attached to each run.

Output rules (deliberate):
  * No <style>, <script>, <image>, external fonts, CDN or CSS @import.
    Every visual property is an inline attribute. This is what makes the SVGs
    render on GitHub (which sanitises SVG), in IDE previews and offline.
  * Light background + dark ink everywhere, so they are readable in a README
    and printable.
  * <title> and <desc> give each diagram an accessible name/description.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from xml.sax.saxutils import escape as X

HERE = Path(__file__).resolve().parent

FONT = "Segoe UI, Arial, Helvetica, sans-serif"
INK = "#16202c"
MUTED = "#5b6b7d"
RULE = "#d8e1ec"
PANEL = "#f8fafc"

# tone -> (light fill, strong stroke, dark text)
PAL = {
    "blue":   ("#e8f0ff", "#2563eb", "#12336b"),
    "teal":   ("#e3f6f2", "#0f8a7a", "#0b5c53"),
    "violet": ("#f0ebff", "#6d4bd8", "#432a91"),
    "amber":  ("#fff2dc", "#c07a12", "#7c4a05"),
    "green":  ("#e6f6ea", "#18803d", "#0d5427"),
    "red":    ("#fdeaea", "#c0392b", "#822018"),
    "gray":   ("#eff3f8", "#7a8899", "#3f4c5c"),
    "ink":    ("#ffffff", "#5b6b7d", "#16202c"),
}


class Canvas:
    """Tiny SVG builder: boxes, lanes, notes, arrows, chips, legends."""

    def __init__(self, w, h, title, subtitle):
        self.w, self.h = w, h
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-labelledby="ttl dsc">',
            f'<title id="ttl">{X(title)}</title>',
            f'<desc id="dsc">{X(subtitle)}</desc>',
            "<defs>",
        ]
        for tone, (_, stroke, _) in PAL.items():
            self.parts.append(
                f'<marker id="ar_{tone}" viewBox="0 0 10 10" refX="9" refY="5" '
                f'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{stroke}"/></marker>')
        self.parts.append("</defs>")
        self.parts.append(f'<rect width="{w}" height="{h}" fill="#ffffff"/>')
        self.parts.append(
            f'<text x="36" y="42" font-family="{FONT}" font-size="23" '
            f'font-weight="700" fill="{INK}">{X(title)}</text>')
        self.parts.append(
            f'<text x="36" y="67" font-family="{FONT}" font-size="13" '
            f'fill="{MUTED}">{X(subtitle)}</text>')
        self.parts.append(
            f'<line x1="36" y1="84" x2="{w - 36}" y2="84" '
            f'stroke="{RULE}" stroke-width="1.5"/>')

    # ---------------------------------------------------------------- primitives
    def rect(self, x, y, w, h, fill, stroke=PAL["gray"][1], sw=1.4,
             r=10, dash=False):
        d = ' stroke-dasharray="6 4"' if dash else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
        if r and r < h / 2:
            pass  # rx is capped by the renderer anyway

    def text(self, x, y, s, size=12, color=INK, weight=400, anchor="start",
             italic=False):
        it = ' font-style="italic"' if italic else ""
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}"'
            f'{it}>{X(s)}</text>')

    def box(self, x, y, w, h, label, tone="blue", sub=None, fs=13,
            subfs=10.5, dashed=False, align="center", r=10):
        fill, stroke, dark = PAL[tone]
        self.rect(x, y, w, h, fill, stroke, 1.6, r, dashed)
        lines = [(t, fs, 700, dark) for t in str(label).split("\n")]
        if sub:
            lines += [(t, subfs, 400, MUTED) for t in str(sub).split("\n")]
        heights = [size + 5 for _, size, _, _ in lines]
        cur = y + h / 2 - sum(heights) / 2
        tx = x + w / 2 if align == "center" else x + 14
        anchor = "middle" if align == "center" else "start"
        for (t, size, weight, color), lh in zip(lines, heights):
            self.text(f"{tx:.1f}", f"{cur + lh / 2 + size * 0.34:.1f}",
                      t, size, color, weight, anchor)
            cur += lh

    def lane(self, x, y, w, h, label, tone="gray"):
        fill, stroke, dark = PAL[tone]
        self.rect(x, y, w, h, PANEL, RULE, 1.3, 12)
        self.parts.append(
            f'<path d="M{x},{y + 32} L{x},{y + 12} Q{x},{y} {x + 12},{y} '
            f'L{x + w - 12},{y} Q{x + w},{y} {x + w},{y + 12} '
            f'L{x + w},{y + 32} Z" fill="{fill}"/>')
        self.parts.append(
            f'<line x1="{x}" y1="{y + 32}" x2="{x + w}" y2="{y + 32}" '
            f'stroke="{stroke}" stroke-width="1"/>')
        self.text(x + 14, y + 21, label, 11.5, dark, 700)

    def note(self, x, y, w, h, text, tone="amber", fs=11, title=None):
        fill, stroke, dark = PAL[tone]
        self.rect(x, y, w, h, fill, stroke, 1.1, 8)
        self.rect(x, y, 4, h, stroke, stroke, 0, 2)
        lines = ([("T", title)] if title else []) + [("N", t) for t in
                                                      str(text).split("\n")]
        cur = y + 12
        for kind, line in lines:
            if kind == "T":
                self.text(x + 14, cur + 11, line, fs, dark, 700)
                cur += fs + 7
            else:
                self.text(x + 14, cur + 10, line, fs, MUTED)
                cur += fs + 6

    def chip(self, x, y, w, h, label, tone="gray", fs=10.5):
        fill, stroke, dark = PAL[tone]
        self.rect(x, y, w, h, fill, stroke, 1.2, h / 2)
        self.text(x + w / 2, y + h / 2 + fs * 0.34, label, fs, dark, 700,
                  "middle")

    def arrow(self, points, tone="gray", label=None, lx=None, ly=None,
              dashed=False, width=2.0, fs=10.5):
        _, stroke, dark = PAL[tone]
        pts = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        d = ' stroke-dasharray="7 5"' if dashed else ""
        self.parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" '
            f'stroke-width="{width}" stroke-linejoin="round" '
            f'stroke-linecap="round" marker-end="url(#ar_{tone})"{d}/>')
        if label:
            self.text(lx if lx is not None else points[0][0],
                      ly if ly is not None else points[0][1] - 8,
                      label, fs, dark, 700, "middle")

    def diamond(self, cx, cy, w, h, label, tone="amber", fs=12):
        fill, stroke, dark = PAL[tone]
        pts = (f"{cx},{cy - h / 2} {cx + w / 2},{cy} "
               f"{cx},{cy + h / 2} {cx - w / 2},{cy}")
        self.parts.append(
            f'<polygon points="{pts}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="1.6"/>')
        lines = str(label).split("\n")
        cur = cy - (len(lines) - 1) * (fs + 4) / 2
        for line in lines:
            self.text(cx, cur + fs * 0.34, line, fs, dark, 700, "middle")
            cur += fs + 4

    def rule(self, y, label=None, tone="gray"):
        self.parts.append(
            f'<line x1="36" y1="{y}" x2="{self.w - 36}" y2="{y}" '
            f'stroke="{RULE}" stroke-width="1"/>')
        if label:
            self.text(self.w - 36, y - 8, label, 10.5, PAL[tone][2], 400,
                      "end")

    def legend(self, y, items):
        x = 36
        for label, tone in items:
            fill, stroke, _ = PAL[tone]
            self.rect(x, y, 15, 11, fill, stroke, 1, 3)
            self.text(x + 21, y + 9.5, label, 10.5, MUTED)
            x += 24 + len(label) * 6.4 + 34

    def footer(self, text):
        self.rule(self.h - 44)
        self.text(36, self.h - 24, text, 10.5, MUTED)

    def save(self, name):
        self.parts.append("</svg>")
        path = HERE / name
        path.write_text("\n".join(self.parts) + "\n", encoding="utf-8",
                        newline="\n")
        print("wrote %-34s %6d bytes" % (name, path.stat().st_size))


# ---------------------------------------------------------------------------
# 01 - system context / the measurement boundary
# ---------------------------------------------------------------------------
def d01():
    c = Canvas(1200, 700, "01 - System context",
               "Where every number comes from - and why server internals can never cross the HTTP boundary.")
    c.lane(36, 104, 520, 486, "WINDOWS HOST  |  stdlib Python 3.10+  |  your machine")
    c.lane(644, 104, 520, 486, "TARGET  |  native process on this host, or any HTTP site", "violet")

    c.box(60, 150, 270, 50, "cli.py  -  headless", "blue",
          "one run -> runs/<run-id>/ artifacts", fs=12.5)
    c.box(60, 212, 270, 50, "server.py + ui/app.js", "blue",
          "web UI :8787 - polls status at 1 Hz", fs=12.5)
    c.box(60, 290, 270, 58, "engine.Benchmark", "teal",
          "N worker threads send HTTP requests", fs=12.5)
    c.box(60, 368, 270, 58, "samplers.SamplerSet", "teal",
          "health probe 1 Hz + generator self-monitor\n(CPU + working set of THIS process)", fs=12.5, subfs=9.5)
    c.box(60, 452, 270, 58, "grading.grade -> quality\n-> scalability -> report.build", "gray",
          "S1-S9 - verdict - self-contained HTML", fs=11.5, subfs=10)
    c.note(60, 528, 270, 46, "No pip install. No WebSockets. No third-party JS.",
           "gray", 10.5)

    c.chip(360, 300, 170, 34, "HTTP client", "blue", 11)
    c.chip(360, 380, 170, 34, "self-monitor", "teal", 11)
    c.note(360, 428, 170, 58,
           "observes the generator\nitself - it never\ncrosses the boundary",
           "teal", 10)
    c.note(360, 496, 170, 66,
           "runtime_comparison.py\n14 languages +\nsuitability.build()",
           "violet", 10)
    c.arrow([(330, 319), (360, 319)], "blue")

    c.box(670, 150, 225, 62, "Target app (native)", "violet",
          "scripts/demo_target.py :8090\nor any HTTP site you own", fs=12, subfs=9.5)
    c.box(670, 230, 225, 62, "HTTP surface", "blue",
          "status - latency - bytes\nheaders + /health endpoint", fs=12, subfs=9.5)
    c.box(670, 310, 225, 62, "Server internals", "red",
          "CPU - memory - OOM: not\nvisible over HTTP, never\nestimated", fs=12, subfs=9.5)
    c.box(915, 150, 225, 62, "Native target lifecycle", "gray",
          "scripts/native_target.py\nstart | stop | status (PID record)", fs=11.5, subfs=9.5)
    c.box(915, 230, 225, 62, "Runtime adapter (optional)", "gray",
          "app /__hlc/runtime JSON endpoint", fs=11.5, subfs=9.5)
    c.box(915, 310, 225, 62, "Headless Chrome (optional)", "gray",
          "CDP: FCP / LCP / JS heap", fs=11.5, subfs=9.5)

    # network boundary: dashed vertical line separating what a client can see
    c.parts.append(
        f'<line x1="600" y1="118" x2="600" y2="580" stroke="{PAL["red"][1]}" '
        f'stroke-width="2" stroke-dasharray="8 6"/>')
    c.arrow([(530, 317), (578, 317), (578, 181), (670, 181)], "blue")
    c.arrow([(530, 397), (880, 397), (880, 292)], "gray", dashed=True,
            label="optional adapter path", lx=700, ly=389)

    c.note(670, 470, 470, 100,
           "Every run is client-only: latency, throughput, errors and\n"
           "availability are real observations over the wire. Server CPU,\n"
           "RAM and OOM state are not visible and are never estimated -\n"
           "those SLOs grade N/A - insufficient evidence.",
           "amber", 11, title="What this means")
    c.chip(470, 592, 260, 26, "HTTP BOUNDARY - CLIENT-ONLY VANTAGE", "red", 11)
    c.legend(632, [("Blue = HTTP requests/responses (only path to target)", "blue"),
                   ("Teal = generator self-monitor", "teal"),
                   ("Violet = target-side process", "violet"),
                   ("Dashed = optional", "gray")])
    c.footer("Sources: heavyload/engine.py, samplers.py, http_client.py, runner.py, "
             "scripts/native_target.py, server.py, docs/comparability.md")
    c.save("01-system-context.svg")


# ---------------------------------------------------------------------------
# 02 - module architecture
# ---------------------------------------------------------------------------
def d02():
    c = Canvas(1200, 840, "02 - Module architecture",
               "Three entry points, one orchestrator, the v2 analysis stack, one immutable result document.")
    c.box(60, 106, 212, 54, "cli.py", "blue", "headless runs +\ncampaign / compare", fs=13, subfs=10)
    c.box(282, 106, 212, 54, "server.py", "blue", "HTTP API + static web UI", fs=13, subfs=10)
    c.box(504, 106, 212, 54, "selftest.py", "blue", "end-to-end regression", fs=13, subfs=10)
    c.box(726, 106, 212, 54, "scripts/\nnative_target.py", "gray",
          "target lifecycle: start /\nstop / status (PID record)", fs=12, subfs=9.5, dashed=True)
    c.note(948, 106, 216, 54,
           "cli / server /\nselftest converge\non the SAME runner.", "gray", 10)

    c.box(330, 190, 510, 62, "heavyload/runner.run_benchmark()", "teal",
          "preflight (4 checks) - create run dir - orchestrate", fs=14)
    for x in (187.5, 457.5, 727.5, 1017):
        c.arrow([(585, 252), (585, 272), (x, 272), (x, 292)], "gray")
    c.arrow([(166, 160), (166, 176), (585, 176), (585, 190)], "gray")
    c.arrow([(388, 160), (388, 176), (585, 176)], "gray")
    c.arrow([(610, 160), (610, 190)], "gray")

    c.box(60, 292, 255, 72, "engine.Benchmark", "teal",
          "worker pool - 8 phases\nguardrails", fs=12.5, subfs=10)
    c.box(330, 292, 255, 72, "samplers.SamplerSet", "teal",
          "health probe + generator\nself-monitor (CPU + RSS)", fs=12.5, subfs=10)
    c.box(600, 292, 255, 72, "procmetrics.py", "gray",
          "OS-measured child RSS +\nCPU time (comparison)", fs=12.5, subfs=10, dashed=True)
    c.box(870, 292, 294, 72, "browser/cdp.py", "gray",
          "headless Chrome page metrics (optional)", fs=12.5, subfs=10, dashed=True)

    c.box(60, 392, 255, 60, "raw.jsonl", "blue",
          "one JSON line per request", fs=12.5, subfs=10)
    c.box(330, 392, 255, 60, "health_series +\ngenerator_series", "violet",
          "1 Hz probes + CPU / working set", fs=12, subfs=9.5)
    c.box(600, 392, 255, 60, "step_windows", "teal",
          "one window per concurrency level", fs=12.5, subfs=10)
    c.box(870, 392, 294, 60, "series.runtime_series /\nseries.browser_series", "gray",
          "optional adapter / CDP series", fs=11.5, subfs=9.5, dashed=True)
    for x in (187.5, 457.5, 727.5):
        c.arrow([(x, 364), (x, 392)], "gray")
    c.arrow([(1017, 364), (1017, 392)], "gray", dashed=True)

    c.box(60, 480, 1104, 54, "results{}  -  metrics.http + metrics.resources + series + step_windows  (assembled by engine.run())",
          "teal", fs=13)
    for x in (187.5, 457.5, 727.5):
        c.arrow([(x, 452), (x, 480)], "gray")
    c.arrow([(1017, 452), (1017, 480)], "gray", dashed=True)

    c.box(60, 562, 255, 64, "grading.grade()\nS1-S9", "amber",
          "findings - health score\nGO / NO-GO", fs=12.5, subfs=10)
    c.box(330, 562, 255, 64, "quality.check_run()\nscalability.analyze()", "amber",
          "integrity statuses - knee\npoint - classification", fs=12, subfs=10)
    c.box(600, 562, 255, 64, "schema.ensure_current()", "violet",
          "v2 schema - visibility\n(incl. client_only_same_host)", fs=11.5, subfs=10)
    c.box(870, 562, 294, 64, "runtime_comparison.run()\n+ suitability.build()", "violet",
          "14-language compute + weighted\nranking (AFTER the HTTP schedule)", fs=11.5, subfs=10)
    for x in (187.5, 457.5, 727.5, 1017):
        c.arrow([(x, 534), (x, 562)], "gray", dashed=(x == 1017))

    # one publish bus: every graded / normalised result feeds the same set
    c.parts.append(
        f'<line x1="187.5" y1="640" x2="1017" y2="640" '
        f'stroke="{PAL["gray"][1]}" stroke-width="1.4"/>')
    for x in (187.5, 457.5, 727.5, 1017):
        c.arrow([(x, 626), (x, 640)], "gray", width=1.6)

    c.box(60, 654, 255, 60, "report.build()\n-> report.html", "green",
          "self-contained: 16 sections", fs=12.5, subfs=10)
    c.box(330, 654, 255, 60, "results.json +\nstage2_verdict.json", "green",
          "+ status.json for live UI", fs=12.5, subfs=10)
    c.box(600, 654, 255, 60, "runtime_comparison.\njson / .html + sources/", "green",
          "also report.html section 16", fs=11.5, subfs=10)
    c.box(870, 654, 294, 60, "suitability.json / .html", "green",
          "weighted ranking + provenance", fs=12.5, subfs=10)
    for x in (187.5, 457.5, 727.5, 1017):
        c.arrow([(x, 640), (x, 654)], "gray", width=1.6)

    c.note(60, 732, 1104, 48,
           "server.py serves these artifacts from /runs/<id>/<file> (SAFE_ARTIFACTS whitelist); ui/app.js polls /api/status and /api/log at 1 Hz.",
           "blue", 11, title="Live UI path")
    c.footer("Sources: heavyload/runner.py, engine.py, samplers.py, procmetrics.py, "
             "comparison_sources/, quality.py, scalability.py, suitability.py, schema/, report.py, server.py")
    c.save("02-architecture.svg")


# ---------------------------------------------------------------------------
# 03 - one run, start to finish
# ---------------------------------------------------------------------------
def d03():
    c = Canvas(1200, 780, "03 - One run, start to finish",
               "The eight orchestration steps (top) and the eight HTTP phases of the smoke profile (bottom).")
    steps = [
        ("1  Preflight", "4 read-only checks:\nURL, health,\nvantage, host RAM", "blue"),
        ("2  Set up run", "runs/run-<stamp>/\nwrite config.json\n+ preflight.json", "blue"),
        ("3  Instrument", "start sampler threads:\nhealth probe @ 1 Hz\n+ generator self-monitor", "teal"),
        ("4  Drive load", "8 phases, guardrails,\nper-request raw.jsonl\n+ health probes", "teal"),
        ("5  Aggregate", "stop workers,\nlatency percentiles,\nquality + scalability", "teal"),
        ("6  Adjudicate", "grade S1-S9,\nschema v2 normalize,\nverdict + score", "amber"),
        ("7  Compare", "runtime comparison\n+ suitability\n(FAILURE-SAFE, after HTTP)", "violet"),
        ("8  Publish", "results.json,\nreport.html, status.json\n+ comparison / suitability", "green"),
    ]
    xs = [60 + i * 139.5 for i in range(8)]
    for i, (label, sub, tone) in enumerate(steps):
        c.box(xs[i], 112, 126, 104, label, tone, sub, fs=11, subfs=9)
        if i:
            c.arrow([(xs[i] - 13.5, 164), (xs[i], 164)], "gray", width=1.6)

    c.text(60, 254, "SMOKE PROFILE  -  8 phases, ~33 s total, peak C=20 (quick ~3.5 min / heavy ~5.5 min / stress ~10.5 min / standard ~24 min share the same shape)",
           12, INK, 700)
    segs = [
        ("01", "plateau", 3, "C=0", "idle baseline", "gray"),
        ("02", "warmup", 3, "C=2", "", "blue"),
        ("03", "ramp", 6, "2-5-10", "", "blue"),
        ("04", "sustained", 6, "C=10", "S4", "teal"),
        ("05", "spike", 3, "15-20", "", "amber"),
        ("06", "soak", 3, "C=5", "", "teal"),
        ("07", "zero-load", 6, "C=0", "S6 cooldown", "gray"),
        ("08", "reprobe", 3, "C=0", "S7 availability", "green"),
    ]
    x, total_w, total_s = 60.0, 1104.0, 33
    for ident, name, secs, conc, tag, tone in segs:
        w = total_w * secs / total_s
        fill, stroke, dark = PAL[tone]
        c.rect(x, 276, w - 4, 66, fill, stroke, 1.4, 7)
        c.text(x + (w - 4) / 2, 298, ident + " " + name, 10.5, dark, 700, "middle")
        c.text(x + (w - 4) / 2, 316, conc, 11, INK, 700, "middle")
        c.text(x + (w - 4) / 2, 332, "%ds" % secs, 9.5, MUTED, 400, "middle")
        if tag:
            c.text(x + (w - 4) / 2, 358, tag, 9.5, PAL["amber"][2], 400, "middle")
        x += w
    c.parts.append(
        f'<polyline points="60,268 1164,268" fill="none" stroke="{PAL["gray"][1]}" '
        f'stroke-width="1.5" marker-end="url(#ar_gray)"/>')
    c.text(60, 392, "time  ->", 10.5, MUTED, 400)

    c.box(60, 412, 540, 66, "Guardrails can abort early", "amber",
          "generator working-set ceiling, host RAM floor, error-rate ceiling,\nconsecutive-failure breaker -> partial results are written (right-censored)",
          fs=12.5, subfs=10)
    c.box(620, 412, 544, 66, "Step 7 cannot lose step 4", "green",
          "comparison + suitability run AFTER aggregation + grading;\nany exception becomes a warning in results.json", fs=12.5, subfs=10)

    notes = [
        ("01 idle", "capture baseline generator CPU / working set so net values can be derived"),
        ("02-03", "warm the target, then step concurrency up instead of hammering cold"),
        ("04 sustained", "the only phase used for the throughput SLO (S4)"),
        ("05 spike", "short burst to expose queueing and tail-latency blow-ups"),
        ("07 cooldown", "zero load; recovery behaviour means the same thing in every profile"),
        ("08 reprobe", "S7 confirms the target still answers after the whole schedule"),
    ]
    c.text(60, 512, "WHY THE PHASES EXIST", 12, INK, 700)
    for i, (k, v) in enumerate(notes):
        col, row = i % 2, i // 2
        xx = 60 + col * 570
        yy = 526 + row * 58
        c.rect(xx, yy, 546, 48, PANEL, RULE, 1, 7)
        c.text(xx + 12, yy + 19, k, 11, PAL["teal"][2], 700)
        c.text(xx + 12, yy + 36, v, 10.5, MUTED)
    c.footer("Sources: heavyload/config.py (preset_phases: 5 profiles), engine.py (_run_phase / _guard_check), "
             "runner.py (preflight / run_benchmark), grading.py (S1-S9)")
    c.save("03-run-lifecycle.svg")


# ---------------------------------------------------------------------------
# 04 - data flow: raw bytes -> verdict
# ---------------------------------------------------------------------------
def d04():
    c = Canvas(1200, 740, "04 - Data flow: raw bytes to verdict",
               "Every published number is traceable back to a raw request line, a self-monitor sample or a verified checksum.")
    c.text(60, 112, "1  RAW INPUT", 11.5, PAL["blue"][2], 700)
    c.text(340, 112, "2  AGGREGATION", 11.5, PAL["teal"][2], 700)
    c.text(620, 112, "3  GRADE + NORMALISE", 11.5, PAL["amber"][2], 700)
    c.text(920, 112, "4  OUTPUTS", 11.5, PAL["green"][2], 700)

    c.box(60, 128, 240, 88, "raw.jsonl", "blue",
          "t, phase, status, connect /\nTTFB / body, bytes, error", fs=13, subfs=10)
    c.box(60, 240, 240, 88, "health_series +\ngenerator_series", "violet",
          "1 Hz availability probes,\ngenerator CPU% + working set", fs=12.5, subfs=10)
    c.box(60, 352, 240, 88, "runtime / browser series", "gray",
          "optional adapter endpoint,\nCDP page metrics", fs=12, subfs=10, dashed=True)
    c.box(60, 464, 240, 88, "TIME_US per variant", "violet",
          "in-process compute, N repeats,\n+ procmetrics-measured\nRSS + CPU time", fs=12.5, subfs=10)

    c.box(340, 128, 240, 88, "_aggregate_requests()", "teal",
          "RPS, p50/p90/p95/p99/max,\nerror %, bytes, per-phase", fs=12.5, subfs=10)
    c.box(340, 240, 240, 88, "_aggregate_samples()", "teal",
          "timeline bins, generator\ncpu/rss, health success pct", fs=12.5, subfs=10)
    c.box(340, 352, 240, 88, "metrics.http + metrics.resources", "blue",
          "v2 schema, each field tagged\nwith its visibility", fs=12, subfs=10)
    c.box(340, 464, 240, 88, "checksum gate + median", "amber",
          "reject any RESULT mismatch;\nmedian of N in-process runs", fs=12.5, subfs=10)

    c.box(620, 128, 260, 160, "grading.grade()", "amber",
          "S1 errors - S2/S3 tail - S4 RPS\nS5/S6/S8 memory + stability:\nN/A in client-only runs\n(no server evidence)\nS7 availability - S9 schedule", fs=13, subfs=10)
    c.box(620, 310, 260, 90, "adjudication", "amber",
          "verdict GO / NO-GO / INSUFFICIENT\nhealth score 0-100 + findings", fs=12.5, subfs=10)
    c.box(620, 422, 260, 130, "schema.ensure_current()", "violet",
          "sets benchmark_mode, visibility\n(client_only /\nclient_only_same_host),\nfingerprint, comparability fields.\n\nAdditive v1 migration: no old key\nis deleted, renamed or coerced.",
          fs=12.5, subfs=10)

    c.box(920, 128, 244, 76, "results.json", "green",
          "full v2 result document (+\nquality + scalability blocks)", fs=12.5, subfs=10)
    c.box(920, 218, 244, 76, "report.html", "green",
          "self-contained, 16 sections,\ninline SVG charts, no JS", fs=12.5, subfs=10)
    c.box(920, 308, 244, 76, "stage2_verdict.json", "green",
          "verdict only, for automation", fs=12.5, subfs=10)
    c.box(920, 398, 244, 76, "status.json", "green",
          "live KPIs -> /api/status -> UI", fs=12.5, subfs=10)
    c.box(920, 488, 244, 76, "runtime_comparison.*\n+ suitability.*", "green",
          "json + html (+ sources/),\nalso report sections 15-16", fs=11.5, subfs=10)

    for y in (172, 284, 396, 508):
        c.arrow([(300, y), (340, y)], "gray", width=1.6)
    for y in (172, 284, 396, 508):
        c.arrow([(580, y), (620, y)], "gray", width=1.6,
                dashed=(y == 508))
    # A single "publish bus" instead of five arrows pretending to come from
    # one box: every graded/normalised result feeds the same artifact set.
    for y in (208, 355, 487):
        c.arrow([(880, y), (900, y)], "gray", width=1.6)
    c.parts.append(
        f'<line x1="900" y1="166" x2="900" y2="526" '
        f'stroke="{PAL["gray"][1]}" stroke-width="1.4"/>')
    for y in (166, 256, 346, 436, 526):
        c.arrow([(900, y), (920, y)], "gray", width=1.4)
    c.note(60, 588, 1104, 88,
           "A number is never invented: HTTP metrics come from one raw.jsonl line per request;\n"
           "generator CPU / working set come from the self-monitor samples; language timings come\n"
           "from a program that had to print the exact expected checksum. Missing sources degrade\n"
           "to \"unavailable\".",
           "blue", 11, title="Traceability rule")
    c.footer("Sources: heavyload/engine.py (_aggregate_*), grading.py, quality.py, scalability.py, "
             "schema/metrics.py, schema/migrate.py, report.py, runtime_comparison.py")
    c.save("04-data-flow.svg")


# ---------------------------------------------------------------------------
# 05 - comparability gate (v2: one vantage point)
# ---------------------------------------------------------------------------
def d05():
    c = Canvas(1200, 760, "05 - Benchmark modes and the comparability gate",
               "v2 has ONE vantage point (client-only). Comparability is decided by what can be held constant - not by an instrumentation switch.")
    c.text(60, 110, "PICK THE COMPARISON PAIR BY WHAT CAN ACTUALLY BE HELD CONSTANT", 11.5, MUTED, 700)
    c.chip(60, 126, 340, 34, "Archived v1 run of the old instrumented mode", "teal", 11)
    c.chip(430, 126, 340, 34, "Loopback target, same host, same profile", "green", 11)
    c.chip(800, 126, 364, 34, "Remote target / no generator fingerprint", "red", 11)

    cards = [
        (60, "docker_lab (v1 archive)", "teal", "visibility: full_server",
         ["archived v1 runs only - that instrumentation is gone",
          "carry real server-side resource evidence",
          "comparable against other archived runs of the era",
          "same schema_version + workload_class required",
          "restore from git history if ever needed again"],
         "COMPARABLE (v1 pairs)", "green"),
        (430, "client_only_same_host", "green", "visibility: client_only_same_host",
         ["loopback target (127.0.0.1 / ::1 / localhost)",
          "same generator_fingerprint + target_host",
          "same schema_version + workload_class + profile",
          "generator saturation monitored (generator_series)",
          "single-host client-side comparison - with caveats"],
         "COMPARABLE", "green"),
        (800, "client_only (remote)", "red", "visibility: client_only",
         ["HTTP latency, status, errors, RPS, bytes",
          "network conditions cannot be held constant",
          "no server evidence, no generator fingerprint",
          "resources.* and server series -> unavailable",
          "never infer server CPU / RAM from latency"],
         "NEVER COMPARABLE", "red"),
    ]
    for x, name, tone, vis, rows, badge, btone in cards:
        w = 364 if x == 800 else 340
        c.box(x, 180, w, 46, name, tone, fs=14)
        c.text(x + 12, 246, vis, 11, PAL[tone][2], 700)
        c.rect(x, 256, w, 168, PANEL, RULE, 1.2, 8)
        for i, row in enumerate(rows):
            yy = 276 + i * 30
            c.text(x + 12, yy, "-", 11, PAL[tone][1], 700)
            c.text(x + 26, yy, row, 10.5, INK if i < 4 else MUTED)
        c.chip(x, 436, w, 32, badge, btone, 11)
        c.arrow([(x + w / 2, 160), (x + w / 2, 180)], "gray", width=1.6)

    c.text(60, 506, "schema.can_compare(a, b)  -  HARD RULE, ENFORCED BEFORE ANY LEADERBOARD", 12, INK, 700)
    c.box(60, 522, 540, 116, "ALL of these must hold", "violet",
          "same benchmark_mode  +  same visibility  +  same schema_version\n"
          "same workload_class; client_only_same_host additionally needs\n"
          "equal generator_fingerprint + equal target_host", fs=12.5, subfs=11)
    c.arrow([(600, 580), (650, 580)], "gray", width=2)
    c.box(650, 522, 250, 52, "FAIR COMPARISON", "green", fs=12.5)
    c.box(920, 522, 244, 52, "NOT COMPARABLE", "red", fs=12.5)
    c.chip(980, 484, 164, 24, "any rule fails", "red", 10)
    c.arrow([(1062, 508), (1062, 522)], "red", width=1.6, dashed=True)
    c.note(650, 590, 514, 48,
           "Otherwise: comparable=False + a stated reason; no ranking, no winner, no misleading chart.",
           "amber", 10.5)
    c.note(60, 638, 1104, 72,
           "client_only_same_host measures the full stack through one loopback path: target and generator\n"
           "share physical cores and latency includes the loopback stack - same conditions for both sides,\n"
           "but not a LAN or production number. Label it: \"single-host client-side comparison\".",
           "gray", 10.5, title="The single-host caveat")
    c.footer("Sources: heavyload/schema/metrics.py (can_compare, VISIBILITY_LEVELS), compare.py, "
             "report_compare.py, docs/comparability.md")
    c.save("05-benchmark-modes.svg")


# ---------------------------------------------------------------------------
# 06 - the runtime comparison pipeline
# ---------------------------------------------------------------------------
def d06():
    c = Canvas(1200, 720, "06 - Language runtime comparison pipeline",
               "Five pinned workloads, identical algorithms in 14 languages, one checksum gate, zero simulated numbers.")
    steps = [
        (60, 130, "1  PIN THE WORKLOAD", "violet",
         ["A  prime, N=50,000     ->  5133 121013308",
          "B  json, 200x600      ->  249868800 9163",
          "C  map, 64x100,000    ->  44432313 100000",
          "D  text, 50,000 rows  ->  1295428 300465975",
          "E  matmul, 128x128    ->  -1985 -40",
          ""],
         "Change a constant -> checksum changes -> run is rejected."),
        (430, 130, "2  DETECT + BUILD", "blue",
         ["comparison_sources/: one module per language",
          "14 languages: Spid3r, Python, JS/Node, TS, PHP,",
          "Ruby, Java, Kotlin, Go, C#, Swift, Rust, C, C++",
          "Spid3r variants: tree-walk, VM, VM --no-opt,",
          "bytecode exec, native --release",
          ""],
         "cli.py --verify-sources compiles + runs every detected toolchain against the pinned checksums."),
        (800, 130, "3  MEASURE", "teal",
         ["Run every variant N times (default 5).",
          "Each program prints RESULT and TIME_US.",
          "",
          "Timing is IN-PROCESS: startup + build time",
          "excluded; median of repeats is reported.",
          "procmetrics: OS-measured peak RSS + CPU time."],
         "Spid3r has only now_ms(), so workloads are sized >= 28 ms."),
        (800, 400, "4  CHECKSUM GATE", "red",
         ["Observed RESULT must be identical across",
          "every repeat AND exactly match EXPECTED.",
          "",
          "Mismatch -> variant REJECTED and never",
          "published. Verified rows -> slowdown ratios",
          "+ a pairwise geomean ratio matrix."],
         "This is the anti-fake invariant."),
        (430, 400, "5  SUMMARISE", "amber",
         ["runtime_comparison.json  (all repeats,",
          "  min/max, versions, ratios, matrix)",
          "runtime_comparison.html (self-contained)",
          "sources/ (exact programs per language)",
          "results.json <- compact summary",
          "report.html <- section 16"],
         "Every normal run and the standalone --runtime-comparison mode."),
        (60, 400, "6  READ IT HONESTLY", "green",
         ["This measures language-runtime COMPUTE,",
          "not end-to-end web-framework throughput.",
          "",
          "Do NOT compare these milliseconds with",
          "RPS / p99 from the HTTP load test.",
          "Both answer different questions."],
         "Production still runs tree-walk - the slowest Spid3r engine."),
    ]
    for x, y, title, tone, rows, note in steps:
        c.box(x, y, 340, 36, title, tone, fs=12)
        c.rect(x, y + 42, 340, 150, PANEL, RULE, 1.2, 8)
        for i, row in enumerate(rows):
            c.text(x + 12, y + 64 + i * 22, row, 10.5,
                   INK if not row.startswith("     ") else MUTED)
        c.note(x, y + 200, 340, 40, note, tone, 9.5)

    c.arrow([(400, 235), (430, 235)], "gray", width=2)
    c.arrow([(770, 235), (800, 235)], "gray", width=2)
    c.arrow([(970, 368), (970, 400)], "gray", width=2)
    c.arrow([(800, 475), (770, 475)], "gray", width=2)
    c.arrow([(430, 475), (400, 475)], "gray", width=2)
    c.legend(652, [("always on", "teal"), ("integrity gate", "red"),
                   ("output", "green"), ("skipped if toolchain missing", "gray")])
    c.footer("Sources: heavyload/runtime_comparison.py, comparison_sources/ (14 modules), "
             "procmetrics.py, report.py (_runtime_section), cli.py --verify-sources")
    c.save("06-runtime-comparison.svg")


# (file, title, one-line caption, developer notes). The notes are the
# "where do I put my hands on the keyboard" companion to each picture.
INDEX = [
    ("01-system-context.svg", "01 - System context",
     "One host, one HTTP boundary. An external client can never see server "
     "CPU, RAM or OOM events - and v2 never pretends otherwise.",
     [
         "`heavyload/http_client.py` owns `make_connection()` and one-shot "
         "`probe()`; the load generator never uses anything else to reach the target.",
         "`samplers.SamplerSet` runs exactly two daemon threads: the 1 Hz health "
         "probe and the generator self-monitor (`generator_series`: CPU% from "
         "`process_time` deltas, working set via ctypes psapi / procfs). Always "
         "`client_only=True`.",
         "The generator self-monitor observes THIS process only - it never crosses "
         "the HTTP boundary. Server CPU / RAM / OOM stay invisible and are never "
         "estimated; those SLOs grade `N/A - insufficient evidence`.",
         "`runner.preflight()` performs 4 read-only checks: target reachable, "
         "health endpoint, vantage point (loopback vs remote), host RAM headroom.",
         "`scripts/native_target.py <site-id> start|stop|status` manages local "
         "targets natively (os.spawnve, PID records under `runs/targets/`) - "
         "no shell, no SDK.",
     ]),
    ("02-architecture.svg", "02 - Module architecture",
     "Entry points, one runner, the v2 analysis stack (quality, scalability, "
     "suitability), one result document. Dashed boxes are optional.",
     [
         "`runner.run_benchmark(cfg, root, log, preflight_result, bench_holder)` "
         "is the SINGLE orchestrator - `cli.py` and `server.py /api/run/start` "
         "both call it, so a feature added there works in both UIs.",
         "`engine.Benchmark.run()` returns the `results{}` dict and records "
         "`step_windows` (one window per concurrency level); `grading.grade()` "
         "adds `adjudication`, `quality.check_run()` + `scalability.analyze()` "
         "attach their blocks, `schema.ensure_current()` adds v2 identity fields.",
         "`runtime_comparison.run()` + `suitability.build()` are wrapped in "
         "try/except: any exception becomes `status=error` + a warning in "
         "`results.json`, never a lost run.",
         "`comparison_sources/` is the language registry: one module per language "
         "(14 total); adding a module = adding a language. `procmetrics.py` "
         "measures child RSS + CPU time (Windows `GetProcessMemoryInfo`/"
         "`GetProcessTimes`, POSIX `wait4` rusage) or returns `None` - "
         "`unavailable`, never estimated.",
         "`report.build()` emits one self-contained HTML (16 sections, inline "
         "SVG, no JS); `paths.SAFE_ARTIFACTS` is the whitelist used by "
         "`server.py` for `/runs/<run-id>/<artifact>`.",
     ]),
    ("03-run-lifecycle.svg", "03 - One run, start to finish",
     "Eight orchestration steps and the eight HTTP phases. The language "
     "comparison deliberately runs last.",
     [
         "`config.preset_phases(name)` returns the 8 phases; profiles only change "
         "durations + ceilings (smoke ~33 s C=20, quick ~3.5 min C=100, "
         "heavy ~5.5 min C=200, stress ~10.5 min C=400, standard ~24 min C=300).",
         "`engine._run_phase()` drives one phase, `_worker()` issues requests, "
         "`_writer()` appends one JSON line per request to `raw.jsonl`, "
         "`_guard_check()` can set `_set_abort(reason)`.",
         "Guardrails come from `config.guardrails`: `max_anon_mib` (generator "
         "working-set ceiling), `min_host_free_gb`, `max_error_rate_pct`, "
         "`max_consecutive_failures`. On abort, `cooldown_s` + `reprobe_s` still "
         "run, partial results are written and the run is graded right-censored.",
         "Step 7 sits after `ensure_current()` on purpose - the HTTP result is "
         "already aggregated, graded and quality-checked before a single language "
         "program is spawned.",
     ]),
    ("04-data-flow.svg", "04 - Data flow: raw bytes to verdict",
     "Every published number traces back to a raw line, a self-monitor sample "
     "or a verified checksum.",
     [
         "`engine._aggregate_requests()` -> `metrics.http` (see `schema/metrics.py "
         "empty_http()`); `_aggregate_samples()` -> `metrics.resources`. "
         "Server-side keys stay `None` (shown as `unavailable`); generator-side "
         "keys (`gen_cpu_*`, `gen_rss_*`) are measured.",
         "`grading.grade()` registers S1-S9; in client-only runs S5/S6/S8 grade "
         "`N/A` because server memory/stability is not observable - the tool "
         "never invents evidence.",
         "`schema/migrate.py` performs an ADDITIVE v1 -> v2 migration: no v1 key "
         "is deleted, renamed or coerced, so archived reports still render.",
         "`quality.check_run()` labels every check `ok / warn / fail / "
         "insufficient_evidence`; `scalability.analyze()` reports "
         "`insufficient_evidence` below two measured concurrency levels.",
         "Writes go through `util.atomic_json()`; `report.py` draws inline SVG "
         "charts from the same dict - no second data source, no estimates.",
     ]),
    ("05-benchmark-modes.svg", "05 - Modes and the comparability gate",
     "v2 comparability: only runs measured under conditions that can be proven "
     "equal may be ranked.",
     [
         "`schema/metrics.py:can_compare(a, b)` requires equal `benchmark_mode`, "
         "`visibility`, `schema_version` and `workload_class`; plain "
         "`client_only` (remote target) is then refused outright.",
         "`client_only_same_host` additionally requires equal "
         "`generator_fingerprint` (`hostname|platform|machine|cpu_count`) and "
         "`target_host` - archived runs without the fingerprint are refused.",
         "`VISIBILITY_LEVELS = (\"full_server\", \"client_only\", "
         "\"client_only_same_host\", \"server_via_adapter\")`; archived "
         "`docker_lab` (v1) runs keep `full_server` and compare only among "
         "themselves - that instrumentation no longer exists.",
         "`compare.py` sets `comparable=False` + a reason and still returns raw "
         "rows; `report_compare.py` prints NOT COMPARABLE instead of a "
         "leaderboard. A refusal reason is always shown, never silently dropped.",
         "`server_via_adapter` is a declared seam, not an implementation: add a "
         "`RuntimeAdapter` (`heavyload/adapters/`) and its outputs stay labelled "
         "separately.",
     ]),
    ("06-runtime-comparison.svg", "06 - Language runtime comparison",
     "Pinned workloads, 14 languages via the registry, one checksum gate, no "
     "simulated numbers.",
     [
         "`runtime_comparison.py` pins 5 workloads (`PRIME_N`, "
         "`JSON_ROWS/ROUNDS`, `MAP_KEYS/ROUNDS`, `TEXT_ROWS`, `MATMUL_N`) and "
         "the `EXPECTED` checksum dict. Change a constant and you MUST recompute "
         "EXPECTED or every variant is rejected.",
         "`comparison_sources/` registers 14 languages (Spid3r, Python, "
         "JavaScript, TypeScript, PHP, Ruby, Java, Kotlin, Go, C#, Swift, Rust, "
         "C, C++); each module declares `TOOL_KEYS`, build steps and run "
         "variants. Workload B exists only where the stdlib ships a JSON "
         "serializer (`not applicable` elsewhere).",
         "Each generated program prints exactly two lines: `RESULT <a> <b>` and "
         "`TIME_US <n>`; `_parse_output()` enforces that contract.",
         "`detect_tools()` probes host toolchains; missing ones are skipped with "
         "a warning. `procmetrics` adds OS-measured peak working set + CPU time "
         "per variant (or honest `unavailable`).",
         "`cli.py --verify-sources` compiles + runs every detected toolchain "
         "against the pinned checksums and prints the table. Stats: median, "
         "stdev, CV%, IQR, t-CI + bootstrap CI; slowdown ratios + pairwise "
         "geomean matrix.",
     ]),
]

# ---------------------------------------------------------------------------
# Developer reference content for index.html
# ---------------------------------------------------------------------------
COMMANDS = [
    ("`python server.py --open`", "Web UI on 127.0.0.1:8787; polls status at 1 Hz."),
    ("`python scripts/demo_target.py`", "Real local target on http://127.0.0.1:8090/ (/, /catalog, /health)."),
    ("`python cli.py --profile smoke`", "One headless run (~33 s) + runtime comparison + suitability."),
    ("`python cli.py --profile heavy` / `--profile stress`", "Heavy (peak C=200, ~5.5 min) and stress (peak C=400, ~10.5 min) profiles."),
    ("`python cli.py --verify-sources`", "Compile + run every detected toolchain against the pinned checksums."),
    ("`python cli.py --runtime-comparison`", "Language comparison ONLY - no HTTP traffic, target may be down."),
    ("`python cli.py --no-runtime-comparison`", "Skip the comparison for this run."),
    ("`python cli.py --runtime-repeats 1`", "Shorten the comparison while iterating."),
    ("`python cli.py --preflight-only`", "Run the 4 read-only preflight checks and exit."),
    ("`python cli.py --site <site-id>`", "Apply a targets/<site-id>.json spec (same routes/payloads per language)."),
    ("`python scripts/native_target.py <site-id> start`", "Start a native target (waits for health); `stop` ends it by PID record."),
    ("`python cli.py --campaign benchmarks/lang-runtime.json`", "Multi-site campaign; the leaderboard ranks only the comparable set."),
    ("`python cli.py --compare run-a run-b --output out/`", "Compare / export existing runs."),
    ("`python selftest.py`", "End-to-end regression (80+ checks)."),
    ("`python -m unittest discover -s tests -v`", "Unit suite."),
    ("`python docs/diagrams/generate.py`", "Regenerate these six SVGs + this page."),
]

MODULES = [
    ("`heavyload/paths.py`", "ROOT, `runs_dir()`, `SAFE_ARTIFACTS`; honours `config.output_dir`."),
    ("`heavyload/config.py`", "DEFAULTS, 5 profiles, 8-phase schedule, normalize/validate/save; legacy configs map to the single host environment with a recorded note."),
    ("`heavyload/runner.py`", "`preflight()` (4 checks) + `run_benchmark()` orchestration."),
    ("`heavyload/engine.py`", "`Benchmark`: workers, guardrails, `step_windows`, `raw.jsonl`, aggregation."),
    ("`heavyload/http_client.py` / `latency.py`", "Connection factory, probes, `Stopwatch` (connect / TTFB / body / dispatch delay)."),
    ("`heavyload/samplers.py`", "Two daemon threads: health probe + generator self-monitor. Always `client_only=True`."),
    ("`heavyload/procmetrics.py`", "OS-measured child metrics: Windows GetProcessMemoryInfo / GetProcessTimes via ctypes; POSIX wait4 rusage. `None` = unavailable, never estimated."),
    ("`heavyload/comparison_sources/`", "One module per language (14): pinned sources, toolchain keys, variants, honesty notes. Registry in `__init__.py`."),
    ("`heavyload/runtime_comparison.py`", "Toolchain detection, builds, variant timing, checksum gate, stats (median/CI/CV/IQR/bootstrap), ratios + pairwise matrix, `--verify-sources`."),
    ("`heavyload/quality.py`", "Data-integrity checks over the run's own evidence; ok / warn / fail / insufficient_evidence."),
    ("`heavyload/scalability.py`", "Concurrency curve from `step_windows`; knee detection; linear / sublinear / plateau / degraded."),
    ("`heavyload/suitability.py`", "Weighted ranking from measured + explicitly-labelled documented criteria; standalone HTML."),
    ("`heavyload/grading.py`", "SLOs S1-S9, findings, health score, verdict; re-grades archived runs identically."),
    ("`heavyload/schema/`", "v2 metric contract, visibility map (incl. `client_only_same_host`), `can_compare`, additive v1 migration."),
    ("`heavyload/report.py`", "Single self-contained HTML: 16 sections incl. scalability, quality, strengths/weaknesses, suitability."),
    ("`heavyload/adapters/`, `browser/`", "Optional runtime-adapter contract (6 adapters) and stdlib CDP page metrics."),
    ("`heavyload/targetspec.py`", "Per-site spec loader / validator, `apply_to_config`, native start/build argv parsing (no shell)."),
    ("`scripts/native_target.py`", "Native target lifecycle: start (os.spawnve) / stop (ctypes TerminateProcess) / status; PID records under `runs/targets/`."),
    ("`scripts/archive_legacy.py`", "Verified backup + Recycle Bin cleanup (reversible, nothing deletes without `--confirm`)."),
    ("`heavyload/compare.py`, `campaign.py`, `report_compare.py`", "Rankings + comparability guard; the leaderboard ranks only the comparable set."),
    ("`server.py`, `ui/app.js`", "HTTP API + vanilla-JS single-page UI."),
]

CONFIG_KEYS = [
    ("`target_url`, `paths[]`, `health_path`", "What is hit and how it is health-checked."),
    ("`profile` / `phases[]`", "`smoke` / `quick` / `heavy` / `stress` / `standard`, or an explicit 8-phase schedule."),
    ("`benchmark_mode`", "Fixed `live_blackbox` - every v2 run is an honest client-only measurement."),
    ("`runtime_environment`", "Fixed `windows` (installed host runtimes); a legacy value maps with a recorded note."),
    ("`target{}`, `targets[]`", "Site metadata (site_id, language, framework, ...) + optional per-site specs."),
    ("`max_concurrency`, `request_timeout_s`, `keep_alive`", "Load-generator envelope."),
    ("`sample_period_s`, `cooldown_s`, `reprobe_s`", "Sampler cadence; post-abort zero-HTTP observation + availability re-probe."),
    ("`fairness.*`", "warmup_s, idle_baseline_s, repeats, seed, median aggregation, cpuset_target / cpuset_generator, pin_host_generator."),
    ("`guardrails.*`", "Abort thresholds: generator working-set MiB ceiling, host free GB floor, error rate, consecutive failures."),
    ("`slo.*`", "Thresholds graded by S1-S9 (error %, p95, p99, min RPS, ...); server-side ones grade N/A without evidence."),
    ("`runtime_comparison.*`", "enabled / repeats / include_windows / include_native / include_references / write_sources / timeout_s / workloads."),
    ("`suitability.*`", "enabled + criterion weights (measured provenance vs labelled documented criteria)."),
    ("`browser`, `runtime_adapter`", "Opt-in CDP page metrics and per-language runtime endpoint."),
    ("`output_dir`", "Where run directories are written (default `runs`)."),
]

RESULT_FIELDS = [
    ("`benchmark_mode`, `visibility`, `schema_version`", "Identity + comparability checked by `can_compare()`; visibility includes `client_only_same_host`."),
    ("`metrics.http`", "`rps`, `error_pct`, `latency_ms{p50,p90,p95,p99,max,mean}`, `bytes_total`, `connect_ms`, `ttfb_ms`, `total_ms`."),
    ("`metrics.resources`", "Server-side keys stay `unavailable`; generator-side evidence is measured: `gen_cpu_peak_pct`, `gen_rss_peak_mib`, `host_free_gb_min`, ..."),
    ("`metrics.runtime`, `metrics.browser`", "Optional adapter / CDP fields; `null` when unavailable."),
    ("`series.*`", "`health_series` + `generator_series` (always); `runtime_series` / `browser_series` when the optional instrumentation is on."),
    ("`environment.fingerprint`", "`generator_fingerprint` (hostname|platform|machine|cpu_count), `target_host`, seed - the same-host proof used by `can_compare()`."),
    ("`quality`", "Per-check statuses `ok / warn / fail / insufficient_evidence` + overall status."),
    ("`scalability`", "Throughput-vs-concurrency points, RPS-per-worker, knee point, classification (or `insufficient_evidence`)."),
    ("`adjudication`", "`verdict`, `health_score`, `slo[]`, `findings[]`."),
    ("`runtime_comparison`, `suitability`", "Compact summaries: status, ratios + pairwise matrix, ranking with per-criterion provenance."),
]

SLOS = [
    ("S1", "Request error rate", "`slo.max_error_pct` (default 0.1%)"),
    ("S2", "Successful-200 p95 latency", "`slo.max_p95_ms` (500 ms)"),
    ("S3", "Successful-200 p99 latency", "`slo.max_p99_ms` (2000 ms)"),
    ("S4", "Sustained throughput", "`slo.min_rps` (0 => informational)"),
    ("S5", "Bounded memory growth", "`slo.max_memory_growth_mib` (256 MiB) - server-side; `N/A` in client-only runs"),
    ("S6", "Memory released after cooldown", "`slo.max_retained_pct` (120%) - server-side; `N/A` in client-only runs"),
    ("S7", "Availability probe success", "`slo.min_health_success_pct` (99%)"),
    ("S8", "No OOM kill / restart", "needs server-side evidence; `N/A` when stability is not observable"),
    ("S9", "Full schedule completed", "no guardrail abort (an abort is graded right-censored)"),
]

ARTIFACTS = [
    ("`raw.jsonl`", "One JSON line per request - the ground truth for all HTTP metrics."),
    ("`results.json`", "Full v2 result document (metrics, series, quality, scalability, adjudication)."),
    ("`report.html`", "Self-contained HTML: 16 sections incl. scalability + data quality + suitability."),
    ("`stage2_verdict.json`", "Verdict only - for CI / automation."),
    ("`status.json`", "Live KPIs consumed by `/api/status` during a run."),
    ("`config.json`, `preflight.json`", "Exact effective config + the 4 preflight checks for reproducibility."),
    ("`runtime_comparison.json`", "Every repeat, min/max, versions, checksums, ratios + pairwise matrix."),
    ("`runtime_comparison.html`, `sources/`", "Comparison report + the exact generated programs per language."),
    ("`suitability.json` / `.html`", "Weighted ranking with per-criterion provenance."),
]

API = [
    ("`/api/bootstrap`, `/api/status`, `/api/log`", "UI bootstrap + 1 Hz live state."),
    ("`/api/config`, `/api/preset`", "Read / update config; apply a profile preset."),
    ("`/api/preflight`, `/api/routes`, `/api/health`", "Read-only checks and route discovery."),
    ("`/api/run/start`, `/api/run/stop`", "Start / gracefully stop a run (same runner as CLI)."),
    ("`/api/reports`, `/runs/<id>/<artifact>`", "List reports; serve whitelisted artifacts."),
    ("`/api/campaign/list`, `/api/campaign/status`, `/api/campaign/start`", "Campaigns: list, live status, start."),
    ("`/api/compare`, `/api/compare/report`, `/api/export`", "Comparison reports and CSV export."),
]

WORKLOADS = [
    ("A - prime", "`N = 50,000`", "`RESULT 5133 121013308`", "pure interpreter integer loops"),
    ("B - JSON", "`200 rows x 600 rounds`", "`RESULT 249868800 9163`", "native serializers (only where the stdlib ships JSON)"),
    ("C - map", "`64 keys x 100,000 lookups`", "`RESULT 44432313 100000`", "string-keyed route / config / session lookups"),
    ("D - text", "`50,000 rows`", "`RESULT 1295428 300465975`", "string churn, GC pressure, formatting"),
    ("E - matmul", "`128 x 128 (transposed B)`", "`RESULT -1985 -40`", "tight numeric loops, exact i64 arithmetic"),
]

SEAMS = [
    ("New language in the comparison", "Add one `lang_<x>.py` module to `heavyload/comparison_sources/` (the registry in `__init__.py`: add a module = add a language)."),
    ("New target site", "Write `targets/<site-id>.json`, then `scripts/native_target.py <site-id> start`; keep routes/payloads identical across languages."),
    ("New server metric source", "Implement a `RuntimeAdapter` (`heavyload/adapters/base.py`); its outputs are labelled `server_via_adapter`."),
    ("New benchmark profile", "Add a `*_phases()` function + `PRESETS` entry in `config.py`."),
    ("New SLO", "Add one `add(...)` call in `grading.grade()` (S1-S9 live there)."),
    ("New result field", "Extend `schema/metrics.py` additively; never delete or rename a v1 key."),
    ("Suitability criteria / weights", "Tune `config.suitability.weights`; documented criteria stay labelled as opinions with provenance."),
]

INVARIANTS = [
    ("Stdlib only", "No pip install for the core. Optional extras (psutil, Playwright) are never required; "
     "the CDP client uses plain `socket`."),
    ("Nothing fabricates evidence", "Missing toolchains degrade to `unavailable` / `not applicable`; checksum-mismatched "
     "comparison variants are REJECTED, never published; quality checks can answer "
     "`insufficient_evidence`."),
    ("Client-only vantage", "Server CPU / RAM / OOM is never estimated from the outside; S5 / S6 / S8 grade "
     "`N/A - insufficient evidence` instead of a fake PASS."),
    ("Additive schema", "v1 keys are never deleted, renamed or coerced - archived reports keep rendering."),
    ("Visibility is explicit", "`can_compare()` refuses cross-vantage and cross-host rankings with a stated reason; "
     "`client_only_same_host` is a single-host client-side comparison, not a server-resource "
     "benchmark."),
    ("Instrumentation must not perturb", "The sampler stack is two daemon threads; the generator's own CPU/memory is measured "
     "so client-side saturation can never hide."),
    ("No simulated numbers", "Workload constants are pinned; `--verify-sources` re-proves every toolchain; "
     "noisy (CV > 15%) variants are flagged, not hidden."),
    ("Compute speed is not HTTP throughput", "The language comparison measures in-process milliseconds; RPS / p99 "
     "come from the HTTP load test. Never compare the two."),
    ("Comparison is failure-safe", "It runs AFTER the HTTP schedule; an exception becomes a warning in "
     "`results.json` and the load result survives."),
    ("Coordinated omission is handled", "Percentiles carry the `as_scheduled_latency` view; generator dispatch lag is "
     "measured separately so client scheduling delay is never blamed on the target."),
    ("Spid3r specifics", "Only `now_ms()` (1 ms resolution); JSON builtin is `json_write`, not `json_encode`. "
     "The deployed store still runs the tree-walk engine, the slowest of the measured variants."),
]

GLOSSARY = [
    ("visibility", "`full_server` (archived v1), `client_only`, `client_only_same_host` or "
     "`server_via_adapter` - what vantage point the numbers came from."),
    ("client_only", "Only HTTP observations exist; server CPU/RAM fields are `unavailable`."),
    ("client_only_same_host", "Loopback target + matching generator fingerprint: runs are rankable as a "
     "single-host client-side comparison (explicit caveats)."),
    ("generator_series", "The self-monitor samples of the load generator itself: CPU% + working set. "
     "The cost of measuring, always published."),
    ("procmetrics", "OS-measured child metrics (Windows GetProcessMemoryInfo / GetProcessTimes; "
     "POSIX wait4 rusage). `None` = unavailable, never estimated."),
    ("TTFB", "Time to first byte; `latency.Stopwatch` splits connect / TTFB / body / dispatch delay."),
    ("p99 / p95", "Tail latency percentiles over successful 200 responses."),
    ("health score", "0-100 score produced by `grading.grade()` from graded S1-S9 rows."),
    ("knee point", "Last concurrency level before marginal throughput collapses or p95 inflates "
     "beyond 2x the best level."),
    ("right-censored", "Grading of an aborted run: the schedule did not complete, so schedule-dependent "
     "conclusions are `N/A`, never passed silently."),
    ("TIME_US", "In-process compute microseconds printed by every language benchmark program."),
    ("checksum gate", "The exact `RESULT <a> <b>` string that must match `EXPECTED` or the variant is rejected."),
    ("workload_class", "Result identity field (default `http_loadtest`) used by `can_compare()`."),
]


def fmt(s):
    """Escape text, turning `inline code` spans into <code> elements."""
    out = []
    for part in re.split(r"(`[^`]+`)", str(s)):
        if len(part) > 2 and part.startswith("`") and part.endswith("`"):
            out.append("<code>%s</code>" % X(part[1:-1]))
        else:
            out.append(X(part))
    return "".join(out)


def table(headers, rows):
    h = "".join("<th>%s</th>" % fmt(x) for x in headers)
    r = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % fmt(x) for x in row)
                for row in rows)
    return ('<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>'
            % (h, r))


def ref_section(anchor, title, lead, inner):
    return ('<section class="ref" id="%s"><h2>%s</h2><p>%s</p>%s</section>\n'
            % (anchor, X(title), fmt(lead), inner))


def namespace_svg(svg, n):
    """Give every id in one inlined SVG a per-diagram suffix.

    Each standalone SVG defines the same marker ids (ar_blue, ar_violet, ...),
    ttl and dsc. Inlining six of them into one HTML document would create
    duplicate ids, and url(#ar_blue) could bind to another diagram's <defs>.
    All local references (url(#...), aria-labelledby, href="#...") are rewritten
    to match, so the page stays valid and each SVG stays self-contained.
    """
    names = set(re.findall(r'\bid="([^"]+)"', svg))

    def suffix(m):
        return 'id="%s-%d"' % (m.group(1), n)

    out = re.sub(r'\bid="([^"]+)"', suffix, svg)
    out = re.sub(r'url\(#([^)]+)\)',
                 lambda m: 'url(#%s-%d)' % (m.group(1), n)
                 if m.group(1) in names else m.group(0), out)
    out = re.sub(r'aria-labelledby="([^"]+)"',
                 lambda m: 'aria-labelledby="%s"' % " ".join(
                     "%s-%d" % (x, n) if x in names else x
                     for x in m.group(1).split()), out)
    out = re.sub(r'href="#([^"]+)"',
                 lambda m: 'href="#%s-%d"' % (m.group(1), n)
                 if m.group(1) in names else m.group(0), out)
    return out


def build_index():
    """One self-contained HTML page: 6 inlined SVGs + engineer reference.

    The SVGs are embedded (not <img src>) so the page is a single file with
    zero external requests. namespace_svg() makes every id unique first.
    All prose lives in the data tables above, not in this HTML string.
    """
    head = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Heavy Load Checker - core diagrams &amp; developer reference</title>
<style>
  :root { color-scheme: light; }
  body { margin:0; padding:32px 18px 64px; background:#f4f7fb; color:#16202c;
         font:15px/1.6 "Segoe UI",Arial,Helvetica,sans-serif; }
  main { max-width:1180px; margin:0 auto; }
  h1 { font-size:27px; margin:0 0 6px; }
  .lead { color:#5b6b7d; margin:0 0 20px; }
  nav { background:#fff; border:1px solid #d8e1ec; border-radius:10px;
        padding:14px 18px; margin-bottom:18px; }
  nav a { color:#2563eb; text-decoration:none; margin-right:16px;
          white-space:nowrap; font-size:12.5px; }
  nav a:hover { text-decoration:underline; }
  nav .sep { display:block; height:1px; background:#e6ecf4; margin:11px 0; }
  nav .reflink { color:#0b5c53; font-weight:700; }
  .legend, .callout { background:#fff; border:1px solid #d8e1ec;
        border-left:4px solid #2563eb; border-radius:9px;
        padding:13px 18px; margin:0 0 24px; font-size:13px; color:#3f4c5c; }
  .callout { background:#fff8ea; border-color:#f0d9a8; border-left-color:#c07a12; }
  .legend b, .callout b { color:#16202c; }
  section { background:#fff; border:1px solid #d8e1ec; border-radius:12px;
            padding:22px 22px 12px; margin:0 0 26px; }
  section h2 { font-size:19px; margin:0 0 4px; }
  section p { color:#5b6b7d; margin:0 0 16px; font-size:13px; }
  section svg { width:100%; height:auto; display:block; }
  .devnotes { margin:2px 0 14px; padding:13px 18px; background:#f8fafc;
        border:1px solid #e2e9f2; border-left:4px solid #2563eb; border-radius:8px; }
  .devnotes h3 { font-size:11.5px; margin:0 0 8px; color:#12336b;
        text-transform:uppercase; letter-spacing:.05em; }
  .devnotes ul { margin:0; padding-left:18px; font-size:12.8px; color:#3f4c5c; }
  .devnotes li { margin:5px 0; }
  .ref h3 { font-size:14px; margin:20px 0 4px; color:#0b5c53; }
  .ref h3:first-child { margin-top:4px; }
  code { background:#eef2f8; border:1px solid #dde5ef; border-radius:4px;
        padding:1px 5px; font:12px/1.5 Consolas,"Courier New",monospace;
        color:#12336b; white-space:nowrap; }
  table { width:100%; border-collapse:collapse; font-size:12.8px; margin:6px 0 18px; }
  th, td { text-align:left; padding:8px 11px; border-bottom:1px solid #e6ecf4;
           vertical-align:top; }
  th { background:#f4f7fb; color:#3f4c5c; font-size:11px;
       text-transform:uppercase; letter-spacing:.04em; }
  tbody tr:hover { background:#fafbfd; }
  td:first-child { width:31%; }
  ol.inv { padding-left:20px; font-size:13px; color:#3f4c5c; margin:4px 0 14px; }
  ol.inv li { margin:8px 0; }
  ol.inv strong { color:#16202c; }
  footer { color:#5b6b7d; font-size:12px; text-align:center; margin-top:30px; }
  @media print { body { background:#fff; } section, .devnotes, table {
      break-inside:avoid; } nav { display:none; } }
</style>
</head>
<body><main>
<h1>Heavy Load Checker - core diagrams &amp; developer reference</h1>
<p class="lead">Six diagrams explaining the system end to end, each followed by
concrete developer notes, plus a compact engineering reference (modules, config,
result schema, SLOs, API and invariants). This page is a single file: all SVGs
are inlined, no JavaScript, no network requests.</p>
<nav>
"""
    body = []
    for i, (name, title, _, _) in enumerate(INDEX, 1):
        body.append('<a href="#d%d">%s</a>' % (i, X(title)))
    body.append('<span class="sep"></span>')
    body.append('<a class="reflink" href="#ref">Developer reference '
                '(commands / modules / config / schema / SLOs / API)</a>')
    body.append('<a class="reflink" href="#invariants">Invariants &amp; gotchas</a>')
    body.append('<a class="reflink" href="#glossary">Glossary</a>')
    body.append("</nav>\n")

    body.append('<div class="callout"><b>If you only remember three things:</b> '
                '(1) every run is client-only - an HTTP client can never see '
                'server CPU/RAM, and v2 never estimates it (archived v1 runs '
                'aside); (2) every run produces one '
                '<code>results.json</code> from <code>raw.jsonl</code> + '
                'self-monitor samples, and nothing is invented; (3) the language '
                'comparison and the HTTP load test answer <em>different '
                'questions</em> - never compare their numbers.</div>\n')
    body.append('<div class="legend"><b>Diagram legend:</b> solid box = always '
                'on, dashed box = optional / best effort, blue = HTTP path, '
                'violet = target-side / optional instrumentation read path, '
                'green = output artifact, amber = caveat or decision, red = '
                'integrity gate or a hard "no".</div>\n')

    for i, (name, title, desc, notes) in enumerate(INDEX, 1):
        svg = (HERE / name).read_text(encoding="utf-8").strip()
        svg = namespace_svg(svg, i)
        dev = ('<div class="devnotes"><h3>Developer notes - %s</h3><ul>%s</ul></div>'
               % (X(title), "".join("<li>%s</li>" % fmt(n) for n in notes)))
        # Each SVG already draws its own title, so the section adds only a
        # one-line caption + notes (avoids a duplicated heading).
        body.append('<section id="d%d"><p>%s</p>\n%s\n%s\n</section>\n'
                    % (i, fmt(desc), svg, dev))

    ref = []
    ref.append('<h3>1. Commands you will actually use</h3>')
    ref.append(table(("Command", "What it does"), COMMANDS))
    ref.append('<h3>2. Module map</h3>')
    ref.append(table(("Path", "Responsibility"), MODULES))
    ref.append('<h3>3. Config keys that matter</h3>')
    ref.append(table(("Key", "Meaning"), CONFIG_KEYS))
    ref.append('<h3>4. The v2 result document</h3>')
    ref.append(table(("Field", "Contents"), RESULT_FIELDS))
    ref.append('<h3>5. SLOs graded by <code>grading.grade()</code></h3>')
    ref.append(table(("ID", "Check", "Threshold / source"), SLOS))
    ref.append('<h3>6. Artifacts written per run</h3>')
    ref.append(table(("File", "Contents"), ARTIFACTS))
    ref.append('<h3>7. Web API surface</h3>')
    ref.append(table(("Endpoint", "Purpose"), API))
    ref.append('<h3>8. Language benchmark contract</h3>')
    ref.append(table(("Workload", "Pinned constants", "Expected checksum", "Exercises"),
                     WORKLOADS))
    ref.append('<p>Every variant prints <code>RESULT &lt;a&gt; &lt;b&gt;</code> '
               'and <code>TIME_US &lt;n&gt;</code>. Timing is in-process '
               '(startup excluded); the median of <code>repeats</code> runs is '
               'reported with full dispersion (stdev, CV%, IQR, 95% CIs); any '
               'checksum mismatch rejects the variant. 14 languages are '
               'detected from <code>comparison_sources/</code>; missing '
               'toolchains are skipped, never faked.</p>')
    ref.append('<h3>9. Extension seams</h3>')
    ref.append(table(("When you want...", "Change this"), SEAMS))
    body.append(ref_section(
        "ref", "Developer reference",
        "A lookup sheet for the code behind the pictures. Paths are relative to "
        "the repository root; all details are taken from the current source, "
        "not from memory.",
        "".join(ref)))

    inv = ('<ol class="inv">' + "".join(
        "<li><strong>%s</strong> - %s</li>" % (fmt(k), fmt(v))
        for k, v in INVARIANTS) + '</ol>')
    body.append(ref_section(
        "invariants", "Invariants & gotchas",
        "Rules that are easy to break and expensive to debug. Treat these as "
        "the project's contract.",
        inv))
    body.append(ref_section(
        "glossary", "Glossary",
        "Terms that appear in results, reports and code reviews.",
        table(("Term", "Meaning"), GLOSSARY)))

    body.append('<footer>Generated by <code>docs/diagrams/generate.py</code> - '
                'edit the generator, not this file. Standard library only; '
                'regenerate with <code>python docs/diagrams/generate.py</code>.'
                '</footer>\n</main></body>\n</html>\n')
    path = HERE / "index.html"
    path.write_text(head + "".join(body), encoding="utf-8", newline="\n")
    print("wrote %-34s %6d bytes" % ("index.html", path.stat().st_size))


def main():
    d01()
    d02()
    d03()
    d04()
    d05()
    d06()
    build_index()
    print("\n6 diagrams + index.html written to %s" % HERE)


if __name__ == "__main__":
    main()

