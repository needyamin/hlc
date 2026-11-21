#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Measured child-process resource usage (peak memory, CPU time).

Cross-language benchmark rows carry not just compute time but the resources
the run actually consumed:

  * ``peak_rss_bytes``  - peak resident set / working set of the child
  * ``cpu_user_s``      - user-mode CPU seconds consumed by the child
  * ``cpu_kernel_s``    - kernel-mode CPU seconds consumed by the child

Every value is MEASURED by the OS or reported as ``None`` ("unavailable")
- nothing is estimated. Provenance is recorded in ``method`` so a report
can state exactly how each number was obtained.

Windows: a poller reads the child's working-set counters through an opened
process handle (GetProcessMemoryInfo) while it runs, then queries
GetProcessTimes after exit - both are accurate kernel-accounting values, not
samplings, for the CPU side. POSIX: ``os.wait4`` returns the child's
``rusage`` (ru_maxrss / ru_utime / ru_stime) directly.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time


def _win_run(cmd, cwd, timeout, env):
    """Windows implementation via ctypes + kernel accounting handles."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32")
    psapi = ctypes.WinDLL("psapi")

    class PMEWS(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t)]

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_ulong),
                    ("dwHighDateTime", ctypes.c_ulong)]

    def _ft_seconds(ft):
        return ((ft.dwHighDateTime << 32) | ft.dwLowDateTime) / 1e7

    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    handle = None
    for _ in range(25):                      # ~250 ms total; fast-exit guard
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, p.pid)
        if handle:
            break
        time.sleep(0.01)
    peak = 0
    stop_poll = threading.Event()

    def poll_ws():
        nonlocal peak
        m = PMEWS()
        while not stop_poll.is_set() and handle:
            m.cb = ctypes.sizeof(m)
            if psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle),
                                          ctypes.byref(m), m.cb):
                peak = max(peak, int(m.WorkingSetSize),
                           int(m.PeakWorkingSetSize))
            stop_poll.wait(0.004)

    poller = None
    if handle:
        poller = threading.Thread(target=poll_ws, daemon=True)
        poller.start()
    t0 = time.perf_counter()
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
        rc = p.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        out, err = p.communicate()
        rc = -1
    wall_ms = (time.perf_counter() - t0) * 1000.0
    if timed_out:
        err = ((err or "") + "\ntimeout after %ss" % timeout).strip()
    stop_poll.set()
    user_s = kernel_s = None
    if handle:
        if poller:
            poller.join(timeout=2)
        # Final read after exit: PeakWorkingSetSize is retained on the open
        # handle, so a short-lived peak cannot be missed by the poller.
        m = PMEWS()
        m.cb = ctypes.sizeof(m)
        if psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle),
                                      ctypes.byref(m), m.cb):
            peak = max(peak, int(m.PeakWorkingSetSize))
        ct, et, kt, ut = (FILETIME(), FILETIME(), FILETIME(), FILETIME())
        if kernel32.GetProcessTimes(
                ctypes.c_void_p(handle), ctypes.byref(ct), ctypes.byref(et),
                ctypes.byref(kt), ctypes.byref(ut)):
            user_s = _ft_seconds(ut)
            kernel_s = _ft_seconds(kt)
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    return {
        "rc": rc, "stdout": out or "", "stderr": err or "",
        "wall_ms": wall_ms,
        "peak_rss_bytes": peak if peak > 0 else None,
        "cpu_user_s": round(user_s, 4) if user_s is not None else None,
        "cpu_kernel_s": round(kernel_s, 4) if kernel_s is not None else None,
        "method": ("windows GetProcessMemoryInfo + GetProcessTimes"
                   if peak > 0 or user_s is not None else
                   "windows (process handle unavailable)"),
    }


def _posix_run(cmd, cwd, timeout, env):
    """POSIX implementation: os.wait4 IS the reaper, so rusage is exact.

    Reader threads drain the pipes (no deadlock), wait4 collects the child's
    rusage, and Popen's own reaper is bypassed by pre-setting returncode so
    no later wait() can reap twice.
    """
    import errno
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, env=env)
    chunks = {"out": [], "err": []}

    def drain(stream, key):
        try:
            for line in stream:
                chunks[key].append(line)
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    readers = [
        threading.Thread(target=drain, args=(p.stdout, "out"), daemon=True),
        threading.Thread(target=drain, args=(p.stderr, "err"), daemon=True),
    ]
    for r in readers:
        r.start()
    t0 = time.perf_counter()
    deadline = t0 + max(1.0, float(timeout))
    status = None
    ru = None
    timed_out = False
    while True:
        try:
            pid_w, status, ru = os.wait4(p.pid, os.WNOHANG)
        except ChildProcessError:
            status = 0
            break
        if pid_w == p.pid:
            break
        if time.perf_counter() > deadline:
            timed_out = True
            try:
                p.kill()
            except Exception:
                pass
            try:
                _, status, ru = os.wait4(p.pid, 0)
            except ChildProcessError:
                status = 0
            break
        time.sleep(0.005)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    for r in readers:
        r.join(timeout=5)
    rc = -1 if (status is None or status == -1) \
        else os.waitstatus_to_exitcode(status) \
        if hasattr(os, "waitstatus_to_exitcode") else status
    if rc < 0 and status == 0:
        rc = 0
    stderr_text = "".join(chunks["err"])
    if timed_out:
        stderr_text = (stderr_text + "\ntimeout after %ss" % timeout).strip()
    # Pre-set returncode so Popen never calls waitpid again (ECHILD guard).
    p.returncode = rc
    peak = user_s = kernel_s = None
    if ru is not None:
        peak = ru.ru_maxrss
        if sys.platform != "darwin":
            peak = ru.ru_maxrss * 1024      # Linux reports KiB; macOS bytes
        user_s = ru.ru_utime
        kernel_s = ru.ru_stime
    return {
        "rc": rc, "stdout": "".join(chunks["out"]),
        "stderr": stderr_text,
        "wall_ms": wall_ms,
        "peak_rss_bytes": peak if peak else None,
        "cpu_user_s": round(user_s, 4) if user_s is not None else None,
        "cpu_kernel_s": round(kernel_s, 4) if kernel_s is not None else None,
        "method": ("posix wait4 rusage" if ru is not None else
                   "posix (rusage unavailable)"),
    }


def run_measured(cmd, cwd=None, timeout=60, env=None):
    """Run ``cmd`` and measure the child's resources. Never raises.

    Returns a dict with rc/stdout/stderr/wall_ms plus the measured fields
    (None when the platform could not provide them - never a guess).
    """
    if sys.platform == "win32":
        fn = _win_run
    else:
        fn = _posix_run
    try:
        return fn(cmd, cwd, timeout, env)
    except Exception as ex:
        return {
            "rc": -1, "stdout": "", "stderr": "%s: %s"
            % (type(ex).__name__, ex),
            "wall_ms": 0.0, "peak_rss_bytes": None, "cpu_user_s": None,
            "cpu_kernel_s": None, "method": "error",
        }
