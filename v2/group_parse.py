"""Assemble group membership from Jev classification answers."""

import re

_DAY_TOKEN_RE = re.compile(r"[a-z]+", re.I)
# Every spelling a group sheet uses for a day header, mapped to the canonical
# Mon..Sun vocabulary the allocator compares against. Publishers abbreviate
# freely (the Year 2 sheet heads the Stand Up column "Friday" while every
# other day cell there is "Monday " with a trailing space), and a day cell
# that fails to parse is not a cosmetic loss: the column's group label is
# derived from it, so an unreadable day silently deletes the class.
DAY_ALIASES = {
    "monday": "Mon", "mon": "Mon",
    "tuesday": "Tue", "tue": "Tue", "tues": "Tue",
    "wednesday": "Wed", "wed": "Wed",
    "thursday": "Thu", "thu": "Thu", "thur": "Thu", "thurs": "Thu",
    "friday": "Fri", "fri": "Fri",
    "saturday": "Sat", "sat": "Sat",
    "sunday": "Sun", "sun": "Sun",
}

# Teacher names — used for stripping from segments in event_creator.
# Kept for deterministic fallback; Jev handles teacher detection during classification.
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

MERGE_MAP = {
    "pip": "pipper",
    "fin": "finley",
    "meg": "megan",
    "maddie": "madeline",
    "jj": "jjangel",
    "jj angel": "jjangel",
    "charlierope": "charlie",
    "charliestraps": "charlie",
    "deedee": "dee dee",
}

# Thresholds for Jev noul answers
TEACHER_THRESHOLD = 0.7  # Stricter to avoid false positives
APPARATUS_THRESHOLD = 0.5
JUNK_THRESHOLD = 0.5
GROUP_LABEL_THRESHOLD = 0.5
STUDENT_THRESHOLD = 0.5
DISCIPLINE_THRESHOLD = 0.5
GROUP_THRESHOLD = 0.5


def _normalize_name(name):
    """Apply nickname merges."""
    low = name.lower().strip()
    return MERGE_MAP.get(low, low)


def _extract_days(text):
    """Canonical day labels from a day cell: 'Tuesday and Thursday', 'Fridays',
    'Mon'. Both lookups are exact against DAY_ALIASES, so a plural is only
    ever a real day name and not a substring match. First-seen order,
    duplicates collapsed."""
    out = []
    for token in _DAY_TOKEN_RE.findall(text or ""):
        low = token.lower()
        day = DAY_ALIASES.get(low) or DAY_ALIASES.get(low[:-1] if low.endswith("s") else low)
        if day and day not in out:
            out.append(day)
    return out


def _merged_map(ws):
    """{(row, col): (top_row, top_col)} resolving merged cells."""
    m = {}
    for rng in ws.merged_cells.ranges:
        tl = (rng.min_row, rng.min_col)
        for r in range(rng.min_row, rng.max_row + 1):
            for c in range(rng.min_col, rng.max_col + 1):
                m[(r, c)] = tl
    return m


def _cell_val(ws, r, c, merged):
    """Get cell value at resolved coordinates."""
    vr, vc = merged.get((r, c), (r, c))
    v = ws.cell(row=vr, column=vc).value
    return str(v).strip() if v is not None else ""


def _is_teacher(answers, vr, vc):
    """Check if cell is classified as teacher."""
    return answers.get(f"q_{vr}_{vc}_teacher", {}).get("noul", 0) > TEACHER_THRESHOLD


def _is_apparatus(answers, vr, vc):
    """Check if cell is classified as apparatus booking."""
    return answers.get(f"q_{vr}_{vc}_apparatus", {}).get("noul", 0) > APPARATUS_THRESHOLD


def _is_junk(answers, vr, vc):
    """Check if cell is classified as junk."""
    return answers.get(f"q_{vr}_{vc}_junk", {}).get("noul", 0) > JUNK_THRESHOLD


# Literal group-label cells, as written on the sheets. These are parseable
# deterministically from the cell; Jev's _group noul
# still gates "is this a group cell at all", but the label itself must never
# come from Jev's choice question, which has no Group D option and coerces
# "Group D" to "group_c" (this exact bug once mapped the whole Year-1
# Group-D columns to Group C).
GROUP_LABEL_RE = re.compile(
    r"^(?P<grp>Group\s+[0-9A-E]|PAR\s+Group\s+\d+|Major|Minors|All)$", re.I
)


def _deterministic_group_label(val, year=None):
    """Literal group label from a header cell, or None if not one.

    Core Skills "Group 3" is the roster's Group C (cross-year identity);
    the day sheets write "Group 3" and the roster canonical is "Group C".
    """
    if not val:
        return None
    m = GROUP_LABEL_RE.match(val.strip())
    if not m:
        return None
    raw = m.group("grp").strip()
    if raw.lower().startswith("group"):
        token = raw.split()[-1]
        if token.isdigit():
            if year is None and token == "3":
                return "Group C"
            return f"Group {int(token)}"
        return "Group " + token.upper()
    if raw.lower().startswith("par"):
        return "PAR Group " + raw.split()[-1]
    return raw[:1].upper() + raw[1:]


def _get_group_label(answers, vr, vc):
    """Get explicit group label from Jev choice answer (for PAR GROUP, Major, Minors, etc.)."""
    choice = answers.get(f"q_{vr}_{vc}_group_label", {}).get("choice", "none")
    if choice == "none":
        return None
    # Map choice back to display label
    label_map = {
        "group_1": "Group 1", "group_2": "Group 2", "group_3": "Group C",
        "group_a": "Group A", "group_b": "Group B", "group_c": "Group C",
        "group_d": "Group D", "group_e": "Group E", "major": "Major",
        "minors": "Minors", "all": "All",
        "par_group_1": "PAR Group 1", "par_group_2": "PAR Group 2",
    }
    return label_map.get(choice)


def assemble(ws, classification, year=None):
    """Build per-student group assignments from a classified sheet.

    Parameters
    ----------
    ws : openpyxl worksheet
    classification : dict from classify_sheet()

    Returns
    -------
    list of (student_name, {subject, group, days})
    """
    answers = classification["answers"]
    resolutions = classification["resolutions"]
    merged = _merged_map(ws)

    max_col = ws.max_column or 30

    # Pre-build column -> group label.
    # Start at row 4 (skipping row 3 discipline headers).  Skip any cell
    # that is also classified as discipline — that's a subject header, not
    # a group label (e.g. Year 3 "Acro" at row 3 has both flags).
    # Use Jev _group noul to detect group-like cells, then use cell value.
    # For explicit labels (PAR GROUP, Major, Minors), use Jev group_label choice.
    col_group = {}
    for col in range(2, max_col + 1):
        for r in range(4, 7):
            vr, vc = merged.get((r, col), (r, col))
            v = ws.cell(row=vr, column=vc).value
            val = str(v).strip() if v is not None else ""
            if not val:
                continue
            # Check Jev _group noul for group-like cells
            is_group = answers.get(f"q_{vr}_{vc}_group", {}).get("noul", 0) > GROUP_THRESHOLD
            is_discipline = answers.get(f"q_{vr}_{vc}_discipline", {}).get("noul", 0) > DISCIPLINE_THRESHOLD
            if is_group and not is_discipline:
                # Deterministic label first (exact tokens like 'Group D',
                # 'Group 1', 'Major', 'PAR Group 1') — never trust Jev's
                # coerced choice for these. Fall back to Jev only for
                # ambiguous text (abbreviations, etc.).
                deterministic_label = _deterministic_group_label(val, year)
                if deterministic_label:
                    col_group[col] = deterministic_label
                else:
                    # Try explicit label first (PAR GROUP, Major, Minors, etc.)
                    explicit_label = _get_group_label(answers, vr, vc)
                    if explicit_label:
                        col_group[col] = explicit_label
                    else:
                        # Use cell value directly (e.g., day names like "Monday")
                        col_group[col] = val
                break

    # Pre-build column -> subject by walking rows and resolving merges.
    # A column's subject is the first discipline-classified cell that
    # either lives at (r, col) or spans col via a merge.
    # If no discipline cell, fall back to the group label when it looks
    # like a subject (e.g. "PAR GROUP 1").
    col_subject = {}
    for col in range(2, max_col + 1):
        for r in range(1, 20):
            vr, vc = merged.get((r, col), (r, col))
            v = ws.cell(row=vr, column=vc).value
            val = str(v).strip() if v is not None else ""
            if not val:
                continue
            if answers.get(f"q_{vr}_{vc}_discipline", {}).get("noul", 0) > DISCIPLINE_THRESHOLD:
                col_subject[col] = val
                break
        if col not in col_subject:
            # No discipline — check if the group label IS the subject.
            grp = col_group.get(col, "")
            if grp and re.search(r"(?i)^par\s+group", grp):
                col_subject[col] = grp

    # Also scan row 3 for PAR GROUP headers that have group=True but
    # no discipline — these need to appear in col_group too.
    for col in range(2, max_col + 1):
        if col in col_group:
            continue
        vr, vc = merged.get((3, col), (3, col))
        v = ws.cell(row=vr, column=vc).value
        val = str(v).strip() if v is not None else ""
        if val:
            label = _get_group_label(answers, vr, vc)
            if label and re.search(r"(?i)^par\s+group", label):
                col_group[col] = label
                if col not in col_subject:
                    col_subject[col] = label

    # Pre-build column -> days.
    col_days = {}
    for col in range(2, max_col + 1):
        for r in range(2, 6):
            vr, vc = merged.get((r, col), (r, col))
            v = ws.cell(row=vr, column=vc).value
            val = str(v).strip() if v is not None else ""
            if val:
                days = _extract_days(val)
                if days:
                    col_days[col] = days
                    break

    results = []

    # Year 1 tutor columns without group labels — skip entirely.
    TUTOR_NO_GROUP = {"devising", "movement"}

    # Pre-compute which subjects have at least one labelled column.
    subjects_with_labels = set()
    for col in range(2, max_col + 1):
        subject = col_subject.get(col)
        group_label = col_group.get(col)
        if subject and group_label:
            subjects_with_labels.add(subject)

    # Build a set of known student names from cells that look like names
    # (not group labels, not discipline headers, not day names, not
    # dash-form apparatus bookings like "Name - Hoop").
    known_students = set()
    for col in range(2, max_col + 1):
        for r in range(5, 50):
            vr, vc = merged.get((r, col), (r, col))
            if vc != col:
                continue
            v = ws.cell(row=vr, column=vc).value
            val = str(v).strip() if v is not None else ""
            if not val:
                continue
            # Skip group labels, discipline headers, day names using Jev.
            if answers.get(f"q_{vr}_{vc}_group", {}).get("noul", 0) > GROUP_THRESHOLD:
                continue
            if answers.get(f"q_{vr}_{vc}_discipline", {}).get("noul", 0) > DISCIPLINE_THRESHOLD:
                continue
            if _extract_days(val):
                continue
            # Skip dash-form apparatus bookings ("Name - Hoop", "Rose - Hoop TBC").
            if " - " in val:
                continue
            # Skip teacher/apparatus/junk using Jev
            if _is_teacher(answers, vr, vc):
                continue
            if _is_apparatus(answers, vr, vc):
                continue
            if _is_junk(answers, vr, vc):
                continue
            resolved = resolutions.get(f"res_{vr}_{vc}", val)
            base = re.sub(r"\s*\(.*?\)", "", resolved)
            if "&" in base:
                parts = [p.strip().lower() for p in base.split("&") if p.strip()]
            elif "(" in resolved:
                parts = [re.sub(r"\s*\(.*?\).*", "", resolved).strip().lower()]
            else:
                parts = [base.strip().lower()] if base.strip() else []
            known_students.update(parts)
            # Also add first-word variants for matching.
            known_students.update(p.split()[0] for p in parts if p)

    for col in range(2, max_col + 1):
        subject = col_subject.get(col)
        group_label = col_group.get(col)
        days = col_days.get(col, [])

        # Day-identified columns: no group label, use the day name.
        # This must run BEFORE the tutor column skip, because Devising/
        # Movement in Year 1 may have day indicators (e.g., "Fridays")
        # instead of traditional group labels.
        if not group_label and days:
            group_label = days[0]

        # Skip tutor columns (Devising/Movement) with no group labels.
        # Movement is only a tutor column in Year 1; Year 2/3 Movement
        # is a real subject.
        skip_movement = "movement" in TUTOR_NO_GROUP and year == 1
        skip_devising = "devising" in TUTOR_NO_GROUP
        if not group_label and subject:
            if subject.lower() == "devising" or (skip_movement and subject.lower() == "movement"):
                continue

        # Skip unlabelled columns when labelled columns exist for this
        # subject (handles Context 1 Monday, etc.).
        # Must run AFTER the day-identified fix above.
        if not group_label and subject and subject in subjects_with_labels:
            continue

        # Students: every cell classified as student in this column,
        # OR whose resolved base name matches a known student.
        # Tutor columns (Devising/Movement) are already skipped above.
        use_known = bool(subject)
        seen_students = set()
        for r in range(5, 50):
            vr, vc = merged.get((r, col), (r, col))
            if vc != col:
                continue
            v = ws.cell(row=vr, column=vc).value
            val = str(v).strip() if v is not None else ""
            if not val:
                continue
            # Skip dash-form apparatus bookings ("Name - Hoop").
            if " - " in val:
                continue
            resolved = resolutions.get(f"res_{vr}_{vc}", val)
            is_student = answers.get(f"q_{vr}_{vc}_student", {}).get("noul", 0) > STUDENT_THRESHOLD
            # Extract base name(s) from complex cell values.
            # "Billie (minor) dance trap" → "billie" (strip parens + trailing),
            # "Lucy & Nem" → ["lucy", "nem"],
            # "Charlie straps" → "charlie" (strip apparatus),
            # "Imogen H" → ["imogen h"] (keep multi-word names intact).
            if "&" in resolved:
                base = re.sub(r"\s*\(.*?\)", "", resolved)
                # Use Jev apparatus detection instead of regex
                if _is_apparatus(answers, vr, vc):
                    continue
                parts = [p.strip().lower() for p in base.split("&") if p.strip()]
            elif "(" in resolved:
                # "Billie (minor) dance trap" → "Billie"
                parts = [re.sub(r"\s*\(.*?\).*", "", resolved).strip().lower()]
            else:
                # Use Jev apparatus detection instead of regex
                if _is_apparatus(answers, vr, vc):
                    continue
                parts = [resolved.strip().lower()] if resolved.strip() else []
            if not is_student and use_known:
                # Check if any part matches a known student (use first
                # word for matching, to catch "Charlie straps" → "charlie").
                check_parts = [p.split()[0] for p in parts if p]
                if not any(p in known_students for p in check_parts):
                    continue
                student_names = parts
            elif not is_student:
                continue
            else:
                student_names = parts

            for sname in student_names:
                if sname in seen_students:
                    continue
                seen_students.add(sname)
                # Apply nickname merges and filter out teachers using Jev.
                norm_name = _normalize_name(sname)
                base_name = re.sub(r"\s*\(.*?\)", "", norm_name).strip()
                # Also strip trailing junk like "????" from names.
                base_name = re.sub(r"[^a-z ].*", "", base_name).strip()
                # Note: Teacher filtering already done via Jev _is_teacher check above
                # but keep as safety net for resolved names
                if _is_teacher(answers, vr, vc):
                    continue
                results.append((norm_name, {
                    "subject": subject or "Unknown",
                    "group": group_label or "",
                    "days": days,
                }))

    # Deduplicate: merge entries for the same (subject, group) pair,
    # collecting their days.  Unlabelled columns contribute their days
    # to ALL labelled entries of the same subject (e.g. Context 1
    # Monday → Group 1 + Group 2).
    by_name = {}
    for name, info in results:
        by_name.setdefault(name, []).append(info)

    deduped = []
    for name, infos in by_name.items():
        by_subj = {}
        for info in infos:
            subj = info["subject"]
            grp = info["group"]
            key = (subj, grp)
            if key not in by_subj:
                by_subj[key] = {"info": dict(info), "is_labelled": bool(grp)}
            else:
                for d in info["days"]:
                    if d not in by_subj[key]["info"]["days"]:
                        by_subj[key]["info"]["days"].append(d)

        # Collect unlabelled days per subject to merge into labelled entries.
        unlabelled_days = {}
        has_labelled = set()
        for (subj, grp), entry in by_subj.items():
            if grp:
                has_labelled.add(subj)
            else:
                unlabelled_days.setdefault(subj, []).extend(entry["info"]["days"])

        for (subj, grp), entry in by_subj.items():
            if grp:
                # Merge unlabelled days for this subject.
                for d in unlabelled_days.get(subj, []):
                    if d not in entry["info"]["days"]:
                        entry["info"]["days"].append(d)
                deduped.append((name, entry["info"]))
            elif subj not in has_labelled:
                # No labelled entry for this subject — keep unlabelled.
                deduped.append((name, entry["info"]))

    # Normalize subject names and PAR GROUP labels for the feed schema.
    for _, info in deduped:
        s = (info["subject"] or "").lower()
        if "pro tour" in s:
            info["subject"] = "Context 3"
        elif "par group 1" in s:
            info["subject"] = "PAR"
            info["group"] = "Group 1"
        elif "par group 2" in s:
            info["subject"] = "PAR"
            info["group"] = "Group 2"

    return deduped


JUNK_ROSTER_RE = re.compile(
    r"\bneed\b|monday|tuesday|wednesday|thursday|friday|\bwk\b|\?|,", re.I)


def enrolled_students(worksheet):
    """Names the school enrolled, read off the Core Skills Groups sheet.

    That sheet is the school's own cross-year enrolment list: one row per
    student, cross-referenced into the numbered blocks. It is the only
    place a person is stated to be a *student* rather than a name written
    in a group column, and it is what separates a mentor from a student who
    happens to share a name with one — Lisa is a Year 2 student on the
    group sheets AND a Core Skills tutor, and only this list says so.

    Returns a set of lowercase keys (nickname merges applied); empty when
    the sheet is absent, which leaves callers on their previous behaviour.
    """
    mm = _merged_map(worksheet)
    names = set()
    for r in range(5, (worksheet.max_row or 0) + 1):
        for c in range(2, (worksheet.max_column or 0) + 1):
            vr, _vc = mm.get((r, c), (r, c))
            v = worksheet.cell(row=vr, column=c).value
            if v is None:
                continue
            token = " ".join(str(v).split()).lower()
            if not token or token.isdigit():
                continue  # the numbered block column, not a name
            names.add(_normalize_name(token))
    return names


def drop_junk(students_by_year, student_group_data, enrolled=None):
    """Strip junk entries the group sheets picked up.

    Lives here (not in the build script) because both the production build
    and the v2 CLI must apply it: the day-sheet cache fingerprint is derived
    from students_by_year, so disagreeing about the roster silently
    invalidates every cached classification and forces a full Jev re-run.

    `enrolled` (from enrolled_students) is the school's own enrolment list
    and takes precedence over the staff-name list: a person on it is a
    student even if a mentor shares their name, and a person absent from it
    is dropped when their name is a known staff name. Without it the list is
    the only signal, which is what silently deleted Lisa.
    """
    import sys
    bad = set()
    for s in student_group_data:
        if len(s) > 25 or JUNK_ROSTER_RE.search(s):
            bad.add(s)
        elif s in TEACHERS and (not enrolled or s not in enrolled):
            bad.add(s)
    for b in sorted(bad):
        print(f"  drop junk roster '{b}'", file=sys.stderr)
    if bad:
        students_by_year = {
            y: [n for n in ns if n.lower() not in bad]
            for y, ns in students_by_year.items()
        }
        student_group_data = {k: v for k, v in student_group_data.items() if k not in bad}
    # Unconditional: a year that ends up with nobody is not a cohort, and
    # leaving it behind would put an empty year in the published roster.
    students_by_year = {y: ns for y, ns in students_by_year.items() if ns}
    return students_by_year, student_group_data
