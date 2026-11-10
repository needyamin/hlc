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
                  host_shared_cpu, generator_pinned, seed, collector_mode }
metrics:        { http, resources, runtime, browser }   # below
```

`meta` additionally carries `schema_version`, `benchmark_mode`,
`workload_class` (`http_loadtest`), `mode_inferred`, `mode_note`, and a real
`started_utc` / `finished_utc` pair.

## `metrics`

```
http:      rps, error_pct, connect_ms, ttfb_ms, total_ms,
           latency_ms{p50,p90,p95,p99,max,mean}, bytes_total,
           ttfb_p95_ms, dispatch_delay_p95_ms, as_scheduled_p95_ms
resources: cpu_pct_core, mem_anon_bytes, mem_anon_mib, mem_rss_bytes,
           mem_peak_bytes, io_read_bytes, io_write_bytes, pids_current,
           oom_kill_events, psi_avg10
runtime:   heap_used_bytes, heap_total_bytes, gc_pause_ms, gc_count,
           goroutines, threads, fds, rss_bytes
browser:   ttfb_ms, fcp_ms, lcp_ms, dom_content_loaded_ms, tbt_ms, cls
```

`http.latency_ms.*` stays the v1 `PRIMARY_successful_200_only` distribution.
`ttfb_ms` = headers received; `body_ms` = body read; `latency_ms` = full.
`dispatch_delay_ms` = generator scheduling lag; `as_scheduled_latency` is the
coordinated-omission view (`latency + dispatch delay`).

## `series`

`cgroup_series`, `stats_series`, `health_series`, `mysql_series` (v1) plus
`proc_series`, `runtime_series`, `browser_series`, `repeats`. An empty
`mysql_series` is explicitly labelled `unavailable` in
`per_metric_visibility` instead of pretending to be data.

## Additional `resources` fields

```
cpu_idle_mean_pct, cpu_peak_pct_net, cpu_mean_pct_net,
anon_idle_mean_mib, anon_peak_mib_net, proc_summary
```

The `_net` values subtract the idle plateau baseline. Original v1 fields
(`cpu_peak_pct`, `anon_peak_mib`, …) are untouched so grading is unchanged.

## Comparability guard

`schema.metrics.can_compare(a, b)` returns `(False, reason)` when
`benchmark_mode`, `visibility`, `schema_version` or `workload_class` differ,
or when either run is `client_only`. Phase 2's comparison report must call it
before producing any number.

## Migration

```python
from heavyload.schema import ensure_current, is_current
ensure_current(results)                     # v1 -> v2, in place, idempotent
from heavyload.schema.migrate import upgrade_file
upgrade_file("runs/run-.../results.json", write=True)
```

Archived v1 runs live in `_archive/runs/` and are never rewritten in place.
