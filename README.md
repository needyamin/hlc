# Heavy Load Checker

A standard-library-only Python HTTP load tester with a local web UI. It drives
a phased workload at any HTTP target from your host and automatically runs a
**cross-language runtime comparison** (14 languages) plus a **technology
suitability assessment** on every run.

> **Vantage point (the one thing to understand first):** every run is
> *client-only*. Latency, throughput, error taxonomy and availability are real
> observations made over the wire; server-side CPU, memory and OOM state are
> **not visible from outside and are never estimated** — those SLOs are graded
> `N/A - insufficient evidence`. What IS measured on your side is the load
> generator itself (CPU + working set), so client-side saturation can never
> hide inside the percentiles. Container instrumentation existed in v1 and was
> removed in v2 (restore it from git history if ever needed).

## Quickstart

```bat
start.bat
```
opens <http://127.0.0.1:8787/> (Windows). Or:

```bash
python server.py --open              # web UI
python scripts/demo_target.py        # real local target on http://127.0.0.1:8090/ (/, /catalog, /health)
python cli.py --profile smoke        # headless, ~33 s
python cli.py --profile heavy        # heavy load: ramp to C=200, sustained, spike
python cli.py --profile stress       # stress conditions: ramp to C=400, long soak
python cli.py --verify-sources       # checksum-verify every detected toolchain
python cli.py --runtime-comparison   # language benchmark only, no HTTP load
python cli.py --campaign benchmarks/lang-runtime.json   # multi-site campaign
python cli.py --compare run-a run-b --output out/       # compare runs
python selftest.py                   # end-to-end regression test
python -m unittest discover -s tests -v
```

Requirements: Python 3.10+ (tested on 3.15). No pip install. No Docker.

## What a run produces

Every run writes into `runs/<run-id>/`:

| Artifact | Contents |
|---|---|
| `report.html` | **THE report — one self-contained page covering everything**: executive summary, methodology, KPIs, S1–S9 SLO scorecard, time-series charts, **scalability & concurrency analysis**, phase/route breakdowns, generator resource evidence, data-quality & accuracy, strengths/weaknesses/limitations, findings, go/no-go, config & environment — **plus the full language-runtime comparison** (every language and every Spid3r engine variant, checksum-gated medians with dispersion, slowdown ratios, pairwise matrix) **and the full suitability assessment** (ranking, dedicated Spid3r subject-language report, per-criterion provenance). Wide tables scroll horizontally inside their cards. No JS, no external resources. |
| `results.json` | Full v2 metric document (schema-stable, additive migration for archived v1 runs) |
| `runtime_comparison.json` | Raw comparison data: every repeat, per-variant measured peak memory & CPU time, checksums (the rendered tables live inside `report.html`) |
| `suitability.json` | Raw suitability data: weights, per-criterion scores + provenance (rendered inside `report.html`) |
| `raw.jsonl` | One line per request — every published number traces back to it |
| `config.json`, `preflight.json`, `status.json`, `stage2_verdict.json` | Reproducibility & live telemetry |

## Language runtime comparison (runs on every benchmark by default)

Five identical, **checksum-verified** workloads run in every detected runtime.
A variant that prints a wrong result is REJECTED and never published — no
simulated numbers can reach a report, ever.

| ID | Workload | What it exercises |
|---|---|---|
| A | Prime counting, N = 50,000 | pure interpreter integer loops |
| B | JSON round-trip, 200 rows x 600 rounds | native serializers + parsed-field loops |
| C | String-keyed map lookups, 64 keys x 100,000 | route / config / session lookups |
| D | Text template building, 50,000 rows | string churn, GC pressure, formatting |
| E | Integer matmul, 128 x 128 (transposed B) | tight numeric loops, exact i64 arithmetic |

Supported runtimes (auto-detected; missing toolchains are skipped with a
warning, never faked):

**Spid3r** (5 engine variants: tree-walk, VM, VM `--no-opt`, bytecode exec,
native `--release`), **Python, JavaScript/Node, TypeScript, PHP, Ruby, Java,
Kotlin, Go, C#/.NET, Swift, Rust, C, C++**.

Workload B exists only where the language's standard library ships a JSON
serializer (Java/Kotlin/C/C++/Rust are documented `not applicable` — a
hand-rolled parser would not be the native-serializer comparison B measures).

Per variant the report publishes: in-process `TIME_US` (startup excluded),
median of N repeats with full dispersion (stdev, CV%, IQR, t-based 95% CI of
the mean, bootstrap 95% CI of the median), wall clock, and **OS-measured peak
working set + CPU time** (Windows `GetProcessMemoryInfo`/`GetProcessTimes`,
POSIX `wait4` rusage — or honest `unavailable`).

```bash
python cli.py --verify-sources   # compile+run every detected toolchain
                                 # against the pinned checksums: a table
```

Expected verified checksums (a mismatched variant is rejected, never timed):

```
A  prime   RESULT 5133 121013308
B  json    RESULT 249868800 9163
C  map     RESULT 44432313 100000
D  text    RESULT 1295428 300465975
E  matmul  RESULT -1985 -40
```

## Heavy-load testing profiles

| Profile | Duration | Peak C | Purpose |
|---|---|---|---|
| `smoke` | ~33 s | 20 | fast validation of an unknown target |
| `quick` | ~3.5 min | 100 | meaningful ramp + sustained + spike |
| `heavy` | ~5.5 min | 200 | heavy load: ramp 25→150, sustained C=150, break-point spike |
| `stress` | ~10.5 min | 400 | stress: ramp to C=300, sustained C=300, long soak |
| `standard` | ~24 min | 300 | the original frozen Spid3r schedule |

Every profile uses the same eight phases — idle baseline, warm-up, ramp,
sustained, spike, soak, zero-load cooldown, availability re-probe — so memory
of the generator and recovery behaviour mean the same thing in every run.
Guardrails (generator working-set ceiling, host RAM floor, error-rate ceiling,
consecutive-failure breaker) can abort a run early; an aborted run is graded
*right-censored*, never passed silently.

## Scalability analysis

The engine records one time window per concurrency level (per step inside
ramp/spike phases, per phase otherwise) and derives — from measured requests
only — the throughput-vs-concurrency curve, RPS-per-worker efficiency, a
**knee point** (last level before marginal throughput collapses or p95
inflates beyond 2x the best level) and a conservative classification
(`linear / sublinear / plateau / degraded`). Fewer than two measured levels
reports `insufficient_evidence` instead of guessing.

## Data integrity and accuracy

* Checksum gates (above) — wrong results are rejected, never published.
* `heavyload/quality.py` inspects every run: malformed raw lines, sampler
  coverage gaps, percentile monotonicity, runt phases, generator saturation,
  latency-bounds sanity. Findings are labelled `ok / warn / fail /
  insufficient_evidence` and surface in grading and the report.
* Percentiles carry the coordinated-omission view (`as_scheduled_latency`) and
  generator dispatch lag is measured separately so client scheduling delay is
  never blamed on the target.
* Timer-resolution floors and noisy (CV > 15%) variants are flagged in the
  comparison report, not hidden.

## Comparability: when may two runs be ranked?

| Situation | Comparable? |
|---|---|
| Archived `docker_lab` runs (v1) with server evidence | yes, against each other |
| Loopback targets, same host + same profile (`client_only_same_host`) | **yes — single-host client-side comparison** (explicit caveats) |
| Different generator host / target host / profile / schema | no, with a stated reason |
| Remote-target client-only runs | never (uncontrolled network) |

`schema.can_compare()` enforces this before any leaderboard; a refusal prints
its reason instead of producing a misleading ranking.

## Measuring a website written in language X

1. Write a spec in `targets/<site-id>.json` (start from
   `targets/examples/python-demo.json`). Keep the **same routes, payloads and
   expectations** for every language.
2. Start it natively: `python scripts/native_target.py <site-id> start`
   (uses `native.start_cmd`, waits for `health_path`; `stop` ends it by PID
   record).
3. Run at least 3 repeats (`repeat_count` in the spec) and compare medians —
   one noisy run never decides a ranking.
4. `python cli.py --campaign benchmarks/lang-runtime.json` runs the campaign
   and writes `benchmarks/<id>/campaign.json` + `report.html` + leaderboard
   CSV/JSON.

## Cleanup / safety

`scripts/archive_legacy.py` still handles the legacy archive workflow
(manifest / backup / verify / recycle-caches — nothing deletes without
`--confirm`).

## Architecture at a glance

Six diagrams in [docs/diagrams/](docs/diagrams/) explain the system end to
end (plain inline-styled SVG, no JS, no CDN, works offline). Start with 01 and
02; the [single-file gallery](docs/diagrams/index.html) inlines all six plus a
developer reference (module map, config keys, schema, SLOs, API, invariants).

| # | Diagram | Question it answers |
|---|---|---|
| 01 | [System context](docs/diagrams/01-system-context.svg) | What can this tool actually measure? |
| 02 | [Module architecture](docs/diagrams/02-architecture.svg) | Which module does what? |
| 03 | [Run lifecycle](docs/diagrams/03-run-lifecycle.svg) | What happens during one run? |
| 04 | [Data flow](docs/diagrams/04-data-flow.svg) | Where does every published number come from? |
| 05 | [Comparability gate](docs/diagrams/05-benchmark-modes.svg) | When may two runs be ranked? |
| 06 | [Runtime comparison](docs/diagrams/06-runtime-comparison.svg) | How is the language benchmark kept honest? |

To change a diagram, edit [docs/diagrams/generate.py](docs/diagrams/generate.py)
and run `python docs/diagrams/generate.py`; the SVGs are generated files.

## Documentation

- [docs/comparability.md](docs/comparability.md) — when runs may be ranked, and why client-only is the honest default
- [docs/suitability.md](docs/suitability.md) — the suitability criteria, weights and provenance rules
- [docs/metric-schema.md](docs/metric-schema.md) — v2 result fields and units
- [docs/architecture.md](docs/architecture.md) — module map and data flow
- [docs/phase2-roadmap.md](docs/phase2-roadmap.md) — campaigns (built) + deferred work
- [docs/diagrams/](docs/diagrams/) — architecture diagrams (regenerate with `python docs/diagrams/generate.py`)
