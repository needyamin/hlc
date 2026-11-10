#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate the project's core architecture diagrams as standalone SVGs.

    python docs/diagrams/generate.py

Why a generator instead of hand-edited SVG: every box, arrow and colour is
declared once here, so the six diagrams stay visually consistent and can be
updated when the code changes. Standard library only -- no pip install, no
Mermaid CLI, no network.

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
from xml.sax.saxutils import escape as X

HERE = os.path.dirname(os.path.abspath(__file__))

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
        path = os.path.join(HERE, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(self.parts) + "\n")
        print("wrote %-34s %6d bytes" % (name, os.path.getsize(path)))


# ---------------------------------------------------------------------------
# 01 - system context / the measurement boundary
# ---------------------------------------------------------------------------
def d01():
    c = Canvas(1200, 700, "01 - System context",
               "Where every number comes from - and the one boundary an external client cannot cross.")
    c.lane(36, 104, 520, 486, "WINDOWS HOST  |  stdlib Python 3.10+  |  your machine")
    c.lane(644, 104, 520, 486, "DOCKER DESKTOP / WSL2 LINUX VM  |  containers you control", "violet")

    c.box(60, 150, 270, 50, "cli.py  -  headless", "blue",
          "one run -> runs/<run-id>/ artifacts", fs=12.5)
    c.box(60, 212, 270, 50, "server.py + ui/app.js", "blue",
          "web UI :8787 - polls status at 1 Hz", fs=12.5)
    c.box(60, 290, 270, 58, "engine.Benchmark", "teal",
          "N worker threads send HTTP requests", fs=12.5)
    c.box(60, 368, 270, 58, "samplers.SamplerSet", "teal",
          "cgroup - procfs - docker stats - health - mysql", fs=12.5)
    c.box(60, 452, 270, 58, "grading.grade -> report.build", "gray",
          "S1-S9 - verdict - self-contained HTML", fs=12.5)
    c.note(60, 528, 270, 46, "No pip install. No WebSockets. No third-party JS.",
           "gray", 10.5)

    c.chip(360, 300, 170, 34, "HTTP client", "blue", 11)
    c.chip(360, 380, 170, 34, "docker exec / stats", "violet", 11)
    c.note(360, 440, 170, 68,
           "config.json\nprofiles, phases,\nguardrails", "gray", 10.5)
    c.note(360, 522, 170, 52, "runtime_comparison.py\n5 language toolchains",
           "violet", 10.5)
    c.arrow([(330, 319), (360, 319)], "blue")
    c.arrow([(330, 397), (360, 397)], "violet", dashed=True)

    c.box(670, 150, 225, 62, "Target app container", "violet",
          "e.g. spid3rweb-app :8000", fs=12.5)
    c.box(670, 230, 225, 62, "cgroup v2 files", "violet",
          "memory.stat - cpu.stat - io.stat - *.pressure", fs=12.5, subfs=9.5)
    c.box(670, 310, 225, 62, "/proc/<pid> + docker stats", "violet",
          "RSS - threads - restarts - OOMKilled", fs=12, subfs=9.5)
    c.box(670, 390, 225, 62, "MySQL (optional)", "gray",
          "mysql collector", fs=12)
    c.box(915, 150, 225, 62, "Container limits", "gray",
          "--cpus - --memory - --cpuset-cpus", fs=12)
    c.box(915, 230, 225, 62, "Runtime adapter (optional)", "gray",
          "app /__hlc/runtime JSON endpoint", fs=12)
    c.box(915, 310, 225, 62, "Headless Chrome (optional)", "gray",
          "CDP: FCP / LCP / JS heap", fs=12)
    c.box(915, 390, 225, 62, "Spid3r 1.0.0 binary", "violet",
          "in-Docker language benchmark", fs=12)

    # network boundary: dashed vertical line separating what a client can see
    c.parts.append(
        f'<line x1="600" y1="118" x2="600" y2="580" stroke="{PAL["red"][1]}" '
        f'stroke-width="2" stroke-dasharray="8 6"/>')
    c.arrow([(530, 317), (578, 317), (578, 181), (670, 181)], "blue")
    c.arrow([(530, 397), (622, 397), (622, 261), (670, 261)], "violet",
            dashed=True)

    c.note(670, 470, 470, 100,
           "docker_lab  = both arrows live  ->  visibility: full_server\n"
           "live_blackbox = blue arrow only  ->  client_only  ->  NOT comparable\n"
           "An external HTTP client can NEVER reconstruct server CPU, RAM or OOM events.",
           "amber", 11, title="What this means")
    c.chip(490, 592, 220, 26, "NETWORK BOUNDARY", "red", 11)
    c.legend(632, [("Blue = HTTP requests / responses (always)", "blue"),
                   ("Violet = docker exec / docker stats (docker_lab)", "violet"),
                   ("Always on", "teal"),
                   ("Optional", "gray")])
    c.footer("Sources: heavyload/engine.py, samplers.py, collectors/, docker_probe.py, server.py, docs/benchmark-modes.md")
    c.save("01-system-context.svg")


# ---------------------------------------------------------------------------
# 02 - module architecture
# ---------------------------------------------------------------------------
def d02():
    c = Canvas(1200, 840, "02 - Module architecture",
               "Three entry points, one orchestrator, pluggable collectors, one immutable result document.")
    c.box(60, 106, 240, 54, "cli.py", "blue", "headless runs + campaign / compare", fs=13)
    c.box(330, 106, 240, 54, "server.py", "blue", "HTTP API + static web UI", fs=13)
    c.box(600, 106, 240, 54, "selftest.py", "blue", "end-to-end regression", fs=13)
    c.note(870, 106, 294, 54,
           "All three converge on the SAME runner.", "gray", 11)

    c.box(330, 190, 510, 62, "heavyload/runner.run_benchmark()", "teal",
          "preflight (6 checks) - resolve container - create run dir - orchestrate", fs=14)
    for x in (180, 450, 720, 990):
        c.arrow([(585, 252), (585, 272), (x, 272), (x, 292)], "gray")
    c.arrow([(180, 160), (180, 176), (585, 176), (585, 190)], "gray")
    c.arrow([(450, 160), (450, 190)], "gray")
    c.arrow([(720, 160), (720, 176), (585, 176)], "gray")

    c.box(60, 292, 240, 72, "engine.Benchmark", "teal",
          "worker pool - 8 phases - guardrails", fs=12.5)
    c.box(330, 292, 240, 72, "samplers.SamplerSet", "teal",
          "thread orchestration + snapshots", fs=12.5)
    c.box(600, 292, 240, 72, "adapters/*", "gray",
          "per-language runtime metrics (optional)", fs=12.5, dashed=True)
    c.box(870, 292, 294, 72, "browser/cdp.py", "gray",
          "headless Chrome page metrics (optional)", fs=12.5, dashed=True)

    c.box(60, 392, 240, 60, "raw.jsonl", "blue",
          "one JSON line per request", fs=12.5)
    c.box(330, 392, 240, 60, "collectors/", "violet",
          "cgroup - procfs - docker_stats - health - mysql", fs=12, subfs=9.5)
    c.box(600, 392, 564, 60, "series.runtime_series / series.browser_series", "gray",
          "attached only when the optional adapter / browser is available", fs=12.5, dashed=True)
    for x in (180, 450, 882):
        c.arrow([(x, 364), (x, 392)], "gray")
    c.arrow([(720, 364), (720, 392)], "gray", dashed=True)
    c.arrow([(1017, 364), (1017, 392)], "gray", dashed=True)

    c.box(60, 480, 1104, 54, "results{}  -  metrics.http + metrics.resources + series  (assembled by engine.run())",
          "teal", fs=13)
    for x in (180, 450, 882, 1017):
        c.arrow([(x, 452), (x, 480)], "gray", dashed=(x > 800))

    c.box(60, 562, 340, 64, "grading.grade()  S1-S9", "amber",
          "findings - health score 0-100 - GO / NO-GO", fs=13)
    c.box(430, 562, 340, 64, "schema.ensure_current()", "violet",
          "v2 schema - additive v1 migration - visibility", fs=12.5)
    c.box(800, 562, 364, 64, "runtime_comparison.run()", "violet",
          "cross-language compute (runs AFTER the HTTP schedule)", fs=12.5)
    c.arrow([(210, 534), (210, 562)], "gray")
    c.arrow([(600, 534), (600, 562)], "gray")
    c.arrow([(982, 534), (982, 562)], "gray")

    c.box(60, 654, 340, 60, "report.build() -> report.html", "green",
          "self-contained: inline SVG, no JS / CDN", fs=12.5)
    c.box(430, 654, 340, 60, "results.json + stage2_verdict.json", "green",
          "+ status.json for live UI KPIs", fs=12.5)
    c.box(800, 654, 364, 60, "runtime_comparison.json / .html + sources/", "green",
          "also linked from report.html section 11", fs=12)
    for x in (230, 600, 982):
        c.arrow([(x, 626), (x, 654)], "gray")
    c.note(60, 732, 1104, 48,
           "server.py serves these artifacts from /runs/<id>/<file> (SAFE_ARTIFACTS whitelist); ui/app.js polls /api/status and /api/log at 1 Hz.",
           "blue", 11, title="Live UI path")
    c.footer("Sources: heavyload/runner.py, engine.py, grading.py, schema/, report.py, paths.py, runtime_comparison.py, server.py")
    c.save("02-architecture.svg")


# ---------------------------------------------------------------------------
# 03 - one run, start to finish
# ---------------------------------------------------------------------------
def d03():
    c = Canvas(1200, 780, "03 - One run, start to finish",
               "The eight orchestration steps (top) and the eight HTTP phases of the smoke profile (bottom).")
    steps = [
        ("1  Preflight", "6 read-only checks:\nURL, health, Docker,\ncontainer, cgroup, RAM", "blue"),
        ("2  Set up run", "runs/run-<stamp>/\nwrite config.json\n+ preflight.json", "blue"),
        ("3  Instrument", "start sampler threads\n+ in-container\ncollector process", "teal"),
        ("4  Drive load", "8 phases, guardrails,\nper-request raw.jsonl\n+ health probes", "teal"),
        ("5  Aggregate", "stop workers,\nlatency percentiles,\nresource series", "teal"),
        ("6  Adjudicate", "grade S1-S9,\nschema v2 normalize,\nverdict + score", "amber"),
        ("7  Compare", "runtime_comparison\n(FAILURE-SAFE, after\nthe HTTP result)", "violet"),
        ("8  Publish", "results.json,\nreport.html, status.json\n+ comparison files", "green"),
    ]
    xs = [60 + i * 139.5 for i in range(8)]
    for i, (label, sub, tone) in enumerate(steps):
        c.box(xs[i], 112, 126, 104, label, tone, sub, fs=11, subfs=9)
        if i:
            c.arrow([(xs[i] - 13.5, 164), (xs[i], 164)], "gray", width=1.6)

    c.text(60, 254, "SMOKE PROFILE  -  8 phases, ~33 s total, peak C=20 (quick ~3.5 min / standard ~24 min share the same shape)",
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
          "error-rate ceiling, OOM / restart, host free-RAM floor -> partial results are still written", fs=12.5, subfs=10)
    c.box(620, 412, 544, 66, "Step 7 cannot lose step 4", "green",
          "comparison runs AFTER aggregation + grading; any exception becomes a warning in results.json", fs=12.5, subfs=10)

    notes = [
        ("01 idle", "capture baseline CPU / anon memory so net values can be derived"),
        ("02-03", "warm the target, then step concurrency up instead of hammering cold"),
        ("04 sustained", "the only phase used for the throughput SLO (S4)"),
        ("05 spike", "short burst to expose queueing and tail-latency blow-ups"),
        ("07 cooldown", "zero load; S6 checks memory is actually released"),
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
    c.footer("Sources: heavyload/config.py (preset_phases), engine.py (_run_phase / _guard_check), runner.py (run_benchmark), grading.py (S1-S9)")
    c.save("03-run-lifecycle.svg")


# ---------------------------------------------------------------------------
# 04 - data flow: raw bytes -> verdict
# ---------------------------------------------------------------------------
def d04():
    c = Canvas(1200, 740, "04 - Data flow: raw bytes to verdict",
               "Every published number is traceable back to a raw line, a cgroup sample or a verified timing.")
    c.text(60, 112, "1  RAW INPUT", 11.5, PAL["blue"][2], 700)
    c.text(340, 112, "2  AGGREGATION", 11.5, PAL["teal"][2], 700)
    c.text(620, 112, "3  GRADE + NORMALISE", 11.5, PAL["amber"][2], 700)
    c.text(920, 112, "4  OUTPUTS", 11.5, PAL["green"][2], 700)

    c.box(60, 128, 240, 88, "raw.jsonl", "blue",
          "t, phase, status, connect /\nTTFB / body, bytes, error", fs=13, subfs=10)
    c.box(60, 240, 240, 88, "sampler series", "violet",
          "cgroup anon / peak, cpu.stat,\npids, OOM, per-proc RSS", fs=12.5, subfs=10)
    c.box(60, 352, 240, 88, "health + browser series", "gray",
          "1 Hz probes, mysql stats,\nCDP page metrics (optional)", fs=12, subfs=10, dashed=True)
    c.box(60, 464, 240, 88, "TIME_US per variant", "violet",
          "in-process compute, N repeats,\nchecksum per run", fs=12.5, subfs=10)

    c.box(340, 128, 240, 88, "_aggregate_requests()", "teal",
          "RPS, p50/p90/p95/p99/max,\nerror %, bytes, per-phase", fs=12.5, subfs=10)
    c.box(340, 240, 240, 88, "_aggregate_samples()", "teal",
          "timeline bins, anon peak,\ncpu_pct_core, oom_kill_events", fs=12.5, subfs=10)
    c.box(340, 352, 240, 88, "metrics.http + metrics.resources", "blue",
          "v2 schema, each field tagged\nwith its visibility", fs=12.5, subfs=10)
    c.box(340, 464, 240, 88, "checksum gate + median", "amber",
          "reject any RESULT mismatch;\nmedian of N in-process runs", fs=12.5, subfs=10)

    c.box(620, 128, 260, 160, "grading.grade()", "amber",
          "S1 errors - S2/S3 tail - S4 RPS\nS5 growth - S6 cooldown\nS7 availability - S8 OOM\nS9 schedule completion", fs=13, subfs=10)
    c.box(620, 310, 260, 90, "adjudication", "amber",
          "verdict GO / NO-GO / INSUFFICIENT\nhealth score 0-100 + findings", fs=12.5, subfs=10)
    c.box(620, 422, 260, 130, "schema.ensure_current()", "violet",
          "sets benchmark_mode, visibility,\nfingerprint, comparability fields.\n\nAdditive v1 migration: no old key\nis deleted, renamed or coerced.", fs=12.5, subfs=10)

    c.box(920, 128, 244, 76, "results.json", "green",
          "full v2 result document", fs=12.5)
    c.box(920, 218, 244, 76, "report.html", "green",
          "self-contained, 10-11 sections,\ninline SVG charts, no JS", fs=12.5, subfs=10)
    c.box(920, 308, 244, 76, "stage2_verdict.json", "green",
          "verdict only, for automation", fs=12.5, subfs=10)
    c.box(920, 398, 244, 76, "status.json", "green",
          "live KPIs -> /api/status -> UI", fs=12.5, subfs=10)
    c.box(920, 488, 244, 76, "runtime_comparison.*", "green",
          "json + html + sources/ + report\nsection 11", fs=12, subfs=10)

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
           "server metrics come from cgroup / procfs samples; language timings come from a program\n"
           "that had to print the exact expected checksum. Missing sources degrade to \"unavailable\".",
           "blue", 11, title="Traceability rule")
    c.footer("Sources: heavyload/engine.py (_aggregate_*), grading.py, schema/metrics.py, schema/migrate.py, report.py, runtime_comparison.py")
    c.save("04-data-flow.svg")


# ---------------------------------------------------------------------------
# 05 - modes + comparability gate
# ---------------------------------------------------------------------------
def d05():
    c = Canvas(1200, 760, "05 - Benchmark modes and the comparability gate",
               "Which mode you get depends on what you control - and only one mode may be ranked against another run.")
    c.text(60, 110, "PICK THE MODE FROM THE VANTAGE POINT YOU ACTUALLY HAVE", 11.5, MUTED, 700)
    c.chip(60, 126, 340, 34, "Docker container you control", "teal", 11)
    c.chip(430, 126, 340, 34, "You own the server, no container", "amber", 11)
    c.chip(800, 126, 364, 34, "Public site / no server access", "red", 11)

    cards = [
        (60, "docker_lab", "teal", "visibility: full_server",
         ["HTTP latency, status, errors, RPS, bytes",
          "cgroup v2 anon / peak / cpu.stat / io.stat",
          "per-process RSS, threads, OOM kills, restarts",
          "optional runtime internals + browser CDP",
          "pinned --cpus / --memory / --cpuset-cpus"],
         "COMPARABLE", "green"),
        (430, "instrumented_live", "amber", "visibility: server_via_adapter",
         ["HTTP client metrics (as above)",
          "Prometheus / cAdvisor / APM metrics",
          "collected via a future Collector adapter",
          "no cgroup files without a container",
          "NOT BUILT IN THIS ROUND"],
         "FUTURE ADAPTER", "amber"),
        (800, "live_blackbox", "red", "visibility: client_only",
         ["HTTP latency, status, errors, RPS, bytes",
          "optional browser FCP / LCP / JS heap",
          "resources.* -> unavailable",
          "series.cgroup_series -> unavailable",
          "never infer server CPU / RAM from latency"],
         "NOT COMPARABLE", "red"),
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
          "same workload_class  +  visibility != client_only", fs=12.5, subfs=11)
    c.arrow([(600, 580), (650, 580)], "gray", width=2)
    c.box(650, 522, 250, 52, "FAIR COMPARISON", "green", fs=12.5)
    c.box(920, 522, 244, 52, "NOT COMPARABLE", "red", fs=12.5)
    c.chip(980, 484, 164, 24, "any rule fails", "red", 10)
    c.arrow([(1062, 508), (1062, 522)], "red", width=1.6, dashed=True)
    c.note(650, 590, 514, 48,
           "Otherwise: comparable=False + a reason; no ranking, no winner, no misleading chart.",
           "amber", 10.5)
    c.note(60, 644, 1104, 60,
           "cgroup files live in the WSL2 Linux VM and are reached by docker exec; target, VM and generator share physical cores.\n"
           "Pin cores, subtract the idle baseline, and always keep environment.fingerprint.host_shared_cpu=true.",
           "gray", 10.5, title="Do not quote raw lab CPU% as absolute capacity")
    c.footer("Sources: heavyload/schema/metrics.py (can_compare, VISIBILITY_LEVELS), compare.py, report_compare.py, config.py, docs/benchmark-modes.md")
    c.save("05-benchmark-modes.svg")


# ---------------------------------------------------------------------------
# 06 - the runtime comparison pipeline
# ---------------------------------------------------------------------------
def d06():
    c = Canvas(1200, 720, "06 - Language runtime comparison pipeline",
               "Three pinned workloads, identical algorithms in five languages, one checksum gate, zero simulated numbers.")
    steps = [
        (60, 130, "1  PIN THE WORKLOAD", "violet",
         ["A  prime count, N=50,000",
          "     RESULT 5133 121013308",
          "B  JSON round-trip, 200 x 600",
          "     RESULT 249868800 9163",
          "C  map lookups, 64 x 100,000",
          "     RESULT 44432313 100000"],
         "Change a constant -> checksum changes -> run is rejected."),
        (430, 130, "2  GENERATE + BUILD", "blue",
         ["Sources: Spid3r, PHP, Python, Node, Rust",
          "",
          "Variants: tree-walk, VM, VM --no-opt,",
          ".matrixc exec, build --release,",
          "rustc -O, and the real binary copied into",
          "the running Docker container."],
         "Missing toolchain -> skipped with a warning, never faked."),
        (800, 130, "3  MEASURE", "teal",
         ["Run every variant N times (default 5).",
          "Each program prints RESULT and TIME_US.",
          "",
          "Timing is IN-PROCESS: process startup,",
          "compilation and docker exec overhead are",
          "excluded. Report the median compute time."],
         "Spid3r has only now_ms(), so workloads are sized >= 28 ms."),
        (800, 400, "4  CHECKSUM GATE", "red",
         ["Observed RESULT must be identical across",
          "every repeat AND exactly match EXPECTED.",
          "",
          "Mismatch -> variant REJECTED and never",
          "published. Verified rows -> slowdown ratios",
          "vs PHP and Python + geometric mean."],
         "This is the anti-fake invariant."),
        (430, 400, "5  SUMMARISE", "amber",
         ["runtime_comparison.json  (all repeats,",
          "  min/max, versions, ratios)",
          "runtime_comparison.html (self-contained)",
          "sources/ (exact programs per language)",
          "results.json <- compact summary",
          "report.html <- section 11"],
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
                   ("output", "green"), ("optional / best effort", "gray")])
    c.footer("Sources: heavyload/runtime_comparison.py, runner.py, report.py (_runtime_section), cli.py, config.json")
    c.save("06-runtime-comparison.svg")


# (file, title, one-line caption, developer notes). The notes are the
# "where do I put my hands on the keyboard" companion to each picture.
INDEX = [
    ("01-system-context.svg", "01 - System context",
     "Two read paths, one network boundary. An external HTTP client can never "
     "see server CPU, RAM or OOM events.",
     [
         "`heavyload/http_client.py` owns `make_connection()` and one-shot "
         "`probe()`; the load generator never uses anything else to reach the target.",
         "`docker_probe.py` provides `list_containers()`, `resolve_container()`, "
         "`cgroup_caps()` and `docker_available()` - all via the Docker CLI, no SDK.",
         "`collectors/cgroup.py` runs ONE Python process inside the container and "
         "reads `memory.stat anon`, `memory.current`, `memory.peak`, `cpu.stat`, "
         "`io.stat`, `*.pressure` and `pids.current` with plain `open()`.",
         "If the container has no `python3`, `samplers.py` falls back to "
         "`collector_mode=legacy_shell` (forks grep/awk/sleep); idle CPU can read "
         "8-15% instead of ~0.85%. Always check `environment.fingerprint.collector_mode`.",
         "On Windows the cgroup files live in the WSL2 VM: reachable only via "
         "`docker exec`. Target, VM and load generator share physical cores.",
     ]),
    ("02-architecture.svg", "02 - Module architecture",
     "Three entry points, one runner, pluggable collectors, one result "
     "document. Dashed boxes are optional.",
     [
         "`runner.run_benchmark(cfg, root, log, preflight_result, bench_holder)` "
         "is the SINGLE orchestrator - `cli.py` and `server.py /api/run/start` "
         "both call it, so a feature added there works in both UIs.",
         "`engine.Benchmark.run()` returns the `results{}` dict; `grading.grade()` "
         "adds `adjudication`, `schema.ensure_current()` adds v2 identity fields.",
         "`runtime_comparison.run()` is wrapped in try/except: any exception "
         "becomes `status=error` + a warning in `results.json`, never a lost run.",
         "`report.build()` emits one self-contained HTML (inline SVG charts, no JS); "
         "`paths.SAFE_ARTIFACTS` is the whitelist used by `server.py` for "
         "`/runs/<run-id>/<artifact>`.",
         "UI liveness: `server.py` reads `status.json`; `ui/app.js` polls "
         "`/api/status` and `/api/log` at 1 Hz. No WebSockets, no framework.",
     ]),
    ("03-run-lifecycle.svg", "03 - One run, start to finish",
     "Eight orchestration steps and the eight HTTP phases. The language "
     "comparison deliberately runs last.",
     [
         "`config.preset_phases(name)` returns the 8 phases; profiles only change "
         "durations (smoke ~33 s, quick ~3.5 min, standard ~24 min).",
         "`engine._run_phase()` drives one phase, `_worker()` issues requests, "
         "`_writer()` appends one JSON line per request to `raw.jsonl`, "
         "`_guard_check()` can set `_set_abort(reason)`.",
         "Guardrails come from `config.guardrails`: `max_anon_mib`, "
         "`min_host_free_gb`, `max_error_rate_pct`, `max_consecutive_failures`. "
         "On abort, `cooldown_s` + `reprobe_s` still run and partial results are written.",
         "Step 7 sits after `ensure_current()` on purpose - the HTTP result is "
         "already aggregated and graded before a single language program is spawned.",
     ]),
    ("04-data-flow.svg", "04 - Data flow: raw bytes to verdict",
     "Every published number traces back to a raw line, a cgroup sample or a "
     "verified timing.",
     [
         "`engine._aggregate_requests()` -> `metrics.http` (see `schema/metrics.py "
         "empty_http()`); `_aggregate_samples()` -> `metrics.resources` "
         "(`empty_resources()`). Latency keys: p50/p90/p95/p99/max/mean.",
         "`grading.grade()` registers S1-S9, findings, `health_score` (0-100) and "
         "a GO / NO-GO / INSUFFICIENT verdict.",
         "`schema/migrate.py` performs an ADDITIVE v1 -> v2 migration: no v1 key "
         "is deleted, renamed or coerced, so archived reports still render.",
         "Writes go through `util.atomic_json()`; `report.py` draws inline SVG "
         "charts from the same dict - no second data source, no estimates.",
     ]),
    ("05-benchmark-modes.svg", "05 - Modes and the comparability gate",
     "Only runs measured from the same vantage point may be ranked.",
     [
         "`schema/metrics.py:can_compare(a, b)` requires equal `benchmark_mode`, "
         "`visibility`, `schema_version` and `workload_class`, then rejects any "
         "run whose `visibility == \"client_only\"`.",
         "`VISIBILITY_LEVELS = (\"full_server\", \"client_only\", "
         "\"server_via_adapter\")`; `benchmark_mode=auto` picks `docker_lab` when "
         "`resolve_container()` finds a container.",
         "`compare.py` sets `comparable=False` + a reason and still returns raw "
         "rows; `report_compare.py` prints NOT COMPARABLE instead of a leaderboard.",
         "`instrumented_live` is a declared seam, not an implementation: add a "
         "`collectors/base.Collector` subclass for your Prometheus / APM endpoint.",
     ]),
    ("06-runtime-comparison.svg", "06 - Language runtime comparison",
     "Pinned workloads, real variants, one checksum gate, no simulated "
     "numbers.",
     [
         "`runtime_comparison.py`: pinned `PRIME_N`, `JSON_ROWS/ROUNDS`, "
         "`MAP_KEYS/ROUNDS` and the `EXPECTED` checksum dict. Change a constant "
         "and you MUST recompute EXPECTED or every variant is rejected.",
         "Each generated program prints exactly two lines: `RESULT <a> <b>` and "
         "`TIME_US <n>`; `_parse_output()` enforces that contract.",
         "`_variants()` builds tree-walk, VM, VM --no-opt, `.matrixc` exec, "
         "`build --release`, rustc -O, Node, PHP, Python AND the real binary "
         "copied into the container with `docker cp`.",
         "`detect_tools()` probes toolchains; missing ones are skipped with a "
         "warning. `_cleanup_container()` removes `/tmp/hlc_rt_*` afterwards.",
         "Spid3r has only `now_ms()` (1 ms resolution), so workloads are sized "
         "well above 28 ms. Its JSON builtin is `json_write` / `json_parse`, "
         "NOT `json_encode`.",
     ]),
]

# ---------------------------------------------------------------------------
# Developer reference content for index.html
# ---------------------------------------------------------------------------
COMMANDS = [
    ("`python server.py --open`", "Web UI on 127.0.0.1:8787; polls status at 1 Hz."),
    ("`python cli.py --profile smoke`", "One headless run (~33 s) + auto language comparison."),
    ("`python cli.py --runtime-comparison`", "Language comparison ONLY - no HTTP traffic, store may be down."),
    ("`python cli.py --no-runtime-comparison`", "Skip the comparison for this run."),
    ("`python cli.py --runtime-repeats 1`", "Shorten the comparison while iterating."),
    ("`python cli.py --preflight-only`", "Run the 6 read-only preflight checks and exit."),
    ("`python cli.py --campaign benchmarks/lang-runtime.json`", "Phase 2 multi-site campaign."),
    ("`python cli.py --compare run-a run-b --output out/`", "Compare / export existing runs."),
    ("`python selftest.py`", "End-to-end regression (45 checks, ~35 s)."),
    ("`python -m unittest discover -s tests -v`", "Unit suite."),
    ("`python docs/diagrams/generate.py`", "Regenerate these six SVGs + this page."),
]

MODULES = [
    ("`heavyload/paths.py`", "ROOT, `runs_dir()`, `SAFE_ARTIFACTS`; honours `config.output_dir`."),
    ("`heavyload/config.py`", "DEFAULTS, 3 profiles, 8 phases, normalize/validate/save."),
    ("`heavyload/runner.py`", "`preflight()` + `run_benchmark()` orchestration."),
    ("`heavyload/engine.py`", "`Benchmark`: workers, guardrails, `raw.jsonl`, aggregation."),
    ("`heavyload/http_client.py` / `latency.py`", "Connection factory, probes, `Stopwatch` timing."),
    ("`heavyload/samplers.py` + `collectors/`", "Sampler threads; cgroup / procfs / docker_stats / health / mysql."),
    ("`heavyload/docker_probe.py`", "Docker CLI wrappers, container resolution, cgroup capability probe."),
    ("`heavyload/grading.py`", "SLOs S1-S9, findings, health score, verdict."),
    ("`heavyload/schema/`", "v2 metric contract, visibility, `can_compare`, additive v1 migration."),
    ("`heavyload/report.py`", "Single self-contained HTML report (section 11 = comparison)."),
    ("`heavyload/runtime_comparison.py`", "Cross-language workload generator, runner and HTML report."),
    ("`heavyload/adapters/`, `browser/`", "Optional runtime metrics and stdlib CDP page metrics."),
    ("`heavyload/compare.py`, `campaign.py`, `report_compare.py`", "Phase 2 rankings + comparability guard."),
    ("`server.py`, `ui/app.js`", "HTTP API + vanilla-JS single-page UI."),
]

CONFIG_KEYS = [
    ("`target_url`, `paths[]`, `health_path`", "What is hit and how it is health-checked."),
    ("`container`, `mysql_container`", 'Empty = auto-resolve from the published port / no MySQL sampler.'),
    ("`benchmark_mode`", "`auto` | `docker_lab` | `live_blackbox` | `instrumented_live`."),
    ("`profile` / `phases[]`", "`smoke` / `quick` / `standard`, or an explicit 8-phase schedule."),
    ("`max_concurrency`, `request_timeout_s`, `keep_alive`", "Load-generator envelope."),
    ("`sample_period_s`, `stats_period_s`", "cgroup and docker-stats sampling cadence."),
    ("`guardrails.*`", "Abort thresholds: anon MiB, host free GB, error rate, consecutive failures."),
    ("`slo.*`", "Thresholds graded by S1-S9 (error %, p95, p99, min RPS, memory growth...)."),
    ("`fairness.*`", "cpuset pinning, repeats, seed, median aggregation for campaigns."),
    ("`runtime_comparison.*`", "enabled / repeats / container / include_native / workloads / timeout_s."),
    ("`browser`, `runtime_adapter`", "Opt-in CDP page metrics and per-language runtime endpoint."),
    ("`output_dir`", "Where run directories are written (default `runs`)."),
]

RESULT_FIELDS = [
    ("`benchmark_mode`, `visibility`, `schema_version`", "Identity + comparability triple checked by `can_compare()`."),
    ("`metrics.http`", "`rps`, `error_pct`, `latency_ms{p50,p90,p95,p99,max,mean}`, `bytes_total`, `connect_ms`, `ttfb_ms`, `total_ms`."),
    ("`metrics.resources`", "`cpu_pct_core`, `mem_anon_mib`, `mem_peak_bytes`, `oom_kill_events`, `pids_current`, `psi_avg10`, io bytes."),
    ("`metrics.runtime`, `metrics.browser`", "Optional adapter / CDP fields; `null` when unavailable."),
    ("`series.*`", "cgroup, procfs, health, mysql and timeline sample arrays."),
    ("`environment.fingerprint`", "`collector_mode`, `generator_pinned`, `host_shared_cpu`, CPU model, seed."),
    ("`adjudication`", "`verdict`, `health_score`, `slo[]`, `findings[]`."),
    ("`runtime_comparison`", "Compact summary: status, verdict, slowdown ratios, artifact names."),
]

SLOS = [
    ("S1", "Request error rate", "`slo.max_error_pct` (default 0.1%)"),
    ("S2", "Successful-200 p95 latency", "`slo.max_p95_ms` (500 ms)"),
    ("S3", "Successful-200 p99 latency", "`slo.max_p99_ms` (2000 ms)"),
    ("S4", "Sustained throughput", "`slo.min_rps` (0 => informational)"),
    ("S5", "Bounded anonymous-memory growth", "`slo.max_memory_growth_mib` (256 MiB)"),
    ("S6", "Memory released after zero-load cooldown", "`slo.max_retained_pct` (120%)"),
    ("S7", "Availability probe success", "`slo.min_health_success_pct` (99%)"),
    ("S8", "No OOM kill / restart", "from `memory.events` + `docker inspect`"),
    ("S9", "Full schedule completed", "no guardrail abort"),
]

ARTIFACTS = [
    ("`raw.jsonl`", "One JSON line per request - the ground truth for all HTTP metrics."),
    ("`results.json`", "Full v2 result document (metrics, series, adjudication)."),
    ("`report.html`", "Self-contained HTML report: 10 sections, +11 when comparison ran."),
    ("`stage2_verdict.json`", "Verdict only - for CI / automation."),
    ("`status.json`", "Live KPIs consumed by `/api/status` during a run."),
    ("`config.json`, `preflight.json`", "Exact effective config + the 6 preflight checks for reproducibility."),
    ("`runtime_comparison.json`", "Every repeat, min/max, versions, checksums and ratios."),
    ("`runtime_comparison.html`, `sources/`", "Comparison report + the exact generated programs in 5 languages."),
]

API = [
    ("`/api/bootstrap`, `/api/status`, `/api/log`", "UI bootstrap + 1 Hz live state."),
    ("`/api/config`, `/api/preset`, `/api/containers`", "Read / update config, presets, container list."),
    ("`/api/preflight`, `/api/routes`, `/api/health`", "Read-only checks and route discovery."),
    ("`/api/run/start`, `/api/run/stop`", "Start / gracefully stop a run (same runner as CLI)."),
    ("`/api/reports`, `/runs/<id>/<artifact>`", "List reports; serve whitelisted artifacts."),
    ("`/api/campaign/*`, `/api/compare*`, `/api/export`", "Campaigns, comparison reports and CSV export."),
]

WORKLOADS = [
    ("A - prime", "`N = 50,000`", "`RESULT 5133 121013308`", "pure interpreter integer loops"),
    ("B - JSON", "`200 rows x 600 rounds`", "`RESULT 249868800 9163`", "`json_write` / `json_parse`, 9,163-char JSON"),
    ("C - map", "`64 keys x 100,000 lookups`", "`RESULT 44432313 100000`", "string-keyed route / session lookups"),
]

SEAMS = [
    ("New server metric source", "Subclass `collectors/base.Collector`; `samplers.SamplerSet` picks it up."),
    ("New language runtime internals", "Subclass `adapters/base.RuntimeAdapter` or expose `/__hlc/runtime`."),
    ("New benchmark profile", "Add a `*_phases()` function + `PRESETS` entry in `config.py`."),
    ("New workload in the language comparison", "Add `WORKLOAD_INFO`, `SOURCES[wid]`, `EXPECTED[wid]` in `runtime_comparison.py`."),
    ("New SLO", "Add one `add(...)` call in `grading.grade()` (S1-S9 live there)."),
    ("New result field", "Extend `schema/metrics.py` additively; never delete or rename a v1 key."),
]

INVARIANTS = [
    ("Stdlib only", "No pip install for the core. Optional extras (psutil, Playwright) are never required; "
     "the CDP client uses plain `socket`."),
    ("No collector fails a run", "Missing Docker, missing in-container `python3`, missing runtime endpoint and "
     "missing Chrome all degrade to `unavailable` labels."),
    ("Additive schema", "v1 keys are never deleted, renamed or coerced - archived reports keep rendering."),
    ("Visibility is explicit", "`can_compare()` refuses to rank a `client_only` run against a `docker_lab` run. "
     "The tool would rather say NOT COMPARABLE than print a wrong winner."),
    ("Instrumentation must not perturb", "One in-container Python process, no shell forks, deterministic stdin "
     "shutdown. Treat `collector_mode=legacy_shell` CPU numbers as inflated."),
    ("No simulated numbers", "Workload constants are pinned; a variant with a wrong checksum is REJECTED, "
     "never published; missing toolchains are skipped with a warning."),
    ("Compute speed is not HTTP throughput", "The language comparison measures in-process milliseconds; RPS / p99 "
     "come from the HTTP load test. Never compare the two."),
    ("Comparison is failure-safe", "It runs AFTER the HTTP schedule; an exception becomes a warning in "
     "`results.json` and the load result survives."),
    ("Git Bash / MSYS path trap", "Leading `/` args and `docker cp ...:/tmp/x` get rewritten to Windows paths. "
     "Set `MSYS_NO_PATHCONV=1` and `MSYS2_ARG_CONV_EXCL='*'` in subprocess env."),
    ("Shared cores on Windows", "Pin the target (`--cpuset-cpus`) and the generator "
     "(`fairness.cpuset_generator`), subtract the idle baseline, and keep "
     "`host_shared_cpu=true`. Never quote raw lab CPU% as absolute capacity."),
    ("Spid3r specifics", "Only `now_ms()` (1 ms resolution); JSON builtin is `json_write`, not `json_encode`. "
     "The deployed store still runs the tree-walk engine, the slowest of the measured variants."),
]

GLOSSARY = [
    ("visibility", "`full_server`, `client_only` or `server_via_adapter` - what vantage point the numbers came from."),
    ("client_only", "Only HTTP observations exist; server CPU/RAM fields are `unavailable`."),
    ("cgroup v2", "Linux kernel accounting files; `memory.stat anon` is the canonical raw memory metric."),
    ("TTFB", "Time to first byte; `latency.Stopwatch` splits connect / TTFB / body."),
    ("p99 / p95", "Tail latency percentiles over successful 200 responses."),
    ("health score", "0-100 score produced by `grading.grade()` from S1-S9."),
    ("collector_mode", "`python` (single in-container process) or `legacy_shell` (fallback, higher perturbation)."),
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
                '(1) an HTTP client can never see server CPU/RAM - that needs the '
                'Docker read path; (2) every run produces one '
                '<code>results.json</code> from <code>raw.jsonl</code> + cgroup '
                'samples, and nothing is estimated; (3) the language comparison '
                'and the HTTP load test answer <em>different questions</em> - '
                'never compare their numbers.</div>\n')
    body.append('<div class="legend"><b>Diagram legend:</b> solid box = always '
                'on, dashed box = optional / best effort, blue = HTTP path, '
                'violet = Docker / instrumentation read path, green = output '
                'artifact, amber = caveat or decision, red = integrity gate or a '
                'hard "no".</div>\n')

    for i, (name, title, desc, notes) in enumerate(INDEX, 1):
        svg = open(os.path.join(HERE, name), encoding="utf-8").read().strip()
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
               'reported; any checksum mismatch rejects the variant.</p>')
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
    path = os.path.join(HERE, "index.html")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(head + "".join(body))
    print("wrote %-34s %6d bytes" % ("index.html", os.path.getsize(path)))


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
