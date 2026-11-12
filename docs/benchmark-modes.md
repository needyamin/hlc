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

## The exclusive runtime environment switch (Windows is the default)

`config.runtime_environment` is a single, exclusive choice — `windows` or
`docker`. It is the **source of truth** for both the language runtime
load check and the HTTP load test's vantage point. There is deliberately no
second instrumentation selector that could contradict it.

| | `windows` (default) | `docker` |
|---|---|---|
| `mode` | `direct` | `auto` |
| `benchmark_mode` | `live_blackbox` | `auto` → `docker_lab` when a container resolves |
| `visibility` | `client_only` | `full_server` |
| Runtime comparison | installed host toolchains only (`include_windows: true`, `include_container: false`) | host baselines **plus** `spid3r_docker_*` variants (`true` / `true`) |
| Docker daemon | not required | required |

Because the user asked for installed Windows runtimes to be load-checked by
default, **the default run is client-only**. That is an honest but real
capability reduction, so it is disclosed loudly rather than hidden:

- a persistent "Runtime: Windows (client-only)" badge and a disclosure card in
  the web UI, with a mode-aware "What gets measured" list;
- a preflight row explicitly stating cgroup instrumentation is **not**
  collected in Windows runtime mode;
- report **section 10** warning: no server CPU / cgroup memory / PSI / OOM
  metrics, memory-recovery SLOs graded on insufficient evidence, never rank
  this run against a Docker lab run;
- `visibility = "client_only"` pinned by `schema/migrate.py` for any
  `live_blackbox` result, **regardless** of stray cgroup evidence;
- S5 / S6 memory grades forced to `N/A` by `grading.py`;
- a hard collector gate in `samplers.py`: in Windows mode no cgroup stream,
  no `docker stats` poller and no MySQL sampler can start, even if a
  container name is still pinned in `config.json`;
- `runner.run_benchmark()` forces `container=""`, `mysql_container=""` and
  `mode="live_blackbox"`; cached preflight reuse is keyed on
  `runtime_environment`, so a Docker preflight can never leak into a Windows
  run.

Docker is **additive**: host PHP / Python baselines stay in both modes because
`slowdown` ratios are computed against them. Docker rows carry
`source: "docker"` and are tagged in the HTML report with a different-environment
caveat.

### Changing the switch

```bash
python cli.py --runtime-env docker --profile smoke
python cli.py --runtime-env windows --profile smoke
```

or use the **Runtime environment** radio card in the web UI. Both rewrite all
four derived keys (`mode`, `benchmark_mode`, `include_windows`,
`include_container`) together via `config.apply_runtime_environment()`.
Changing only `runtime_environment` in a saved `config.json` leaves a
half-configured state. Configs predating the switch are inferred **once** from
legacy Docker signals so existing Docker setups keep working.

Explicit Windows overrides that request server instrumentation are rejected:
`--mode auto|docker` and `--benchmark-mode docker_lab|instrumented_live` exit
with `use --runtime-env docker`.

## The three modes

### `docker_lab` — primary, comparable

Selected by `runtime_environment: docker` (with `benchmark_mode: auto` and a
resolving container). You own the app and run it in a container with pinned CPU/RAM
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

### `live_blackbox` — client-only, now the DEFAULT

Selected by `runtime_environment: windows`. For a public website, or when no
container can be resolved — and now the out-of-the-box behaviour. Results get
`visibility: client_only`; `resources.*` and `series.cgroup_series` are marked
`unavailable`. `schema.metrics.can_compare()` refuses to compare such a run
with a `docker_lab` run. Useful for real-world latency, useless for "how much
load did the server take". Memory SLOs S5 / S6 are graded `N/A — insufficient
evidence`, never silently passed or failed.

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
