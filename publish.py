#!/usr/bin/env python3
"""Publish built feeds to the Vercel Blob store (runs on the Mac).

Uploads only files whose content hash changed since the last publish, so a
15-minute sync that finds nothing new finishes in ~2s with zero uploads.
Stable pathnames + --allow-overwrite: calendar subscriptions keep working,
new bytes go live on the CDN within the blob cache TTL.

Auth: uses the Vercel CLI session (run `vercel login` once). The store is
resolved from the linked project; --store-id override via BLOB_STORE_ID.

Usage:
  python3 publish.py [--site site] [--force]
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".sync_state.json"
ENV_LOCAL = ROOT / ".env.local"

# pathname -> (content-type, cache max-age seconds)
TYPES = {
    ".ics": ("text/calendar", 300),
    ".json": ("application/json", 120),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2))


def blob_put(local_path, pathname, content_type, max_age, store_id=None):
    cmd = ["vercel", "blob", "put", str(local_path),
           "--access", "public",
           "--pathname", pathname,
           "--content-type", content_type,
           "--cache-control-max-age", str(max_age),
           "--allow-overwrite", "true"]
    if store_id:
        cmd += ["--store-id", store_id]
    r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True,
                       env=blob_env())
    if r.returncode != 0 and needs_refresh(r.stdout + r.stderr):
        refresh_oidc()
        r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True,
                           env=blob_env())
    if r.returncode != 0:
        sys.exit(f"blob put failed for {pathname}:\n{r.stdout}\n{r.stderr}")
    return r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "ok"


def blob_env():
    """Subprocess env with .env.local values (OIDC token) filled in."""
    env = dict(os.environ)
    if ENV_LOCAL.exists():
        for line in ENV_LOCAL.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"'))
    return env


def needs_refresh(output):
    return any(s in output for s in
               ("OIDC", "token", "401", "403", "Unauthorized"))


def refresh_oidc():
    print("refreshing Vercel OIDC token...", flush=True)
    r = subprocess.run(["vercel", "env", "pull", str(ENV_LOCAL), "--yes"],
                       cwd=ROOT, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"OIDC refresh failed:\n{r.stdout}\n{r.stderr}")


def main():
    ap = argparse.ArgumentParser(description="Publish feeds to Vercel Blob")
    ap.add_argument("--site", default="site")
    ap.add_argument("--force", action="store_true",
                    help="upload everything, ignoring hashes")
    ap.add_argument("--store-id", default=None)
    a = ap.parse_args()

    store_id = a.store_id or os.environ.get("BLOB_STORE_ID")
    site = ROOT / a.site
    roster = site / "roster.json"
    manifest = site / "manifest.json"
    if not roster.exists() or not manifest.exists():
        sys.exit(f"run build_feeds.py first ({site} has no roster/manifest)")

    files = sorted((site / "feeds").glob("*.ics")) + [roster, manifest]
    state = load_state()
    published = state.get("published_hashes") or {}

    uploaded, skipped = 0, 0
    new_hashes = {}
    for f in files:
        digest = sha256(f)
        pathname = f.name if f.suffix == ".json" else f"feeds/{f.name}"
        new_hashes[pathname] = digest
        if not a.force and published.get(pathname) == digest:
            skipped += 1
            continue
        ctype, age = TYPES[f.suffix]
        url = blob_put(f, pathname, ctype, age, store_id)
        print(f"uploaded {pathname} -> {url}")
        uploaded += 1

    state["published_hashes"] = new_hashes
    save_state(state)
    print(f"done: {uploaded} uploaded, {skipped} unchanged")

    # Delete blobs for feeds that no longer exist locally (renames/merges).
    removed = [p for p in published if p not in new_hashes]
    for pathname in sorted(removed):
        cmd = ["vercel", "blob", "del", pathname]
        if store_id:
            cmd += ["--store-id", store_id]
        r = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True,
                           env=blob_env())
        print(f"deleted {pathname}: {'ok' if r.returncode == 0 else r.stderr.strip()}")


if __name__ == "__main__":
    main()
