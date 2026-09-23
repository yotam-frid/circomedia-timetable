#!/usr/bin/env python3
"""Fetch, build and publish the Circomedia timetable feeds.

Runs locally on the Mac (SharePoint login only works here). Pipeline:
  1. fetch_sharepoint_timetable.py --all --headless  (download all weeks)
  2. build_feeds_v2.py                               (incremental v2 pipeline)
  3. publish.py                                      -> rsync site/ to the Hetzner box (opt-in)

Usage:
  python3 sync.py                 # build only; publish is opt-in
  python3 sync.py --publish       # also rsync changed feeds to the Hetzner box
  python3 sync.py --force         # force rebuild (ignore xlsx hashes)
  python3 sync.py --no-fetch      # reuse existing incoming/
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
INCOMING = ROOT / "incoming"
STATE = ROOT / ".sync_state.json"
LONDON = ZoneInfo("Europe/London")


def london_now():
    return datetime.now(LONDON)


def run(cmd):
    print(f"$ {' '.join(map(str, cmd))}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    if r.stdout:
        print(r.stdout.rstrip(), flush=True)
    if r.stderr:
        print(r.stderr.rstrip(), file=sys.stderr, flush=True)
    if r.returncode != 0:
        sys.exit(f"command failed ({r.returncode}): {' '.join(map(str, cmd))}")


def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2))


def is_auth_failure(stderr):
    return "HTTP 403" in stderr


def notify_auth_expired():
    msg = ("Circomedia sync blocked: SharePoint session expired. "
           "Re-login with: python3 fetch_sharepoint_timetable.py --login")
    subprocess.run(
        ["osascript", "-e",
         f'display alert "Circomedia sync" message "{msg}" '
         'buttons {"OK"} default button "OK"'],
        capture_output=True,
    )


def main():
    ap = argparse.ArgumentParser(description="Fetch + build + publish timetable feeds")
    ap.add_argument("--force", action="store_true", help="force rebuild (ignore xlsx hashes)")
    ap.add_argument("--no-fetch", action="store_true", help="skip SharePoint fetch")
    ap.add_argument("--publish", action="store_true",
                    help="rsync built feeds to the Hetzner box (opt-in; default is build-only)")
    a = ap.parse_args()

    state = load_state()

    if not a.no_fetch:
        fetch = [sys.executable, "fetch_sharepoint_timetable.py", "--all", "--headless"]
        print(f"$ {' '.join(map(str, fetch))}", flush=True)
        r = subprocess.run(fetch, cwd=ROOT, text=True, capture_output=True)
        if r.stdout:
            print(r.stdout.rstrip(), flush=True)
        if r.stderr:
            print(r.stderr.rstrip(), file=sys.stderr, flush=True)
        if r.returncode != 0:
            if is_auth_failure(r.stderr) and not state.get("auth_notified"):
                notify_auth_expired()
                state["auth_notified"] = True
            state["last_run"] = london_now().isoformat()
            save_state(state)
            sys.exit(f"command failed ({r.returncode}): {' '.join(map(str, fetch))}")
        if state.get("auth_notified"):
            state["auth_notified"] = False
            save_state(state)

    run([sys.executable, "build_feeds_v2.py"] + (["--force"] if a.force else []))

    state = load_state()
    state["last_run"] = london_now().isoformat()
    save_state(state)

    if a.publish:
        run([sys.executable, "publish.py"])

    print("done")


if __name__ == "__main__":
    main()
