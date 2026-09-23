#!/usr/bin/env python3
"""Publish built feeds to the Hetzner box (runs on the Mac).

rsync site/ -> root@91.98.227.5:/srv/timetable/, content-addressed
(--checksum) so an unchanged run transfers nothing and finishes in ~1s.
--delete prunes remote files that no longer exist locally (renames/merges) —
the old 'vercel blob del' cleanup. Static file serving: no per-file operation
metering anywhere; just disk + nginx.

Usage:
  python3 publish.py [--site site]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".sync_state.json"
HOST = "root@91.98.227.5"
REMOTE = "/srv/timetable"


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2))


def main():
    ap = argparse.ArgumentParser(description="Publish feeds to the Hetzner box")
    ap.add_argument("--site", default="site")
    a = ap.parse_args()

    site = ROOT / a.site
    if not (site / "roster.json").exists() or not (site / "manifest.json").exists():
        sys.exit(f"run build_feeds_v2.py first ({site} has no roster/manifest)")

    dest = f"{HOST}:{REMOTE}/"
    r = subprocess.run(
        ["rsync", "-ac", "--delete", "-i", "--exclude=.events/",
         str(site) + "/", dest],
        capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"rsync failed:\n{r.stdout}\n{r.stderr}")

    lines = r.stdout.splitlines()
    transferred = sum(1 for ln in lines if ln.startswith(">f"))
    deleted = sum(1 for ln in lines if ln.startswith("*deleting"))

    cycle = datetime.now().strftime("%Y-%m")
    state = load_state()
    usage = state.get("usage") or {}
    if usage.get("cycle") != cycle:
        usage = {"cycle": cycle, "files": 0}
    usage["files"] += transferred
    state["usage"] = usage
    save_state(state)
    print(f"delivered {transferred} files to {dest}"
          + (f", pruned {deleted}" if deleted else ""))
    print(f"operations this run: {transferred} files; "
          f"month-to-date ({cycle}): {usage['files']}")


if __name__ == "__main__":
    main()