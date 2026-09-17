#!/usr/bin/env python3
"""Extract one student's lessons from a Circomedia weekly timetable and write an .ics file.

Deterministic, repeatable file transform: xlsx -> ics. See AGENTS.md for the
architecture and matcher rules summary.

Procedure:
  1. Read the week's group sheets -> who is in which group.
     Year sheets have: row3 subjects, row4 days, row5 group descriptor,
     rows 6+ members. Row5 is a true group label only when BOLD
     ('Group 1', 'Major', ...). An unbolded row5 person-name ('Billie',
     'James', 'Bee', ...) is the column's FIRST MEMBER (peer-group model:
      groups are named after a member); staff-only names ('Nicky', 'Joe',
      ...) and dash-form apparatus bookings ('Maya - Hoop', 'James - Rod')
      are session descriptors and grant no membership. Dash-less apparatus
      annotations ('Charlie straps', 'Charlie rope') and duets
      ('Oakley & Joanna', 'Lucy & Nem') name real students and resolve to
      them (verified against the roster, so nothing unverified is ever
      minted); parenthesised staff credits ('Lewis (Nicky)') still grant
      nothing.
      Dash-form apparatus bookings ('X - Hoop'), '?????' garble and week
      notes never mint students; duets and annotations grant membership
      only when every named person verifies as a student. Membership
      carries the column's weekday(s).
  2. Read every day sheet (Mon-Fri). Each location column splits into time
     blocks anchored by time-range headers (including dashless '2.15 3.30'
     sub-headers); names embedded in header cells are recovered.
     A block belongs to the student if it:
       - names them explicitly (pre-dash segment only; teacher-name segments
         and pure teacher lists never count), OR
       - targets their whole year (All Yr N / All Nth years / Year N / YR N
         text, or the header's colour-year from the sheet legend:
         yellow=Year 1, blue=Year 2, orange=Year 3), OR
       - names a group they hold for that block's subject (subject-scoped;
         PAR groups match PAR only, never plain 'Group N'), OR
       - is an unmarked session in their year's colour on a weekday their
         group meets that subject (covers 'Acro | Lisa and Ethan',
         'Stand up | Angie', 'Clown | George', ...).
     BTEC / Diploma / external-hire colours never match by year or group;
     1-to-1s still match by explicit name regardless of colour.
  3. Write Apple/Google-Calendar-compatible .ics (RFC 5545 line folding).

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
YEAR3_ORANGE = "FF92D050"

# When True, group-specific events carry the student's group in the title,
# e.g. 'Acro (Group B)', 'Core Skills - Tumbling (Group 1)',
# 'Conditioning (Group D)'. Non-group events (All-years, named 1-to-1s)
# keep the bare class name. UIDs are unaffected (they key on subject_key).
INCLUDE_GROUP_IN_TITLE = True

TERM_WEEK1_MONDAY = dt.date(2026, 9, 14)  # Week 1 Monday (dd-mm-yyyy 14-09-2026)
WEEK_RE = re.compile(r"weeks?\s*(\d+)", re.I)

TIME_RANGE_RE = re.compile(r"(\d{1,2})\s*[.:]\s*(\d{2})\s*[-\u2013]\s*(\d{1,2})\s*[.:]\s*(\d{2})")
# Dashless sub-headers: '2.15 3.30' buried inside a block (Thu Acro minors).
DASHLESS_RANGE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{2})\s+(\d{1,2})\.(\d{2})(?!\d)")
GROUP_RE = re.compile(r"(?<!par )group\s*([ABCD123abcd123])\b", re.I)
PAR_GROUP_RE = re.compile(r"\bpar\s+group\s*([12])\b", re.I)
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
TEACHER_LIST_SPLIT_RE = re.compile(r",|\band\b|&")
# 'All Yr 1', 'All 1st years', 'All 2nd Year', 'All 3rd years' -> year number.
ALL_YEAR_RE = re.compile(
    r"\ball\s*(?:(?:yr|year)s?\s*)?([123])(?:\s*(?:st|nd|rd|th))?(?:\s*years?)?\b", re.I)
ALL_YEARS_GENERIC_RE = re.compile(r"\ball\s+years\b", re.I)
# 'Year 2', 'Yr 2', 'YRs' markers (Teacher Training, Manipulation...).
YEAR_MARK_RE = re.compile(r"\by(?:ea)?rs?\s*([123])\b", re.I)
DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday"]
DAY_SHORT = {"monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
             "thursday": "Thu", "friday": "Fri"}
DAY_RE = re.compile(r"monday|tuesday|wednesday|thursday|friday", re.I)
DAY_PLURAL_FIX = re.compile(r"fridays", re.I)

# Staff names (lowercase). Students who also teach (James, Billie, ...) are
# deliberately NOT here: they attend sessions named after them, and the
# segment-position rules below keep their teaching mentions from matching.
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
# Multi-word staff names: a student-name match inside one never counts
# ('Charlie White' must not match student Charlie).
TEACHER_FULLNAMES = {t for t in TEACHERS if " " in t or "-" in t}

# Subjects with no group structure: the whole year attends.
WHOLE_COHORT = {"teacher_training"}

# Apparatus vocabulary lives in APPARATUS_TOKEN_RE below. Note there is
# deliberately NO blanket apparatus regex anymore: whether an annotated
# cell grants membership is decided per-cell by _member_names (verified
# student + not a teacher + not a dash-form booking), never by the mere
# presence of an apparatus word.
# Whole-word apparatus tokens for cleaning member names: 'Charlie straps'
# -> 'Charlie', 'Billie (minor) dance trap' -> 'Billie'. Dash-form
# bookings ('Maya - Hoop', 'James - Rod') are deliberately NOT cleaned --
# they are session schedules and must stay excluded (see
# _is_apparatus_booking).
APPARATUS_TOKEN_RE = re.compile(
    r"\b(?:hoop|rod|straps|rope|trapeze|silks?|dance\s*trap)\b", re.I)


def _is_apparatus_booking(text):
    """Dash-form apparatus booking ('Maya - Hoop', 'Finley - Rod',
    'James - Rod', 'Rose - Hoop TBC'): a session schedule, never group
    membership. Anything without a dash-apparatus tail ('Charlie straps',
    'Charlie rope', 'Oakley & Joanna') is a person annotation and keeps
    flowing into verification."""
    parts = DASH_SPLIT_RE.split((text or "").strip(), maxsplit=1)
    return len(parts) > 1 and bool(APPARATUS_TOKEN_RE.search(parts[1]))


def _clean_member_text(seg):
    """A group-sheet name segment with decoration removed: parenthesised
    credits ('(minor)', '(Nicky)'), teacher full names, apparatus words and
    stray 'TBC'. Pure text, unverified."""
    t = re.sub(r"\(.*?\)", "", seg)
    for full in TEACHER_FULLNAMES:
        t = re.sub(re.escape(full), " ", t, flags=re.I)
    t = APPARATUS_TOKEN_RE.sub(" ", t)
    t = re.sub(r"\btbc\b", " ", t, flags=re.I)
    return " ".join(t.split())


def _member_names(text, verify_all):
    """Single-person segments of a group-sheet cell, each resolved to a
    verified student: 'Charlie straps' -> ['Charlie'];
    'Lucy & Nem' -> ['Lucy', 'Nem']. Empty when the cell grants nothing:
    dash-form apparatus bookings ('Maya - Hoop'), teachers, junk, or
    anything unverified (all-or-nothing for '&' duets, so '&' pairs never
    mint phantoms)."""
    t = (text or "").strip()
    if _cell_is_junk(t):
        return []
    if _is_apparatus_booking(t):
        return []
    segs = []
    for part in t.split("&"):
        clean = _clean_member_text(part)
        if not clean:
            return []
        norm = roster_norm(clean) or clean.lower()
        if norm not in verify_all or clean.lower() in TEACHERS:
            return []
        segs.append(clean)
    return segs


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


def fill_key(cell):
    """Hashable fill identity: ('rgb', RRGGBB), ('theme', idx, tint) or
    ('none',). Year 2 headers use theme tints, so raw-RGB comparison alone
    misreads them as unfilled."""
    f = cell.fill
    if not getattr(f, "patternType", None):
        return ("none",)
    fg = f.fgColor
    t = getattr(fg, "type", None)
    if t == "rgb":
        rgb = str(getattr(fg, "rgb", "") or "")
        if rgb and rgb != "00000000":
            return ("rgb", rgb)
        return ("none",)
    if t == "theme":
        return ("theme", getattr(fg, "theme", None), getattr(fg, "tint", None))
    return ("none",)


def fill_rgb(cell):
    """Legacy helper: explicit RGB string or '00000000'."""
    try:
        for attr in ("fgColor", "bgColor"):
            c = getattr(cell.fill, attr, None)
            if c is not None and getattr(c, "type", None) == "rgb" \
                    and getattr(c, "rgb", None) not in (None, "00000000"):
                return str(c.rgb)
    except Exception:
        pass
    return "00000000"


def val(ws, r, c, merged_map):
    """Value resolving merged cells: merged_map[(r,c)] -> (vr,vc) top-left."""
    vr, vc = merged_map.get((r, c), (r, c))
    return ws.cell(row=vr, column=vc).value


def val_cell(ws, r, c, merged_map):
    """Cell object resolving merged cells (for fill/font reads)."""
    vr, vc = merged_map.get((r, c), (r, c))
    return ws.cell(row=vr, column=vc)


def cell_rgb(ws, r, c, merged_map):
    vr, vc = merged_map.get((r, c), (r, c))
    return fill_rgb(ws.cell(row=vr, column=vc))


def cell_year(ws, r, c, merged_map, legend):
    """Header colour-year from the sheet legend: 1/2/3, 'other' (BTEC /
    Diploma / external-hire colours) or None (unfilled/neutral)."""
    vr, vc = merged_map.get((r, c), (r, c))
    key = fill_key(ws.cell(row=vr, column=vc))
    if key[0] == "none":
        return None
    return legend.get(key, "other")


def merged_map_of(ws):
    m = {}
    for rng in ws.merged_cells.ranges:
        tl = (rng.min_row, rng.min_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                m[(r, c)] = tl
    return m


def parse_legend(wb):
    """Colour-year map from the Monday sheet's Key area ('Year 1/2/3'
    swatches). Falls back to explicit RGB for Years 1+3 and any theme
    fill for Year 2 (all observed theme header fills are Year 2 blue)."""
    legend = {}
    monday = next((s for s in wb.sheetnames
                   if s.strip().lower().startswith("monday")), None)
    if monday is not None:
        ws = wb[monday]
        mm = merged_map_of(ws)
        # Key area lives top-right (cols 10+, first rows); exact matches
        # only ('Year 1 BTEC' day texts must not match).
        for r in range(1, 16):
            for c in range(10, ws.max_column + 1):
                v = ws.cell(row=r, column=c).value
                if v and str(v).strip().lower() in ("year 1", "year 2", "year 3"):
                    n = int(str(v).strip()[-1])
                    legend[fill_key(val_cell(ws, r, c, mm))] = n
    legend.setdefault(("rgb", YEAR1_YELLOW), 1)
    legend.setdefault(("rgb", YEAR3_ORANGE), 3)
    return legend


def norm_subject_key(s):
    s = (s or "").lower()
    if "aerial conditioning" in s:
        return "aerial_conditioning"
    if "teacher training" in s:
        return "teacher_training"
    # PAR groups before physical theatre: the Tuesday PAR slot carries a
    # 'PT Minors' helper line ('research & materials | On teams') that is
    # annotation, not session identity -- the slot belongs to PAR Group 2.
    if "par group 1" in s:
        return "par_group_1"
    if "par group 2" in s:
        return "par_group_2"
    if "physical theatre" in s or "physical theater" in s \
            or re.search(r"\bpt\b", s):
        return "physical_theatre"
    if "pro tour" in s:
        return "context3"
    if re.search(r"\bpar\b", s):
        return "par"
    if "core skill" in s:
        return "core_skills"
    if "stand up" in s or "standup" in s:
        return "stand_up"
    if "clown" in s:
        return "clown"
    m = re.search(r"context\s*([123])", s)
    if m:
        return f"context{m.group(1)}"
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


DISPLAY_OVERRIDES = {
    "jj angel": "JJ Angel",
}

# Canonical identity merges (nickname/shorthand/apparatus variants).
# Applied inside roster_norm so every consumer merges identically.
MERGE_MAP = {
    "pip": "pipper",
    "fin": "finley",
    "meg": "megan",
    "maddie": "madeline",
    "jj": "jjangel",
    "charlierope": "charlie",
    "charliestraps": "charlie",
}


def parse_year_maps(wb):
    """{1: people, 2: people, 3: people} without Core Skills merge.

    Two passes: first collect rows-6+ names everywhere (plus Core Skills),
    then re-parse with the full verify set so row5 peer-names (Billie,
    Pipper, Bee, James, ...) resolve as first members rather than
    vanishing. Cheap (workbook already in memory) and robust to new
    teacher-named columns.
    """
    core = parse_core_skills(wb)
    verify = set(core)
    for n in (1, 2, 3):
        _, rownames = parse_year_sheet(wb, n)
        verify |= {roster_norm(x) or x.lower() for x in rownames}
    prelim = {}
    for n in (1, 2, 3):
        prelim[n], _ = parse_year_sheet(wb, n, verify=verify)
    return prelim, core


def _merged_away(key):
    """True if roster_norm maps this key onto a different canonical name
    ('pip' -> 'pipper'): the merged-away variant must never win display."""
    k = key.strip()
    if k.startswith("(") and k.endswith(")"):
        k = k[1:-1]
    k = re.sub(r"\(.*?\)", "", k)
    return re.sub(r"[^a-z0-9]", "", k.lower()) in MERGE_MAP


def parse_roster(wb):
    """Canonical roster from raw year sheets (no Core Skills merge for year).

    Returns {key: {"name": display, "year": n, "keys": [variant keys]}}.
    Spelling variants ('farrah' vs 'farrah (minor)') and nicknames ('pip'
    vs 'pipper') merge into one entry; apparatus bookings ('x - hoop') and
    teacher annotations never mint entries. Year comes from year-sheet
    membership only (Core-Skills-only names fall back to year 1).
    """
    prelim, core = parse_year_maps(wb)
    by_norm = {}
    for n in (1, 2, 3):
        for key in prelim[n]:
            if JUNK_ROSTER_RE.search(key):
                continue
            norm = roster_norm(key) or key.lower()
            e = by_norm.setdefault(norm, {"keys": [], "year": n})
            if key not in e["keys"]:
                e["keys"].append(key)
            e["year"] = min(e["year"], n)
    for key in core:
        if JUNK_ROSTER_RE.search(key):
            continue
        norm = roster_norm(key) or key.lower()
        e = by_norm.setdefault(norm, {"keys": [], "year": 1})
        if key not in e["keys"]:
            e["keys"].append(key)
    out = {}
    for norm, e in by_norm.items():
        # Prefer the cleanest key for identity: merged-away nicknames
        # ('pip') never win; then ('farrah' over 'farrah (minor)';
        # 'dee dee' over 'deedee').
        pref = sorted(e["keys"], key=lambda k: (_merged_away(k), "(" in k,
                                                 " " not in k, len(k)))[0]
        clean = re.sub(r"\(.*?\)", "", pref).strip() or pref
        disp = DISPLAY_OVERRIDES.get(clean.lower(),
                                     display_name(clean.lower()))
        out[pref] = {"name": disp, "year": e["year"],
                     "keys": sorted(e["keys"])}
    return out


def parse_core_skills(wb):
    """Shared Core Skills map: {name_lower: {'core_skills': 'Group N'}}."""
    out = {}
    for sheet in ("Core Skills Groups",):
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        mm = merged_map_of(ws)
        col_group = {}
        for c in range(1, ws.max_column + 1):
            v = val(ws, 4, c, mm)
            if v and re.search(r"group\s*([123])", str(v), re.I):
                col_group[c] = "Group " + re.search(
                    r"group\s*([123])", str(v), re.I).group(1)
        if not col_group:
            col_group = {4: "Group 1", 6: "Group 2", 8: "Group 3"}
        for r in range(5, ws.max_row + 1):
            for c, g in col_group.items():
                v = val(ws, r, c, mm)
                if v and str(v).strip() and not str(v).strip().isdigit():
                    out.setdefault(str(v).strip().lower(), {})["core_skills"] = g
    return out


def _cell_is_junk(name):
    if not name or not name.strip():
        return True
    n = name.strip()
    if n.isdigit():
        return True
    if len(n) > 25 or "need" in n.lower():
        return True
    if JUNK_ROSTER_RE.search(n):
        return True
    if n.startswith("(") and n.endswith(")"):
        return True  # '(James)' style annotations, not members
    if "?" in n:
        return True  # 'Lewis ?????' uncertainty garble
    return False


def _row5_kind(label):
    """Classify a row5 group descriptor: 'label' (true Group X-style label),
    'apparatus' (dash-form apparatus booking like 'Maya - Hoop', which
    grants nothing), 'teacher' (staff-only name, grants nothing) or 'peer'
    (a person name, possibly carrying an apparatus/annotation/duet marker
    -- 'Charlie straps', 'Oakley & Joanna', 'Billie (minor) dance trap':
    label AND first member -- decided by the caller against the verify
    set)."""
    lab = (label or "").strip()
    if not lab:
        return "empty"
    if _is_apparatus_booking(lab):
        return "apparatus"
    if re.sub(r"\(.*?\)", "", lab).strip().lower() in TEACHERS:
        return "teacher"
    # Person-name annotations and duets are peer first-members; the
    # major/minor label test must not fire on '(minor)' decorations.
    stripped = _clean_member_text(lab.replace("&", " "))
    if stripped and re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", stripped):
        return "peer"
    if GROUP_RE.search(lab) or re.search(
            r"\bmajor\b|\bminor\b", lab, re.I):
        return "label"
    if re.search(r"^[A-Za-z][A-Za-z .'-]*$", lab):
        return "peer"
    return "label"


def _column_days(day_text):
    """Weekday indices from a row4 day cell ('Tuesday and Thursday',
    'Thursday (minors)', 'Fridays')."""
    days = set()
    if day_text:
        t = DAY_PLURAL_FIX.sub("friday", str(day_text).lower())
        for i, d in enumerate(DAY_ORDER):
            if d in t:
                days.add(i)
    return days


def parse_year_sheet(wb, n, verify=frozenset()):
    """Generic parser for 'Year N Groups': row3 subjects, row4 days,
    row5 group descriptor, rows 6+ members.

    Returns (people, rownames): people {name_lower: {subject_key:
    {"labels": [...], "days": [...], "detail": {label: [days]}}}};
    rownames = rows-6+ human names from membership columns (for the
    cross-sheet verify set: row5 peer-names count as members only if the
    person is verifiably a student elsewhere).
    """
    sheet = _year_sheet_name(wb, n)
    if not sheet:
        return {}, set()
    ws = wb[sheet]
    mm = merged_map_of(ws)
    subj_by_col, day_by_col, last = {}, {}, None
    for c in range(1, ws.max_column + 1):
        v = val(ws, 3, c, mm)
        if v and str(v).strip():
            last = str(v).strip()
        subj_by_col[c] = last
        day_by_col[c] = val(ws, 4, c, mm)
    # Column classification first (no verify needed except peer row5s).
    col_kind, col_label = {}, {}
    for c in range(1, ws.max_column + 1):
        lab = val(ws, 5, c, mm)
        if lab and str(lab).strip():
            if val_cell(ws, 5, c, mm).font.bold:
                col_kind[c] = "label"
            else:
                col_kind[c] = _row5_kind(str(lab).strip())
            col_label[c] = str(lab).strip()
        else:
            col_kind[c] = "empty"
    membership_cols = {c for c, k in col_kind.items()
                       if k in ("label", "peer")}
    # Rows-6+ human names from membership columns (verify set contribution).
    # Raw text only: these never become member keys, they feed the verify
    # set via roster_norm below ('Charlie straps' verifies as Charlie;
    # 'Lucy & Nem' or 'Maya - Hoop' normalise to nothing verifiable and
    # die there, harmlessly).
    rownames = set()
    for r in range(6, ws.max_row + 1):
        for c in membership_cols:
            v = val(ws, r, c, mm)
            if not v or not str(v).strip():
                continue
            name = str(v).strip()
            if _cell_is_junk(name):
                continue
            base = re.sub(r"\(.*?\)", "", name).strip() or name
            rownames.add(base.lower())
    verify_all = set(verify) | {roster_norm(x) or x.lower() for x in rownames}
    people = {}

    def add(name_lower, key, label, days):
        if _cell_is_junk(name_lower):
            return
        d = people.setdefault(name_lower, {}).setdefault(
            key, {"labels": [], "days": [], "detail": {}})
        if label not in d["labels"]:
            d["labels"].append(label)
        for wd in days:
            if wd not in d["days"]:
                d["days"].append(wd)
        det = d["detail"].setdefault(label, [])
        for wd in days:
            if wd not in det:
                det.append(wd)

    for c in range(1, ws.max_column + 1):
        kind = col_kind.get(c, "empty")
        if kind not in ("label", "peer"):
            continue  # apparatus/teacher/empty columns grant nothing
        s = subj_by_col.get(c)
        if not s:
            continue
        key = norm_subject_key(s) or s.strip().lower()
        if n == 1 and key in ("context1", "devising", "movement") \
                and not GROUP_RE.search(col_label.get(c) or ""):
            continue  # Abigail tutor columns carry no membership (Year 1);
            # only proper Group X columns do
        days = _column_days(day_by_col.get(c)) or set(range(5))
        labels = []
        if kind == "label":
            lab = col_label[c]
            gm = GROUP_RE.search(lab)
            labels.append(f"Group {gm.group(1).upper()}" if gm else lab)
        elif _member_names(col_label[c], verify_all) \
                or col_label[c].lower() in TEACHERS:
            # Person-named columns are never named after the person: the
            # row5 name (Jasmine, Bee, Billie, Pipper, ...) is just its
            # first member; annotated row5s ('Charlie straps',
            # 'Oakley & Joanna', 'Billie (minor) dance trap') resolve to
            # their verified students the same way. Year-2-style
            # day-identified groups take the
            # weekday (Mon Clown, Wed Conditioning, ...); PAR
            # takes its header number (Group 1 / Group 2). Year-3 company
            # columns (Billie, Pipper, ...) likewise resolve to the days
            # they meet -- leads are members, not group names.
            # (Non-persons like 'All' fall through and keep their
            # descriptor. Year 1 has no peer columns.)
            if key in ("par_group_1", "par_group_2"):
                labels.append(f"Group {key[-1]}")
            else:
                labels.extend(DAY_SHORT[DAY_ORDER[d]]
                              for d in sorted(days))
        else:  # peer: label AND first member if verifiably a student
            lab = col_label[c]
            labels.append(lab)
        for r in range(6, ws.max_row + 1):
            v = val(ws, r, c, mm)
            if not v or not str(v).strip():
                continue
            name = str(v).strip()
            # Cells resolving to verified students only: apparatus
            # annotations ('Charlie straps'), duets ('Lucy & Nem') and
            # plain names grant membership; dash-form bookings
            # ('Maya - Hoop'), teachers, junk and anything unverified die.
            for seg in _member_names(name, verify_all):
                if n == 1 and seg.lower() == "abigail" and key in (
                        "context1", "devising", "movement"):
                    continue  # tutor-group label, not a student (Year 1)
                for lab in labels:
                    add(seg.lower(), key, lab, days)
        if kind == "peer":
            # Row5 is the column's first member: annotated/duet row5s add
            # each verified student ('Charlie straps' -> Charlie).
            for seg in _member_names(col_label[c], verify_all):
                for dl in labels:
                    add(seg.lower(), key, dl, days)
    return people, rownames


def parse_groups(wb, start_year=1):
    """Return ({1: map, 2: map, 3: map}) with Core Skills merged into each."""
    prelim, core = parse_year_maps(wb)
    maps = {}
    for n in (1, 2, 3):
        people = prelim[n]
        for k, v in core.items():
            g = v["core_skills"]
            d = people.setdefault(k, {}).setdefault(
                "core_skills",
                {"labels": [], "days": [], "detail": {}})
            if g not in d["labels"]:
                d["labels"].append(g)
            for wd in range(5):
                if wd not in d["days"]:
                    d["days"].append(wd)
            det = d["detail"].setdefault(g, [])
            for wd in range(5):
                if wd not in det:
                    det.append(wd)
        maps[n] = people
    return maps


def _merge_detail(dst, src):
    for subj, entry in src.items():
        d = dst.setdefault(subj, {"labels": [], "days": [], "detail": {}})
        for lab in entry.get("labels", []):
            if lab not in d["labels"]:
                d["labels"].append(lab)
        for wd in entry.get("days", []):
            if wd not in d["days"]:
                d["days"].append(wd)
        for lab, wds in entry.get("detail", {}).items():
            det = d.setdefault("detail", {}).setdefault(lab, [])
            for wd in wds:
                if wd not in det:
                    det.append(wd)
    return dst


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
            _merge_detail(me, maps.get(n, {}).get(k.lower(), {}))
        if me:
            return me, n
    return {}, None


# Group-sheet annotations that are schedules, not people.
JUNK_ROSTER_RE = re.compile(
    r"monday|tuesday|wednesday|thursday|friday|\bweeks?\b|\bwk\b|\?", re.I)


def roster_norm(key):
    """Identity for entity resolution: 'Farrah (minor)' -> 'farrah',
    with nickname/apparatus merges ('Pip' -> 'pipper')."""
    k = key.strip()
    if k.startswith("(") and k.endswith(")"):
        k = k[1:-1]
    k = re.sub(r"\(.*?\)", "", k)
    norm = re.sub(r"[^a-z0-9]", "", k.lower())
    return MERGE_MAP.get(norm, norm)


def detect_blocks(ws, legend=None):
    """Split each location column into time blocks.

    Returns (locations, blocks): locations {col: name}, blocks list of
    dict(col, location, start_row, end_row, time_text, header_year,
    header_names). header_year comes from the sheet legend (1/2/3,
    'other', or None when unfilled). header_names are person texts
    recovered from inside time-header cells ('Kitty - Jonathan' hidden
    in a '12.45 - 1.30' header cell); dashless '2.15 3.30' headers split
    sub-blocks (Thu Acro minors).
    """
    legend = legend or {}
    mm = merged_map_of(ws)
    locations = {}
    for c in range(2, ws.max_column + 1):
        v = val(ws, 2, c, mm)
        if v and str(v).strip() and "first aider" not in str(v).lower() and str(v).strip().lower() != "key":
            locations[c] = " ".join(str(v).split())
    # find time-range headers per column (yellow or any, but record fill)
    headers = []  # (col, row, text, year, names)
    for c in locations:
        for r in range(4, min(ws.max_row + 1, 45)):
            v = val(ws, r, c, mm)
            if not v or not str(v).strip():
                continue
            t = " ".join(str(v).split())
            m = TIME_RANGE_RE.search(t)
            dm = None if m else DASHLESS_RANGE_RE.search(t)
            if not (m or dm):
                continue
            if dm and m:
                dm = None
            if dm:
                sh, sm, eh, em = (int(dm.group(1)), int(dm.group(2)),
                                  int(dm.group(3)), int(dm.group(4)))
                t = f"{sh}.{sm:02d} - {eh}.{em:02d}"
            year = cell_year(ws, r, c, mm, legend)
            # Recover person texts hidden inside the header cell: split on
            # any time range they contain ('Joanna- Aimee 12.45 - 1.30
            # Kitty - Jonathan').
            parts = [p for p in re.split(
                r"(\d{1,2}\s*[.:]\s*\d{2}\s*[-\u2013]\s*\d{1,2}\s*[.:]\s*\d{2}|\d{1,2}\.\d{2}\s+\d{1,2}\.\d{2})", t)
                if p and not re.fullmatch(
                    r"\s*(\d{1,2}\s*[.:]\s*\d{2}\s*[-\u2013]\s*\d{1,2}\s*[.:]\s*\d{2}|\d{1,2}\.\d{2}\s+\d{1,2}\.\d{2})\s*",
                    p)]
            names = [" ".join(p.split()) for p in parts
                     if p.strip() and re.search(r"[A-Za-z]", p)]
            headers.append((c, r, t, year, names))
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
    for (c, r, t, y, _) in headers:
        p = block_times(t)
        col_time_counts[(c, p)] = col_time_counts.get((c, p), 0) + 1
    row_times = {}
    for idx, (c, r, t, y, _) in enumerate(headers):
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
            c, rr, t, y, nm = headers[i]
            # only override genuine duplicates of an earlier same-col header
            if p != majority and col_time_counts.get((c, p), 0) > 1:
                fixed = next(headers[j][2] for j, q in parsed if q == majority)
                print(f"WARNING:{ws.title} row {rr} {locations[c]} header "
                      f"{t!r} duplicates earlier block; using row consensus "
                      f"{fixed!r}",
                      file=sys.stderr)
                headers[i] = (c, rr, fixed, y, nm)
    blocks = []
    for i, (c, r, t, y, nm) in enumerate(headers):
        # end = next header in same column, else next 'Student Training Ends' / 'Closed' / sheet end
        end = ws.max_row + 1
        for (c2, r2, _, _, _) in headers:
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
                       "end_row": end, "time_text": t, "header_year": y,
                       "header_names": nm})
    return locations, blocks


def block_texts(ws, block):
    mm = merged_map_of(ws)
    texts = list(block.get("header_names", []))
    for r in range(block["start_row"], min(block["end_row"], 42)):
        v = val(ws, r, block["col"], mm)
        if v and str(v).strip() and not TIME_RANGE_RE.search(str(v)) \
                and not DASHLESS_RANGE_RE.search(str(v)):
            t = " ".join(str(v).split())
            if t not in texts:
                texts.append(t)
    return texts


def block_subject_key(texts):
    return norm_subject_key(" | ".join(texts))


def pre_dash(text):
    """Student-name segment of a block text: 'Tan - Jonathan' -> 'Tan'."""
    return DASH_SPLIT_RE.split(text, maxsplit=1)[0]


def is_teacher_list(text):
    """True for pure staff lists ('Lisa, Ethan, Chané', 'Tony and Janine'):
    2+ name-like parts that are ALL known teachers. Student lists
    ('Holly, Paul', 'Maya, Dee Dee, Rose') fail on the first name."""
    parts = [p.strip() for p in TEACHER_LIST_SPLIT_RE.split(text) if p.strip()]
    return len(parts) >= 2 and all(p.lower() in TEACHERS for p in parts)


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


def year_marks(texts):
    """Year numbers from 'Year N / YR N' markers ('Teacher Training | YR 2',
    'Manipulation | Year 2')."""
    out = set()
    for t in texts:
        for m in YEAR_MARK_RE.finditer(t):
            out.add(int(m.group(1)))
    return out


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
    elif "teacher training" in jl:
        base = "Teacher Training"
    elif "context" in jl and "lecture" in jl:
        m = re.search(r"context\s*([123])", jl)
        base = f"Context {m.group(1)} - Lecture" if m else "Context 1 - Lecture"
    elif "context" in jl:
        m = re.search(r"context\s*([123])", jl)
        base = f"Context {m.group(1)}" if m else "Context 1"
    elif "pro tour" in jl:
        base = "Pro Tour"
    elif "par group 1" in jl:
        base = "PAR Group 1"
    elif "par group 2" in jl:
        base = "PAR Group 2"
    elif re.search(r"\bpar\b", jl):
        base = "PAR"
    elif "stand up" in jl or "standup" in jl:
        base = "Stand Up"
    elif "clown" in jl:
        base = "Clown"
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
                    or "heather" in tl or "mark parfitt" in tl or TIME_RANGE_RE.search(t)
                    or "student training ends" in tl or tl == "closed"
                    or "warm up" in tl or "self led" in tl or "btec" in tl
                    or "professional" in tl or "private hire" in tl):
                continue
            base = t
            break
        base = base or (texts[0] if texts else "Class")
    if sub:
        return f"{base} - {sub}"
    return base


def display_event_name(texts, subject_key, matched_label):
    """Class name, with ' (Group X)' suffix when the event is group-specific
    (matched_label is the student's own label that fired, or a label found
    in the block text). Non-group events keep the bare name."""
    base = event_name(texts)
    if not INCLUDE_GROUP_IN_TITLE or not subject_key or not matched_label:
        return base
    if matched_label.lower() in base.lower():
        return base
    return f"{base} ({matched_label})"


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


def _explicit_hit(seg, candidates):
    """Seg names the student, ignoring staff full names inside it
    ('Charlie White' never matches student Charlie)."""
    low = seg.lower()
    for t in TEACHER_FULLNAMES:
        if t in low:
            low = low.replace(t, " ")
    return any(re.search(rf"\b{re.escape(c)}\b", low) for c in candidates)


def extract_for_student(wb, name, monday=None, cal_year=2026, cal_month=9,
                        start_year=1, filename=None, groups=None,
                        matched_year=None, aliases=()):
    if groups is None:
        maps = parse_groups(wb)
        groups, matched_year = lookup_student(maps, name, start_year)
    me = groups
    legend = parse_legend(wb)
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
        _, blocks = detect_blocks(ws, legend)
        for b in blocks:
            texts = block_texts(ws, b)
            if not texts:
                continue
            joined = " | ".join(texts)
            jl = joined.lower()
            skey = block_subject_key(texts)
            groups_in_block = set()
            for gm in GROUP_RE.finditer(joined):
                groups_in_block.add(f"Group {gm.group(1).upper()}")
            par_in_block = {f"PAR {m.group(1)}"
                            for m in PAR_GROUP_RE.finditer(joined)}
            taught_class = bool(groups_in_block) and bool(skey)
            owners = [t for t in texts if OWNER_RE.match(t)]
            named = False
            for t in (owners or texts):
                if "///" in t or "student training ends" in t.lower() \
                        or t.strip().lower() == "closed":
                    continue
                seg = pre_dash(t)
                if not OWNER_RE.match(t):
                    # Parenthesised credits are not bookings: 'Ethan (& Lisa)'
                    # is Ethan teaching (Lisa assisting), never a student
                    # naming Lisa. Owner-pattern 1-to-1s ('Charlie
                    # (Creative)') keep their full text.
                    seg = re.sub(r"\(.*?\)", "", seg).strip()
                seg = re.sub(r"\btbc\b", "", seg, flags=re.I).strip()
                if not seg:
                    continue
                if seg.lower() in TEACHERS:
                    continue  # staff mention, never a student match
                if skey and is_teacher_list(seg):
                    continue  # 'Lisa, Ethan, Chané': teachers, not students
                if _explicit_hit(seg, candidates):
                    named = True
                    break
            student_year = matched_year or start_year
            disregards_year = all_years_year(texts)
            ymarks = year_marks(texts)
            hdr = b.get("header_year")
            if hdr == "other" and not named:
                continue  # BTEC / Diploma / external-hire colours
            if ("warm up" in jl or "self led" in jl) and not named:
                continue  # optional drop-ins
            allyear = (disregards_year == student_year
                       or (disregards_year == "all" and hdr == 1
                           and student_year == 1))
            if allyear and hdr not in (student_year, None):
                allyear = False
            attend = False
            reason = ""
            matched_label = ""
            subj = me.get(skey, {}) if skey else {}
            my_labels = subj.get("labels", []) if subj else []
            my_days = subj.get("days", []) if subj else []
            if named:
                attend, reason = True, "named explicitly"
                matched_label = _best_label(my_labels, joined)
            elif allyear:
                attend, reason = True, f"all year {disregards_year}"
            elif ymarks and student_year in ymarks \
                    and hdr in (student_year, None):
                if my_labels:
                    attend, reason = True, f"year {student_year} group"
                    matched_label = _best_label(my_labels, joined)
                elif skey in WHOLE_COHORT:
                    attend, reason = True, f"year {student_year} cohort"
            elif par_in_block and skey in ("par_group_1", "par_group_2"):
                # PAR columns hold a single group each, so subject
                # membership is the match ('PAR GROUP 2' must never match
                # a plain 'Group 2'). Base title already names the group.
                num = skey[-1]
                if f"PAR {num}" in par_in_block and my_labels:
                    attend, reason = True, f"{skey} session"
            elif skey and (groups_in_block or par_in_block):
                # Marked session: strict subject-scoped label match. This is
                # colour-blind on purpose: group namespaces are year-distinct
                # ('Group A' aerial vs 'Group A' manipulation never collide
                # across subjects), while Core Skills groups are genuinely
                # shared across years (all Core blocks are yellow).
                if skey in ("par_group_1", "par_group_2"):
                    pass  # handled above
                else:
                    hit = [l for l in my_labels
                           if l in groups_in_block]
                    if hit:
                        attend, reason = True, f"{skey} {hit[0]}"
                        matched_label = hit[0]
            elif skey and hdr in (student_year, None) \
                    and my_labels and weekday in my_days:
                # Unmarked session in the student's colour on a day their
                # group meets ('Acro | Lisa and Ethan', 'Stand up | Angie',
                # 'Clown | George').
                attend, reason = True, f"{skey} day session"
                matched_label = _best_label(my_labels, joined)
            elif skey == "context3" and "company meeting" in jl \
                    and hdr == student_year:
                # Whole-cohort company meeting (no group structure).
                attend, reason = True, "company meeting"
            if not attend:
                continue
            # Sessions never start before 8am, so '1.45 - 3.15' is pm by
            # construction (block_times rolls hours < 8 forward). Dense
            # morning columns sit low on the sheet, so a row-based pm guess
            # would silently shift 11.45am to 23:45 and drop the session
            # (end <= start) -- only reinterpret as pm when the straight
            # parse is impossible.
            t = block_times(b["time_text"])
            if not t:
                continue
            (sh, sm), (eh, em) = t
            start = dt.datetime(date.year, date.month, date.day, sh, sm)
            end = dt.datetime(date.year, date.month, date.day, eh, em)
            if end <= start:
                if sh >= 12:
                    continue
                start += dt.timedelta(hours=12)
                end += dt.timedelta(hours=12)
                if end <= start:
                    continue
            teachers = teachers_of(texts)
            # One shared slot can hold several 1-to-1s ('Lucy - Joe |
            # Tan - Jonathan'): title the event from this student's own
            # session, not their slot-neighbour's.
            title_texts = texts
            if named:
                mine = [t for t in texts
                        if _explicit_hit(pre_dash(t), candidates)]
                if len(mine) == 1:
                    others = [t for t in texts if t != mine[0]
                              and re.search(r"\w\s+-\s*|\s*-\s+\w", t)
                              and not TIME_RANGE_RE.search(t)]
                    if others:
                        title_texts = mine
            events.append({
                "date": date, "day": s.strip(), "start": start, "end": end,
                "name": display_event_name(
                    title_texts, skey, matched_label),
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
    # Session spread across both gym bays is just "Gym" to the user.
    for e in uniq:
        if e["location"] == "Gym Bay 1 + Gym Bay 2":
            e["location"] = "Gym"
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


def _best_label(labels, joined):
    """The student's own label most likely naming this session (appears in
    the block text); else the first label (no title suffix then)."""
    jl = joined.lower()
    for lab in labels:
        if lab.lower() in jl:
            return lab
    return ""


def to_ics(events, name):
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        flat = {s: v.get("labels", []) for s, v in me.items()}
        print(f"Groups for {a.name} (Year {matched_year}): {flat}")
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
