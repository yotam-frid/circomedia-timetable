#!/usr/bin/env python3
"""Extract one student's lessons from a Circomedia weekly timetable and write an .ics file.

Why a script (not an agent skill):
  - deterministic, repeatable file transform: xlsx -> ics
  - no judgement needed once group mapping + yellow=Year1 rules are encoded
  - runnable in CI / cron for each new weekly file

Procedure:
  1. Read the week's group sheets -> who is in which group.
     Args: student name (--name, default Yotam, case-insensitive) and
     study year (--year, default 1). If the name is not found in that
     year's groups sheet, fall forward to Year 2, then Year 3.
     Sources: 'Year N Groups' sheets (Year 1 has Acro, Aerial,
     Manipulation, Aerial Conditioning, Conditioning, Physical Theatre,
     Context 1, Devising, Movement; Years 2/3 use the same generic
     layout) plus 'Core Skills Groups' (Group 1/2/3, shared).
  2. Read every day sheet (Mon-Fri). For each location column, split into
     time blocks anchored by time-range headers (e.g. '8.45 - 10.00').
     A block belongs to the student if it:
       - names them explicitly, OR
       - says All Yr N / All 1st years (matching their year), OR
       - names a group they belong to for that block's subject.
     Year-1 yellow fill (FFFFFF00) marks Year-1 blocks; explicit name
     matches count regardless of fill.
  3. Write Apple-Calendar-compatible .ics. Event name is the class
     ('Core Skills - Tumbling', no group in name). Location is the
     room (Gym Bay 1, Classroom, South Wing...). Teacher/group go in DESCRIPTION.

Week dating: the first Monday of term is 14-09-2026 (Week 1). The week
number is read from the input filename ('Week 1', 'Weeks 2', ...), so
Week 2 -> Monday 21-09-2026, etc. Pass --monday YYYY-MM-DD to override.

Usage:
  python3 timetable_to_ics.py INPUT.xlsx --name Yotam --year 1
  (Monday auto-derived from filename; output defaults to INPUT.ics)
"""

import argparse
import datetime as dt
import re
import sys
import hashlib
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("Need openpyxl: pip install openpyxl")

YEAR1_YELLOW = "FFFFFF00"

TERM_WEEK1_MONDAY = dt.date(2026, 9, 14)  # Week 1 Monday (dd-mm-yyyy 14-09-2026)
WEEK_RE = re.compile(r"weeks?\s*(\d+)", re.I)

TIME_RANGE_RE = re.compile(r"(\d{1,2})\s*[.:]\s*(\d{2})\s*[-\u2013]\s*(\d{1,2})\s*[.:]\s*(\d{2})")
GROUP_RE = re.compile(r"Group\s*([ABCD123abcd123])\b")
DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def make_uid(date, start, end, subject_key):
    """Deterministic UID: same logical class always gets the same UID."""
    raw = f"{date.isoformat()}|{start:%H:%M}|{end:%H:%M}|{subject_key or 'unknown'}"
    return f"{hashlib.sha1(raw.encode()).hexdigest()[:16]}@circomedia"


def fill_rgb(cell):
    try:
        for attr in ("fgColor", "bgColor"):
            c = getattr(cell.fill, attr, None)
            if c is not None and getattr(c, "rgb", None) not in (None, "00000000"):
                return str(c.rgb)
    except Exception:
        pass
    return "00000000"


def val(ws, r, c, merged_map):
    """Value resolving merged cells: merged_map[(r,c)] -> (vr,vc) top-left."""
    vr, vc = merged_map.get((r, c), (r, c))
    return ws.cell(row=vr, column=vc).value


def cell_rgb(ws, r, c, merged_map):
    vr, vc = merged_map.get((r, c), (r, c))
    return fill_rgb(ws.cell(row=vr, column=vc))


def merged_map_of(ws):
    m = {}
    for rng in ws.merged_cells.ranges:
        tl = (rng.min_row, rng.min_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                m[(r, c)] = tl
    return m


def norm_subject_key(s):
    s = (s or "").lower()
    if "aerial conditioning" in s:
        return "aerial_conditioning"
    if "physical theatre" in s or "physical theater" in s:
        return "physical_theatre"
    if "core skill" in s:
        return "core_skills"
    if "context" in s:
        return "context1"
    if "conditioning" in s:
        return "conditioning"
    if "acro" in s:
        return "acro"
    if "aerial" in s:
        return "aerial"
    if "manipulation" in s:
        return "manipulation"
    if "creative project" in s:
        return "creative_project"
    if "devising" in s:
        return "devising"
    if "movement" in s:
        return "movement"
    if "dance" in s:
        return "dance"
    return None


def _year_sheet_name(wb, n):
    for s in wb.sheetnames:
        if s.strip().lower() == f"year {n} groups":
            return s
    return None


def parse_core_skills(wb):
    """Shared Core Skills map: {name_lower: {'core_skills': 'Group N'}}."""
    out = {}
    for sheet in ("Core Skills Groups",):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        mm = merged_map_of(ws)
        col_group = {4: "Group 1", 6: "Group 2", 8: "Group 3"}
        for r in range(5, ws.max_row + 1):
            for c, g in col_group.items():
                v = val(ws, r, c, mm)
                if v and str(v).strip() and not str(v).strip().isdigit():
                    out.setdefault(str(v).strip().lower(), {})["core_skills"] = g
    return out


def parse_year_sheet(wb, n):
    """Generic parser for 'Year N Groups': row3 subjects, row5 group labels.

    Returns {name_lower: {subject_key_or_raw: label}}. Subject keys use
    norm_subject_key when recognisable, else the raw header text. Group
    labels are kept raw ('Group 2', 'Group D', 'Major', 'Minors',
    'Billie', ...) plus normalised for Group X.
    """
    sheet = _year_sheet_name(wb, n)
    if not sheet:
        return {}
    ws = wb[sheet]
    mm = merged_map_of(ws)
    subj_by_col, last = {}, None
    for c in range(1, ws.max_column + 1):
        v = val(ws, 3, c, mm)
        if v and str(v).strip():
            last = str(v).strip()
        subj_by_col[c] = last
    group_by_col = {c: val(ws, 5, c, mm) for c in range(1, ws.max_column + 1)}
    people = {}
    for r in range(6, ws.max_row + 1):
        for c in range(1, ws.max_column + 1):
            v = val(ws, r, c, mm)
            g = group_by_col.get(c)
            s = subj_by_col.get(c)
            if not v or not g or not s:
                continue
            name = str(v).strip()
            glab = str(g).strip()
            if not name or name.isdigit() or not glab or glab.isdigit():
                continue
            if len(name) > 25 or "need" in name.lower():
                continue
            if name.lower() == "abigail" and n == 1 and norm_subject_key(s) in ("context1", "devising", "movement"):
                continue  # tutor-group label, not a student (Year 1)
            key = norm_subject_key(s) or s.strip().lower()
            if n == 1 and key in ("context1", "devising", "movement") and not GROUP_RE.search(glab):
                continue  # tutor-group label (e.g. 'Abigail'), not a student group
            gm = GROUP_RE.search(glab)
            glabel = f"Group {gm.group(1).upper()}" if gm else glab
            people.setdefault(name.lower(), {})[key] = glabel
    return people


def parse_groups(wb, start_year=1):
    """Return ({1: map, 2: map, 3: map}) with Core Skills merged into each."""
    core = parse_core_skills(wb)
    maps = {}
    for n in (1, 2, 3):
        m = parse_year_sheet(wb, n)
        for k, v in core.items():
            m.setdefault(k, {}).setdefault("core_skills", v["core_skills"])
        maps[n] = m
    return maps


def lookup_student(maps, name, start_year=1):
    """Find the student from start_year forward (1 -> 2 -> 3).

    Returns (groups_dict, matched_year or None).
    """
    for n in range(start_year, 4):
        m = maps.get(n, {})
        if name.lower() in m and m[name.lower()]:
            return m[name.lower()], n
    return {}, None


def detect_blocks(ws):
    """Split each location column into time blocks.

    Returns (locations, blocks): locations {col: name}, blocks list of
    dict(col, location, start_row, end_row, time_text).
    """
    mm = merged_map_of(ws)
    locations = {}
    for c in range(2, ws.max_column + 1):
        v = val(ws, 2, c, mm)
        if v and str(v).strip() and "first aider" not in str(v).lower() and str(v).strip().lower() != "key":
            locations[c] = " ".join(str(v).split())
    # find time-range headers per column (yellow or any, but record fill)
    headers = []  # (col, row, text, is_yellow)
    for c in locations:
        for r in range(4, min(ws.max_row + 1, 45)):
            v = val(ws, r, c, mm)
            if v and TIME_RANGE_RE.search(str(v)):
                rgb = cell_rgb(ws, r, c, mm)
                headers.append((c, r, " ".join(str(v).split()), rgb == YEAR1_YELLOW))
    headers.sort()
    # Repair spreadsheet typos where a time header was copy-pasted: if a
    # header duplicates an earlier header in the SAME column (same time text)
    # but disagrees with the consensus of OTHER locations' headers on the
    # SAME row, trust the row consensus. Concrete case: Week 1 Thursday
    # South Wing row 14 says '10.15 -11.15' (duplicate of its row-10 block)
    # while Gym Bay 1/2 on row 14 say '11.15 - 12.15' -- the second Aerial
    # Conditioning block is really 11.15-12.15, back-to-back with the
    # 10.15 Acro block, not overlapping it.
    # Count how many times each parsed time occurs per column, so we can
    # spot a header that duplicates an earlier block in the same column.
    col_time_counts = {}
    for (c, r, t, y) in headers:
        p = block_times(t)
        col_time_counts[(c, p)] = col_time_counts.get((c, p), 0) + 1
    row_times = {}
    for idx, (c, r, t, y) in enumerate(headers):
        row_times.setdefault(r, []).append(idx)
    for r, idxs in row_times.items():
        if len(idxs) < 2:
            continue
        parsed = [(i, block_times(headers[i][2])) for i in idxs]
        if any(p is None for _, p in parsed):
            continue
        counts = {}
        for _, p in parsed:
            counts[p] = counts.get(p, 0) + 1
        majority = max(counts, key=counts.get)
        if counts[majority] < 2:
            continue
        for i, p in parsed:
            c, rr, t, y = headers[i]
            # only override genuine duplicates of an earlier same-col header
            if p != majority and col_time_counts.get((c, p), 0) > 1:
                fixed = next(headers[j][2] for j, q in parsed if q == majority)
                print(f"WARNING:{ws.title} row {rr} {locations[c]} header "
                      f"{t!r} duplicates earlier block; using row consensus "
                      f"{fixed!r}",
                      file=sys.stderr)
                headers[i] = (c, rr, fixed, y)
    blocks = []
    for i, (c, r, t, y) in enumerate(headers):
        # end = next header in same column, else next 'Student Training Ends' / 'Closed' / sheet end
        end = ws.max_row + 1
        for (c2, r2, _, _) in headers:
            if c2 == c and r2 > r:
                end = r2
                break
        else:
            for rr in range(r + 1, min(ws.max_row + 1, 42)):
                v = val(ws, rr, c, mm)
                if v and ("student training ends" in str(v).lower() or str(v).strip().lower() == "closed"):
                    end = rr
                    break
        blocks.append({"col": c, "location": locations[c], "start_row": r,
                       "end_row": end, "time_text": t, "header_yellow": y})
    return locations, blocks


def block_texts(ws, block):
    mm = merged_map_of(ws)
    texts = []
    for r in range(block["start_row"], min(block["end_row"], 42)):
        v = val(ws, r, block["col"], mm)
        if v and str(v).strip() and not TIME_RANGE_RE.search(str(v)):
            t = " ".join(str(v).split())
            if t not in texts:
                texts.append(t)
    return texts


def block_subject_key(texts):
    joined = " | ".join(texts).lower()
    for key in ("aerial conditioning", "physical theatre", "core skills", "context",
                "conditioning", "acro", "aerial", "manipulation",
                "creative project", "devising", "movement", "dance"):
        if key in joined:
            return norm_subject_key(key)
    return None


def event_name(texts):
    j = " | ".join(texts)
    jl = j.lower()
    base = None
    sub = None
    if "core skill" in jl:
        base = "Core Skills"
        if "tumbling" in jl:
            sub = "Tumbling"
        elif "handstand" in jl:
            sub = "Handstands"
        elif "professional" in jl or "member" in jl:
            sub = "Professional Members"
    elif "context" in jl:
        base = "Context 1 - Lecture" if "lecture" in jl else "Context 1"
    elif "aerial conditioning" in jl:
        base = "Aerial Conditioning"
    elif "conditioning" in jl:
        base = "Conditioning"
    elif "acro" in jl:
        base = "Acro"
    elif "aerial" in jl:
        base = "Aerial"
    elif "manipulation" in jl:
        base = "Manipulation"
    elif "physical theatre" in jl:
        base = "Physical Theatre"
    elif "creative project" in jl:
        base = "Creative Project"
    elif "devising" in jl:
        base = "Devising"
    elif "movement" in jl:
        base = "Movement"
    elif "dance" in jl:
        base = "Dance"
    else:
        # fallback: first non-teacher/group text
        for t in texts:
            tl = t.lower()
            if ("group" in tl or "yr 1" in tl or "1st year" in tl or "all year" in tl
                    or "heather" in tl or "mark parfitt" in tl or TIME_RANGE_RE.search(t)):
                continue
            base = t
            break
        base = base or (texts[0] if texts else "Class")
    if sub:
        return f"{base} - {sub}"
    return base


def teachers_of(texts):
    out = []
    skip = ("group", "yr 1", "1st year", "all year", "core skill", "tumbling",
            "handstand", "professional", "member", "context", "conditioning",
            "acro", "aerial", "manipulation", "physical theatre", "creative",
            "devising", "movement", "dance", "lecture", "majors", "minors",
            "student training ends", "closed", "staff in", "ceo presentation",
            "registration")
    for t in texts:
        tl = t.lower()
        if TIME_RANGE_RE.search(t):
            continue
        if any(k in tl for k in skip):
            continue
        if "///" in t:
            continue
        if re.search(r"[A-Za-z]", t):
            out.append(t)
    return out


def parse_hm(h, m):
    return int(h), int(m)


def block_times(time_text, afternoon_hint=False):
    """'8.45 - 10.00' -> ((8,45),(10,0)). Handles pm rollover: 12.00-1.30 -> 12:00-13:30."""
    m = TIME_RANGE_RE.search(time_text)
    if not m:
        return None
    sh, sm, eh, em = map(int, m.groups())
    sh, sm = parse_hm(sh, sm)
    eh, em = parse_hm(eh, em)
    if sh < 8:
        sh += 12
    if eh < 8 or eh < sh or (eh == sh and em <= sm):
        # e.g. 12.00-1.30, 1.45-3.15
        if eh != 12:
            eh += 12
    if afternoon_hint and sh < 12 and sh != 12:
        sh += 12
        if eh < 12:
            eh += 12
    return (sh, sm), (eh, em)


def monday_from_filename(path):
    """Week 1 Monday is 14-09-2026; add 7 days per week number in filename."""
    m = WEEK_RE.search(Path(path).stem)
    if not m:
        return None
    return TERM_WEEK1_MONDAY + dt.timedelta(weeks=int(m.group(1)) - 1)


def extract_for_student(wb, name, monday=None, cal_year=2026, cal_month=9,
                        start_year=1, filename=None):
    maps = parse_groups(wb)
    me, matched_year = lookup_student(maps, name, start_year)
    if monday is None and filename:
        monday = monday_from_filename(filename)
    day_sheets = [s for s in wb.sheetnames
                  if any(s.strip().lower().startswith(d) for d in DAY_ORDER)]
    # order Mon..Fri
    day_sheets.sort(key=lambda s: next(i for i, d in enumerate(DAY_ORDER)
                                       if s.strip().lower().startswith(d)))
    events = []
    for s in day_sheets:
        ws = wb[s]
        weekday = next(i for i, d in enumerate(DAY_ORDER) if s.strip().lower().startswith(d))
        dm = re.search(r"(\d{1,2})", s)
        if monday:
            date = monday + dt.timedelta(days=weekday)
        elif dm:
            date = dt.date(cal_year, cal_month, int(dm.group(1)))
        else:
            date = dt.date(cal_year, cal_month, 1) + dt.timedelta(days=weekday)
        _, blocks = detect_blocks(ws)
        for b in blocks:
            texts = block_texts(ws, b)
            if not texts:
                continue
            skey = block_subject_key(texts)
            # need some Year-1 signal: yellow header, All Yr1, group, or explicit name
            named = any(re.search(rf"\b{re.escape(name)}\b", t, re.I) for t in texts)
            yr1_only = any(re.search(r"all\s*(yr|year)?\s*1|all\s*1st\s*years?", t, re.I)
                           for t in texts)
            all_years_generic = any(re.search(r"\ball\s*years\b", t, re.I) for t in texts)
            # Generic "All years" (e.g. Friday self-led warm-up for Yr 2/3) only
            # counts when the block header itself is Year-1 yellow. "All Yr 1"
            # always counts.
            all1 = yr1_only or (all_years_generic and b["header_yellow"])
            groups_in_block = set()
            for t in texts:
                for gm in GROUP_RE.finditer(t):
                    groups_in_block.add(f"Group {gm.group(1).upper()}")
            if not (b["header_yellow"] or all1 or groups_in_block or named):
                continue
            attend = False
            reason = ""
            if named:
                attend, reason = True, "named explicitly"
            elif all1:
                attend, reason = True, "all-years block"
            elif groups_in_block:
                mine = me.get(skey, "") if skey else ""
                if skey and mine and mine in groups_in_block:
                    attend, reason = True, f"{skey} {mine}"
                # else: strict reject for Group X mismatches (do NOT fall back
                # to "any group matches", or Core Skills Group 1 would wrongly
                # match an Acro Group 1 block).
                elif skey and mine and not GROUP_RE.search(mine):
                    # Non-'Group X' labels (Year 2/3: 'Major', 'Minors',
                    # teacher-name groups like 'Billie'): match if the label
                    # appears in the block text, e.g. 'Major' in 'Acro Majors'.
                    if mine.lower() in " | ".join(texts).lower():
                        attend, reason = True, f"{skey} {mine}"
            if not attend:
                continue
            # afternoon heuristic from start row (>= ~17 is noon onwards)
            t = block_times(b["time_text"], afternoon_hint=b["start_row"] >= 15)
            if not t:
                continue
            (sh, sm), (eh, em) = t
            start = dt.datetime(date.year, date.month, date.day, sh, sm)
            end = dt.datetime(date.year, date.month, date.day, eh, em)
            if end <= start:
                continue
            teachers = teachers_of(texts)
            events.append({
                "date": date, "day": s.strip(), "start": start, "end": end,
                "name": event_name(texts), "location": b["location"],
                "teachers": teachers, "reason": reason,
                "texts": texts, "subject_key": skey,
            })
    # de-dupe: Gym Bay 1 / Gym Bay 2 are often merged (same block in both
    # columns). Merge events with same date+time+name, combining locations.
    merged = {}
    for e in sorted(events, key=lambda e: e["start"]):
        k = (e["start"], e["end"], e["name"])
        if k not in merged:
            merged[k] = e
        else:
            prev = merged[k]
            locs = sorted(set(prev["location"].split(" + ") + [e["location"]]))
            prev["location"] = " + ".join(locs)
            for t in e["teachers"]:
                if t not in prev["teachers"]:
                    prev["teachers"].append(t)
    uniq = sorted(merged.values(), key=lambda e: e["start"])
    # Impossibility guard: one student can't be in two places at once.
    # Any remaining overlap is a spreadsheet typo or matching bug -- shout
    # loudly instead of silently writing an impossible .ics.
    for prev, cur in zip(uniq, uniq[1:]):
        if cur["start"] < prev["end"] and cur["date"] == prev["date"]:
            print(f"CONFLICT:{cur['date']} {prev['start'].strftime('%H:%M')}-"
                  f"{prev['end'].strftime('%H:%M')} {prev['name']} @ {prev['location']} "
                  f"overlaps {cur['start'].strftime('%H:%M')}-{cur['end'].strftime('%H:%M')} "
                  f"{cur['name']} @ {cur['location']}", file=sys.stderr)
    return uniq, me, matched_year


def to_ics(events, name):
    now = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//circomedia-timetable//EN",
        f"X-WR-CALNAME:Circomedia - {name}",
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
        uid = make_uid(e["date"], e["start"], e["end"], e.get("subject_key"))
        desc = ""
        if e["teachers"]:
            desc += "Teacher: " + ", ".join(e["teachers"]) + "\\n"
        desc += f"Matched: {e['reason']}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{now}",
            f"LAST-MODIFIED:{now}",
            f"SEQUENCE:0",
            f"DTSTART;TZID=Europe/London:{e['start'].strftime('%Y%m%dT%H%M%S')}",
            f"DTEND;TZID=Europe/London:{e['end'].strftime('%Y%m%dT%H%M%S')}",
            f"SUMMARY:{e['name']}",
            f"LOCATION:{e['location']}",
            f"DESCRIPTION:{desc}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    ap = argparse.ArgumentParser(description="Extract a student's lessons to .ics")
    ap.add_argument("file", help="weekly timetable .xlsx")
    ap.add_argument("--name", default="Yotam", help="student first name (default Yotam)")
    ap.add_argument("--monday", default=None, help="Monday date YYYY-MM-DD (default: from filename, Week 1 = 14-09-2026)")
    ap.add_argument("--year", type=int, default=1, choices=(1, 2, 3),
                    help="study year to look up (default 1; falls forward to 2 then 3 if name not found)")
    ap.add_argument("-o", "--out", default=None, help="output .ics (default: input filename with .ics extension)")
    a = ap.parse_args()
    monday = dt.date.fromisoformat(a.monday) if a.monday else monday_from_filename(a.file)
    m = re.search(r"(20\d{2})", a.file)
    cal_year = int(m.group(1)) if m else 2026
    wb = openpyxl.load_workbook(a.file, data_only=True)
    events, me, matched_year = extract_for_student(
        wb, a.name, monday=monday, cal_year=cal_year, cal_month=9,
        start_year=a.year, filename=a.file)
    if me:
        print(f"Groups for {a.name} (Year {matched_year}): {me}")
    else:
        print(f"{a.name} not found in Year {a.year} groups"
              + (" (nor 2/3)" if a.year == 1 else "") + " - explicit/all-years matches only")
    if monday:
        print(f"Monday: {monday.isoformat()} (from {'--monday' if a.monday else 'filename'})")
    for e in events:
        print(f"{e['date']} {e['start'].strftime('%H:%M')}-{e['end'].strftime('%H:%M')} "
              f"{e['name']} @ {e['location']} [{', '.join(e['teachers'])}] ({e['reason']})")
    ics = to_ics(events, a.name)
    out = a.out or str(Path(a.file).with_suffix(".ics"))
    Path(out).write_text(ics)
    print(f"Wrote {len(events)} events -> {out}")


if __name__ == "__main__":
    main()
