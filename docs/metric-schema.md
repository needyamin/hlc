# Result schema v2

`runs/<run-id>/results.json`. All v2 additions are **additive**: every v1 key
(`meta`, `summary`, `resources`, `series`, `adjudication`, …) is preserved, so
`report.py`, `grading.py` and the UI keep working. `heavyload/schema/migrate.py`
upgrades v1 documents by adding defaults only; `ensure_current()` is idempotent.

## Units

| Kind | Unit |
|---|---|
| bytes | integer |
| durations | float milliseconds (raw timers use nanoseconds internally) |
| percent | float 0..100 |
| counts | integer |
| `t` | float seconds since run start |
| wall timestamps | ISO-8601 UTC |

## Top-level v2 fields

```
schema_version: 2
benchmark_mode: docker_lab | live_blackbox | instrumented_live
visibility:     full_server | client_only | server_via_adapter
per_metric_visibility: { "<dotted.group>": "<level>|unavailable" }
verdict_code:   GO | NO_GO | NO_GO_ABORT | INSUFFICIENT
target:         { site_id, group, language, runtime_version, framework,
                  framework_version, image, image_digest, build_cmd,
                  start_cmd, stack_label }
environment.fingerprint: { image_digest, runtime_version, cgroup_version,
                  docker_version, kernel, cpu_model, pinned_cpus[],
                  host_shared_cpu, generator_pinned, seed,
                  generator_fingerprint, target_host }
metrics:        { http, resources, runtime, browser }   # below
step_metrics:   [ { phase, role, concurrency, start_s, end_s, duration_s,
                    requests, rps, errors, error_pct, bytes_mib,
                    latency_200, latency_all } ]
scalability:    { status, classification, knee_concurrency,
                  best_throughput_concurrency, best_rps, points[], note }
quality:        { status, checks[ {name, status, detail, evidence} ],
                  summary, note }
suitability:    { status, weights, criteria, ranked[], unranked[],
                  disclaimer }            # compact digest of suitability.json
runtime_comparison: { status, verdict, slowdown, ... }   # compact digest
```

`meta` additionally carries `schema_version`, `benchmark_mode`,
`workload_class` (`http_loadtest`), `mode_inferred`, `mode_note`, and a real
`started_utc` / `finished_utc` pair. `meta.container` /
`meta.mysql_container` are gone in v2 runs (archived v1 documents keep them).

## Visibility levels

```
full_server | client_only | client_only_same_host | server_via_adapter
```

`client_only_same_host` is assigned by the runner for loopback targets and is
the only client-only level that `can_compare()` may rank (single-host policy;
see docs/comparability.md).

## `metrics`

```
http:      rps, error_pct, connect_ms, ttfb_ms, total_ms,
           latency_ms{p50,p90,p95,p99,max,mean}, bytes_total,
           ttfb_p95_ms, dispatch_delay_p95_ms, as_scheduled_p95_ms
resources: cpu_pct_core, mem_anon_bytes, mem_anon_mib, mem_rss_bytes,
           mem_peak_bytes, io_read_bytes, io_write_bytes, pids_current,
           oom_kill_events, psi_avg10            # all None in client-only runs
runtime:   heap_used_bytes, heap_total_bytes, gc_pause_ms, gc_count,
           goroutines, threads, fds, rss_bytes
browser:   ttfb_ms, fcp_ms, lcp_ms, dom_content_loaded_ms, tbt_ms, cls
```

`http.latency_ms.*` stays the v1 `PRIMARY_successful_200_only` distribution.
`ttfb_ms` = headers received; `body_ms` = body read; `latency_ms` = full.
`dispatch_delay_ms` = generator scheduling lag; `as_scheduled_latency` is the
coordinated-omission view (`latency + dispatch delay`).

## `series`

`health_series` and `generator_series` (v2 runs) plus `cgroup_series`,
`stats_series`, `mysql_series`, `proc_series` (v1/archived), and
`runtime_series`, `browser_series`, `repeats`. Absent series are explicitly
labelled `unavailable` in `per_metric_visibility` instead of pretending to be
data.

`generator_series` records: `t`, `wall`, `phase`, `cpu_time_s` (monotonic
process CPU), `cpu_pct` (derived per sample), `rss_mib`, `peak_rss_mib`
(OS-tracked peak), `host_free_gb`.

## Additional `resources` fields (v2, all measured)

```
generator_samples, gen_cpu_peak_pct, gen_cpu_mean_pct,
gen_cpu_idle_mean_pct, gen_cpu_peak_pct_net,
gen_rss_start_mib, gen_rss_end_mib, gen_rss_peak_mib,
gen_rss_peak_mib_os, gen_rss_net_change_mib,
health_success_pct, health_probe_latency, host_free_gb_min
```

Server-side v1 resource fields remain in the schema with `None` values in
client-only runs so grading (S5/S6/S8 = N/A) and archived-run comparisons
stay stable. The `_net` values subtract the idle-plateau baseline.

## Runtime-comparison row fields (runtime_comparison.json)

```
key, runtime, group, source("host"|"docker"), result, compute_ms,
compute_min_ms, compute_max_ms, wall_ms, compute_runs_ms[],
stats{ median_ms, mean_ms, stdev_ms, cv_pct, q1_ms, q3_ms, iqr_ms,
       min_ms, max_ms, mean_ci95, median_ci95, flags[], stable },
peak_rss_bytes, peak_rss_min_bytes, peak_rss_max_bytes,
cpu_user_s, cpu_kernel_s, proc_metrics_method
```

`proc_metrics_method` names the OS mechanism (or `unavailable`) so every
measured resource number carries its provenance.

## Comparability guard

`schema.metrics.can_compare(a, b)` returns `(False, reason)` when
`benchmark_mode`, `visibility`, `schema_version` or `workload_class` differ;
plain `client_only` runs (remote targets) are refused outright;
`client_only_same_host` pairs are accepted only when BOTH
`generator_fingerprint` and `target_host` match. Archived `docker_lab` pairs
keep working unchanged.

## Migration

```python
from heavyload.schema import ensure_current, is_current
ensure_current(results)                     # v1 -> v2, in place, idempotent
from heavyload.schema.migrate import upgrade_file
upgrade_file("runs/run-.../results.json", write=True)
```

Archived v1 runs live in `_archive/runs/` and are never rewritten in place.
