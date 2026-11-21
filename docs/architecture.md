# Architecture

## Data flow

```
start.bat / cli.py / server.py (127.0.0.1:8787)
        |
        v
runner.run_benchmark()   -- preflight(), run dir, orchestration
        |
        +--> engine.Benchmark    (phased HTTP load generator, raw.jsonl)
        |        |
        |        +--> http_client.make_connection / latency.Stopwatch
        |        +--> samplers.SamplerSet
        |                 +--> collectors.health      (1 Hz availability probe)
        |                 +--> generator self-monitor (CPU + working set)
        |
        +--> adapters.*          (optional /__hlc/runtime -> runtime_series)
        +--> browser.cdp         (optional headless Chrome -> browser_series)
        |
        +--> quality.check_run()      (data-integrity & anomaly checks)
        +--> scalability.analyze()    (concurrency curve, knee, classification)
        +--> runtime_comparison.run() (14-language checksum-gated benchmark,
        |                              procmetrics-measured RSS/CPU per variant)
        +--> suitability.build()      (measured + documented weighted ranking)
        |
        v
grading.grade() -> schema.ensure_current() -> report.build()
        |
        v
runs/<run-id>/ {config,preflight,status,raw.jsonl,results.json,
                stage2_verdict.json,report.html,
                runtime_comparison.{json,html},suitability.{json,html}}
```

`server.py` reads `status.json` for live KPIs and serves the artifacts; the UI
polls `/api/status` and `/api/log` at 1 Hz. No WebSockets, no third-party JS.

## Module responsibilities

| Path | Role |
|---|---|
| `heavyload/paths.py` | Single source of truth for ROOT / runs / targets / archive. `runs_dir()` honours `config.output_dir`. `SAFE_ARTIFACTS` whitelist. |
| `heavyload/config.py` | Defaults, 5 profiles (smoke/quick/heavy/stress/standard), 8-phase schedule, normalise/validate/save. Legacy Docker-era configs map to the single host environment with a recorded note. |
| `heavyload/engine.py` | `Benchmark`: worker pool, per-step concurrency windows (`step_windows` -> `step_metrics`), guardrails, per-request `raw.jsonl`, aggregation, result assembly. |
| `heavyload/http_client.py` | One connection factory + one-shot `probe()` (preflight + health). |
| `heavyload/latency.py` | `perf_counter_ns` timing, `Stopwatch` (connect / TTFB / body / dispatch delay). |
| `heavyload/samplers.py` | Background threads only: health probe + generator self-monitor (`generator_series`: CPU% from process_time deltas, working set via ctypes psapi / procfs). Always `client_only=True`. |
| `heavyload/procmetrics.py` | OS-measured child metrics: Windows GetProcessMemoryInfo / GetProcessTimes via ctypes; POSIX wait4 rusage. `None` = unavailable, never estimated. |
| `heavyload/comparison_sources/` | One module per language (14): pinned workload sources, toolchain keys, optional build steps, run variants, per-language honesty notes (e.g. why workload B is absent). Registry in `__init__.py`. |
| `heavyload/runtime_comparison.py` | Toolchain detection (17 probes incl. clang/dotnet-SDK checks), traversal-safe source writing, builds (javac/kotlinc/dotnet/go/swiftc/tsc/gcc/g++/rustc/spid3r), variant timing via procmetrics, checksum integrity gate, stats engine (median/CI/CV/IQR/bootstrap), slowdown ratios + pairwise matrix, standalone HTML, `--verify-sources` support. |
| `heavyload/quality.py` | Data-integrity & anomaly checks over the run's own evidence; statuses ok / warn / fail / insufficient_evidence. |
| `heavyload/scalability.py` | Merges step windows into the concurrency curve; knee detection; conservative classification (linear / sublinear / plateau / degraded). |
| `heavyload/suitability.py` | Weighted ranking from measured + explicitly-labelled documented criteria; log-linear normalization; weight redistribution with per-row measured-share disclosure; standalone HTML. |
| `heavyload/grading.py` | SLOs S1–S9, findings, health score, verdict. Still grades archived docker_lab evidence so historical results re-grade identically. |
| `heavyload/report.py` | Single self-contained HTML (inline SVG, no JS / CDN): 16 sections incl. methodology, scalability, quality & accuracy, strengths/weaknesses, suitability. |
| `heavyload/schema/` | v2 metric contract, visibility map (incl. `client_only_same_host`), `can_compare()` comparability gate, additive v1 migration. |
| `heavyload/adapters/` | `RuntimeAdapter` contract, Python reference adapter, 5 endpoint adapters. |
| `heavyload/browser/` | Stdlib RFC-6455 WebSocket CDP client for optional page metrics. |
| `heavyload/targetspec.py` | Per-site spec loader / validator, `apply_to_config`, native start/build argv parsing (no shell). |
| `scripts/native_target.py` | Native target lifecycle: start (os.spawnve) / stop (ctypes TerminateProcess) / status; PID records under runs/targets/. |
| `scripts/archive_legacy.py` | Verified backup + Recycle Bin cleanup (reversible). |

## Key invariants

1. **Stdlib-only core.** Optional extras are never required; the CDP client
   uses plain `socket`.
2. **Nothing ever fabricates evidence.** Missing toolchains degrade to
   `unavailable` / `not applicable`; checksum-mismatched comparison variants
   are REJECTED, not published; quality checks can answer
   `insufficient_evidence`.
3. **Additive schema.** No v1 key is deleted, renamed or coerced, so archived
   reports still render.
4. **Visibility and comparability are explicit.** `can_compare()` refuses
   cross-vantage or cross-host rankings with a stated reason; documented
   (opinion) criteria in the suitability ranking are always labelled.
5. **Instrumentation must not perturb the subject.** The sampler stack is two
   daemon threads; the generator's own CPU/memory is measured so client-side
   saturation can never hide.

## Extension seams

`RuntimeAdapter`, `config.targets[]`, `environment.fingerprint`,
`series.repeats[]`, the `comparison_sources` registry (add a module = add a
language), and the suitability criteria/weights are the places to hook new
collectors, languages or scoring without touching the engine. See
[phase2-roadmap.md](phase2-roadmap.md).
