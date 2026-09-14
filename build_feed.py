#!/usr/bin/env python3
"""Merge every week's timetable in incoming/ into a single calendar feed.

Reads all 'Term * Weeks? N[...].xlsx' files in incoming/, extracts the
student's lessons from each (via timetable_to_ics), merges the events, and
writes feed.ics with deterministic UIDs so a subscribed calendar updates in
place instead of duplicating.

Usage:
  python3 build_feed.py [--name Yotam] [--year 1] [--incoming incoming]
"""

import argparse
import sys
from pathlib import Path

import openpyxl

import timetable_to_ics as tt


def find_weeks(incoming):
    files = [p for p in sorted(incoming.iterdir())
             if p.suffix.lower() == ".xlsx" and "week" in p.name.lower()
             and p.name.lower().startswith("term")]
    if not files:
        print(f"No timetable xlsx found in {incoming}", file=sys.stderr)
    return files


def main():
    ap = argparse.ArgumentParser(description="Merge week timetables into a calendar feed")
    ap.add_argument("--name", default="Yotam")
    ap.add_argument("--year", type=int, default=1, choices=(1, 2, 3))
    ap.add_argument("--incoming", default="incoming")
    ap.add_argument("-o", "--out", default="docs/feed.ics")
    a = ap.parse_args()

    files = find_weeks(Path(a.incoming))
    if not files:
        sys.exit("No weeks to build")

    all_events = []
    for f in files:
        wb = openpyxl.load_workbook(f, data_only=True)
        events, me, matched_year = tt.extract_for_student(
            wb, a.name, start_year=a.year, filename=f.name)
        print(f"{f.name}: {len(events)} events (Year {matched_year}, "
              f"Monday {tt.monday_from_filename(f.name)})")
        all_events.extend(events)

    all_events.sort(key=lambda e: (e["date"], e["start"]))
    # De-dupe by deterministic UID, keeping the last occurrence.
    seen = {}
    for e in all_events:
        seen[tt.make_uid(e["date"], e["start"], e["end"], e.get("subject_key"))] = e
    unique = [seen[k] for k in sorted(seen)]

    Path(a.out).write_text(tt.to_ics(unique, a.name))
    print(f"Wrote {len(unique)} unique events -> {a.out}")


if __name__ == "__main__":
    main()