# Phase 2 roadmap — multi-language benchmark campaigns

**Status (this round):** Sections **1 (Campaign file + batch runner)** and
**2 (Comparison / leaderboard)** are **BUILT and tested**. Sections 3–5
(deeper runtime collectors, max-rigor isolation, workload realism) remain
deferred — they are additive on top of the now-working comparison core.

## What is built

- `benchmarks/lang-runtime.json` — example campaign referencing
  `targets/examples/*.json` specs under one shared `smoke` profile.
- `heavyload/campaign.py` — batch runner: per site, warm-up (discarded) +
  `repeat_count` measured passes, per-site median/mean/stddev aggregation,
  `can_compare` guard, graceful stop, writes `campaign.json` + `report.html` +
  `leaderboard.csv` / `leaderboard.json`.
- `heavyload/compare.py` — pure comparison core: load existing runs, normalise
  to a leaderboard view, enforce `schema.can_compare`, CSV/JSON export.
- `heavyload/report_compare.py` — multi-run HTML report with inline SVG bars
  and a comparability badge (honest warning, never a misleading ranking).
- CLI: `python cli.py --campaign <file>` and `python cli.py --compare id1 id2 …`
  (`--output <dir>`), plus `--list-campaigns`.
- Server: `/api/campaign/list`, `/api/campaign/start`, `/api/campaign/status`,
  `/api/compare`, `/api/compare/report`, `/api/export`.
- UI: Campaigns section (start/stop + live progress + history) and a
  multi-select Compare/Export bar on the Past runs table.

## Current state (foundation)

- v2 result schema with `benchmark_mode`, `visibility`, `target`, `metrics`,
  `per_metric_visibility`, `environment.fingerprint`, `verdict_code`.
- `heavyload/targetspec.py` + `targets/_schema.json` + example specs.
- `Collection` and `RuntimeAdapter` interfaces, one working reference adapter,
  thin endpoint adapters for Node/JVM/Go/PHP-FPM/.NET.
- Optional browser metrics via stdlib CDP.
- `config.fairness` (warm-up, repeats, seed, median aggregation, CPU pinning),
  idle-baseline subtraction, TTFB/coordinated-omission timing.
- `runner.list_runs(..., include_archive=True)` already returns `language`,
  `site_id`, `benchmark_mode`, `visibility` per run.

## To build

### 1. Campaign file + batch runner — ✅ BUILT
`benchmarks/<campaign>.json` references N `targets/*.json` specs plus one shared
workload/profile. `heavyload/campaign.py`:

- For each site -> each repeat: warm-up (discarded) -> idle baseline -> seeded
  identical workload -> cooldown.
- Writes `series.repeats[]` (via the standard per-run results), aggregates with
  median + stddev/variance across measured repeats.
- **Hard rule** (`schema.metrics.can_compare`): refuses to compare runs whose
  `benchmark_mode`, `visibility`, `schema_version` or `workload_class` differ,
  or whose runs are client-only. Warns instead of producing a misleading number.
- Server allows one campaign (or one single run) at a time (409 busy) with
  per-campaign progress in `/api/campaign/status` and a graceful Stop.

### 2. Comparison / leaderboard — ✅ BUILT
- `heavyload/report_compare.py`: multi-run HTML + inline SVG bars
  (reusing `report.py`'s chart helpers).
- `/api/compare` + `/api/compare/report` endpoints, and a comparison view in
  `ui/app.js` (multi-select checkboxes in the Past runs table).
- CSV/JSON export of the normalised leaderboard view via `/api/export`.
- Leaderboard columns: `RPS (overall)`, `RPS (peak phase)`, `p95 ms (as-sched)`,
  `Error %`, `CPU peak/mean % (net)`, `Mem peak MiB (net)`, `Mem Δ MiB`,
  `MB / 1k req`, `Health /100`, `Verdict`, `image_digest`.

### 3. Deeper runtime collectors
- Dedicated adapters replacing the generic `/__hlc/runtime` endpoint: JMX /
  Spring Actuator, V8 `getHeapStatistics` + GC observer, Go `/debug/pprof` and
  `runtime/metrics`, `dotnet-counters`, PHP-FPM status, Rust `jemalloc` stats.
- `instrumented_live` mode: node_exporter / cAdvisor / Prometheus / APM
  collector for servers you own but do not run in Docker.

### 4. Maximum-rigor isolation (optional)
- A dedicated collector **sidecar** container (`--pid=container:<target>` with
  a read-only `/sys/fs/cgroup` mount) so cgroup reads are not charged to the
  target cgroup at all.
- A separate load-generator container on the Docker network, replacing the
  Windows GIL-bound thread pool, eliminating generator/target CPU contention.
- Both plug into `collectors.base.Collector` / `config.generator.isolated`
  without touching `engine.py` internals.

### 5. Workload realism
POST / auth / cookies / sessions, browser-driven scenarios, DB seeding, and
per-route payload hashing (`expected_payload_sha`) so every language really
serves the same bytes.

## Ordering rationale

Do (1) and (2) before (3)-(5): a comparison report with honest, comparable
numbers is more valuable than deeper metrics on a single site. The fairness
controls and the `can_compare` guard exist precisely to stop an early
cross-language comparison from being wrong.
