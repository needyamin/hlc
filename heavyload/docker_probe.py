#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Docker discovery, container inspection and orphan-shell cleanup.

Design note (carried over from the original Spid3r benchmark):
`p.terminate()` kills only the LOCAL `docker exec` client. On Docker Desktop
29.8.2 the in-container `sh -c` sampler loop survives as an orphan and keeps
running a shell pipeline every second inside the target cgroup. Five such
orphans inflated idle CPU from ~0.85% to 8-15%, inflated pids.current and
contaminated every CPU number.

v2 note: `CG_LOOP` below is now a FALLBACK ONLY. The primary sampler is
`heavyload.collectors.cgroup.CgroupCollector`, a single Python process with no
in-container shell forks and a deterministic stdin-based shutdown handshake.
`kill_orphans` is retained solely for that legacy fallback path.
"""
from __future__ import annotations

import json
import re
import subprocess

SAMPLER_MARKER = "HLC_CG_LOOP_7C41B2"

# The marker is assembled from fragments so the killer's OWN cmdline never
# contains the full marker (otherwise it would kill itself).
KILL_ORPHANS = r"""
M=HL; M=${M}C_CG_LOOP; M=${M}_7C; M=${M}41B2
me=$$; pp=$PPID
for p in /proc/[0-9]*; do
  pid=${p##*/}
  [ "$pid" = "$me" ] && continue
  [ "$pid" = "$pp" ] && continue
  cmd=$(tr '\0' ' ' < "$p/cmdline" 2>/dev/null)
  case "$cmd" in
    *"$M"*) kill -9 "$pid" 2>/dev/null ;;
  esac
done
exit 0
"""


def docker_out(args, timeout=20):
    """Return (returncode, stdout, stderr). Never raises for CLI failures."""
    try:
        r = subprocess.run(["docker"] + list(args), capture_output=True,
                           text=True, timeout=timeout, encoding="utf-8",
                           errors="replace")
        return r.returncode, r.stdout or "", r.stderr or ""
    except FileNotFoundError:
        return 127, "", "docker CLI not found on PATH"
    except Exception as ex:
        return 1, "", str(ex)


def docker_available():
    rc, out, _ = docker_out(["version", "--format", "{{.Server.Version}}"], 15)
    return {"available": rc == 0 and bool(out.strip()),
            "server_version": out.strip() or None,
            "engine_version": None}


def list_containers():
    rc, out, err = docker_out(["ps", "--format", "{{json .}}"], 25)
    rows = []
    if rc != 0:
        return rows, err.strip() or "docker ps failed"
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        ports = str(d.get("Ports") or "")
        published = sorted({int(x) for x in re.findall(r":(\d+)->", ports)})
        labels = str(d.get("Labels") or "")
        for p in re.findall(r"desktop\.docker\.io/ports/\d+/tcp=:(\d+)", labels):
            published.append(int(p))
        rows.append({
            "name": d.get("Names") or d.get("ID") or "",
            "id": (d.get("ID") or "")[:12],
            "image": d.get("Image") or "",
            "status": d.get("Status") or "",
            "ports_text": ports,
            "published_ports": sorted(set(published)),
        })
    return rows, None


def _match_by_port(rows, port):
    hits = [r for r in rows if port in r["published_ports"]]
    # Prefer containers that publish a web-looking internal port, then name.
    def score(r):
        s = 0
        if re.search(r"->(80|8000|8080|3000|5000|8008)/tcp", r["ports_text"]):
            s -= 10
        if "mysql" in r["name"].lower() or "postgres" in r["name"].lower() \
                or "redis" in r["name"].lower() or "db" in r["name"].lower():
            s += 20
        return s
    hits.sort(key=lambda r: (score(r), r["name"]))
    return hits[0]["name"] if hits else ""


def resolve_container(cfg, containers=None):
    """Resolve the container to instrument from config + docker ps.

    Returns (name_or_empty, method, note).
    """
    mode = cfg.get("mode", "auto")
    configured = str(cfg.get("container") or "").strip()
    # The exclusive Windows runtime switch is authoritative over container
    # resolution. Without this guard a pinned container plus an advanced
    # --mode override (or a cached Docker preflight) would still collect cgroup
    # data while the result document claims client_only visibility.
    if cfg.get("runtime_environment") == "windows":
        return "", "windows-runtime", (
            "Windows runtime mode: no container instrumentation, "
            "client-only metrics (latency / throughput / errors).")
    if mode == "direct":
        return "", "direct", "Direct mode: no container instrumentation."
    if configured:
        return configured, "configured", "Using configured container %r." % configured
    avail = docker_available()
    if not avail["available"]:
        return "", "unavailable", "Docker daemon not reachable; falling back to direct HTTP mode."
    rows, err = list_containers() if containers is None else (containers, None)
    if err:
        return "", "unavailable", err
    port = cfg["_derived"]["port"]
    name = _match_by_port(rows, port)
    if name:
        return name, "auto-port", ("Auto-detected %r from published host port %d."
                                   % (name, port))
    return "", "unresolved", ("No running container publishes host port %d; "
                              "continuing without cgroup instrumentation. "
                              "Select a container manually if needed." % port)


def inspect_snapshot(container):
    if not container:
        return {}
    rc, out, _ = docker_out(
        ["inspect", container, "--format",
         "{{.RestartCount}}|{{.State.Status}}|{{.State.OOMKilled}}|"
         "{{.State.Running}}|{{.State.StartedAt}}|{{.HostConfig.Memory}}"], 20)
    if rc != 0:
        return {"error": "docker inspect failed"}
    p = (out or "").strip().split("|")
    return {
        "restart_count": p[0] if len(p) > 0 else "?",
        "state": p[1] if len(p) > 1 else "?",
        "oom_killed": p[2] if len(p) > 2 else "?",
        "running": p[3] if len(p) > 3 else "?",
        "started_at": p[4] if len(p) > 4 else "?",
        "memory_limit_bytes": p[5] if len(p) > 5 else "?",
    }


def cgroup_caps(container):
    """Check which cgroup interface the container exposes."""
    if not container:
        return {"available": False, "version": None,
                "reason": "no container selected"}
    script = ("if [ -f /sys/fs/cgroup/memory.stat ]; then echo v2; "
              "grep -E '^(anon|file|kernel) ' /sys/fs/cgroup/memory.stat | head -3; "
              "elif [ -f /sys/fs/cgroup/memory/memory.stat ]; then echo v1; "
              "else echo none; fi")
    rc, out, err = docker_out(["exec", container, "sh", "-c", script], 20)
    if rc != 0:
        return {"available": False, "version": None,
                "reason": (err or "docker exec failed").strip()[:200]}
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    ver = lines[0] if lines else "none"
    if ver not in ("v2", "v1"):
        return {"available": False, "version": None,
                "reason": "container has no readable memory cgroup"}
    return {"available": True, "version": ver,
            "preview": lines[1:4],
            "canonical_metric": ("memory.stat anon (raw bytes -> MiB)" if ver == "v2"
                                 else "memory.stat rss (raw bytes -> MiB)")}


def kill_orphans(container):
    if not container:
        return False
    rc, _, _ = docker_out(["exec", container, "sh", "-c", KILL_ORPHANS], 20)
    return rc == 0


# ------------------------------------------------ persistent sampler script --
# ONE exec process for the entire run. Per-sample `docker exec` inflates
# pids.current and adds transient anonymous pages (measurement artifact).
CG_LOOP = r"""
# HLC_CG_LOOP_7C41B2
if [ -f /sys/fs/cgroup/memory.stat ]; then CG=/sys/fs/cgroup; V=2;
elif [ -f /sys/fs/cgroup/memory/memory.stat ]; then CG=/sys/fs/cgroup/memory; V=1;
else V=0; fi
while :; do
echo '@@S'
echo CGVER v$V
if [ "$V" = 2 ]; then
  echo CUR $(cat $CG/memory.current 2>/dev/null)
  echo PEK $(cat $CG/memory.peak 2>/dev/null)
  grep -E '^(anon|file|kernel|slab|sock|shmem|pgfault|pgmajfault|pgscan|pgsteal|workingset_refault) ' $CG/memory.stat 2>/dev/null
  grep -E '^(oom_kill|oom|max) ' $CG/memory.events 2>/dev/null | sed 's/^/EV_/'
  grep -E '^(usage_usec|nr_throttled|nr_periods) ' $CG/cpu.stat 2>/dev/null
  echo PIDS $(cat $CG/pids.current 2>/dev/null)
  grep -E '^max ' $CG/pids.events 2>/dev/null | sed 's/^/PIDS_EV_/'
  for f in cpu memory io; do grep -E '^some avg10=' $CG/$f.pressure 2>/dev/null | sed "s/^/PSI_$f /"; done
  awk '{for(i=1;i<=NF;i++){split($i,a,"="); if(a[1]=="rbytes")r+=a[2]; if(a[1]=="wbytes")w+=a[2]}} END{print "IO",r+0,w+0}' $CG/io.stat 2>/dev/null
elif [ "$V" = 1 ]; then
  echo CUR $(cat $CG/memory.usage_in_bytes 2>/dev/null)
  echo PEK $(cat $CG/memory.max_usage_in_bytes 2>/dev/null)
  grep -E '^(rss|cache|mapped_file|pgfault|pgmajfault|pgscan|pgsteal) ' $CG/memory.stat 2>/dev/null
  echo PIDS $(cat /sys/fs/cgroup/pids/pids.current 2>/dev/null)
fi
grep -E '^(VmPeak|VmSize|VmRSS|VmData|Threads):' /proc/1/status 2>/dev/null | sed 's/^/PROC_/'
echo START $(awk '{print $22}' /proc/1/stat 2>/dev/null)
awk '/^TcpExt:/{for(i=1;i<=NF;i++){if($i=="ListenOverflows")o=i; if($i=="ListenDrops")d=i} getline; print "LISTEN",$o,$d; exit}' /proc/net/netstat 2>/dev/null
sleep 1
done
"""

MYSQL_VARS = ("Questions,Threads_connected,Threads_running,Aborted_connects,"
              "Slow_queries,Bytes_sent,Bytes_received,Connections,"
              "Max_used_connections,Created_tmp_disk_tables,Table_locks_waited,"
              "Innodb_row_lock_waits,Innodb_row_lock_time")


def mysql_loop_script(period=10):
    # HLC_CG_LOOP_7C41B2
    q = ("SHOW GLOBAL STATUS WHERE Variable_name IN (" +
         ",".join("'%s'" % v for v in MYSQL_VARS.split(",")) + ")")
    return ("# HLC_CG_LOOP_7C41B2\nwhile :; do echo '@@M'; "
            "mysql -uroot -p\"$MYSQL_ROOT_PASSWORD\" -N -B -e \"" + q +
            "\" 2>/dev/null; sleep %d; done" % period)
