#!/usr/bin/env python3
"""Fetch, build and publish the Circomedia timetable feeds.

Runs locally on the Mac (SharePoint login only works here). Pipeline:
  1. fetch_sharepoint_timetable.py --all --headless  (download all weeks)
  2. hash incoming xlsx; if unchanged since last build, exit
  3. build_feeds.py -> site/ (per-student feeds + roster + manifest)
  4. publish.py -> Vercel Blob (only changed files; live in seconds)

No site redeploy is needed for data changes; `vercel deploy` runs only when
the SvelteKit app changes (src/, svelte.config.js, package.json).

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
    if 7 <= now.hour < 22:
        return True, "in window 07:00-22:00"
    if last is None or now - last >= timedelta(hours=2):
        return True, "overnight 2h cadence"
    return False, f"overnight cooldown (last run {last.isoformat() if last else 'never'})"


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

    last_built = state.get("last_built_hashes") or {}
    if not a.force and incoming_hashes() == last_built:
        state["last_run"] = london_now().isoformat()
        STATE.write_text(json.dumps(state, indent=2))
        print("skip: no incoming xlsx changed since last build")
        return

    run([sys.executable, "build_feeds.py"])

    # Reload state: build/publish steps don't write it, but keep fresh.
    state = load_state()
    state["last_built_hashes"] = incoming_hashes()
    state["last_run"] = london_now().isoformat()
    STATE.write_text(json.dumps(state, indent=2))

    if not a.no_publish:
        run([sys.executable, "publish.py"])

    print("done")


if __name__ == "__main__":
    main()
