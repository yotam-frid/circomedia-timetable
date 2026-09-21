"""Entry point: python -m v2.main [--year N] [xlsx_path ...]

Reads xlsx group sheets, classifies cells via Jev, and prints every
student with their year and groups.
"""

import argparse
import sys
import re
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("Need openpyxl: pip install openpyxl")

from .jev_classify import classify_sheet
from .group_parse import assemble

YEAR_RE = re.compile(r"year\s*(\d)", re.I)
GROUP_SHEET_RE = re.compile(r"(?:year\s*\d|core\s*skills)\s*group", re.I)

SUBJECT_NORMALIZE = {
    "core skills": "Core Skills",
    "aerial conditioning": "Aerial Conditioning",
    "physical theatre": "Physical Theatre",
    "context 1": "Context 1",
    "context 2": "Context 2",
    "context 3": "Context 3",
    "pro tour": "Context 3",
    "par": "PAR",
    "stand up": "Stand Up",
}


def _normalize_subject(s):
    low = (s or "").strip().lower()
    for pattern, canon in SUBJECT_NORMALIZE.items():
        if pattern in low:
            return canon
    # Title-case unknown subjects
    return (s or "Unknown").strip().title()


def _year_from_name(sheet_name):
    m = YEAR_RE.search(sheet_name)
    if m:
        return int(m.group(1))
    return None


def _is_core_skills(name):
    return "core" in name.lower() and "skill" in name.lower()


def _is_group_sheet(name):
    return bool(GROUP_SHEET_RE.search(name))


def process_file(path, year_filter=None):
    wb = openpyxl.load_workbook(path, data_only=True)
    print(f"\n===== {path.name} =====\n")

    all_entries = []  # (student, year, info)
    core_entries = []  # (student, info) from Core Skills
    total_calls = 0

    for name in wb.sheetnames:
        if not _is_group_sheet(name):
            continue
        year = _year_from_name(name)
        is_cs = _is_core_skills(name)
        if year is None and not is_cs:
            continue

        ws = wb[name]
        if is_cs:
            print(f"  Processing '{name}'...")
            classification = classify_sheet(ws, 0)
            entries = assemble(ws, classification)
            for student, info in entries:
                core_entries.append((student, info))
        else:
            if year_filter and year != year_filter:
                continue
            print(f"  Processing '{name}' (Year {year})...")
            classification = classify_sheet(ws, year)
            entries = assemble(ws, classification, year=year)
            for student, info in entries:
                all_entries.append((student, year, info))
        total_calls += 2

    # Build per-student year map from year sheets.
    by_student = {}  # name -> {year: [info]}
    for student, year, info in all_entries:
        by_student.setdefault(student.lower(), {}).setdefault(year, []).append(info)

    # Merge Core Skills into years the student actually belongs to.
    for student, info in core_entries:
        key = student.lower()
        if key not in by_student:
            by_student[key] = {}
        for year in by_student[key]:
            by_student[key][year].append(info)

    # Format and print output.
    for name_lower in sorted(by_student):
        year_map = by_student[name_lower]
        for year in sorted(year_map):
            infos = year_map[year]
            # De-duplicate: same subject+group+days
            seen = set()
            lines = []
            for info in infos:
                subj = _normalize_subject(info["subject"])
                grp = info["group"]
                days = " ".join(info["days"]) if info["days"] else ""
                line = f"  {subj}"
                if grp:
                    line += f" - {grp}"
                if days:
                    line += f" ({days})"
                if line not in seen:
                    seen.add(line)
                    lines.append(line)
            if lines:
                display = infos[0].get("_display", name_lower)
                print(f"{display.title()} (Year {year})")
                for line in lines:
                    print(line)

    total_students = len(by_student)
    print(f"\nTotal: {total_students} students")
    print(f"Jev calls: {total_calls}")
    wb.close()

    return total_students, total_calls


def main():
    ap = argparse.ArgumentParser(description="Classify timetable cells via Jev")
    ap.add_argument("files", nargs="*", help="xlsx files (default: all in incoming/)")
    ap.add_argument("--year", type=int, choices=(1, 2, 3),
                    help="Only show students from this year")
    a = ap.parse_args()

    if a.files:
        paths = [Path(p) for p in a.files]
    else:
        incoming = Path(__file__).resolve().parent.parent / "incoming"
        paths = sorted(incoming.glob("*.xlsx"))

    if not paths:
        sys.exit("No xlsx files found")

    total_students = 0
    total_calls = 0
    for path in paths:
        students, calls = process_file(path, year_filter=a.year)
        total_students += students
        total_calls += calls

    print(f"\n===== Summary =====")
    print(f"Files: {len(paths)}")
    print(f"Total students: {total_students}")
    print(f"Total Jev calls: {total_calls}")


if __name__ == "__main__":
    main()
