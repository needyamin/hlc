"""Low-perturbation cgroup collector.

Why this exists
---------------
The legacy sampler (``docker_probe.CG_LOOP``) forks ``grep``/``awk``/``sed``/
``cat``/``sleep`` every second *inside the target container's cgroup*. Orphaned
shell loops previously inflated idle CPU from ~0.85% to 8-15% and contaminated
every CPU number.

This collector runs ONE long-lived Python process that reads cgroup
pseudo-files with plain ``open()`` - zero subprocesses, zero shell pipelines.
Its stdin is held open by the parent, so shutting down is deterministic: writing
``quit`` (or simply closing the pipe on an abrupt parent death) makes the
collector exit. No marker-based ``pkill`` scavenging is needed.

It emits the exact same ``@@S`` text protocol as the legacy loop, so
``samplers.parse_cg_line`` keeps working unchanged. One extra line per sample,
``PROCJSON {...}``, carries per-process attribution and is ignored by the old
parser.

Fallback order: python v2 collector -> legacy shell loop -> no cgroup sampler
(docker stats alone still provides coarse CPU/RAM).
"""

from __future__ import annotations

import subprocess

from .. import docker_probe as dp
from .base import Collector

COLLECTOR_MARKER = "HLC_CG_COLLECTOR_V2_9F31"

# Passed as `python3 -c <this source> <period>`. Keeping the code in argv (not
# stdin) leaves stdin free for the deterministic shutdown handshake.
CG_COLLECTOR_PY = r'''
import os, sys, json, time
try:
    import select
except ImportError:
    select = None

PERIOD = 1.0
try:
    PERIOD = max(0.05, float(sys.argv[1]))
except Exception:
    pass

CG, V = None, 0
if os.path.isfile("/sys/fs/cgroup/memory.stat"):
    CG, V = "/sys/fs/cgroup", 2
elif os.path.isfile("/sys/fs/cgroup/memory/memory.stat"):
    CG, V = "/sys/fs/cgroup/memory", 1

MEMKEYS = ("anon", "file", "kernel", "slab", "sock", "shmem", "rss", "cache",
           "mapped_file", "pgfault", "pgmajfault", "pgscan", "pgsteal",
           "workingset_refault")
CPUKEYS = ("usage_usec", "nr_throttled", "nr_periods")
PROCKEYS = ("VmPeak", "VmSize", "VmRSS", "VmData", "Threads")
MEMSET, CPUSET, PROCSET = set(MEMKEYS), set(CPUKEYS), set(PROCKEYS)


def rd(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception:
        return ""


def num(path):
    s = rd(path).strip()
    return s.split("\n")[0].strip() if s else None


def kv(path, wanted):
    out = []
    for line in rd(path).splitlines():
        p = line.split()
        if len(p) >= 2 and p[0] in wanted:
            try:
                out.append(p[0] + " " + str(int(p[1])))
            except Exception:
                pass
    return out


def emit(s):
    sys.stdout.write(s + "\n")


def proc_sample():
    """Per-process RSS / CPU ticks / threads, for attribution beyond cgroup."""
    rows = []
    try:
        pids = [n for n in os.listdir("/proc") if n.isdigit()]
    except Exception:
        return rows
    for pid in pids[:256]:
        try:
            with open("/proc/" + pid + "/stat", "r") as f:
                stat = f.read()
            cut = stat.rfind(")")
            if cut < 0:
                continue
            comm = stat[stat.find("(") + 1:cut]
            f = stat[cut + 2:].split()
            # After comm: state(0) ppid(1) ... utime(11) stime(12)
            # ... threads(17) ... rss_pages(21) ... starttime(22)
            utime, stime = int(f[11]), int(f[12])
            threads = int(f[17])
            rss_pages = int(f[21])
            starttime = int(f[19]) if len(f) > 19 else 0
            vmrss = 0
            for line in rd("/proc/" + pid + "/status").splitlines():
                if line.startswith("VmRSS:"):
                    try:
                        vmrss = int(line.split()[1]) * 1024
                    except Exception:
                        pass
                    break
            rows.append({"pid": int(pid), "comm": comm,
                         "rss_bytes": vmrss or rss_pages * 4096,
                         "utime": utime, "stime": stime,
                         "threads": threads, "starttime": starttime})
        except Exception:
            continue
    return rows


def one_sample():
    emit("@@S")
    emit("CGVER v" + str(V))
    if V == 2 and CG:
        n = num(CG + "/memory.current")
        if n:
            emit("CUR " + n)
        n = num(CG + "/memory.peak")
        if n:
            emit("PEK " + n)
        for line in kv(CG + "/memory.stat", MEMSET):
            emit(line)
        for line in kv(CG + "/memory.events", set(("low", "high", "max", "oom", "oom_kill"))):
            emit("EV_" + line)
        for line in kv(CG + "/cpu.stat", CPUSET):
            emit(line)
        n = num(CG + "/pids.current")
        if n:
            emit("PIDS " + n)
        n = num(CG + "/pids.events")
        if not n:
            for line in rd(CG + "/pids.events").splitlines():
                if line.startswith("max "):
                    emit("PIDS_EV_" + line.strip())
                    break
        else:
            emit("PIDS_EV_" + n)
        for kind in ("cpu", "memory", "io"):
            for line in rd(CG + "/" + kind + ".pressure").splitlines():
                if line.startswith("some "):
                    emit("PSI_" + kind + " " + line.strip())
                    break
        r = w = 0
        for line in rd(CG + "/io.stat").splitlines():
            for tok in line.split()[1:]:
                try:
                    if tok.startswith("rbytes="):
                        r += int(tok.split("=", 1)[1])
                    elif tok.startswith("wbytes="):
                        w += int(tok.split("=", 1)[1])
                except Exception:
                    pass
        emit("IO " + str(r) + " " + str(w))
    elif V == 1 and CG:
        n = num(CG + "/memory.usage_in_bytes")
        if n:
            emit("CUR " + n)
        n = num(CG + "/memory.max_usage_in_bytes")
        if n:
            emit("PEK " + n)
        for line in kv(CG + "/memory.stat", MEMSET):
            emit(line)
        n = num("/sys/fs/cgroup/pids/pids.current")
        if n:
            emit("PIDS " + n)
    for line in rd("/proc/1/status").splitlines():
        k = line.split(":")[0]
        if k in PROCSET:
            emit("PROC_" + line.strip().split()[0] + " " + line.split()[1])
    try:
        with open("/proc/1/stat", "r") as f:
            emit("START " + f.read().split(") ", 1)[1].split()[19])
    except Exception:
        pass
    try:
        lines = rd("/proc/net/netstat").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("TcpExt:") and i + 1 < len(lines):
                names, vals = line.split()[1:], lines[i + 1].split()[1:]
                d = dict(zip(names, vals))
                emit("LISTEN " + str(int(d.get("ListenOverflows", 0)))
                     + " " + str(int(d.get("ListenDrops", 0))))
                break
    except Exception:
        pass
    emit("PROCJSON " + json.dumps(proc_sample(), separators=(",", ":")))
    sys.stdout.flush()


def main():
    while True:
        if select is not None:
            try:
                ready, _, _ = select.select([sys.stdin], [], [], PERIOD)
                if ready:
                    line = sys.stdin.readline()
                    if not line or line.strip() == "quit":
                        break
                    continue
            except Exception:
                time.sleep(PERIOD)
        else:
            time.sleep(PERIOD)
        try:
            one_sample()
        except Exception as ex:
            emit("COLLECTOR_ERROR " + type(ex).__name__)
            sys.stdout.flush()
            break


main()
'''


class CgroupCollector(Collector):
    """One in-container process (or a legacy shell fallback) producing samples."""

    name = "cgroup"

    def __init__(self, container, period=1.0, log=None, allow_legacy=True):
        self.container = container or ""
        self.period = max(0.05, min(10.0, float(period or 1.0)))
        self.log = log
        self.allow_legacy = allow_legacy
        self.proc = None
        self.mode = "none"
        self.reason = ""

    def _say(self, msg):
        if self.log:
            self.log.add(msg)

    def available(self):
        return bool(self.container)

    @staticmethod
    def has_python3(container):
        rc, out, _ = dp.docker_out(
            ["exec", container, "python3", "-c", "print(1)"], timeout=12)
        return rc == 0 and "1" in (out or "")

    def start(self):
        if not self.container:
            self.reason = "no container resolved (client-only mode)"
            return False
        if self.has_python3(self.container):
            try:
                self.proc = subprocess.Popen(
                    ["docker", "exec", "-i", self.container,
                     "python3", "-c", CG_COLLECTOR_PY, str(self.period)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL, text=True, bufsize=1,
                    encoding="utf-8", errors="replace")
                self.mode = "python_v2"
                self._say("cgroup collector: single python process at %.2fs "
                          "(no in-container shell forks)" % self.period)
                return True
            except Exception as ex:
                self.reason = "python collector failed to start: %s" % ex
                self.proc = None
        else:
            self.reason = "python3 not present in container"

        if self.allow_legacy:
            try:
                self.proc = subprocess.Popen(
                    ["docker", "exec", self.container, "sh", "-c", dp.CG_LOOP],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, bufsize=1, encoding="utf-8", errors="replace")
                self.mode = "legacy_shell"
                self._say("cgroup collector fallback: legacy shell loop "
                          "(%s) - higher perturbation" % self.reason)
                return True
            except Exception as ex:
                self.reason += "; legacy fallback failed: %s" % ex
        self._say("cgroup collector unavailable: %s" % self.reason)
        return False

    def lines(self):
        """Yield stdout lines until the process exits or is stopped."""
        if self.proc is None or self.proc.stdout is None:
            return
        for line in self.proc.stdout:
            yield line.rstrip("\r\n")

    def stop(self):
        p, self.proc = self.proc, None
        if p is None:
            return
        if self.mode == "python_v2" and p.stdin is not None:
            # Deterministic shutdown: the collector exits on 'quit' or EOF.
            try:
                p.stdin.write("quit\n")
                p.stdin.flush()
            except Exception:
                pass
            try:
                p.stdin.close()
            except Exception:
                pass
        else:
            # Legacy shell loop needs the marker-based orphan cleanup, because
            # terminating the local `docker exec` client does not kill it.
            dp.kill_orphans(self.container)
        try:
            p.wait(timeout=3)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass
        if self.mode == "legacy_shell":
            dp.kill_orphans(self.container)
