#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-language runtime comparison (Spid3r vs 13 baseline/reference runtimes).

This is the "test my language" feature. It runs *identical* checksum-verified
workloads in every available language runtime and writes, into the run
directory:

    runtime_comparison.json    full raw data (all repeats, min/max, versions,
                               measured peak memory + CPU time per variant)
    runtime_comparison.html    self-contained report (no JS, no CDN)
    sources/                   the exact generated programs, for provenance

Supported runtimes (detected automatically, skipped when absent):
    Spid3r, Python, Node/JavaScript, TypeScript, PHP, Ruby, Java, Kotlin,
    Go, C#/.NET, Swift, Rust, C, C++.

Design rules (deliberate, do not weaken):
  * Workload constants are PINNED. Changing them changes the checksum, and a
    run whose checksum does not match EXPECTED is rejected, not published.
  * Every variant prints "RESULT <a> <b>" and "TIME_US <n>". A variant whose
    RESULT differs is discarded. No simulated / estimated numbers, ever.
  * Timing is IN-PROCESS (startup excluded). Spid3r only has now_ms(), so all
    workloads are sized to run well above 1 ms.
  * Memory (peak working set) and CPU time of each variant are MEASURED by the
    OS via heavyload.procmetrics, or reported as unavailable - never guessed.
  * Any missing toolchain is skipped with a warning, never faked.
  * This module never raises into the caller; failures become status + warning.

Standalone use:
    from heavyload import runtime_comparison as RC
    data = RC.run(cfg, out_dir, log=log)
"""
from __future__ import annotations

import html
import json
import math
import os
import pathlib
import random
import re
import shutil
import statistics
import subprocess
import sys
import platform
import tempfile
import time
from datetime import datetime, timezone

from .util import atomic_json, cpu_model, utc
from . import util
from .comparison_sources import LANG_MODULES, module_by_key
from . import procmetrics

# v2 adds runtime_environment / config_meta flags and a "source" tag per
# variant row. Old v1 documents still render (every new field has a default),
# but variant counts and geomeans must never be compared across the boundary.
SCHEMA_VERSION = 2
EXE = ".exe" if os.name == "nt" else ""

DEFAULT_OPTIONS = {
    "enabled": True,
    "repeats": 5,
    "include_windows": True,   # installed host toolchains (checked by default)
    "include_native": True,
    "include_references": True,
    "write_sources": True,
    "timeout_s": 300,
    "build_timeout_s": 900,
    "workloads": ["prime", "json", "map", "text", "matmul"],
}

RUNTIME_ENVIRONMENT_LABELS = {
    "windows": "Host (installed runtimes, client-only)",
}

# Display line used in the HTML subtitle (derived from the registry).
_LANG_LINE = " / ".join(
    m.LABEL for m in LANG_MODULES if m.KEY != "matrix")

# Constants are pinned so results stay comparable between runs. If you change
# any of these you MUST recompute and update EXPECTED below.
PRIME_N = 50000
JSON_ROWS, JSON_ROUNDS = 200, 600
MAP_KEYS, MAP_ROUNDS = 64, 100000
TEXT_ROWS = 50000
MATMUL_N = 128

EXPECTED = {
    "prime": "RESULT 5133 121013308",
    "json": "RESULT 249868800 9163",
    "map": "RESULT 44432313 100000",
    "text": "RESULT 1295428 300465975",
    "matmul": "RESULT -1985 -40",
}

WORKLOAD_INFO = {
    "prime": {
        "id": "A",
        "title": "Prime counting (CPU / integer loops)",
        "constants": "N = 50,000 integers",
        "description": "Trial-division prime counter with a summed checksum. Pure "
                       "interpreter loop with no native helpers.",
        "expected": EXPECTED["prime"],
    },
    "json": {
        "id": "B",
        "title": "JSON API round-trip (serialize / parse)",
        "constants": "200 rows x 600 rounds",
        "description": "Serialize a 200-record array, parse it back and sum the "
                       "parsed fields. Spid3r uses json_write/json_parse, PHP "
                       "json_encode/json_decode, Python json.dumps/json.loads. "
                       "All produce byte-identical 9,163-character JSON.",
        "expected": EXPECTED["json"],
    },
    "map": {
        "id": "C",
        "title": "String-keyed map lookups (routing / session)",
        "constants": "64 keys x 100,000 lookups",
        "description": "Build a 64-entry string-keyed table, then do 100,000 "
                       "lookups with a freshly constructed key each time. "
                       "Mirrors route / config / session lookups.",
        "expected": EXPECTED["map"],
    },
    "text": {
        "id": "D",
        "title": "Text template building (string churn / allocation)",
        "constants": "50,000 template rows",
        "description": "Build a user-record template string per row (number "
                       "formatting, concatenation, length measurement) with a "
                       "rolling hash over string lengths. Exercises string "
                       "allocation and GC pressure, like log/report generation.",
        "expected": EXPECTED["text"],
    },
    "matmul": {
        "id": "E",
        "title": "Integer matrix multiply (numeric loops / indexing)",
        "constants": "128 x 128 integer matrices",
        "description": "Classic i-k-j matmul with a transposed B (built untimed) "
                       "so every language multiplies row-by-row. Integer-only "
                       "arithmetic keeps checksums exact across languages. "
                       "Exercises nested indexing and tight numeric loops.",
        "expected": EXPECTED["matmul"],
    },
}

# --------------------------------------------------------------------------
# Workload sources live in heavyload/comparison_sources/ - one module per
# language, each carrying the same pinned algorithms and the native-serializer
# JSON workload where the language's standard library provides one.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _env():
    """Environment that stops Git Bash / MSYS rewriting /tmp paths."""
    e = dict(os.environ)
    e["MSYS_NO_PATHCONV"] = "1"
    e["MSYS2_ARG_CONV_EXCL"] = "*"
    return e


def _call(cmd, cwd=None, timeout=60):
    """Run a command. Never raises; returns (rc, stdout, stderr, wall_ms)."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=False, capture_output=True,
                           text=True, timeout=timeout, env=_env())
        return p.returncode, p.stdout or "", p.stderr or "", \
            (time.perf_counter() - t0) * 1000.0
    except subprocess.TimeoutExpired:
        return -1, "", "timeout after %ss" % timeout, \
            (time.perf_counter() - t0) * 1000.0
    except Exception as ex:
        return -1, "", "%s: %s" % (type(ex).__name__, ex), \
            (time.perf_counter() - t0) * 1000.0


def _first_line(text):
    for line in (text or "").strip().splitlines():
        if line.strip():
            return line.strip()
    return ""


# Box-drawing / block characters used by ASCII-art banners (Spid3r prints a
# figlet-style "SPID3R" logo ahead of its version line). A banner line must
# never reach a version string, a variant label or an HTML table cell.
_BANNER_CHARS = set("█▀▄▌▐░▒▓╗╔╝╚║═╦╩╠╣╬╭╮╯╰─│┤├┬┴┼╒╕╘╛╞╡")
_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _clean_version(text):
    """Return a bare 'x.y[.z]' version from tool output, skipping banners.

    Spid3r's ``--version`` output is: empty line, ASCII-art banner, blank
    lines, then fields like "Version       1.1.1". The first banner-safe line
    containing a version number wins; "" when none exists (never guessed).
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if any(ch in _BANNER_CHARS for ch in line):
            continue
        m = _VERSION_RE.search(line)
        if m:
            return m.group(1)
    return ""


def _parse_output(out):
    """Return (result_line, compute_ms) or (None, None)."""
    lines = [x.strip() for x in (out or "").strip().splitlines() if x.strip()]
    if len(lines) < 2:
        return None, None
    try:
        us = int(lines[-1].split()[-1])
    except (ValueError, IndexError):
        return None, None
    return lines[0], us / 1000.0


def _log(log, msg, level="info"):
    if log is not None:
        try:
            log.add("[runtime-compare] %s" % msg, level)
        except TypeError:
            log.add("[runtime-compare] %s" % msg)
        except Exception:
            pass


def _measure(cmd, cwd=None, timeout=60):
    """Run one variant repeat with measured resources (never raises).

    Wraps procmetrics.run_measured with command resolution (Windows .bat/.cmd
    shims) and the MSYS path-conversion guards used everywhere else.
    """
    m = procmetrics.run_measured(_resolve_cmd(cmd), cwd=cwd, timeout=timeout,
                                 env=_env())
    return m


def geo(values):
    values = [v for v in values if v and v > 0]
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


# --------------------------------------------------------------------------
# Measurement statistics
# Aligned with common benchmark-reporting practice (SPEC-style repeated runs,
# medians with dispersion, ISO/IEC 80000-3 time units):
#   * median of N repeats is the reported value (robust to outliers)
#   * dispersion is always published: mean, sample stdev, CV%, IQR, min/max
#   * a 95% confidence interval accompanies both mean (t-based) and median
#     (deterministic bootstrap, percentile method)
#   * every measurement-quality flag is visible in the report, never hidden
# --------------------------------------------------------------------------
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
        7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
        13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
        19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}

_NOISY_CV_PCT = 15.0        # CV above this = run-to-run noise is material
_TIMER_FLOOR_MS = 10.0      # < 10 ms on a 1 ms timer = >10% quantization error


def _t95(df):
    """Two-sided 95% t critical value for df degrees of freedom."""
    if df in _T95:
        return _T95[df]
    if df > 30:
        return 1.96
    # between tabulated points: conservative (take the next-larger df entry)
    below = max(k for k in _T95 if k <= df)
    return _T95[below]


def row_stats(runs_ms, group=""):
    """Compute the published statistics for one variant's repeat runs.

    Deterministic: the bootstrap uses a fixed seed, so re-running the report
    over the same raw data always yields identical numbers.
    """
    runs = sorted(x for x in runs_ms if isinstance(x, (int, float)))
    n = len(runs)
    if not n:
        return {"n": 0}
    med = statistics.median(runs)
    mean = statistics.fmean(runs)
    stdev = statistics.stdev(runs) if n >= 2 else 0.0
    cv = (stdev / mean * 100.0) if mean > 0 else None
    if n >= 4:
        q = statistics.quantiles(runs, n=4, method="inclusive")
        q1, q3 = q[0], q[2]
    else:
        q1 = runs[0]
        q3 = runs[-1]
    iqr = q3 - q1
    # 95% CI of the mean (t-based, normal-theory; honest for small n)
    if n >= 2 and mean > 0:
        half = _t95(n - 1) * stdev / math.sqrt(n)
        mean_lo, mean_hi = mean - half, mean + half
    else:
        mean_lo = mean_hi = None
    # 95% CI of the median (percentile bootstrap, 2000 resamples, fixed seed)
    rng = random.Random(20261120)
    if n >= 2:
        meds = []
        for _ in range(2000):
            sample = [rng.choice(runs) for _ in range(n)]
            meds.append(statistics.median(sample))
        meds.sort()
        med_lo = meds[int(0.025 * len(meds))]
        med_hi = meds[min(len(meds) - 1, int(0.975 * len(meds)))]
    else:
        med_lo = med_hi = None
    flags = []
    if n < 5:
        flags.append("few-repeats (n=%d; >=5 recommended)" % n)
    if cv is not None and cv > _NOISY_CV_PCT:
        flags.append("noisy (CV %.1f%% > %.0f%%)" % (cv, _NOISY_CV_PCT))
    if group == "spid3r" and med < _TIMER_FLOOR_MS:
        flags.append("timer floor (median %.2f ms near the 1 ms now_ms "
                     "resolution)" % med)
    return {
        "n": n,
        "median_ms": round(med, 3),
        "mean_ms": round(mean, 3),
        "stdev_ms": round(stdev, 3),
        "cv_pct": round(cv, 2) if cv is not None else None,
        "q1_ms": round(q1, 3),
        "q3_ms": round(q3, 3),
        "iqr_ms": round(iqr, 3),
        "min_ms": round(runs[0], 3),
        "max_ms": round(runs[-1], 3),
        "mean_ci95": [round(mean_lo, 3), round(mean_hi, 3)]
        if mean_lo is not None else None,
        "median_ci95": [round(med_lo, 3), round(med_hi, 3)]
        if med_lo is not None else None,
        "flags": flags,
        "stable": not flags,
    }


def _safe_rel(rel):
    """Validate a registry-generated relative filename.

    Registry filenames are constants, but generated file names must never be
    able to leave the work/sources directories: absolute paths, ``..``
    segments and escapes are rejected before anything is opened.
    """
    if not rel or os.path.isabs(rel):
        raise ValueError("unsafe relative path: %r" % rel)
    native = rel.replace("/", os.sep)
    if ".." in native.split(os.sep) or native.startswith(os.sep):
        raise ValueError("unsafe relative path: %r" % rel)
    return native


def _write_source(base, rel, text):
    """Write one generated workload source under ``base`` (traversal-safe)."""
    native = _safe_rel(rel)
    dest = pathlib.Path(base) / native
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8", newline="\n")
    return str(dest)


def _under(base, native_rel):
    """base + validated relative path; verifies containment explicitly."""
    p = os.path.normpath("%s%s%s" % (base, os.sep, native_rel))
    base_abs = os.path.abspath(base) + os.sep
    if not os.path.abspath(p + os.sep).startswith(base_abs):
        raise ValueError("path %r escapes %r" % (native_rel, base))
    return p


def _resolve_cmd(cmd):
    """Resolve a command for subprocess execution of PATH toolchains.

    shutil.which finds .bat/.cmd shims (kotlinc.bat, tsc.cmd), but
    CreateProcess cannot execute batch files directly - they must go through
    cmd.exe. Everything else passes through unchanged.
    """
    if not cmd:
        return list(cmd)
    head = cmd[0]
    if os.path.isabs(head) or os.sep in head:
        resolved = head if os.path.isfile(head) else (shutil.which(head) or head)
    else:
        resolved = shutil.which(head) or head
    ext = os.path.splitext(resolved)[1].lower()
    if ext in (".bat", ".cmd"):
        return ["cmd", "/d", "/c", resolved] + list(cmd[1:])
    return [resolved] + list(cmd[1:])


# Toolchain probes. java/kotlin print their version to STDERR, so the
# version extractor falls back to the error stream.
_PROBES = [
    ("spid3r", ["spid3r", "--version"]),
    ("php", ["php", "-r", "echo PHP_VERSION;"]),
    ("python", [sys.executable, "-c",
                "import sys;print(sys.version.split()[0])"]),
    ("node", ["node", "--version"]),
    ("rustc", ["rustc", "--version"]),
    ("java", ["java", "-version"]),
    ("javac", ["javac", "--version"]),
    ("kotlinc", ["kotlinc", "-version"]),
    ("gcc", ["gcc", "-dumpfullversion", "-dumpversion"]),
    ("gpp", ["g++", "-dumpfullversion", "-dumpversion"]),
    ("clang", ["clang", "--version"]),
    ("clangpp", ["clang++", "--version"]),
    ("dotnet", ["dotnet", "--version"]),
    ("go", ["go", "version"]),
    ("ruby", ["ruby", "-v"]),
    ("swiftc", ["swiftc", "--version"]),
    ("tsc", ["tsc", "--version"]),
]


def detect_tools(cfg, opts=None):
    """Probe toolchains. Missing entries are False, never guessed."""
    opts = opts or {}
    tools = {}
    for key, cmd in _PROBES:
        rc, out, err, _ = _call(_resolve_cmd(cmd), timeout=60)
        version = ""
        if rc == 0:
            version = (_clean_version(out) if key == "spid3r"
                       else (_first_line(out) or _first_line(err)))
        tools[key] = {
            "available": rc == 0,
            "version": version if rc == 0 else "",
            "path": (shutil.which(cmd[0]) or "") if rc == 0 else ""}
        if rc != 0:
            tools.setdefault("_errors", {})[key] = _first_line(err)

    # C/C++ fallbacks: use clang/clang++ when gcc/g++ are absent.
    if not tools.get("gcc", {}).get("available") and \
            tools.get("clang", {}).get("available"):
        tools["gcc"] = dict(tools["clang"])
        tools["gcc"]["via"] = "clang"
    if not tools.get("gpp", {}).get("available") and \
            tools.get("clangpp", {}).get("available"):
        tools["gpp"] = dict(tools["clangpp"])
        tools["gpp"]["via"] = "clang++"

    # dotnet: the SDK (not just the runtime) is required to BUILD; derive the
    # highest installed NETCore.App TargetFramework for generated projects.
    if tools.get("dotnet", {}).get("available"):
        rc, out, err, _ = _call(["dotnet", "--list-runtimes"], timeout=60)
        tfm = ""
        if rc == 0:
            for line in (out or "").splitlines():
                if "Microsoft.NETCore.App" not in line:
                    continue
                m = re.search(r"(\d+)\.\d+", line)
                if m:
                    tfm = "net%s.0" % m.group(1)
        tools["dotnet"]["tfm"] = tfm
        rc2, out2, _, _ = _call(["dotnet", "--list-sdks"], timeout=60)
        if not (rc2 == 0 and (out2 or "").strip()):
            tools["dotnet"]["available"] = False
            tools["dotnet"]["error"] = \
                "runtime present but no SDK installed (cannot build)"
            tools.setdefault("_errors", {})["dotnet"] = \
                "no .NET SDK installed"
    return tools


def _available_modules(tools, opts):
    """Language modules runnable right now, in registry order."""
    mods = []
    if not bool(opts.get("include_windows", True)):
        return mods
    for m in LANG_MODULES:
        if not m.available(tools):
            continue
        if m.GROUP == "reference" and not opts.get("include_references", True):
            continue
        mods.append(m)
    return mods


def _build_for_module(wid, stem, mod, work, tools, opts, warnings,
                      build_timeout):
    """Compile artifacts for one language module (if it needs building)."""
    if not hasattr(mod, "build"):
        return None
    return mod.build(work, stem, tools, build_timeout, _call, warnings, EXE)


def _variants_for_module(wid, stem, mod, work, tools, built, opts, warnings):
    """Variant rows contributed by one language module for one workload."""
    if stem not in mod.SOURCES:
        return []
    kwargs = {}
    if mod.KEY == "python":
        kwargs["python_exe"] = sys.executable
    return mod.variants(stem, work, tools, built, **kwargs)


# --------------------------------------------------------------------------
# Variant construction is registry-driven: every language module carries its
# own sources, build step and run commands (see comparison_sources/).
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def run(cfg, out_dir, log=None, container="", write_html=True):
    """Execute the comparison and write artifacts into ``out_dir``.

    ``container`` is accepted for call-site compatibility and ignored: the
    container instrumentation was removed in v2.

    Returns the full data dict. Never raises: all problems become warnings
    plus a non-ok status, so the HTTP load test result is never lost.
    """
    raw_opts = dict(DEFAULT_OPTIONS)
    raw_opts.update(cfg.get("runtime_comparison") or {})
    opts = dict(raw_opts)
    repeats = max(1, min(20, int(opts.get("repeats") or 5)))
    timeout = max(60, int(opts.get("timeout_s") or 300))
    build_timeout = max(60, int(opts.get("build_timeout_s") or 900))
    wanted = [w for w in (opts.get("workloads") or []) if w in WORKLOAD_INFO]
    if not wanted:
        wanted = list(WORKLOAD_INFO)
    runtime_env = str(cfg.get("runtime_environment") or "windows").strip().lower()
    if runtime_env not in RUNTIME_ENVIRONMENT_LABELS:
        runtime_env = "windows"
    env_label = RUNTIME_ENVIRONMENT_LABELS[runtime_env]
    want_windows = bool(opts.get("include_windows", True))
    write_sources = bool(opts.get("write_sources", True))

    os.makedirs(out_dir, exist_ok=True)
    warnings = []
    data = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": utc(),
        "status": "running",
        "config": {k: v for k, v in opts.items()},
        "config_meta": {
            "constants_pinned": True,
            "prime_n": PRIME_N, "json_rows": JSON_ROWS,
            "json_rounds": JSON_ROUNDS, "map_keys": MAP_KEYS,
            "map_rounds": MAP_ROUNDS, "text_rows": TEXT_ROWS,
            "matmul_n": MATMUL_N, "repeats": repeats,
            "runtime_environment": runtime_env,
            "runtime_environment_label": env_label,
            "include_windows": want_windows,
        },
        "environment": {},
        "workloads": {},
        "summary": {},
        "artifacts": {},
        "warnings": warnings,
    }
    # Early placeholder so a long comparison is visible in the UI / on disk.
    atomic_json(os.path.join(out_dir, "runtime_comparison.json"), data)

    work = tempfile.mkdtemp(prefix="hlc-rt-")
    sources_dir = os.path.join(out_dir, "sources")
    if write_sources:
        # A stable output dir (runtime-comparison/) is reused between runs;
        # clear stale programs so `sources/` always matches this run.
        shutil.rmtree(sources_dir, ignore_errors=True)
        os.makedirs(sources_dir, exist_ok=True)
    try:
        _log(log, "detecting language toolchains (%d known)" % len(_PROBES))
        tools = detect_tools(cfg, opts)
        mods = _available_modules(tools, opts)
        detected = [m.KEY for m in mods]
        _log(log, "available language modules: %s"
             % (", ".join(detected) or "none"))
        data["environment"] = {
            "runtime_environment": env_label,
            "comparison_sources": "installed host toolchains",
            "detected_modules": detected,
            "platform": sys.platform,
            "os": "%s (%s)" % (platform.system(), platform.release()),
            "os_build": platform.platform(),
            "cpu_model": (cpu_model() or platform.machine()
                          or "not recorded"),
            "cpu_count": os.cpu_count() or "not recorded",
            "python_runner": sys.version.split()[0],
            "proc_metrics": "measured per variant (proc_metrics_method field)",
        }
        # One version line per detected toolchain, plus the resolved binary
        # path (multiple installs can coexist on PATH; publish provenance).
        for key, tinfo in tools.items():
            if key.startswith("_"):
                continue
            if tinfo.get("available"):
                data["environment"][key] = tinfo.get("version") or "available"
                data["environment"][key + "_executable"] = tinfo.get("path", "")
            else:
                data["environment"][key] = "not available"
        if not mods:
            warnings.append("No supported language toolchain detected on this "
                            "host; comparison cannot be produced.")
            data["status"] = "unavailable"
            data["summary"] = summarize_data(data)
            _finish(data, out_dir, opts, log, write_html=write_html)
            return data

        for wid in wanted:
            info = WORKLOAD_INFO[wid]
            _log(log, "workload %s (%s): generating sources" % (wid, info["id"]))
            stem = wid
            for mod in mods:
                src = mod.SOURCES.get(stem)
                if src is None:
                    continue
                rel = _safe_rel(mod.filename(stem))
                (pathlib.Path(work) / rel).write_text(
                    src, encoding="utf-8", newline="\n")
                if write_sources:
                    (pathlib.Path(sources_dir) / rel).write_text(
                        src, encoding="utf-8", newline="\n")
                # Extra generated files (e.g. the C# project file) go through
                # the same source writer so sources/ stays the full truth.
                if hasattr(mod, "extra_files"):
                    for extra_rel, extra_text in \
                            mod.extra_files(stem, tools).items():
                        erel = _safe_rel(extra_rel)
                        (pathlib.Path(work) / erel).write_text(
                            extra_text, encoding="utf-8", newline="\n")
                        if write_sources:
                            (pathlib.Path(sources_dir) / erel).write_text(
                                extra_text, encoding="utf-8", newline="\n")

            rows = []
            for mod in mods:
                if stem not in mod.SOURCES:
                    continue
                built = _build_for_module(wid, stem, mod, work, tools, opts,
                                          warnings, build_timeout)
                variants = _variants_for_module(wid, stem, mod, work, tools,
                                                built, opts, warnings)
                for var in variants:
                    comps, walls, results = [], [], []
                    rss, cpu_user, cpu_kernel = [], [], []
                    failed = ""
                    method = ""
                    for _ in range(repeats):
                        m = _measure(var["cmd"], cwd=work, timeout=timeout)
                        if m["rc"] != 0:
                            failed = _first_line(m["stderr"]) or "rc=%s" % m["rc"]
                            break
                        res, cms = _parse_output(m["stdout"])
                        if res is None:
                            failed = "missing RESULT/TIME_US output"
                            break
                        results.append(res)
                        comps.append(cms)
                        walls.append(m["wall_ms"])
                        if m.get("peak_rss_bytes") is not None:
                            rss.append(m["peak_rss_bytes"])
                        if m.get("cpu_user_s") is not None:
                            cpu_user.append(m["cpu_user_s"])
                        if m.get("cpu_kernel_s") is not None:
                            cpu_kernel.append(m["cpu_kernel_s"])
                        method = m.get("method", "")
                    if failed or not comps:
                        warnings.append("%s / %s skipped: %s"
                                        % (wid, var["label"],
                                           failed or "no measurements"))
                        continue
                    # Integrity guard: identical work in every language.
                    observed = set(results)
                    if len(observed) != 1 or \
                            next(iter(observed)) != info["expected"]:
                        warnings.append(
                            "%s / %s REJECTED: checksum %r != expected %r"
                            % (wid, var["label"], sorted(observed),
                               info["expected"]))
                        continue
                    rows.append({
                        "key": var["key"], "runtime": var["label"],
                        "group": var["group"],
                        "source": "host",
                        "result": results[0],
                        "compute_ms": round(statistics.median(comps), 3),
                        "compute_min_ms": round(min(comps), 3),
                        "compute_max_ms": round(max(comps), 3),
                        "wall_ms": round(statistics.median(walls), 2),
                        "compute_runs_ms": [round(x, 3) for x in comps],
                        "stats": row_stats(comps, var["group"]),
                        # Measured resources (None = unavailable, never
                        # estimated; provenance in procmetrics).
                        "peak_rss_bytes": (round(statistics.median(rss))
                                           if rss else None),
                        "peak_rss_min_bytes": round(min(rss)) if rss else None,
                        "peak_rss_max_bytes": round(max(rss)) if rss else None,
                        "cpu_user_s": (round(statistics.median(cpu_user), 4)
                                       if cpu_user else None),
                        "cpu_kernel_s": (round(statistics.median(cpu_kernel), 4)
                                         if cpu_kernel else None),
                        "proc_metrics_method": method,
                    })
            verified = len(rows) >= 2 and all(
                w["result"] == info["expected"] for w in rows)
            if not verified and rows:
                warnings.append("%s: only %d verified variants; ratios for "
                                "this workload are omitted" % (wid, len(rows)))
            data["workloads"][wid] = {
                "id": info["id"], "title": info["title"],
                "constants": info["constants"],
                "description": info["description"],
                "expected_checksum": info["expected"],
                "verified": verified,
                "variants": len(rows),
                "rows": sorted(rows, key=lambda x: x["compute_ms"]),
            }
            _log(log, "workload %s (%s): %d verified variants"
                 % (wid, info["id"], len(rows)))

        data["summary"] = summarize_data(data)
        data["status"] = ("ok" if all(
            w["verified"] for w in data["workloads"].values())
            and data["workloads"] else
            "partial" if any(w["verified"] for w in
                             data["workloads"].values()) else "unavailable")
        _finish(data, out_dir, opts, log, write_html=write_html)
        return data
    except Exception as ex:
        warnings.append("unexpected error: %s: %s" % (type(ex).__name__, ex))
        data["status"] = "error"
        data["summary"] = {"verdict": "Comparison failed: %s" % ex}
        _finish(data, out_dir, opts, log, write_html=write_html)
        return data
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _finish(data, out_dir, opts, log, write_html=True):
    json_path = os.path.join(out_dir, "runtime_comparison.json")
    html_path = os.path.join(out_dir, "runtime_comparison.html")
    sources_dir = os.path.join(out_dir, "sources")
    data["artifacts"] = {
        "json": "runtime_comparison.json",
        # Full benchmark runs embed this content in report.html and skip the
        # standalone page; only the JSON data artifact is kept there.
        "html": "runtime_comparison.html" if write_html else "",
        "sources": ("sources/" if (opts.get("write_sources", True)
                                   and os.path.isdir(sources_dir)) else ""),
    }
    atomic_json(json_path, data)
    if write_html:
        build_html(data, html_path)
        _log(log, "wrote %s" % html_path)
    else:
        _log(log, "comparison embedded in the run report (no standalone "
                  "page written)")
    s = data.get("summary") or {}
    _log(log, "status=%s | %s" % (data["status"],
                                  s.get("verdict", "no verdict")),
         "warn" if data["status"] not in ("ok",) else "info")
    return data


def summarize_data(data):
    """Compute slowdown ratios. Pure function - also used by selftest.

    Ratios are computed against every measured baseline (python, php, node,
    ruby, typescript, ...) plus ``vs_fastest``; a full pairwise geomean ratio
    matrix covers every measured language pair. Only verified workloads
    participate, so a wrong checksum can never enter a ratio.
    """
    by_wid = {}
    for wid, w in (data.get("workloads") or {}).items():
        if not w.get("verified"):
            continue
        by_wid[wid] = {r["key"]: r["compute_ms"] for r in w["rows"]}

    labels = {r["key"]: r["runtime"]
              for w in (data.get("workloads") or {}).values()
              for r in w["rows"]}
    groups = {r["key"]: r.get("group", "")
              for w in (data.get("workloads") or {}).values()
              for r in w["rows"]}

    def ratios_for(subject, base):
        out = {}
        for wid, vals in by_wid.items():
            if subject in vals and base in vals and vals[base] > 0:
                out[wid] = round(vals[subject] / vals[base], 3)
        g = geo(list(out.values()))
        return {"workloads": out,
                "geomean": round(g, 3) if g else None}

    # Spid3r rows keep the classic vs_php / vs_python view and gain
    # vs_fastest; every other language pair goes into the matrix.
    slowdown = {}
    spid3r_keys = sorted({r["key"] for w in (data.get("workloads") or {}).values()
                          for r in w["rows"]
                          if r["group"] == "spid3r"})
    for key in spid3r_keys:
        entry = {"label": labels.get(key, key), "vs_php": {},
                 "vs_python": {}}
        for base in ("php", "python"):
            entry["vs_" + base] = ratios_for(key, base)
        slowdown[key] = entry

    # Pairwise geomean ratio matrix over ALL measured keys (subject / base).
    matrix = {}
    all_keys = sorted({k for vals in by_wid.values() for k in vals})
    for subject in all_keys:
        entry = {}
        for base in all_keys:
            if subject == base:
                continue
            entry[base] = ratios_for(subject, base)["geomean"]
        matrix[subject] = {"label": labels.get(subject, subject),
                           "geomeans": entry}
    # vs_fastest per key: geomean ratio against the fastest measured key
    # per workload (the honest "how far from the best tool" number).
    fastest = {}
    for wid, vals in by_wid.items():
        if vals:
            k = min(vals, key=lambda x: vals[x])
            fastest[wid] = k
    for subject, entry in matrix.items():
        ratios = {}
        for wid, vals in by_wid.items():
            if subject in vals and wid in fastest and vals.get(fastest[wid]):
                ratios[wid] = round(vals[subject] / vals[fastest[wid]], 3)
        g = geo(list(ratios.values()))
        entry["vs_fastest"] = {"workloads": ratios,
                               "geomean": round(g, 3) if g else None}

    # Spid3r entries gain vs_fastest too.
    for key, entry in slowdown.items():
        ratios = {}
        for wid, vals in by_wid.items():
            if key in vals and wid in fastest and vals.get(fastest[wid]):
                ratios[wid] = round(vals[key] / vals[fastest[wid]], 3)
        g = geo(list(ratios.values()))
        entry["vs_fastest"] = {"workloads": ratios,
                               "geomean": round(g, 3) if g else None}

    all_ratios = []
    for e in slowdown.values():
        for b in ("vs_php", "vs_python", "vs_fastest"):
            all_ratios.extend((e.get(b) or {}).get("workloads", {}).values())
    best = min(all_ratios) if all_ratios else None
    worst = max(all_ratios) if all_ratios else None
    if not by_wid:
        verdict = "No verified workloads - comparison unavailable."
    elif not slowdown:
        # Baselines exist but no Spid3r variant was measured, so there are no
        # Spid3r ratios. Never claim Spid3r "matched or beat" anything.
        verdict = ("No Spid3r variant could be measured - Spid3r slowdown "
                   "ratios unavailable; per-language matrix still valid.")
    elif best is None:
        verdict = "Slowdown ratios unavailable."
    elif best > 1:
        verdict = ("Spid3r is slower than the measured baselines on every "
                   "workload (%.2fx-%.2fx)." % (best, worst))
    else:
        verdict = "Spid3r matched or beat a baseline in at least one workload."
    # Measurement-quality roll-up across all verified rows.
    flagged, stable, worst_cv = [], 0, None
    for w in (data.get("workloads") or {}).values():
        for r in w.get("rows") or []:
            st = r.get("stats") or {}
            if st.get("flags"):
                flagged.append("%s / %s: %s"
                               % (w.get("id"), r.get("runtime"),
                                  "; ".join(st["flags"])))
            else:
                stable += 1
            cv = st.get("cv_pct")
            if cv is not None and (worst_cv is None or cv > worst_cv):
                worst_cv = cv
    return {
        "workloads_verified": len(by_wid),
        "workloads_total": len(data.get("workloads") or {}),
        "variants": sum(len(w["rows"]) for w in
                        (data.get("workloads") or {}).values()),
        "quality": {
            "stable_variants": stable,
            "flagged_variants": len(flagged),
            "flag_detail": flagged,
            "worst_cv_pct": round(worst_cv, 2) if worst_cv is not None else None,
            "thresholds": {"noisy_cv_pct": _NOISY_CV_PCT,
                           "timer_floor_ms": _TIMER_FLOOR_MS},
        },
        "slowdown_range": {"min": round(best, 3) if best else None,
                           "max": round(worst, 3) if worst else None},
        "slowdown": slowdown,
        "matrix": matrix,
        "verdict": verdict,
        "note": "Language-runtime compute only; not an end-to-end HTTP "
                "framework comparison.",
    }


def summarize(data):
    """Compact dict embedded in results.json (keeps the result doc small)."""
    s = data.get("summary") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "status": data.get("status"),
        "generated_utc": data.get("generated_utc"),
        "runtime_environment": (data.get("environment") or {}).get(
            "runtime_environment", ""),
        "comparison_sources": (data.get("environment") or {}).get(
            "comparison_sources", ""),
        "verdict": s.get("verdict"),
        "workloads_verified": s.get("workloads_verified"),
        "variants": s.get("variants"),
        "slowdown_range": s.get("slowdown_range"),
        "slowdown": s.get("slowdown"),
        "warnings": (data.get("warnings") or [])[:10],
        "artifacts": data.get("artifacts"),
    }


# --------------------------------------------------------------------------
# Self-contained HTML report
# --------------------------------------------------------------------------
_CSS = """
:root{--bg:#f6f8fb;--card:#fff;--ink:#1b2434;--muted:#667085;--line:#e4e9f0;
--blue:#2563eb;--orange:#f85606;--green:#16a34a;--amber:#d97706;--red:#dc2626;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 22px 70px}
header.top{background:var(--card);border:1px solid var(--line);border-top:4px
solid var(--orange);border-radius:12px;padding:24px 28px}
h1{margin:0 0 6px;font-size:24px;letter-spacing:-.3px}
.sub{margin:0;color:var(--muted);font-size:13px}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:3px 10px;
border-radius:999px;margin:10px 6px 0 0;background:#eef2f7;color:var(--muted)}
.badge.ok{background:#e8f7ee;color:var(--green)}
.badge.warn{background:#fff4e0;color:var(--amber)}
.badge.bad{background:#fdecec;color:var(--red)}
section.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:20px 24px;margin-top:20px;min-width:0}
h2{font-size:17px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:18px 0 6px}
p{margin:0 0 10px}.note{font-size:12px;color:var(--muted)}
code{font-family:var(--mono);font-size:.88em;background:#f0f2f6;border:1px
solid #e6e9f0;border-radius:4px;padding:1px 5px}
.tblwrap{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%;margin:10px 0}
.tblwrap table{margin:0}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin:10px 0}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line)}
th{background:#f8fafc;font-size:11px;text-transform:uppercase;letter-spacing:.4px;
color:var(--muted)}
td.num,th.num{text-align:right;font-family:var(--mono);white-space:nowrap}
tr.spid3r td{background:#fffaf7}tr.base td{background:#f5f9ff}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.kpi .n{font-size:21px;font-weight:700}.kpi .l{font-size:11px;color:var(--muted)}
.barrow{display:grid;grid-template-columns:250px 1fr 84px;gap:10px;align-items:center;
margin-bottom:6px;font-size:12px}
.barlabel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bartrack{background:#eef1f6;border-radius:4px;height:15px;overflow:hidden;
position:relative}
.barrange{position:absolute;top:0;height:100%;background:rgba(27,36,52,.14)}
.barfill{position:absolute;top:0;left:0;height:100%;border-radius:4px}
.barval{text-align:right;font-family:var(--mono);color:var(--muted)}
.qtag{font-size:10px;font-weight:700;border-radius:4px;padding:1px 6px;
white-space:nowrap}
.qok{color:#166534;background:#e8f7ee;border:1px solid #bfe8cf}
.qwarn{color:#92400e;background:#fff4e0;border:1px solid #f3d19e}
.srctag{font-size:10px;font-weight:700;color:#7c3aed;background:#f3e8ff;
border:1px solid #ddd0fe;border-radius:4px;padding:1px 5px;margin-left:5px}
ul{margin:6px 0 10px;padding-left:20px}li{margin-bottom:4px}
.verdict{border-left:4px solid var(--orange);background:#fff6f1;padding:12px 16px;
border-radius:0 8px 8px 0;margin:12px 0}
footer{margin-top:30px;padding-top:14px;border-top:1px solid var(--line);
font-size:11.5px;color:var(--muted)}
@media(max-width:720px){.barrow{grid-template-columns:140px 1fr 70px;font-size:11px}}
"""


def _e(x):
    return html.escape(str(x if x is not None else ""), quote=True)


def _f(v, d=2):
    return "n/a" if v is None else ("%.*f" % (d, v))


def row_source(row, *, provenance_recorded=True):
    """WHERE a comparison row was measured: 'host', 'docker' or 'unavailable'.

    v2.x runs measure everything on the host; archived docker-era rows keep
    their 'docker' label so a v1 archive still renders honestly.
    """
    src = row.get("source")
    if src == "docker":
        return "docker"                       # archived instrumented run
    if src in ("host", "windows"):
        return "host"
    if (str(row.get("key") or "").startswith("spid3r_docker_")
            or "(Docker)" in str(row.get("runtime") or "")):
        return "docker"  # unambiguous evidence even in a v1 archive
    if provenance_recorded:
        return "host"
    return "unavailable"  # never invent a source for an unrecorded v1 row


def build_body(data, include_page_header=False):
    """Return the comparison report BODY as an HTML fragment.

    Contains every section (status card, per-workload evidence, ratios,
    pairwise matrix, quality, environment & method) but no page shell. Used
    by ``build_html`` for the standalone page and embedded verbatim by
    ``report.build`` so the run has ONE self-contained report.
    """
    s = data.get("summary") or {}
    env = data.get("environment") or {}

    # One v1 provenance guard, computed once and reused by every consumer below.
    schema_v = int(data.get("schema_version") or 1)
    provenance_recorded = schema_v >= 2 and (
        "runtime_environment" in (data.get("environment") or {})
        or "runtime_environment_label" in (data.get("config_meta") or {}))
    all_rows = [r for w in (data.get("workloads") or {}).values()
                for r in (w.get("rows") or [])]
    docker_rows = [r for r in all_rows
                   if row_source(r, provenance_recorded=provenance_recorded)
                   == "docker"]

    P = []
    status = data.get("status", "unknown")
    bcls = {"ok": "ok", "partial": "warn", "unavailable": "bad",
            "error": "bad"}.get(status, "warn")
    if include_page_header:
        P.append('<header class="top"><h1>Language Runtime Comparison</h1>')
        P.append('<p class="sub">Spid3r vs %s &middot; '
                 'generated %s &middot; checksum-verified, no simulated values'
                 '</p>' % (_e(_LANG_LINE), _e(data.get("generated_utc"))))
    env_label = ((data.get("environment") or {}).get("runtime_environment")
                 or (data.get("config_meta") or {}).get("runtime_environment_label")
                 or ("Host - installed runtimes" if provenance_recorded
                     else "provenance not recorded (schema v1)"))
    P.append('<div class="rc-status">')
    P.append('<span class="badge %s">status: %s</span>' % (bcls, _e(status)))
    P.append('<span class="badge">runtime: %s</span>' % _e(env_label))
    P.append('<span class="badge">%s workloads</span>'
             % _e(s.get("workloads_verified", 0)))
    P.append('<span class="badge">%s variants</span>' % _e(s.get("variants", 0)))
    repeats = (data.get("config_meta") or {}).get("repeats", 5)
    P.append('<span class="badge">median of %s repeats</span>' % _e(repeats))
    q = s.get("quality") or {}
    if q:
        flagged = q.get("flagged_variants") or 0
        stable = q.get("stable_variants") or 0
        if flagged:
            P.append('<span class="badge warn">quality: %d flagged / %d '
                     'stable</span>' % (flagged, stable))
        else:
            P.append('<span class="badge ok">quality: %d/%d stable</span>'
                     % (stable, stable + flagged))
    P.append("</div>")

    rng = s.get("slowdown_range") or {}
    P.append('<div class="kpis">')
    _rmin = ("%s&times;" % _f(rng.get("min"))) if rng.get("min") is not None else "n/a"
    _rmax = ("%s&times;" % _f(rng.get("max"))) if rng.get("max") is not None else "n/a"
    for n, l in [(_rmin, "best Spid3r slowdown"),
                 (_rmax, "worst Spid3r slowdown"),
                 (_e(s.get("workloads_verified", 0)), "verified workloads"),
                 (_e(s.get("variants", 0)), "measured runtime variants")]:
        P.append('<div class="kpi"><div class="n">%s</div><div class="l">%s</div>'
                 "</div>" % (n, l))
    P.append("</div>")

    P.append('<section class="card"><h2>Verdict</h2>')
    P.append('<div class="verdict"><strong>%s</strong></div>' % _e(s.get("verdict")))
    P.append('<p class="note">%s</p>' % _e(s.get("note")))
    P.append("</section>")

    for wid, w in (data.get("workloads") or {}).items():
        rows = w.get("rows") or []
        P.append('<section class="card"><h2>%s. %s</h2>'
                 % (_e(w.get("id")), _e(w.get("title"))))
        P.append('<p class="note">%s &middot; verified checksum '
                 '<code>%s</code> &middot; %s %s</p>'
                 % (_e(w.get("constants")), _e(w.get("expected_checksum")),
                    len(rows), "variant" if len(rows) == 1 else "variants"))
        P.append('<p>%s</p>' % _e(w.get("description")))
        if rows:
            mx = max((r.get("stats") or {}).get("max_ms",
                     r["compute_ms"]) for r in rows) or 1
            for r in rows:
                color = {"spid3r": "#f85606", "baseline": "#2563eb",
                         "reference": "#94a3b8"}.get(r.get("group"), "#94a3b8")
                st = r.get("stats") or {}
                lo = st.get("min_ms", r["compute_ms"])
                hi = st.get("max_ms", r["compute_ms"])
                band = ('<div class="barrange" style="left:%.2f%%;width:%.2f%%">'
                        "</div>" % (lo / mx * 100.0,
                                    max(0.5, (hi - lo) / mx * 100.0))
                        if hi > lo else "")
                pct = max(1.0, r["compute_ms"] / mx * 100)
                src = row_source(r, provenance_recorded=provenance_recorded)
                if src == "docker":
                    tag = '<span class="srctag">Docker</span>'
                elif src == "unavailable":
                    tag = '<span class="srctag">source unavailable</span>'
                else:
                    tag = ""
                P.append('<div class="barrow"><div class="barlabel">%s%s</div>'
                         '<div class="bartrack">%s<div class="barfill" '
                         'style="width:%.1f%%;background:%s"></div></div>'
                         '<div class="barval">%s ms</div></div>'
                         % (_e(r["runtime"]), tag, band, pct, color,
                            _f(r["compute_ms"], 3)))
            P.append('<div class="tblwrap"><table><thead><tr><th>Runtime / engine</th><th>Where</th>'
                     '<th class="num">Median</th>'
                     '<th class="num">Mean &plusmn; CI95</th>'
                     '<th class="num">Stdev</th><th class="num">CV%</th>'
                     '<th class="num">IQR</th>'
                     '<th class="num">Min</th><th class="num">Max</th>'
                     "<th>Quality</th></tr></thead><tbody>")
            for r in rows:
                cls = {"spid3r": " class=\"spid3r\"",
                       "baseline": " class=\"base\""}.get(r.get("group"), "")
                src = row_source(r, provenance_recorded=provenance_recorded)
                where = ("Docker container (archived run)" if src == "docker"
                         else "Host machine" if src == "host"
                         else "unavailable (provenance not recorded)")
                st = r.get("stats") or {}
                mc = st.get("mean_ci95")
                mean_ci = ("%s &plusmn; %s" % (_f(st.get("mean_ms"), 2),
                           _f((mc[1] - mc[0]) / 2.0, 2)) if mc else "n/a")
                cv = st.get("cv_pct")
                if not st:
                    q = '<span class="qtag qwarn">n/a (legacy data)</span>'
                elif st.get("flags"):
                    q = "".join('<span class="qtag qwarn">%s</span> '
                                % _e(f) for f in st["flags"])
                else:
                    q = '<span class="qtag qok">stable</span>'
                P.append("<tr%s><td>%s</td><td>%s</td>"
                         "<td class=\"num\">%s</td>"
                         "<td class=\"num\">%s</td>"
                         "<td class=\"num\">%s</td>"
                         "<td class=\"num\">%s</td>"
                         "<td class=\"num\">%s</td>"
                         "<td class=\"num\">%s</td><td class=\"num\">%s</td>"
                         "<td>%s</td></tr>"
                         % (cls, _e(r["runtime"]), _e(where),
                            _f(r["compute_ms"], 3),
                            mean_ci,
                            _f(st.get("stdev_ms"), 3),
                            _f(cv, 1),
                            _f(st.get("iqr_ms"), 3),
                            _f(st.get("min_ms"), 3),
                            _f(st.get("max_ms"), 3),
                            q))
            P.append("</tbody></table></div>")
            P.append('<p class="note">Median of %s in-process repeats. '
                     'CI95 = t-based 95%% confidence interval of the mean; '
                     'CV%% = coefficient of variation (stdev / mean). The grey '
                     'band behind each bar shows the observed min&ndash;max '
                     'range. Raw repeat values are in '
                     '<code>runtime_comparison.json</code>.</p>' % _e(repeats))
        else:
            P.append('<p class="note">No verified variants for this workload.</p>')
        P.append("</section>")

    sd = s.get("slowdown") or {}
    if sd:
        P.append('<section class="card"><h2>Slowdown ratios</h2>')
        P.append('<p class="note">Spid3r time divided by baseline time. '
                 'Values above 1.00&times; mean Spid3r is slower. '
                 'Geomean covers all verified workloads. <code>vs fastest'
                 '</code> compares against the fastest measured runtime for '
                 'each workload.</p>')
        wids = sorted({k for e in sd.values()
                       for b in ("vs_php", "vs_python", "vs_fastest")
                       for k in ((e.get(b) or {}).get("workloads") or {})})
        P.append('<div class="tblwrap"><table><thead><tr><th rowspan="2">Spid3r variant</th>')
        for b in ("PHP", "Python", "Fastest"):
            P.append('<th class="num" colspan="%d">vs %s</th>'
                     % (len(wids) + 1, b))
        P.append("</tr><tr>")
        for _ in range(3):
            for wid in wids:
                P.append('<th class="num">%s</th>' % _e(WORKLOAD_INFO.get(
                    wid, {}).get("id", wid)))
            P.append('<th class="num">geo</th>')
        P.append("</tr></thead><tbody>")
        for key, entry in sd.items():
            P.append('<tr class="spid3r"><td>%s</td>' % _e(entry.get("label")))
            for b in ("vs_php", "vs_python", "vs_fastest"):
                vals = (entry.get(b) or {}).get("workloads") or {}
                for wid in wids:
                    v = vals.get(wid)
                    P.append('<td class="num">%s</td>'
                             % ("%.2f&times;" % v if v else "n/a"))
                g = (entry.get(b) or {}).get("geomean")
                P.append('<td class="num"><strong>%s</strong></td>'
                         % ("%.2f&times;" % g if g else "n/a"))
            P.append("</tr>")
        P.append("</tbody></table></div></section>")

    mx = s.get("matrix") or {}
    if mx:
        P.append('<section class="card"><h2>Pairwise ratio matrix</h2>')
        P.append('<p class="note">Geomean slowdown of the row runtime vs the '
                 'column runtime across all verified workloads. 1.00&times; '
                 'means equal; values are computed ONLY from checksum-verified '
                 'workloads, so a wrong-checksum variant can never enter a '
                 'ratio.</p>')
        keys = sorted(mx.keys())
        P.append('<div class="tblwrap"><table><thead><tr><th>Runtime</th>')
        for k in keys:
            P.append('<th class="num">%s</th>'
                     % _e(mx[k].get("label", k)))
        P.append("</tr></thead><tbody>")
        for sk in keys:
            P.append("<tr><td>%s</td>" % _e(mx[sk].get("label", sk)))
            for k in keys:
                if k == sk:
                    P.append('<td class="num">&mdash;</td>')
                else:
                    v = (mx[sk].get("geomeans") or {}).get(k)
                    P.append('<td class="num">%s</td>'
                             % ("%.2f&times;" % v if v else "n/a"))
            P.append("</tr>")
        P.append("</tbody></table></div></section>")

    q = s.get("quality") or {}
    if q:
        P.append('<section class="card"><h2>Measurement quality</h2>')
        P.append('<div class="kpis">')
        _wcv = (_f(q.get("worst_cv_pct"), 1) + "%") if q.get("worst_cv_pct") is not None else "n/a"
        for n, l in [(_e(q.get("stable_variants", 0)), "stable variants"),
                     (_e(q.get("flagged_variants", 0)), "flagged variants"),
                     (_wcv, "worst run-to-run CV"),
                     (_e(repeats), "repeats per variant")]:
            P.append('<div class="kpi"><div class="n">%s</div><div class="l">'
                     "%s</div></div>" % (n, l))
        P.append("</div>")
        thr = q.get("thresholds") or {}
        P.append('<p class="note">Quality gates: a variant is flagged when its '
                 'coefficient of variation exceeds %s%% (run-to-run noise), '
                 'when a Spid3r median falls below %s ms (timer-resolution '
                 'floor of the 1 ms <code>now_ms()</code> clock), or when '
                 'fewer than 5 repeats were collected. Flags are advisory: '
                 'the numbers stay measured, but compare them with care.'
                 '</p>' % (_f(thr.get("noisy_cv_pct", _NOISY_CV_PCT), 0),
                           _f(thr.get("timer_floor_ms", _TIMER_FLOOR_MS), 0)))
        detail = q.get("flag_detail") or []
        if detail:
            P.append("<ul>")
            for d in detail:
                P.append("<li>%s</li>" % _e(d))
            P.append("</ul>")
        else:
            P.append('<p class="note">All measured variants passed the quality '
                     'gates in this run.</p>')
        P.append("</section>")

    P.append('<section class="card"><h2>Environment &amp; method</h2>')
    P.append('<div class="tblwrap"><table><thead><tr><th>Component</th><th>Version</th></tr>'
             "</thead><tbody>")
    for k, v in env.items():
        P.append("<tr><td>%s</td><td><code>%s</code></td></tr>"
                 % (_e(k), _e(v)))
    P.append("</tbody></table></div>")
    if docker_rows:
        P.append('<p class="note"><strong>Docker rows belong to an archived, '
                 'differently-instrumented run</strong> (container CPU / '
                 'filesystem / image). The container name is printed in each '
                 'Docker variant label. Ratios against host rows are therefore '
                 'approximate and are never presented as an apples-to-apples '
                 'deployment benchmark.</p>')
    elif provenance_recorded:
        P.append('<p class="note">Host runtime mode: only toolchains installed '
                 'on this machine were measured, each through its own resolved '
                 'binary (paths published below). Every variant also carries '
                 'the OS-measured peak working set and CPU time when the '
                 'platform provides them.</p>')
    else:
        P.append('<p class="note">Provenance not recorded for this archived run '
                 '(schema v1); the measured sources cannot be determined.</p>')
    P.append("<h3>Method</h3><ul>"
             "<li>Identical algorithm and pinned constants in every language; "
             "every variant must print the exact expected checksum or it is "
             "rejected and never published (correctness gate before any "
             "timing is accepted).</li>"
             "<li>In-process timing (startup excluded); median of %s repeats "
             "is the reported value; min, max, raw repeat values, dispersion "
             "and confidence intervals are published, never hidden.</li>"
             "<li>Peak working set and CPU time of every variant are MEASURED "
             "by the operating system (Windows: GetProcessMemoryInfo / "
             "GetProcessTimes; POSIX: wait4 rusage) or reported as "
             "unavailable - never estimated.</li>"
             "<li>Spid3r uses <code>now_ms()</code> (1 ms resolution); workloads "
             "are sized to stay clear of the timer floor, and any variant that "
             "still lands near it is flagged in the quality table.</li>"
             "<li>Missing toolchains are skipped with a warning, never faked; "
             "archived Docker rows are labelled as a different environment.</li>"
             "</ul>" % _e(repeats))
    P.append("<h3>Standards alignment</h3><ul>"
             "<li><strong>Units:</strong> durations in milliseconds per "
             "ISO/IEC 80000-3; ratios are dimensionless.</li>"
             "<li><strong>Repeated-measures protocol:</strong> median of "
             "N independent runs with published dispersion, following the "
             "practice popularized by SPEC CPU run rules (median of repeated "
             "runs, verify before publish).</li>"
             "<li><strong>Correctness gate:</strong> deterministic checksums "
             "(like SPEC's verify step) - a wrong result is discarded, never "
             "timed and reported.</li>"
             "<li><strong>Statistics:</strong> median, mean, sample stdev, "
             "CV%, IQR, min/max, t-based 95% CI of the mean and percentile-"
             "bootstrap 95% CI of the median (fixed seed, deterministic).</li>"
             "<li><strong>Disclosure:</strong> raw repeat data in "
             "<code>runtime_comparison.json</code>; generated programs in "
             "<code>sources/</code> (artifact reproducibility); every warning "
             "surfaced in this report.</li>"
             "<li><strong>Scope:</strong> language-runtime compute speed only - "
             "not an end-to-end web-framework or deployment benchmark.</li>"
             "</ul>")
    P.append('<p class="note">Raw data: <code>runtime_comparison.json</code>. '
             'Generated programs are kept in <code>sources/</code>.</p>')
    P.append("</section>")

    warns = data.get("warnings") or []
    if warns:
        P.append('<section class="card"><h2>Warnings</h2><ul>')
        for w in warns:
            P.append("<li>%s</li>" % _e(w))
        P.append("</ul></section>")

    return "".join(P)


def build_html(data, out_path, extra_sections=""):
    """Render the standalone single-file page.

    ``extra_sections`` (an HTML fragment, e.g. the suitability body) is
    appended before the footer so even the standalone comparison produces
    ONE self-contained page. The full benchmark run embeds ``build_body``
    into report.html instead and skips this file entirely.
    """
    P = ["<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         "<title>Runtime Comparison - %s</title>" % _e(data.get("generated_utc")),
         "<style>%s</style></head><body><div class=\"wrap\">" % _CSS,
         build_body(data, include_page_header=True)]
    if extra_sections:
        P.append(extra_sections)
    P.append("<footer>Heavy-Load Checker runtime comparison &middot; "
             "schema v%s &middot; single-file report, no JavaScript, no "
             "external resources.</footer>" % _e(SCHEMA_VERSION))
    P.append("</div></body></html>")

    dest = pathlib.Path(os.path.normpath(out_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(P), encoding="utf-8", newline="\n")
    return str(dest)


def console_summary(data, stream=None):
    """Print a short human-readable summary. Returns the summary dict."""
    out = stream or sys.stdout
    s = data.get("summary") or {}
    print("\n" + "=" * 76, file=out)
    env = (data.get("environment") or {}).get("runtime_environment", "")
    print("RUNTIME COMPARISON  status=%s  runtime=%s  %s"
          % (data.get("status"), env, s.get("verdict", "")), file=out)
    print("=" * 76, file=out)
    for wid, w in (data.get("workloads") or {}).items():
        print("\n%s. %s  [%s]  checksum %s"
              % (w.get("id"), w.get("title"), w.get("constants"),
                 w.get("expected_checksum")), file=out)
        for r in w.get("rows", []):
            print("  %-40s %10.3f ms" % (r["runtime"], r["compute_ms"]),
                  file=out)
    print("\nSlowdown vs baselines (geomean):", file=out)
    for key, e in (s.get("slowdown") or {}).items():
        vf = ((e.get("vs_fastest") or {}).get("geomean"))
        print("  %-38s PHP %6s  Python %6s  fastest %6s"
              % (e.get("label"),
                 ("%.2fx" % e["vs_php"]["geomean"]
                  if (e.get("vs_php") or {}).get("geomean") else "n/a"),
                 ("%.2fx" % e["vs_python"]["geomean"]
                  if (e.get("vs_python") or {}).get("geomean") else "n/a"),
                 ("%.2fx" % vf if vf else "n/a")), file=out)
    for w in (data.get("warnings") or [])[:8]:
        print("  WARN: %s" % w, file=out)
    print("\nArtifacts: runtime_comparison.json / runtime_comparison.html",
          file=out)
    return s
