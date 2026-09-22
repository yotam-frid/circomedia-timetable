"""Classify day-sheet blocks via Jev.

Extracts time blocks deterministically, then asks Jev to classify each
block individually: subject, target year, applicable weeks. Each block
gets its own compact Jev call — no full-day state needed.
"""

import re
from . import cache as sheet_cache
from .jev_client import jev_call


def _merge_map(ws):
    """{(row, col): (top_row, top_col)} resolving merged cells."""
    m = {}
    for rng in ws.merged_cells.ranges:
        tl = (rng.min_row, rng.min_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                m[(r, c)] = tl
    return m


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


def _val_cell(ws, r, c, mm):
    """Cell object resolving merged cells (for fill/font reads)."""
    vr, vc = mm.get((r, c), (r, c))
    return ws.cell(row=vr, column=vc)


def parse_legend(wb):
    """Colour-year map from the Monday sheet's Key area.

    Returns dict mapping fill_key -> year (1/2/3).
    Falls back to explicit RGB for Years 1+3 and any theme fill for Year 2.
    """
    legend = {}
    monday = next((s for s in wb.sheetnames
                   if s.strip().lower().startswith("monday")), None)
    if monday is not None:
        ws = wb[monday]
        mm = _merge_map(ws)
        for r in range(1, 16):
            for c in range(10, ws.max_column + 1):
                v = ws.cell(row=r, column=c).value
                if v and str(v).strip().lower() in ("year 1", "year 2", "year 3"):
                    n = int(str(v).strip()[-1])
                    legend[fill_key(_val_cell(ws, r, c, mm))] = n
    legend.setdefault(("rgb", YEAR1_YELLOW), 1)
    legend.setdefault(("rgb", YEAR3_ORANGE), 3)
    return legend


def cell_year(ws, r, c, mm, legend):
    """Header colour-year from the sheet legend: 1/2/3, 'other' (BTEC /
    Diploma / external-hire colours) or None (unfilled/neutral)."""
    vr, vc = mm.get((r, c), (r, c))
    key = fill_key(ws.cell(row=vr, column=vc))
    if key[0] == "none":
        return None
    return legend.get(key, "other")

TIME_RANGE_RE = re.compile(
    r"(\d{1,2})\s*[.:]\s*(\d{2})\s*[-\u2013]\s*(\d{1,2})\s*[.:]\s*(\d{2})")
DASHLESS_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\.(\d{2})\s+(\d{1,2})\.(\d{2})(?!\d)")

DAY_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday"]

YEARS = ["Year 1", "Year 2", "Year 3", "All Years"]

YEAR1_YELLOW = "FFFFFF00"
YEAR3_ORANGE = "FF92D050"
BTEC_ORANGE = "FFFFC000"

# Shared domain rules — constant across all blocks.
DOMAIN_RULES = """CIRCOMEDIA TIMETABLE — Block Classification

You are classifying a single timetable block from a Circomedia circus school schedule.

DOMAIN RULES:
- Staff names are people who teach, not students
- Apparatus tokens: Hoop, Rod, Straps, Rope, Trapeze, Silks, Dance Trap
- Dash-form "X - Apparatus" or "X - Teacher" is a 1-to-1 private lesson
- Parenthesised text like "(Creative)" is an owner tag, not a person
- "Group 1", "Group A", "Major", "Minors" are group labels
- "All 3rd years" = Year 3 only (NOT all years)
- "All 1st years" = Year 1 only (NOT all years)
- "All 2nd Year" = Year 2 only (NOT all years)
- Week markers: "(wk3)" = week 3 only, "(wk3 & 5)" = weeks 3 and 5
- No week marker = applies to ALL weeks
- Core Skills runs 8:45-10:00 Mon-Thu mornings only; there is NO Core Skills on Friday
- Core Skills groups are shared ACROSS years (cross-year, own groups sheet), so its block colours vary — never infer a year from Core Skills colours
- "Self led warm up", Registration, Site Closed, Closed, First Aider duty,
  "Student Training Ends" and "///" break lines are NOT classes — answer subject = "Not a class"
"""


def _parse_time(text):
    """Deterministic time parse: '8.45 - 10.00' -> ((8,45),(10,0))."""
    m = TIME_RANGE_RE.search(text)
    if not m:
        dm = DASHLESS_RANGE_RE.search(text)
        if dm:
            sh = int(dm.group(1)); eh = int(dm.group(3))
            if sh < 8:
                sh += 12
            if eh < 8 or eh < sh:
                eh += 12
            return (sh, int(dm.group(2))), (eh, int(dm.group(4)))
        return None
    sh, sm, eh, em = map(int, m.groups())
    if sh < 8:
        sh += 12
    if eh < 8 or eh < sh:
        eh += 12
    return (sh, sm), (eh, em)


def _block_texts(ws, col, start_row, end_row, mm):
    """Collect all non-empty text cells in a block range.

    Time ranges are KEPT: v1 recovers student names hidden inside
    time-header cells ('12.45 - 1.30 Joanna- Nicky 12.45 - 1.30
    Kitty - Jonathan') — stripping the times erases the per-attendee
    split structure. Pure-time cells still count as headers, not texts.
    """
    texts = []
    for r in range(start_row, end_row):
        vr, vc = mm.get((r, col), (r, col))
        v = ws.cell(row=vr, column=vc).value
        if v is not None:
            raw = " ".join(str(v).split())
            if not raw:
                continue
            if TIME_RANGE_RE.fullmatch(raw) or DASHLESS_RANGE_RE.fullmatch(raw):
                continue
            if "///" in raw:
                continue
            texts.append(raw)
    return texts


def _locations(ws, mm):
    """Row 2 location headers: {col: name}."""
    locs = {}
    for c in range(2, ws.max_column + 1):
        vr, vc = mm.get((2, c), (2, c))
        v = ws.cell(row=vr, column=vc).value
        if v:
            name = " ".join(str(v).split())
            if name and "first aider" not in name.lower() and name.lower() != "key":
                locs[c] = name
    return locs


def _day_from_sheetname(name):
    low = name.strip().lower()
    for i, d in enumerate(DAY_ORDER):
        if low.startswith(d):
            return i, d.capitalize()
    return None, None


def extract_blocks(ws, wb=None, legend=None):
    """Extract time blocks from a day sheet.

    Returns list of dicts: {col, location, start_time, end_time, texts,
    start_row, end_row, color_year}.
    color_year is 1/2/3 from the header cell fill, or None.
    """
    mm = _merge_map(ws)
    locs = _locations(ws, mm)
    blocks = []

    for col in locs:
        headers = []
        for r in range(3, min(ws.max_row + 1, 50)):
            vr, vc = mm.get((r, col), (r, col))
            v = ws.cell(row=vr, column=vc).value
            if not v:
                continue
            t = " ".join(str(v).split())
            if TIME_RANGE_RE.search(t) or DASHLESS_RANGE_RE.search(t):
                headers.append((r, t))
        for idx, (r, t) in enumerate(headers):
            end = headers[idx + 1][0] if idx + 1 < len(headers) else min(ws.max_row + 1, 45)
            for rr in range(r + 1, end):
                vv = ws.cell(row=rr, column=col).value
                if vv and ("student training ends" in str(vv).lower()
                           or str(vv).strip().lower() == "closed"):
                    end = rr
                    break
            times = _parse_time(t)
            if not times:
                continue
            if end <= r:
                continue
            texts = _block_texts(ws, col, r, end, mm)
            if not texts:
                continue
            start_h = times[0][0]
            if start_h >= 17:
                continue
            texts = [t for t in texts if "///" not in t and t.strip()]
            if not texts:
                continue

            # Determine year from header cell fill colour.
            cy = None
            btec = False
            if legend is not None:
                cy = cell_year(ws, r, col, mm, legend)
                if cy == "other":
                    cy = None  # BTEC/Diploma/hire — not a regular year.
                # BTEC cohort blocks never match regular-year students by
                # year or group (v1 invariant); tag them so build_events
                # can skip group-created events while 1-to-1 name matches
                # still land (matching stays colour-blind).
                if fill_key(_val_cell(ws, r, col, mm)) == ("rgb", BTEC_ORANGE):
                    btec = True

            blocks.append({
                "col": col,
                "location": locs[col],
                "start_time": times[0],
                "end_time": times[1],
                "time_text": t,
                "texts": texts,
                "start_row": r,
                "end_row": end,
                "color_year": cy,
                "btec": btec,
            })
    return blocks


def _build_target_options(groups_by_subject_year):
    """Build the target choice list from student-group data."""
    opts = ["All Years"]
    for subj, year_map in groups_by_subject_year.items():
        for year, groups in year_map.items():
            for g in groups:
                opts.append(f"{g} ({subj}, {year})")
    for y in YEARS[1:]:
        opts.append(f"All {y}")
    opts.append("student_match")
    return opts


def _classify_block(block, day_name, groups_by_subject_year, target_opts,
                     weeks_in_file):
    """Classify a single block via Jev. Returns {subject, target, weeks}."""
    content = "; ".join(TIME_RANGE_RE.sub("", t).strip() or t
                        for t in block["texts"])

    # Subject choices come from the actual data, not hardcoded lists.
    subject_opts = sorted(groups_by_subject_year.keys()) + ["Not a class"]

    state = (
        f"{DOMAIN_RULES}\n\n"
        f"Day: {day_name}\n"
        f"Available subjects: {', '.join(subject_opts)}\n"
        f"Available groups:\n"
        + "\n".join(f"  {s} ({y}): {', '.join(gs)}"
                     for s, ym in sorted(groups_by_subject_year.items())
                     for y, gs in sorted(ym.items()))
    )

    questions = {
        "subject": {
            "type": "choice",
            "instructions": (
                f"What subject is this block? "
                f"Location: {block['location']}. Time: {block['time_text']}. "
                f"Content: {content}"
            ),
            "criteria": {s: s for s in subject_opts},
        },
        "target": {
            "type": "choice",
            "instructions": (
                f"What is the target audience? "
                f"Location: {block['location']}. Time: {block['time_text']}. "
                f"Content: {content}"
            ),
            "criteria": {t: t for t in target_opts},
        },
    }

    # Weeks: one noul question per week in the file.
    for w in weeks_in_file:
        questions[f"wk{w}"] = {
            "type": "noul",
            "instructions": (
                f"Does this block apply to week {w}? "
                f"Look for week markers like (wk{w}) in the content. "
                f"No week marker means it applies to ALL weeks. "
                f"Content: {content}"
            ),
            "criteria": {
                "true": f"This block is in week {w}",
                "false": f"This block is NOT in week {w}",
            },
        }

    answers = jev_call(state, questions)

    # Collect weeks where Jev said true.
    weeks = [w for w in weeks_in_file
             if answers.get(f"wk{w}", {}).get("noul", 0) > 0.5]

    return {
        "subject": answers.get("subject", {}).get("choice", "Unknown"),
        "target": answers.get("target", {}).get("choice", "All Years"),
        "weeks": weeks,  # empty list = all weeks (no marker found)
    }


def classify_day_sheet(ws, sheet_name, groups_by_subject_year, weeks_in_file,
                       wb=None, legend=None, cache_name=None):
    """Classify all blocks in a day sheet via Jev (one call per block).

    Parameters
    ----------
    ws : openpyxl worksheet
    sheet_name : str
    groups_by_subject_year : dict
        {subject: {year: [group_names]}} from the student-group extraction.
    weeks_in_file : list of int
        Week numbers present in this xlsx file (e.g. [3, 4, 5]).
    wb : openpyxl workbook, optional
        For parsing the colour legend from Monday sheet.
    legend : dict, optional
        Pre-parsed legend {fill_key: year}. Passed through to extract_blocks.
    cache_name : str, optional
        xlsx filename used to key the per-sheet classification cache.

    Returns
    -------
    list of dict
        Each dict: {block, subject, target, weeks}
    """
    _, day_name = _day_from_sheetname(sheet_name)
    if not day_name:
        day_name = sheet_name.strip()

    blocks = extract_blocks(ws, wb=wb, legend=legend)
    if not blocks:
        return []

    # Per-sheet cache: key = filename + sheet name, fingerprint = what
    # classification depends on (blocks + group options + weeks in file).
    fingerprint = sheet_cache.fingerprint(
        blocks, groups_by_subject_year, weeks_in_file)

    cached = sheet_cache.load(cache_name, sheet_name, fingerprint=fingerprint)
    if cached is not None and len(cached) == len(blocks):
        print(f"  Cached '{sheet_name}' ({len(cached)} blocks)")
        return [
            {"block": b, "subject": c["subject"], "target": c["target"],
             "weeks": c["weeks"]}
            for b, c in zip(blocks, cached)
        ]

    target_opts = _build_target_options(groups_by_subject_year)

    results = []
    for i, b in enumerate(blocks):
        cls = _classify_block(b, day_name, groups_by_subject_year, target_opts,
                              weeks_in_file)

        # Post-process: fix common Jev misclassifications based on text patterns.
        cls = _fix_classification(b, cls, groups_by_subject_year)

        print(f"    Block {i}: {b['time_text']} @ {b['location']} -> "
              f"{cls['subject']} | {cls['target']} | {cls['weeks']}"
              + (f" [color_year={b.get('color_year')}]" if b.get('color_year') else ""))
        results.append({
            "block": b,
            "subject": cls["subject"],
            "target": cls["target"],
            "weeks": cls["weeks"],
        })

    sheet_cache.save(cache_name, sheet_name,
                     [{k: v for k, v in r.items() if k != "block"} for r in results],
                     fingerprint=fingerprint)
    return results


def _fix_classification(block, cls, groups_by_subject_year):
    """Post-process Jev classification to fix common misclassifications.

    Checks block texts for patterns that Jev often misses:
    - "All Yr N" / "All Nth years" → target should be "All Year N"
    - Owner patterns like "Charlie (Creative)" → student_match
    - "Pro Tour" → Context 3
    - "Dance" / "Forro" / "Capoeira" with year markers
    """
    texts = block.get("texts", [])
    joined = " ".join(texts).lower()
    subject = cls["subject"]
    target = cls["target"]

    # Fix 1: "All Yr N" / "All Nth years" text → override target to "All Year N"
    import re
    m = re.search(r"All\s+(?:Yr|Year)\s+(\d)", " ".join(texts), re.I)
    if m:
        year_num = int(m.group(1))
        target = f"All Year {year_num}"
        # Also fix subject if it was misclassified as student_match
        if subject == "student_match" or target == "student_match":
            # Try to extract subject from first text
            for t in texts:
                tl = t.strip().lower()
                if tl and "all" not in tl and "yr" not in tl and "year" not in tl:
                    # Check if this is a known subject
                    for s in groups_by_subject_year:
                        if s.lower() in tl or tl in s.lower():
                            subject = s
                            break
                    break

    # Fix 2: "All Nth years" pattern (e.g., "All 1st years", "All 2nd years")
    m = re.search(r"All\s+(\d)(?:st|nd|rd|th)\s+years", " ".join(texts), re.I)
    if m:
        year_num = int(m.group(1))
        target = f"All Year {year_num}"
        if subject == "student_match":
            for t in texts:
                tl = t.strip().lower()
                if tl and "all" not in tl and "year" not in tl and "yr" not in tl:
                    for s in groups_by_subject_year:
                        if s.lower() in tl or tl in s.lower():
                            subject = s
                            break
                    break

    # Fix 3: Owner patterns like "Charlie (Creative)" → student_match
    # These are 1-to-1 sessions with the owner's name in parentheses
    OWNER_RE = re.compile(r"(\w+)\s*\((\w+(?:\s+\w+)*)\)")
    for t in texts:
        om = OWNER_RE.match(t.strip())
        if om:
            # This is an owner block like "Charlie (Creative)"
            # The subject in parentheses is the session type
            session_type = om.group(2).lower()
            # Map to a known subject
            creative_like = ["creative", "devising", "project"]
            if any(cl in session_type for cl in creative_like):
                for s in groups_by_subject_year:
                    if any(cl in s.lower() for cl in creative_like):
                        subject = s
                        break
            # Ensure target is student_match (1-to-1)
            if target != "student_match":
                target = "student_match"
            break

    # Fix 4: "Pro Tour" → Context 3
    if "pro tour" in joined:
        if "context 3" in [s.lower() for s in groups_by_subject_year]:
            subject = "Context 3"
            # If target is student_match, try to find the right group
            if target == "student_match":
                # Check for year marker (the key is an int year; do not
                # str()-wrap it or the check never fires — 3 != '3').
                cy = block.get("color_year")
                if cy and cy in groups_by_subject_year.get("Context 3", {}):
                    groups = groups_by_subject_year["Context 3"][cy]
                    if groups:
                        target = f"{groups[0]} (Context 3, {cy})"

    # Fix 5: "Dance" / "Forro" / "Capoeira" with year marker
    if any(w in joined for w in ["dance", "forro", "capoeira"]):
        m = re.search(r"All\s+(?:Yr|Year)\s+(\d)", " ".join(texts), re.I)
        if m:
            year_num = int(m.group(1))
            target = f"All Year {year_num}"
            # Find the correct subject name — may be "Movement" in group data
            for s in groups_by_subject_year:
                sl = s.lower()
                if any(w in sl for w in ["dance", "forro", "capoeira", "movement"]):
                    subject = s
                    break

    # Fix 5b: "Dance" / "Forro" / "Capoeira" without year marker but with color_year
    if any(w in joined for w in ["dance", "forro", "capoeira"]) and target == "student_match":
        cy = block.get("color_year")
        if cy:
            target = f"All Year {cy}"
            for s in groups_by_subject_year:
                sl = s.lower()
                if any(w in sl for w in ["dance", "forro", "capoeira", "movement"]):
                    subject = s
                    break

    # Fix 6: "Teacher Training" / "teacher training" → check for year cohort
    if "teacher training" in joined:
        cy = block.get("color_year")
        if cy:
            target = f"All Year {cy}"
            subject = "Teacher Training"

    # Fix 7: Target subject mismatch — e.g. "Major (Acro, 2)" for an Aerial block.
    # Jev sometimes puts the wrong subject in the target parenthetical.
    # If the target's subject doesn't match the block subject, fix it.
    m = re.match(r"(.+?)\s*\((.+?),\s*(?:Year\s+)?(\d+)\)", target)
    if m and subject != "student_match":
        target_subject = m.group(2).strip()
        if target_subject.lower() != subject.lower():
            # Rebuild target with the correct subject
            target = f"{m.group(1).strip()} ({subject}, {m.group(3)})"

    cls["subject"] = subject
    cls["target"] = target
    return cls
