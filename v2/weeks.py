"""Week-range inference from incoming xlsx filenames (pure, deterministic).

This module is the single source of truth for timetable filename parsing.
The fetcher deliberately does NOT parse names (it downloads every .xlsx), so
build_feeds_v2.find_weeks is the only gate and it calls is_timetable_name here.
"""

import re
from pathlib import Path

# A standalone number: non-alphanumeric on both sides, so "1a"/"2b" (term
# markers) and "2026" adjacent to letters never match.
NUM_RE = re.compile(r"(?<![A-Za-z0-9])(\d+)(?![A-Za-z0-9])")
# Dash chain continuation, tolerant of en/em dashes and surrounding spaces.
DASH_RE = re.compile(r"\s*[-‐-―]\s*(\d+)\b")
TERM_RE = re.compile(r"\bterm\b", re.I)
WEEK_RE = re.compile(r"\bweeks?\b", re.I)

MAX_WEEK = 52


def is_timetable_name(name):
    """True if a filename looks like a timetable workbook (gate for builders).

    Requires a 'term' and a 'week' token so stray workbooks such as
    'Floor 2 plan.xlsx' are never handed to the classifier. Does not attempt
    to read the week number — that is weeks_from_filename's job.
    """
    return bool(TERM_RE.search(name) and WEEK_RE.search(name))


def weeks_from_filename(path):
    """'Term 1a Weeks 3-5 2026 -.xlsx' -> [3,4,5]; 'Week 1' -> [1].

    Takes the first standalone number, then extends along a dash chain
    ('3-5', '12 – 14'). The chain stops on a non-increasing or out-of-range
    value, so a trailing ' -' and 'Week 1 - 2026' stay inert. Returns None
    when no standalone number is present; the caller decides what that means.
    """
    stem = Path(path).stem
    m = NUM_RE.search(stem)
    if not m:
        return None
    lo = int(m.group(1))
    weeks = [lo]
    pos = m.end()
    while True:
        d = DASH_RE.match(stem, pos)
        if not d:
            break
        hi = int(d.group(1))
        if hi < lo or hi > MAX_WEEK:
            break
        weeks = list(range(lo, hi + 1))
        pos = d.end()
    return weeks
