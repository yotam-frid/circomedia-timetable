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
  if (key === 'context1') return 'Context 1';
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
