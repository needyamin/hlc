#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Native (non-Docker) target lifecycle for Heavy-Load Checker.

Starts or stops the local server process described by a targets/<site>.json
spec's ``native.start_cmd``. The PID record goes to runs/targets/<site-id>
.pid.json so a run can be stopped deterministically. This replaces the
removed lab_up.py / lab_down.py Docker helpers.

Usage:
  python scripts/native_target.py <site-id> start   # spawn + wait for health
  python scripts/native_target.py <site-id> stop    # terminate by PID record
  python scripts/native_target.py <site-id> status

Safety model: the spec file is trusted local configuration, but nothing is
ever passed through a shell. Commands are parsed into argv form by
targetspec.native_start_argv()/native_build_argv() and executed with
os.spawnve (no shell, no string concatenation). Windows termination uses
the kernel32 API directly via ctypes. ``site_id`` is validated against a
strict charset before it touches any filesystem path.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from heavyload import target_manager  # noqa: E402
from heavyload import targetspec  # noqa: E402
from heavyload.runner import http_probe  # noqa: E402
from heavyload.util import atomic_json  # noqa: E402

PID_DIR = os.path.join(ROOT, "runs", "targets")
_SITE_RE = re.compile(r"[A-Za-z0-9._-]+")


def _check_site_id(site_id):
    if not _SITE_RE.fullmatch(site_id or ""):
        raise ValueError("site_id must match [A-Za-z0-9._-]+: %r" % site_id)
    return site_id


def _pid_path(site_id):
    _check_site_id(site_id)
    return os.path.join(PID_DIR, "%s.pid.json" % site_id)


def _load_pid(site_id):
    path = _pid_path(site_id)
    if not os.path.isfile(path):
        return None
    try:
        import pathlib
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return int(data.get("pid") or 0) or None
    except Exception:
        return None


def _alive(pid):
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes
            SYNCHRONIZE = 0x00100000
            h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            alive = bool(h)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
            return alive
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _terminate(pid, force=False):
    """Windows: kernel32 TerminateProcess via ctypes (no subprocess).
    POSIX: SIGTERM/SIGKILL. Terminates the named process only."""
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_TERMINATE = 0x0001
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False,
                                                   pid)
            if h:
                ctypes.windll.kernel32.TerminateProcess(
                    h, 1 if force else 0)
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        import signal
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
        return True
    except Exception:
        return False


def _stop_pid(pid, timeout=8):
    _terminate(pid, force=False)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    _terminate(pid, force=True)
    return not _alive(pid)


def _wait_ready(spec, deadline_s=20):
    """Probe the target's health path until it answers (best effort)."""
    cfg = {"target_url": "http://localhost:%d/" % spec["host_port"],
           "health_path": spec["health_path"], "_derived": {
               "scheme": "http", "host": "localhost",
               "port": spec["host_port"], "base_path": "/",
               "origin": "http://localhost:%d" % spec["host_port"]}}
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        r = http_probe(cfg, spec["health_path"], timeout=3)
        if 200 <= r["status"] < 500:          # any HTTP answer = alive
            return True, r["status"]
        time.sleep(0.5)
    return False, None


def main(argv):
    if not argv or len(argv) < 2:
        print(__doc__)
        return 2
    site_id, action = argv[0], argv[1].lower()
    spec_path = targetspec.find_spec(site_id, root=ROOT)
    if not spec_path:
        print("No target spec found for %r" % site_id, file=sys.stderr)
        return 2
    spec, err = targetspec.load_spec(spec_path)
    if err:
        print("Invalid spec: %s" % err, file=sys.stderr)
        return 2
    _check_site_id(spec["site_id"] or site_id)

    if action == "status":
        pid = _load_pid(site_id)
        alive = _alive(pid)
        ok, st = _wait_ready(spec, deadline_s=2)
        print(json.dumps({"site_id": site_id, "pid": pid, "alive": alive,
                          "health_status": st}, indent=2))
        return 0

    if action == "stop":
        pid = _load_pid(site_id)
        if not pid:
            print("No PID record for %s (nothing started by this tool)."
                  % site_id)
            return 0
        stopped = _stop_pid(pid)
        try:
            os.remove(_pid_path(site_id))
        except OSError:
            pass
        state = "stopped" if stopped else "STILL RUNNING"
        print("%s pid %d %s" % (site_id, pid, state))
        return 0 if stopped else 1

    if action == "start":
        argv_start, cwd, resolve_err = target_manager.resolve_argv_and_cwd(
            spec, ROOT)
        if not targetspec.native_start_argv(spec):
            print("Spec %s has no native.start_cmd; start the target "
                  "manually, then run the benchmark against port %d."
                  % (site_id, spec["host_port"]))
            return 0
        if resolve_err or not argv_start or not cwd:
            print("Cannot start %s: %s" % (site_id, resolve_err),
                  file=sys.stderr)
            return 1
        existing = _load_pid(site_id)
        if _alive(existing):
            print("%s already running as pid %d" % (site_id, existing))
            return 0
        import subprocess
        build_argv = targetspec.native_build_argv(spec)
        if build_argv:
            print("Building:", " ".join(str(a) for a in build_argv))
            b_exe = target_manager._resolve_executable(build_argv[0], cwd)
            if not b_exe:
                print("Build executable not found: %s" % build_argv[0],
                      file=sys.stderr)
                return 1
            subprocess.run([b_exe] + build_argv[1:], cwd=cwd, check=True)
        env = dict(os.environ)
        env["HLC_TARGET_SITE"] = str(site_id)
        env["HLC_PORT"] = str(spec["host_port"])
        os.makedirs(PID_DIR, exist_ok=True)
        log_fp = open(os.path.join(PID_DIR, "%s.log" % site_id), "wb")
        popen_kw = {
            "cwd": cwd, "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fp, "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kw["creationflags"] = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
        proc = subprocess.Popen(argv_start, **popen_kw)
        pid = int(proc.pid)
        log_fp.close()
        atomic_json(_pid_path(site_id),
                    {"site_id": site_id, "pid": pid,
                     "start_cmd": argv_start, "cwd": cwd,
                     "host_port": spec["host_port"],
                     "started_utc": time.strftime(
                         "%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        print("Started %s as pid %d (cmd: %s)"
              % (site_id, pid, " ".join(str(a) for a in argv_start)))
        if spec["native"].get("ready_probe", True):
            ok, st = _wait_ready(spec)
            print("Health probe %s: %s"
                  % (spec["health_path"],
                     ("HTTP %s" % st) if ok else
                     "no answer within the ready timeout"))
            return 0 if ok else 1
        return 0

    print("Unknown action %r (start | stop | status)" % action,
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
