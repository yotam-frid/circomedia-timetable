import { json } from '@sveltejs/kit';
import { getRoster, getSpaces, feedUrls, spaceFeedUrls } from '$lib/server/blob.js';

/** GET /api/student?name=<q>&exact=1&kind=space — pure JSON, no HTML.
 *
 *  Searches students AND bookable spaces (Batcave, Gym, South Wing, …)
 *  in one box. Space matching ignores case and spacing, so "southwing"
 *  finds "South Wing".
 *
 *  200 { status: "empty", matches: [] }                      // blank query
 *  200 { status: "match", student }                          // exact or single hit
 *  200 { status: "picker", matches: [{name,year?,slug,kind}] }// 2–12 hits
 *  200 { status: "none", query }                             // no hits
 *  200 { status: "too-many", query, count }                  // >12 hits
 *  502 { error }                                             // box unreadable
 *
 *  student = { name, slug, kind: "student", year, groups,
 *              feed: { https, webcal, google } }
 *  space   = { name, slug, kind: "space", feed: { https, webcal, google } }
 *            (space feeds live under /feeds/spaces/)
 */
export async function GET({ url }) {
  const q = (url.searchParams.get('name') ?? '').trim().slice(0, 60);
  if (!q) return json({ status: 'empty', matches: [] });

  let students;
  try {
    students = (await getRoster()).students;
  } catch {
    return json({ error: 'Timetable is updating — try again in a minute.' }, { status: 502 });
  }
  let spaces = [];
  try {
    spaces = (await getSpaces()).spaces ?? [];
  } catch {
    spaces = [];
  }

  const exact = url.searchParams.get('exact');
  const kind = url.searchParams.get('kind');
  if (exact) {
    if (kind === 'space') {
      const hit = spaces.find((s) => norm(s.name) === norm(q));
      if (hit) return json({ status: 'match', student: withSpaceFeed(url, hit) });
    } else if (kind === 'student') {
      const hit = students.find((s) => s.name.toLowerCase() === q.toLowerCase());
      if (hit) return json({ status: 'match', student: withStudentFeed(url, hit) });
    } else {
      const hit = students.find((s) => s.name.toLowerCase() === q.toLowerCase());
      if (hit) return json({ status: 'match', student: withStudentFeed(url, hit) });
      const shit = spaces.find((s) => norm(s.name) === norm(q));
      if (shit) return json({ status: 'match', student: withSpaceFeed(url, shit) });
    }
  }

  const ql = q.toLowerCase();
  const studentMatches = students.filter((s) => s.name.toLowerCase().includes(ql));
  const nq = norm(q);
  const spaceMatches = spaces.filter(
    (s) => s.name.toLowerCase().includes(ql) || (nq && norm(s.name).includes(nq))
  );
  const total = studentMatches.length + spaceMatches.length;
  if (total === 1) {
    const only = studentMatches[0] ?? spaceMatches[0];
    const student = studentMatches[0] ? withStudentFeed(url, only) : withSpaceFeed(url, only);
    return json({ status: 'match', student });
  }
  if (total === 0) return json({ status: 'none', query: q });
  if (total > 12) return json({ status: 'too-many', query: q, count: total });
  return json({
    status: 'picker',
    matches: [
      ...studentMatches.map(({ name, year, slug }) => ({ name, year, slug, kind: 'student' })),
      ...spaceMatches.map(({ name, slug }) => ({ name, slug, kind: 'space' }))
    ].slice(0, 12)
  });
}

/** Lowercase alphanumeric only: "South Wing" -> "southwing". */
function norm(s) {
  return (s ?? '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function withStudentFeed(url, s) {
  return { ...s, kind: 'student', feed: feedUrls(url.origin, s.slug) };
}

function withSpaceFeed(url, s) {
  return { name: s.name, slug: s.slug, kind: 'space', feed: spaceFeedUrls(url.origin, s.slug) };
}
