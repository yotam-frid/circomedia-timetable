"""CLI entry point for v2 event pipeline.

Usage:
    python -m v2.v2_cli [xlsx_path ...] [--weeks 3,4,5] [--out dir] [--force]
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("Need openpyxl: pip install openpyxl")

from .jev_classify import classify_sheet
from .group_parse import assemble
from .day_classify import classify_day_sheet, extract_blocks
from .event_creator import build_events
from .feed_gen import generate_feeds
from .spaces import build_space_events, generate_space_feeds

GROUP_SHEET_RE = re.compile(r"(?:year\s*\d|core\s*skills)\s*group", re.I)
DAY_SHEET_RE = re.compile(r"^(?:monday|tuesday|wednesday|thursday|friday)", re.I)
YEAR_RE = re.compile(r"year\s*(\d)", re.I)
CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def _is_group_sheet(name):
    return bool(GROUP_SHEET_RE.search(name))


def _is_day_sheet(name):
    return bool(DAY_SHEET_RE.search(name.strip().lower()))


def _year_from_name(name):
    m = YEAR_RE.search(name)
    return int(m.group(1)) if m else None


def _cache_path(xlsx_path):
    """Cache key based on file name + size + mtime."""
    p = Path(xlsx_path)
    stat = p.stat()
    key = f"{p.name}_{stat.st_size}_{int(stat.st_mtime)}"
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"groups_{h}.json"


def _load_cache(xlsx_path):
    """Load cached group data if available."""
    cache_file = _cache_path(xlsx_path)
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except Exception:
            pass
    return None


def _save_cache(xlsx_path, data):
    """Save group data to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(xlsx_path)
    cache_file.write_text(json.dumps(data, indent=2))


def extract_groups_from_xlsx(wb, year_filter=None, force=False, xlsx_path=None):
    """Extract student-group relationships from group sheets.

    Uses cache unless force=True.
    Returns (groups_by_subject_year, students_by_year, student_group_data).
    """
    # Try cache first.
    if not force and xlsx_path:
        cached = _load_cache(xlsx_path)
        if cached:
            print("  (using cached group data)")
            return (
                cached["groups_by_subject_year"],
                {int(k): v for k, v in cached["students_by_year"].items()},
                cached["student_group_data"],
            )

    all_entries = []
    core_entries = []
    total_calls = 0

    for name in wb.sheetnames:
        if not _is_group_sheet(name):
            continue
        year = _year_from_name(name)
        is_cs = "core" in name.lower() and "skill" in name.lower()
        if year is None and not is_cs:
            continue

        ws = wb[name]
        if is_cs:
            print(f"  Classifying '{name}'...")
            classification = classify_sheet(ws, 0)
            entries = assemble(ws, classification)
            for student, info in entries:
                core_entries.append((student, info))
        else:
            if year_filter and year != year_filter:
                continue
            print(f"  Classifying '{name}' (Year {year})...")
            classification = classify_sheet(ws, year)
            entries = assemble(ws, classification, year=year)
            for student, info in entries:
                all_entries.append((student, year, info))
        total_calls += 2

    # Build per-student year map.
    by_student = {}
    for student, year, info in all_entries:
        by_student.setdefault(student.lower(), {}).setdefault(year, []).append(info)

    # Merge Core Skills.
    for student, info in core_entries:
        key = student.lower()
        if key not in by_student:
            by_student[key] = {}
        for year in by_student[key]:
            by_student[key][year].append(info)

    # Build students_by_year.
    students_by_year = {}
    for name_lower, year_map in by_student.items():
        for year, infos in year_map.items():
            display = infos[0].get("_display", name_lower)
            students_by_year.setdefault(year, []).append(display)

    # Build groups_by_subject_year from the student data.
    groups_by_subject_year = {}
    for name_lower, year_map in by_student.items():
        for year, infos in year_map.items():
            for info in infos:
                subj = info.get("subject", "")
                grp = info.get("group", "")
                if subj and grp:
                    groups_by_subject_year.setdefault(subj, {}).setdefault(year, set()).add(grp)
    # Convert sets to sorted lists.
    for subj in groups_by_subject_year:
        for year in groups_by_subject_year[subj]:
            groups_by_subject_year[subj][year] = sorted(groups_by_subject_year[subj][year])

    # Student group data for feed generation.
    student_group_data = {}
    for name_lower, year_map in by_student.items():
        for year, infos in year_map.items():
            student_group_data.setdefault(name_lower, []).extend(infos)

    print(f"  Groups extracted: {total_calls} Jev calls")

    # Cache.
    if xlsx_path:
        cache_data = {
            "groups_by_subject_year": groups_by_subject_year,
            "students_by_year": {str(k): v for k, v in students_by_year.items()},
            "student_group_data": student_group_data,
        }
        _save_cache(xlsx_path, cache_data)
        print(f"  (cached to {_cache_path(xlsx_path).name})")

    return groups_by_subject_year, students_by_year, student_group_data


def process_file(path, weeks=None, out_dir=None, force=False, student=None):
    """Full pipeline: groups -> classify day sheets -> events -> ICS."""
    wb = openpyxl.load_workbook(path, data_only=True)
    print(f"\n===== {path.name} =====\n")

    # Step 1: Extract groups.
    print("Step 1: Extracting student groups...")
    groups_by_subject_year, students_by_year, student_group_data = \
        extract_groups_from_xlsx(wb, force=force, xlsx_path=path)

    # Step 2: Classify day sheets and build events.
    print("\nStep 2: Classifying day sheets...")
    day_sheets = []
    for name in wb.sheetnames:
        if _is_day_sheet(name):
            ws = wb[name]
            day_sheets.append((name, ws))
            print(f"  Found day sheet: {name!r}")

    # Determine which weeks to cover.
    if weeks:
        weeks_to_cover = weeks
    else:
        # Infer from filename.
        m = re.search(r"weeks?\s*(\d+)", path.stem, re.I)
        if m:
            weeks_to_cover = [int(m.group(1))]
        else:
            m2 = re.search(r"(\d+)-(\d+)", path.stem)
            if m2:
                weeks_to_cover = list(range(int(m2.group(1)), int(m2.group(2)) + 1))
            else:
                weeks_to_cover = list(range(1, 37))
    print(f"  Weeks to cover: {weeks_to_cover}")

    print("\nStep 3: Building events...")
    all_events = build_events(
        day_sheets, groups_by_subject_year, students_by_year,
        student_group_data, weeks_to_cover=weeks_to_cover, wb=wb)

    # Print summary.
    total_events = sum(len(evts) for evts in all_events.values())
    print(f"  Total events across all students: {total_events}")

    # Step 4: Generate ICS files.
    if out_dir:
        print(f"\nStep 4: Generating ICS files...")
        count = generate_feeds(
            all_events, students_by_year, student_group_data,
            out_dir, weeks_to_cover=weeks_to_cover)
        print(f"  Wrote {count} student feed files")

        # Step 5: Generate space feeds.
        print(f"\nStep 5: Generating space feeds...")
        space_events = build_space_events(day_sheets, weeks_to_cover=weeks_to_cover)
        space_count = generate_space_feeds(space_events, out_dir)
        print(f"  Wrote {space_count} space feed files")

    # If specific student requested, show their events.
    if student:
        s_key = student.lower()
        events = all_events.get(s_key, [])
        group_data = student_group_data.get(s_key, [])
        year = None
        for y, students in students_by_year.items():
            if any(s.lower() == s_key for s in students):
                year = y
                break
        if events:
            from .feed_gen import dedup_events
            events = dedup_events(events)
            print(f"\n--- {student} (Year {year}) ---")
            for e in events:
                print(f"  {e['date']} {e['start'].strftime('%H:%M')}-{e['end'].strftime('%H:%M')} "
                      f"{e['name']} @ {e['location']} [week {e.get('week_num', '?')}]")
        else:
            print(f"\n  No events found for {student}")

    wb.close()
    return all_events


def main():
    ap = argparse.ArgumentParser(description="v2 timetable event pipeline")
    ap.add_argument("files", nargs="*", help="xlsx files (default: all in incoming/)")
    ap.add_argument("--weeks", type=str, default=None,
                    help="Comma-separated week numbers (e.g. 3,4,5)")
    ap.add_argument("--out", type=str, default=None,
                    help="Output directory for ICS files")
    ap.add_argument("--force", action="store_true",
                    help="Force rebuild (ignore cache)")
    ap.add_argument("--student", type=str, default=None,
                    help="Show events for a specific student")
    a = ap.parse_args()

    if a.files:
        paths = [Path(p) for p in a.files]
    else:
        incoming = Path(__file__).resolve().parent.parent / "incoming"
        paths = sorted(incoming.glob("*.xlsx"))

    if not paths:
        sys.exit("No xlsx files found")

    weeks = [int(w) for w in a.weeks.split(",")] if a.weeks else None

    for path in paths:
        process_file(path, weeks=weeks, out_dir=a.out,
                     force=a.force, student=a.student)


if __name__ == "__main__":
    main()
