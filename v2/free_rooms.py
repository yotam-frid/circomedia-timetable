"""Availability derived from room occupancy, never from empty room-days."""

import datetime as dt
from collections import defaultdict

from .spaces import to_space_ics

DAY_START = dt.time(8, 45)
DAY_END = dt.time(18)
MIN_GAP = dt.timedelta(minutes=30)


def build_free_room_events(space_events):
    events = []
    for room, bookings in sorted(space_events.items()):
        if room.casefold() in {"meeting room", "classroom", "studio 1"}:
            continue
        by_day = defaultdict(list)
        for booking in bookings:
            by_day[booking["date"]].append(booking)
        for date, daily in sorted(by_day.items()):
            opening = dt.datetime.combine(date, DAY_START)
            closing = dt.datetime.combine(date, DAY_END)
            cursor = opening
            gaps = []
            for booking in sorted(daily, key=lambda e: (e["start"], e["end"])):
                start = max(opening, booking["start"])
                end = min(closing, booking["end"])
                if end <= start:
                    continue
                if start - cursor > MIN_GAP:
                    gaps.append((cursor, start))
                cursor = max(cursor, end)
            if closing - cursor > MIN_GAP:
                gaps.append((cursor, closing))
            for start, end in gaps:
                events.append({
                    "date": date, "start": start, "end": end,
                    "name": f"{room} (free)", "location": room,
                    "subject": "Free Rooms",
                })
    return sorted(events, key=lambda e: (e["start"], e["location"], e["end"]))


def to_free_rooms_ics(events):
    return to_space_ics(events, "Free Rooms", availability=True)
