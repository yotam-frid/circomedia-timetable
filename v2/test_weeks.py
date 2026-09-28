"""Filename parsing tests: python3 -m unittest v2.test_weeks"""

import unittest

from v2.weeks import is_timetable_name, weeks_from_filename


class TestWeeksFromFilename(unittest.TestCase):
    def test_single_week_singular(self):
        self.assertEqual(weeks_from_filename("Term 1a Week 1 2026.xlsx"), [1])

    def test_plural_word_single_number(self):
        # Publishers write "Weeks 2" for a single week.
        self.assertEqual(weeks_from_filename("Term 1a Weeks 2 2026.xlsx"), [2])

    def test_range_with_trailing_junk(self):
        self.assertEqual(
            weeks_from_filename("Term 1a Weeks 3-5 2026 -.xlsx"), [3, 4, 5])

    def test_range_without_trailing_junk(self):
        self.assertEqual(
            weeks_from_filename("Term 1a Weeks 1-3 2026.xlsx"), [1, 2, 3])

    def test_term_number_is_not_a_week(self):
        # "2a" must not be read as week 2.
        self.assertEqual(weeks_from_filename("Term 2a Week 7 2026.xlsx"), [7])
        self.assertEqual(
            weeks_from_filename("Term 2b Weeks 8-10 2027.xlsx"), [8, 9, 10])

    def test_en_dash_and_spaces(self):
        self.assertEqual(
            weeks_from_filename("Term 1a Week 12 – 14 2026.xlsx"), [12, 13, 14])

    def test_missing_year(self):
        self.assertEqual(weeks_from_filename("Term 1a Week 4.xlsx"), [4])

    def test_trailing_word_suffix_still_reads(self):
        self.assertEqual(
            weeks_from_filename("Term 1a Week 1 2026 Old.xlsx"), [1])

    def test_dangling_dash_does_not_extend(self):
        # The trailing " -" has no number after it.
        self.assertEqual(
            weeks_from_filename("Term 1a Week 1 2026 -.xlsx"), [1])

    def test_year_after_dash_is_not_a_range(self):
        # Guards the old 1..36 default: a 2026-week "range" is nonsense.
        self.assertEqual(
            weeks_from_filename("Term 1a Week 1 - 2026.xlsx"), [1])

    def test_reversed_range_is_ignored(self):
        self.assertEqual(
            weeks_from_filename("Term 1a Weeks 5-3 2026.xlsx"), [5])

    def test_no_standalone_number_returns_none(self):
        self.assertIsNone(weeks_from_filename("Timetable.xlsx"))
        self.assertIsNone(weeks_from_filename("Term timetable.xlsx"))

    def test_letter_attached_number_is_not_standalone(self):
        self.assertIsNone(weeks_from_filename("Week1.xlsx"))


class TestIsTimetableName(unittest.TestCase):
    def test_accepts_known_shapes(self):
        for name in ("Term 1a Week 1 2026.xlsx",
                     "Term 1a Weeks 3-5 2026 -.xlsx",
                     "Term 1a Weeks 1-3 2026.xlsx",
                     "term 2b weeks 8-10 2027.XLSX"):
            self.assertTrue(is_timetable_name(name), name)

    def test_requires_term_and_week_tokens(self):
        self.assertFalse(is_timetable_name("Floor 2 plan.xlsx"))
        self.assertFalse(is_timetable_name("Term 1a staff notes.xlsx"))
        self.assertFalse(is_timetable_name("Week 1 roster.xlsx"))
        self.assertFalse(is_timetable_name("Termnotes.xlsx"))
        self.assertFalse(is_timetable_name("Weeknotes.xlsx"))


if __name__ == "__main__":
    unittest.main()
