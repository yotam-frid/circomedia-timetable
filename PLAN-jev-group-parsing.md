# Plan: Jev-Based Group Parsing (Phase 1)

## Goal

Replace `parse_roster()`, `parse_year_sheet()`, `parse_core_skills()`, `parse_year_maps()`, and `parse_groups()` in `timetable_to_ics.py` with Jev-powered classification. Output the same data structure so downstream code is unchanged.

## Architecture

```
xlsx group sheet
    ↓
openpyxl → cell dump (coordinates, values, bold, fill)
    ↓
jev_group_state.py → state text + domain rules
    ↓
jev_client.py → POST to OpenRouter System One API
    ↓
jev_group_questions.py → 3 Nouls per cell (discipline? group? student?)
    ↓
Python assembly → classify cells, build column structure
    ↓
jev_client.py → chained call: "which student?" with roster as options
    ↓
jev_group_assemble.py → {name: {subject: {labels, days, detail}}}
```

## Steps

### 1. `jev_client.py` — Jev API wrapper
- `jev_call(state: str, questions: dict) -> dict`
- Auth from `OPENROUTER_API_KEY` env var
- Endpoint: `POST https://openrouter.ai/api/alpha/decisions`
- Model: `~typesafe/jev-latest`
- Error handling, response parsing

### 2. `jev_group_state.py` — Build state from group sheet
- Input: openpyxl worksheet
- Output: state text string
- Includes: all cells with coordinates, values, bold, fill color
- Header rules: staff names, apparatus tokens, color→year, subject names, formatting semantics

### 3. `jev_group_questions.py` — Generate questions per cell
- Input: list of non-empty cells
- Output: questions dict for Jev
- 3 Noul questions per cell: is_discipline, is_group_title, is_student
- Plus Choice questions for row 4 (day text) and row 3 (subject text) per column

### 4. Chained student resolution
- Input: cells classified as student
- Output: resolved student names
- Choice question per cell: "Which student does '[value]' refer to?"
- Options: all candidate names from across all group sheets

### 5. `jev_group_assemble.py` — Build membership data structure
- Input: Jev answers
- Output: `{name_lower: {subject_key: {"labels": [...], "days": [...], "detail": {label: [days]}}}}`
- Same format as current `parse_year_sheet()` return value

### 6. Integration into `build_feeds.py`
- Replace `tt.parse_roster(wb)` with Jev-powered version
- Replace `tt.parse_groups(wb)` with Jev-powered version
- Keep everything else unchanged

### 7. Validation
- Diff output against current parser for Week 1 and Week 2 files
- Known acceptable differences: W1 vs W2/W3-5 roster changes
- Verify: same students, same group assignments, same year levels

## Token Budget Estimate

- Group sheet: ~200-400 non-empty cells
- State text: ~3,000-5,000 tokens (cells + rules header)
- Questions: ~600-1,200 Nouls (3 per cell) × ~10 tokens each = ~6,000-12,000 tokens
- Total per sheet: ~9,000-17,000 tokens (within 32k context)
- Cost per call: ~$0.0004

## Key Design Decisions

1. **Block-based, not cell-based**: blocks are shared-color regions; but for group sheets, cells are the natural unit (no block merging)
2. **No rigid row rules**: Jev classifies by content, not position
3. **Chained resolution**: classify first, then resolve against roster
4. **One call per group sheet**: safer than batching all 4 sheets
5. **Roster bootstrap**: first pass collects all name candidates, second pass resolves

## Files to Create

- `jev_client.py`
- `jev_group_state.py`
- `jev_group_questions.py`
- `jev_group_assemble.py`

## Files to Modify

- `build_feeds.py` — replace roster/group parsing calls
- `timetable_to_ics.py` — add Jev-powered parse functions alongside existing ones (keep old ones for validation)
