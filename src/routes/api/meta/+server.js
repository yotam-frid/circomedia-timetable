import { json, text } from '@sveltejs/kit';
import { getManifest, getRoster } from '$lib/server/blob.js';

/** GET /api/meta — freshness stamp. The newer of the roster and manifest
 *  stamps: card groups (roster.json) can change without any feed changing,
 *  and the footer must not claim older data than what's on screen. */
export async function GET() {
  try {
    const settled = await Promise.allSettled([getManifest(), getRoster()]);
    const sources = settled.flatMap((s) =>
      s.status === 'fulfilled' ? [s.value] : []
    );
    if (!sources.length) throw new Error('no data source readable');
    let updated_at = null;
    let best = -Infinity;
    for (const src of sources) {
      const stamp = src.updated_at;
      if (!stamp) continue;
      const t = Date.parse(stamp);
      if (!Number.isNaN(t) && t > best) {
        best = t;
        updated_at = stamp;
      }
    }
    return json({ updated_at });
  } catch {
    return text('recently', { status: 503 });
  }
}
