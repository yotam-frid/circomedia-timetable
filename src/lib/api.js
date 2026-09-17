/** Lego pieces: tiny fetch helpers + formatters. Import what you need. */

/** Search the roster. Returns the /api/student JSON payload verbatim:
 *  { status: "match"|"picker"|"none"|"too-many"|"empty", ... } */
export async function searchStudent(name, { exact = false, signal } = {}) {
  const params = new URLSearchParams({ name });
  if (exact) params.set('exact', '1');
  const res = await fetch(`/api/student?${params}`, { signal });
  if (!res.ok) throw new Error('search failed');
  return res.json();
}

/** Pick one entry from a picker list (re-queries with exact=1). */
export async function pickStudent(name) {
  return searchStudent(name, { exact: true });
}

/** Freshness stamp: { updated_at: ISO|null }. */
export async function fetchMeta() {
  const res = await fetch('/api/meta');
  if (!res.ok) return { updated_at: null };
  return res.json();
}

/** "2026-09-14T19:23:08.199221+01:00" -> "Mon 14 Sep, 19:23". */
export function formatUpdated(iso) {
  if (!iso) return 'recently';
  const d = new Date(iso);
  if (Number.isNaN(d)) return iso;
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const pad = (n) => String(n).padStart(2, '0');
  return `${days[d.getDay()]} ${d.getDate()} ${months[d.getMonth()]}, ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** "aerial_conditioning" -> "Aerial Conditioning", "context1" -> "Context 1". */
export function prettySubject(key) {
  const pretty = {
    context1: 'Context 1',
    context2: 'Context 2',
    context3: 'Context 3',
    teacher_training: 'Teacher Training',
    stand_up: 'Stand Up',
    clown: 'Clown',
    par_group_1: 'PAR Group 1',
    par_group_2: 'PAR Group 2',
    par: 'PAR'
  };
  if (key in pretty) return pretty[key];
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Calendar URLs for a feed slug on the current origin. */
export function feedUrls(slug, origin = window.location.origin) {
  const https = `${origin}/feeds/${slug}.ics`;
  const webcal = https.replace(/^https?:\/\//, 'webcal://');
  return {
    https,
    webcal,
    // Google's cid= deep-link only accepts webcal:// (see src/lib/server/blob.js).
    google: 'https://calendar.google.com/calendar/r?cid=' + encodeURIComponent(webcal)
  };
}

/** Copy text, with a legacy fallback. Resolves true on success. */
export async function copyText(t) {
  try {
    await navigator.clipboard.writeText(t);
    return true;
  } catch {
    const ta = document.createElement('textarea');
    ta.value = t;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      /* noop */
    }
    ta.remove();
    return ok;
  }
}

/** localStorage key for the last student whose feed loaded successfully. */
const LAST_STUDENT_KEY = 'circomedia:last-student';

/** Remember a student name after their feed loads OK.
 *  Best-effort: no-ops outside the browser or when storage is unavailable. */
export function saveLastStudent(name) {
  try {
    if (typeof localStorage === 'undefined' || !name) return;
    localStorage.setItem(LAST_STUDENT_KEY, name);
  } catch {
    /* private mode etc. — persistence is best-effort */
  }
}

/** The remembered student name, or null when there isn't one. */
export function loadLastStudent() {
  try {
    if (typeof localStorage === 'undefined') return null;
    return localStorage.getItem(LAST_STUDENT_KEY);
  } catch {
    return null;
  }
}

/** Forget the remembered student (e.g. the search box was cleared). */
export function clearLastStudent() {
  try {
    if (typeof localStorage === 'undefined') return;
    localStorage.removeItem(LAST_STUDENT_KEY);
  } catch {
    /* noop */
  }
}

/** Unescape an ICS property value (\, \; \\ and line-break escapes). */
function unescapeICS(v) {
  return v
    .replace(/\\([\\,;])/g, '$1')
    .replace(/\\[nN]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** "20260917T084500" -> { dateKey: "2026-09-17", minutes: 525, label: "8:45" }. */
function parseICSDateTime(v) {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(v.trim());
  if (!m) return null;
  const [, y, mo, d, h, mi] = m;
  return {
    dateKey: `${y}-${mo}-${d}`,
    minutes: parseInt(h, 10) * 60 + parseInt(mi, 10),
    label: `${parseInt(h, 10)}:${mi}`
  };
}

/** Weekday shortening for group names: "Wednesday" -> "Wed",
 *  "Monday + Tuesday + Wednesday" -> "Mon + Tue + Wed". Already-short
 *  names pass through untouched. */
const WEEKDAY_SHORT = {
  monday: 'Mon',
  tuesday: 'Tue',
  wednesday: 'Wed',
  thursday: 'Thu',
  friday: 'Fri',
  saturday: 'Sat',
  sunday: 'Sun'
};

function shortenWeekdays(s) {
  return (s ?? '').replace(
    /\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b/gi,
    (m) => WEEKDAY_SHORT[m.toLowerCase().replace(/s$/, '')] ?? m
  );
}

function groupLabel(description, summary) {
  const m = /Matched:[^\n]*?\b(Group\s+\S+)/.exec(description ?? '');
  if (m) return shortenWeekdays(m[1]);
  // Day-identified groups ("Clown (Wednesday)", "Clown (Wed)") carry the
  // weekday in the SUMMARY suffix instead of the Matched reason.
  const t = /\(([^)]+)\)\s*$/.exec(summary ?? '');
  if (
    t &&
    /\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)s?\b/i.test(
      t[1]
    )
  ) {
    return shortenWeekdays(t[1].trim());
  }
  return '';
}

/** Parse an .ics feed into per-day event groups.
 *  Returns [{ key: "2026-09-17", events: [{ start, end, title, location, group }] }]
 *  sorted chronologically. Times stay wall-clock strings from the feed
 *  (Europe/London), so no timezone shifting is involved. */
export function parseICS(text) {
  const unfolded = text.replace(/\r\n/g, '\n').replace(/\n[ \t]/g, '');
  const byDay = new Map();
  let cur = null;
  for (const line of unfolded.split('\n')) {
    if (line === 'BEGIN:VEVENT') {
      cur = {};
    } else if (line === 'END:VEVENT') {
      if (cur) {
        const s = cur.dtstart ? parseICSDateTime(cur.dtstart) : null;
        const e = cur.dtend ? parseICSDateTime(cur.dtend) : null;
        if (s && e) {
          const titleRaw = cur.summary || 'Class';
          const group = groupLabel(cur.description, titleRaw);
          // SUMMARY carries the group suffix ("Core Skills - Handstands
          // (Group 1)", "Clown (Wed)") -- strip it so the card can render
          // it non-bold. The suffix may still use the full weekday name
          // in feeds built before the shortening, so compare shortened.
          const title = group
            ? titleRaw
                .replace(/\s*\(([^)]+)\)\s*$/, (full, inner) =>
                  shortenWeekdays(inner.trim()) === group ? '' : full
                )
                .trim()
            : titleRaw;
          const list = byDay.get(s.dateKey) ?? [];
          list.push({
            start: s.label,
            end: e.label,
            sort: s.minutes,
            title,
            location: cur.location || '',
            group
          });
          byDay.set(s.dateKey, list);
        }
      }
      cur = null;
    } else if (cur) {
      const i = line.indexOf(':');
      if (i === -1) continue;
      const name = line.slice(0, i).split(';')[0];
      const value = line.slice(i + 1);
      if (name === 'DTSTART') cur.dtstart = value;
      else if (name === 'DTEND') cur.dtend = value;
      else if (name === 'SUMMARY') cur.summary = unescapeICS(value);
      else if (name === 'LOCATION') cur.location = unescapeICS(value);
      else if (name === 'DESCRIPTION') cur.description = unescapeICS(value);
    }
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([key, events]) => ({
      key,
      events: events.sort((a, b) => a.sort - b.sort)
    }));
}

/** "2026-09-17" -> "Thu, 17 Sep". */
export function formatDayLabel(dateKey) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateKey);
  if (!m) return dateKey;
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${days[d.getDay()]}, ${d.getDate()} ${months[d.getMonth()]}`;
}

/** Local today as "YYYY-MM-DD" for picking the initial calendar day. */
export function todayKey() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Index of the day to show first: today when present, else the next
 *  upcoming day, else the start of the range. */
export function initialDayIndex(days, today = todayKey()) {
  const i = days.findIndex((d) => d.key >= today);
  return i === -1 ? 0 : i;
}
