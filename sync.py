#!/usr/bin/env python3
"""Fetch, build and publish the timetable calendar feed.

Runs locally on the Mac. Pipeline:
  1. fetch_sharepoint_timetable.py --all --headless  (download all weeks)
  2. hash incoming xlsx; if unchanged since last build, exit
  3. build_feed.py -> feed.ics
  4. git commit + push feed.ics to GitHub Pages

GitHub Pages serves feed.ics at https://<user>.github.io/<repo>/feed.ics
iCloud Calendar subscribes to that URL and polls every 30-60 min.

Usage:
  python3 sync.py                 # full run with schedule logic
  python3 sync.py --force         # skip hash check
  python3 sync.py --no-fetch      # reuse existing incoming/
"""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "incoming"
STATE = ROOT / ".sync_state.json"
LONDON = ZoneInfo("Europe/London")


def london_now():
    return datetime.now(LONDON)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def incoming_hashes():
    return {p.name: sha256(p) for p in sorted(INCOMING.glob("*.xlsx"))}


def run(cmd):
    print(f"$ {' '.join(map(str, cmd))}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if r.stdout:
        print(r.stdout.rstrip(), flush=True)
    if r.stderr:
        print(r.stderr.rstrip(), file=sys.stderr, flush=True)
    if r.returncode != 0:
        sys.exit(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}")


def should_run(state, force=False):
    if force:
        return True, "forced"
    now = london_now()
    last = None
    if state.get("last_run"):
        last = datetime.fromisoformat(state["last_run"])
    if 7 <= now.hour < 22:
        return True, "in window 07:00-22:00"
    if last is None or now - last >= timedelta(hours=2):
        return True, "overnight 2h cadence"
    return False, f"overnight cooldown (last run {last.isoformat() if last else 'never'})"


def git_push_feed(feed_path):
    """Commit and push feed.ics so GitHub Pages picks it up."""
    subprocess.run(["git", "add", str(feed_path)], cwd=ROOT, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--stat"],
                          cwd=ROOT, capture_output=True, text=True)
    if not diff.stdout.strip():
        print("git: feed.ics already committed")
        return False
    msg = f"update feed.ics {london_now().strftime('%Y-%m-%d %H:%M')}"
    run(["git", "commit", "-m", msg])
    run(["git", "push", "origin", "main"])
    return True


def main():
    ap = argparse.ArgumentParser(description="Fetch + build + publish timetable feed")
    ap.add_argument("--force", action="store_true", help="skip schedule/hash checks")
    ap.add_argument("--no-fetch", action="store_true", help="skip SharePoint fetch")
    ap.add_argument("--no-push", action="store_true", help="skip git push (build only)")
    a = ap.parse_args()

    state = {}
    if STATE.exists():
        state = json.loads(STATE.read_text())

    ok, why = should_run(state, force=a.force)
    if not ok:
        print(f"skip: {why}")
        return

    before = incoming_hashes()
    if not a.no_fetch:
        run([sys.executable, "fetch_sharepoint_timetable.py", "--all", "--headless"])
    after = incoming_hashes()

    last_built = state.get("last_built_hashes") or {}
    if not a.force and after == last_built:
        state["last_run"] = london_now().isoformat()
        STATE.write_text(json.dumps(state, indent=2))
        print("skip: no incoming xlsx changed since last build")
        return

    run([sys.executable, "build_feed.py", "-o", str(ROOT / "docs" / "feed.ics")])

    state["last_built_hashes"] = incoming_hashes()
    state["last_run"] = london_now().isoformat()
    STATE.write_text(json.dumps(state, indent=2))

    if not a.no_push:
        git_push_feed(ROOT / "docs" / "feed.ics")

    print("done")


if __name__ == "__main__":
    main()