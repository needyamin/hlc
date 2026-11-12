#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local web interface + JSON API for Heavy-Load Checker.

Starts a stdlib-only HTTP server bound to 127.0.0.1 by default:

    python server.py                 # http://127.0.0.1:8787/
    python server.py --port 9000 --open

No external packages. The server never exposes itself on 0.0.0.0 unless
explicitly asked with --host, because it can drive heavy load against a target.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from heavyload import config as C  # noqa: E402
from heavyload import docker_probe as dp  # noqa: E402
from heavyload import runner  # noqa: E402
from heavyload import campaign as CAM  # noqa: E402
from heavyload import compare as CMP  # noqa: E402
from heavyload import report_compare  # noqa: E402
from heavyload import paths  # noqa: E402
from heavyload.util import LogBuffer, utc  # noqa: E402
from heavyload.paths import (  # noqa: E402
    ROOT, CONFIG_PATH, UI_DIR, RUNS_DIR, SAFE_ARTIFACTS, BENCHMARKS_DIR,
)


def runs_dir():
    """Honour config.output_dir; falls back to ROOT/runs."""
    return paths.runs_dir(ROOT, STATE.get("config"))

STATE = {
    "lock": threading.RLock(),
    "config": None,
    "log": LogBuffer(1200),
    "state": "idle",                 # idle | starting | running | complete | failed
    "run_id": "",
    "bench_holder": {},
    "last_result": None,
    "last_error": "",
    "preflight": None,
    "campaign": {
        "running": False,
        "campaign_id": "",
        "title": "",
        "current_site": "",
        "sites_total": 0,
        "sites_done": 0,
        "current_run": "",
        "log": LogBuffer(2000),
        "last_error": "",
        "report_path": "",
        "stop_requested": False,
    },
}
STATE["config"] = C.load_config(CONFIG_PATH)

# SAFE_ARTIFACTS now lives in heavyload/paths.py (single source of truth).


# --------------------------------------------------------------- run thread --
def start_run():
    with STATE["lock"]:
        if STATE["state"] in ("starting", "running"):
            return False, "A benchmark is already running."
        cfg = C.normalize(STATE["config"])
        STATE["state"] = "starting"
        STATE["run_id"] = ""
        STATE["last_result"] = None
        STATE["last_error"] = ""
        STATE["bench_holder"] = {}
        STATE["log"].add("Starting benchmark via web interface (target=%s, "
                         "profile=%s)" % (cfg["target_url"], cfg["profile"]))

    def work():
        try:
            info = runner.run_benchmark(
                cfg, ROOT, log=STATE["log"],
                preflight_result=STATE.get("preflight"),
                bench_holder=STATE["bench_holder"])
            with STATE["lock"]:
                STATE["state"] = "complete"
                STATE["run_id"] = info["run_id"]
                STATE["last_result"] = info
                C.save_config(CONFIG_PATH, cfg)
        except Exception as ex:
            tb = traceback.format_exc(limit=4)
            with STATE["lock"]:
                STATE["state"] = "failed"
                STATE["last_error"] = "%s: %s" % (type(ex).__name__, ex)
            STATE["log"].add("RUN FAILED: %s" % STATE["last_error"], "error")
            STATE["log"].add(tb.replace("\n", " | "), "error")

    t = threading.Thread(target=work, name="hlc-web-run", daemon=True)
    t.start()
    return True, "started"


def stop_run():
    with STATE["lock"]:
        b = STATE["bench_holder"].get("bench")
        if STATE["state"] not in ("starting", "running") or b is None:
            return False, "No active benchmark to stop."
    b.request_stop()
    return True, "stop requested"


# ----------------------------------------------------------- campaign ------
def start_campaign(path):
    """Launch a batch campaign in a background thread (Phase 2)."""
    with STATE["lock"]:
        if STATE["state"] in ("starting", "running"):
            return False, "A single benchmark is already running."
        if STATE["campaign"]["running"]:
            return False, "A campaign is already running."
        spec, err = CAM.load_campaign(path)
        if err:
            return False, "Invalid campaign: %s" % err
        camp = STATE["campaign"]
        camp["running"] = True
        camp["campaign_id"] = ""
        camp["title"] = spec.get("title", "")
        camp["current_site"] = ""
        camp["sites_total"] = len(spec.get("sites") or [])
        camp["sites_done"] = 0
        camp["current_run"] = ""
        camp["last_error"] = ""
        camp["report_path"] = ""
        camp["stop_requested"] = False
        camp["log"].add("Campaign queued: %s (%d sites, profile %s)"
                        % (spec.get("title", path), camp["sites_total"],
                           spec.get("profile")))

    def work():
        try:
            camp["log"].add("Campaign starting: %s" % path)

            def on_event(ev):
                t = ev.get("type")
                if t == "site_start":
                    with STATE["lock"]:
                        STATE["campaign"]["current_site"] = ev.get("site_id")
                elif t == "run_done":
                    with STATE["lock"]:
                        STATE["campaign"]["current_run"] = ev.get("run_id")
                elif t == "site_done":
                    with STATE["lock"]:
                        STATE["campaign"]["sites_done"] += 1
                elif t == "campaign_done":
                    with STATE["lock"]:
                        STATE["campaign"]["campaign_id"] = ev.get("campaign_id")
                        STATE["campaign"]["report_path"] = os.path.join(
                            CAM.paths_benchmarks_dir(ROOT), ev.get("campaign_id"),
                            "report.html")

            result = CAM.run_campaign(
                ROOT, spec, log=camp["log"], on_event=on_event,
                should_stop=lambda: STATE["campaign"]["stop_requested"])
            with STATE["lock"]:
                STATE["campaign"]["campaign_id"] = result["campaign_id"]
                STATE["campaign"]["report_path"] = os.path.join(
                    result["campaign_dir"], "report.html")
                STATE["campaign"]["running"] = False
                C.save_config(CONFIG_PATH, STATE["config"])
        except Exception as ex:
            tb = traceback.format_exc(limit=4)
            with STATE["lock"]:
                STATE["campaign"]["running"] = False
                STATE["campaign"]["last_error"] = "%s: %s" % (
                    type(ex).__name__, ex)
            camp["log"].add("CAMPAIGN FAILED: %s" % STATE["campaign"]["last_error"],
                            "error")
            camp["log"].add(tb.replace("\n", " | "), "error")

    threading.Thread(target=work, name="hlc-campaign", daemon=True).start()
    return True, "campaign queued"


# ------------------------------------------------------------------ helpers --
def read_json_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def presets_payload():
    out = []
    for name, p in C.PRESETS.items():
        phases = p["phases"]()
        out.append({
            "name": name, "label": p["label"],
            "schedule_s": sum(x["duration_s"] for x in phases),
            "phases": [{"name": x["name"], "label": x["label"],
                        "duration_s": x["duration_s"],
                        "concurrency": x.get("concurrency"),
                        "steps": x.get("steps")} for x in phases],
            "max_concurrency": p["max_concurrency"],
        })
    return out


def runtime_environments_payload():
    """Exclusive switch options + the coherent preset each one applies."""
    out = []
    for name in C.RUNTIME_ENVIRONMENTS:
        preset = C.RUNTIME_ENVIRONMENT_DEFAULTS[name]
        out.append({
            "value": name,
            "label": C.RUNTIME_ENVIRONMENT_LABELS[name],
            "default": name == C.DEFAULTS["runtime_environment"],
            "mode": preset["mode"],
            "benchmark_mode": preset["benchmark_mode"],
            "include_windows": preset["include_windows"],
            "include_container": preset["include_container"],
            "client_only": name == "windows",
        })
    return out


def current_status():
    with STATE["lock"]:
        state, rid = STATE["state"], STATE["run_id"]
        err, last = STATE["last_error"], STATE["last_result"]
    live = {}
    if rid:
        live = read_json_file(os.path.join(runs_dir(), rid, "status.json"), {}) or {}
    elif STATE["bench_holder"].get("run_id"):
        tmp = STATE["bench_holder"]["run_id"]
        live = read_json_file(os.path.join(runs_dir(), tmp, "status.json"), {}) or {}
    # The benchmark writes "running" into its own status.json as soon as the
    # first phase starts; reflect that instead of leaving the UI on "starting".
    if state == "starting" and live.get("state") == "running":
        state = "running"
    return {
        "state": state,
        "run_id": rid or STATE["bench_holder"].get("run_id", ""),
        "last_error": err,
        "last_result": last,
        "live": live,
        "server_utc": utc(),
        "campaign": campaign_status(),
    }


def campaign_status():
    with STATE["lock"]:
        c = STATE["campaign"]
        return {
            "running": c["running"],
            "campaign_id": c["campaign_id"],
            "title": c["title"],
            "current_site": c["current_site"],
            "sites_total": c["sites_total"],
            "sites_done": c["sites_done"],
            "current_run": c["current_run"],
            "last_error": c["last_error"],
            "report_path": c["report_path"],
            "stop_requested": c["stop_requested"],
            "log_seq": c["log"].last_seq(),
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "HeavyLoadChecker/1.0"

    def log_message(self, fmt, *args):
        if "--quiet" not in sys.argv:
            sys.stderr.write("[http] %s\n" % (fmt % args))

    # --------------------------------------------------------- low level --
    def _send(self, code, body, ctype="application/json; charset=utf-8",
              extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, obj)

    def _err(self, msg, code=400):
        self._json({"ok": False, "error": str(msg)}, code)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(min(n, 4 * 1024 * 1024))
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as ex:
            raise ValueError("invalid JSON body: %s" % ex)

    def _file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self._err("not found", 404)
        ctype = ctype or (mimetypes.guess_type(path)[0] or
                          "application/octet-stream")
        if (ctype.startswith("text/") or "javascript" in ctype or "json" in ctype) \
                and "charset=" not in ctype:
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    _CAMPAIGN_SAFE = {
        "report.html": "text/html; charset=utf-8",
        "campaign.json": "application/json; charset=utf-8",
        "leaderboard.csv": "text/csv; charset=utf-8",
        "leaderboard.json": "application/json; charset=utf-8",
    }

    def _serve_campaign_artifact(self, rel):
        parts = [x for x in rel.split("/") if x]
        if len(parts) != 2 or parts[0] in ("", ".", "..") \
                or parts[1] not in self._CAMPAIGN_SAFE:
            return self._err("bad campaign artifact path", 400)
        p = os.path.join(BENCHMARKS_DIR, parts[0], parts[1])
        if not os.path.isfile(p):
            return self._err("not found", 404)
        with open(p, "rb") as f:
            self._send(200, f.read(), self._CAMPAIGN_SAFE[parts[1]])

    # ------------------------------------------------------------- routes --
    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(UI_DIR, "index.html"))
            if p.startswith("/ui/"):
                name = os.path.basename(p)
                if name not in ("style.css", "app.js"):
                    return self._err("not found", 404)
                return self._file(os.path.join(UI_DIR, name))
            if p == "/favicon.ico":
                return self._send(204, b"")
            if p == "/api/bootstrap":
                containers, cerr = dp.list_containers()
                return self._json({
                    "ok": True,
                    "config": C.public_config(STATE["config"]),
                    "derived": {k: v for k, v in STATE["config"]["_derived"].items()
                                if k != "weighted_paths"},
                    "presets": presets_payload(),
                    "runtime_environments": runtime_environments_payload(),
                    "runtime_environment_default": C.DEFAULTS["runtime_environment"],
                    "containers": containers,
                    "container_error": cerr,
                    "docker": dp.docker_available(),
                    "status": current_status(),
                    "reports": runner.list_runs(ROOT, cfg=STATE["config"]),
                    "preflight": STATE.get("preflight"),
                })
            if p == "/api/status":
                return self._json({"ok": True, **current_status(),
                                   "log_seq": STATE["log"].last_seq()})
            if p == "/api/log":
                seq = int(u.query.split("seq=")[-1]) if "seq=" in u.query else 0
                return self._json({"ok": True,
                                   "rows": STATE["log"].since(seq, 300),
                                   "log_seq": STATE["log"].last_seq()})
            if p == "/api/config":
                return self._json({"ok": True,
                                   "config": C.public_config(STATE["config"])})
            if p == "/api/containers":
                rows, err = dp.list_containers()
                return self._json({"ok": True, "containers": rows,
                                   "error": err, "docker": dp.docker_available()})
            if p == "/api/reports":
                return self._json({"ok": True,
                                   "reports": runner.list_runs(
                                       ROOT, cfg=STATE["config"])})
            if p.startswith("/report/"):
                rid = os.path.basename(p)
                if not rid.startswith("run-"):
                    return self._err("bad run id", 400)
                return self._file(os.path.join(runs_dir(), rid, "report.html"))
            if p.startswith("/runs/"):
                parts = [x for x in p.split("/") if x]
                if len(parts) != 3 or not parts[1].startswith("run-") \
                        or parts[2] not in SAFE_ARTIFACTS:
                    return self._err("bad artifact path", 400)
                return self._file(os.path.join(runs_dir(), parts[1], parts[2]),
                                  SAFE_ARTIFACTS[parts[2]])
            if p == "/api/health":
                return self._json({"ok": True, "utc": utc(),
                                   "state": STATE["state"]})
            if p == "/api/campaign/list":
                return self._json({"ok": True,
                                   "campaigns": CAM.list_campaigns(ROOT)})
            if p == "/api/campaign/status":
                cs = campaign_status()
                seq = int(u.query.split("seq=")[-1]) if "seq=" in u.query else 0
                return self._json({"ok": True, **cs,
                                   "log": STATE["campaign"]["log"].since(seq, 500),
                                   "log_seq": cs["log_seq"]})
            if p == "/api/compare":
                ids = [x for x in (u.query.split("ids=", 1)[-1]
                                   if "ids=" in u.query else "").split(",") if x]
                if not ids:
                    return self._err("missing ids", 400)
                return self._json({"ok": True,
                                   "comparison": CMP.compare_runs(ROOT, ids)})
            if p == "/api/compare/report":
                ids = [x for x in (u.query.split("ids=", 1)[-1]
                                   if "ids=" in u.query else "").split(",") if x]
                if not ids:
                    return self._err("missing ids", 400)
                cmp = CMP.compare_runs(ROOT, ids)
                html = report_compare.render(cmp)
                return self._send(200, html, "text/html; charset=utf-8")
            if p == "/api/export":
                ids = [x for x in (u.query.split("ids=", 1)[-1]
                                   if "ids=" in u.query else "").split(",") if x]
                fmt = (u.query.split("format=", 1)[-1]
                       if "format=" in u.query else "json")
                if not ids:
                    return self._err("missing ids", 400)
                cmp = CMP.compare_runs(ROOT, ids)
                import io
                if fmt == "csv":
                    import csv as _csv
                    buf = io.StringIO()
                    w = _csv.writer(buf)
                    cols = cmp.get("columns") or CMP.LEADERBOARD_COLUMNS
                    w.writerow([lab for _, lab, _ in cols])
                    for s in cmp["leaderboard"]:
                        w.writerow([CMP._fmt(s.get(k), kind)
                                    for k, _, kind in cols])
                    body = buf.getvalue().encode("utf-8")
                    return self._send(200, body,
                                      "text/csv; charset=utf-8",
                                      {"Content-Disposition":
                                       "attachment; filename=leaderboard.csv"})
                body = json.dumps(cmp, indent=2, default=str).encode("utf-8")
                return self._send(200, body, "application/json; charset=utf-8",
                                  {"Content-Disposition":
                                   "attachment; filename=leaderboard.json"})
            if p.startswith("/campaign/"):
                return self._serve_campaign_artifact(p[10:])
            return self._err("not found: %s" % p, 404)
        except Exception as ex:
            return self._err("%s: %s" % (type(ex).__name__, ex), 500)

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            body = self._body()
        except ValueError as ex:
            return self._err(ex, 400)
        try:
            if p == "/api/config":
                incoming = body.get("config", body)
                old_cfg = STATE["config"]
                old_env = old_cfg.get("runtime_environment", "windows")
                # A typo must never silently reclassify a Docker run as
                # Windows (or vice versa). File-based migration self-heals,
                # but an explicit API value is validated instead.
                if "runtime_environment" in incoming:
                    raw_env = str(incoming.get("runtime_environment")
                                  or "").strip().lower()
                    if raw_env not in C.RUNTIME_ENVIRONMENTS:
                        return self._err(
                            "runtime_environment must be one of %s"
                            % ", ".join(C.RUNTIME_ENVIRONMENTS), 400)
                else:
                    raw_env = old_env
                merged = dict(old_cfg)
                merged.update(incoming)
                merged = {k: v for k, v in merged.items()
                          if not k.startswith("_")}
                # The UI posts a full config clone, so a selector change
                # arrives with four STALE derived keys from the old
                # environment. Drop them and re-apply the coherent preset.
                # When the selector is unchanged, advanced overrides are
                # preserved untouched (no unconditional re-derivation).
                if raw_env != old_env:
                    merged.pop("mode", None)
                    merged.pop("benchmark_mode", None)
                    rc = dict(merged.get("runtime_comparison") or {})
                    rc.pop("include_container", None)
                    rc.pop("include_windows", None)
                    merged["runtime_comparison"] = rc
                    try:
                        merged = C.apply_runtime_environment(merged, raw_env)
                    except Exception as ex:
                        return self._err("configuration error: %s" % ex, 400)
                norm, err = C.validate(merged)
                if err:
                    return self._err("configuration error: %s" % err, 400)
                with STATE["lock"]:
                    STATE["config"] = norm
                    STATE["preflight"] = None
                    C.save_config(CONFIG_PATH, norm)
                STATE["log"].add("Configuration saved (target=%s, profile=%s)"
                                 % (norm["target_url"], norm["profile"]))
                return self._json({"ok": True,
                                   "config": C.public_config(norm),
                                   "derived": {k: v for k, v in
                                               norm["_derived"].items()
                                               if k != "weighted_paths"}})

            if p == "/api/preset":
                name = str(body.get("name") or "")
                before = C.public_config(STATE["config"])
                try:
                    norm = C.apply_profile(before, name)
                except Exception as ex:
                    return self._err(str(ex), 400)
                # A profile switch rewrites ONLY the schedule / concurrency
                # ceiling. Guard the exclusive selector and its derived keys
                # explicitly so a legacy config (or a future normalize()
                # change) can never silently flip Windows -> Docker.
                for k in ("runtime_environment", "mode", "benchmark_mode"):
                    if k in before:
                        norm[k] = before[k]
                rc_before = before.get("runtime_comparison") or {}
                for k in ("include_container", "include_windows"):
                    if k in rc_before:
                        norm["runtime_comparison"][k] = rc_before[k]
                with STATE["lock"]:
                    STATE["config"] = norm
                    STATE["preflight"] = None
                    C.save_config(CONFIG_PATH, norm)
                return self._json({"ok": True,
                                   "config": C.public_config(norm),
                                   "derived": {k: v for k, v in
                                               norm["_derived"].items()
                                               if k != "weighted_paths"}})

            if p == "/api/preflight":
                cfg = C.normalize(STATE["config"])
                pf = runner.preflight(cfg)
                with STATE["lock"]:
                    STATE["preflight"] = pf
                    if pf.get("container") and not cfg.get("container"):
                        STATE["config"]["container"] = pf["container"]
                STATE["log"].add("Preflight: " + "; ".join(
                    "%s=%s" % (x["name"], "OK" if x["ok"] else "WARN")
                    for x in pf["checks"]))
                return self._json({"ok": True, "preflight": pf})

            if p == "/api/routes":
                cfg = C.normalize(STATE["config"])
                rows = runner.discover_routes(cfg, body.get("candidates"))
                return self._json({"ok": True, "routes": rows})

            if p == "/api/run/start":
                ok, msg = start_run()
                return self._json({"ok": ok, "message": msg},
                                  200 if ok else 409)

            if p == "/api/run/stop":
                ok, msg = stop_run()
                # A campaign runs its own loop that ignores the single-run
                # STATE, so also raise its graceful stop flag.
                with STATE["lock"]:
                    if STATE["campaign"]["running"]:
                        STATE["campaign"]["stop_requested"] = True
                        ok, msg = True, "campaign stop requested"
                return self._json({"ok": ok, "message": msg},
                                  200 if ok else 409)

            if p == "/api/campaign/start":
                path = body.get("path") or body.get("campaign")
                if not path:
                    return self._err("missing campaign path", 400)
                if not os.path.isabs(path):
                    path = os.path.join(ROOT, path)
                ok, msg = start_campaign(path)
                return self._json({"ok": ok, "message": msg},
                                  200 if ok else 409)

            return self._err("not found: %s" % p, 404)
        except Exception as ex:
            STATE["log"].add("API error on %s: %s" % (p, ex), "error")
            return self._err("%s: %s" % (type(ex).__name__, ex), 500)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Heavy-Load Checker web interface")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--open", action="store_true", help="open the browser")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(runs_dir(), exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    url = "http://%s:%d/" % (a.host if a.host != "0.0.0.0" else "127.0.0.1",
                             a.port)
    print("Heavy-Load Checker ready at %s" % url)
    print("Target: %s | profile: %s | Ctrl+C to stop."
          % (STATE["config"]["target_url"], STATE["config"]["profile"]))
    STATE["log"].add("Web interface started at %s" % url)
    if a.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nShutting down.")
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
