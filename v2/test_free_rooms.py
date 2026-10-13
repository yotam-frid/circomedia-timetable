import datetime as dt
import json
import subprocess
import unittest
from pathlib import Path

from .free_rooms import build_free_room_events, to_free_rooms_ics


class FreeRoomsTests(unittest.TestCase):
    date = dt.date(2026, 10, 13)

    def bookings(self, times):
        return [{"date": self.date,
                 "start": dt.datetime.combine(self.date, dt.time.fromisoformat(start)),
                 "end": dt.datetime.combine(self.date, dt.time.fromisoformat(end))}
                for start, end in times]

    def test_intervals_and_ui_parity(self):
        cases = [
            ([("10:00", "12:00"), ("14:00", "16:00")],
             [("08:45", "10:00"), ("12:00", "14:00"), ("16:00", "18:00")]),
            # Nested and touching bookings must never create false free time.
            ([("09:00", "12:00"), ("10:00", "10:30"), ("11:00", "11:30"), ("12:00", "13:00")],
             [("13:00", "18:00")]),
            # The old twenty-minute gaps no longer qualify.
            ([("09:05", "10:00"), ("10:19", "17:40")],
             []),
            # Exactly thirty minutes is excluded at opening and closing.
            ([("09:15", "10:00"), ("10:31", "17:30")],
             [("10:00", "10:31")]),
            # Thirty-one minutes qualifies; an internal thirty-minute gap does not.
            ([("09:16", "10:00"), ("10:30", "17:29")],
             [("08:45", "09:16"), ("17:29", "18:00")]),
            # Twenty-nine-minute gaps are excluded everywhere.
            ([("09:14", "10:00"), ("10:29", "17:31")], []),
            ([("08:00", "09:00"), ("17:00", "19:00")], [("09:00", "17:00")]),
            ([("08:00", "19:00")], []),
            ([], []),
        ]
        root = Path(__file__).resolve().parents[1]
        for bookings, expected in cases:
            with self.subTest(bookings=bookings):
                events = build_free_room_events({"South Wing": self.bookings(bookings)})
                actual = [(e["start"].strftime("%H:%M"), e["end"].strftime("%H:%M")) for e in events]
                self.assertEqual(actual, expected)
                # The individual room UI must produce the same intervals.
                script = """
                  import { roomGaps } from './src/lib/availability.js';
                  const events = JSON.parse(process.argv[1]);
                  console.log(JSON.stringify(roomGaps(events).map(e => [e.start.padStart(5, '0'), e.end.padStart(5, '0')])));
                """
                output = subprocess.check_output(
                    ["node", "--input-type=module", "-e", script,
                     json.dumps([{"start": s, "end": e} for s, e in bookings])], cwd=root, text=True)
                self.assertEqual(json.loads(output), [list(pair) for pair in expected])

    def test_feed_titles_order_transparency_and_identity(self):
        bookings = self.bookings([("10:00", "16:00")])
        events = build_free_room_events({
            "South Wing": bookings, "Batcave": bookings,
            "Meeting Room": bookings, "Classroom": bookings,
        })
        self.assertEqual([e["location"] for e in events], ["Batcave", "South Wing", "Batcave", "South Wing"])
        ics = to_free_rooms_ics(events)
        self.assertIn("X-WR-CALNAME:Circomedia - Free Rooms\r\n", ics)
        self.assertIn("SUMMARY:South Wing (free)\r\n", ics)
        self.assertEqual(ics.count("TRANSP:TRANSPARENT"), 4)
        uids = [line for line in ics.splitlines() if line.startswith("UID:")]
        self.assertEqual(len(set(uids)), 4)
        self.assertEqual(uids, [line for line in to_free_rooms_ics(events).splitlines() if line.startswith("UID:")])
        escaped = to_free_rooms_ics(build_free_room_events({"Room, A;B": bookings}))
        self.assertIn("SUMMARY:Room\\, A\\;B (free)", escaped)


if __name__ == "__main__":
    unittest.main()
