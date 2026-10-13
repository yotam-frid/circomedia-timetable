import { blobGet } from '$lib/server/blob.js';
import { dev } from '$app/environment';

const headers = {
  'content-type': 'text/calendar; charset=utf-8',
  'content-disposition': 'inline; filename="free-rooms.ics"',
  'cache-control': dev ? 'no-store' : 'public, max-age=300',
  ...(!dev ? {
    'cdn-cache-control': 'public, s-maxage=300, stale-while-revalidate=60',
    'vercel-cdn-cache-control': 'public, s-maxage=300, stale-while-revalidate=60'
  } : {})
};

async function serve(request, headOnly = false) {
  const { status, etag, body } = await blobGet(
    '/feeds/free-rooms.ics', request.headers.get('if-none-match')
  ).catch(() => ({ status: 502 }));
  const responseHeaders = { ...headers, ...(etag ? { etag } : {}) };
  if (status === 304) return new Response(null, { status, headers: responseHeaders });
  if (status === 502) return new Response('timetable updating, try again soon', { status: 502 });
  if (status !== 200 || !body) return new Response('unknown feed', { status: 404 });
  return new Response(headOnly ? null : body, { headers: responseHeaders });
}

export function GET({ request }) {
  return serve(request);
}

export function HEAD({ request }) {
  return serve(request, true);
}
