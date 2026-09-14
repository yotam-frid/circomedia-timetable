import { json, text } from '@sveltejs/kit';
import { getManifest } from '$lib/server/blob.js';

/** GET /api/meta — freshness stamp. Frontend formats it via formatUpdated(). */
export async function GET() {
  try {
    const manifest = await getManifest();
    return json({ updated_at: manifest.updated_at ?? null });
  } catch {
    return text('recently', { status: 503 });
  }
}
