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
       - names them explicitly (pre-dash segment only: in 'Tan - Jonathan'
         the teacher after the dash does not count; pure teacher lists like
         'Lisa, Ethan, Chané' never count), OR
       - says All Yr N / All Nth years matching their year, or generic All
         years with a yellow header, OR
       - names a group they belong to for that block's subject.
     Year-1 yellow fill (FFFFFF00) marks Year-1 blocks; explicit name
     matches count regardless of fill.
  3. Write Apple/Google-Calendar-compatible .ics (RFC 5545 line folding). Event name is the class
      ('Core Skills - Tumbling', plus ' (Group N)' suffix when
      INCLUDE_GROUP_IN_TITLE and the event is group-specific). Location is the
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

# When True, group-specific events carry the student's group in the title,
# e.g. 'Acro (Group B)', 'Core Skills - Tumbling (Group 1)',
# 'Conditioning (Group D)'. Non-group events (All-years, named 1-to-1s)
# keep the bare class name. UIDs are unaffected (they key on subject_key).
INCLUDE_GROUP_IN_TITLE = True

TERM_WEEK1_MONDAY = dt.date(2026, 9, 14)  # Week 1 Monday (dd-mm-yyyy 14-09-2026)
WEEK_RE = re.compile(r"weeks?\s*(\d+)", re.I)

TIME_RANGE_RE = re.compile(r"(\d{1,2})\s*[.:]\s*(\d{2})\s*[-\u2013]\s*(\d{1,2})\s*[.:]\s*(\d{2})")
GROUP_RE = re.compile(r"Group\s*([ABCD123abcd123])\b")
# Private lessons are written '<student> - <teacher>' ('Tan - Jonathan',
# 'Oakley & Joanna - Nicky'). Only the pre-dash segment can name a student;
# split only on dashes with whitespace beside them so hyphenated surnames
# ('Mark Parfitt-Jones') stay intact.
DASH_SPLIT_RE = re.compile(r"\s+-\s*|\s*-\s+|[\u2013\u2014]")
# A booked 1-to-1 session: 'Charlie (Creative)'. Any other person named
# in the same block ('Charlie (Creative)' + 'Jonathan') is the tutor.
OWNER_RE = re.compile(r"^[^()]{1,25} \([A-Za-z]+\)$")
# A pure list of 2+ person names ('Lisa, Ethan, Chané', 'Nicky and Janine').
NAME_LIKE_RE = re.compile(r"^[A-Z\u00c0-\u00de][a-z\u00e0-\u00fe]+(?: [A-Z\u00c0-\u00de][a-z\u00e0-\u00fe]+)?$")
TEACHER_LIST_SPLIT_RE = re.compile(r",|\band\b")
# 'All Yr 1', 'All 1st years', 'All 2nd Year', 'All 3rd years' -> year number.
ALL_YEAR_RE = re.compile(
    r"\ball\s*(?:(?:yr|year)s?\s*)?([123])(?:\s*(?:st|nd|rd|th))?(?:\s*years?)?\b", re.I)
ALL_YEARS_GENERIC_RE = re.compile(r"\ball\s+years\b", re.I)
DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def make_uid(date, start, end, subject_key):
    """Deterministic UID: same logical class always gets the same UID."""
    raw = f"{date.isoformat()}|{start:%H:%M}|{end:%H:%M}|{subject_key or 'unknown'}"
    return f"{hashlib.sha1(raw.encode()).hexdigest()[:16]}@circomedia"


def fold_ics_line(line):
    """RFC 5545 §3.1 folding: max 75 octets per line, continuations start
    with a space. Never splits a multibyte UTF-8 character. Apple tolerates
    unfolded lines; Google's importer does not."""
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
    return [parts[0].decode("utf-8")] + [" " + p.decode("utf-8")
                                         for p in parts[1:]]


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


def slugify(name):
    """'Dee Dee' -> 'dee-dee'. URL-safe, deterministic, lowercase."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "student"


def display_name(lower_name):
    """Recover a display form from a lowercased roster key."""
    return " ".join(
        w[:1].upper() + w[1:] if w else w
        for w in lower_name.split(" ")
    )


def parse_roster(wb):
    """Canonical roster from raw year sheets (no Core Skills merge).

    Returns {key: {"name": display, "year": n, "keys": [variant keys]}}.
    Spelling variants ('farrah' vs 'farrah (minor)', '(james)' vs 'james')
    merge into one entry; schedule annotations ('friday wk5') are dropped.
    Core-skills-only names (spelling variants) fall back to year 1;
    lookup_merged resolves them at build time anyway.
    """
    by_norm = {}
    for n in (1, 2, 3):
        for key in parse_year_sheet(wb, n):
            if JUNK_ROSTER_RE.search(key):
                continue
            norm = roster_norm(key) or key.lower()
            e = by_norm.setdefault(norm, {"keys": [], "year": n})
            if key not in e["keys"]:
                e["keys"].append(key)
            e["year"] = min(e["year"], n)
    core = parse_core_skills(wb)
    for key in core:
        if JUNK_ROSTER_RE.search(key):
            continue
        norm = roster_norm(key) or key.lower()
        e = by_norm.setdefault(norm, {"keys": [], "year": 1})
        if key not in e["keys"]:
            e["keys"].append(key)
    out = {}
    for norm, e in by_norm.items():
        # Prefer the cleanest key for identity ('farrah' over
        # 'farrah (minor)'; 'dee dee' over 'deedee').
        pref = sorted(e["keys"], key=lambda k: ("(" in k, " " not in k, len(k)))[0]
        clean = re.sub(r"\(.*?\)", "", pref).strip() or pref
        out[pref] = {"name": display_name(clean.lower()),
                     "year": e["year"], "keys": sorted(e["keys"])}
    return out


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


def lookup_merged(maps, keys, start_year=1):
    """lookup_student across spelling variants ('farrah', 'farrah (minor)').

    Same-year variants merge; first year (from start_year) with any match
    wins. Identical to lookup_student for a single key.
    """
    for n in range(start_year, 4):
        me = {}
        for k in keys:
            for subj, grp in maps.get(n, {}).get(k.lower(), {}).items():
                me.setdefault(subj, grp)
        if me:
            return me, n
    return {}, None


# Group-sheet annotations that are schedules, not people.
JUNK_ROSTER_RE = re.compile(
    r"monday|tuesday|wednesday|thursday|friday|\bweeks?\b|\bwk\b|\?", re.I)


def roster_norm(key):
    """Identity for entity resolution: 'Farrah (minor)' -> 'farrah'."""
    k = key.strip()
    if k.startswith("(") and k.endswith(")"):
        k = k[1:-1]
    k = re.sub(r"\(.*?\)", "", k)
    return re.sub(r"[^a-z0-9]", "", k.lower())


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


def pre_dash(text):
    """Student-name segment of a block text: 'Tan - Jonathan' -> 'Tan'."""
    return DASH_SPLIT_RE.split(text, maxsplit=1)[0]


def is_teacher_list(text):
    """True for pure multi-name lists ('Lisa, Ethan, Chané')."""
    parts = [p.strip() for p in TEACHER_LIST_SPLIT_RE.split(text) if p.strip()]
    return len(parts) >= 2 and all(NAME_LIKE_RE.match(p) for p in parts)


def all_years_year(texts):
    """Year N from 'All Yr N / All Nth years' texts, 'all', or None."""
    for t in texts:
        m = ALL_YEAR_RE.search(t)
        if m:
            return int(m.group(1))
    for t in texts:
        if ALL_YEARS_GENERIC_RE.search(t):
            return "all"
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


def display_event_name(texts, subject_key, groups, groups_in_block):
    """Class name, with ' (Group X)' suffix when the event is group-specific.

    Controlled by INCLUDE_GROUP_IN_TITLE. Uses the student's own group label
    for the block's subject (e.g. 'Group B'); non-group events (All-years,
    named 1-to-1s with no group in the block) keep the bare name.
    """
    base = event_name(texts)
    if not INCLUDE_GROUP_IN_TITLE or not subject_key:
        return base
    mine = (groups or {}).get(subject_key, "")
    if not mine:
        return base
    joined = " | ".join(texts).lower()
    group_specific = bool(groups_in_block) or (mine.lower() in joined)
    if not group_specific:
        return base
    if mine.lower() in base.lower():
        return base
    return f"{base} ({mine})"


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
                        start_year=1, filename=None, groups=None,
                        matched_year=None, aliases=()):
    if groups is None:
        maps = parse_groups(wb)
        groups, matched_year = lookup_student(maps, name, start_year)
    me = groups
    # Name candidates for explicit matches: variants plus their
    # paren-stripped bases ('lewis (nicky)' -> 'lewis').
    candidates = {name.lower()}
    for a in aliases:
        candidates.add(a.lower())
        base = re.sub(r"\(.*?\)", "", a).strip()
        if base:
            candidates.add(base.lower())
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
            # need some Year-1 signal: yellow header, All-years, group, or explicit name
            groups_in_block = set()
            for t in texts:
                for gm in GROUP_RE.finditer(t):
                    groups_in_block.add(f"Group {gm.group(1).upper()}")
            taught_class = bool(groups_in_block) and bool(skey)
            owners = [t for t in texts if OWNER_RE.match(t)]
            named = False
            for t in (owners or texts):
                seg = pre_dash(t)
                if taught_class and is_teacher_list(seg):
                    continue  # 'Lisa, Ethan, Chané': teachers, not students
                if any(re.search(rf"\b{re.escape(c)}\b", seg, re.I)
                       for c in candidates):
                    named = True
                    break
            student_year = matched_year or start_year
            disregards_year = all_years_year(texts)
            allyear = (disregards_year == student_year
                       or (disregards_year == "all" and b["header_yellow"]))
            if not (b["header_yellow"] or allyear or groups_in_block or named):
                continue
            attend = False
            reason = ""
            if named:
                attend, reason = True, "named explicitly"
            elif allyear:
                attend, reason = True, f"all year {disregards_year}"
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
                "name": display_event_name(texts, skey, me, groups_in_block),
                "location": b["location"],
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
    folded = []
    for line in lines:
        folded.extend(fold_ics_line(line))
    return "\r\n".join(folded) + "\r\n"


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
