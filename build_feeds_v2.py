#!/usr/bin/env python3
"""Build per-student + per-space feeds from the v2 (Jev) pipeline — incrementally.

Rewires the build/publish path onto v2/ (event_creator.build_events,
spaces.build_space_events, feed_gen.to_ics / spaces.to_space_ics). The old
v1 scripts (build_feeds.py, timetable_to_ics.py) are kept but unused.

Incremental model:
  - Only xlsx files in incoming/ whose content changed since the last build go
    through the v2 classifier (their per-sheet classification cache + the
    per-file event cache make repeats free).
  - Each file's events are stored under site/.events/<stem>.json.
  - Feeds are the union of per-file events across ALL week files, deduped by
    deterministic UIDs (make_uid / make_space_uid); a feed is rewritten only
    when its normalized content changed (volatile DTSTAMP/LAST-MODIFIED lines
    excluded), so an update to one week touches only that week's events in
    each feed, leaving the rest byte-identical (and publish.py uploads nothing
    for unchanged feeds).

Writes (same layout the app + publish.py already expect):
  site/feeds/<slug>.ics          per-student feed
  site/feeds/spaces/<slug>.ics   per-space feed
  site/roster.json
  site/spaces.json
  site/manifest.json

Roster identity comes from v2's group-sheet extraction (lowercased,
MERGE_MAP-merged) with junk entries ('need X,', teachers, day names) dropped.
Display names are title-cased (JJ Angel override); slugs = slugify(display)
=> identical URLs to the old v1 output for unchanged names.

Usage:
  python3 build_feeds_v2.py [--incoming incoming] [--out site] [--force]
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl

from v2.v2_cli import extract_groups_from_xlsx, _is_day_sheet
from v2.event_creator import build_events
from v2.spaces import build_space_events, make_space_uid
from v2.feed_gen import to_ics, dedup_events, make_uid, _subject_key
from v2.spaces import to_space_ics
from v2.weeks import weeks_from_filename

from timetable_to_ics import slugify, MERGE_MAP, DISPLAY_OVERRIDES

# Extend display overrides for v2 keys (lowercased MERGE_MAP outputs)
DISPLAY_OVERRIDES = {**DISPLAY_OVERRIDES, **{"jjangel": "JJ Angel"}}

LONDON = ZoneInfo("Europe/London")
THIN_THRESHOLD = 8
EVENTS_VERSION = 1

# Subjects hidden from card groups (whole-cohort single-group subjects)
HIDE_SUBJECTS = {
    (2, "movement"), (2, "context2"), (2, "conditioning"), (3, "context3"),
    (2, "teacher_training"),
}

# Map v2 subject display -> roster.json subject key
SUBJECT_KEY = {
    "acro": "acro",
    "aerial": "aerial",
    "aerial conditioning": "aerial_conditioning",
    "conditioning": "conditioning",
    "context 1": "context1",
    "context 2": "context2",
    "context 3": "context3",
    "teacher training": "teacher_training",
    "core skills": "core_skills",
    "stand up": "stand_up",
    "clown": "clown",
    "par": "par",
    "par group 1": "par_group_1",
    "par group 2": "par_group_2",
    "manipulation": "manipulation",
    "physical theatre": "physical_theatre",
    "movement": "movement",
    "devising": "devising",
    "creative project": "creative_project",
}

DAY_FULL = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
            "thu": "Thursday", "fri": "Friday"}

JUNK_ROSTER_RE = re.compile(
    r"\bneed\b|monday|tuesday|wednesday|thursday|friday|\bwk\b|\?|,", re.I)

# Teacher names from v2 group_parse (kept in sync)
TEACHERS = {
    "lisa", "ethan", "jane", "chané", "aimee", "aimee bennett",
    "janine", "nicky", "joe", "joe palmer", "jonathan", "jono",
    "mark", "mark parfitt-jones", "parfitt-jones",
    "george", "george fuller", "fuller", "owen",
    "rachel", "rachel kirby", "kirby", "angie", "tony",
    "jamie", "sorcha", "lewis", "lewis trump", "trump",
    "rosy", "coralee", "maia", "heather", "heather parkin", "parkin",
    "moira", "moira hunt", "hunt", "denis", "charlie white",
    "tilly", "emily", "emily orme", "orme",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def normalize_ics(text):
    return "\n".join(l for l in text.splitlines()
                     if not l.startswith(("DTSTAMP:", "LAST-MODIFIED:")))


def write_json_if_changed(dest, payload):
    text = json.dumps(payload, indent=2) + "\n"
    if dest.exists():
        old = dest.read_text()
        strip = lambda s: "\n".join(
            l for l in s.splitlines() if '"updated_at"' not in l)
        if strip(old) == strip(text):
            return False
    dest.write_text(text)
    return True


def load_state():
    state_file = Path(".sync_state.json")
    return json.loads(state_file.read_text()) if state_file.exists() else {}


def save_state(state):
    Path(".sync_state.json").write_text(json.dumps(state, indent=2))


def find_weeks(incoming):
    files = [p for p in sorted(incoming.iterdir())
             if p.suffix.lower() == ".xlsx" and "week" in p.name.lower()
             and p.name.lower().startswith("term")]
    if not files:
        print(f"No timetable xlsx found in {incoming}", file=sys.stderr)
    return files


def display_name(key):
    """lowercased v2 key -> display name."""
    parts = [w[:1].upper() + w[1:] if w else w for w in key.split()]
    disp = " ".join(parts)
    return DISPLAY_OVERRIDES.get(key, disp)


def drop_junk(students_by_year, student_group_data):
    bad = {s for s in student_group_data
           if len(s) > 25 or JUNK_ROSTER_RE.search(s) or s in TEACHERS}
    if bad:
        for b in sorted(bad):
            print(f"  drop junk roster '{b}'", file=sys.stderr)
        students_by_year = {
            y: [n for n in ns if n.lower() not in bad]
            for y, ns in students_by_year.items()
        }
        students_by_year = {y: ns for y, ns in students_by_year.items() if ns}
        student_group_data = {k: v for k, v in student_group_data.items() if k not in bad}
    return students_by_year, student_group_data


def _sanitize_in(obj):
    if isinstance(obj, dt.datetime):
        return {"__dt__": obj.isoformat()}
    if isinstance(obj, dt.date):
        return {"__date__": obj.isoformat()}
    if isinstance(obj, dict):
        return {k: _sanitize_in(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_in(v) for v in obj]
    return obj


def _restore_in(obj):
    if isinstance(obj, dict):
        if "__dt__" in obj:
            return dt.datetime.fromisoformat(obj["__dt__"])
        if "__date__" in obj:
            return dt.date.fromisoformat(obj["__date__"])
        return {k: _restore_in(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_restore_in(v) for v in obj]
    return obj


def assign_slugs(roster):
    seen = {}
    slugs = {}
    for key in sorted(roster):
        base = slugify(roster[key]["display"])
        slug = base
        i = 2
        while slug in seen:
            slug = f"{base}-{i}"
            i += 1
        seen[slug] = key
        slugs[key] = slug
    return slugs


def build_groups(student_group_data, roster):
    out = {}
    for key, infos in student_group_data.items():
        year = roster.get(key, {}).get("year", 1)
        by_subj = {}
        for info in infos:
            s = (info.get("subject") or "").lower()
            g = (info.get("group") or "").strip()
            sk = SUBJECT_KEY.get(s, re.sub(r"[^a-z0-9]+", "_", s).strip("_"))
            if not sk or (year, sk) in HIDE_SUBJECTS:
                continue
            if g:
                gdisp = DAY_FULL.get(g.lower(), g)
                by_subj.setdefault(sk, set()).add(gdisp)
        if by_subj:
            out[key] = {sk: " + ".join(sorted(gs)) for sk, gs in by_subj.items()}
    return out


def process_one_file(path):
    stem = path.stem
    wb = openpyxl.load_workbook(path, data_only=True)
    weeks = weeks_from_filename(path)
    groups_by_subject_year, students_by_year, student_group_data = \
        extract_groups_from_xlsx(wb, xlsx_path=path)
    students_by_year, student_group_data = drop_junk(students_by_year, student_group_data)
    day_sheets = [(n, wb[n]) for n in wb.sheetnames if _is_day_sheet(n)]
    events = build_events(day_sheets, groups_by_subject_year, students_by_year,
                          student_group_data, weeks_to_cover=weeks, wb=wb,
                          cache_name=path.name)
    events = {k: v for k, v in events.items() if k in student_group_data}
    space_events = build_space_events(day_sheets, weeks_to_cover=weeks)
    wb.close()
    return {
        "weeks": weeks,
        "students_by_year": students_by_year,
        "student_group_data": student_group_data,
        "events": events,
        "space_events": space_events,
    }


def merge_all(merged, payload):
    for y, names in payload["students_by_year"].items():
        merged["students_by_year"].setdefault(y, set()).update(names)
    for k, infos in payload["student_group_data"].items():
        merged["student_group_data"].setdefault(k, []).extend(infos)
    for k, evts in payload["events"].items():
        merged["events"].setdefault(k, []).extend(evts)
    for k, evts in payload["space_events"].items():
        merged["space_events"].setdefault(k, []).extend(evts)
    return merged


def dedup_student_events(events):
    return dedup_events(events)


def dedup_space_events(events):
    seen = {}
    for e in events:
        k = make_space_uid(e["date"], e["start"], e["end"],
                           _subject_key(e.get("subject")), e["name"])
        if k not in seen:
            seen[k] = e
        else:
            prev = seen[k]
            locs = sorted(set(prev["location"].split(" + ") + [e["location"]]))
            prev["location"] = " + ".join(locs)
            for t in e.get("teachers", []):
                if t not in prev.get("teachers", []):
                    prev.setdefault("teachers", []).append(t)
    return sorted(seen.values(), key=lambda e: e["start"])


def main():
    ap = argparse.ArgumentParser(description="Build v2 feeds incrementally")
    ap.add_argument("--incoming", default="incoming")
    ap.add_argument("--out", default="site")
    ap.add_argument("--force", action="store_true",
                    help="reprocess all files, ignore hashes")
    a = ap.parse_args()

    incoming = Path(a.incoming)
    out = Path(a.out)
    files = find_weeks(incoming)
    if not files:
        sys.exit("No Term* week xlsx found")

    events_dir = out / ".events"
    events_dir.mkdir(parents=True, exist_ok=True)
    feeds_dir = out / "feeds"
    feeds_dir.mkdir(parents=True, exist_ok=True)

    state = load_state()
    last_built = state.get("last_built_hashes") or {}

    hashes = {f.name: sha256(f) for f in files}
    changed = []
    unchanged = []
    for f in files:
        evfile = events_dir / f"{f.stem}.json"
        if a.force or hashes[f.name] != last_built.get(f.name) or not evfile.exists():
            changed.append(f)
        else:
            try:
                cached = json.loads(evfile.read_text())
                if cached.get("version", 0) != EVENTS_VERSION or \
                   cached.get("file_sha256") != hashes[f.name]:
                    changed.append(f)
                else:
                    unchanged.append(f)
            except (OSError, ValueError):
                changed.append(f)

    merged = {
        "students_by_year": {},
        "student_group_data": {},
        "events": {},
        "space_events": {},
    }
    rebuilt = 0
    for f in changed:
        print(f"\n===== {f.name} (changed) =====")
        payload = process_one_file(f)
        payload = _sanitize_in(payload)
        evfile = events_dir / f"{f.stem}.json"
        evfile.write_text(json.dumps({
            "version": EVENTS_VERSION,
            "file_sha256": hashes[f.name],
            **payload
        }, indent=1))
        merged = merge_all(merged, _restore_in(payload))
        rebuilt += 1
    for f in unchanged:
        print(f"unchanged: {f.name}")
        payload = _restore_in(json.loads((events_dir / f"{f.stem}.json").read_text()))
        merged = merge_all(merged, payload)

    if not merged["events"]:
        print("No events generated", file=sys.stderr)
        sys.exit(1)

    # Build roster from merged students_by_year
    roster = {}
    for y, names in merged["students_by_year"].items():
        for n in names:
            e = roster.setdefault(n, {"year": int(y), "name": n})
            e["year"] = min(e["year"], int(y))
    for key in roster:
        roster[key]["display"] = display_name(key)

    slugs = assign_slugs(roster)
    groups_map = build_groups(merged["student_group_data"], roster)

    # Student feeds
    manifest_feeds = {}
    event_counts = {}
    built = 0
    for key in sorted(roster):
        info = roster[key]
        slug = slugs[key]
        events = merged["events"].get(key, [])
        events = dedup_student_events(events)
        ics = to_ics(events, info["display"])
        manifest_feeds[slug] = sha256_text(normalize_ics(ics))
        dest = feeds_dir / f"{slug}.ics"
        if dest.exists() and sha256_text(normalize_ics(dest.read_text())) == manifest_feeds[slug]:
            continue
        dest.write_text(ics)
        built += 1
        event_counts[slug] = len(events)

    # Space feeds
    spaces_dir = feeds_dir / "spaces"
    spaces_dir.mkdir(parents=True, exist_ok=True)
    space_names = sorted(merged["space_events"].keys())
    space_slugs = {s: slugify(s) for s in space_names}
    # collision-safe
    seen = {}
    for s in space_names:
        base = space_slugs[s]
        slug = base
        i = 2
        while slug in seen:
            slug = f"{base}-{i}"
            i += 1
        seen[slug] = s
        space_slugs[s] = slug
    space_counts = {}
    for canon in space_names:
        slug = space_slugs[canon]
        events = dedup_space_events(merged["space_events"].get(canon, []))
        if not events:
            continue
        ics = to_space_ics(events, canon)
        manifest_feeds[f"spaces/{slug}"] = sha256_text(normalize_ics(ics))
        dest = spaces_dir / f"{slug}.ics"
        if dest.exists() and sha256_text(normalize_ics(dest.read_text())) == manifest_feeds[f"spaces/{slug}"]:
            continue
        dest.write_text(ics)
        built += 1
        space_counts[slug] = len(events)

    now = dt.datetime.now(LONDON)
    write_json_if_changed(out / "roster.json", {
        "updated_at": now.isoformat(),
        "students": [{"name": roster[k]["display"], "slug": slugs[k],
                      "year": roster[k]["year"],
                      "groups": groups_map.get(k, {})}
                     for k in sorted(roster)]
    })
    write_json_if_changed(out / "spaces.json", {
        "updated_at": now.isoformat(),
        "spaces": [{"name": s, "slug": space_slugs[s]} for s in space_names]
    })
    write_json_if_changed(out / "manifest.json", {
        "updated_at": now.isoformat(), "feeds": manifest_feeds
    })

    # Prune stale feeds
    wanted = {f"{k}.ics" for k in manifest_feeds}
    for f in feeds_dir.rglob("*.ics"):
        if f.relative_to(feeds_dir).as_posix() not in wanted:
            f.unlink()
            print(f"pruned stale {f.relative_to(out).as_posix()}")

    # Prune stale event caches
    current_stems = {f.stem for f in files}
    for ev in events_dir.glob("*.json"):
        if ev.stem not in current_stems:
            ev.unlink()
            print(f"pruned stale events cache {ev.name}")

    # Update state
    state["last_built_hashes"] = {f.name: hashes[f.name] for f in files}
    save_state(state)

    print(f"\nRoster: {len(roster)} students, {len(space_names)} spaces "
          f"from {len(files)} week files; "
          f"{rebuilt} files reprocessed, {built} feeds written/updated -> {out}/")
    for slug in sorted(space_counts):
        print(f"SPACE:{slug}:{space_counts[slug]} events")
    for slug in sorted(event_counts):
        if event_counts[slug] < THIN_THRESHOLD:
            print(f"THIN:{slug}:{event_counts[slug]} events", file=sys.stderr)


if __name__ == "__main__":
    main()