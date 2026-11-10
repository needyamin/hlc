"""Reversible cleanup helper for Heavy Load Checker.

Safety design (this workspace lives on the Desktop):
  * ``backup``  COPIES legacy sources + generated runs into ``_archive/`` and
    verifies every file by SHA-256 before anything is touched.
  * ``verify``  re-checks the backup against the originals.
  * ``manifest`` lists every path that a later cleanup would affect.
  * ``recycle`` sends paths to the Windows Recycle Bin (NOT permanent deletion),
    in batches of at most 10, verifying after each batch. Requires --confirm.

Nothing is ever removed with ``rm -rf`` / ``shutil.rmtree``.

Usage:
  python scripts/archive_legacy.py manifest
  python scripts/archive_legacy.py backup
  python scripts/archive_legacy.py verify
  python scripts/archive_legacy.py recycle --confirm <path> [<path> ...]
  python scripts/archive_legacy.py recycle-caches --confirm
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import sys
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVE = os.path.join(ROOT, "_archive")
MANIFEST = os.path.join(ARCHIVE, "MANIFEST.json")

LEGACY_DIR = os.path.join(ROOT, "original_scripts")
RUNS_DIR = os.path.join(ROOT, "runs")
PREVIEW = os.path.join(ROOT, "ui_preview.png")

CACHE_DIRS = [
    os.path.join(ROOT, "__pycache__"),
    os.path.join(ROOT, "heavyload", "__pycache__"),
    os.path.join(ROOT, "original_scripts", "__pycache__"),
]


# ------------------------------------------------------------------ helpers --
def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def files_under(path):
    """Return [(relative_path, absolute_path)] for every file in a tree."""
    out = []
    for base, _dirs, names in os.walk(path):
        for name in sorted(names):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                out.append((os.path.relpath(p, path).replace("\\", "/"), p))
    return sorted(out)


def sources():
    """(label, source_dir, archive_destination) triples."""
    items = [("original_scripts", LEGACY_DIR,
              os.path.join(ARCHIVE, "original_scripts"))]
    if os.path.isdir(RUNS_DIR):
        for name in sorted(os.listdir(RUNS_DIR)):
            d = os.path.join(RUNS_DIR, name)
            if os.path.isdir(d):
                items.append(("runs/" + name, d,
                              os.path.join(ARCHIVE, "runs", name)))
    return [x for x in items if os.path.isdir(x[1])]


def affected_paths():
    out = [LEGACY_DIR, PREVIEW]
    if os.path.isdir(RUNS_DIR):
        out += [os.path.join(RUNS_DIR, n) for n in sorted(os.listdir(RUNS_DIR))
                if os.path.isdir(os.path.join(RUNS_DIR, n))]
    out += [p for p in CACHE_DIRS if os.path.isdir(p)]
    return out


# ------------------------------------------------------------------- backup --
def backup():
    os.makedirs(ARCHIVE, exist_ok=True)
    entries = []
    for label, src, dst in sources():
        if os.path.exists(dst):
            # Never silently overwrite an existing backup.
            print("SKIP (already archived): %s" % label)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree(src, dst)
        entries.append({
            "label": label,
            "source": src,
            "archive": dst,
            "files": {rel: {"size": os.path.getsize(p), "sha256": sha256(p)}
                      for rel, p in files_under(src)},
        })
        print("COPIED  %-34s -> %s" % (label, os.path.relpath(dst, ROOT)))

    # The UI screenshot is a doc asset, not legacy code: keep a copy too.
    preview_dst = os.path.join(ROOT, "docs", "screenshots", "ui-preview.png")
    if os.path.isfile(PREVIEW) and not os.path.isfile(preview_dst):
        shutil.copy2(PREVIEW, preview_dst)
        print("COPIED  ui_preview.png -> docs/screenshots/ui-preview.png")

    # Merge with any previous backup manifest so repeated runs stay additive
    # (e.g. after a selftest created new run directories).
    all_entries = []
    if os.path.isfile(MANIFEST):
        try:
            with open(MANIFEST, "r", encoding="utf-8") as f:
                all_entries = json.load(f).get("entries", [])
        except Exception:
            all_entries = []
    known = {e["label"] for e in all_entries}
    for e in entries:
        if e["label"] not in known:
            all_entries.append(e)
            known.add(e["label"])
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"root": ROOT, "entries": all_entries}, f, indent=2,
                  sort_keys=True)

    ok, bad = verify(verbose=False)
    print("\nBACKUP VERIFIED: %d files OK, %d mismatches" % (ok, bad))
    if bad:
        print("ABORT: backup is incomplete, do NOT recycle anything.",
              file=sys.stderr)
        return 1
    print("Backup manifest: %s" % MANIFEST)
    return 0


def verify(verbose=True):
    if not os.path.isfile(MANIFEST):
        if verbose:
            print("No manifest yet; run 'backup' first.")
        return 0, 1
    with open(MANIFEST, "r", encoding="utf-8") as f:
        data = json.load(f)
    ok = bad = 0
    for entry in data["entries"]:
        for rel, meta in entry["files"].items():
            dst = os.path.join(entry["archive"], *rel.split("/"))
            src = os.path.join(entry["source"], *rel.split("/"))
            good = (os.path.isfile(dst)
                    and os.path.getsize(dst) == meta["size"]
                    and sha256(dst) == meta["sha256"])
            if good:
                ok += 1
            else:
                bad += 1
                if verbose:
                    print("MISMATCH %s (source=%s)" % (dst, src))
            # Also make sure the original is still intact while we verify.
            if os.path.isfile(src) and sha256(src) != meta["sha256"]:
                print("WARNING: original changed since backup: %s" % src)
    if verbose:
        print("VERIFY: %d files OK, %d mismatches" % (ok, bad))
    return ok, bad


# ------------------------------------------------------- recycle bin (Win32) --
class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_uint16),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


FO_DELETE = 3
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040          # <- Recycle Bin, not permanent delete
FOF_NOERRORUI = 0x0400


def to_recycle_bin(paths):
    """Send <=10 existing paths to the Recycle Bin using SHFileOperationW."""
    if os.name != "nt":
        raise RuntimeError("Recycle Bin helper is Windows-only")
    if not paths:
        return []
    if len(paths) > 10:
        raise ValueError("max 10 paths per batch")

    for p in paths:
        if not os.path.exists(p):
            raise FileNotFoundError(p)
        if os.path.abspath(p).startswith(os.path.abspath(ARCHIVE)):
            raise ValueError("refusing to recycle the backup itself: %s" % p)

    # PCZZWSTR: each path NUL-separated, list terminated by a double NUL.
    joined = "\0".join(os.path.abspath(p) for p in paths) + "\0"
    buf = ctypes.create_unicode_buffer(joined)
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, wintypes.LPCWSTR)
    op.pTo = None
    op.fFlags = (FOF_ALLOWUNDO | FOF_NOCONFIRMATION
                 | FOF_SILENT | FOF_NOERRORUI)
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None

    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise RuntimeError("SHFileOperationW failed (rc=%s, aborted=%s)"
                           % (rc, bool(op.fAnyOperationsAborted)))

    remaining = [p for p in paths if os.path.exists(p)]
    if remaining:
        raise RuntimeError("paths still present after recycle: %s" % remaining)
    return list(paths)


def recycle(paths, confirm):
    if not confirm:
        print("Refusing: pass --confirm after reading the warning and path list.",
              file=sys.stderr)
        return 2
    if not os.path.isfile(MANIFEST):
        print("Refusing: no verified backup manifest. Run 'backup' first.",
              file=sys.stderr)
        return 2
    _ok, bad = verify(verbose=False)
    if bad:
        print("Refusing: backup verification failed (%d mismatches)." % bad,
              file=sys.stderr)
        return 2

    existing = [os.path.abspath(p) for p in paths if os.path.exists(p)]
    done = 0
    for i in range(0, len(existing), 10):
        batch = existing[i:i + 10]
        print("RECYCLING batch %d (%d paths):" % (i // 10 + 1, len(batch)))
        for p in batch:
            print("  %s" % p)
        to_recycle_bin(batch)
        done += len(batch)
        print("  verified: %d paths removed to Recycle Bin" % len(batch))
    print("DONE: %d paths sent to Recycle Bin (recoverable)." % done)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["manifest", "backup", "verify",
                                       "recycle", "recycle-caches"])
    ap.add_argument("paths", nargs="*", help="paths for 'recycle'")
    ap.add_argument("--confirm", action="store_true",
                    help="required for any destructive action")
    a = ap.parse_args()

    if a.action == "manifest":
        print("\n".join(affected_paths()))
        return 0
    if a.action == "backup":
        return backup()
    if a.action == "verify":
        _ok, bad = verify()
        return 1 if bad else 0
    if a.action == "recycle":
        return recycle(a.paths, a.confirm)
    if a.action == "recycle-caches":
        return recycle([p for p in CACHE_DIRS if os.path.isdir(p)], a.confirm)
    return 0


if __name__ == "__main__":
    sys.exit(main())
