#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configuration schema, schedule presets and validation.

The original Spid3r harness hard-coded APP/MYSQL/HOST/PORT and a frozen
24-minute schedule. Everything that was hard-coded is expressed here as
configuration so an arbitrary plain-HTTP site can be checked from the host.
"""
from __future__ import annotations

import copy
import json
import os
import re
from urllib.parse import urlparse

from .util import atomic_json

# Config schema version (distinct from heavyload.schema.SCHEMA_VERSION, which
# versions the RESULT document). v2 adds target metadata, optional targets[],
# fairness controls, browser metrics and generator isolation flags.
SCHEMA_VERSION = 2

# The runtime environment. v2.x removed container-based instrumentation: every
# run is a client-only measurement from the host. The constant is kept (with
# one value) so saved configs and the UI keep a single source of truth, and a
# legacy "docker" value is mapped to "windows" in normalize() with a note.
RUNTIME_ENVIRONMENTS = ("windows",)
RUNTIME_ENVIRONMENT_LABELS = {
    "windows": "Host (installed runtimes, client-only vantage)",
}


# ------------------------------------------------------------------ phases --
def phase(name, label, duration_s, concurrency=0, steps=None,
          probe_hz=1, role="active"):
    p = {
        "name": str(name),
        "label": str(label),
        "duration_s": int(duration_s),
        "probe_hz": float(probe_hz),
        "role": role,
    }
    if steps is not None:
        p["steps"] = [int(x) for x in steps]
    else:
        p["concurrency"] = int(concurrency)
    return p


def smoke_phases():
    """~33 s. Safe for validation and for a first look at an unknown target."""
    return [
        phase("01-plateau", "Idle plateau (C=0)", 3, 0, probe_hz=0, role="idle"),
        phase("02-warmup", "Warm-up (C=2)", 3, 2),
        phase("03-ramp", "Ramp 2-5-10", 6, steps=[2, 5, 10]),
        phase("04-sustained", "Sustained (C=10)", 6, 10, role="sustained"),
        phase("05-spike", "Spike (C=15-20)", 3, steps=[15, 20], role="spike"),
        phase("06-soak", "Short soak (C=5)", 3, 5, role="soak"),
        phase("07-zero-load", "Zero-load recovery (C=0)", 6, 0, probe_hz=0,
              role="cooldown"),
        phase("08-reprobe", "Availability re-probe", 3, 0, probe_hz=1,
              role="reprobe"),
    ]


def quick_phases():
    """~3.5 min. Default profile: meaningful ramp, sustained load, cooldown."""
    return [
        phase("01-plateau", "Idle plateau (C=0)", 15, 0, probe_hz=0, role="idle"),
        phase("02-warmup", "Warm-up (C=5)", 15, 5),
        phase("03-ramp", "Ramp 5-10-25-50", 30, steps=[5, 10, 25, 50]),
        phase("04-sustained", "Sustained heavy (C=50)", 45, 50, role="sustained"),
        phase("05-spike", "Spike / break point (C=75-100)", 15,
              steps=[75, 100], role="spike"),
        phase("06-soak", "Endurance soak (C=25)", 30, 25, role="soak"),
        phase("07-zero-load", "Zero-load recovery (C=0)", 45, 0, probe_hz=0,
              role="cooldown"),
        phase("08-reprobe", "Availability re-probe", 15, 0, probe_hz=1,
              role="reprobe"),
    ]


def heavy_phases():
    """~5.5 min. Heavy load: high ramp, sustained C=150, break-point spike."""
    return [
        phase("01-plateau", "Idle plateau (C=0)", 20, 0, probe_hz=0, role="idle"),
        phase("02-warmup", "Warm-up (C=10)", 15, 10),
        phase("03-ramp", "Ramp 25-50-100-150", 60, steps=[25, 50, 100, 150]),
        phase("04-sustained", "Sustained heavy (C=150)", 60, 150,
              role="sustained"),
        phase("05-spike", "Spike / break point (C=175-200)", 20,
              steps=[175, 200], role="spike"),
        phase("06-soak", "Endurance soak (C=75)", 45, 75, role="soak"),
        phase("07-zero-load", "Zero-load recovery (C=0)", 60, 0, probe_hz=0,
              role="cooldown"),
        phase("08-reprobe", "Availability re-probe", 15, 0, probe_hz=1,
              role="reprobe"),
    ]


def stress_phases():
    """~10.5 min. Stress conditions: very high concurrency, long soak."""
    return [
        phase("01-plateau", "Idle plateau (C=0)", 30, 0, probe_hz=0, role="idle"),
        phase("02-warmup", "Warm-up (C=20)", 20, 20),
        phase("03-ramp", "Ramp 50-100-200-300", 90, steps=[50, 100, 200, 300]),
        phase("04-sustained", "Sustained stress (C=300)", 120, 300,
              role="sustained"),
        phase("05-spike", "Spike / break point (C=350-400)", 30,
              steps=[350, 400], role="spike"),
        phase("06-soak", "Endurance soak (C=150)", 180, 150, role="soak"),
        phase("07-zero-load", "Zero-load recovery (C=0)", 120, 0, probe_hz=0,
              role="cooldown"),
        phase("08-reprobe", "Availability re-probe", 30, 0, probe_hz=1,
              role="reprobe"),
    ]


def standard_phases():
    """~24 min. The frozen schedule used for the original Spid3r benchmark."""
    return [
        phase("01-plateau", "Idle plateau (C=0)", 120, 0, probe_hz=0, role="idle"),
        phase("02-warmup", "Warm-up (C=5)", 30, 5),
        phase("03-ramp", "Ramp 5-10-25-50-100", 100, steps=[5, 10, 25, 50, 100]),
        phase("04-sustained", "Sustained heavy (C=100)", 180, 100,
              role="sustained"),
        phase("05-spike", "Spike / break point (C=200-300)", 45,
              steps=[200, 300], role="spike"),
        phase("06-soak", "Endurance soak (C=50)", 300, 50, role="soak"),
        phase("07-zero-load", "Zero-load recovery (C=0)", 600, 0, probe_hz=0,
              role="cooldown"),
        phase("08-reprobe", "Availability re-probe", 60, 0, probe_hz=1,
              role="reprobe"),
    ]


PRESETS = {
    "smoke": {"label": "Smoke (~33 s)", "phases": smoke_phases,
              "max_concurrency": 20},
    "quick": {"label": "Quick (~3.5 min)", "phases": quick_phases,
              "max_concurrency": 100},
    "heavy": {"label": "Heavy (~5.5 min)", "phases": heavy_phases,
              "max_concurrency": 200},
    "stress": {"label": "Stress (~10.5 min)", "phases": stress_phases,
               "max_concurrency": 400},
    "standard": {"label": "Standard (~24 min)", "phases": standard_phases,
                 "max_concurrency": 300},
}


# ---------------------------------------------------------------- defaults --
DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "runtime_environment": "windows",   # only value since v2 (client-only)
    "target_url": "http://localhost:8090/",
    "mode": "direct",                   # fixed: no container instrumentation
    "profile": "quick",
    "health_path": "/health",
    "paths": [{"path": "/", "weight": 100}],
    "headers": {},
    "max_concurrency": 100,
    "request_timeout_s": 12,
    "keep_alive": True,
    "sample_period_s": 1,           # generator/health sampler cadence (seconds)
    "cooldown_s": 60,               # post-abort zero-HTTP observation
    "reprobe_s": 15,                # post-abort 1 Hz availability re-probe
    "phases": quick_phases(),
    # --- v2: identity, comparability and multi-language campaigns ---
    "benchmark_mode": "live_blackbox",  # fixed: honest client-only visibility
    "target": {                     # metadata about the SITE, not the host
        "site_id": "", "group": "", "language": "", "runtime_version": "",
        "framework": "", "framework_version": "", "image": "",
        "image_digest": "", "build_cmd": "", "start_cmd": "", "stack_label": "",
    },
    "targets": [],                  # optional per-site specs; empty = single target (v1 behaviour)
    "fairness": {
        "warmup_s": 10,
        "idle_baseline_s": 10,
        "repeats": 3,
        "seed": 7919,
        "aggregate": "median",
        "cooldown_between_repeats_s": 15,
        "identical_workload": True,
        "cpuset_target": "",
        "cpuset_generator": "",
        "pin_host_generator": True,
    },
    "browser": {"enabled": False, "cdp_url": "http://127.0.0.1:9222"},
    "runtime_adapter": {"type": "", "endpoint": "/__hlc/runtime"},
    # Cross-language runtime comparison. Enabled by default so EVERY run also
    # produces runtime_comparison.json + runtime_comparison.html. All installed
    # host toolchains are checked; missing ones are skipped, never simulated.
    "runtime_comparison": {
        "enabled": True,
        "repeats": 5,                 # median of N in-process repeats
        "include_windows": True,      # installed host runtimes (checked by default)
        "include_native": True,       # spid3r build --release variant
        "include_references": True,   # compiled reference rows
        "write_sources": True,        # keep generated programs in sources/
        "timeout_s": 300,             # per-run execution timeout
        "build_timeout_s": 900,       # kotlinc / dotnet can be slow on cold start
        "workloads": ["prime", "json", "map", "text", "matmul"],
    },
    # Technology suitability assessment. Weights are relative (normalised to
    # 100 inside the module). Criteria fed only by measured data carry a
    # "measured" provenance; the documented criteria are labelled as such in
    # every report and never presented as measurements.
    "suitability": {
        "enabled": True,
        "weights": {
            "compute_performance": 20,
            "memory_efficiency": 10,
            "cpu_efficiency": 5,
            "http_throughput": 15,
            "http_latency": 10,
            "http_stability": 5,
            "ecosystem_maturity": 10,
            "ecosystem_libraries": 5,
            "maintainability": 10,
            "reliability": 5,
            "concurrency_model": 5,
        },
    },
    "generator": {"isolated": False},
    "guardrails": {
        "max_anon_mib": 8192.0,       # generator working-set ceiling (MiB)
        "consecutive_samples": 10,
        "min_host_free_gb": 3.0,
        "max_error_rate_pct": 50.0,
        "max_consecutive_failures": 50,
    },
    "slo": {
        "max_error_pct": 0.1,
        "max_p95_ms": 500.0,
        "max_p99_ms": 2000.0,
        "min_rps": 0.0,               # 0 => informational, not graded
        "max_memory_growth_mib": 256.0,
        "max_retained_pct": 120.0,
        "min_health_success_pct": 99.0,
    },
    "output_dir": "runs",
    "notes": "",
}

# Common candidate routes used by the "discover routes" helper.
ROUTE_CANDIDATES = [
    "/", "/health", "/healthz", "/status", "/api", "/api/health",
    "/login", "/catalog", "/products", "/cart", "/checkout", "/search",
    "/account", "/about", "/contact", "/robots.txt", "/favicon.ico",
    "/css/style.css", "/js/app.js", "/__hlc_missing_404",
]


def preset_phases(name):
    return copy.deepcopy(PRESETS[name]["phases"]())


def apply_profile(cfg, name):
    """Switch schedule + ceiling to a preset, preserving target/workload."""
    if name not in PRESETS:
        raise ValueError("Unknown profile %r (choose from %s)"
                         % (name, ", ".join(PRESETS)))
    out = copy.deepcopy(cfg)
    out["profile"] = name
    out["phases"] = preset_phases(name)
    out["max_concurrency"] = PRESETS[name]["max_concurrency"]
    return normalize(out)


def apply_runtime_environment(cfg, env):
    """Apply the runtime switch as one coherent preset.

    Since v2.x the only supported environment is ``windows`` (host, client
    only). The helper still exists as the single write path used by the web
    UI / API so a legacy ``docker`` request is rejected with a clear message
    instead of producing a half-configured state.
    """
    env = str(env or "").strip().lower()
    if env not in RUNTIME_ENVIRONMENTS:
        raise ValueError("runtime_environment must be one of %s "
                         "(container instrumentation was removed in v2)"
                         % ", ".join(RUNTIME_ENVIRONMENTS))
    out = {k: v for k, v in (cfg or {}).items() if not k.startswith("_")}
    out["runtime_environment"] = env
    out["mode"] = "direct"
    out["benchmark_mode"] = "live_blackbox"
    return public_config(normalize(out))


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def normalize(cfg):
    """Fill defaults, coerce types, derive the parsed target, validate."""
    raw = cfg or {}
    out = _deep_merge(DEFAULTS, raw)
    out["schema_version"] = SCHEMA_VERSION
    # v1 configs carry a key that was never implemented; drop it so it does
    # not leak into new result documents.
    out.pop("follow_redirects", None)

    # --- runtime environment --------------------------------------------------
    # Only the host (client-only) environment exists since v2. A legacy
    # "docker" value (or legacy Docker-era derived keys) is mapped to the host
    # environment and the fact is recorded, never hidden.
    legacy_docker = (
        str(raw.get("runtime_environment") or "").strip().lower() == "docker"
        or bool((raw.get("runtime_comparison") or {}).get("include_container"))
        or str(raw.get("benchmark_mode")) in ("docker_lab", "instrumented_live")
        or str(raw.get("mode")) == "docker"
        or raw.get("container") or raw.get("mysql_container"))
    env = str(out.get("runtime_environment") or "windows").strip().lower()
    if env not in RUNTIME_ENVIRONMENTS:
        env = "windows"          # self-heal a typo; never invent an environment
    out["runtime_environment"] = env
    out["mode"] = "direct"
    out["benchmark_mode"] = "live_blackbox"

    # --- legacy Docker-era keys: stripped, never published --------------------
    dropped = [k for k in ("container", "mysql_container", "stats_period_s")
               if k in out]
    for k in dropped:
        out.pop(k, None)
    rc0 = out.get("runtime_comparison") or {}
    for k in ("container", "include_container"):
        rc0.pop(k, None)
    out["runtime_comparison"] = rc0
    if legacy_docker:
        dropped = (dropped or []) + ["runtime_comparison.include_container"]
        legacy_note = ("config carried Docker-era keys (%s); container "
                       "instrumentation was removed in v2 and the run is "
                       "client-only" % ", ".join(dropped))
    else:
        legacy_note = ""

    if not isinstance(out["targets"], list):
        raise ValueError("targets must be a list of site ids / spec paths")
    out["targets"] = [str(x) for x in out["targets"]]
    # A profile switch with no explicit phase list must bring its own schedule
    # and concurrency ceiling. Saved configs carry explicit phases, so they
    # are preserved untouched.
    if "profile" in raw and "phases" not in raw and raw["profile"] in PRESETS:
        name = raw["profile"]
        out["phases"] = preset_phases(name)
        out["max_concurrency"] = PRESETS[name]["max_concurrency"]

    url = str(out["target_url"] or "").strip()
    if not url:
        raise ValueError("target_url is required")
    if "://" not in url:
        url = "http://" + url
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise ValueError("target_url must be http:// or https://")
    if not u.hostname:
        raise ValueError("target_url has no hostname")
    out["target_url"] = url
    out["_derived"] = {
        "scheme": u.scheme,
        "host": u.hostname,
        "port": u.port or (443 if u.scheme == "https" else 80),
        "base_path": u.path or "/",
        "origin": "%s://%s:%d" % (u.scheme, u.hostname,
                                  u.port or (443 if u.scheme == "https" else 80)),
    }
    if legacy_note:
        out["_derived"]["legacy_migration_note"] = legacy_note

    if str(out["mode"]) != "direct":
        raise ValueError("mode must be 'direct' (container instrumentation "
                         "was removed in v2)")

    paths = []
    for p in out["paths"] or []:
        if isinstance(p, str):
            p = {"path": p, "weight": 1}
        q = str(p.get("path") or "").strip()
        # Guard against filesystem paths (MSYS/Git-Bash path conversion of a
        # leading "/" can silently turn "/,/health" into "C:/Program Files/...").
        if ("\\" in q or " " in q or "://" in q
                or re.match(r"^/?[A-Za-z]:[\\/]", q)):
            raise ValueError(
                "invalid HTTP route %r: looks like a filesystem path. "
                "Use routes such as /, /health, /cart" % q)
        if not q.startswith("/"):
            q = "/" + q
        w = int(p.get("weight", 1))
        if w < 1:
            w = 1
        paths.append({"path": q, "weight": min(w, 1000)})
    if not paths:
        paths = [{"path": "/", "weight": 1}]
    out["paths"] = paths

    hp = str(out["health_path"] or "/health").strip()
    out["health_path"] = hp if hp.startswith("/") else "/" + hp

    phases = []
    for i, p in enumerate(out["phases"] or []):
        q = {
            "name": str(p.get("name") or "phase-%02d" % (i + 1)),
            "label": str(p.get("label") or p.get("name") or "phase"),
            "duration_s": max(1, int(p.get("duration_s", 10))),
            "probe_hz": max(0.0, min(20.0, float(p.get("probe_hz", 1)))),
            "role": str(p.get("role") or "active"),
        }
        steps = p.get("steps")
        if steps:
            q["steps"] = [max(0, int(x)) for x in steps]
            q["concurrency"] = max(q["steps"])
        else:
            q["concurrency"] = max(0, int(p.get("concurrency", 0)))
        phases.append(q)
    if not phases:
        raise ValueError("at least one phase is required")
    out["phases"] = phases

    peak = max([p["concurrency"] for p in phases] or [1])
    out["max_concurrency"] = max(1, min(1000, int(out["max_concurrency"])))
    out["_derived"]["peak_concurrency"] = max(1, min(peak, out["max_concurrency"]))
    out["_derived"]["schedule_s"] = sum(p["duration_s"] for p in phases)
    out["_derived"]["weighted_paths"] = [
        p["path"] for p in paths for _ in range(p["weight"])
    ]
    # v2: sub-second sampler cadence is allowed. cooldown/reprobe stay whole
    # seconds for backwards-compatible phase arithmetic.
    out["request_timeout_s"] = max(1.0, float(out["request_timeout_s"]))
    out["sample_period_s"] = min(10.0, max(0.05,
                                           float(out["sample_period_s"])))
    for k in ("cooldown_s", "reprobe_s"):
        out[k] = max(0, int(out[k]))
    out["guardrails"]["max_anon_mib"] = int(out["guardrails"]["max_anon_mib"])
    f = out["fairness"]
    f["repeats"] = max(1, min(20, int(f["repeats"])))
    f["seed"] = int(f["seed"])
    f["warmup_s"] = max(0, int(f["warmup_s"]))
    f["idle_baseline_s"] = max(0, int(f["idle_baseline_s"]))
    f["cooldown_between_repeats_s"] = max(0, int(f["cooldown_between_repeats_s"]))
    if f["aggregate"] not in ("median", "mean", "min"):
        f["aggregate"] = "median"
    out["browser"]["enabled"] = bool(out["browser"]["enabled"])
    ra = out["runtime_adapter"]
    ra["type"] = str(ra.get("type") or "").strip()
    if not str(ra.get("endpoint") or "").startswith("/"):
        ra["endpoint"] = "/__hlc/runtime"
    out["generator"]["isolated"] = bool(out["generator"]["isolated"])
    rc = out["runtime_comparison"] or {}
    rc["enabled"] = bool(rc.get("enabled", True))
    rc["repeats"] = max(1, min(20, int(rc.get("repeats", 5))))
    rc["include_windows"] = bool(rc.get("include_windows", True))
    rc["include_native"] = bool(rc.get("include_native", True))
    rc["include_references"] = bool(rc.get("include_references", True))
    rc["write_sources"] = bool(rc.get("write_sources", True))
    rc["timeout_s"] = max(60, min(1800, int(rc.get("timeout_s", 300))))
    rc["build_timeout_s"] = max(60, min(3600,
                                        int(rc.get("build_timeout_s", 900))))
    valid_w = [str(w) for w in (rc.get("workloads") or [])
               if str(w) in ("prime", "json", "map", "text", "matmul")]
    rc["workloads"] = valid_w or ["prime", "json", "map", "text", "matmul"]
    out["runtime_comparison"] = rc
    su = out["suitability"] or {}
    su["enabled"] = bool(su.get("enabled", True))
    w_src = su.get("weights") or {}
    w_clean = {}
    for k, v in w_src.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv >= 0:
            w_clean[str(k)] = fv
    su["weights"] = w_clean
    out["suitability"] = su
    return out


def validate(cfg):
    """Validate by normalizing; returns (normalized, None) or (None, message)."""
    try:
        return normalize(cfg), None
    except Exception as ex:
        return None, str(ex)


def load_config(path):
    if not os.path.isfile(path):
        return normalize({})
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    return normalize(raw)


def public_config(cfg):
    """Config without private derived fields, safe to send to the browser."""
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


def save_config(path, cfg):
    """Atomic write, shared with every other result artifact."""
    atomic_json(path, public_config(cfg))
