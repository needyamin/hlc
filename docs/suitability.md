# Technology suitability assessment

`heavyload/suitability.py` turns a run's cross-language evidence into a
weighted, per-criterion suitability ranking — with every score's provenance
attached. The design goal is the opposite of a "which language is fastest"
blog post: measured data and qualitative judgments are **never mixed
silently**.

## The two kinds of criteria

| Provenance | Source | Examples |
|---|---|---|
| `measured` | this run's checksum-verified runtime comparison (compute time, OS-measured peak working set, OS-measured CPU time); HTTP criteria from multi-language campaigns when present | compute performance, memory efficiency, CPU efficiency |
| `documented` | curated 0–10 judgments with a one-line justification each, published in the knowledge base inside the module | ecosystem maturity, library depth, maintainability, reliability track record, concurrency model |

Rules baked into the module:

1. **Normalization** is log-linear between the best and worst measured value
   of a criterion (10 = best, 0 = worst; rank-preserving, scale-free, and
   documented in the code so anyone can recompute a score by hand).
2. **Weight redistribution** — a criterion with no evidence is excluded and
   its weight is redistributed proportionally over the criteria that DID
   score. Every row then reports `measured_weight_share`
   (effective weight backed by this run's measurements),
   `documented_weight_pct_of_total` and `missing_weight_pct_of_total`.
3. **HTTP criteria require campaign data.** A single-run HTTP result
   describes ONE target application, not a language, so `http_throughput`,
   `http_latency` and `http_stability` stay out of single-run scoring
   (deliberately). Run a campaign to feed them.
4. **Determinism** — same inputs produce the same ranking; the bootstrap and
   normalization have no randomness.
5. **Honest exclusion** — a language with neither measured data nor a
   documented entry is listed under "No score available" instead of guessed.

## Weights

Weights live in `config.json` → `suitability.weights` (relative; normalized
to 1.0 in the module). Defaults:

```
compute_performance 20, memory_efficiency 10, cpu_efficiency 5,
http_throughput 15, http_latency 10, http_stability 5,
ecosystem_maturity 10, ecosystem_libraries 5, maintainability 10,
reliability 5, concurrency_model 5
```

Change them to match your context (e.g. an embedded team may raise memory and
CPU weights; a startup may raise ecosystem/maintainability). Unknown keys are
ignored; the module records the weights actually used in `suitability.json`.

## Artifacts

* `suitability.json` — full data: weights, criteria descriptions, ranked rows
  with per-criterion scores + provenance + evidence strings, unranked rows,
  the disclaimer.
* `suitability.html` — standalone report: method & honesty rules, overall
  ranking with measured-share columns, per-criterion detail grid (hover a
  score for its evidence; blue = measured, amber = documented), criteria &
  weights table, limitations, and a documented framework-context list that is
  explicitly *not* measurements.
* Section 15 of the main `report.html` — compact ranking + link.

## Reading the ranking responsibly

The measured numbers are from THIS machine, THIS OS and the pinned workloads.
The documented scores are hypotheses, included for completeness and always
labelled. Framework choice, team experience, hiring and operations tooling
routinely dominate real decisions — the report says so, and the campaign
workflow is the intended way to add YOUR stack's measured evidence.
