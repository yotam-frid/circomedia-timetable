"""Week-range inference from incoming xlsx filenames (pure, deterministic)."""

import re
from pathlib import Path

RANGE_RE = re.compile(r"weeks?\s*(\d+)\s*[-–]\s*(\d+)", re.I)
SINGLE_RE = re.compile(r"weeks?\s*(\d+)", re.I)
DASH_RE = re.compile(r"(?:^|[^a-z])(\d+)\s*[-–]\s*(\d+)", re.I)


def weeks_from_filename(path):
    """'Term 1a Weeks 3-5 2026 -.xlsx' -> [3,4,5]; 'Week 1' -> [1].

    Order: explicit range, single week number, dashed pair, default 1..36.
    """
    stem = Path(path).stem
    m = RANGE_RE.search(stem)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, min(hi, 52) + 1))
    m = SINGLE_RE.search(stem)
    if m:
        return [int(m.group(1))]
    m = DASH_RE.search(stem)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return list(range(lo, min(hi, 52) + 1))
    return list(range(1, 37))