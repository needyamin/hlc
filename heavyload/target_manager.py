#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dynamic lifecycle manager for local native benchmark targets.

When a benchmark, preflight, route discovery, or campaign targets a loopback
URL (127.0.0.1 / localhost / ::1) that is not currently listening, this module
checks ``targets/*.json`` (and ``targets/examples/*.json``) under the project
root for a matching, launchable native target spec and starts it automatically.

Honesty & safety guarantees:
  * Remote targets are NEVER auto-started or substituted.
  * Arbitrary loopback ports with no matching local spec are NEVER substituted
    with a fake server — preflight still refuses dead unknown targets.
  * Commands are executed with ``subprocess.Popen(..., shell=False)`` using
    resolved executable paths (no shell interpolation).
  * Every process auto-started by this manager is tracked in-memory and in
    ``runs/targets/<site-id>.pid.json``, and terminated cleanly on exit via
    ``atexit`` or explicit ``stop_target()`` / ``stop_all_managed()`` calls.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

from . import http_client
from . import paths
from . import targetspec
from .util import atomic_json

_SITE_RE = re.compile(r"[A-Za-z0-9._-]+")
_LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")

_LOCK = threading.RLock()
# site_id -> {"proc": Popen, "pid": int, "port": int, "root": str,
#             "log_path": str, "log_fp": file|None, "spec": dict}
_MANAGED: dict[str, dict] = {}


def _check_site_id(site_id: str) -> str:
    if not _SITE_RE.fullmatch(site_id or ""):
        raise ValueError("site_id must match [A-Za-z0-9._-]+: %r" % site_id)
    return site_id


def _pid_dir(root: str) -> str:
    return os.path.join(root, "runs", "targets")


def _pid_path(root: str, site_id: str) -> str:
    _check_site_id(site_id)
    return os.path.join(_pid_dir(root), "%s.pid.json" % site_id)


def _log_path(root: str, site_id: str) -> str:
    _check_site_id(site_id)
    return os.path.join(_pid_dir(root), "%s.log" % site_id)


def _is_loopback_host(host: str) -> bool:
    return str(host or "").strip().lower() in _LOOPBACK_HOSTS


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes
            SYNCHRONIZE = 0x00100000
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                code = ctypes.c_ulong(0)
                if ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                    STILL_ACTIVE = 259
                    return code.value == STILL_ACTIVE
                return True
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _terminate_pid(pid: int, force: bool = False) -> bool:
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if h:
                ctypes.windll.kernel32.TerminateProcess(h, 1 if force else 0)
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        import signal
        os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
        return True
    except Exception:
        return False


def _resolve_executable(cmd0: str, cwd: str) -> str | None:
    """Resolve argv[0] to an executable path without invoking a shell."""
    if not cmd0:
        return None
    low = cmd0.lower()
    if low in ("python", "python3", "python.exe", "python3.exe", "py", "py.exe"):
        return sys.executable
    # Direct path or relative to working directory (e.g. "server.exe")
    cand = cmd0 if os.path.isabs(cmd0) else os.path.join(cwd, cmd0)
    if os.path.isfile(cand):
        return os.path.normpath(cand)
    if os.name == "nt" and not low.endswith(".exe"):
        if os.path.isfile(cand + ".exe"):
            return os.path.normpath(cand + ".exe")
    found = shutil.which(cmd0)
    return os.path.normpath(found) if found else None


def resolve_argv_and_cwd(spec: dict, root: str) -> tuple[list[str] | None, str | None, str | None]:
    """Resolve a target spec into (argv, cwd, error_reason).

    Validates that the working directory, executable, and (for python/node
    script invocations) target script file all exist so unlaunchable template
    specs are cleanly skipped.
    """
    raw_argv = targetspec.native_start_argv(spec)
    if not raw_argv:
        return None, None, "no native.start_cmd"

    raw_wd = str((spec.get("native") or {}).get("working_dir") or "").strip()
    if raw_wd:
        cwd = raw_wd if os.path.isabs(raw_wd) else os.path.join(root, raw_wd)
        cwd = os.path.normpath(cwd)
        if not os.path.isdir(cwd):
            return None, None, "working_dir does not exist: %s" % raw_wd
    else:
        cwd = os.path.normpath(root)

    exe = _resolve_executable(raw_argv[0], cwd)
    if not exe:
        return None, None, "executable not found: %s" % raw_argv[0]

    argv = [exe] + list(raw_argv[1:])

    # If launching a script via python or node, verify the script file exists.
    cmd0_low = os.path.basename(raw_argv[0]).lower()
    if cmd0_low in ("python", "python3", "python.exe", "python3.exe", "node", "node.exe"):
        for arg in argv[1:]:
            if arg.startswith("-"):
                continue
            script_path = arg if os.path.isabs(arg) else os.path.join(cwd, arg)
            if not os.path.isfile(script_path):
                return None, None, "script not found: %s" % arg
            break

    return argv, cwd, None


def _all_spec_candidates(root: str) -> list[tuple[dict, str, bool]]:
    """Return [(spec, spec_path, is_example), ...] with top-level specs first."""
    base = os.path.join(root, "targets")
    out: list[tuple[dict, str, bool]] = []
    for folder, is_ex in ((base, False), (os.path.join(base, "examples"), True)):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json") and not name.startswith("_"):
                p = os.path.join(folder, name)
                sp, err = targetspec.load_spec(p)
                if not err and sp:
                    sp["_spec_path"] = p
                    out.append((sp, p, is_ex))
    return out


def _demo_fallback_spec(root: str, port: int) -> dict | None:
    """Fallback spec for port 8090 if scripts/demo_target.py exists in root."""
    if port != 8090:
        return None
    demo_script = os.path.join(root, "scripts", "demo_target.py")
    if not os.path.isfile(demo_script):
        return None
    spec, err = targetspec.validate({
        "schema_version": 2,
        "site_id": "python-demo",
        "group": "hello-json",
        "language": "python",
        "runtime_version": "%d.%d" % sys.version_info[:2],
        "framework": "http.server (stdlib)",
        "start_cmd": "python scripts/demo_target.py --port 8090",
        "host_port": 8090,
        "health_path": "/health",
        "routes": [
            {"path": "/", "weight": 40},
            {"path": "/catalog", "weight": 40},
            {"path": "/health", "weight": 20},
        ],
        "native": {
            "start_cmd": "python scripts/demo_target.py --port 8090",
            "working_dir": "",
            "build_cmd": "",
            "ready_probe": True,
        },
    })
    return spec if not err else None


def resolve_target_for_cfg(cfg: dict, root: str) -> dict | None:
    """Find a launchable local target spec matching ``cfg``'s loopback URL.

    Returns ``None`` for remote URLs or loopback ports with no matching local
    spec under ``root``.
    """
    derived = cfg.get("_derived") or {}
    host = str(derived.get("host") or "")
    port = int(derived.get("port") or 0)
    if not _is_loopback_host(host) or port <= 0:
        return None

    candidates = _all_spec_candidates(root)
    wanted_id = str((cfg.get("target") or {}).get("site_id") or "").strip()

    # 1. Exact site_id match whose host_port matches the requested URL port.
    if wanted_id:
        for sp, _, _ in candidates:
            if sp.get("site_id") == wanted_id and int(sp.get("host_port") or 0) == port:
                argv, cwd, err = resolve_argv_and_cwd(sp, root)
                if not err and argv and cwd:
                    return sp

    # 2. First launchable spec matching the requested port (top-level targets/
    #    before targets/examples/).
    for sp, _, _ in candidates:
        if int(sp.get("host_port") or 0) == port:
            argv, cwd, err = resolve_argv_and_cwd(sp, root)
            if not err and argv and cwd:
                return sp

    # 3. Practice demo_target.py fallback for default port 8090.
    fb = _demo_fallback_spec(root, port)
    if fb:
        argv, cwd, err = resolve_argv_and_cwd(fb, root)
        if not err and argv and cwd:
            return fb

    return None


def _is_reachable(cfg: dict, timeout: float = 2.0) -> tuple[bool, int]:
    """Quick HTTP reachability check against base_path or health_path."""
    d = cfg.get("_derived") or {}
    scheme = d.get("scheme") or "http"
    host = d.get("host") or "127.0.0.1"
    port = int(d.get("port") or 80)
    base_path = d.get("base_path") or "/"
    health_path = cfg.get("health_path") or "/health"

    # Prefer 127.0.0.1 when probing localhost on Windows to avoid IPv6 ::1
    # connection-refused latency when local servers bind 127.0.0.1.
    probe_host = "127.0.0.1" if host == "localhost" else host

    r = http_client.probe(scheme, probe_host, port, health_path, timeout=timeout)
    if r["status"] > 0 and not r["error"]:
        return True, r["status"]
    if base_path != health_path:
        r2 = http_client.probe(scheme, probe_host, port, base_path, timeout=timeout)
        if r2["status"] > 0 and not r2["error"]:
            return True, r2["status"]
    return False, 0


def _read_tail(path: str, max_chars: int = 400) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().strip()
        return txt[-max_chars:] if len(txt) > max_chars else txt
    except Exception:
        return ""


def start_spec(spec: dict, root: str, *, cfg: dict | None = None,
               log=None, timeout_s: float = 20.0) -> dict:
    """Start a validated target spec natively and wait until it answers HTTP."""
    site_id = _check_site_id(str(spec.get("site_id") or ""))
    port = int(spec["host_port"])
    health_path = str(spec.get("health_path") or "/health")

    argv, cwd, err = resolve_argv_and_cwd(spec, root)
    if err or not argv or not cwd:
        raise RuntimeError("Cannot start target %s: %s" % (site_id, err or "invalid command"))

    with _LOCK:
        existing = _MANAGED.get(site_id)
        if existing and existing["proc"].poll() is None:
            return {
                "started": False,
                "already_running": True,
                "site_id": site_id,
                "pid": existing["pid"],
                "port": port,
                "managed": True,
            }

        # Optional build step
        build_argv = targetspec.native_build_argv(spec)
        if build_argv:
            b_exe = _resolve_executable(build_argv[0], cwd)
            if not b_exe:
                raise RuntimeError("Build executable not found for %s: %s"
                                   % (site_id, build_argv[0]))
            if log:
                log.add("Building local target '%s': %s"
                        % (site_id, " ".join(build_argv)))
            subprocess.run([b_exe] + build_argv[1:], cwd=cwd, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        os.makedirs(_pid_dir(root), exist_ok=True)
        lp = _log_path(root, site_id)
        log_fp = open(lp, "wb")

        env = dict(os.environ)
        env["HLC_TARGET_SITE"] = site_id
        env["HLC_PORT"] = str(port)

        popen_kwargs = {
            "cwd": cwd,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fp,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.Popen(argv, **popen_kwargs)
        except Exception:
            log_fp.close()
            raise

        pid = int(proc.pid)
        _MANAGED[site_id] = {
            "proc": proc,
            "pid": pid,
            "port": port,
            "root": root,
            "log_path": lp,
            "log_fp": log_fp,
            "spec": spec,
        }
        atomic_json(_pid_path(root, site_id), {
            "site_id": site_id,
            "pid": pid,
            "start_cmd": argv,
            "cwd": cwd,
            "host_port": port,
            "managed": True,
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    if log:
        log.add("Auto-starting local target '%s' on port %d (pid %d)..."
                % (site_id, port, pid))

    probe_cfg = cfg or {
        "health_path": health_path,
        "_derived": {
            "scheme": "http",
            "host": "127.0.0.1",
            "port": port,
            "base_path": "/",
        },
    }

    deadline = time.time() + max(1.0, float(timeout_s))
    ready = False
    status = 0
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            tail = _read_tail(lp)
            stop_target(site_id, root=root)
            detail = " (%s)" % tail if tail else ""
            raise RuntimeError(
                "Local target '%s' exited immediately with code %s%s"
                % (site_id, rc, detail))
        ok, st = _is_reachable(probe_cfg, timeout=1.5)
        if ok:
            ready = True
            status = st
            break
        time.sleep(0.15)

    if not ready:
        tail = _read_tail(lp)
        stop_target(site_id, root=root)
        detail = " (%s)" % tail if tail else ""
        raise RuntimeError(
            "Local target '%s' did not become reachable on port %d within %.0fs%s"
            % (site_id, port, timeout_s, detail))

    if log:
        log.add("Local target '%s' is ready on port %d (HTTP %d, pid %d)"
                % (site_id, port, status, pid))

    return {
        "started": True,
        "already_running": False,
        "site_id": site_id,
        "pid": pid,
        "port": port,
        "status": status,
        "managed": True,
    }


def ensure_target_running(cfg: dict, root: str, *, log=None,
                          timeout_s: float = 20.0) -> dict:
    """Ensure a loopback target is reachable, auto-starting its spec if needed.

    Never starts anything for remote URLs or unknown loopback ports without a
    matching launchable spec under ``root``.
    """
    ok, st = _is_reachable(cfg, timeout=1.5)
    if ok:
        return {
            "started": False,
            "already_running": True,
            "status": st,
            "site_id": str((cfg.get("target") or {}).get("site_id") or ""),
        }

    spec = resolve_target_for_cfg(cfg, root)
    if spec is None:
        return {
            "started": False,
            "already_running": False,
            "site_id": "",
            "reason": "no matching local target spec",
        }

    return start_spec(spec, root, cfg=cfg, log=log, timeout_s=timeout_s)


def stop_target(site_id: str, root: str | None = None, timeout: float = 5.0) -> bool:
    """Stop a managed (or PID-recorded) target process cleanly."""
    with _LOCK:
        entry = _MANAGED.pop(site_id, None)

    stopped = True
    if entry is not None:
        proc = entry["proc"]
        root = root or entry.get("root")
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=2.0)
                    except Exception:
                        pass
            stopped = proc.poll() is not None
        except Exception:
            stopped = not _pid_alive(entry.get("pid"))
        finally:
            fp = entry.get("log_fp")
            if fp is not None:
                try:
                    fp.close()
                except Exception:
                    pass
    elif root:
        pp = _pid_path(root, site_id)
        if os.path.isfile(pp):
            try:
                with open(pp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                pid = int(data.get("pid") or 0)
            except Exception:
                pid = 0
            if pid and _pid_alive(pid):
                _terminate_pid(pid, force=False)
                deadline = time.time() + timeout
                while time.time() < deadline and _pid_alive(pid):
                    time.sleep(0.15)
                if _pid_alive(pid):
                    _terminate_pid(pid, force=True)
                stopped = not _pid_alive(pid)

    if root:
        try:
            os.remove(_pid_path(root, site_id))
        except OSError:
            pass
    return stopped


def stop_all_managed(root: str | None = None) -> None:
    """Stop every target process started by this process."""
    with _LOCK:
        ids = list(_MANAGED.keys())
    for sid in ids:
        try:
            stop_target(sid, root=root)
        except Exception:
            pass


def list_available_targets(root: str, *, probe_live: bool = False) -> list[dict]:
    """List launchable local target specs under ``root/targets/``."""
    seen: set[str] = set()
    rows: list[dict] = []
    for sp, spec_path, is_ex in _all_spec_candidates(root):
        sid = sp.get("site_id") or ""
        if not sid or sid in seen:
            continue
        argv, cwd, err = resolve_argv_and_cwd(sp, root)
        if err or not argv or not cwd:
            continue
        seen.add(sid)
        port = int(sp["host_port"])
        with _LOCK:
            m = _MANAGED.get(sid)
            managed = bool(m and m["proc"].poll() is None)
            pid = m["pid"] if managed else None
        running = managed
        if probe_live and not running:
            r = http_client.probe("http", "127.0.0.1", port,
                                  sp.get("health_path") or "/health",
                                  timeout=0.4)
            running = r["status"] > 0 and not r["error"]
        rows.append({
            "site_id": sid,
            "language": sp.get("language", ""),
            "runtime_version": sp.get("runtime_version", ""),
            "framework": sp.get("framework", ""),
            "host_port": port,
            "target_url": "http://127.0.0.1:%d/" % port,
            "health_path": sp.get("health_path", "/health"),
            "routes": sp.get("routes", []),
            "is_example": is_ex,
            "running": running,
            "managed": managed,
            "pid": pid,
            "spec_path": os.path.relpath(spec_path, root),
        })
    return rows


atexit.register(stop_all_managed)
