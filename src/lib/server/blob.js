import { env } from '$env/dynamic/private';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

// Public bucket — the base URL is not secret (objects are world-readable).
// Env wins when set (Vercel production/preview); fallback keeps `npm run dev`
// working with zero setup.
export const BLOB_BASE =
  env.BLOB_BASE_URL?.replace(/\/$/, '') ||
  'https://aej7l7keofspndyy.public.blob.vercel-storage.com';

// Local-dev escape hatch: point at a build_feeds.py output dir and the server
// reads roster/manifest/feeds from disk instead of the published Blob store,
// with the caches below disabled. Production is untouched (env unset there).
//   python3 build_feeds.py                  # refresh site/ from incoming/
//   LOCAL_SITE_DIR=$PWD/site pnpm dev
const SITE_DIR = env.LOCAL_SITE_DIR?.replace(/\/$/, '') || null;

export const ROSTER_TTL = SITE_DIR ? 0 : 300; // seconds; roster changes rarely

const cache = new Map(); // key -> { expires, value }

/** GET a path from the Blob store (or the local site/ dir).
 *  Returns { status, etag, body }. */
export async function blobGet(path, etag = null) {
  if (SITE_DIR) {
    try {
      return {
        status: 200,
        etag: null,
        body: new Uint8Array(await readFile(join(SITE_DIR, path)))
      };
    } catch {
      return { status: 404, etag: null, body: null };
    }
  }
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

async function getJson(path) {
  const { status, body } = await blobGet(path);
  if (status !== 200 || !body) throw new Error(`${path} unreadable`);
  return JSON.parse(new TextDecoder().decode(body));
}

export function getRoster() {
  return cached('roster', ROSTER_TTL, () => getJson('/roster.json'));
}

export function getManifest() {
  return cached('manifest', ROSTER_TTL, () => getJson('/manifest.json'));
}

export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Calendar URLs for a feed slug, anchored at the request origin so
 *  preview deployments produce correct links automatically. */
export function feedUrls(origin, slug) {
  const https = `${origin}/feeds/${slug}.ics`;
  const webcal = https.replace(/^https:\/\//, 'webcal://').replace(/^http:\/\//, 'webcal://');
  // Google's cid= deep-link only accepts webcal:// (since ~2025 it rejects
  // https:// feeds with "Unable to Add Calendar. Check the URL."; https still
  // works via the manual Settings -> Add calendar -> From URL flow).
  const google = 'https://calendar.google.com/calendar/r?cid=' + encodeURIComponent(webcal);
  return { https, webcal, google };
}
