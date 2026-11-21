"""Per-site target specs: the data model for multi-language campaigns.

A spec describes ONE website/runtime so that N sites can run through an
identical workload and be compared fairly. v2 removed the Docker launcher:
targets are started NATIVELY on this host via the ``native.start_cmd`` field
(or manually - the spec then serves as metadata). The Docker-era fields
(``image``, ``container``, cpuset limits) are tolerated on legacy specs and
ignored.

All resources live under ``targets/``. ``targets/_schema.json`` is the
machine-readable contract; ``targets/examples/`` has ready-to-edit templates.
``scripts/native_target.py`` starts/stops a spec's native process.
"""

from __future__ import annotations

import copy
import json
import os

from . import paths

SPEC_DEFAULTS = {
    "schema_version": 2,
    "site_id": "",
    "group": "",
    "language": "",
    "runtime_version": "",
    "framework": "",
    "framework_version": "",
    "image": "",
    "image_digest": "",
    "build_cmd": "",
    "start_cmd": "",
    "host_port": 8090,
    "health_path": "/health",
    "routes": [{"path": "/", "weight": 100, "method": "GET",
                "expected_status": 200, "expected_payload_sha": ""}],
    "resource_limits": {"cpus": 1.0, "cpuset_cpus": "", "mem_limit_mb": 512},
    "native": {"start_cmd": "", "working_dir": "", "build_cmd": "",
               "ready_probe": True},
    "warmup": {"duration_s": 10, "requests": 1000},
    "repeat_count": 3,
    "cooldown_between_repeats_s": 15,
    "seed_workload": {"seed": 7919, "paths_file": ""},
    "adapter": {"type": "", "endpoint": "/__hlc/runtime"},
}


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def validate(spec):
    """Return ``(normalised_spec, None)`` or ``(None, error_message)``."""
    try:
        if not isinstance(spec, dict):
            raise ValueError("spec must be a JSON object")
        s = _deep_merge(SPEC_DEFAULTS, spec)
        s["site_id"] = str(s["site_id"]).strip()
        if not s["site_id"]:
            raise ValueError("site_id is required")
        if not s["language"]:
            raise ValueError("language is required (e.g. python, node, go, java)")
        # image/container are legacy Docker-era fields: tolerated, never
        # required. A native start command is what launches a target now.
        s["host_port"] = int(s["host_port"])
        if not 1 <= s["host_port"] <= 65535:
            raise ValueError("host_port out of range")
        if not str(s["health_path"]).startswith("/"):
            raise ValueError("health_path must start with /")
        routes = s["routes"]
        if not isinstance(routes, list) or not routes:
            raise ValueError("routes must be a non-empty list")
        for r in routes:
            if not str(r.get("path", "")).startswith("/"):
                raise ValueError("each route.path must start with /")
            r["weight"] = max(1, int(r.get("weight", 1)))
            r["method"] = str(r.get("method", "GET")).upper()
            r["expected_status"] = int(r.get("expected_status", 200))
        s["repeat_count"] = max(1, min(20, int(s["repeat_count"])))
        lim = s["resource_limits"]
        lim["cpus"] = max(0.1, float(lim["cpus"]))
        lim["mem_limit_mb"] = max(64, int(lim["mem_limit_mb"]))
        native = s["native"]
        native["start_cmd"] = str(native.get("start_cmd") or "").strip()
        native["working_dir"] = str(native.get("working_dir") or "").strip()
        native["build_cmd"] = str(native.get("build_cmd") or "").strip()
        native["ready_probe"] = bool(native.get("ready_probe", True))
        s["warmup"]["duration_s"] = max(0, int(s["warmup"]["duration_s"]))
        s["cooldown_between_repeats_s"] = max(
            0, int(s["cooldown_between_repeats_s"]))
        return s, None
    except Exception as ex:
        return None, str(ex)


def load_spec(path):
    """Load and validate a spec file. Returns ``(spec, None)`` or error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as ex:
        return None, "cannot read %s: %s" % (path, ex)
    return validate(raw)


def find_spec(site_id, root=None):
    """Locate a spec by filename OR by its ``site_id`` field.

    Filenames are not required to equal site_id (the bundled examples use
    shorter names), so fall back to scanning every spec under targets/.
    """
    base = os.path.join(root or paths.ROOT, "targets")
    for rel in (site_id + ".json", site_id,
                os.path.join("examples", site_id + ".json")):
        p = os.path.join(base, rel)
        if os.path.isfile(p):
            return p
    for p in list_specs(root):
        s, err = load_spec(p)
        if not err and s.get("site_id") == site_id:
            return p
    return None


def list_specs(root=None):
    base = os.path.join(root or paths.ROOT, "targets")
    out = []
    for folder in (base, os.path.join(base, "examples")):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json") and not name.startswith("_"):
                out.append(os.path.join(folder, name))
    return out


def apply_to_config(cfg, spec):
    """Fold a validated spec into a v2 single-target config."""
    out = copy.deepcopy(cfg)
    out["target_url"] = "http://localhost:%d/" % spec["host_port"]
    out["health_path"] = spec["health_path"]
    out["paths"] = [{"path": r["path"], "weight": r["weight"]}
                    for r in spec["routes"]]
    lim = spec["resource_limits"]
    t = out.setdefault("target", {})
    for key in ("site_id", "group", "language", "runtime_version",
                "framework", "framework_version", "image", "image_digest",
                "build_cmd", "start_cmd"):
        t[key] = spec.get(key, "")
    t["stack_label"] = "%s %s / %s" % (spec["language"],
                                       spec["runtime_version"],
                                       spec["framework"])
    f = out.setdefault("fairness", {})
    f["cpuset_target"] = lim.get("cpuset_cpus", "")
    f["warmup_s"] = spec["warmup"]["duration_s"]
    f["repeats"] = spec["repeat_count"]
    f["cooldown_between_repeats_s"] = spec["cooldown_between_repeats_s"]
    f["seed"] = spec["seed_workload"]["seed"]
    f["identical_workload"] = True
    ad = spec.get("adapter") or {}
    ra = out.setdefault("runtime_adapter", {})
    ra["type"] = ad.get("type", "")
    ra["endpoint"] = ad.get("endpoint") or "/__hlc/runtime"
    return out


def native_start_argv(spec):
    """Shell-argv for the spec's native start command (used by
    scripts/native_target.py). Empty when the spec has none."""
    cmd = ((spec.get("native") or {}).get("start_cmd")
           or spec.get("start_cmd") or "").strip()
    if not cmd:
        return []
    import shlex
    return shlex.split(cmd, posix=False) if os.name == "nt" else \
        shlex.split(cmd)


def native_build_argv(spec):
    """Shell-argv for the spec's native build command (optional)."""
    cmd = str((spec.get("native") or {}).get("build_cmd") or "").strip()
    if not cmd:
        return []
    import shlex
    return shlex.split(cmd, posix=False) if os.name == "nt" else \
        shlex.split(cmd)


def native_working_dir(spec, root=None):
    """Resolved working directory for the native target process."""
    workdir = str((spec.get("native") or {}).get("working_dir") or "").strip()
    if workdir and os.path.isdir(workdir):
        return os.path.normpath(workdir)
    base = os.path.dirname(str((spec.get("_spec_path") or "")) or "") \
        if isinstance(spec.get("_spec_path"), str) else ""
    if base and os.path.isdir(base):
        return os.path.normpath(base)
    return os.path.normpath(root or paths.ROOT)
