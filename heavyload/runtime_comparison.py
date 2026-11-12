#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-language runtime comparison (Spid3r vs PHP / Python / Node / Rust).

This is the "test my language" feature. It runs three *identical* checksum-
verified workloads in every available language runtime and writes, into the
run directory:

    runtime_comparison.json    full raw data (all repeats, min/max, versions)
    runtime_comparison.html    self-contained report (no JS, no CDN)
    sources/                   the exact generated programs, for provenance

Design rules (deliberate, do not weaken):
  * Workload constants are PINNED. Changing them changes the checksum, and a
    run whose checksum does not match EXPECTED is rejected, not published.
  * Every variant prints "RESULT <a> <b>" and "TIME_US <n>". A variant whose
    RESULT differs is discarded. No simulated / estimated numbers, ever.
  * Timing is IN-PROCESS (startup excluded). Spid3r only has now_ms(), so all
    workloads are sized to run well above 1 ms.
  * Any missing toolchain is skipped with a warning, never faked.
  * This module never raises into the caller; failures become status + warning.

Standalone use:
    from heavyload import runtime_comparison as RC
    data = RC.run(cfg, out_dir, log=log, container="spid3rweb-app")
"""
from __future__ import annotations

import html
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

from .util import atomic_json, utc

# v2 adds runtime_environment / config_meta flags and a "source" tag per
# variant row. Old v1 documents still render (every new field has a default),
# but variant counts and geomeans must never be compared across the boundary.
SCHEMA_VERSION = 2
EXE = ".exe" if os.name == "nt" else ""

DEFAULT_OPTIONS = {
    "enabled": True,
    "repeats": 5,
    "container": "",           # "" => cfg["container"] or auto-resolve
    "include_windows": True,   # installed host toolchains (checked by default)
    "include_container": False,  # Docker variants only when Docker is chosen
    "include_native": True,
    "include_references": True,
    "write_sources": True,
    "timeout_s": 300,
    "workloads": ["prime", "json", "map"],
}

RUNTIME_ENVIRONMENT_LABELS = {
    "windows": "Windows - installed runtimes",
    "docker": "Docker - container + host baselines",
}

# Constants are pinned so results stay comparable between runs. If you change
# any of these you MUST recompute and update EXPECTED below.
PRIME_N = 50000
JSON_ROWS, JSON_ROUNDS = 200, 600
MAP_KEYS, MAP_ROUNDS = 64, 100000

EXPECTED = {
    "prime": "RESULT 5133 121013308",
    "json": "RESULT 249868800 9163",
    "map": "RESULT 44432313 100000",
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
}

# --------------------------------------------------------------------------
# Workload A: prime counting
# --------------------------------------------------------------------------
PRIME_MATRIX = r"""fn is_prime(n: int) -> bool {
    if n < 2 { return false; }
    let d = 2;
    while d * d <= n { if n % d == 0 { return false; } d += 1; }
    return true;
}
fn main() {
    let t0 = now_ms();
    let i = 2;
    let count = 0;
    let checksum = 0;
    while i < 50000 {
        if is_prime(i) { count += 1; checksum += i; }
        i += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(count) + " " + string(checksum));
    println("TIME_US " + string(us));
    return 0;
}
"""

PRIME_PY = r"""import time
def is_prime(n):
    if n < 2: return False
    d = 2
    while d * d <= n:
        if n % d == 0: return False
        d += 1
    return True
t0 = time.perf_counter_ns()
i, count, checksum = 2, 0, 0
while i < 50000:
    if is_prime(i):
        count += 1; checksum += i
    i += 1
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", count, checksum)
print("TIME_US", us)
"""

PRIME_JS = r"""function isPrime(n) {
  if (n < 2) return false;
  let d = 2;
  while (d * d <= n) { if (n % d === 0) return false; d += 1; }
  return true;
}
const t0 = process.hrtime.bigint();
let i = 2, count = 0, checksum = 0;
while (i < 50000) { if (isPrime(i)) { count += 1; checksum += i; } i += 1; }
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log('RESULT ' + count + ' ' + checksum);
console.log('TIME_US ' + us);
"""

PRIME_PHP = r"""<?php
function is_prime($n) {
    if ($n < 2) return false;
    $d = 2;
    while ($d * $d <= $n) { if ($n % $d == 0) return false; $d += 1; }
    return true;
}
$t0 = hrtime(true);
$i = 2; $count = 0; $checksum = 0;
while ($i < 50000) { if (is_prime($i)) { $count += 1; $checksum += $i; } $i += 1; }
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $count $checksum\n";
echo "TIME_US $us\n";
"""

PRIME_RS = r"""use std::time::Instant;
fn is_prime(n: i64) -> bool {
    if n < 2 { return false; }
    let mut d: i64 = 2;
    while d * d <= n { if n % d == 0 { return false; } d += 1; }
    true
}
fn main() {
    let t0 = Instant::now();
    let mut i: i64 = 2;
    let mut count: i64 = 0;
    let mut checksum: i64 = 0;
    while i < 50000 { if is_prime(i) { count += 1; checksum += i; } i += 1; }
    let us = t0.elapsed().as_micros();
    println!("RESULT {} {}", count, checksum);
    println!("TIME_US {}", us);
}
"""

# --------------------------------------------------------------------------
# Workload B: JSON API round-trip
# --------------------------------------------------------------------------
JSON_MATRIX = r"""fn build_rows(n: int) {
    let rows = list();
    let i = 0;
    while i < n {
        rows.push(map("id", i, "sku", "SKU-" + string(i), "qty", (i % 7) + 1, "price", (i * 37) % 1000));
        i += 1;
    }
    return rows;
}
fn main() {
    let rows = build_rows(200);
    let t0 = now_ms();
    let total = 0;
    let last_len = 0;
    let r = 0;
    while r < 600 {
        let s = json_write(rows);
        let back = json_parse(s);
        last_len = s.len();
        let i = 0;
        while i < back.len() {
            let row = back[i];
            total = total + row["price"] * row["qty"] + row["id"];
            i += 1;
        }
        r += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(total) + " " + string(last_len));
    println("TIME_US " + string(us));
    return 0;
}
"""

JSON_PY = r"""import json, time
def build_rows(n):
    return [{"id": i, "sku": "SKU-" + str(i), "qty": (i % 7) + 1,
             "price": (i * 37) % 1000} for i in range(n)]
rows = build_rows(200)
t0 = time.perf_counter_ns()
total = 0
last_len = 0
for r in range(600):
    s = json.dumps(rows, separators=(",", ":"))
    back = json.loads(s)
    last_len = len(s)
    for row in back:
        total += row["price"] * row["qty"] + row["id"]
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", total, last_len)
print("TIME_US", us)
"""

JSON_JS = r"""function buildRows(n) {
  const rows = [];
  for (let i = 0; i < n; i++) {
    rows.push({id: i, sku: "SKU-" + i, qty: (i % 7) + 1, price: (i * 37) % 1000});
  }
  return rows;
}
const rows = buildRows(200);
const t0 = process.hrtime.bigint();
let total = 0, lastLen = 0;
for (let r = 0; r < 600; r++) {
  const s = JSON.stringify(rows);
  const back = JSON.parse(s);
  lastLen = s.length;
  for (let i = 0; i < back.length; i++) {
    const row = back[i];
    total += row.price * row.qty + row.id;
  }
}
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + total + " " + lastLen);
console.log("TIME_US " + us);
"""

JSON_PHP = r"""<?php
function build_rows($n) {
    $rows = [];
    for ($i = 0; $i < $n; $i++) {
        $rows[] = ["id" => $i, "sku" => "SKU-" . $i,
                   "qty" => ($i % 7) + 1, "price" => ($i * 37) % 1000];
    }
    return $rows;
}
$rows = build_rows(200);
$t0 = hrtime(true);
$total = 0;
$last_len = 0;
for ($r = 0; $r < 600; $r++) {
    $s = json_encode($rows);
    $back = json_decode($s, true);
    $last_len = strlen($s);
    foreach ($back as $row) {
        $total += $row["price"] * $row["qty"] + $row["id"];
    }
}
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $total $last_len\n";
echo "TIME_US $us\n";
"""

# --------------------------------------------------------------------------
# Workload C: string-keyed map lookups
# --------------------------------------------------------------------------
MAP_MATRIX = r"""fn build_table(n: int) {
    let t = map();
    let i = 0;
    while i < n {
        t["k" + string(i)] = (i * 37 + 11) % 997;
        i += 1;
    }
    return t;
}
fn main() {
    let t = build_table(64);
    let t0 = now_ms();
    let acc = 0;
    let r = 0;
    while r < 100000 {
        let key = "k" + string(r % 64);
        acc = acc + t[key];
        r += 1;
    }
    let us = (now_ms() - t0) * 1000;
    println("RESULT " + string(acc) + " 100000");
    println("TIME_US " + string(us));
    return 0;
}
"""

MAP_PY = r"""import time
def build_table(n):
    return {"k" + str(i): (i * 37 + 11) % 997 for i in range(n)}
t = build_table(64)
t0 = time.perf_counter_ns()
acc = 0
for r in range(100000):
    acc += t["k" + str(r % 64)]
us = (time.perf_counter_ns() - t0) // 1000
print("RESULT", acc, 100000)
print("TIME_US", us)
"""

MAP_JS = r"""function buildTable(n) {
  const t = {};
  for (let i = 0; i < n; i++) t["k" + i] = (i * 37 + 11) % 997;
  return t;
}
const t = buildTable(64);
const t0 = process.hrtime.bigint();
let acc = 0;
for (let r = 0; r < 100000; r++) acc += t["k" + (r % 64)];
const us = Number((process.hrtime.bigint() - t0) / 1000n);
console.log("RESULT " + acc + " 100000");
console.log("TIME_US " + us);
"""

MAP_PHP = r"""<?php
function build_table($n) {
    $t = [];
    for ($i = 0; $i < $n; $i++) $t["k" . $i] = ($i * 37 + 11) % 997;
    return $t;
}
$t = build_table(64);
$t0 = hrtime(true);
$acc = 0;
for ($r = 0; $r < 100000; $r++) {
    $acc += $t["k" . ($r % 64)];
}
$us = intdiv(hrtime(true) - $t0, 1000);
echo "RESULT $acc 100000\n";
echo "TIME_US $us\n";
"""

SOURCES = {
    "prime": {"matrix": PRIME_MATRIX, "py": PRIME_PY, "js": PRIME_JS,
              "php": PRIME_PHP, "rs": PRIME_RS},
    "json": {"matrix": JSON_MATRIX, "py": JSON_PY, "js": JSON_JS,
             "php": JSON_PHP},
    "map": {"matrix": MAP_MATRIX, "py": MAP_PY, "js": MAP_JS,
            "php": MAP_PHP},
}

BASELINE_KEYS = ("php", "python", "node", "rust")


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


def geo(values):
    values = [v for v in values if v and v > 0]
    if not values:
        return None
    return math.exp(sum(math.log(v) for v in values) / len(values))


def resolve_container(cfg, explicit=""):
    """Best-effort container name for the Spid3r 1.0.0 in-Docker runs."""
    name = explicit or str(cfg.get("container") or "")
    if name:
        return name
    try:
        from . import docker_probe as dp
        containers, _ = dp.list_containers()
        found, _, _ = dp.resolve_container(cfg, containers)
        return found or ""
    except Exception:
        return ""


def detect_tools(cfg, opts):
    """Probe toolchains. Missing entries are False, never guessed."""
    tools = {}
    probes = [
        ("spid3r", ["spid3r", "--version"]),
        ("php", ["php", "-r", "echo PHP_VERSION;"]),
        ("python", [sys.executable, "-c",
                    "import sys;print(sys.version.split()[0])"]),
        ("node", ["node", "--version"]),
        ("rustc", ["rustc", "--version"]),
    ]
    for key, cmd in probes:
        rc, out, err, _ = _call(cmd, timeout=20)
        tools[key] = {"available": rc == 0,
                      "version": _first_line(out) if rc == 0 else ""}
        if rc != 0 and key != "rustc":
            tools.setdefault("_errors", {})[key] = _first_line(err)

    container = ""
    # Windows mode never touches Docker: no resolve, no docker exec, no cp.
    if opts.get("include_container", False):
        container = resolve_container(cfg, opts.get("container", ""))
    tools["container"] = {"available": False, "name": container,
                          "version": "", "spid3r": False}
    if container:
        rc, out, err, _ = _call(
            ["docker", "exec", container, "spid3r", "--version"], timeout=30)
        tools["container"] = {
            "available": rc == 0, "name": container,
            "version": _first_line(out) if rc == 0 else "",
            "spid3r": rc == 0,
            "error": "" if rc == 0 else _first_line(err),
        }
    return tools


# --------------------------------------------------------------------------
# Variant construction
# --------------------------------------------------------------------------
def _build_artifacts(wid, stem, work, tools, opts, warnings, timeout):
    """Compile bytecode / native / rust artifacts. Returns a dict of paths."""
    built = {}
    # These artifacts are only consumed by host (Windows) variants.
    if not opts.get("include_windows", True) or not tools["spid3r"]["available"]:
        return built
    for label, cmd, dest in [
        ("bytecode", ["spid3r", "compile", stem + ".matrix",
                      "-o", stem + ".matrixc"], stem + ".matrixc"),
        ("native", ["spid3r", "build", stem + ".matrix", "-o",
                    stem + "_native" + EXE, "--release"],
         stem + "_native" + EXE),
    ]:
        if label == "native" and not opts.get("include_native", True):
            continue
        rc, out, err, dt = _call(cmd, cwd=work, timeout=timeout)
        if rc == 0 and os.path.isfile(os.path.join(work, dest)):
            built[label] = os.path.join(work, dest)
        else:
            warnings.append("%s: %s build failed (%s)"
                            % (wid, label, _first_line(err) or _first_line(out)
                               or "rc=%s" % rc))
    if wid == "prime" and tools.get("rustc", {}).get("available"):
        dest = stem + "_rs" + EXE
        rc, out, err, _ = _call(
            ["rustc", "-O", stem + ".rs", "-o", dest],
            cwd=work, timeout=timeout)
        if rc == 0 and os.path.isfile(os.path.join(work, dest)):
            built["rust"] = os.path.join(work, dest)
        else:
            warnings.append("prime: rustc build failed (%s)"
                            % (_first_line(err) or "rc=%s" % rc))
    return built


def _variants(wid, stem, work, tools, opts, built, warnings, timeout):
    """Return list of dicts: key, label, group, source, cmd.

    ``source`` records WHERE a number came from ("windows" or "docker").
    Docker variants are never silently mixed with host baselines: the report
    tags each row and names the container so the different environment is
    visible to anyone reading the numbers.
    """
    v = []
    pyexe = sys.executable
    want_windows = bool(opts.get("include_windows", True))
    want_docker = bool(opts.get("include_container", False))

    def add(key, label, group, source, cmd):
        v.append({"key": key, "label": label, "group": group,
                  "source": source, "cmd": cmd})

    # --- installed Windows / host toolchains -------------------------------
    if want_windows:
        if opts.get("include_references", True):
            if tools.get("rustc", {}).get("available") and built.get("rust"):
                add("rust", "Rust -O native", "reference", "windows",
                    [built["rust"]])
            if tools["node"]["available"]:
                add("node", "Node %s" % tools["node"]["version"].lstrip("v").split(".")[0],
                    "reference", "windows", ["node", stem + ".js"])
        if tools["php"]["available"]:
            add("php", "PHP %s" % ".".join(tools["php"]["version"].split(".")[:2]),
                "baseline", "windows", ["php", stem + ".php"])
        if tools["python"]["available"]:
            add("python", "Python %s" % ".".join(
                tools["python"]["version"].split(".")[:2]),
                "baseline", "windows", [pyexe, stem + ".py"])

        # --- Spid3r installed on the host ---------------------------------
        if tools["spid3r"]["available"]:
            ver = tools["spid3r"]["version"].replace("spid3r ", "").split(" ")[0]
            add("spid3r_tw", "Spid3r %s tree-walk" % ver, "spid3r",
                "windows", ["spid3r", "run", stem + ".matrix"])
            add("spid3r_vm", "Spid3r %s VM" % ver, "spid3r",
                "windows", ["spid3r", "run", "--engine", "vm", stem + ".matrix"])
            add("spid3r_vm_noopt", "Spid3r %s VM --no-opt" % ver, "spid3r",
                "windows", ["spid3r", "run", "--engine", "vm", "--no-opt",
                            stem + ".matrix"])
            if built.get("bytecode"):
                add("spid3r_bytecode", "Spid3r %s bytecode exec" % ver, "spid3r",
                    "windows", ["spid3r", "exec", stem + ".matrixc"])
            if built.get("native"):
                add("spid3r_native", "Spid3r %s native --release" % ver,
                    "spid3r", "windows", [built["native"]])

    # --- deployed Spid3r inside the Docker container -----------------------
    # Docker is ADDITIVE, not a replacement: host baselines are the measuring
    # stick and must stay so slowdown ratios remain meaningful and honest.
    c = tools.get("container") or {}
    if want_docker and c.get("spid3r"):
        name = c["name"]
        remote = "/tmp/hlc_rt_%s.matrix" % stem
        rc, out, err, _ = _call(
            ["docker", "cp", os.path.join(work, stem + ".matrix"),
             "%s:%s" % (name, remote)], timeout=90)
        if rc != 0:
            warnings.append("%s: docker cp into %s failed (%s)"
                            % (wid, name, _first_line(err) or "rc=%s" % rc))
        else:
            cver = c["version"].replace("spid3r ", "").split(" ")[0]
            tag = "Docker: %s" % name
            add("spid3r_docker_tw",
                "Spid3r %s tree-walk (%s)" % (cver, tag), "spid3r",
                "docker", ["docker", "exec", name, "spid3r", "run", remote])
            add("spid3r_docker_vm",
                "Spid3r %s VM (%s)" % (cver, tag), "spid3r",
                "docker", ["docker", "exec", name, "spid3r", "run",
                           "--engine", "vm", remote])
    elif want_docker and c.get("name"):
        warnings.append("container %s has no spid3r binary; in-Docker "
                        "variants skipped" % c["name"])
    elif want_docker:
        warnings.append("Docker mode selected but no container with a Spid3r "
                        "binary resolved; in-Docker variants skipped")
    return v


def _cleanup_container(tools):
    c = tools.get("container") or {}
    if c.get("spid3r"):
        _call(["docker", "exec", c["name"], "sh", "-c",
               "rm -f /tmp/hlc_rt_*.matrix /tmp/hlc_rt_*.matrixc"],
              timeout=30)


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def run(cfg, out_dir, log=None, container=""):
    """Execute the comparison and write artifacts into ``out_dir``.

    Returns the full data dict. Never raises: all problems become warnings
    plus a non-ok status, so the HTTP load test result is never lost.
    """
    raw_opts = dict(DEFAULT_OPTIONS)
    raw_opts.update(cfg.get("runtime_comparison") or {})
    opts = dict(raw_opts)
    if container:
        opts["container"] = container
    repeats = max(1, min(20, int(opts.get("repeats") or 5)))
    timeout = max(60, int(opts.get("timeout_s") or 300))
    wanted = [w for w in (opts.get("workloads") or []) if w in WORKLOAD_INFO]
    if not wanted:
        wanted = list(WORKLOAD_INFO)
    runtime_env = str(cfg.get("runtime_environment") or "windows").strip().lower()
    if runtime_env not in RUNTIME_ENVIRONMENT_LABELS:
        runtime_env = "windows"
    env_label = RUNTIME_ENVIRONMENT_LABELS[runtime_env]
    want_windows = bool(opts.get("include_windows", True))
    want_docker = bool(opts.get("include_container", False))

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
            "map_rounds": MAP_ROUNDS, "repeats": repeats,
            "runtime_environment": runtime_env,
            "runtime_environment_label": env_label,
            "include_windows": want_windows,
            "include_container": want_docker,
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
    if opts.get("write_sources", True):
        # A stable output dir (runtime-comparison/) is reused between runs;
        # clear stale programs so `sources/` always matches this run.
        shutil.rmtree(sources_dir, ignore_errors=True)
        os.makedirs(sources_dir, exist_ok=True)
    try:
        _log(log, "detecting language toolchains")
        tools = detect_tools(cfg, opts)
        data["environment"] = {
            "runtime_environment": env_label,
            "comparison_sources": ("Windows + Docker" if want_windows and want_docker
                                   else "Windows only" if want_windows
                                   else "Docker only"),
            "spid3r_host": tools["spid3r"]["version"] or "not available",
            "spid3r_container": (tools["container"]["version"]
                                 or "not available"),
            "spid3r_container_name": tools["container"]["name"] or "",
            "php": tools["php"]["version"] or "not available",
            "python": tools["python"]["version"] or "not available",
            "node": tools["node"]["version"] or "not available",
            "rustc": tools.get("rustc", {}).get("version") or "not available",
            "platform": sys.platform,
        }
        if not want_windows and not want_docker:
            warnings.append("Neither installed Windows runtimes nor Docker "
                            "variants are enabled; comparison cannot be produced.")
            data["status"] = "unavailable"
            data["summary"] = summarize_data(data)
            _finish(data, out_dir, opts, log)
            return data
        if want_windows and not (tools["php"]["available"]
                                 or tools["python"]["available"]):
            warnings.append("Baseline unavailable: neither PHP nor Python is "
                            "installed on the host, so slowdown ratios cannot "
                            "be computed (measured times are still reported).")
        if not (tools["spid3r"]["available"]
                or (tools.get("container") or {}).get("spid3r")):
            warnings.append("No Spid3r binary found on host or in the container "
                            "(%s) - comparison cannot be produced." % env_label)
            data["status"] = "unavailable"
            data["summary"] = summarize_data(data)
            _finish(data, out_dir, opts, log)
            return data

        for wid in wanted:
            info = WORKLOAD_INFO[wid]
            _log(log, "workload %s (%s): generating sources" % (wid, info["id"]))
            stem = wid
            for ext, text in SOURCES[wid].items():
                name = "%s.%s" % (stem, ext)
                with open(os.path.join(work, name), "w",
                          encoding="utf-8", newline="\n") as f:
                    f.write(text)
                if opts.get("write_sources", True):
                    with open(os.path.join(sources_dir, name), "w",
                              encoding="utf-8", newline="\n") as f:
                        f.write(text)

            built = _build_artifacts(wid, stem, work, tools, opts,
                                     warnings, timeout)
            variants = _variants(wid, stem, work, tools, opts, built,
                                 warnings, timeout)
            rows = []
            for var in variants:
                comps, walls, results = [], [], []
                failed = ""
                for _ in range(repeats):
                    rc, out, err, wall = _call(var["cmd"], cwd=work,
                                               timeout=timeout)
                    if rc != 0:
                        failed = _first_line(err) or "rc=%s" % rc
                        break
                    res, cms = _parse_output(out)
                    if res is None:
                        failed = "missing RESULT/TIME_US output"
                        break
                    results.append(res)
                    comps.append(cms)
                    walls.append(wall)
                if failed or not comps:
                    warnings.append("%s / %s skipped: %s"
                                    % (wid, var["label"],
                                       failed or "no measurements"))
                    continue
                # Integrity guard: identical work in every language.
                observed = set(results)
                if len(observed) != 1 or next(iter(observed)) != info["expected"]:
                    warnings.append(
                        "%s / %s REJECTED: checksum %r != expected %r"
                        % (wid, var["label"], sorted(observed),
                           info["expected"]))
                    continue
                rows.append({
                    "key": var["key"], "runtime": var["label"],
                    "group": var["group"],
                    "source": var.get("source", "windows"),
                    "result": results[0],
                    "compute_ms": round(statistics.median(comps), 3),
                    "compute_min_ms": round(min(comps), 3),
                    "compute_max_ms": round(max(comps), 3),
                    "wall_ms": round(statistics.median(walls), 2),
                    "compute_runs_ms": [round(x, 3) for x in comps],
                })
            verified = len(rows) >= 2 and all(
                r["result"] == info["expected"] for r in rows)
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
        _finish(data, out_dir, opts, log)
        return data
    except Exception as ex:
        warnings.append("unexpected error: %s: %s" % (type(ex).__name__, ex))
        data["status"] = "error"
        data["summary"] = {"verdict": "Comparison failed: %s" % ex}
        _finish(data, out_dir, opts, log)
        return data
    finally:
        _cleanup_container(tools) if "tools" in locals() else None
        shutil.rmtree(work, ignore_errors=True)


def _finish(data, out_dir, opts, log):
    json_path = os.path.join(out_dir, "runtime_comparison.json")
    html_path = os.path.join(out_dir, "runtime_comparison.html")
    sources_dir = os.path.join(out_dir, "sources")
    data["artifacts"] = {
        "json": "runtime_comparison.json",
        "html": "runtime_comparison.html",
        "sources": ("sources/" if (opts.get("write_sources", True)
                                   and os.path.isdir(sources_dir)) else ""),
    }
    atomic_json(json_path, data)
    build_html(data, html_path)
    s = data.get("summary") or {}
    _log(log, "status=%s | %s" % (data["status"],
                                  s.get("verdict", "no verdict")),
         "warn" if data["status"] not in ("ok",) else "info")
    _log(log, "wrote %s" % html_path)
    return data


def summarize_data(data):
    """Compute slowdown ratios. Pure function - also used by selftest."""
    by_wid = {}
    for wid, w in (data.get("workloads") or {}).items():
        if not w.get("verified"):
            continue
        by_wid[wid] = {r["key"]: r["compute_ms"] for r in w["rows"]}

    slowdown = {}
    spid3r_keys = sorted({r["key"] for w in (data.get("workloads") or {}).values()
                          for r in w["rows"]
                          if r["group"] == "spid3r"})
    labels = {r["key"]: r["runtime"]
              for w in (data.get("workloads") or {}).values()
              for r in w["rows"]}
    for key in spid3r_keys:
        entry = {"label": labels.get(key, key), "vs_php": {},
                 "vs_python": {}}
        for base in ("php", "python"):
            ratios = {}
            for wid, vals in by_wid.items():
                if key in vals and base in vals and vals[base] > 0:
                    ratios[wid] = round(vals[key] / vals[base], 3)
            g = geo(list(ratios.values()))
            entry["vs_" + base] = {"workloads": ratios,
                                   "geomean": round(g, 3) if g else None}
        slowdown[key] = entry

    all_ratios = []
    for e in slowdown.values():
        for b in ("vs_php", "vs_python"):
            all_ratios.extend(e[b]["workloads"].values())
    best = min(all_ratios) if all_ratios else None
    worst = max(all_ratios) if all_ratios else None
    if not by_wid:
        verdict = "No verified workloads - comparison unavailable."
    elif best is None:
        # Guard: baselines exist but no Spid3r variant was measured, so there
        # are no ratios. Never claim Spid3r "matched or beat" anything.
        verdict = ("No PHP/Python baseline or Spid3r variant could be measured "
                   "- slowdown ratios unavailable.")
    elif best > 1:
        verdict = ("Spid3r is slower than PHP and Python on every measured "
                   "workload (%.2fx-%.2fx)." % (best, worst))
    else:
        verdict = "Spid3r matched or beat a baseline in at least one workload."
    return {
        "workloads_verified": len(by_wid),
        "workloads_total": len(data.get("workloads") or {}),
        "variants": sum(len(w["rows"]) for w in
                        (data.get("workloads") or {}).values()),
        "slowdown_range": {"min": round(best, 3) if best else None,
                           "max": round(worst, 3) if worst else None},
        "slowdown": slowdown,
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
padding:20px 24px;margin-top:20px}
h2{font-size:17px;margin:0 0 12px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:14px;margin:18px 0 6px}
p{margin:0 0 10px}.note{font-size:12px;color:var(--muted)}
code{font-family:var(--mono);font-size:.88em;background:#f0f2f6;border:1px
solid #e6e9f0;border-radius:4px;padding:1px 5px}
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
.bartrack{background:#eef1f6;border-radius:4px;height:15px;overflow:hidden}
.barfill{height:100%;border-radius:4px}
.barval{text-align:right;font-family:var(--mono);color:var(--muted)}
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
    """WHERE a comparison row was measured: 'windows', 'docker' or 'unavailable'."""
    src = row.get("source")
    if src in ("windows", "docker"):
        return src  # v2 authoritative
    if (str(row.get("key") or "").startswith("spid3r_docker_")
            or "(Docker)" in str(row.get("runtime") or "")):
        return "docker"  # unambiguous evidence even in a v1 archive
    if provenance_recorded:
        return "windows"
    return "unavailable"  # never invent a source for an unrecorded v1 row


def build_html(data, out_path):
    """Render a single-file, dependency-free HTML report."""
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

    P = ["<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">",
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         "<title>Runtime Comparison - %s</title>" % _e(data.get("generated_utc")),
         "<style>%s</style></head><body><div class=\"wrap\">" % _CSS]

    status = data.get("status", "unknown")
    bcls = {"ok": "ok", "partial": "warn", "unavailable": "bad",
            "error": "bad"}.get(status, "warn")
    P.append('<header class="top"><h1>Language Runtime Comparison</h1>')
    P.append('<p class="sub">Spid3r vs PHP / Python / Node / Rust &middot; '
             'generated %s &middot; checksum-verified, no simulated values</p>'
             % _e(data.get("generated_utc")))
    env_label = ((data.get("environment") or {}).get("runtime_environment")
                 or (data.get("config_meta") or {}).get("runtime_environment_label")
                 or ("Windows - installed runtimes" if provenance_recorded
                     else "provenance not recorded (schema v1)"))
    P.append('<span class="badge %s">status: %s</span>' % (bcls, _e(status)))
    P.append('<span class="badge">runtime: %s</span>' % _e(env_label))
    P.append('<span class="badge">%s workloads</span>'
             % _e(s.get("workloads_verified", 0)))
    P.append('<span class="badge">%s variants</span>' % _e(s.get("variants", 0)))
    repeats = (data.get("config_meta") or {}).get("repeats", 5)
    P.append('<span class="badge">median of %s repeats</span>' % _e(repeats))
    P.append("</header>")

    rng = s.get("slowdown_range") or {}
    P.append('<div class="kpis">')
    for n, l in [("%s&times;" % _f(rng.get("min")), "best Spid3r slowdown"),
                 ("%s&times;" % _f(rng.get("max")), "worst Spid3r slowdown"),
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
            mx = max(r["compute_ms"] for r in rows) or 1
            for r in rows:
                color = {"spid3r": "#f85606", "baseline": "#2563eb",
                         "reference": "#94a3b8"}.get(r.get("group"), "#94a3b8")
                pct = max(1.0, r["compute_ms"] / mx * 100)
                src = row_source(r, provenance_recorded=provenance_recorded)
                if src == "docker":
                    tag = '<span class="srctag">Docker</span>'
                elif src == "unavailable":
                    tag = '<span class="srctag">source unavailable</span>'
                else:
                    tag = ""
                P.append('<div class="barrow"><div class="barlabel">%s%s</div>'
                         '<div class="bartrack"><div class="barfill" '
                         'style="width:%.1f%%;background:%s"></div></div>'
                         '<div class="barval">%s ms</div></div>'
                         % (_e(r["runtime"]), tag, pct, color,
                            _f(r["compute_ms"], 3)))
            P.append("<table><thead><tr><th>Runtime / engine</th><th>Where</th>"
                     "<th class=\"num\">Median ms</th><th class=\"num\">Min</th>"
                     "<th class=\"num\">Max</th></tr></thead><tbody>")
            for r in rows:
                cls = {"spid3r": " class=\"spid3r\"",
                       "baseline": " class=\"base\""}.get(r.get("group"), "")
                src = row_source(r, provenance_recorded=provenance_recorded)
                where = ("Docker container" if src == "docker"
                         else "Windows host" if src == "windows"
                         else "unavailable (provenance not recorded)")
                P.append("<tr%s><td>%s</td><td>%s</td>"
                         "<td class=\"num\">%s</td>"
                         "<td class=\"num\">%s</td><td class=\"num\">%s</td></tr>"
                         % (cls, _e(r["runtime"]), _e(where),
                            _f(r["compute_ms"], 3),
                            _f(r["compute_min_ms"], 3),
                            _f(r["compute_max_ms"], 3)))
            P.append("</tbody></table>")
        else:
            P.append('<p class="note">No verified variants for this workload.</p>')
        P.append("</section>")

    sd = s.get("slowdown") or {}
    if sd:
        P.append('<section class="card"><h2>Slowdown ratios</h2>')
        P.append('<p class="note">Spid3r time divided by baseline time. '
                 'Values above 1.00&times; mean Spid3r is slower. '
                 'Geomean covers all verified workloads.</p>')
        wids = sorted({k for e in sd.values()
                       for k in (e.get("vs_php", {}).get("workloads") or {})})
        P.append("<table><thead><tr><th rowspan=\"2\">Spid3r variant</th>")
        for b in ("PHP", "Python"):
            P.append('<th class="num" colspan="%d">vs %s</th>'
                     % (len(wids) + 1, b))
        P.append("</tr><tr>")
        for _ in range(2):
            for wid in wids:
                P.append('<th class="num">%s</th>' % _e(WORKLOAD_INFO.get(
                    wid, {}).get("id", wid)))
            P.append('<th class="num">geo</th>')
        P.append("</tr></thead><tbody>")
        for key, entry in sd.items():
            P.append('<tr class="spid3r"><td>%s</td>' % _e(entry.get("label")))
            for b in ("vs_php", "vs_python"):
                vals = (entry.get(b) or {}).get("workloads") or {}
                for wid in wids:
                    v = vals.get(wid)
                    P.append('<td class="num">%s</td>'
                             % ("%.2f&times;" % v if v else "n/a"))
                g = (entry.get(b) or {}).get("geomean")
                P.append('<td class="num"><strong>%s</strong></td>'
                         % ("%.2f&times;" % g if g else "n/a"))
            P.append("</tr>")
        P.append("</tbody></table></section>")

    P.append('<section class="card"><h2>Environment &amp; method</h2>')
    P.append("<table><thead><tr><th>Component</th><th>Version</th></tr>"
             "</thead><tbody>")
    for k, v in env.items():
        P.append("<tr><td>%s</td><td><code>%s</code></td></tr>"
                 % (_e(k), _e(v)))
    P.append("</tbody></table>")
    if docker_rows:
        P.append('<p class="note"><strong>Docker rows run in a different '
                 'environment</strong> than the host baselines (container CPU / '
                 'filesystem / image). The container name is printed in each '
                 'Docker variant label. Ratios against host PHP / Python are '
                 'therefore approximate and are never presented as an '
                 'apples-to-apples deployment benchmark.</p>')
    elif (provenance_recorded
          and not (data.get("config_meta") or {}).get("include_container")):
        P.append('<p class="note">Windows runtime mode: only toolchains '
                 'installed on this host were measured. No Docker container '
                 'was contacted and no container variant is present.</p>')
    else:
        P.append('<p class="note">Provenance not recorded for this archived run '
                 '(schema v1); the measured sources cannot be determined.</p>')
    P.append("<h3>Method</h3><ul>"
             "<li>Identical algorithm and pinned constants in every language.</li>"
             "<li>Each variant must print the exact expected checksum or it is "
             "rejected and never published.</li>"
             "<li>In-process timing (startup excluded); median of %s repeats.</li>"
             "<li>Spid3r uses <code>now_ms()</code> (1 ms resolution), so every "
             "workload is sized to run well above 1 ms.</li>"
             "<li>Missing toolchains are skipped with a warning, never faked.</li>"
             "</ul>" % _e(repeats))
    P.append('<p class="note">Raw data: <code>runtime_comparison.json</code>. '
             'Generated programs are kept in <code>sources/</code>.</p>')
    P.append("</section>")

    warns = data.get("warnings") or []
    if warns:
        P.append('<section class="card"><h2>Warnings</h2><ul>')
        for w in warns:
            P.append("<li>%s</li>" % _e(w))
        P.append("</ul></section>")

    P.append("<footer>Heavy-Load Checker runtime comparison &middot; "
             "schema v%s &middot; single-file report, no JavaScript, no "
             "external resources.</footer>" % _e(SCHEMA_VERSION))
    P.append("</div></body></html>")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(P))
    return out_path


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
        print("  %-38s PHP %6s  Python %6s"
              % (e.get("label"),
                 ("%.2fx" % e["vs_php"]["geomean"]
                  if e["vs_php"]["geomean"] else "n/a"),
                 ("%.2fx" % e["vs_python"]["geomean"]
                  if e["vs_python"]["geomean"] else "n/a")), file=out)
    for w in (data.get("warnings") or [])[:8]:
        print("  WARN: %s" % w, file=out)
    print("\nArtifacts: runtime_comparison.json / runtime_comparison.html",
          file=out)
    return s
