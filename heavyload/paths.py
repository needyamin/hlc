"""Single source of truth for the Heavy Load Checker filesystem layout.

Everything that used to hard-code ``os.path.join(ROOT, "runs")`` goes through
here, so ``output_dir`` can be honoured and the archive can be found from any
entry point (cli.py, server.py, selftest.py).
"""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(ROOT, "config.json")
UI_DIR = os.path.join(ROOT, "ui")
DEFAULT_RUNS_DIRNAME = "runs"
RUNS_DIR = os.path.join(ROOT, DEFAULT_RUNS_DIRNAME)
TARGETS_DIR = os.path.join(ROOT, "targets")
BENCHMARKS_DIR = os.path.join(ROOT, "benchmarks")
ARCHIVE_DIR = os.path.join(ROOT, "_archive")
DOCS_DIR = os.path.join(ROOT, "docs")
TESTS_DIR = os.path.join(ROOT, "tests")

SAFE_ARTIFACTS = {
    "report.html": "text/html; charset=utf-8",
    "runtime_comparison.html": "text/html; charset=utf-8",
    "runtime_comparison.json": "application/json; charset=utf-8",
    "results.json": "application/json; charset=utf-8",
    "stage2_verdict.json": "application/json; charset=utf-8",
    "config.json": "application/json; charset=utf-8",
    "preflight.json": "application/json; charset=utf-8",
    "status.json": "application/json; charset=utf-8",
    "raw.jsonl": "application/x-ndjson; charset=utf-8",
}


def runs_dir(root_dir=None, cfg=None):
    """Resolve the output directory, honouring ``config.output_dir``.

    ``root_dir`` wins over ``paths.ROOT`` so ``cli.py --root`` keeps working.
    Relative ``output_dir`` values stay relative to root_dir; absolute values
    are rejected by ``config.normalize`` and therefore never reach here.
    """
    base = root_dir or ROOT
    name = ((cfg or {}).get("output_dir") or DEFAULT_RUNS_DIRNAME).strip()
    return os.path.join(base, name)


def archive_runs_dir(root_dir=None):
    return os.path.join(root_dir or ROOT, "_archive", "runs")
