"""Build per-space ICS feeds from day-sheet blocks.

Spaces are simple: every block with content occupies its room, no student
matching needed. Gym Bay 1 + Gym Bay 2 collapse to "Gym"; "Also Batcave"
merges with "Batcave".
"""

import datetime as dt
import hashlib
import re
from pathlib import Path

from .feed_gen import slugify


SPACE_ALIASES = {
    "gym bay 1": "Gym",
    "gym bay 2": "Gym",
    "also batcave": "Batcave",
    "batcave": "Batcave",
}


def canonical_space(location):
    """'Gym Bay 1' -> 'Gym', 'Studio 5 (12-3.45)' -> 'Studio 5'."""
    loc = re.sub(r"\s*\(.*?\)\s*$", "", (location or "")).strip()
    loc = " ".join(loc.split())
    if not loc:
        return None
    return SPACE_ALIASES.get(loc.lower(), loc)


def list_spaces(day_sheets):
    """Canonical bookable spaces found across the given day sheets."""
    from .day_classify import extract_blocks, parse_legend, _merge_map

    # Get legend from first day sheet's workbook.
    legend = None
    if day_sheets:
        ws0 = day_sheets[0][1]
        legend = parse_legend(ws0.parent)

    spaces = set()
    for name, ws in day_sheets:
        blocks = extract_blocks(ws, wb=ws.parent, legend=legend)
        for b in blocks:
            cs = canonical_space(b["location"])
            if cs:
                spaces.add(cs)
    return sorted(spaces)


def build_space_events(day_sheets, weeks_to_cover=None):
    """Build per-space event lists from day-sheet blocks.

    Returns dict {canonical_space: [event_dict, ...]}
    """
    from .day_classify import extract_blocks, parse_legend, _day_from_sheetname
    from .event_creator import week_monday, _teachers

    if weeks_to_cover is None:
        weeks_to_cover = list(range(1, 37))

    legend = None
    if day_sheets:
        legend = parse_legend(day_sheets[0][1].parent)

    space_events = {}  # {canonical_space: [event, ...]}

    for sheet_name, ws in day_sheets:
        weekday, day_name = _day_from_sheetname(sheet_name)
        if weekday is None:
            continue

        blocks = extract_blocks(ws, wb=ws.parent, legend=legend)

        for b in blocks:
            cs = canonical_space(b["location"])
            if not cs:
                continue

            texts = b["texts"]
            if not texts:
                continue

            # Filter out empty/closed blocks.
            content_lower = " ".join(texts).lower()
            if "closed" in content_lower or "student training ends" in content_lower:
                continue

            (sh, sm), (eh, em) = b["start_time"], b["end_time"]
            teachers = _teachers(texts)
            name = _space_event_name(texts)

            # Determine weeks this block applies to.
            # For spaces, we include all weeks unless we can infer otherwise.
            # Simple approach: include all weeks_to_cover for now.
            applicable_weeks = list(weeks_to_cover)

            for week_num in applicable_weeks:
                monday = week_monday(week_num)
                event_date = monday + dt.timedelta(days=weekday)
                start = dt.datetime(event_date.year, event_date.month, event_date.day, sh, sm)
                end = dt.datetime(event_date.year, event_date.month, event_date.day, eh, em)
                if end <= start:
                    start += dt.timedelta(hours=12)
                    end += dt.timedelta(hours=12)

                event = {
                    "date": event_date,
                    "start": start,
                    "end": end,
                    "name": name,
                    "location": b["location"],
                    "subject": name,
                    "teachers": teachers,
                    "weeks": applicable_weeks,
                    "week_num": week_num,
                }

                space_events.setdefault(cs, []).append(event)

    # Dedup within each space: same start+end+name -> merge locations.
    for cs in space_events:
        space_events[cs] = _dedup_space_events(space_events[cs])

    return space_events


def _space_event_name(texts):
    """Produce a human-readable name for a space booking from block texts."""
    # Use the first meaningful text as the name, cleaned up.
    skip = {"closed", "student training ends", "///"}
    for t in texts:
        tl = t.strip().lower()
        if tl in skip or not tl:
            continue
        # Clean up: remove time-like patterns, take first part.
        t = t.strip()
        # Remove leading/trailing pipes and clean.
        t = re.sub(r"^\|+\s*", "", t)
        t = re.sub(r"\s*\|+$", "", t)
        if t:
            return t
    return "Unknown"


def _dedup_space_events(events):
    """De-duplicate events with same date+time+name, merging locations."""
    merged = {}
    for e in sorted(events, key=lambda e: e["start"]):
        k = (e["start"], e["end"], e["name"])
        if k not in merged:
            merged[k] = dict(e)
        else:
            prev = merged[k]
            locs = sorted(set(prev["location"].split(" + ") + [e["location"]]))
            prev["location"] = " + ".join(locs)
            for t in e.get("teachers", []):
                if t not in prev.get("teachers", []):
                    prev.setdefault("teachers", []).append(t)
    return sorted(merged.values(), key=lambda e: e["start"])


def make_space_uid(date, start, end, subject_key, name):
    """Deterministic UID for space bookings."""
    raw = (f"{date.isoformat()}|{start:%H:%M}|{end:%H:%M}|"
           f"{subject_key or 'unknown'}|{name}")
    return f"{hashlib.sha1(raw.encode()).hexdigest()[:16]}@circomedia"


def to_space_ics(events, space_name):
    """Generate RFC 5545 .ics content for a space schedule."""
    from .feed_gen import fold_ics_line, _subject_key

    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//circomedia-timetable//EN",
        f"X-WR-CALNAME:Circomedia - {space_name}",
        "REFRESH-INTERVAL:PT30M",
        "X-PUBLISHED-TTL:PT30M",
        "BEGIN:VTIMEZONE",
        "TZID:Europe/London",
        "BEGIN:STANDARD",
        "DTSTART:19701025T020000",
        "RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10",
        "TZOFFSETFROM:+0100",
        "TZOFFSETTO:+0000",
        "TZNAME:GMT",
        "END:STANDARD",
        "BEGIN:DAYLIGHT",
        "DTSTART:19700329T010000",
        "RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=3",
        "TZOFFSETFROM:+0000",
        "TZOFFSETTO:+0100",
        "TZNAME:BST",
        "END:DAYLIGHT",
        "END:VTIMEZONE",
    ]
    for e in events:
        uid = make_space_uid(e["date"], e["start"], e["end"],
                            _subject_key(e.get("subject")), e["name"])
        desc = ""
        if e.get("teachers"):
            desc += "Teacher: " + ", ".join(e["teachers"]) + "\\n"
        desc += f"Subject: {e.get('subject', 'Unknown')}"
        loc = e["location"]
        if loc == "Gym Bay 1 + Gym Bay 2":
            loc = "Gym"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"LAST-MODIFIED:{now}",
            f"SEQUENCE:0",
            f"DTSTART;TZID=Europe/London:{e['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/London:{e['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{e['name']}",
            f"LOCATION:{loc}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    folded = []
    for line in lines:
        folded.extend(fold_ics_line(line))
    return "\r\n".join(folded) + "\r\n"


def generate_space_feeds(space_events, output_dir):
    """Generate per-space .ics files.

    Parameters
    ----------
    space_events : dict {canonical_space: [event, ...]}
    output_dir : str or Path

    Returns
    -------
    int : number of feed files written
    """
    output_dir = Path(output_dir)
    spaces_dir = output_dir / "spaces"
    spaces_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for space_name, events in sorted(space_events.items()):
        if not events:
            continue

        ics = to_space_ics(events, space_name)
        slug = slugify(space_name)
        out_path = spaces_dir / f"{slug}.ics"
        out_path.write_text(ics)
        print(f"  {space_name} ({len(events)} events) -> feeds/spaces/{slug}.ics")
        count += 1

    return count
