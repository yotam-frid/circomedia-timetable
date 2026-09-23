"""Classify cells via Jev (two passes) + resolve student names."""

from .jev_client import jev_call
from .jev_state import build_state

BATCH_SIZE = 40  # cells per classification call


def _non_empty_cells(ws):
    """Yield (row, col, value) for non-empty cells."""
    for row in ws.iter_rows():
        for cell in row:
            val = str(cell.value).strip() if cell.value is not None else ""
            if val:
                yield cell.row, cell.column, val


def _make_classify_questions(cells):
    """Build noul questions for a batch of (r, c, val) cells."""
    questions = {}
    for r, c, val in cells:
        prefix = f"q_{r}_{c}"
        questions[f"{prefix}_discipline"] = {
            "type": "noul",
            "instructions": f"Is '{val}' a discipline/subject name (e.g. Acro, Aerial, Manipulation, Conditioning, Devising, Context, Movement, Creative Project, Physical Theatre, PAR, Core Skills)?",
            "criteria": {
                "true": "It is a subject or discipline name",
                "false": "It is not a subject name",
            },
        }
        questions[f"{prefix}_group"] = {
            "type": "noul",
            "instructions": f"Is '{val}' a group title or label (e.g. Group 1, Group A, Major, Minors, All)?",
            "criteria": {
                "true": "It is a group label or title",
                "false": "It is not a group label",
            },
        }
        questions[f"{prefix}_student"] = {
            "type": "noul",
            "instructions": f"Is '{val}' a student name — a person who attends classes? Not a staff member, not an apparatus booking, not a day name, not a time, not a subject.",
            "criteria": {
                "true": "It is a student's name",
                "false": "It is not a student name",
            },
        }
        # Teacher detection
        questions[f"{prefix}_teacher"] = {
            "type": "noul",
            "instructions": f"Is '{val}' a teacher/staff name (NOT a student)? Teachers appear in row 3 (subject row) or comma-lists like 'Lisa, Ethan, Chané'. Students in row 5+ are NOT teachers even if they share a name with a teacher.",
            "criteria": {
                "true": "It is a teacher name (staff, not student)",
                "false": "It is not a teacher name (could be student, apparatus, etc.)",
            },
        }
        # Apparatus detection
        questions[f"{prefix}_apparatus"] = {
            "type": "noul",
            "instructions": f"Is '{val}' an apparatus booking? Looks like 'Name - Hoop', 'Name - Straps', 'Hoop TBC', 'Silks'. Contains apparatus words: Hoop, Rod, Straps, Rope, Trapeze, Silks, Dance Trap.",
            "criteria": {
                "true": "It is an apparatus booking",
                "false": "It is not an apparatus booking",
            },
        }
        # Junk detection
        questions[f"{prefix}_junk"] = {
            "type": "noul",
            "instructions": f"Is '{val}' clearly NOT a student name? Junk includes ONLY: day names (Monday...), week markers '(wk3)', 'need X,' notes, '?', numbers only, empty strings. A person's name (even if also a teacher name) is NOT junk.",
            "criteria": {
                "true": "It is clearly junk (day name, week marker, note, number)",
                "false": "It could be a student name (including names that might also be teachers)",
            },
        }
        # Group label detection (choice for specific label)
        questions[f"{prefix}_group_label"] = {
            "type": "choice",
            "instructions": f"Is '{val}' a group label? Examples: 'Group 1', 'Group A', 'Group C', 'Group D', 'Group 3', 'Major', 'Minors', 'PAR Group 1', 'PAR Group 2', 'All'. Not a subject, not a student name.",
            "criteria": {
                "group_1": "Group 1", "group_2": "Group 2", "group_3": "Group 3",
                "group_a": "Group A", "group_b": "Group B",
                "group_c": "Group C", "group_d": "Group D", "group_e": "Group E",
                "major": "Major", "minors": "Minors", "all": "All",
                "par_group_1": "PAR Group 1", "par_group_2": "PAR Group 2", "none": "Not a group label"
            },
        }
    return questions


def _classify_sheet(ws, year):
    """Pass 1: classify every non-empty cell, in batches.

    Returns (state, answers, name_candidates).
    """
    state = build_state(ws, year)
    all_cells = list(_non_empty_cells(ws))
    answers = {}

    for i in range(0, len(all_cells), BATCH_SIZE):
        batch = all_cells[i : i + BATCH_SIZE]
        questions = _make_classify_questions(batch)
        batch_answers = jev_call(state, questions)
        answers.update(batch_answers)

    name_candidates = []
    for r, c, val in all_cells:
        ans = answers.get(f"q_{r}_{c}_student", {})
        if ans.get("noul", 0) > 0.5:
            name_candidates.append(val)

    return state, answers, name_candidates


def classify_sheet(ws, year):
    """Two-pass classification of a group sheet.

    Pass 1: classify each cell (discipline / group / student) via nouls.
    Pass 2: for every student candidate, resolve it against the full
    candidate list collected across all sheets (choice question).

    Returns
    -------
    dict
        {
            "state": str,
            "answers": dict,
            "resolutions": dict,
            "name_candidates": list,
        }
    """
    state, answers, candidates = _classify_sheet(ws, year)

    # Pass 2: resolve each student candidate against the full list.
    unique_candidates = sorted(set(candidates))
    if not unique_candidates:
        return {
            "state": state,
            "answers": answers,
            "resolutions": {},
            "name_candidates": candidates,
        }

    # Build resolution questions for all student cells.
    res_questions = {}
    res_cells = []
    for r, c, val in _non_empty_cells(ws):
        ans = answers.get(f"q_{r}_{c}_student", {})
        if ans.get("noul", 0) > 0.5:
            criteria = {name: name for name in unique_candidates}
            res_questions[f"res_{r}_{c}"] = {
                "type": "choice",
                "instructions": f"Which student does '{val}' refer to?",
                "criteria": criteria,
            }
            res_cells.append((r, c))

    resolutions = {}
    # Batch resolution calls too if there are many students.
    res_keys = list(res_questions.keys())
    for i in range(0, len(res_keys), BATCH_SIZE):
        batch_keys = res_keys[i : i + BATCH_SIZE]
        batch_q = {k: res_questions[k] for k in batch_keys}
        batch_answers = jev_call(state, batch_q)
        for key, ans in batch_answers.items():
            resolutions[key] = ans.get("choice", "")

    return {
        "state": state,
        "answers": answers,
        "resolutions": resolutions,
        "name_candidates": candidates,
    }
