#!/usr/bin/env python3
"""Fetch, build and publish the Circomedia timetable feeds.

Runs locally on the Mac (SharePoint login only works here). Pipeline:
  1. fetch_sharepoint_timetable.py --all --headless  (download all weeks)
  2. build_feeds_v2.py                               (incremental v2 pipeline)
  3. publish.py                                      -> Vercel Blob (only-changed)

No site redeploy is needed for data changes; `vercel deploy` runs only when
the SvelteKit app changes (src/, svelte.config.js, package.json).

Usage:
  python3 sync.py                 # full run with schedule logic
  python3 sync.py --force         # skip schedule checks, force rebuild
  python3 sync.py --no-fetch      # reuse existing incoming/
  python3 sync.py --no-publish    # skip blob publish (build only)
"""

import argparse
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


def should_run(state, force=False):
    if force:
        return True, "forced"
    now = london_now()
    last = None
    if state.get("last_run"):
        last = datetime.fromisoformat(state["last_run"])
    if last is None or now - last >= timedelta(hours=6):
        return True, "6h cooldown elapsed"
    return False, f"cooldown (last run {last.isoformat() if last else 'never'})"


def main():
    ap = argparse.ArgumentParser(description="Fetch + build + publish timetable feeds")
    ap.add_argument("--force", action="store_true", help="skip schedule/hash checks")
    ap.add_argument("--no-fetch", action="store_true", help="skip SharePoint fetch")
    ap.add_argument("--no-publish", action="store_true", help="skip blob publish (build only)")
    a = ap.parse_args()

    state = load_state()

    ok, why = should_run(state, force=a.force)
    if not ok:
        print(f"skip: {why}")
        return

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

    if not a.no_publish:
        run([sys.executable, "publish.py"])

    print("done")


if __name__ == "__main__":
    main()
