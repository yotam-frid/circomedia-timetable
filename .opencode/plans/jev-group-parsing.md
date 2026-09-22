# Plan: Jev-Based Timetable Ingestion v2

## Directory Structure

All v2 code lives in `v2/` — completely separate from the existing pipeline.

```
circomedia-timetable/
  v2/                          # NEW — all v2 code here
    __init__.py
    jev_client.py              # Jev API wrapper
    jev_state.py               # xlsx -> Jev state text
    jev_classify.py            # Cell classification + student resolution
    group_parse.py             # Assemble group membership from Jev answers
    main.py                    # Entry point: python -m v2.main
  incoming/                    # existing — xlsx source data (unchanged)
  timetable_to_ics.py          # existing — v1 parser (untouched)
  build_feeds.py               # existing — v1 orchestrator (untouched)
  ...
```

## Phase 1 Goal: Group Parsing Experiment

A standalone script that reads xlsx group sheets via Jev and prints every student with their year and groups:

```
Edie (Year 1)
  Acro - Group 1
  Aerial - Group C
  Conditioning - Group A
Laurie (Year 1)
  Acro - Group 1
  Aerial - Group C
Billie (Year 3)
  Acro - Billie
  Manipulation - Mon
  Physical Theatre - Wed
  ...
```

Year from sheet name ("Year 1 Groups" -> Year 1). Groups include subject + label. Fetching xlsx remains the same for now.

## Architecture

```
xlsx file
    |
openpyxl -> read group sheets (Year 1/2/3 Groups, Core Skills)
    |
v2/jev_state.py -> cell dump + domain rules per sheet
    |
v2/jev_client.py -> POST to OpenRouter System One API
    |
For each cell: 3 Nouls (discipline? group? student?)
    |
Python: collect all name candidates across all sheets
    |
Chained call: "which student?" with candidate list as options
    |
Python: assemble {student: [{year, subject, group, days}]}
    |
Print formatted output
```

## Steps

### 1. `v2/jev_client.py` — Jev API wrapper (~30 lines)

- `jev_call(state: str, questions: dict) -> dict`
- Auth from `OPENROUTER_API_KEY` env var (loaded from `.env`)
- Endpoint: `POST https://openrouter.ai/api/alpha/decisions`
- Model: `~typesafe/jev-latest`
- Returns `response["answers"]`

### 2. `v2/jev_state.py` — Build state from a group sheet (~60 lines)

- Input: openpyxl worksheet, year number
- Output: state text string
- Format: header rules + cell dump

```
YEAR 1 GROUPS — Circomedia timetable
RULES:
- Staff names: Ethan, Marcus, Chane, Sorcha, Jamie, Lisa, Rosy, Owen, Emily Orme, Heather Parkin, Mark Parfitt-Jones, Lewis Trump, Moira Hunt, Aimee Bennett, Joe Palmer, Charlie White, Denis, Janine, Nicky, Jonathan
- Apparatus tokens: Hoop, Rod, Straps, Rope, Trapeze, Silks, Dance Trap
- Dash-form "X - Apparatus" means X is a student, not apparatus
- Parenthesised text like "(Creative)" is an owner tag, not a person
- "(minor)" is a group-level annotation, not a person
- Bold text is likely a group label
- "Group 1", "Group A", "Major", "Minors" are group labels
- "All" means all students in that year
- Day names: Monday, Tuesday, Wednesday, Thursday, Friday

CELLS:
(3,2) 'Acro' [bold=True, merged=B3:C3]
(4,2) 'Tuesday and Thursday' [bold=False, merged=B4:C4]
(5,2) 'Group 1' [bold=True]
(6,2) 'Edie' [bold=False]
(7,2) 'Laurie' [bold=False]
...
```

### 3. `v2/jev_classify.py` — Classify cells + resolve students (~100 lines)

**Pass 1 — Classify** (one Jev call per group sheet):
For each non-empty cell, 3 Noul questions:
- `q_{r}_{c}_discipline`: "Is this cell a discipline/subject name like Acro, Aerial, Manipulation?"
- `q_{r}_{c}_group`: "Is this cell a group title or label like Group 1, Major, Minors?"
- `q_{r}_{c}_student`: "Is this cell a student name?"

**Pass 2 — Resolve** (one chained Jev call per sheet):
For each cell classified as student, one Choice question:
- "Which student does '[value]' refer to?"
- Options: all name candidates collected from Pass 1 across all sheets

### 4. `v2/group_parse.py` — Assemble + print (~80 lines)

- Input: Jev answers from both passes
- Build: `{student_name: [{year, subject, group_label, days}]}`
- For each column:
  - Subject comes from row 3 (deterministic, forward-filled)
  - Group comes from row 5 (classified by Jev as group label)
  - Days come from row 4 (deterministic text parsing)
- Output: sorted, formatted printout

### 5. `v2/main.py` — Entry point (~30 lines)

- `python -m v2.main` or `python v2/main.py`
- Args: xlsx path (default: all files in `incoming/`)
- Loads workbook, iterates group sheets
- Calls jev_classify -> group_parse -> print
- Prints summary: total students, total groups, Jev call count, cost

## Output Format

```
===== Term 1a Week 1 2026.xlsx =====

Abigail (Year 1)
  Context 1 - Abigail
  Devising - Abigail
  Movement - Abigail
Billie (Year 3)
  Acro - Billie
  Manipulation - Mon
  Physical Theatre - Wed
  Context 3 (Pro Tour)
  PAR Group 1
...

Total: 52 students
Jev calls: 8 (4 group sheets x (1 classify + 1 resolve))
Cost: ~$0.003
```

## Key Design Decisions

1. **Separate `v2/` directory** — clean break from existing code, no mixing
2. **Standalone script** — not integrated into build_feeds.py yet
3. **One Jev call per group sheet for classification** — safer than batching
4. **One chained call per sheet for resolution** — uses all name candidates as options
5. **No rigid row rules** — Jev classifies by content, not position
6. **Year from sheet name** — "Year 1 Groups" -> Year 1, no Jev needed
7. **Subject from row 3** — forward-filled across merged columns, deterministic
8. **Days from row 4** — deterministic text parsing (Mon/Tue/etc.)

## Files to Create

- `v2/__init__.py`
- `v2/jev_client.py`
- `v2/jev_state.py`
- `v2/jev_classify.py`
- `v2/group_parse.py`
- `v2/main.py`

## Files to Reference (read-only)

- `timetable_to_ics.py` — `TEACHERS` set, `APPARATUS_TOKEN_RE`, `MERGE_MAP`, `norm_subject_key()`
- `incoming/*.xlsx` — source data

## Token Budget Estimate

- Group sheet state: ~3,000-5,000 tokens (cells + rules)
- Classification questions: ~200 cells x 3 Nouls x ~10 tokens = ~6,000 tokens
- Total per sheet: ~9,000-15,000 tokens (within 32k context)
- 4 sheets x 2 calls = 8 Jev calls per file
- Cost: ~$0.003 per file
