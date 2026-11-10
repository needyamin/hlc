"""Stop and remove a benchmark target container (dry-run by default).

    python scripts/lab_down.py python-fastapi-hello
    python scripts/lab_down.py python-fastapi-hello --apply
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
    ap.add_argument("--apply", action="store_true",
                    help="actually execute (default: dry run)")
    a = ap.parse_args()

    name = a.site
    path = a.site if os.path.isfile(a.site) else targetspec.find_spec(a.site)
    if path:
        spec, err = targetspec.load_spec(path)
        if err:
            print("Invalid spec: %s" % err, file=sys.stderr)
            return 2
        name = spec.get("container") or spec["site_id"]

    cmd = "docker rm -f %s" % name
    print("$ %s" % cmd)
    if not a.apply:
        print("DRY RUN - nothing executed. Add --apply.")
        return 0
    return subprocess.call(cmd, shell=True, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
