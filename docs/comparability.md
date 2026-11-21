# Comparability: when may two runs be ranked?

Replaces the v1 *benchmark-modes* document (Docker-era). v2 has one vantage
point — a client-only measurement from the host — so comparability is decided
by **what can actually be held constant**, not by an instrumentation switch.

## The rule

`heavyload/schema/metrics.py::can_compare()` gates every leaderboard and
returns `(bool, reason)`. A reason string is ALWAYS attached to a refusal —
the UI and reports show it instead of silently dropping a run.

| Pair | Comparable | Why |
|---|---|---|
| Archived `docker_lab` (v1) + archived `docker_lab` (v1) | yes | both carry server evidence from the same instrumentation era |
| `client_only_same_host` + `client_only_same_host` | **yes** | loopback targets, same generator fingerprint, same schema/workload class/profile |
| `client_only_same_host` + anything else | no | mode/visibility/schema differ |
| plain `client_only` (remote target) + anything | **never** | network conditions cannot be held constant; no server evidence |
| missing generator fingerprint (archived run) + same-host run | no | the gate needs both sides to prove same-machine conditions |

## What `client_only_same_host` means

The runner assigns this visibility when `target_url` points at a loopback
host (`127.0.0.1`, `::1`, `localhost`) and records into the fingerprint:

* `generator_fingerprint` = `hostname|platform|machine|cpu_count`
* `target_host` = the target's host component

Two runs are rankable only when **both** match plus the usual
`benchmark_mode` / `visibility` / `schema_version` / `workload_class`
equality. The comparison report labels the result
*"single-host client-side comparison — not a server-resource benchmark"* and
carries these caveats:

* target and load generator share physical cores; generator saturation is
  monitored (`generator_series`) and reported per run
* latency includes the loopback stack — that is the point (same conditions
  for both sides), but it is not a LAN/production number
* server CPU/memory stays invisible; resource-based criteria are absent from
  the HTTP leaderboard by design

## Campaigns

`benchmarks/<name>.json` campaigns run the same workload against N sites on
one host. Comparability is enforced per site against the first measured
member; a site measured under a different profile is flagged
`not comparable` with reasons in `campaign.json`, and the leaderboard ranks
only the comparable set. Run campaigns with
`python scripts/native_target.py <site> start` per target, then
`python cli.py --campaign <file>`; stop targets with `stop` afterwards.

## Why client-only is the honest default

A load generator that lives outside the target's process boundary cannot see
server-internal counters without an instrumentation contract. v1 used a
Docker container as that contract; v2 removes it. Pretending otherwise —
grading memory SLOs from guesses, ranking runs measured under different
conditions — produces exactly the misleading comparisons this tool refuses to
publish. When you need server-side evidence again, the v1 instrumentation
(code in git history) or a runtime adapter
(`heavyload/adapters/`, `server_via_adapter` visibility) is the supported
path, and its outputs stay labelled separately.
