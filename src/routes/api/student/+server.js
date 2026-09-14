import { json } from '@sveltejs/kit';
import { getRoster, feedUrls } from '$lib/server/blob.js';

/** GET /api/student?name=<q>&exact=1 — pure JSON, no HTML.
 *
 *  200 { status: "empty", matches: [] }                      // blank query
 *  200 { status: "match", student }                          // exact or single hit
 *  200 { status: "picker", matches: [{name,year,slug}] }     // 2–12 hits
 *  200 { status: "none", query }                             // no hits
 *  200 { status: "too-many", query, count }                  // >12 hits
 *  502 { error }                                             // blob unreadable
 *
 *  student = { name, slug, year, groups, feed: { https, webcal, google } }
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

  const exact = url.searchParams.get('exact');
  if (exact) {
    const hit = students.find((s) => s.name.toLowerCase() === q.toLowerCase());
    if (hit) return json({ status: 'match', student: withFeed(url, hit) });
  }

  const matches = students.filter((s) => s.name.toLowerCase().includes(q.toLowerCase()));
  if (matches.length === 1) return json({ status: 'match', student: withFeed(url, matches[0]) });
  if (matches.length === 0) return json({ status: 'none', query: q });
  if (matches.length > 12) return json({ status: 'too-many', query: q, count: matches.length });
  return json({
    status: 'picker',
    matches: matches.slice(0, 12).map(({ name, year, slug }) => ({ name, year, slug }))
  });
}

function withFeed(url, s) {
  return { ...s, feed: feedUrls(url.origin, s.slug) };
}
