import { getRoster, blobGet, SLUG_RE } from '$lib/server/blob.js';

const CACHE = 'public, max-age=300';
const CDN_CACHE = 'public, s-maxage=300, stale-while-revalidate=60';

/** GET|HEAD /feeds/{slug}.ics — per-student calendar feed, proxied from Blob. */
export async function GET({ params, request }) {
  return serve(params, request);
}

export async function HEAD({ params, request }) {
  return serve(params, request, true);
}

async function serve(params, request, headOnly = false) {
  // Route captures "yotam.ics" whole; the slug is the part before the suffix.
  const raw = params.slug;
  const slug = raw.endsWith('.ics') ? raw.slice(0, -4) : '';
  if (!SLUG_RE.test(slug)) return new Response('unknown feed', { status: 404 });

  let slugs;
  try {
    slugs = new Set((await getRoster()).students.map((s) => s.slug));
  } catch {
    return new Response('timetable updating, try again soon', { status: 502 });
  }
  if (!slugs.has(slug)) return new Response('unknown feed', { status: 404 });
  if (headOnly) {
    return new Response(null, {
      headers: { 'content-type': 'text/calendar; charset=utf-8', 'cache-control': CACHE }
    });
  }

  const { status, etag, body } = await blobGet(
    `/feeds/${slug}.ics`,
    request.headers.get('if-none-match')
  ).catch(() => ({ status: 502 }));
  if (status === 502) return new Response('timetable updating, try again soon', { status: 502 });
  if (status === 304) return new Response(null, { status: 304, headers: etag ? { etag } : {} });
  if (status !== 200 || !body) return new Response('unknown feed', { status: 404 });

  const headers = {
    'content-type': 'text/calendar; charset=utf-8',
    'content-disposition': `inline; filename="${slug}.ics"`,
    'cache-control': CACHE,
    'cdn-cache-control': CDN_CACHE,
    'vercel-cdn-cache-control': CDN_CACHE
  };
  if (etag) headers.etag = etag;
  return new Response(body, { headers });
}
