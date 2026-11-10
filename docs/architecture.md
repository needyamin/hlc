# Architecture

## Data flow

```
start.bat / cli.py / server.py (127.0.0.1:8787)
        |
        v
runner.run_benchmark()   -- preflight(), container resolution, run dir
        |
        +--> engine.Benchmark    (phased HTTP load generator, raw.jsonl)
        |        |
        |        +--> http_client.make_connection / latency.Stopwatch
        |        +--> samplers.SamplerSet
        |                 |
        |                 +--> collectors.CgroupCollector  (1 python proc, 5-10 Hz)
        |                 +--> collectors.procfs           (PROCJSON -> proc_series)
        |                 +--> collectors.docker_stats     (docker stats JSON)
        |                 +--> collectors.health / mysql
        |
        +--> adapters.*          (optional /__hlc/runtime -> runtime_series)
        +--> browser.cdp         (optional headless Chrome -> browser_series)
        |
        v
grading.grade() -> schema.ensure_current() -> report.build()
        |
        v
runs/<run-id>/ {config,preflight,status,raw.jsonl,results.json,
                stage2_verdict.json,report.html}
```

`server.py` reads `status.json` for live KPIs and serves the artifacts; the UI
polls `/api/status` and `/api/log` at 1 Hz. No WebSockets, no third-party JS.

## Module responsibilities

| Path | Role |
|---|---|
| `heavyload/paths.py` | Single source of truth for ROOT / runs / targets / archive. `runs_dir()` honours `config.output_dir`. |
| `heavyload/config.py` | Defaults, 3 profiles (smoke/quick/standard), 8-phase schedule, normalise/validate/save. v2: `target`, `targets`, `fairness`, `browser`, `runtime_adapter`, `generator`. |
| `heavyload/engine.py` | `Benchmark`: worker pool, guardrails, per-request `raw.jsonl`, aggregation, result assembly. Intentionally NOT split into a package this round. |
| `heavyload/http_client.py` | One connection factory + one-shot `probe()` (preflight + health). |
| `heavyload/latency.py` | `perf_counter_ns` timing, `Stopwatch` (connect / TTFB / body / dispatch delay). |
| `heavyload/samplers.py` | Orchestrates sampler threads; owns shared sample lists; `snapshot()` / `finalize()`. |
| `heavyload/collectors/` | `Collector` contract + cgroup / procfs / docker-stats / health / mysql implementations. |
| `heavyload/docker_probe.py` | Docker CLI wrappers, container resolution, `docker inspect`, orphan cleanup for the legacy fallback only. |
| `heavyload/grading.py` | SLOs S1–S9, findings, health score, free-text verdict. |
| `heavyload/report.py` | Single self-contained HTML + inline SVG (no JS / CDN). |
| `heavyload/schema/` | v2 metric contract, visibility map, comparability guard, additive v1 migration. |
| `heavyload/adapters/` | `RuntimeAdapter` contract, Python reference adapter, 5 endpoint adapters. |
| `heavyload/browser/` | Stdlib RFC-6455 WebSocket CDP client for optional page metrics. |
| `heavyload/targetspec.py` | Per-site spec loader / validator, `apply_to_config`, `docker_run_args`. |
| `scripts/archive_legacy.py` | Verified backup + Recycle Bin cleanup (reversible). |
| `scripts/lab_up.py` / `lab_down.py` | Dry-run-first Docker target lifecycle helpers. |

## Key invariants

1. **Stdlib-only core.** Optional extras (Playwright, psutil) are never
   required; the CDP client uses plain `socket`.
2. **No collector ever fails a run.** Missing Docker, missing `python3` in the
   container, missing runtime endpoint and missing Chrome all degrade to
   `unavailable` labels.
3. **Additive schema.** No v1 key is deleted, renamed or coerced, so archived
   reports still render.
4. **Visibility is explicit.** A client-only run can never be silently compared
   with a Docker lab run.
5. **Instrumentation must not perturb the subject.** One in-container Python
   process, no shell forks, deterministic stdin shutdown.

## Extension seams for Phase 2

`Collector`, `RuntimeAdapter`, `config.targets[]`, `benchmark_mode`,
`visibility`, `environment.fingerprint`, and `series.repeats[]` are deliberately
the only places a campaign runner / comparison report needs to hook into.
See [phase2-roadmap.md](phase2-roadmap.md).
