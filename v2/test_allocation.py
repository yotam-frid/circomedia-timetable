"""Allocation-rule tests: python3 -m unittest v2.test_allocation

Covers the predicates build_events uses to decide who a day-sheet block
belongs to, with an emphasis on the day-identified subjects (Clown, Stand
Up) whose roster group is a weekday rather than a group name.
"""

import unittest

from v2.event_creator import _day_session_matches, _target_matches_student
from v2.group_parse import _extract_days


class TestTargetMatching(unittest.TestCase):
    """_target_matches_student(target, subject, student's groups, year,
    group_allowed=...)"""

    def test_weekday_label_matches_its_own_members_without_a_block_marker(self):
        # Year 2 "Clown" column is headed by the day it meets, so the
        # classifier's target is 'Mon (Clown, 2)'. No day-sheet block can
        # spell out 'Mon', so the block-text marker gate must not apply.
        self.assertTrue(_target_matches_student(
            "Mon (Clown, 2)", "Clown", {"mon"}, 2, group_allowed=False))

    def test_weekday_label_does_not_grant_non_members(self):
        self.assertFalse(_target_matches_student(
            "Mon (Clown, 2)", "Clown", {"fri"}, 2, group_allowed=False))
        self.assertFalse(_target_matches_student(
            "Mon (Clown, 2)", "Clown", set(), 2, group_allowed=False))

    def test_group_name_still_needs_the_block_marker(self):
        # An unmarked 'Acro Majors | Lisa & Ethan' block whose Jev target
        # drifted to a group must not mint events for that group.
        self.assertFalse(_target_matches_student(
            "Major (Acro, 2)", "Acro", {"major"}, 2, group_allowed=False))
        self.assertTrue(_target_matches_student(
            "Major (Acro, 2)", "Acro", {"major"}, 2, group_allowed=True))

    def test_whole_subject_and_cohort_targets(self):
        self.assertTrue(_target_matches_student("All Years", "Dance", set(), 3))
        self.assertTrue(_target_matches_student("All Year 2", "Dance", set(), 2))
        self.assertFalse(_target_matches_student("All Year 2", "Dance", set(), 1))
        # 'All' still needs the student to actually hold the subject.
        self.assertTrue(_target_matches_student("All (Aerial, 2)", "Aerial", {"all"}, 2))
        self.assertFalse(_target_matches_student("All (Aerial, 2)", "Aerial", set(), 2))


class TestDaySession(unittest.TestCase):
    """_day_session_matches(weekday, subject, cy, sy, days_by_subject)."""

    def test_matches_on_a_day_the_group_meets(self):
        self.assertTrue(_day_session_matches(
            0, "clown", 2, 2, {"clown": {"Mon"}}))
        self.assertFalse(_day_session_matches(
            4, "clown", 2, 2, {"clown": {"Mon"}}))

    def test_unrecorded_day_meets_on_the_timabled_day(self):
        # The group sheet's silence is not a statement that the student
        # never attends — same convention as a block with no week marker.
        self.assertTrue(_day_session_matches(0, "clown", 2, 2, {"clown": set()}))
        self.assertTrue(_day_session_matches(0, "clown", 2, 2, {"clown": {"Mon"}}))

    def test_not_enrolled_never_matches(self):
        # No entry for the subject is the one thing that must not fall back
        # to "meets whenever timetabled".
        self.assertFalse(_day_session_matches(0, "clown", 2, 2, {}))
        self.assertFalse(_day_session_matches(
            0, "clown", 2, 2, {"acro": {"Mon"}}))

    def test_another_subjects_days_do_not_leak(self):
        # The map is keyed by subject, so a day recorded for a different
        # subject can neither satisfy nor un-enrol this one.
        days = {"clown": {"Mon"}, "acro": {"Tue"}}
        self.assertTrue(_day_session_matches(0, "clown", 2, 2, days))
        self.assertFalse(_day_session_matches(2, "clown", 2, 2, days))


class TestExtractDays(unittest.TestCase):
    """_extract_days: the group sheets' day-header vocabulary."""

    def test_full_names(self):
        self.assertEqual(_extract_days("Monday "), ["Mon"])
        self.assertEqual(_extract_days("Wednesday "), ["Wed"])
        self.assertEqual(_extract_days("Friday"), ["Fri"])

    def test_abbreviated_and_plural(self):
        # The Year 2 Stand Up column is headed 'Friday' while every other
        # day cell on that sheet is 'Monday ' with a trailing space;
        # publishers abbreviate freely, so both must parse.
        self.assertEqual(_extract_days("Mon"), ["Mon"])
        self.assertEqual(_extract_days("Fri"), ["Fri"])
        self.assertEqual(_extract_days("Fridays"), ["Fri"])
        self.assertEqual(_extract_days("Mondays"), ["Mon"])

    def test_multiple_days_in_first_seen_order(self):
        self.assertEqual(_extract_days("Tuesday and Thursday"), ["Tue", "Thu"])
        self.assertEqual(_extract_days("Mon + Thu"), ["Mon", "Thu"])

    def test_duplicates_collapse(self):
        self.assertEqual(_extract_days("Mon Monday"), ["Mon"])

    def test_non_day_text(self):
        for text in ("", "Core Skills", "Group 2", "Studio", "students", None):
            self.assertEqual(_extract_days(text), [], text)


if __name__ == "__main__":
    unittest.main()
