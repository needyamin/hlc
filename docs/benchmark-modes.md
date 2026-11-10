# Benchmark modes — why Docker is required for server load

## The short answer

**Yes, your reasoning was right.** If you only send HTTP requests to a website,
you can observe:

- response time (DNS / connect / TLS / TTFB / full body),
- HTTP status codes and error classes,
- throughput the client can drive, bytes transferred,
- (with a browser) FCP / LCP / DOM / JS heap / long tasks.

You **cannot** observe the server's CPU%, RAM, heap, GC pauses, goroutines,
thread count, container OOM kills or restarts. Those live behind the network
boundary. No amount of client-side measurement reconstructs them.

## The three modes

### `docker_lab` — primary, comparable

You own the app and run it in a container with pinned CPU/RAM
(`--cpus`, `--memory`, `--cpuset-cpus`). The tool then reads:

- cgroup v2 `memory.stat anon` (canonical, raw bytes), `memory.current`,
  `memory.peak`, `memory.events`, `cpu.stat usage_usec`, `io.stat`, `*.pressure`,
  `pids.current`,
- `/proc/<pid>` per-process RSS, CPU ticks, threads,
- `docker stats` (secondary, human-readable decimal MB) and `docker inspect`
  (restart / OOMKilled / memory limit),
- optional runtime internals from an app `/__hlc/runtime` JSON endpoint.

This is the **only** setup that produces fair cross-language numbers, because
workload, hardware limits and measurement are all under your control.

### `live_blackbox` — client-only

For a public website, or when no container can be resolved. Results get
`visibility: client_only`; `resources.*` and `series.cgroup_series` are marked
`unavailable`. `schema.metrics.can_compare()` refuses to compare such a run
with a `docker_lab` run. Useful for real-world latency, useless for "how much
load did the server take".

### `instrumented_live` — no container, but you own the server

If the server exposes node_exporter / cAdvisor / Prometheus `/metrics` or an
APM agent, server metrics can be collected without Docker. The
`collectors.base.Collector` interface is the seam for this. **Not built in this
round.**

## Windows / Docker Desktop / WSL2 caveat

cgroup pseudo-files live inside the WSL2 **Linux VM**, not Win32. The host can
only reach them via `docker exec` (or a sidecar container in Phase 2). The
target container, the VM and the Windows-hosted load generator all share the
same physical cores, so:

1. Pin the target with `--cpuset-cpus` (spec `resource_limits.cpuset_cpus`).
2. Pin the generator to different cores (`config.fairness.cpuset_generator`);
   `util.pin_current_process()` uses `SetProcessAffinityMask` on Windows.
3. Capture an idle baseline (`01-plateau`) and subtract it — reported as
   `cpu_*_net` and `anon_peak_mib_net` fields.
4. Record `environment.fingerprint.host_shared_cpu = true` in every result.

Never quote raw lab CPU% as absolute server capacity without these annotations.

## Instrumentation must not perturb the subject

The legacy sampler forked `grep`/`awk`/`sed`/`cat`/`sleep` every second **inside
the target cgroup**; orphan loops once inflated idle CPU from ~0.85% to 8-15%.
The v2 collector (`heavyload/collectors/cgroup.py`) is one Python process that
reads cgroup files with plain `open()` — no subprocesses — and exits on a stdin
`quit` handshake, so there are no orphans. The old shell loop is retained only
as a fallback when the container has no `python3`.

See `tests/test_no_perturbation.py` for the idle-CPU guard.
