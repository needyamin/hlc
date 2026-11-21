# Roadmap — multi-language benchmark campaigns and beyond

**Status (v2):** Campaigns and comparisons are **BUILT and tested**. The v2
round added the 14-language runtime comparison, measured per-variant
resources (procmetrics), scalability analysis, data-quality checks, the
technology suitability assessment, heavy/stress profiles and native (Docker-
free) target lifecycle. Container instrumentation was removed; see
[comparability.md](comparability.md) for the single-host comparability policy.

## What is built

- Campaigns: `benchmarks/<name>.json` → `heavyload/campaign.py` (per site:
  warm-up discarded + `repeat_count` measured passes, median aggregation,
  `can_compare` guard, graceful stop, `campaign.json` + `report.html` +
  leaderboard CSV/JSON). Start targets natively with
  `scripts/native_target.py <site> start`.
- Comparisons: `heavyload/compare.py` + `report_compare.py` with a
  comparability badge and CSV/JSON export; UI multi-select Compare/Export.
- Language comparison: 14-language registry (`comparison_sources/`),
  checksum integrity gate, medians + dispersion + CIs, OS-measured peak
  memory/CPU per variant, slowdown ratios + pairwise geomean matrix,
  `cli.py --verify-sources`.
- Suitability: `heavyload/suitability.py` with measured + labelled
  documented criteria and per-row provenance shares.
- Quality + scalability: `heavyload/quality.py`, `heavyload/scalability.py`
  wired into every run and the report.

## Deferred (still valuable, in rough order)

### 1. HTTP campaign criteria feeding the suitability ranking
The suitability module reserves weights for `http_throughput`, `http_latency`
and `http_stability` but excludes them from single-run scoring. Wiring
campaign leaderboards into `suitability.build()` (per-language measured
values + repeat-CV dispersion) activates them.

### 2. Deeper runtime collectors
Dedicated adapters replacing the generic `/__hlc/runtime` endpoint: JMX /
Spring Actuator, V8 `getHeapStatistics` + GC observer, Go `/debug/pprof` +
`runtime/metrics`, `dotnet-counters`, PHP-FPM status, jemalloc stats for
Rust. All slot into `heavyload/adapters/` (`server_via_adapter` visibility)
without touching the engine.

### 3. Open-loop workload model
The closed-loop pool is deliberately simple and identical across languages;
an open-loop (paced) mode would measure queueing behaviour at arrival rate
rather than saturation. Requires scheduler work in `engine.py` plus new
quality checks (paced-vs-actual drift).

### 4. Workload realism
POST / auth / cookies / sessions, browser-driven scenarios, DB seeding, and
per-route payload hashing (`expected_payload_sha`) so every language really
serves the same bytes.

### 5. Cross-machine campaign orchestration
Run the same campaign on N hosts and aggregate with a machine-level
`generator_fingerprint`; the comparability gate already refuses cross-host
pairing, so this needs an explicit "consent + label" extension rather than a
silent widening.

## Ordering rationale

The comparison core exists so that every new metric lands inside an honest,
gated pipeline. Item 1 is next because it completes the suitability data
model with measurements you already collect; deeper collectors and workload
realism stay additive after that.
