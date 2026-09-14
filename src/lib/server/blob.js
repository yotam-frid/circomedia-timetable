import { env } from '$env/dynamic/private';

// Public bucket — the base URL is not secret (objects are world-readable).
// Env wins when set (Vercel production/preview); fallback keeps `npm run dev`
// working with zero setup.
export const BLOB_BASE =
  env.BLOB_BASE_URL?.replace(/\/$/, '') ||
  'https://aej7l7keofspndyy.public.blob.vercel-storage.com';

export const ROSTER_TTL = 300; // seconds; roster changes rarely

const cache = new Map(); // key -> { expires, value }

/** GET a path from the Blob store. Returns { status, etag, body }. */
export async function blobGet(path, etag = null) {
  const headers = {};
  if (etag) headers['if-none-match'] = etag;
  const res = await fetch(BLOB_BASE + path, { headers });
  return {
    status: res.status,
    etag: res.headers.get('etag'),
    body: res.status === 200 ? new Uint8Array(await res.arrayBuffer()) : null
  };
}

async function cached(key, ttlSeconds, loader) {
  const hit = cache.get(key);
  if (hit && hit.expires > Date.now()) return hit.value;
  const value = await loader();
  cache.set(key, { expires: Date.now() + ttlSeconds * 1000, value });
  return value;
}

export function getRoster() {
  return cached('roster', ROSTER_TTL, async () => {
    const res = await fetch(`${BLOB_BASE}/roster.json`);
    if (!res.ok) throw new Error(`roster.json -> HTTP ${res.status}`);
    return res.json();
  });
}

export function getManifest() {
  return cached('manifest', ROSTER_TTL, async () => {
    const res = await fetch(`${BLOB_BASE}/manifest.json`);
    if (!res.ok) throw new Error(`manifest.json -> HTTP ${res.status}`);
    return res.json();
  });
}

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Calendar URLs for a feed slug, anchored at the request origin so
 *  preview deployments produce correct links automatically. */
export function feedUrls(origin, slug) {
  const https = `${origin}/feeds/${slug}.ics`;
  const webcal = https.replace(/^https:\/\//, 'webcal://').replace(/^http:\/\//, 'webcal://');
  const google = 'https://calendar.google.com/calendar/r?cid=' + encodeURIComponent(https);
  return { https, webcal, google };
}
