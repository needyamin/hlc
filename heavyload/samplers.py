#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instrumentation samplers: cgroup v2/v1, docker stats, optional MySQL, health.

Canonical memory metric = cgroup v2 `memory.stat anon` in RAW BYTES, converted
to MiB. `docker stats` human-readable strings (decimal MB) are kept in separate
fields and never mixed with cgroup bytes. In cgroup v1 containers `rss` is used
as the anonymous-memory proxy and is explicitly labelled as such in the report.
"""
from __future__ import annotations

import subprocess
import threading
import time

from . import docker_probe as dp
from .collectors.cgroup import CgroupCollector
from .collectors.docker_stats import parse_stats_line
from .collectors.health import probe_once
from .collectors.mysql import loop_script as mysql_loop_script
from .collectors.procfs import parse_proc_line, summarize_proc_series
from .latency import ms, now_ns
from .util import host_free_gb, utc

MEMSTAT_KEYS = {"anon", "file", "kernel", "slab", "sock", "shmem", "rss",
                "cache", "mapped_file", "pgfault", "pgmajfault", "pgscan",
                "pgsteal", "workingset_refault"}
BYTE_KEYS = {"anon", "file", "kernel", "slab", "sock", "shmem", "rss",
             "cache", "mapped_file"}
PROC_KEYS = {"VmPeak", "VmSize", "VmRSS", "VmData", "Threads"}


def _int(tok, i=1):
    try:
        return int(tok[i])
    except Exception:
        return None


def parse_cg_line(rec, line):
    tok = line.split()
    if not tok:
        return
    k = tok[0]
    if k == "CGVER" and len(tok) > 1:
        rec["cgroup_version"] = tok[1]
    elif k == "CUR":
        v = _int(tok)
        if v is not None:
            rec["current_bytes"] = v
            rec["current_mib"] = round(v / 1048576.0, 3)
    elif k == "PEK":
        v = _int(tok)
        if v is not None:
            rec["peak_bytes"] = v
            rec["peak_mib"] = round(v / 1048576.0, 3)
    elif k == "PIDS":
        v = _int(tok)
        if v is not None:
            rec["pids_current"] = v
    elif k == "PIDS_EV_max":
        v = _int(tok)
        if v is not None:
            rec["pids_events_max"] = v
    elif k == "START":
        v = _int(tok)
        if v is not None:
            rec["pid1_starttime"] = v
    elif k == "LISTEN" and len(tok) > 2:
        a, b = _int(tok), _int(tok, 2)
        if a is not None and b is not None:
            rec["listen_overflows"], rec["listen_drops"] = a, b
    elif k == "IO" and len(tok) > 2:
        a, b = _int(tok), _int(tok, 2)
        if a is not None and b is not None:
            rec["io_rbytes"], rec["io_wbytes"] = a, b
    elif k.startswith("EV_") and len(tok) > 1:
        v = _int(tok)
        if v is not None:
            rec[k[3:]] = v
    elif k.startswith("PSI_") and len(tok) > 2:
        for t_ in tok[2:]:
            if t_.startswith("avg10="):
                try:
                    rec["psi_" + k[4:] + "_avg10"] = float(t_.split("=")[1])
                except Exception:
                    pass
    elif k.startswith("PROC_") and len(tok) > 1:
        key = k[5:].rstrip(":")
        if key in PROC_KEYS:
            v = _int(tok)
            if v is not None:
                rec["proc_" + key.lower()] = v
    elif k in MEMSTAT_KEYS and len(tok) > 1:
        v = _int(tok)
        if v is not None:
            rec[k + ("_bytes" if k in BYTE_KEYS else "")] = v
            if k in BYTE_KEYS:
                rec[k + "_mib"] = round(v / 1048576.0, 3)
    elif k in ("usage_usec", "nr_throttled", "nr_periods") and len(tok) > 1:
        v = _int(tok)
        if v is not None:
            rec[k] = v


class SamplerSet:
    """Owns every background sampler thread and the shared sample lists."""

    def __init__(self, cfg, container="", mysql_container="", log=None, t0=None):
        self.cfg = cfg
        self.container = container or ""
        self.mysql_container = mysql_container or ""
        self.log = log
        self.t0 = t0 if t0 is not None else time.perf_counter()
        self.stop_evt = threading.Event()
        self.lock = threading.Lock()
        self.data = {"cgroup": [], "stats": [], "mysql": [], "health": [],
                     "proc": []}
        self.threads = []
        self.proc_handles = []
        self.phase_ref = ["preflight"]
        self.probe_hz = [1.0]
        self._procs = []
        self._cgroup_collector = None
        self.proc_summary = {}
        self.collector_mode = "none"

    def _say(self, msg):
        if self.log:
            self.log.add(msg)

    def set_phase(self, name):
        self.phase_ref[0] = name

    def set_probe_hz(self, hz):
        self.probe_hz[0] = float(hz)

    def _t(self):
        return round(time.perf_counter() - self.t0, 3)

    # ------------------------------------------------------------ cgroup --
    def _cgroup_stream(self):
        """Single low-overhead collector process; PROCJSON -> proc series."""
        if not self.container:
            return
        period = float(self.cfg.get("sample_period_s") or 1.0)
        col = CgroupCollector(self.container, period=period, log=self.log,
                              allow_legacy=True)
        if not col.start():
            self.collector_mode = "none"
            return
        self._cgroup_collector = col
        self.collector_mode = col.mode
        rec = None
        try:
            for line in col.lines():
                if self.stop_evt.is_set():
                    break
                line = line.strip()
                if line == "@@S":
                    if rec is not None:
                        self._push("cgroup", rec)
                    rec = {"t": self._t(), "wall": utc(),
                           "container": self.container,
                           "phase": self.phase_ref[0]}
                    continue
                if line.startswith("PROCJSON "):
                    rows = parse_proc_line(line)
                    if rows and rec is not None:
                        self._push("proc", {
                            "t": rec["t"], "wall": rec["wall"],
                            "phase": rec["phase"], "processes": rows,
                        })
                    continue
                if rec is not None:
                    try:
                        parse_cg_line(rec, line)
                    except Exception:
                        pass
        finally:
            if rec is not None:
                self._push("cgroup", rec)
            col.stop()

    # ------------------------------------------------------- docker stats --
    def _stats_poller(self):
        if not self.container:
            return                      # direct mode: no docker stats needed
        period = max(0.5, float(self.cfg.get("stats_period_s", 4)))
        # `docker stats` streaming yields nothing into a pipe on Docker Desktop
        # 29.8.2, so poll --no-stream instead. Parsing lives in
        # collectors/docker_stats.py so it can be unit tested.
        while not self.stop_evt.is_set():
            rc, out, _ = dp.docker_out(
                ["stats", "--no-stream", "--format", "{{json .}}"], timeout=25)
            if rc == 0:
                for line in (out or "").splitlines():
                    rec = parse_stats_line(
                        line, container=self.container, t=self._t(),
                        wall=utc(), phase=self.phase_ref[0])
                    if rec is not None:
                        self._push("stats", rec)
            self.stop_evt.wait(period)

    # -------------------------------------------------------------- mysql --
    def _mysql_stream(self):
        if not self.mysql_container:
            return
        script = mysql_loop_script(10)
        try:
            p = subprocess.Popen(
                ["docker", "exec", self.mysql_container, "sh", "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, encoding="utf-8", errors="replace")
            self._procs.append(p)
        except Exception as ex:
            self._say("MySQL sampler failed to start: %s" % ex)
            return
        rec = None
        try:
            for line in p.stdout:
                if self.stop_evt.is_set():
                    break
                line = line.strip()
                if line == "@@M":
                    if rec is not None:
                        self._push("mysql", rec)
                    rec = {"t": self._t(), "container": self.mysql_container,
                           "phase": self.phase_ref[0]}
                    continue
                if rec is not None and "\t" in line:
                    k, v = line.split("\t", 1)
                    try:
                        rec[k] = int(v)
                    except Exception:
                        pass
        finally:
            if rec is not None:
                self._push("mysql", rec)
            try:
                p.terminate()
            except Exception:
                pass

    # ------------------------------------------------------------- health --
    def _health_probe(self):
        hp = self.cfg["health_path"]
        while not self.stop_evt.is_set():
            hz = self.probe_hz[0]
            if hz <= 0:
                self.stop_evt.wait(0.25)
                continue
            t = now_ns()
            r = probe_once(self.cfg, timeout=8)
            self._push("health", {
                "t": self._t(), "wall": utc(), "phase": self.phase_ref[0],
                "path": hp, "status": r["status"], "error": r["error"],
                "latency_ms": round(ms(now_ns() - t), 2),
                "ttfb_ms": r.get("ttfb_ms"),
            })
            self.stop_evt.wait(1.0 / hz)

    # ------------------------------------------------------------ plumbing --
    def _push(self, key, rec):
        with self.lock:
            self.data[key].append(rec)

    def start(self):
        dp.kill_orphans(self.container)
        dp.kill_orphans(self.mysql_container)
        targets = [
            ("hlc-cgroup", self._cgroup_stream),
            ("hlc-stats", self._stats_poller),
            ("hlc-mysql", self._mysql_stream),
            ("hlc-health", self._health_probe),
        ]
        for name, fn in targets:
            t = threading.Thread(target=fn, name=name, daemon=True)
            t.start()
            self.threads.append(t)

    def stop(self):
        # Stop the v2 collector deterministically first (stdin handshake);
        # only legacy shell loops need the marker-based orphan cleanup, because
        # terminating the local `docker exec` client does not kill those.
        self.stop_evt.set()
        col = self._cgroup_collector
        if col is not None:
            col.stop()
        dp.kill_orphans(self.container)
        dp.kill_orphans(self.mysql_container)
        for p in self._procs:
            try:
                p.terminate()
            except Exception:
                pass
        for t in self.threads:
            t.join(timeout=6)

    def snapshot(self):
        with self.lock:
            cg = self.data["cgroup"][-1] if self.data["cgroup"] else {}
            st = self.data["stats"][-1] if self.data["stats"] else {}
            hp = list(self.data["health"][-20:])
        ok = sum(1 for x in hp if 200 <= x.get("status", 0) < 400)
        return {
            "anon_mib": cg.get("anon_mib", cg.get("rss_mib")),
            "current_mib": cg.get("current_mib"),
            "peak_mib": cg.get("peak_mib"),
            "cpu_pct": cg.get("cpu_pct", st.get("cpu_pct_stats")),
            "pids": cg.get("pids_current", st.get("pids")),
            "host_free_gb": st.get("host_free_gb", host_free_gb()),
            "probes": len(self.data["health"]),
            "recent_probe_ok_pct": round(100.0 * ok / len(hp), 1) if hp else None,
            "cgroup_samples": len(self.data["cgroup"]),
        }

    def finalize(self):
        """Derive CPU% from cpu.stat usage_usec deltas, then return all series."""
        with self.lock:
            cg = self.data["cgroup"]
            self.proc_summary = summarize_proc_series(self.data["proc"])
        prev_u, prev_t = None, None
        for rec in cg:
            u = rec.get("usage_usec")
            if u is not None and prev_u is not None:
                dt = rec["t"] - prev_t
                du = u - prev_u
                if dt > 0 and du >= 0:
                    rec["cpu_pct"] = round(du / (dt * 1e6) * 100.0, 2)
            if u is not None:
                prev_u, prev_t = u, rec["t"]
            # v1 fallback: rss is the anonymous-memory proxy
            if "anon_mib" not in rec and "rss_mib" in rec:
                rec["anon_mib"] = rec["rss_mib"]
                rec["anon_proxy"] = "cgroup v1 rss"
        return self.data
