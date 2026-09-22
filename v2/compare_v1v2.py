"""Compare v1 production ICS feeds against v2 generated events.

Usage:
    python -m v2.compare_v1v2

Runs v2 against weeks 1+2, downloads v1 feeds, compares event-by-event.
Reports matches, missing-in-v1, and missing-in-v2.
"""
import datetime as dt
import re
import sys
from pathlib import Path

import openpyxl

from v2.feed_gen import dedup_events
from v2.v2_cli import extract_groups_from_xlsx, process_file


# --- ICS parsing ---

def parse_ics(ics_text):
    """Parse ICS text into list of event dicts."""
    events = []
    for block in ics_text.split("BEGIN:VEVENT"):
        if "DTSTART" not in block:
            continue
        ds = re.search(r"DTSTART[^:]*:(\d{8}T\d{6})", block)
        de = re.search(r"DTEND[^:]*:(\d{8}T\d{6})", block)
        sm = re.search(r"SUMMARY:(.*)", block)
        lo = re.search(r"LOCATION:(.*)", block)
        if not (ds and sm):
            continue
        start = dt.datetime.strptime(ds.group(1), "%Y%m%dT%H%M%S")
        end = dt.datetime.strptime(de.group(1), "%Y%m%dT%H%M%S") if de else None
        events.append({
            "date": start.date(),
            "start": start,
            "end": end,
            "name": sm.group(1).strip(),
            "location": lo.group(1).strip() if lo else "",
        })
    return events


# --- Normalization ---

# Map v1 event name prefixes to v2 subject names
_NAME_ALIASES = {
    "Core Skills - Tumbling": "core skills",
    "Core Skills - Handstands": "core skills",
    "Core Skills - Professional Members": "core skills",
    "Core Skills": "core skills",
    "Dance": "movement",
    "Pro Tour": "context 3",
    "Devising": "physical theatre",
}

# Strip group suffixes like "(Group 1)", "(Group A)", "(Group C)" from v1 names
_GROUP_SUFFIX_RE = re.compile(r"\s*\(Group\s+[A-Z0-9]+\)\s*$")
# Strip "StudentName - Tutor" 1-to-1 suffix (e.g., "Charlie straps - Janine" → "Aerial")
_1TO1_SUFFIX_RE = re.compile(r"\s*-\s*\w+\s*$")
# "Charlie (Creative)" → "Creative" (student name in parens for owner blocks)
_OWNER_PARENS_RE = re.compile(r"^\w+\s*\((.+)\)\s*$")
# "Charlie straps" → "Aerial", "Kitty" → keep as-is, etc.
# These are 1-to-1 events where v1 uses text content, v2 uses subject
_1TO1_TEXT_MAP = {
    "charlie straps": "aerial",
    "charlie rope": "aerial",
    "joanna": "movement",  # Joanna- Nicky = Movement in v1
    "nem": "movement",
    "lucy": "clown",  # Lucy - Aimee = Clown in v1
    "rose": "clown",
    "tali": "movement",
    "kitty": "clown",  # Kitty - Jonathan = Clown in v1
    "saphy": "movement",
    "maya": "physical theatre",
    "finley": "par",
    "pipper": "clown",
    "dee dee": "context 2",
    "farrah": "context 2",
    "oakley": "par",  # Oakley & Joanna = PAR
}


def normalize_subject(name):
    """Strip group/type suffixes and normalize names for comparison."""
    # Strip trailing (Group X) suffix
    name = _GROUP_SUFFIX_RE.sub("", name)
    # "Charlie (Creative)" → "Creative"
    m = _OWNER_PARENS_RE.match(name)
    if m:
        name = m.group(1)
    # Strip trailing " - Tutor" from 1-to-1 names
    name = _1TO1_SUFFIX_RE.sub("", name)
    for v1_prefix, v2_name in _NAME_ALIASES.items():
        if name.startswith(v1_prefix):
            return v2_name
    # Check 1-to-1 text map
    key = name.strip().lower()
    if key in _1TO1_TEXT_MAP:
        return _1TO1_TEXT_MAP[key]
    return key


def normalize_location(loc):
    """Normalize location strings."""
    loc = loc.replace("Gym Bay 1 + Gym Bay 2", "Gym")
    loc = loc.replace("Gym Bay 1", "Gym").replace("Gym Bay 2", "Gym")
    loc = re.sub(r"\s*\(.*?\)", "", loc)  # strip parenthetical notes
    return loc.strip()


def event_key_v1(e):
    """Comparable key for a v1 event (date + start time + normalized subject)."""
    return (e["date"], e["start"].time(), normalize_subject(e["name"]))


def event_key_v2(e):
    """Comparable key for a v2 event."""
    return (e["date"], e["start"].time(), normalize_subject(e["name"]))


# --- Comparison ---

WEEK1_START = dt.date(2026, 9, 14)
WEEK2_END = dt.date(2026, 9, 25)


def in_weeks_1_2(d):
    return WEEK1_START <= d <= WEEK2_END


def compare_student(name, v1_ics_text, v2_events):
    """Compare v1 ICS with v2 events for one student. Return discrepancy list."""
    v1_all = parse_ics(v1_ics_text)
    v1 = [e for e in v1_all if in_weeks_1_2(e["date"])]
    v2 = [e for e in v2_events if in_weeks_1_2(e["date"])]

    v1_keys = {}
    for e in v1:
        k = event_key_v1(e)
        v1_keys.setdefault(k, []).append(e)
    v2_keys = {}
    for e in v2:
        k = event_key_v2(e)
        v2_keys.setdefault(k, []).append(e)

    only_v1 = []
    for k, evts in v1_keys.items():
        v2_count = len(v2_keys.get(k, []))
        for i, e in enumerate(evts):
            if i >= v2_count:
                only_v1.append(e)

    only_v2 = []
    for k, evts in v2_keys.items():
        v1_count = len(v1_keys.get(k, []))
        for i, e in enumerate(evts):
            if i >= v1_count:
                only_v2.append(e)

    return v1, v2, only_v1, only_v2


def fmt_event(e):
    """Short event string for display."""
    loc = normalize_location(e.get("location", ""))
    name = e["name"]
    return f"{e['date']} {e['start'].strftime('%H:%M')}-{e['end'].strftime('%H:%M') if e.get('end') else '?'} {name} @ {loc}"


def fmt_event_v2(e):
    loc = e.get("location", "")
    return f"{e['date']} {e['start'].strftime('%H:%M')}-{e['end'].strftime('%H:%M') if e.get('end') else '?'} {e['name']} @ {loc}"


def main():
    students = [
        # Year 1
        ("yotam", "Year 1"),
        ("buddy", "Year 1"),
        ("hazel", "Year 1"),
        # Year 2
        ("mia", "Year 2"),
        ("holly", "Year 2"),
        ("bee", "Year 2"),
        # Year 3
        ("charlie", "Year 3"),
        ("joanna", "Year 3"),
        ("kitty", "Year 3"),
    ]

    # Run v2 for all weeks 1+2 once
    xlsx_files = [
        Path("incoming/Term 1a Week 1 2026.xlsx"),
        Path("incoming/Term 1a Weeks 2 2026.xlsx"),
    ]

    print("Running v2 pipeline on weeks 1+2...")
    all_v2 = {}
    for xf in xlsx_files:
        events = process_file(xf, weeks=None, out_dir=None, student=None)
        for name, evts in events.items():
            all_v2.setdefault(name, []).extend(evts)

    for name in all_v2:
        all_v2[name] = dedup_events(all_v2[name])

    total_discrepancies = 0
    total_v1_events = 0
    total_v2_events = 0

    for name, year in students:
        v1_path = Path(f"/tmp/{name}_v1.ics")
        if not v1_path.exists():
            print(f"\n{'='*60}")
            print(f"{name.upper()} ({year}): v1 feed not found, skipping")
            continue

        v1_text = v1_path.read_text()
        v2 = all_v2.get(name, [])

        v1_events, v2_events, only_v1, only_v2 = compare_student(name, v1_text, v2)

        total_v1_events += len(v1_events)
        total_v2_events += len(v2_events)
        n_disc = len(only_v1) + len(only_v2)
        total_discrepancies += n_disc

        status = "OK" if n_disc == 0 else f"{n_disc} DISCREPANCIES"
        print(f"\n{'='*60}")
        print(f"{name.upper()} ({year}): v1={len(v1_events)} v2={len(v2_events)} [{status}]")

        if only_v1:
            print(f"\n  MISSING from v2 ({len(only_v1)}):")
            for e in sorted(only_v1, key=lambda x: x["start"]):
                print(f"    {fmt_event(e)}")

        if only_v2:
            print(f"\n  EXTRA in v2 ({len(only_v2)}):")
            for e in sorted(only_v2, key=lambda x: x["start"]):
                print(f"    {fmt_event_v2(e)}")

    print(f"\n{'='*60}")
    print(f"TOTAL: {total_v1_events} v1 events, {total_v2_events} v2 events, {total_discrepancies} discrepancies")
    return total_discrepancies


if __name__ == "__main__":
    sys.exit(0 if main() == 0 else 1)
