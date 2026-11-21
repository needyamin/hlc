#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic heavy-load engine: threaded HTTP generator, phase controller,
safety guardrails, raw-log aggregation and result assembly.

The load model is a fixed pool of worker threads whose ACTIVE count is varied
by the phase controller. Each worker keeps one keep-alive connection, issues a
weighted-random GET, records the outcome to a bounded queue, and a dedicated
writer thread streams records to raw.jsonl. Memory therefore stays bounded
even for the 24-minute / 300-concurrency profile.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import random
import sys
import threading
import time
from collections import defaultdict

from . import http_client
from .latency import Stopwatch, ms, now_ns
from .samplers import SamplerSet
from .util import (atomic_json, clamp, host_free_gb, percentiles,
                   pin_current_process, safe_div, utc)


class Benchmark:
    def __init__(self, cfg, run_dir, log=None,
                 env_info=None, run_id=""):
        self.cfg = cfg
        self.run_dir = run_dir
        self.log = log
        self.env_info = env_info or {}
        self.run_id = run_id
        self.t0 = time.perf_counter()

        d = cfg["_derived"]
        self.host, self.port, self.scheme = d["host"], d["port"], d["scheme"]
        self.paths = d["weighted_paths"]
        self.peak = d["peak_concurrency"]
        self.total_schedule_s = d["schedule_s"]

        self.queue = queue.Queue(maxsize=200000)
        self.workers_stop = threading.Event()
        self.stop_requested = threading.Event()
        self.active_count = 0
        self.current_phase = "preflight"
        self.phase_no = 0
        self.phase_total = len(cfg["phases"])
        self.boundaries = []
        self.abort = {"flag": False, "reason": "", "at_s": None, "phase": ""}
        self.executed_phases = []

        self.clock_lock = threading.Lock()
        self.c_total = 0
        self.c_errors = 0
        self.c_connect_errors = 0
        self.c_status = defaultdict(int)
        self.c_bytes = 0
        self.c_consecutive_failures = 0
        self.generator_pinned = False
        self.started_utc = utc()

        self.raw_path = os.path.join(run_dir, "raw.jsonl")
        self.status_path = os.path.join(run_dir, "status.json")
        self.samplers = None
        self.threads = []
        # Per-step windows inside multi-step phases (ramp / spike): recorded
        # live by _run_phase so raw.jsonl can be re-aggregated per concurrency
        # level for the scalability analysis.
        self.step_windows = []

    # ------------------------------------------------------------- logging --
    def _say(self, msg, level="info"):
        if self.log:
            self.log.add(msg, level)
        else:
            print("[hlc] %s" % msg, flush=True)

    # -------------------------------------------------------- status output --
    def _status(self, state, extra=None):
        elapsed = time.perf_counter() - self.t0
        s = {
            "state": state,
            "run_id": self.run_id,
            "phase": self.current_phase,
            "phase_no": self.phase_no,
            "phase_total": self.phase_total,
            "elapsed_s": round(elapsed, 1),
            "schedule_s": self.total_schedule_s,
            "progress_pct": round(clamp(100.0 * elapsed / max(1, self.total_schedule_s), 0, 100), 1),
            "active_concurrency": self.active_count,
            "requests": self.c_total,
            "errors": self.c_errors,
            "error_pct": round(100.0 * safe_div(self.c_errors, self.c_total), 3),
            "rps": round(self.c_total / max(1.0, elapsed), 1),
            "bytes_mib": round(self.c_bytes / 1048576.0, 2),
            "aborted": self.abort["flag"],
            "abort_reason": self.abort["reason"],
            "sample": (self.samplers.snapshot() if self.samplers else {}),
            "updated": utc(),
        }
        if extra:
            s.update(extra)
        try:
            atomic_json(self.status_path, s)
        except Exception:
            pass
        return s

    # ------------------------------------------------------------- counters --
    def _count(self, rec):
        st = rec["status"]
        err = bool(rec["error"]) or st == 0 or st >= 500
        with self.clock_lock:
            self.c_total += 1
            self.c_bytes += rec["bytes"]
            self.c_status[str(st)] += 1
            if err:
                self.c_errors += 1
            if st == 0:
                self.c_connect_errors += 1
            if err:
                self.c_consecutive_failures += 1
            else:
                self.c_consecutive_failures = 0
            # Per-second bins and latency percentiles are rebuilt from
            # raw.jsonl by _aggregate_requests(); no duplicate in-memory copies.

    def _counters(self):
        with self.clock_lock:
            return dict(total=self.c_total, errors=self.c_errors,
                        connect_errors=self.c_connect_errors,
                        consecutive_failures=self.c_consecutive_failures,
                        status=dict(self.c_status), bytes=self.c_bytes)

    # --------------------------------------------------------------- worker --
    def _worker(self, idx):
        rng = random.Random(7919 + idx * 1009)
        base_headers = {
            "User-Agent": "heavy-load-checker/1.0",
            "Accept": "*/*",
            "Accept-Encoding": "identity",
        }
        base_headers.update({str(k): str(v)
                             for k, v in (self.cfg.get("headers") or {}).items()})
        conn = None
        keep = bool(self.cfg.get("keep_alive", True))
        timeout = self.cfg["request_timeout_s"]
        while not self.workers_stop.is_set():
            if idx >= self.active_count:
                time.sleep(0.008)
                continue
            path = rng.choice(self.paths)
            # "scheduled" = the moment this worker decided the next request was
            # due. The gap to Stopwatch.start_ns is generator-side scheduling
            # lag (GIL / thread contention), which must not be blamed on the
            # target server.
            scheduled_ns = now_ns()
            scheduled_t = round(time.perf_counter() - self.t0, 4)
            sw = Stopwatch(scheduled_ns=scheduled_ns)
            status, nbytes, err = 0, 0, ""
            connect_ms = 0.0
            try:
                if conn is None:
                    conn = http_client.make_connection(
                        self.scheme, self.host, self.port, timeout)
                    tc = now_ns()
                    conn.connect()
                    sw.mark_connect()
                    connect_ms = round(ms(now_ns() - tc), 2)
                conn.request("GET", path, headers=base_headers)
                resp = conn.getresponse()
                sw.mark_ttfb()          # headers received = time to first byte
                body = resp.read()
                sw.mark_total()         # full body received
                status, nbytes = resp.status, len(body)
                if not keep:
                    conn.close()
                    conn = None
            except Exception as ex:
                err = type(ex).__name__
                sw.mark_total()
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                conn = None
            timing = sw.as_dict()
            timing["connect_ms"] = connect_ms
            if timing.get("latency_ms") is None:
                timing["latency_ms"] = 0.0
            rec = {
                "t": round(time.perf_counter() - self.t0, 4),
                "scheduled_t": scheduled_t,
                "start_t": round(scheduled_t +
                                 (timing.get("dispatch_delay_ms") or 0.0)
                                 / 1000.0, 4),
                "phase": self.current_phase,
                "path": path,
                "status": status,
                "bytes": nbytes,
                "error": err,
            }
            rec.update(timing)
            self._count(rec)
            try:
                self.queue.put_nowait(rec)
            except queue.Full:
                pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    def _writer(self):
        with open(self.raw_path, "w", encoding="utf-8") as f:
            while True:
                rec = self.queue.get()
                if rec is None:
                    break
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    # ----------------------------------------------------------- guardrails --
    def _guard_check(self, last_state):
        g = self.cfg["guardrails"]
        c = self._counters()
        snap = self.samplers.snapshot() if self.samplers else {}
        # The memory ceiling now protects the LOAD GENERATOR itself (there is
        # no server-side memory visibility in a client-only run): a generator
        # that out-grows its ceiling would corrupt its own percentiles.
        gen_rss = snap.get("gen_rss_mib")
        if gen_rss is not None and gen_rss > float(g["max_anon_mib"]):
            last_state["over_run"] += 1
        else:
            last_state["over_run"] = 0
        if last_state["over_run"] >= int(g["consecutive_samples"]):
            return ("load generator working set %.1f MiB exceeded the %.0f MiB "
                    "software ceiling for %d consecutive samples"
                    % (gen_rss or 0, g["max_anon_mib"], last_state["over_run"]))
        free = snap.get("host_free_gb")
        if free is not None and 0 < free < float(g["min_host_free_gb"]):
            return ("host available RAM %.2f GB fell below the %.1f GB floor"
                    % (free, g["min_host_free_gb"]))
        if c["total"] >= 100:
            ep = 100.0 * safe_div(c["errors"], c["total"])
            if ep > float(g["max_error_rate_pct"]):
                return ("error rate %.2f%% exceeded the %.1f%% guardrail "
                        "after %d requests" % (ep, g["max_error_rate_pct"],
                                               c["total"]))
        if c["consecutive_failures"] >= int(g["max_consecutive_failures"]):
            return ("%d consecutive request failures (target appears down)"
                    % c["consecutive_failures"])
        return None

    def _set_abort(self, reason):
        if self.abort["flag"]:
            return
        self.abort = {"flag": True, "reason": reason,
                      "at_s": round(time.perf_counter() - self.t0, 2),
                      "phase": self.current_phase}
        self.active_count = 0
        self._say("GUARD ABORT: %s" % reason, "error")

    # --------------------------------------------------------------- phases --
    def _sleep_window(self, seconds, label, probe_hz):
        """Sleep in 1 s ticks but remain responsive to a stop request."""
        self.samplers.set_phase(label)
        self.samplers.set_probe_hz(probe_hz)
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            if self.stop_requested.is_set():
                return False
            time.sleep(min(0.5, max(0.0, end - time.perf_counter())))
            self._status("running")
        return True

    def _run_phase(self, p, last_state):
        name = p["name"]
        self.current_phase = name
        self.samplers.set_phase(name)
        self.samplers.set_probe_hz(p["probe_hz"])
        steps = p.get("steps")
        dur = p["duration_s"]
        start = time.perf_counter() - self.t0
        self._say("PHASE %s | %s | %ds | concurrency=%s | probes=%.1fHz"
                  % (name, p["label"], dur,
                     str(steps) if steps else p["concurrency"], p["probe_hz"]))
        last_guard = 0.0
        last_step_i = None
        step_start = None
        step_c = None

        def close_step(t_end):
            # A step window needs enough dwell time to be worth aggregating;
            # sub-second boundary slivers are merged into the next step.
            if step_start is not None and t_end - step_start >= 0.75:
                self.step_windows.append({
                    "phase": name, "role": p["role"],
                    "concurrency": step_c,
                    "start_s": round(step_start, 3),
                    "end_s": round(t_end, 3),
                    "duration_s": round(t_end - step_start, 3),
                })

        while True:
            el = time.perf_counter() - self.t0 - start
            if el >= dur or self.abort["flag"]:
                break
            if self.stop_requested.is_set() and not self.abort["flag"]:
                self._set_abort("run stopped by user from the interface")
                break
            if steps:
                i = min(len(steps) - 1, int(len(steps) * el / float(dur)))
                c = steps[i]
            else:
                c = p["concurrency"]
            now_t = time.perf_counter() - self.t0
            if last_step_i != (i if steps else 0):
                close_step(now_t)
                step_start = now_t
                step_c = c
                last_step_i = i if steps else 0
            self.active_count = min(int(c), self.peak)
            now = time.perf_counter()
            if now - last_guard >= 1.0:
                last_guard = now
                reason = self._guard_check(last_state)
                if reason:
                    self._set_abort(reason)
                    break
                self._status("running")
            time.sleep(0.05)
        end = time.perf_counter() - self.t0
        close_step(end)
        self.active_count = 0
        row = {"name": name, "label": p["label"], "role": p["role"],
               "start_s": round(start, 2), "end_s": round(end, 2),
               "duration_s": round(end - start, 2),
               "concurrency": max(steps or [p["concurrency"]]),
               "probe_hz": p["probe_hz"]}
        self.boundaries.append(row)
        self.executed_phases.append(name)

    # ---------------------------------------------------------- aggregation --
    @staticmethod
    def _is_error(st, err):
        return bool(err) or st == 0 or st >= 500

    def _aggregate_requests(self):
        by_phase = defaultdict(lambda: {"lat": [], "lat200": [], "n": 0,
                                        "err": 0, "bytes": 0,
                                        "status": defaultdict(int)})
        by_path = defaultdict(lambda: {"lat": [], "n": 0, "err": 0,
                                       "status": defaultdict(int)})
        bins = defaultdict(lambda: {"lat": [], "n": 0, "err": 0})
        overall_lat, overall_lat200 = [], []
        ttfb_all, ttfb_200, dispatch_all, body_all, as_scheduled = [], [], [], [], []
        status = defaultdict(int)
        total = errors = connect_errors = nbytes = 0
        bad_lines = 0
        # Per-step (concurrency-level) aggregation: raw.jsonl records are in
        # monotonic t order (single writer thread), so one pointer walk over
        # the sorted step windows aggregates each record exactly once.
        step_wins = sorted(self.step_windows, key=lambda w: w["start_s"])
        step_buckets = [{"win": w, "n": 0, "err": 0, "bytes": 0,
                         "lat": [], "lat200": []} for w in step_wins]
        step_ptr = 0
        with open(self.raw_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    bad_lines += 1
                    continue
                t = float(r.get("t", 0))
                while step_ptr < len(step_buckets) and \
                        t > step_buckets[step_ptr]["win"]["end_s"]:
                    step_ptr += 1
                if step_ptr < len(step_buckets):
                    b = step_buckets[step_ptr]
                    if t >= b["win"]["start_s"]:
                        st_r = int(r.get("status", 0))
                        lat_r = float(r.get("latency_ms", 0))
                        b["n"] += 1
                        b["bytes"] += int(r.get("bytes", 0))
                        if self._is_error(st_r, bool(r.get("error"))):
                            b["err"] += 1
                        b["lat"].append(lat_r)
                        if 200 <= st_r < 300:
                            b["lat200"].append(lat_r)
                st = int(r.get("status", 0))
                err = bool(r.get("error"))
                lat = float(r.get("latency_ms", 0))
                b = int(r.get("bytes", 0))
                ph, pa, t = r.get("phase", "?"), r.get("path", "/"), float(r.get("t", 0))
                bad = self._is_error(st, err)
                total += 1
                nbytes += b
                status[str(st)] += 1
                if bad:
                    errors += 1
                if st == 0:
                    connect_errors += 1
                overall_lat.append(lat)
                if 200 <= st < 300:
                    overall_lat200.append(lat)
                ttfb = r.get("ttfb_ms")
                dsp = float(r.get("dispatch_delay_ms") or 0.0)
                bod = r.get("body_ms")
                if ttfb is not None:
                    ttfb_all.append(float(ttfb))
                    if 200 <= st < 300:
                        ttfb_200.append(float(ttfb))
                if bod is not None:
                    body_all.append(float(bod))
                dispatch_all.append(dsp)
                # Coordinated-omission view: a request that waited behind the
                # generator would also have been observed by an open-loop
                # scheduler at (observed latency + dispatch delay).
                as_scheduled.append(lat + dsp)
                a = by_phase[ph]
                a["n"] += 1; a["lat"].append(lat); a["bytes"] += b
                a["status"][str(st)] += 1
                if bad:
                    a["err"] += 1
                if 200 <= st < 300:
                    a["lat200"].append(lat)
                q = by_path[pa]
                q["n"] += 1; q["lat"].append(lat); q["status"][str(st)] += 1
                if bad:
                    q["err"] += 1
                bi = bins[int(t // 10) * 10]
                bi["n"] += 1; bi["lat"].append(lat)
                if bad:
                    bi["err"] += 1

        summary = {
            "total_requests": total,
            "s1_errors": errors,
            "s1_error_pct": round(100.0 * safe_div(errors, total), 4),
            "connect_errors": connect_errors,
            "status_counts": dict(sorted(status.items())),
            "bytes_total": nbytes,
            "bytes_mib": round(nbytes / 1048576.0, 2),
            "PRIMARY_successful_200_only": percentiles(overall_lat200),
            "all_requests_latency": percentiles(overall_lat),
            "PRIMARY_ttfb_200_only": percentiles(ttfb_200),
            "ttfb_all": percentiles(ttfb_all),
            "body_ms": percentiles(body_all),
            "dispatch_delay_ms": percentiles(dispatch_all),
            "as_scheduled_latency": percentiles(as_scheduled),
        }
        phases = []
        phase_by_name = {b["name"]: b for b in self.boundaries}
        for name, a in sorted(by_phase.items(), key=lambda kv: kv[0]):
            win = phase_by_name.get(name, {})
            dur = win.get("duration_s") or max(1.0, (max(1, len(bins)) * 10))
            phases.append({
                "name": name,
                "label": win.get("label", name),
                "role": win.get("role", "active"),
                "start_s": win.get("start_s"),
                "end_s": win.get("end_s"),
                "duration_s": dur,
                "concurrency": win.get("concurrency", 0),
                "requests": a["n"],
                "rps": round(a["n"] / dur, 2),
                "errors": a["err"],
                "error_pct": round(100.0 * safe_div(a["err"], a["n"]), 3),
                "bytes_mib": round(a["bytes"] / 1048576.0, 2),
                "status_counts": dict(sorted(a["status"].items())),
                "latency_200": percentiles(a["lat200"]),
                "latency_all": percentiles(a["lat"]),
            })
        for b in self.boundaries:
            if b["name"] not in by_phase:
                phases.append({
                    "name": b["name"], "label": b["label"], "role": b["role"],
                    "start_s": b["start_s"], "end_s": b["end_s"],
                    "duration_s": b["duration_s"],
                    "concurrency": b["concurrency"], "requests": 0, "rps": 0.0,
                    "errors": 0, "error_pct": 0.0, "bytes_mib": 0.0,
                    "status_counts": {}, "latency_200": percentiles([]),
                    "latency_all": percentiles([]),
                })
        phases.sort(key=lambda x: (x.get("start_s") is None,
                                   x.get("start_s") or 0, x["name"]))
        paths = []
        for pa, q in sorted(by_path.items(), key=lambda kv: -kv[1]["n"]):
            paths.append({
                "path": pa, "requests": q["n"],
                "error_pct": round(100.0 * safe_div(q["err"], q["n"]), 3),
                "latency": percentiles(q["lat"]),
                "status_counts": dict(sorted(q["status"].items())),
            })
        timeline = []
        for t in sorted(bins):
            a = bins[t]
            pl = percentiles(a["lat"])
            timeline.append({
                "t": t, "requests": a["n"], "rps": round(a["n"] / 10.0, 2),
                "errors": a["err"],
                "error_pct": round(100.0 * safe_div(a["err"], a["n"]), 3),
                "p50": pl["p50"], "p95": pl["p95"], "p99": pl["p99"],
                "phase": self._phase_at(t + 5),
            })
        step_metrics = []
        for b in step_buckets:
            w = b["win"]
            pl200 = percentiles(b["lat200"])
            plall = percentiles(b["lat"])
            step_metrics.append({
                "phase": w["phase"], "role": w["role"],
                "concurrency": w["concurrency"],
                "start_s": w["start_s"], "end_s": w["end_s"],
                "duration_s": w["duration_s"],
                "requests": b["n"],
                "rps": round(b["n"] / max(0.001, w["duration_s"]), 2),
                "errors": b["err"],
                "error_pct": round(100.0 * safe_div(b["err"], b["n"]), 3),
                "bytes_mib": round(b["bytes"] / 1048576.0, 2),
                "latency_200": pl200,
                "latency_all": plall,
            })
        return summary, phases, paths, timeline, step_metrics, bad_lines

    def _phase_at(self, t):
        for b in self.boundaries:
            if b["start_s"] <= t <= b["end_s"]:
                return b["name"]
        return self.boundaries[-1]["name"] if self.boundaries else "?"

    def _aggregate_samples(self, samples):
        """Aggregate sampler series into the resources evidence block.

        Every server-side key is kept with a None value so the v2 schema and
        archived-run comparisons stay stable; a client-only run simply shows
        them as ``unavailable`` in the report. The generator-side keys carry
        the measured cost of generating the load itself.
        """
        hp = samples.get("health", []) or []
        gen = samples.get("generator", []) or []
        ok = sum(1 for x in hp if 200 <= x.get("status", 0) < 400)
        probe_lat = percentiles([x["latency_ms"] for x in hp])

        gen_cpus = [x["cpu_pct"] for x in gen if x.get("cpu_pct") is not None]
        gen_rss = [(x["t"], x["rss_mib"]) for x in gen
                   if x.get("rss_mib") is not None]
        gen_peaks = [x["peak_rss_mib"] for x in gen
                     if x.get("peak_rss_mib") is not None]
        idle_phases = {b["name"] for b in self.boundaries
                       if b.get("role") == "idle"}
        gen_idle_cpus = [x["cpu_pct"] for x in gen
                         if x.get("cpu_pct") is not None
                         and x.get("phase") in idle_phases]
        gen_cpu_idle = (sum(gen_idle_cpus) / len(gen_idle_cpus)
                        if gen_idle_cpus else None)
        host_free = [x["host_free_gb"] for x in gen
                     if x.get("host_free_gb", -1) > 0]

        return {
            # -- server-side evidence: never available from a client-only run --
            "cgroup_samples": 0,
            "stats_samples": 0,
            "health_probes": len(hp),
            "canonical_metric": "unavailable (client-only run: the generator "
                                "has no server-side memory/CPU visibility)",
            "anon_samples": 0,
            "anon_start_mib": None,
            "anon_peak_mib": None,
            "anon_end_mib": None,
            "anon_net_change_mib": None,
            "retained_floor_bins": [],
            "active_window_s": [None, None],
            "active_points": 0,
            "active_slope_mib_per_s": None,
            "active_slope_r2": None,
            "active_theil_sen_mib_per_s": None,
            "active_spearman": None,
            "gross_monotonic_fraction": None,
            "cooldown_start_mib": None,
            "cooldown_end_mib": None,
            "cooldown_retained_pct": None,
            "large_unmap_events": [],
            "large_unmap_count": 0,
            "pgscan_max": 0, "pgsteal_max": 0,
            "oom_kill_events": 0, "psi_max_avg10": None,
            "cpu_peak_pct": None,
            "cpu_mean_pct": None,
            "cpu_idle_mean_pct": None,
            "cpu_peak_pct_net": None,
            "cpu_mean_pct_net": None,
            "anon_idle_mean_mib": None,
            "anon_peak_mib_net": None,
            "pids_max": 0,
            "host_free_gb_min": (round(min(host_free), 2) if host_free
                                 else round(host_free_gb(), 2)),
            # -- load-generator evidence (measured) --------------------------
            "generator_samples": len(gen),
            "gen_cpu_peak_pct": round(max(gen_cpus), 2) if gen_cpus else None,
            "gen_cpu_mean_pct": round(sum(gen_cpus) / len(gen_cpus), 2)
            if gen_cpus else None,
            "gen_cpu_idle_mean_pct": round(gen_cpu_idle, 2)
            if gen_cpu_idle is not None else None,
            "gen_cpu_peak_pct_net": (round(max(gen_cpus) - gen_cpu_idle, 2)
                                     if gen_cpus and gen_cpu_idle is not None
                                     else None),
            "gen_rss_start_mib": round(gen_rss[0][1], 1) if gen_rss else None,
            "gen_rss_end_mib": round(gen_rss[-1][1], 1) if gen_rss else None,
            "gen_rss_peak_mib": round(max(v for _, v in gen_rss), 1)
            if gen_rss else None,
            "gen_rss_peak_mib_os": round(max(gen_peaks), 1) if gen_peaks else None,
            "gen_rss_net_change_mib": round(gen_rss[-1][1] - gen_rss[0][1], 1)
            if gen_rss else None,
            # -- availability evidence (measured) -----------------------------
            "health_success_pct": round(100.0 * safe_div(ok, len(hp)), 2)
            if hp else None,
            "health_probe_latency": probe_lat,
            # -- series -------------------------------------------------------
            "health_series": hp,
            "generator_series": gen,
        }

    # ------------------------------------------------------------------- run --
    def run(self):
        os.makedirs(self.run_dir, exist_ok=True)
        f = self.cfg.get("fairness") or {}
        if f.get("pin_host_generator") and f.get("cpuset_generator"):
            self.generator_pinned = pin_current_process(
                f["cpuset_generator"])
            self._say("Load generator CPU affinity %s -> %s"
                      % (f["cpuset_generator"],
                         "pinned" if self.generator_pinned else "FAILED"))
        self._say("Run %s | target=%s | profile=%s | schedule=%ds | vantage=client_only"
                  % (self.run_id, self.cfg["target_url"],
                     self.cfg["profile"], self.total_schedule_s))
        self._status("starting")

        self.samplers = SamplerSet(self.cfg, log=self.log, t0=self.t0)
        self.samplers.set_probe_hz(0)
        self.samplers.start()
        self._say("Instrumentation started (health probe @1Hz max, generator "
                  "self-monitoring: CPU + working set)")

        writer = threading.Thread(target=self._writer, name="hlc-writer",
                                  daemon=True)
        writer.start()
        self.threads.append(writer)
        for i in range(self.peak):
            t = threading.Thread(target=self._worker, args=(i,),
                                 name="hlc-w%03d" % i, daemon=True)
            t.start()
            self.threads.append(t)
        self._say("Started %d load-generator workers" % self.peak)

        # warm the sampler pipe before the first phase
        time.sleep(min(1.5, max(0.4, self.cfg["sample_period_s"])))
        last_state = {"over_run": 0}
        for i, p in enumerate(self.cfg["phases"]):
            if self.abort["flag"] or self.stop_requested.is_set():
                break
            self.phase_no = i + 1
            self._run_phase(p, last_state)

        # Additive, observation-only windows after a guard abort.
        if self.abort["flag"]:
            eff_cd = min(int(self.cfg["cooldown_s"]),
                         max(5, self.total_schedule_s // 4))
            eff_rp = min(int(self.cfg["reprobe_s"]),
                         max(3, self.total_schedule_s // 10))
            self.current_phase = "post-abort-cooldown"
            self._say("POST-ABORT zero-load cooldown %ds (NO HTTP, NO probes)"
                      % eff_cd)
            self._sleep_window(eff_cd, self.current_phase, 0)
            self.boundaries.append({
                "name": self.current_phase,
                "label": "Post-abort zero-load cooldown",
                "role": "cooldown",
                "start_s": round(time.perf_counter() - self.t0 - eff_cd, 2),
                "end_s": round(time.perf_counter() - self.t0, 2),
                "duration_s": eff_cd, "concurrency": 0, "probe_hz": 0,
            })
            self.current_phase = "post-abort-reprobe"
            self._say("POST-ABORT availability re-probe %ds @1Hz" % eff_rp)
            self._sleep_window(eff_rp, self.current_phase, 1)
            self.boundaries.append({
                "name": self.current_phase,
                "label": "Post-abort availability re-probe",
                "role": "reprobe",
                "start_s": round(time.perf_counter() - self.t0 - eff_rp, 2),
                "end_s": round(time.perf_counter() - self.t0, 2),
                "duration_s": eff_rp, "concurrency": 0, "probe_hz": 1,
            })

        self.active_count = 0
        self.workers_stop.set()
        for t in self.threads:
            if t is not writer:
                t.join(timeout=8)
        self.queue.put(None)          # release the writer before joining it
        writer.join(timeout=15)
        self._say("Load generator stopped; aggregating raw log ...")
        self.samplers.stop()

        summary, phases, paths, timeline, step_metrics, bad_lines = \
            self._aggregate_requests()
        samples = self.samplers.finalize()
        mem = self._aggregate_samples(samples)
        elapsed = time.perf_counter() - self.t0
        summary["overall_rps"] = round(summary["total_requests"] / max(1.0, elapsed), 2)
        summary["duration_s"] = round(elapsed, 2)

        results = {
            "meta": {
                "tool": "Heavy-Load Checker",
                "version": "2.0.0",
                "benchmark_type": "http_loadtest",
                "run_id": self.run_id,
                "started_utc": self.started_utc,
                "finished_utc": utc(),
                "target_url": self.cfg["target_url"],
                "profile": self.cfg["profile"],
                "schedule_s": self.total_schedule_s,
                "peak_concurrency": self.peak,
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "cpu_count": os.cpu_count(),
                "disclosures": [
                    "This is a SOFTWARE guardrail benchmark, not a production "
                    "capacity certificate. No memory cap, restart or "
                    "configuration change is made by the tool.",
                    "Only GET requests are issued; authenticated flows, writes "
                    "and browser-side rendering are out of scope.",
                    "Every run is client-only: the load generator has NO "
                    "server-side CPU/memory visibility. Server resource SLOs "
                    "(S5/S6/S8) are graded N/A - insufficient evidence, never "
                    "guessed.",
                    "Server-side resources CAN be measured for LOCAL targets by "
                    "the target's own runtime adapter or OS tooling outside "
                    "this tool; such numbers must never be mixed into this "
                    "report's client-only evidence.",
                    "An early guardrail abort is right-censored: SLOs that need "
                    "the full schedule are graded N/A - insufficient evidence.",
                    "HTTPS targets use an unverified TLS context because this "
                    "is a local load generator, not a TLS validator.",
                    "The target and the load generator share physical cores. "
                    "Generator saturation is monitored (generator_series) and "
                    "reported; cross-language comparisons are only meaningful "
                    "for sequential same-host runs with identical profiles.",
                    "Latency is split into connect / TTFB / full body; "
                    "dispatch_delay_ms measures load-generator scheduling lag "
                    "and as_scheduled_latency is the coordinated-omission view.",
                ],
            },
            "config": {k: v for k, v in self.cfg.items() if not k.startswith("_")},
            "environment": self.env_info,
            "abort": self.abort,
            "guardrails": self.cfg["guardrails"],
            "slo_targets": self.cfg["slo"],
            "summary": summary,
            "phases": phases,
            "paths": paths,
            "timeline": timeline,
            "step_metrics": step_metrics,
            "phase_windows": self.boundaries,
            "executed_phases": self.executed_phases,
            "resources": {k: v for k, v in mem.items()
                          if not k.endswith("_series")},
            "series": {k: mem[k] for k in
                       ("health_series", "generator_series")},
            "aggregation_meta": {
                "malformed_raw_lines": bad_lines,
                "raw_log": "raw.jsonl",
                "step_windows": len(step_metrics),
            },
            "raw_log": "raw.jsonl",
        }
        self._status("complete", {"report": "report.html",
                                  "requests": summary["total_requests"]})
        self._say("DONE: %d requests, %.3f%% errors, gen CPU peak=%s%%, abort=%s"
                  % (summary["total_requests"], summary["s1_error_pct"],
                     mem.get("gen_cpu_peak_pct"), self.abort["flag"]))
        return results

    def request_stop(self):
        self.stop_requested.set()
        self._say("Stop requested; finishing current phase safely.", "warn")
