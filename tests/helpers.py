"""Shared test fixtures for Heavy Load Checker comparison/campaign tests.

Builds synthetic v2 result documents that mirror the shape produced by the real
runner, so the comparison and aggregation logic can be unit-tested without
Docker or a live target.
"""

import json
import os


def make_result(run_id="run-x", language="python", site_id="py",
                mode="docker_lab", visibility="full_server", **over):
    """Return a complete v2 result dict for assertions.

    ``over`` overrides individual metric scalars so tests can craft scenarios
    (e.g. differing RPS, CPU, memory) without repeating the whole skeleton.
    """
    summary = {
        "total_requests": over.get("total_requests", 3000),
        "overall_rps": over.get("overall_rps", 123.4),
        "duration_s": over.get("duration_s", 33.0),
        "s1_error_pct": over.get("error_pct", 0.2),
        "as_scheduled_latency": {"p95": over.get("p95", 12.5)},
        "all_requests_latency": {"p95": over.get("all_p95", 14.0)},
    }
    resources = {
        "cpu_peak_pct_net": over.get("cpu_peak", 42.1),
        "cpu_mean_pct_net": over.get("cpu_mean", 31.7),
        "anon_peak_mib_net": over.get("anon_peak", 55.0),
        "anon_net_change_mib": over.get("anon_net", 12.3),
        "pids_max": over.get("pids_max", 7),
        "health_success_pct": over.get("health_pct", 99.9),
    }
    target = {
        "site_id": site_id,
        "group": "hello",
        "language": language,
        "runtime_version": over.get("runtime_version", "3.13"),
        "framework": over.get("framework", "fastapi"),
        "image_digest": over.get("image_digest", "sha256:abc"),
        "stack_label": over.get("stack_label", "%s fastapi" % language),
    }
    adjudication = {
        "health_score": over.get("health_score", 98.0),
        "verdict": over.get("verdict", "GO under conditions"),
    }
    meta = {
        "run_id": run_id,
        "target_url": "http://localhost:8090/",
        "profile": "smoke",
        "started_utc": "2025-01-01T00:00:00Z",
        "workload_class": over.get("workload_class", "http_loadtest"),
    }
    phases = [
        {"requests": 1500, "rps": 120.0},
        {"requests": 1500, "rps": 127.0},
    ]
    return {
        "schema_version": 2,
        "benchmark_mode": mode,
        "visibility": visibility,
        "summary": summary,
        "resources": resources,
        "target": target,
        "adjudication": adjudication,
        "meta": meta,
        "environment": {
            "fingerprint": {
                "runtime_version": over.get("runtime_version", "3.13"),
                "image_digest": over.get("image_digest", "sha256:abc"),
            }
        },
        "phases": phases,
        "verdict_code": over.get("verdict_code", "GO"),
    }


def write_run(root, run_id, res):
    """Persist a result under root/runs/<run_id>/results.json (as the runner does)."""
    d = os.path.join(root, "runs", run_id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    return d
