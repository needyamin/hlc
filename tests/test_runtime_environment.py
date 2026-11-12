"""Regression tests for the exclusive ``runtime_environment`` switch.

QA-owned file (task #5). Stdlib ``unittest`` only -- no Docker, Spid3r, PHP,
Node, Rust, a live HTTP target or a browser is required. Every external tool is
either monkeypatched (``RC._call``, ``runner.Benchmark``, ``runner.preflight``)
or exercised through an in-process HTTP server bound to 127.0.0.1:0.

Coverage map (phase-1 checklist + reviewer addenda):
  A1-A3  ConfigDefaultsAndCoercion
  A4     RuntimeComparisonGating
  A5     CliFlagAndCoherence / ApiConfigRoundTrip / UiStaticContract
  A6     ReportHonestyAndClientOnlyInvariant
  A7     deferred: ``selftest.py`` + ``unittest discover`` are run by QA only
         after team-lead's explicit "implementation complete" signal.
  B1-B4  ReportHonestyAndClientOnlyInvariant
  C1-C5  ApiConfigRoundTrip / CliFlagAndCoherence / V1ProvenanceGuard
  #6     V1ProvenanceGuard (v1 provenance guard on real + synthetic archives)
"""
from __future__ import annotations

import contextlib
import copy
import glob
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock
from urllib import request as urlreq
from urllib.error import HTTPError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import config as C  # noqa: E402
from heavyload import grading, report, runner  # noqa: E402
from heavyload import runtime_comparison as RC  # noqa: E402
from heavyload.schema import (  # noqa: E402
    SCHEMA_VERSION as RESULT_SCHEMA_VERSION,
    can_compare,
    ensure_current,
)
import cli  # noqa: E402


# The spec, written literally: these are what the switch MUST produce, not just
# whatever RUNTIME_ENVIRONMENT_DEFAULTS happens to hold. Catches a wrong table.
EXPECTED_WINDOWS = {
    "mode": "direct", "benchmark_mode": "live_blackbox",
    "include_container": False, "include_windows": True,
}
EXPECTED_DOCKER = {
    "mode": "auto", "benchmark_mode": "auto",
    "include_container": True, "include_windows": True,
}


def assert_preset(testcase, cfg, env, expected):
    """Assert all four derived keys + the switch itself match a preset."""
    testcase.assertEqual(cfg["runtime_environment"], env)
    testcase.assertEqual(cfg["mode"], expected["mode"])
    testcase.assertEqual(cfg["benchmark_mode"], expected["benchmark_mode"])
    rc = cfg["runtime_comparison"]
    testcase.assertEqual(rc["include_container"], expected["include_container"])
    testcase.assertEqual(rc["include_windows"], expected["include_windows"])


def fake_tools():
    """A full toolchain + a resolvable Spid3r container, no real probes."""
    return {
        "spid3r": {"available": True, "version": "spid3r 1.0.0"},
        "php": {"available": True, "version": "8.3.1"},
        "python": {"available": True, "version": "3.13.0"},
        "node": {"available": True, "version": "v20.11.0"},
        "rustc": {"available": True, "version": "rustc 1.76.0"},
        "container": {"available": True, "name": "spid3rweb-app",
                      "version": "spid3r 1.0.0", "spid3r": True},
    }


def archived_results():
    return sorted(glob.glob(os.path.join(ROOT, "_archive", "runs",
                                        "*", "results.json")))


def archived_v1_rc_docs():
    out = []
    for pattern in ("runs/*/runtime_comparison.json",
                    "_archive/runs/*/runtime_comparison.json"):
        for path in sorted(glob.glob(os.path.join(ROOT, *pattern.split("/")))):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    doc = json.load(f)
            except Exception:
                continue
            if int(doc.get("schema_version") or 1) == 1:
                out.append((path, doc))
    return out


def synthetic_v1_archive():
    """Mirror the three real schema-1 artifacts: Docker rows, no provenance.

    schema_version=1, config_meta WITHOUT include_container, 6 rows of which
    two are ``spid3r_docker_*``, and environment.spid3r_container_name set.
    Exactly the state that used to render "Windows host" + "No Docker
    container was contacted" while suppressing the different-env caveat.
    """
    def row(key, runtime, group, ms):
        return {"key": key, "runtime": runtime, "group": group,
                "compute_ms": ms, "compute_min_ms": ms - 1,
                "compute_max_ms": ms + 1,
                "result": "RESULT 5133 121013308"}
    rows = [
        row("php", "PHP 8.3", "baseline", 5.0),
        row("python", "Python 3.13", "baseline", 6.0),
        row("spid3r_tw", "Spid3r 1.0.0 tree-walk", "spid3r", 10.0),
        row("spid3r_vm", "Spid3r 1.0.0 VM", "spid3r", 9.0),
        row("spid3r_docker_tw",
            "Spid3r 1.0.0 tree-walk (Docker: spid3rweb-app)", "spid3r", 12.0),
        row("spid3r_docker_vm",
            "Spid3r 1.0.0 VM (Docker: spid3rweb-app)", "spid3r", 11.0),
    ]
    return {
        "schema_version": 1,
        "generated_utc": "2026-01-01T00:00:00Z",
        "status": "ok",
        "config": {"repeats": 5, "workloads": ["prime"]},
        "config_meta": {"constants_pinned": True, "prime_n": 50000,
                        "json_rows": 200, "json_rounds": 600,
                        "map_keys": 64, "map_rounds": 100000, "repeats": 5},
        "environment": {"spid3r_container_name": "spid3rweb-app",
                        "platform": "win32"},
        "summary": {"verdict": "Spid3r is slower.", "note": "compute only",
                    "workloads_verified": 1, "variants": 6,
                    "slowdown_range": {"min": 1.5, "max": 2.0},
                    "slowdown": {}},
        "workloads": {"prime": {
            "id": "A", "title": "Prime counting (CPU / integer loops)",
            "constants": "N = 50,000 integers", "description": "trial division",
            "expected_checksum": "RESULT 5133 121013308", "verified": True,
            "variants": 6, "rows": rows}},
        "warnings": [],
    }


def v2_rc_doc(include_container):
    """Minimal schema-2 doc with explicit ``source`` on every row."""
    rows = [
        {"key": "php", "runtime": "PHP 8.3", "group": "baseline",
         "source": "windows", "compute_ms": 5.0, "compute_min_ms": 4.0,
         "compute_max_ms": 6.0},
        {"key": "spid3r_tw", "runtime": "Spid3r 1.0.0 tree-walk",
         "group": "spid3r", "source": "windows", "compute_ms": 10.0,
         "compute_min_ms": 9.0, "compute_max_ms": 11.0},
    ]
    env_label = ("Docker - container + host baselines" if include_container
                 else "Windows - installed runtimes")
    if include_container:
        rows.append({"key": "spid3r_docker_tw",
                     "runtime": "Spid3r 1.0.0 TW (Docker: spid3rweb-app)",
                     "group": "spid3r", "source": "docker",
                     "compute_ms": 12.0, "compute_min_ms": 11.0,
                     "compute_max_ms": 13.0})
    return {
        "schema_version": 2, "generated_utc": "2026-01-01T00:00:00Z",
        "status": "ok",
        "config_meta": {"repeats": 5,
                        "runtime_environment":
                            "docker" if include_container else "windows",
                        "runtime_environment_label": env_label,
                        "include_container": include_container,
                        "include_windows": True},
        "environment": {"runtime_environment": env_label,
                        "comparison_sources":
                            "Windows + Docker" if include_container
                            else "Windows only"},
        "summary": {"verdict": "v", "note": "n", "workloads_verified": 1,
                    "variants": len(rows),
                    "slowdown_range": {"min": 1.0, "max": 2.0},
                    "slowdown": {}},
        "workloads": {"prime": {
            "id": "A", "title": "Prime counting", "constants": "N",
            "description": "d", "expected_checksum": "RESULT 5133 121013308",
            "verified": True, "variants": len(rows), "rows": rows}},
        "warnings": [],
    }


def render_rc(doc):
    td = tempfile.mkdtemp()
    try:
        out = os.path.join(td, "runtime_comparison.html")
        RC.build_html(doc, out)
        with open(out, "r", encoding="utf-8") as f:
            return f.read()
    finally:
        shutil.rmtree(td, ignore_errors=True)


# --------------------------------------------------------------------------
class ConfigDefaultsAndCoercion(unittest.TestCase):
    """A1 fresh defaults, A2 Docker preset, A3 coercion + legacy inference."""

    def test_a1_fresh_config_defaults_to_windows_client_only(self):
        cfg = C.normalize({})
        assert_preset(self, cfg, "windows", EXPECTED_WINDOWS)
        self.assertTrue(cfg["runtime_comparison"]["enabled"],
                        "runtime comparison must still run on every run")
        self.assertEqual(C.DEFAULTS["runtime_environment"], "windows")

    def test_a2_docker_preset_is_coherent_and_keeps_host_baselines(self):
        cfg = C.normalize({"runtime_environment": "docker"})
        assert_preset(self, cfg, "docker", EXPECTED_DOCKER)
        self.assertTrue(cfg["runtime_comparison"]["include_windows"],
                        "Docker is additive: host baselines keep ratios honest")

    def test_switch_is_exclusive_two_values_with_labels(self):
        self.assertEqual(tuple(C.RUNTIME_ENVIRONMENTS), ("windows", "docker"))
        self.assertEqual(set(C.RUNTIME_ENVIRONMENT_DEFAULTS),
                         set(C.RUNTIME_ENVIRONMENTS))
        self.assertEqual(set(C.RUNTIME_ENVIRONMENT_LABELS),
                         set(C.RUNTIME_ENVIRONMENTS))

    def test_a3_invalid_and_empty_values_coerce_to_windows(self):
        # A typo must self-heal to Windows; it must NEVER silently enable
        # Docker instrumentation.
        for bad in (None, "", "   ", "WINDOWS", "nonsense", "docker-lab",
                    "auto", 123, [], {}, True):
            with self.subTest(value=bad):
                cfg = C.normalize({"runtime_environment": bad})
                self.assertEqual(cfg["runtime_environment"], "windows")
                assert_preset(self, cfg, "windows", EXPECTED_WINDOWS)

    def test_valid_values_are_case_and_whitespace_normalised(self):
        for raw, want in (("WINDOWS", "windows"), ("  Docker ", "docker"),
                          ("DOCKER", "docker"), ("windows", "windows")):
            with self.subTest(value=raw):
                self.assertEqual(
                    C.normalize({"runtime_environment": raw})
                    ["runtime_environment"], want)

    def test_legacy_docker_config_infers_docker(self):
        cases = {
            "benchmark_mode=docker_lab": {"benchmark_mode": "docker_lab"},
            "benchmark_mode=instrumented_live":
                {"benchmark_mode": "instrumented_live"},
            "mode=docker": {"mode": "docker"},
            "rc.include_container": {"runtime_comparison":
                                     {"include_container": True}},
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.assertNotIn("runtime_environment", raw)
                cfg = C.normalize(raw)
                self.assertEqual(cfg["runtime_environment"], "docker")
                self.assertTrue(cfg["runtime_comparison"]["include_container"])

    def test_fresh_config_without_any_docker_evidence_stays_windows(self):
        self.assertEqual(
            C.normalize({"target_url": "http://localhost:8090/"})
            ["runtime_environment"], "windows")

    def test_explicit_advanced_overrides_are_preserved(self):
        # --mode / --benchmark-mode must remain usable; normalize() only
        # defaults derived keys that were absent from the raw input.
        cfg = C.normalize({"runtime_environment": "windows",
                           "mode": "auto",
                           "benchmark_mode": "docker_lab"})
        self.assertEqual(cfg["mode"], "auto")
        self.assertEqual(cfg["benchmark_mode"], "docker_lab")
        cfg2 = C.normalize({"runtime_environment": "docker",
                            "mode": "direct",
                            "benchmark_mode": "live_blackbox"})
        self.assertEqual(cfg2["mode"], "direct")
        self.assertEqual(cfg2["benchmark_mode"], "live_blackbox")

    def test_validate_returns_normalized_and_no_error(self):
        norm, err = C.validate({"runtime_environment": "docker"})
        self.assertIsNone(err)
        self.assertEqual(norm["runtime_environment"], "docker")

    def test_public_config_strips_private_derived_fields(self):
        cfg = C.normalize({})
        self.assertIn("_derived", cfg)
        pub = C.public_config(cfg)
        self.assertNotIn("_derived", pub)
        self.assertEqual(pub["runtime_environment"], "windows")


# --------------------------------------------------------------------------
class RuntimeSwitchDerivation(unittest.TestCase):
    """apply_runtime_environment() is the ONLY coherent switch primitive."""

    @staticmethod
    def _saved_explicit(env="docker"):
        """A fully-explicit saved config, as written by save_config()."""
        return C.public_config(C.normalize({
            "runtime_environment": env,
            "mode": "auto" if env == "docker" else "direct",
            "benchmark_mode": "auto" if env == "docker" else "live_blackbox",
            "runtime_comparison": {"include_container": env == "docker",
                                   "include_windows": True},
        }))

    def test_rewrites_all_four_derived_keys_on_fully_explicit_saved_config(self):
        # The core blocker: saved configs carry explicit derived keys, so
        # normalize() alone will NOT re-derive them. The switch must.
        saved = self._saved_explicit("docker")
        self.assertEqual(saved["mode"], "auto")           # prove it's stale
        applied = C.apply_runtime_environment(saved, "windows")
        assert_preset(self, applied, "windows", EXPECTED_WINDOWS)
        self.assertFalse(any(k.startswith("_") for k in applied))
        self.assertEqual(applied["schema_version"], C.SCHEMA_VERSION)

    def test_docker_from_saved_windows_rewrites_all_four(self):
        saved = self._saved_explicit("windows")
        applied = C.apply_runtime_environment(saved, "docker")
        assert_preset(self, applied, "docker", EXPECTED_DOCKER)

    def test_windows_docker_windows_restores_canonical_preset(self):
        # (b) must restore the CANONICAL Windows values, not resurrect a
        # legacy mode=auto / benchmark_mode=docker_lab.
        cfg = self._saved_explicit("windows")
        cfg["mode"] = "auto"
        cfg["benchmark_mode"] = "docker_lab"
        cfg["runtime_comparison"]["include_container"] = True
        cfg = C.apply_runtime_environment(cfg, "docker")
        assert_preset(self, cfg, "docker", EXPECTED_DOCKER)
        cfg = C.apply_runtime_environment(cfg, "windows")
        assert_preset(self, cfg, "windows", EXPECTED_WINDOWS)

    def test_invalid_env_raises_value_error(self):
        for bad in ("nonsense", "", None):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    C.apply_runtime_environment(self._saved_explicit(), bad)

    def test_does_not_mutate_the_input_config(self):
        saved = self._saved_explicit("docker")
        before = copy.deepcopy(saved)
        C.apply_runtime_environment(saved, "windows")
        self.assertEqual(saved, before)

    def test_normalize_alone_does_not_rewrite_explicit_derived_keys(self):
        # Guards against a future "derive unconditionally" regression: a plain
        # normalize() of a saved config must leave advanced overrides intact.
        saved = self._saved_explicit("docker")
        saved["mode"] = "direct"
        self.assertEqual(C.normalize(saved)["mode"], "direct")


# --------------------------------------------------------------------------
class RuntimeComparisonGating(unittest.TestCase):
    """A4: variant gating, schema version, baseline-less verdict."""

    def test_schema_version_is_2(self):
        self.assertEqual(RC.SCHEMA_VERSION, 2)
        self.assertEqual(RC.SCHEMA_VERSION, RESULT_SCHEMA_VERSION)

    def test_default_options_match_the_windows_default(self):
        self.assertIn("include_windows", RC.DEFAULT_OPTIONS)
        self.assertFalse(RC.DEFAULT_OPTIONS["include_container"])
        self.assertTrue(RC.DEFAULT_OPTIONS["include_windows"])

    def test_detect_tools_windows_never_resolves_or_calls_docker(self):
        with mock.patch.object(RC, "_call",
                               return_value=(1, "", "missing", 1.0)) as call, \
             mock.patch.object(RC, "resolve_container",
                               side_effect=AssertionError("must not resolve")):
            tools = RC.detect_tools({}, {"include_container": False})
        self.assertEqual(tools["container"]["name"], "")
        self.assertFalse(tools["container"]["available"])
        docker_calls = [c for c in call.call_args_list
                        if c.args and c.args[0]
                        and str(c.args[0][0]).lower() == "docker"]
        self.assertEqual(docker_calls, [],
                         "Windows mode must not issue any docker exec/cp")

    def test_variants_windows_has_no_docker_rows_and_no_docker_calls(self):
        opts = dict(RC.DEFAULT_OPTIONS)
        opts.update({"include_windows": True, "include_container": False,
                     "include_references": True, "include_native": True})
        with mock.patch.object(RC, "_call",
                               return_value=(0, "", "", 1.0)) as call:
            variants = RC._variants("prime", "prime", "/tmp",
                                    fake_tools(), opts, {}, [], 60)
        self.assertFalse([v for v in variants if v["source"] == "docker"])
        self.assertFalse([v for v in variants
                          if v["key"].startswith("spid3r_docker_")])
        self.assertTrue([v for v in variants if v["source"] == "windows"])
        self.assertEqual(call.call_count, 0,
                         "_variants in Windows mode must not call docker cp")

    def test_variants_docker_adds_tagged_rows_and_keeps_host_baselines(self):
        opts = dict(RC.DEFAULT_OPTIONS)
        opts.update({"include_windows": True, "include_container": True,
                     "include_references": True, "include_native": True})
        with mock.patch.object(RC, "_call", return_value=(0, "", "", 1.0)):
            variants = RC._variants("prime", "prime", "/tmp",
                                    fake_tools(), opts, {}, [], 60)
        docker = [v for v in variants if v["source"] == "docker"]
        hosts = [v for v in variants if v["source"] == "windows"]
        self.assertEqual(sorted(v["key"] for v in docker),
                         ["spid3r_docker_tw", "spid3r_docker_vm"])
        for v in docker:
            self.assertIn("Docker:", v["label"])
            self.assertIn("spid3rweb-app", v["label"])
            self.assertTrue(any(str(x).lower() == "docker"
                                for x in v["cmd"]))
        self.assertTrue(hosts, "Docker is additive; host baselines must remain")

    def test_variants_include_windows_false_yields_only_docker_rows(self):
        opts = dict(RC.DEFAULT_OPTIONS)
        opts.update({"include_windows": False, "include_container": True})
        with mock.patch.object(RC, "_call", return_value=(0, "", "", 1.0)):
            variants = RC._variants("prime", "prime", "/tmp",
                                    fake_tools(), opts, {}, [], 60)
        self.assertTrue(variants)
        self.assertTrue(all(v["source"] == "docker" for v in variants))

    def test_build_artifacts_skipped_when_windows_disabled(self):
        with mock.patch.object(RC, "_call",
                               side_effect=AssertionError("must not build")):
            built = RC._build_artifacts("prime", "prime", "/tmp",
                                        fake_tools(),
                                        {"include_windows": False}, [], 60)
        self.assertEqual(built, {})

    def test_run_embeds_switch_metadata_in_config_meta(self):
        td = tempfile.mkdtemp()
        try:
            cfg = C.normalize({"runtime_environment": "windows",
                               "runtime_comparison": {"repeats": 1}})
            with mock.patch.object(RC, "detect_tools",
                                   return_value=fake_tools()), \
                 mock.patch.object(RC, "_call",
                                   return_value=(0, "RESULT 5133 121013308\n"
                                                 "TIME_US 1000\n", "", 1.0)):
                data = RC.run(cfg, td)
            cm = data["config_meta"]
            self.assertEqual(cm["runtime_environment"], "windows")
            self.assertFalse(cm["include_container"])
            self.assertTrue(cm["include_windows"])
            self.assertEqual(data["environment"]["comparison_sources"],
                             "Windows only")
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_summarize_embeds_runtime_environment_and_sources(self):
        data = {"status": "ok", "schema_version": 2,
                "environment": {"runtime_environment":
                                    "Windows - installed runtimes",
                                "comparison_sources": "Windows only"},
                "summary": {"verdict": "v"}, "warnings": [], "artifacts": {}}
        s = RC.summarize(data)
        self.assertEqual(s["schema_version"], 2)
        self.assertEqual(s["runtime_environment"],
                         "Windows - installed runtimes")
        self.assertEqual(s["comparison_sources"], "Windows only")

    def test_baseline_less_verdict_does_not_claim_spid3r_beat_baseline(self):
        # Baselines exist as rows but no PHP/Python timing could be paired, so
        # there are zero ratios. The old code fell through to "matched or
        # beat a baseline", which is an unsupported claim.
        data = {"workloads": {"prime": {"verified": True, "rows": [
            {"key": "spid3r_tw", "runtime": "Spid3r", "group": "spid3r",
             "compute_ms": 10.0}]}}}
        s = RC.summarize_data(data)
        self.assertNotIn("matched or beat", s["verdict"])
        self.assertIn("unavailable", s["verdict"].lower())
        self.assertIsNone(s["slowdown_range"]["min"])
        self.assertIsNone(s["slowdown_range"]["max"])

    def test_baseline_less_verdict_with_no_verified_workloads(self):
        s = RC.summarize_data({"workloads": {}})
        self.assertNotIn("matched or beat", s["verdict"])
        self.assertEqual(s["workloads_verified"], 0)

    def test_console_summary_reports_runtime(self):
        data = {"status": "ok",
                "environment": {"runtime_environment": "Windows host"},
                "summary": {"verdict": "v", "slowdown": {}},
                "workloads": {}, "warnings": []}
        buf = io.StringIO()
        RC.console_summary(data, stream=buf)
        self.assertIn("runtime=Windows host", buf.getvalue())


# --------------------------------------------------------------------------
class V1ProvenanceGuard(unittest.TestCase):
    """Task #6: one v1 guard feeds every consumer; no false 'Windows host'."""

    def test_row_source_is_the_single_source_of_truth(self):
        cases = [
            ({"source": "docker"}, True, "docker"),
            ({"source": "windows"}, True, "windows"),
            ({"key": "spid3r_docker_tw"}, False, "docker"),
            ({"runtime": "Spid3r (Docker)"}, False, "docker"),
            ({"key": "php"}, True, "windows"),
            ({"key": "php"}, False, "unavailable"),
        ]
        for row, recorded, want in cases:
            with self.subTest(row=row):
                self.assertEqual(
                    RC.row_source(row, provenance_recorded=recorded), want)

    def assert_honest_v1_html(self, html):
        self.assertIn("Docker container", html,
                      "v1 Docker rows must read 'Docker container' in Where")
        self.assertNotIn("Windows host", html,
                         "v1 Docker rows must not be labelled 'Windows host'")
        self.assertNotIn("No Docker container was contacted", html,
                         "false negative claim must be gated on schema>=2")
        self.assertIn("Docker rows run in a different environment", html,
                      "the different-environment caveat must be present")

    def test_synthetic_v1_archive_renders_honestly(self):
        self.assert_honest_v1_html(render_rc(synthetic_v1_archive()))

    def test_real_v1_archives_render_honestly(self):
        docs = archived_v1_rc_docs()
        if not docs:
            self.skipTest("no schema-1 runtime_comparison.json artifacts")
        for path, doc in docs:
            with self.subTest(archive=os.path.basename(os.path.dirname(path))):
                self.assertEqual(int(doc.get("schema_version") or 1), 1)
                self.assertNotIn("include_container",
                                 doc.get("config_meta") or {})
                rows = [r for w in (doc.get("workloads") or {}).values()
                        for r in (w.get("rows") or [])]
                docker_keys = [r["key"] for r in rows
                               if str(r.get("key", "")).startswith(
                                   "spid3r_docker_")]
                self.assertTrue(docker_keys,
                                "archive should contain Docker rows")
                self.assert_honest_v1_html(render_rc(doc))

    def test_v2_windows_only_has_negative_note_but_no_caveat(self):
        html = render_rc(v2_rc_doc(include_container=False))
        self.assertIn("No Docker container was contacted", html)
        self.assertNotIn("Docker rows run in a different environment", html)
        self.assertIn("Windows host", html)
        self.assertIn("schema v2", html)

    def test_v2_docker_rows_have_caveat_but_no_negative_note(self):
        html = render_rc(v2_rc_doc(include_container=True))
        self.assertNotIn("No Docker container was contacted", html)
        self.assertIn("Docker rows run in a different environment", html)
        self.assertIn("Docker container", html)
        self.assertIn(">Docker</span>", html)

    def test_report_section_11_labels_v1_provenance_unknown(self):
        v1 = {"schema_version": 1, "status": "ok", "verdict": "v",
              "slowdown": {}, "slowdown_range": {}, "warnings": [],
              "artifacts": {}}
        html = report._runtime_section(v1, "run-v1")
        self.assertIn("Provenance not recorded", html)
        self.assertNotIn("measured sources:", html)

    def test_report_section_11_labels_v2_sources(self):
        v2 = {"schema_version": 2, "status": "ok", "verdict": "v",
              "runtime_environment": "Windows - installed runtimes",
              "comparison_sources": "Windows only",
              "slowdown": {}, "slowdown_range": {}, "warnings": [],
              "artifacts": {}}
        html = report._runtime_section(v2, "run-v2")
        self.assertNotIn("Provenance not recorded", html)
        self.assertIn("Runtime environment:", html)
        self.assertIn("measured sources: Windows only", html)


# --------------------------------------------------------------------------
class CliFlagAndCoherence(unittest.TestCase):
    """A5 / C3 / B2: --runtime-env flag, incoherent-override rejection."""

    def test_parser_exposes_exclusive_runtime_flag(self):
        a = cli.build_parser().parse_args(["--runtime-env", "docker"])
        self.assertEqual(a.runtime_env, "docker")
        a = cli.build_parser().parse_args(["--runtime-env", "windows"])
        self.assertEqual(a.runtime_env, "windows")
        self.assertIsNone(cli.build_parser().parse_args([]).runtime_env)

    def test_parser_rejects_bogus_value_with_exit_code_2(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                cli.build_parser().parse_args(["--runtime-env", "nonsense"])
        self.assertEqual(cm.exception.code, 2)

    @staticmethod
    def _write_cfg(raw):
        td = tempfile.mkdtemp()
        path = os.path.join(td, "cfg.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(raw, f)
        return td, path

    def _derive_via_cli(self, saved, flag_value):
        td, path = self._write_cfg(saved)
        captured = {}

        def fake_preflight(cfg):
            captured.update(cfg)
            return {"checks": [], "container": "", "container_method": "fake"}

        argv = ["--config", path, "--runtime-env", flag_value,
                "--preflight-only"]
        try:
            with mock.patch.object(runner, "preflight",
                                   side_effect=fake_preflight), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(argv)
        finally:
            shutil.rmtree(td, ignore_errors=True)
        return rc, captured

    def test_c3_runtime_env_docker_rederives_from_saved_windows(self):
        saved = {"target_url": "http://localhost:8090/",
                 "runtime_environment": "windows", "mode": "direct",
                 "benchmark_mode": "live_blackbox",
                 "runtime_comparison": {"include_container": False,
                                        "include_windows": True}}
        rc, cfg = self._derive_via_cli(saved, "docker")
        self.assertEqual(rc, 0)
        assert_preset(self, cfg, "docker", EXPECTED_DOCKER)

    def test_c3_runtime_env_windows_restores_canonical_from_saved_docker(self):
        saved = {"target_url": "http://localhost:8090/",
                 "runtime_environment": "docker", "mode": "auto",
                 "benchmark_mode": "auto",
                 "runtime_comparison": {"include_container": True,
                                        "include_windows": True}}
        rc, cfg = self._derive_via_cli(saved, "windows")
        self.assertEqual(rc, 0)
        assert_preset(self, cfg, "windows", EXPECTED_WINDOWS)

    def test_same_value_flag_does_not_discard_explicit_overrides(self):
        # A no-op selector must not clobber an advanced --mode override.
        saved = {"target_url": "http://localhost:8090/",
                 "runtime_environment": "docker", "mode": "direct",
                 "benchmark_mode": "live_blackbox"}
        rc, cfg = self._derive_via_cli(saved, "docker")
        self.assertEqual(rc, 0)
        self.assertEqual(cfg["mode"], "direct")
        self.assertEqual(cfg["benchmark_mode"], "live_blackbox")

    def test_b2_windows_rejects_incoherent_server_instrumentation(self):
        saved = {"target_url": "http://localhost:8090/",
                 "runtime_environment": "windows"}
        for extra in (["--mode", "auto"], ["--mode", "docker"],
                      ["--benchmark-mode", "docker_lab"],
                      ["--benchmark-mode", "auto"],
                      ["--benchmark-mode", "instrumented_live"]):
            with self.subTest(flags=extra):
                td, path = self._write_cfg(saved)
                try:
                    with mock.patch.object(runner, "preflight",
                                           return_value={"checks": []}), \
                         contextlib.redirect_stderr(io.StringIO()), \
                         contextlib.redirect_stdout(io.StringIO()):
                        rc = cli.main(["--config", path] + extra +
                                      ["--preflight-only"])
                finally:
                    shutil.rmtree(td, ignore_errors=True)
                self.assertEqual(rc, 2,
                                 "Windows client-only must reject %s" % extra)

    def test_windows_accepts_coherent_overrides(self):
        saved = {"target_url": "http://localhost:8090/",
                 "runtime_environment": "windows"}
        td, path = self._write_cfg(saved)
        captured = {}

        def fake_preflight(cfg):
            captured.update(cfg)
            return {"checks": []}
        try:
            with mock.patch.object(runner, "preflight",
                                   side_effect=fake_preflight), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = cli.main(["--config", path, "--mode", "direct",
                               "--benchmark-mode", "live_blackbox",
                               "--preflight-only"])
        finally:
            shutil.rmtree(td, ignore_errors=True)
        self.assertEqual(rc, 0)
        self.assertEqual(captured["mode"], "direct")
        self.assertEqual(captured["benchmark_mode"], "live_blackbox")


# --------------------------------------------------------------------------
class ApiConfigRoundTrip(unittest.TestCase):
    """A5 / C1 / C2 / C5: real /api/config POST over an in-process server.

    The transition guard must re-derive ONLY when runtime_environment changes,
    and must survive a full config clone carrying four STALE derived keys
    (which is exactly what the UI's collect() posts).
    """

    @classmethod
    def setUpClass(cls):
        import server
        cls.server = server
        cls._td = tempfile.mkdtemp()
        cls._orig_config_path = server.CONFIG_PATH
        cls._orig_state_config = server.STATE["config"]
        cls._orig_preflight = server.STATE.get("preflight")
        server.CONFIG_PATH = os.path.join(cls._td, "config.json")
        cls._patchers = [
            mock.patch.object(server.Handler, "log_message",
                              new=lambda *a, **k: None),
            mock.patch.object(server.dp, "list_containers",
                              return_value=([], None)),
            mock.patch.object(server.dp, "docker_available",
                              return_value={"available": False,
                                            "server_version": ""}),
        ]
        for p in cls._patchers:
            p.start()
        cls._httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls._httpd.daemon_threads = True
        cls._port = cls._httpd.server_address[1]
        cls._thread = threading.Thread(target=cls._httpd.serve_forever,
                                       daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._httpd.shutdown()
        cls._httpd.server_close()
        cls._thread.join(timeout=3)
        for p in reversed(cls._patchers):
            p.stop()
        cls.server.CONFIG_PATH = cls._orig_config_path
        cls.server.STATE["config"] = cls._orig_state_config
        cls.server.STATE["preflight"] = cls._orig_preflight
        shutil.rmtree(cls._td, ignore_errors=True)

    def setUp(self):
        self.server.STATE["config"] = C.normalize(
            {"runtime_environment": "windows"})
        self.server.STATE["preflight"] = None

    def _post(self, obj):
        req = urlreq.Request(
            "http://127.0.0.1:%d/api/config" % self._port,
            data=json.dumps(obj).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urlreq.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get(self, path):
        with urlreq.urlopen("http://127.0.0.1:%d%s" % (self._port, path),
                            timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_c1_switch_only_post_rewrites_all_four_derived_keys(self):
        r = self._post({"config": {"runtime_environment": "docker"}})
        self.assertTrue(r["ok"])
        assert_preset(self, r["config"], "docker", EXPECTED_DOCKER)

    def test_c1_full_clone_with_stale_derived_keys_still_rederives(self):
        # Simulates the UI: collect() clones CFG and posts ALL keys including
        # stale mode/benchmark_mode/include_* from the OLD environment.
        stale = C.public_config(self.server.STATE["config"])
        stale["runtime_environment"] = "docker"
        r = self._post({"config": stale})
        assert_preset(self, r["config"], "docker", EXPECTED_DOCKER)

    def test_c1_windows_docker_windows_restores_canonical(self):
        r1 = self._post({"config": {"runtime_environment": "docker"}})
        assert_preset(self, r1["config"], "docker", EXPECTED_DOCKER)
        r2 = self._post({"config": {"runtime_environment": "windows"}})
        assert_preset(self, r2["config"], "windows", EXPECTED_WINDOWS)

    def test_c2_unrelated_post_does_not_rederive(self):
        # An explicit advanced override must survive a POST to an unrelated
        # field. This is what protects --mode / --benchmark-mode.
        self.server.STATE["config"] = C.normalize({
            "runtime_environment": "docker", "mode": "direct",
            "benchmark_mode": "live_blackbox"})
        r = self._post({"config": {"max_concurrency": 55}})
        self.assertEqual(r["config"]["mode"], "direct")
        self.assertEqual(r["config"]["benchmark_mode"], "live_blackbox")
        self.assertEqual(r["config"]["max_concurrency"], 55)
        self.assertEqual(r["config"]["runtime_environment"], "docker")

    def test_c2_unrelated_post_does_not_rederive_windows_override(self):
        self.server.STATE["config"] = C.normalize({
            "runtime_environment": "windows", "mode": "auto"})
        r = self._post({"config": {"max_concurrency": 42}})
        self.assertEqual(r["config"]["mode"], "auto")
        self.assertEqual(r["config"]["max_concurrency"], 42)

    def test_api_rejects_invalid_explicit_environment(self):
        with self.assertRaises(HTTPError) as cm:
            self._post({"config": {"runtime_environment": "nonsense"}})
        self.assertEqual(cm.exception.code, 400)
        # A rejected POST must not corrupt the saved state.
        self.assertEqual(self._get("/api/config")["config"]
                         ["runtime_environment"], "windows")

    def test_c5_response_ondisk_and_bootstrap_are_coherent(self):
        r = self._post({"config": {"runtime_environment": "docker"}})
        assert_preset(self, r["config"], "docker", EXPECTED_DOCKER)
        # GET /api/config reflects the saved value.
        self.assertEqual(self._get("/api/config")["config"]
                         ["runtime_environment"], "docker")
        # On-disk config.json has coherent derived keys.
        with open(self.server.CONFIG_PATH, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        assert_preset(self, on_disk, "docker", EXPECTED_DOCKER)
        # /api/bootstrap carries the saved config + switch metadata.
        b = self._get("/api/bootstrap")
        self.assertEqual(b["config"]["runtime_environment"], "docker")
        self.assertEqual(b["runtime_environment_default"], "windows")
        envs = {e["value"]: e for e in b["runtime_environments"]}
        self.assertEqual(set(envs), {"windows", "docker"})
        self.assertTrue(envs["windows"]["default"])
        self.assertTrue(envs["windows"]["client_only"])
        self.assertEqual(envs["docker"]["mode"], "auto")
        self.assertFalse(envs["docker"]["include_container"] is False)

    def test_bootstrap_defaults_everything_to_windows(self):
        self.setUp()
        b = self._get("/api/bootstrap")
        assert_preset(self, b["config"], "windows", EXPECTED_WINDOWS)
        self.assertEqual(b["runtime_environment_default"], "windows")


# --------------------------------------------------------------------------
class ReportHonestyAndClientOnlyInvariant(unittest.TestCase):
    """A6 / B1-B4: client-only honesty, no evidence-vs-mode contradictions."""

    def _archived_with_windows_config(self):
        paths = archived_results()
        if not paths:
            self.skipTest("no archived v1 results (run archive_legacy.py first)")
        with open(paths[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        ensure_current(data)
        data["config"]["runtime_environment"] = "windows"
        data["config"]["benchmark_mode"] = "live_blackbox"
        return data

    def _build_report(self, data):
        td = tempfile.mkdtemp()
        try:
            out = os.path.join(td, "report.html")
            report.build(data, out)
            with open(out, "r", encoding="utf-8") as f:
                return f.read()
        finally:
            shutil.rmtree(td, ignore_errors=True)

    def test_a6_windows_report_has_client_only_note_and_config_row(self):
        html = self._build_report(self._archived_with_windows_config())
        self.assertIn("client-only run", html)
        self.assertIn("No server CPU", html)
        self.assertIn("must never be ranked against a Docker lab run", html)
        self.assertIn("Runtime environment / benchmark mode", html)
        self.assertIn("windows / live_blackbox", html)

    def test_a6_report_is_self_contained_no_external_resources(self):
        html = self._build_report(self._archived_with_windows_config())
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("src=\"http", html)
        self.assertNotIn("href=\"http", html)
        self.assertIn("no external resources", html)

    def test_a7_report_h2_counts_unchanged(self):
        data = self._archived_with_windows_config()
        base = self._build_report(data)
        self.assertEqual(base.count("<h2>"), 10)
        data["runtime_comparison"] = {
            "schema_version": 2, "status": "ok", "verdict": "v",
            "runtime_environment": "Windows - installed runtimes",
            "comparison_sources": "Windows only", "slowdown": {},
            "slowdown_range": {}, "warnings": [], "artifacts": {}}
        with_rc = self._build_report(data)
        self.assertEqual(with_rc.count("<h2>"), 11,
                         "section 11 must add exactly one <h2>")

    def test_b4_ensure_current_pins_client_only_despite_leaked_evidence(self):
        # The contradictory state: declared live_blackbox, but a leaked
        # container name + cgroup samples are present. Visibility must be
        # pinned to the declared mode, NOT inferred from evidence.
        d = {"meta": {"container": "spid3rweb-app",
                      "target_url": "http://localhost/"},
             "container": "spid3rweb-app",
             "resources": {"cgroup_samples": 10, "stats_samples": 5,
                           "anon_peak_mib": 50.0},
             "series": {"cgroup_series": [{"t": 1, "anon_bytes": 100}]},
             "summary": {}}
        ensure_current(d, mode="live_blackbox")
        self.assertEqual(d["benchmark_mode"], "live_blackbox")
        self.assertEqual(d["visibility"], "client_only")
        self.assertEqual(
            d["per_metric_visibility"]["metrics.resources.*"], "unavailable")
        self.assertEqual(
            d["per_metric_visibility"]["series.cgroup_series"], "unavailable")
        self.assertNotEqual(d["visibility"], "full_server")

    def test_b4_labels_are_mutually_consistent_for_clean_client_only(self):
        d = {"meta": {"target_url": "http://localhost/"},
             "resources": {}, "summary": {}, "series": {}}
        ensure_current(d, mode="live_blackbox")
        self.assertEqual(d["benchmark_mode"], "live_blackbox")
        self.assertEqual(d["visibility"], "client_only")
        self.assertEqual(
            d["per_metric_visibility"]["metrics.resources.*"], "unavailable")

    def test_b4_client_only_run_is_not_ranked_against_docker_lab(self):
        client = {"schema_version": 2,
                  "benchmark_mode": "live_blackbox",
                  "visibility": "client_only",
                  "meta": {"workload_class": "http_loadtest"}}
        lab = {"schema_version": 2, "benchmark_mode": "docker_lab",
               "visibility": "full_server",
               "meta": {"workload_class": "http_loadtest"}}
        ok, _ = can_compare(client, lab)
        self.assertFalse(ok)
        ok2, _ = can_compare(client, dict(client))
        self.assertFalse(ok2, "client-only runs have no server metrics")

    @staticmethod
    def _grading_result(env, leaked):
        return {
            "config": {"runtime_environment": env, "mode": "direct",
                       "benchmark_mode": "live_blackbox",
                       "runtime_comparison": {}, "phases": [], "slo": {},
                       "guardrails": {}},
            "slo_targets": {"max_error_pct": 0.1, "max_p95_ms": 500,
                            "max_p99_ms": 2000, "min_rps": 0,
                            "max_memory_growth_mib": 256,
                            "max_retained_pct": 120,
                            "min_health_success_pct": 99},
            "summary": {"PRIMARY_successful_200_only":
                        {"p95": 10, "p50": 5, "p99": 20, "max": 30, "n": 100},
                        "s1_error_pct": 0.0, "s1_errors": 0,
                        "total_requests": 100, "connect_errors": 0,
                        "overall_rps": 10, "status_counts": {},
                        "duration_s": 10},
            "resources": {
                "cgroup_samples": 999 if leaked else 0,
                "active_slope_mib_per_s": 50.0 if leaked else None,
                "active_window_s": [0, 100] if leaked else None,
                "active_points": 999 if leaked else 0,
                "retained_floor_bins": list(range(20)) if leaked else [],
                "cooldown_retained_pct": 200.0 if leaked else None,
                "anon_net_change_mib": 999.0 if leaked else None,
                "anon_peak_mib": 999.0 if leaked else None,
                "health_success_pct": 100.0, "health_probes": 100,
                "cpu_peak_pct": 10},
            "abort": {"flag": False},
            "meta": {"cpu_count": 4, "container": "LEAKED",
                     "target_url": "http://localhost/", "profile": "smoke",
                     "schedule_s": 33, "peak_concurrency": 10},
            "inspect_before": {}, "inspect_after": {},
            "executed_phases": [], "adjudication": {},
        }

    def test_b1_s5_s6_are_na_in_windows_mode_even_with_cgroup_evidence(self):
        for leaked in (False, True):
            with self.subTest(leaked_cgroup_evidence=leaked):
                v = grading.grade(self._grading_result("windows", leaked))
                rows = {x["id"]: x["grade"] for x in v["slo"]}
                self.assertEqual(rows.get("S5"), "N/A")
                self.assertEqual(rows.get("S6"), "N/A")

    # ---- B1 / B3: runner hard collector gate ----------------------------
    @staticmethod
    def _run_benchmark_with_fake(cfg, pf, root):
        fake_results = {
            "config": dict(cfg), "summary": {}, "meta": {},
            "abort": {"flag": False, "at_s": 0}, "adjudication": {},
            "executed_phases": [], "resources": {}, "series": {}}
        captured = {}

        class FakeBench:
            generator_pinned = False
            samplers = None

            def run(self):
                return dict(fake_results)

        def make_bench(*a, **k):
            captured.update(k)
            return FakeBench()

        adjudication = {"verdict": "GO", "health_score": 100,
                        "findings": [], "executive_summary": [],
                        "overall_grade": "PASS", "adjudication_note": ""}
        with mock.patch.object(runner, "Benchmark", side_effect=make_bench), \
             mock.patch.object(runner.grading, "grade",
                               return_value=adjudication), \
             mock.patch.object(runner.report, "build",
                               return_value=os.path.join(root, "rep.html")), \
             mock.patch.object(runner, "environment_info", return_value={}):
            runner.run_benchmark(cfg, root, log=None, preflight_result=pf)
        return captured

    def test_b3_cached_docker_preflight_cannot_leak_cgroups_into_windows(self):
        cfg = C.normalize({"runtime_environment": "windows",
                           "container": "spid3rweb-app",
                           "mysql_container": "spid3rweb-db",
                           "target_url": "http://localhost:8090/",
                           "runtime_comparison": {"enabled": False}})
        pf = {"target_url": "http://localhost:8090/",
              "runtime_environment": "windows",
              "container": "spid3rweb-app", "checks": [], "cgroup": {}}
        with tempfile.TemporaryDirectory() as root:
            captured = self._run_benchmark_with_fake(cfg, pf, root)
            self.assertEqual(captured.get("container"), "",
                             "Windows mode must pass no container to samplers")
            self.assertEqual(captured.get("mysql_container"), "")
            preflight_path = glob.glob(
                os.path.join(root, "runs", "*", "preflight.json"))[0]
            with open(preflight_path, "r", encoding="utf-8") as f:
                written_pf = json.load(f)
            self.assertEqual(written_pf.get("container"), "")
            results_path = glob.glob(
                os.path.join(root, "runs", "*", "results.json"))[0]
            with open(results_path, "r", encoding="utf-8") as f:
                res = json.load(f)
        self.assertEqual(res["benchmark_mode"], "live_blackbox")
        self.assertEqual(res["visibility"], "client_only")
        self.assertEqual(
            res["per_metric_visibility"]["metrics.resources.*"], "unavailable")
        self.assertFalse((res.get("series") or {}).get("cgroup_series"))
        self.assertFalse(res.get("resources"))

    def test_b1_docker_mode_keeps_the_resolved_container(self):
        cfg = C.normalize({"runtime_environment": "docker",
                           "container": "spid3rweb-app",
                           "target_url": "http://localhost:8090/",
                           "runtime_comparison": {"enabled": False}})
        pf = {"target_url": "http://localhost:8090/",
              "runtime_environment": "docker",
              "container": "spid3rweb-app", "checks": [], "cgroup": {}}
        with tempfile.TemporaryDirectory() as root:
            captured = self._run_benchmark_with_fake(cfg, pf, root)
        self.assertEqual(captured.get("container"), "spid3rweb-app")

    def test_b3_preflight_cache_is_keyed_on_runtime_environment(self):
        # A cached Docker-lab preflight must be discarded after a switch to
        # Windows, otherwise its resolved container could be reused.
        cfg = C.normalize({"runtime_environment": "windows",
                           "container": "spid3rweb-app",
                           "target_url": "http://localhost:8090/",
                           "runtime_comparison": {"enabled": False}})
        stale = {"target_url": "http://localhost:8090/",
                 "runtime_environment": "docker",
                 "container": "spid3rweb-app", "checks": [], "cgroup": {}}
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(runner, "preflight",
                                   return_value={"checks": [],
                                                 "container": "",
                                                 "container_method": "fresh"}) \
                    as fresh:
                self._run_benchmark_with_fake(cfg, stale, root)
            self.assertTrue(fresh.called,
                            "stale-env preflight must trigger a fresh preflight")


# --------------------------------------------------------------------------
class UiStaticContract(unittest.TestCase):
    """A5: static markup checks for the exclusive selector (no DOM needed)."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "ui", "index.html"), "r",
                  encoding="utf-8") as f:
            cls.html = f.read()

    def _radio(self, radio_id):
        m = re.search(r'<input[^>]*id="%s"[^>]*>' % radio_id,
                      self.html, re.S)
        self.assertIsNotNone(m, "missing radio #%s" % radio_id)
        return m.group(0)

    def test_native_radios_share_one_exclusive_name(self):
        radios = re.findall(r'<input[^>]*type="radio"[^>]*>',
                            self.html, re.S)
        self.assertEqual(len(radios), 2)
        self.assertTrue(all('name="runtime_environment"' in r
                           for r in radios))
        self.assertEqual(self.html.count('name="runtime_environment"'), 2)

    def test_fieldset_legend_and_aria_describedby_are_present(self):
        self.assertRegex(self.html, r'<fieldset[^>]*aria-describedby=')
        self.assertIn("<legend>Runtime environment</legend>", self.html)
        self.assertIn('id="runtime-disclosure"', self.html)
        self.assertIn('aria-describedby="runtime-disclosure"', self.html)

    def test_windows_is_the_default_and_exactly_one_is_checked(self):
        win, dock = self._radio("rt-windows"), self._radio("rt-docker")
        self.assertIn('value="windows"', win)
        self.assertIn('value="docker"', dock)
        checked = sum("checked" in r for r in (win, dock))
        self.assertEqual(checked, 1, "exactly one radio must be checked")
        self.assertIn("checked", win, "Windows must be the default selection")
        self.assertNotIn("checked", dock)

    def test_mode_becomes_readonly_derived_display(self):
        m = re.search(r'<input[^>]*id="mode"[^>]*>', self.html, re.S)
        self.assertIsNotNone(m)
        self.assertIn("readonly", m.group(0))
        self.assertIn('type="text"', m.group(0))
        self.assertIn("derived", self.html)

    def test_container_select_is_present_not_removed(self):
        # Reviewer: disable, do not hide -- the pinned container must stay
        # visible so its value is not silently lost in Windows mode.
        self.assertRegex(self.html, r'<select id="container">')
        self.assertIn('id="container-field"', self.html)
        self.assertIn("client-only", self.html)

    def test_what_gets_measured_mentions_http_metrics(self):
        self.assertIn("What gets measured", self.html)
        self.assertIn("throughput", self.html.lower())


if __name__ == "__main__":
    unittest.main()
