"""Generate per-student ICS feeds from v2 event data.

Cross-references student-group assignments with classified events,
deduplicates, and writes RFC 5545-compliant .ics files.
"""

import datetime as dt
import hashlib
import re
from pathlib import Path


def fold_ics_line(line):
    """RFC 5545 §3.1 folding: max 75 octets per line."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    parts = []
    while raw:
        take = 75 if not parts else 74
        if len(raw) <= take:
            parts.append(raw)
            break
        cut = take
        while cut > 0 and raw[cut] & 0xC0 == 0x80:
            cut -= 1
        parts.append(raw[:cut or take])
        raw = raw[cut or take:]
    return [parts[0].decode("utf-8")] + [" " + p.decode("utf-8") for p in parts[1:]]


def make_uid(date, start, end, subject_key):
    """Deterministic UID."""
    raw = f"{date.isoformat()}|{start:%H:%M}|{end:%H:%M}|{subject_key or 'unknown'}"
    return f"{hashlib.sha1(raw.encode()).hexdigest()[:16]}@circomedia"


def _subject_key(subject):
    """Normalize subject to a key for UID stability."""
    return re.sub(r"[^a-z0-9]", "", subject.lower()) if subject else "unknown"


def slugify(name):
    """Normalize a display name for a public feed path."""
    value = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return value or "student"


def _student_groups_key(student_data):
    """Build a set of (subject, group) tuples for a student from v2 group data."""
    keys = set()
    for info in student_data:
        subj = (info.get("subject") or "").lower()
        grp = (info.get("group") or "").lower()
        if subj:
            keys.add((subj, grp))
    return keys


def _event_applies_to_student(event, student_group_keys, student_year):
    """Check if an event should be assigned to a student.

    An event applies if:
    1. target is "All Years" or "All Year N" matching student's year
    2. The student has a matching (subject, group) pair
    3. target is "student_match" and the student was explicitly matched
    """
    target = event.get("target", "")
    subject = (event.get("subject") or "").lower()

    # "All Years" applies to everyone.
    if target == "All Years":
        return True

    # "All Year N" applies to students in that year.
    m = re.match(r"All Year (\d)", target, re.I)
    if m:
        target_year = int(m.group(1))
        return student_year == target_year

    # Group-specific: check if student has one of the event's groups.
    event_groups = event.get("groups", [])
    for grp in event_groups:
        if (subject, grp.lower()) in student_group_keys:
            return True

    # student_match events are pre-assigned in event_creator.
    if target == "student_match":
        return True  # Already filtered by event_creator.

    return False


def filter_events_for_student(events, student_group_data, student_year):
    """Filter events to only those applicable to this student."""
    group_keys = _student_groups_key(student_group_data)
    return [e for e in events if _event_applies_to_student(e, group_keys, student_year)]


def dedup_events(events):
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


def to_ics(events, student_name):
    """Generate RFC 5545 .ics content for a list of events."""
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//circomedia-timetable//EN",
        f"X-WR-CALNAME:Circomedia - {student_name}",
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
        uid = make_uid(e["date"], e["start"], e["end"], _subject_key(e.get("subject")))
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


def generate_feeds(all_events, students_by_year, student_group_data,
                   output_dir, weeks_to_cover=None):
    """Generate per-student .ics files.

    Parameters
    ----------
    all_events : dict {student_lower: [event, ...]}
        Events already assigned to students by event_creator.
    students_by_year : dict {year: [student_names]}
    student_group_data : dict {student_lower: [info, ...]}
        From v2 group_parse.assemble().
    output_dir : str or Path
    weeks_to_cover : list of int, optional
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for year, students in students_by_year.items():
        for student in students:
            s_key = student.lower()
            events = all_events.get(s_key, [])

            # Filter by student's groups.
            group_data = student_group_data.get(s_key, [])
            events = filter_events_for_student(events, group_data, year)

            # De-duplicate.
            events = dedup_events(events)

            if not events:
                continue

            # Write ICS.
            ics = to_ics(events, student)
            slug = slugify(s_key)
            out_path = output_dir / f"{slug}.ics"
            out_path.write_text(ics)
            print(f"  {student} ({len(events)} events) -> {out_path.name}")

    return len(list(output_dir.glob("*.ics")))
