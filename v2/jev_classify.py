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
