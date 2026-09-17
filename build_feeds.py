#!/usr/bin/env python3
"""Build per-student calendar feeds from every week's timetable.

Reads all 'Term * Weeks? N[...].xlsx' files in incoming/, derives the roster
from the latest week file (timetable_to_ics.parse_roster), extracts each
student's lessons across all weeks, and writes:

  site/feeds/<slug>.ics   one feed per student (deterministic UIDs)
  site/roster.json         [{name, slug, year}] — search index for the web app
  site/manifest.json       {updated_at, feeds: {slug: sha256}} — change tracking

Feeds whose content hash is unchanged are left untouched (mtime preserved),
so publish.py can upload only what changed.

Usage:
  python3 build_feeds.py [--incoming incoming] [--out site]
"""

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl

import timetable_to_ics as tt

LONDON = ZoneInfo("Europe/London")

# Feeds with fewer events than this are suspicious (a real student week has
# Core Skills alone at 4+); listed on stderr for human review, never fatal.
THIN_THRESHOLD = 8


def display_groups(me):
    """Detail dicts -> card strings: 'Group B', 'Billie', 'All + Group B'.
    Weekday annotations are omitted; the feed already carries the days."""
    out = {}
    for s in sorted(me):
        d = me[s]
        labels = d.get("labels", [])
        out[s] = labels[0] if len(labels) == 1 else " + ".join(labels)
    return out


def find_weeks(incoming):
    files = [p for p in sorted(incoming.iterdir())
             if p.suffix.lower() == ".xlsx" and "week" in p.name.lower()
             and p.name.lower().startswith("term")]
    if not files:
        print(f"No timetable xlsx found in {incoming}", file=sys.stderr)
    return files


def assign_slugs(roster):
    """Slug per student, collision-safe ('sam' vs 'Sam!' -> 'sam', 'sam-2')."""
    seen = {}
    slugs = {}
    for key in sorted(roster):
        base = tt.slugify(roster[key]["name"])
        slug = base
        i = 2
        while slug in seen:
            slug = f"{base}-{i}"
            i += 1
        seen[slug] = key
        slugs[key] = slug
    return slugs


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def write_json_if_changed(dest, payload):
    """Write JSON only if content changed ignoring updated_at, so the footer
    timestamp reflects the last real data change, not the last build."""
    text = json.dumps(payload, indent=2) + "\n"
    if dest.exists():
        old = dest.read_text()
        strip = lambda s: "\n".join(
            l for l in s.splitlines() if '"updated_at"' not in l)
        if strip(old) == strip(text):
            return False
    dest.write_text(text)
    return True


def normalize_ics(text):
    """Drop volatile DTSTAMP/LAST-MODIFIED lines so only real schedule
    changes count as changed (same rule the old git-push pipeline used)."""
    return "\n".join(l for l in text.splitlines()
                     if not l.startswith(("DTSTAMP:", "LAST-MODIFIED:")))


def main():
    ap = argparse.ArgumentParser(description="Build per-student calendar feeds")
    ap.add_argument("--incoming", default="incoming")
    ap.add_argument("--out", default="site")
    a = ap.parse_args()

    files = find_weeks(Path(a.incoming))
    if not files:
        sys.exit("No weeks to build")

    out = Path(a.out)
    feeds_dir = out / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    # Roster from all week files; union with older files in case a
    # student only appears in an earlier sheet. Keyed by identity norm so
    # spelling variants merge across files too.
    roster = {}
    for f in sorted(files):
        wb = openpyxl.load_workbook(f, data_only=True)
        for key, info in tt.parse_roster(wb).items():
            norm = tt.roster_norm(key) or key.lower()
            e = roster.setdefault(
                norm, {"name": info["name"], "year": info["year"], "keys": []})
            for k in info["keys"]:
                if k not in e["keys"]:
                    e["keys"].append(k)
            e["year"] = min(e["year"], info["year"])
            if ("(" in e["name"], len(e["name"])) > ("(" in info["name"], len(info["name"])):
                e["name"] = info["name"]
    for e in roster.values():
        e["keys"].sort()
    slugs = assign_slugs(roster)

    # Pre-parse group maps once per week, then reuse per student.
    weeks = []
    for f in files:
        wb = openpyxl.load_workbook(f, data_only=True)
        weeks.append((f, wb, tt.parse_groups(wb)))

    # Groups for the roster cards come from the latest week file.
    latest_groups = {}
    _, _, last_maps = weeks[-1]
    for key in roster:
        me_last, _ = tt.lookup_merged(
            last_maps, roster[key]["keys"], roster[key]["year"])
        latest_groups[key] = display_groups(me_last)

    manifest_feeds = {}
    event_counts = {}
    built = 0
    for key in sorted(roster):
        info = roster[key]
        slug = slugs[key]
        all_events = []
        for f, wb, maps in weeks:
            me, matched_year = tt.lookup_merged(maps, info["keys"], info["year"])
            events, _, _ = tt.extract_for_student(
                wb, info["keys"][0], start_year=info["year"], filename=f.name,
                groups=me, matched_year=matched_year, aliases=info["keys"])
            all_events.extend(events)
        all_events.sort(key=lambda e: (e["date"], e["start"]))
        seen = {}
        for e in all_events:
            seen[tt.make_uid(e["date"], e["start"], e["end"],
                             e.get("subject_key"))] = e
        unique = [seen[k] for k in sorted(seen)]
        event_counts[slug] = len(unique)
        ics = tt.to_ics(unique, info["name"])
        dest = feeds_dir / f"{slug}.ics"
        # Manifest tracks schedule content only (stamps excluded), so it is
        # stable across no-change rebuilds.
        manifest_feeds[slug] = sha256_text(normalize_ics(ics))
        if dest.exists() and sha256_text(normalize_ics(dest.read_text())) == \
                manifest_feeds[slug]:
            continue  # unchanged: keep mtime so publish skips it
        dest.write_text(ics)
        built += 1

    students = [{"name": roster[k]["name"], "slug": slugs[k],
                 "year": roster[k]["year"],
                 "groups": latest_groups.get(k, {})} for k in sorted(roster)]
    now = dt.datetime.now(LONDON)
    write_json_if_changed(out / "roster.json",
                          {"updated_at": now.isoformat(), "students": students})
    write_json_if_changed(out / "manifest.json",
                          {"updated_at": now.isoformat(), "feeds": manifest_feeds})

    # Prune feeds for students no longer on the roster (renames/merges).
    wanted = {f"{s}.ics" for s in manifest_feeds}
    for f in feeds_dir.glob("*.ics"):
        if f.name not in wanted:
            f.unlink()
            print(f"pruned stale {f.name}")

    print(f"Roster: {len(students)} students from {len(files)} week files; "
          f"{built} feeds written/updated -> {out}/")

    # Thin-feed tripwire: suspiciously small feeds are usually matching
    # mistakes (phantom students are gone by construction now). Advisory
    # only -- shout, don't fail.
    for slug in sorted(event_counts):
        if event_counts[slug] < THIN_THRESHOLD:
            print(f"THIN:{slug}:{event_counts[slug]} events", file=sys.stderr)


if __name__ == "__main__":
    main()
