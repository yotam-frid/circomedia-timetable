/** Same occupancy complement as v2/free_rooms.py: 08:45–18:00, gaps >30m. */
export function minutesOf(label) {
  const match = /^(\d+):(\d+)$/.exec(label ?? '');
  return match ? +match[1] * 60 + +match[2] : null;
}

const timeLabel = (minutes) => `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, '0')}`;

export function roomGaps(events) {
  if (!events.length) return [];
  const opening = 8 * 60 + 45;
  const closing = 18 * 60;
  let cursor = opening;
  const gaps = [];
  const addGap = (start, end) => {
    if (end - start > 30) gaps.push({
      start: timeLabel(start), end: timeLabel(end),
      title: 'Free time', location: '', free: true
    });
  };
  for (const event of [...events].sort((a, b) => minutesOf(a.start) - minutesOf(b.start))) {
    const rawStart = minutesOf(event.start);
    const rawEnd = minutesOf(event.end);
    if (rawStart == null || rawEnd == null) continue;
    const start = Math.max(opening, rawStart);
    const end = Math.min(closing, rawEnd);
    if (end <= start) continue;
    addGap(cursor, start);
    cursor = Math.max(cursor, end);
  }
  addGap(cursor, closing);
  return gaps;
}
