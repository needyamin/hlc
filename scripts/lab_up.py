"""Start ONE benchmark target in Docker with pinned resources.

Dry-run by default: prints the exact commands. Pass --apply to execute them,
--build to also run the spec's build_cmd first.

    python scripts/lab_up.py python-fastapi-hello
    python scripts/lab_up.py node-express-hello --build --apply
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from heavyload import targetspec  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("site", help="site_id or path to a spec JSON")
    ap.add_argument("--build", action="store_true", help="run build_cmd")
    ap.add_argument("--apply", action="store_true",
                    help="actually execute (default: dry run)")
    a = ap.parse_args()

    path = a.site if os.path.isfile(a.site) else targetspec.find_spec(a.site)
    if not path:
        print("Spec not found: %s" % a.site, file=sys.stderr)
        print("Available: %s" % ", ".join(
            os.path.basename(p) for p in targetspec.list_specs()),
            file=sys.stderr)
        return 2
    spec, err = targetspec.load_spec(path)
    if err:
        print("Invalid spec: %s" % err, file=sys.stderr)
        return 2

    cmds = []
    if a.build and spec.get("build_cmd"):
        cmds.append(("build", spec["build_cmd"]))
    cmds.append(("run", " ".join(targetspec.docker_run_args(spec))))

    print("Site:      %s (%s / %s)" % (spec["site_id"], spec["language"],
                                       spec["framework"]))
    print("Image:     %s" % spec["image"])
    print("Limits:    cpus=%s cpuset=%r mem=%dMB" % (
        spec["resource_limits"]["cpus"],
        spec["resource_limits"]["cpuset_cpus"],
        spec["resource_limits"]["mem_limit_mb"]))
    print("URL:       http://localhost:%d%s" % (spec["host_port"],
                                                spec["health_path"]))
    for label, cmd in cmds:
        print("%-6s  %s" % (label + ":", cmd))
    print()
    print("Fairness reminders:")
    print("  * Docker Desktop must be running.")
    print("  * Pin the load generator to DIFFERENT cores than "
          "resource_limits.cpuset_cpus (config.fairness.cpuset_generator).")
    print("  * Every language must serve the same route mix and identical "
          "payloads (expected_payload_sha).")
    print("  * Run at least repeat_count=%d times and use median + variance."
          % spec["repeat_count"])

    if not a.apply:
        print("\nDRY RUN - nothing executed. Add --apply to start the container.")
        return 0
    for label, cmd in cmds:
        print("\n$ %s" % cmd)
        rc = subprocess.call(cmd, shell=True, cwd=ROOT)
        if rc != 0:
            print("%s failed (exit %d)" % (label, rc), file=sys.stderr)
            return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
