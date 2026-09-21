"""Assemble group membership from Jev classification answers."""

import re

DAY_RE = re.compile(r"monday|tuesday|wednesday|thursday|friday", re.I)
DAY_SHORT = {
    "monday": "Mon", "tuesday": "Tue", "wednesday": "Wed",
    "thursday": "Thu", "friday": "Fri",
}

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

# Apparatus vocabulary — strip from cell values when extracting names.
APPARATUS_TOKEN_RE = re.compile(
    r"\b(?:hoop|rod|straps?|rope|trapeze|silks?|dance\s*trap)\b", re.I)


def _normalize_name(name):
    """Apply nickname merges."""
    low = name.lower().strip()
    return MERGE_MAP.get(low, low)


def _extract_days(text):
    """Extract day abbreviations from text like 'Tuesday and Thursday'."""
    found = DAY_RE.findall(text)
    return [DAY_SHORT[d.lower()] for d in found] if found else []


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
    col_group = {}
    for col in range(2, max_col + 1):
        for r in range(4, 7):
            vr, vc = merged.get((r, col), (r, col))
            v = ws.cell(row=vr, column=vc).value
            val = str(v).strip() if v is not None else ""
            if not val:
                continue
            if answers.get(f"q_{vr}_{vc}_group", {}).get("noul", 0) > 0.5:
                # Skip if also classified as discipline (subject header).
                if answers.get(f"q_{vr}_{vc}_discipline", {}).get("noul", 0) > 0.5:
                    continue
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
            if answers.get(f"q_{vr}_{vc}_discipline", {}).get("noul", 0) > 0.5:
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
        if val and answers.get(f"q_{vr}_{vc}_group", {}).get("noul", 0) > 0.5:
            if re.search(r"(?i)^par\s+group", val):
                col_group[col] = val
                if col not in col_subject:
                    col_subject[col] = val

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
            # Skip group labels, discipline headers, day names.
            if answers.get(f"q_{vr}_{vc}_group", {}).get("noul", 0) > 0.5:
                continue
            if answers.get(f"q_{vr}_{vc}_discipline", {}).get("noul", 0) > 0.5:
                continue
            if _extract_days(val):
                continue
            # Skip dash-form apparatus bookings ("Name - Hoop", "Rose - Hoop TBC").
            if " - " in val:
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
        # Must run BEFORE the day-identified fix below.
        if not group_label and subject and subject in subjects_with_labels:
            continue

        # Day-identified columns: no group label, use the day name.
        if not group_label and days:
            group_label = days[0]

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
            is_student = answers.get(f"q_{vr}_{vc}_student", {}).get("noul", 0) > 0.5
            # Extract base name(s) from complex cell values.
            # "Billie (minor) dance trap" → "billie" (strip parens + trailing),
            # "Lucy & Nem" → ["lucy", "nem"],
            # "Charlie straps" → "charlie" (strip apparatus),
            # "Imogen H" → ["imogen h"] (keep multi-word names intact).
            if "&" in resolved:
                base = re.sub(r"\s*\(.*?\)", "", resolved)
                base = APPARATUS_TOKEN_RE.sub(" ", base)
                parts = [p.strip().lower() for p in base.split("&") if p.strip()]
            elif "(" in resolved:
                # "Billie (minor) dance trap" → "Billie"
                parts = [re.sub(r"\s*\(.*?\).*", "", resolved).strip().lower()]
            else:
                cleaned = APPARATUS_TOKEN_RE.sub(" ", resolved).strip()
                parts = [cleaned.lower()] if cleaned else []
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
                # Apply nickname merges and filter out teachers.
                norm_name = _normalize_name(sname)
                base_name = re.sub(r"\s*\(.*?\)", "", norm_name).strip()
                # Also strip trailing junk like "????" from names.
                base_name = re.sub(r"[^a-z ].*", "", base_name).strip()
                if norm_name in TEACHERS or base_name in TEACHERS:
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

    # Normalize subject names and PAR GROUP labels to match v1.
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
