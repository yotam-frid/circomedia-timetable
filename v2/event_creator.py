"""Build structured events from classified day-sheet blocks.

Takes classified blocks (subject, target, weeks) and student-group data,
then produces per-student event lists ready for ICS generation.
"""

import datetime as dt
import re

from .day_classify import TIME_RANGE_RE
from .group_parse import TEACHERS, MERGE_MAP

# Capture-free time-range splitter (the shared TIME_RANGE_RE has capture
# groups, which re.split would leak into the segment list).
_TIME_SPLIT_RE = re.compile(
    r"\d{1,2}\s*[.:]\s*\d{2}\s*[-\u2013]\s*\d{1,2}\s*[.:]\s*\d{2}")

TERM_WEEK1_MONDAY = dt.date(2026, 9, 14)

# Subjects that grant whole-cohort attendance without any per-student group
# data (Teacher Training = entire Year 2 cohort, colour-signalled).
WHOLE_COHORT = {"teacher training"}

# Labelled-session markers. "Group 1"/"Group A"/"PAR Group 2" texts make a
# block strict (label matching only); their absence allows the unmarked day-session
# rule (unmarked, same-colour, group-meets-that-weekday).
_GROUP_MARK_RE = re.compile(r"\bgroup\s+(?:[0-9]|[a-e])\b", re.I)
_PAR_MARK_RE = re.compile(r"\bpar\s+group\s+\d", re.I)
_EXPLICIT_AUDIENCE_RE = re.compile(
    r"\b(?:group\s+(?:[0-9]+|[a-e])|par\s+group\s+\d+|major|minors|"
    r"all(?:\s+(?:years?|yr))?|y(?:ea)?rs?\s*[123])\b", re.I)
_TEACHERS_BY_LEN = sorted(TEACHERS, key=len, reverse=True)


def _has_explicit_audience(texts):
    return bool(_EXPLICIT_AUDIENCE_RE.search(" | ".join(texts)))


def _parse_weeks(week_nums, weeks_to_cover):
    """Convert week nums from Jev to applicable weeks.

    week_nums: list of ints from Jev. Empty = all weeks.
    weeks_to_cover: the weeks in this xlsx file.
    """
    if not week_nums:
        return list(weeks_to_cover)
    return [w for w in week_nums if w in weeks_to_cover]


def week_monday(week_num):
    """Monday date for a given week number. Week 1 = Sep 14, 2026."""
    return TERM_WEEK1_MONDAY + dt.timedelta(weeks=week_num - 1)


def _text_subject_key(texts):
    """Subject key read off a block's own text.

    An unmarked class session is identified by a keyword in its text
    ('Acro minors | Ethan (& Lisa)' → 'acro'), while staff/appointment
    cells ('James | Rod', 'Tan - Jonathan') return None. Used to decide
    when an unnamed student_match/private block is really an unmarked
    class session that the day-session rule should grant.
    """
    s = " | ".join(texts).lower()
    if "aerial conditioning" in s:
        return "aerial_conditioning"
    if "teacher training" in s:
        return "teacher_training"
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


def _event_name(block):
    """Produce a human-readable event name from a block's texts.

    Event names are freeform — often a subject, but 'Joanna- Nicky' is
    also a valid event name. Jev knows about subjects but never chooses
    names: the name comes from the block's own text, so e.g.
    a block whose text says 'Dance' is named 'Dance', never a subject
    alias like 'Movement'.
    """
    # Embedded time ranges are stripped per slot, so a cell like
    # '10.35-10.50 Kitty' names the event 'Kitty' (never with the time).
    texts = block.get("texts", [])
    segs = _block_name_segments(texts) or texts
    j = " | ".join(segs)
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
        # fallback: first non-teacher/group text (freeform name)
        for t in segs:
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


def _teachers(texts):
    """Extract teacher names from block texts."""
    skip = {"group", "all", "yr", "year", "core skill", "tumbling",
            "handstand", "professional", "member", "context", "conditioning",
            "acro", "aerial", "manipulation", "physical theatre", "creative",
            "devising", "movement", "dance", "lecture", "majors", "minors",
            "student training ends", "closed", "warm up", "self led",
            "par", "clown", "stand up", "hoop", "capoeira", "forro"}
    teachers = []
    for t in texts:
        tl = t.lower()
        if "///" in t or TIME_RANGE_RE.fullmatch(t):
            continue
        if TIME_RANGE_RE.search(t):
            continue  # time+name header cell ('12.45 - 1.30 Joanna- Nicky')
        if any(k in tl for k in skip):
            continue
        if re.search(r"[A-Za-z]", t):
            teachers.append(t)
    return teachers


def _target_matches_student(target, subject, student_groups_for_subject, student_year,
                            group_allowed=True):
    """Check if an event's target applies to this student.

    Group matching is colour-blind: Core Skills groups are
    shared across years, so we check if the student has the matching
    group regardless of the year in the target. The year comes from
    the header cell colour and is unreliable for cross-year subjects.

    group_allowed gates EXPLICIT group-name matches (a student holding
    'Group C' for the subject). The allocator trusts a group label only
    when the block text actually names it ('Group c'); an unmarked block
    ('Acro Majors | Lisa & Ethan') is a day-session, not a group
    session, so it must never grant by group name alone. Cohort targets
    ('All Years', 'All Year N', 'All (subj, year)') are explicit
    audience labels and always apply.
    """
    if target == "All Years":
        return True

    m = re.match(r"All Year (\d)", target, re.I)
    if m:
        return student_year == int(m.group(1))

    # "Group X (Subject, Year N)" or "Group X (Subject, N)" — extract group.
    m = re.match(r"(.+?)\s*\((.+?),\s*(?:Year\s+)?(\d)\)", target)
    if m:
        group = m.group(1).strip()
        # "All" means all students in this year for this subject. A student
        # must actually hold at least one group for the subject (a wholeton
        # subject like Aerial is not every Year-2 student; Martha has no
        # Aerial group and the allocator never grants her the block).
        if group.lower() == "all":
            return bool(student_groups_for_subject)
        # Explicit group names only match when the block text names the
        # group: an unmarked cross-colour block whose Jev
        # target drifted to a group ('Major (Acro, 2)' on a Wed 'Acro
        # Majors' block) must not mint events for that group.
        if not group_allowed:
            return False
        # Check if student has this group for this subject (year-blind).
        # Support compound group names like "Major + Minor":
        # a student in "Major" or "Minor" should match.
        if group.lower() in student_groups_for_subject:
            return True
        # Check if each part of a compound group name is in student's groups
        parts = re.split(r"\s*\+\s*", group)
        if len(parts) > 1:
            return any(p.lower() in student_groups_for_subject for p in parts)
        return False

    # Bare group name — no year context, can't match safely.
    return False


def _leading_dash_cell(texts):
    """True when a segment is in the 'time - Name' layout ('11.45-12.00 -
    Joanna', '10.55-11.10 -Kitty'). The pre-dash is empty, so no name can be
    recovered from it and consequently never emits an event for these
    Meeting Room slots. Kept as a deliberate allocation quirk."""
    for seg in _block_name_segments(texts):
        s = seg.strip()
        if s.startswith("-") or s.startswith("\u2013"):
            return True
    return False


def _block_name_segments(texts):
    """Split block texts into name-bearing segments.

    The parser recovers names hidden inside time-header cells ('12.45 - 1.30
    Joanna- Nicky 12.45 - 1.30 Kitty - Jonathan'), so a cell can hold
    several 1-to-1 segments. Splitting on embedded time ranges yields
    each attendee's own slot: ['Joanna- Nicky', 'Kitty - Jonathan'].
    """
    segs = []
    for t in texts:
        for piece in _TIME_SPLIT_RE.split(t):
            piece = piece.strip()
            if piece:
                segs.append(piece)
    return segs


def _seg_pre_dash(seg):
    """Teacher-stripped, parenthesised-credit-stripped pre-dash segment."""
    low = seg.lower()
    low = re.sub(r"\(.*?\)", "", low)
    for teacher in _TEACHERS_BY_LEN:
        low = low.replace(teacher, " ")
    low = re.sub(r"\s+", " ", low).strip()
    if not low:
        return ""
    return re.split(r"-(?=\s|\w|$)", low, maxsplit=1)[0].strip()


def _seg_hits_token(seg, token):
    pre = _seg_pre_dash(seg)
    if not pre:
        return False
    return re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", pre) is not None


def _block_names_student(texts, roster_tokens):
    """True if any block text explicitly names a roster student.

    Uses a named-first branch: a block containing a real student's
    name resolves to that student regardless of subject/target. Staff
    mentions, parenthesised credits ('Ethan (& Lisa)'), dash-form apparatus
    bookings and pure time headers never count. Names embedded in
    time-header cells (Meeting Room slots, shared 1-to-1 blocks) do.
    """
    for seg in _block_name_segments(texts):
        if "///" in seg:
            continue
        for cand in roster_tokens:
            if _seg_hits_token(seg, cand):
                return True
    return False


def _block_student_hits(texts, student_tokens):
    """Which roster-verifiable students are deterministically named?

    Names are matched exactly with the candidate set (name + aliases)
    and never via the LLM; seeds let a clearly-named block resolve without an
    LLM round-trip (immune to classifier flakiness).
    """
    hits = []
    segs = _block_name_segments(texts)
    for s_low, toks in student_tokens.items():
        if any(_seg_hits_token(seg, tok) for seg in segs for tok in toks):
            hits.append(s_low)
    return hits


def _student_tokens_by_key(students_by_year):
    """Per-student match tokens: display name, its words, nickname aliases."""
    out = {}
    for year, names in students_by_year.items():
        for disp in names:
            low = disp.lower()
            canonical = MERGE_MAP.get(low, low)
            toks = {low} | set(low.split())
            for nick, canon in MERGE_MAP.items():
                if canon == canonical:
                    toks.add(nick)
            out[low] = toks
    return out


def _event_name_for_student(block, s_key, student_tokens):
    """Per-attendee event title for shared 1-to-1 slots.

    The title narrows to the student's own segment when the block holds
    SEVERAL dash-form attendee segments ('Joanna- Nicky ... '
    'Kitty - Jonathan' → 'Joanna- Nicky' for Joanna, 'Kitty - Jonathan'
    for Kitty). An attendee-list text in an otherwise single-name block
    ('Theo, Corina, Silas, Yotam, JJ' inside a 'Creative Project' block)
    still titles from the block-wide name, never the list.
    """
    texts = block.get("texts", [])
    segs = _block_name_segments(texts)
    toks = student_tokens.get(s_key, {s_key})
    mine = [seg for seg in segs
            if "///" not in seg
            and any(_seg_hits_token(seg, tok) for tok in toks)]
    if len(mine) == 1:
        others = [t for t in segs if t != mine[0]
                  and re.search(r"\w\s+-\s*|\s*-\s+\w", t)]
        if others:
            return _event_name({"texts": [mine[0]]})
    return _event_name(block)


def _day_session_matches(weekday, subject, cy, sy, days_by_subject):
    """Day-session rule: an unmarked, same-year-coloured session on a day
    the student's group meets ('Acro | Lisa and Ethan'). Caller guarantees
    the block has no Group/PAR marker in its text and the colour gate passed.
    """
    days = days_by_subject.get(subject.lower(), set())
    if not days:
        return False
    return DAY_LABELS[weekday] in days


DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _day_from_sheetname(name):
    """Extract weekday index and name from sheet name."""
    low = name.strip().lower()
    for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday"]):
        if low.startswith(d):
            return i, d.capitalize()
    return None, None


def _resolve_student_match_batch(blocks_by_year, students_by_year, wb, seeds=None):
    """Map student_match blocks to their attendees, deterministically.

    A 1-to-1 block is attributed ONLY by explicit roster name in the text,
    never by inference. So the attendee list IS the seeded name hits
    (exact-name matching, nickname aliases via MERGE_MAP). Blocks without
    any recoverable name yield no event ('11.45-12.00 - Joanna' never lands
    because the pre-dash parse can't recover her).

    Parameters
    ----------
    blocks_by_year : dict {year: [(block, subject), ...]}
    students_by_year : dict {year: [student_names]}, unused (kept for shape)
    wb : openpyxl workbook, unused
    seeds : dict {(year, block_idx): [student_keys]}, optional

    Returns
    -------
    dict {(year, block_idx): [matched_student_keys]}
    """
    results = {}
    for year, blocks in blocks_by_year.items():
        for i in range(len(blocks)):
            results[(year, i)] = sorted(set((seeds or {}).get((year, i), [])))
    return results


def build_events(day_sheets, groups_by_subject_year, students_by_year,
                 student_group_data, weeks_to_cover=None, wb=None,
                 cache_name=None):
    """Build per-student events from classified day sheets.

    Parameters
    ----------
    day_sheets : list of (sheet_name, worksheet) tuples
    groups_by_subject_year : dict {subject: {year: [groups]}}
    students_by_year : dict {year: [student_names]}
    student_group_data : dict {student_lower: [info, ...]}
        From v2 group_parse.assemble().
    weeks_to_cover : list of int, optional
    wb : openpyxl workbook, for student_match resolution.
    cache_name : str, optional
        xlsx filename used to key the per-sheet classification cache.

    Returns
    -------
    dict {student_lower: [event_dict, ...]}
    """
    if weeks_to_cover is None:
        weeks_to_cover = list(range(1, 37))

    # Build per-student group lookup: {student_lower: {subject_lower: set(groups_lower)}}
    student_subj_groups = {}
    for s_key, infos in student_group_data.items():
        for info in infos:
            subj = (info.get("subject") or "").lower()
            grp = (info.get("group") or "").strip().lower()
            if subj and grp:
                student_subj_groups.setdefault(s_key, {}).setdefault(subj, set()).add(grp)

    # Build per-student year lookup (coerce to int for safe comparison).
    student_year = {}
    for year, students in students_by_year.items():
        for s in students:
            student_year[s.lower()] = int(year)

    # Roster name tokens for named-first matching + per-subject day sets for
    # the day-session rule (student's group meets on that weekday).
    student_tokens = _student_tokens_by_key(students_by_year)
    roster_tokens = set().union(*student_tokens.values()) if student_tokens else set()
    student_days_by_subject = {}
    for s_key, infos in student_group_data.items():
        for info in infos:
            subj = (info.get("subject") or "").lower()
            days = info.get("days") or []
            if days:
                student_days_by_subject.setdefault(s_key, {}).setdefault(subj, set()).update(days)

    by_student = {}

    # Parse colour legend from Monday sheet for year detection.
    legend = None
    if wb is not None:
        from .day_classify import parse_legend
        legend = parse_legend(wb)

    # Build roster student list for owner_student criteria
    roster_students = []
    for year, names in students_by_year.items():
        roster_students.extend(names)

    # Phase 1: Classify all sheets, collect non-student_match events and
    # group student_match blocks by year for batch resolution.
    student_match_by_year = {}  # {year: [(weekday, cls, block, subject), ...]}
    seeds = {}  # {(year, idx): [student_keys]} deterministic name matches

    for sheet_name, ws in day_sheets:
        from .day_classify import classify_day_sheet, _merge_map, cell_year
        classified = classify_day_sheet(ws, sheet_name, groups_by_subject_year,
                                        weeks_to_cover, wb=wb, legend=legend,
                                        cache_name=cache_name, roster_students=roster_students)
        weekday, _ = _day_from_sheetname(sheet_name)
        # "Other" header fills (red Manipulation 'Group B', hire-blue,
        # Diploma pink, theme-5 staff slots) are read here rather than in
        # extract_blocks on purpose: this signal only matters to allocation,
        # so it must not change the block dict that the day-sheet cache
        # fingerprint hashes (that would invalidate every cached
        # classification and force a full Jev re-run for nothing).
        mm = _merge_map(ws) if legend is not None else {}

        for cls in classified:
            block = cls["block"]
            subject = cls["subject"]
            target = cls["target"]
            owner_student = cls.get("owner_student")
            is_private_lesson = cls.get("is_private_lesson")
            named = _block_names_student(block["texts"], roster_tokens)
            marked = bool(_GROUP_MARK_RE.search("|".join(block["texts"]))
                          or _PAR_MARK_RE.search("|".join(block["texts"])))

            other_fill = (
                legend is not None
                and cell_year(ws, block["start_row"], block["col"], mm,
                              legend) == "other"
            )
            if other_fill and not named and not _has_explicit_audience(block["texts"]):
                continue

            # Named-first: any block naming a roster student is
            # that student's session — Creative Project teams, owner 1-to-1s
            # ('Charlie (Creative)'), named slots, duets — regardless of
            # subject or how Jev classified it. Attendees are seeded
            # deterministically (name hits) so clearly-named blocks never
            # depend on classifier flakiness.
            named_keys = set()
            is_student_match = (named or target == "student_match"
                               or owner_student not in (None, "none")
                               or is_private_lesson)
            # The day-session rule handles unnamed Jev student_match/private
            # blocks that carry a real subject key in their text ('Acro
            # minors | Ethan (& Lisa)' → 'acro'). It grants these via the
            # unmarked day-session rule (skey + colour gate + group-meets-
            # weekday), never as 1-to-1 blocks. Staff/appointment cells
            # ('James | Rod', 'Tan - Jonathan') have no subject key, so
            # they stay silent. Neutralise the target to the bare subject
            # so the day-session branch below ({target != "student_match"})
            # can fire — seeding an empty student_match block would just
            # drop it at {if not named: continue}. Only target exactly
            # 'student_match' counts: group/all-year/whole-cohort targets
            # are already wired to their own deterministic paths, and an
            # explicitly marked block goes to group matching, not here.
            text_key = _text_subject_key(block["texts"])
            day_session_fallback = (not named and not marked
                                    and text_key is not None
                                    and target == "student_match")
            if day_session_fallback:
                is_student_match = False
                target = subject
            if is_student_match:
                cy = block.get("color_year")
                hits = (_block_student_hits(block["texts"], student_tokens)
                        if named else [])
                # If owner_student is set, add that student to hits
                if owner_student and owner_student not in (None, "none"):
                    hits.append(owner_student.lower())
                # Route to the colour's year AND every year whose roster
                # actually contains a named attendee ('Dance | All Yr 1 |
                # Charlie' seeds Year 3 for Charlie while colour=1 grants
                # the Year-1 cohort).
                years = set()
                if cy is not None and cy in students_by_year:
                    years.add(cy)
                for s in hits:
                    for y, names in students_by_year.items():
                        if s in {n.lower() for n in names}:
                            years.add(int(y))
                if not years:
                    years = {int(y) for y in students_by_year}
                for y in sorted(years):
                    idx = len(student_match_by_year.setdefault(y, []))
                    student_match_by_year[y].append((weekday, cls, block, subject))
                    seeded = [s for s in hits
                              if s in {n.lower() for n in students_by_year.get(y, [])}]
                    seeds[(y, idx)] = seeded
                    named_keys.update(seeded)
                # Membership is per-student OR ('block belongs if ANY'):
                # a group block whose teacher row names a student ('Aerial |
                # Group C | Eloise, Janine, Joe') grants the GROUP C members
                # the session AND Eloise by name. So named blocks fall
                # through to group/colour/day matching, skipping only the
                # students who are already getting it by name. A block Jev
                # labelled student_match with no real name yields nothing.
                if not named:
                    continue
                # OR-membership: named blocks fall through ONLY when they
                # also carry an explicit group label or all-year cohort.
                # A named 1-to-1 ('Charlie straps - Janine', a Meeting Room
                # slot misread as a class) grants nobody else.
                if not (marked or target == "All Years"
                        or re.match(r"All Year \d", str(target))):
                    continue

            # Drop 'time - Name' layouts (leading-dash cells): the
            # pre-dash is empty so no attendee is recoverable and the slot
            # never becomes an event ('12.05-12.20 - Nem' @ Meeting Room
            # is Nem's 1-to-1, not a PAR group session, and stays silent).
            # Jev's is_private_lesson handles this; if it's a private lesson
            # but no owner_student, it's a leading-dash cell with no student.
            is_private = cls.get("is_private_lesson", False)
            owner_student = cls.get("owner_student")
            if is_private and not owner_student and not day_session_fallback:
                continue

            # Skip non-classes and unresolved subjects outright.
            if subject.lower() in ("not a class", "unknown"):
                continue
            # Class/group events must be a real subject students group
            # under (or a whole-cohort subject like Teacher Training),
            # else nothing meaningful can match (and unguarded
            # 'All Years' targets would mint junk for everyone).
            # Match case-insensitively against groups_by_subject_year keys.
            subject_key = next((k for k in groups_by_subject_year if k.lower() == subject.lower()), None)
            if subject_key is None and subject.lower() not in WHOLE_COHORT:
                continue
            # Core Skills runs Mon-Thu mornings only (cross-year groups).
            # A Friday Core Skills block is a classification hallucination.
            if weekday == 4 and subject.lower() == "core skills":
                continue
            # BTEC cohort blocks never match regular-year students by year
            # or group; only named 1-to-1s (handled via student_match) land.
            if block.get("btec"):
                continue

            # Non-student_match: assign to matching students.
            name = _event_name(block)
            teachers = _teachers(block["texts"])

            block_weeks = _parse_weeks(cls["weeks"], weeks_to_cover)
            applicable_weeks = [w for w in block_weeks if w in weeks_to_cover]
            if not applicable_weeks:
                continue

            for week_num in applicable_weeks:
                monday = week_monday(week_num)
                event_date = monday + dt.timedelta(days=weekday)
                (sh, sm), (eh, em) = block["start_time"], block["end_time"]
                start = dt.datetime(event_date.year, event_date.month, event_date.day, sh, sm)
                end = dt.datetime(event_date.year, event_date.month, event_date.day, eh, em)
                if end <= start:
                    start += dt.timedelta(hours=12)
                    end += dt.timedelta(hours=12)

                event = {
                    "date": event_date, "start": start, "end": end,
                    "name": name, "location": block["location"],
                    "subject": subject, "teachers": teachers,
                    "weeks": applicable_weeks, "week_num": week_num,
                    "target": target,
                    "color_year": block.get("color_year"),
                }

                for s_key, subj_groups in student_subj_groups.items():
                    if s_key in named_keys:
                        continue  # getting it by name (named branch wins)
                    sy = student_year.get(s_key)
                    if sy is None:
                        continue
                    # When block has a color_year, the student's year must match.
                    # Colour is authoritative (AGENTS.md: "Colour is load-bearing").
                    # Exception: Core Skills is always yellow (Year 1 colour) but
                    # its groups are shared across years — group matching alone
                    # determines eligibility.
                    cy = block.get("color_year")
                    if cy is not None and sy != cy and subject.lower() != "core skills":
                        continue
                    sl = subject.lower()
                    # Explicit group names only match when the block text
                    # carries the group label. Cohort targets
                    # ('All', 'All Year N') always apply; an unmarked block
                    # falls through to the day-session rule below.
                    if _target_matches_student(target, subject, subj_groups.get(sl, set()), sy,
                                               group_allowed=marked):
                        by_student.setdefault(s_key, []).append(event)
                        continue
                    # Whole-cohort subjects (Teacher Training) grant without
                    # any per-student group entry.
                    if sl in WHOLE_COHORT:
                        by_student.setdefault(s_key, []).append(event)
                        continue
                    # Day-session: unmarked, same-colour session on a day
                    # the student's group meets. Never on 1-to-1-resolved
                    # blocks (a named appointment is nobody else's lesson).
                    if not marked and target != "student_match" and _day_session_matches(
                            weekday, sl, cy, sy, student_days_by_subject.get(s_key, {})):
                        by_student.setdefault(s_key, []).append(event)
                        continue
                    # Context 3 whole-cohort company meeting (Pro Tour briefs).
                    if sl == "context 3" and cy is not None and cy == sy \
                            and "company meeting" in "|".join(block["texts"]).lower():
                        by_student.setdefault(s_key, []).append(event)

    # Phase 2: Batch-resolve student_match blocks per year.
    if student_match_by_year and wb:
        total = sum(len(v) for v in student_match_by_year.values())
        seeded = sum(1 for v in seeds.values() if v)
        print(f"  Resolving {total} 1-to-1 blocks across "
              f"{len(student_match_by_year)} years "
              f"({seeded} seeded deterministically)...")

        # Build per-year block lists for batch resolution.
        batch_input = {}
        for year, entries in student_match_by_year.items():
            batch_input[year] = [(e[2], e[3]) for e in entries]  # (block, subject)

        match_results = _resolve_student_match_batch(
            batch_input, students_by_year, wb, seeds=seeds)

        # Create events for resolved student_match blocks.
        for year, entries in student_match_by_year.items():
            for idx, (weekday, cls, block, subject) in enumerate(entries):
                matched = match_results.get((year, idx), [])
                if not matched:
                    continue

                teachers = _teachers(block["texts"])

                block_weeks = _parse_weeks(cls["weeks"], weeks_to_cover)
                applicable_weeks = [w for w in block_weeks if w in weeks_to_cover]
                if not applicable_weeks:
                    continue

                for week_num in applicable_weeks:
                    monday = week_monday(week_num)
                    event_date = monday + dt.timedelta(days=weekday)
                    (sh, sm), (eh, em) = block["start_time"], block["end_time"]
                    start = dt.datetime(event_date.year, event_date.month, event_date.day, sh, sm)
                    end = dt.datetime(event_date.year, event_date.month, event_date.day, eh, em)
                    if end <= start:
                        start += dt.timedelta(hours=12)
                        end += dt.timedelta(hours=12)

                    for s_key in matched:
                        event = {
                            "date": event_date, "start": start, "end": end,
                            # Shared 1-to-1 slots are titled per attendee:
                            # 'Joanna- Nicky  Kitty - Jonathan' becomes each
                            # student's own segment.
                            "name": _event_name_for_student(
                                block, s_key, student_tokens),
                            "location": block["location"],
                            "subject": subject, "teachers": teachers,
                            "weeks": applicable_weeks, "week_num": week_num,
                            "target": f"student_match ({', '.join(sorted(matched))})",
                        }
                        by_student.setdefault(s_key, []).append(event)

    return by_student
