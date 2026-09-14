# AGENTS.md — circomedia-timetable

Micro-app for Circomedia classmates: type your first name → see your groups
+ get a personal calendar feed URL. Live at
**https://circomedia-timetable.vercel.app**.

## Architecture (data flows Mac → Vercel, never the reverse)

```
Mac (launchd, every 15 min; SharePoint login ONLY works here)
  fetch_sharepoint_timetable.py --all --headless → incoming/Term*.xlsx
  build_feeds.py                                → site/{feeds/*.ics, roster.json, manifest.json}
  publish.py                                    → Vercel Blob (public store, only-changed uploads)
Vercel (project: circomedia-timetable, SvelteKit + adapter-vercel)
  src/routes/+page.svelte   → search page (Svelte 5, no UI framework, no preamble)
  src/lib/*                 → lego pieces: api.js helpers + StudentSearch/Card/Picker
  src/routes/api/student    → JSON search (status: match|picker|none|too-many|empty)
  src/routes/api/meta       → JSON freshness {updated_at}
  src/routes/feeds/[slug]   → per-student .ics, proxied from Blob store
  Blob store                → circomedia-feeds, feeds + roster.json + manifest.json
```

The API returns JSON only — never HTML. All rendering happens in Svelte
components. Feed + roster data live in the public Blob store (written by
`publish.py` on the Mac); server routes only read it.

No redeploy is ever needed for timetable changes. `vercel deploy --prod`
runs ONLY when `src/` / `svelte.config.js` / `package.json` change.

## Repo map

| File | Role |
|---|---|
| `timetable_to_ics.py` | xlsx→ics core: group parsing, student matching, ICS emit. Pure logic, no I/O except CLI |
| `build_feeds.py` | All-student builder → `site/`. Roster + slug assignment + manifest |
| `publish.py` | Blob uploader (shells out to `vercel blob put`, `--allow-overwrite`, stable pathnames). Loads OIDC env from `.env.local`, refreshes token on auth failure |
| `sync.py` | Orchestrator: fetch → hash-check → build → publish. Enforces 07:00–22:00 London window itself (launchd fires 24/7) |
| `fetch_sharepoint_timetable.py` | Playwright scraper, persistent profile in `.sharepoint-browser-profile/` |
| `src/routes/api/student/+server.js` | JSON search over Blob `roster.json`. Statuses: match/picker/none/too-many/empty; student carries `feed: {https, webcal, google}` built from request origin |
| `src/routes/api/meta/+server.js` | JSON freshness `{updated_at}`; frontend formats via `formatUpdated()` |
| `src/routes/feeds/[slug]/+server.js` | .ics proxy: strips `.ics` suffix, checks slug against roster, ETag passthrough, 300s cache headers |
| `src/lib/api.js` | Client lego: `searchStudent`, `pickStudent`, `fetchMeta`, `formatUpdated`, `prettySubject`, `feedUrls`, `copyText` |
| `src/lib/StudentSearch.svelte` etc. | Styled components (search box / card / picker) with a Claude-website-inspired look: warm cream bg, serif headings, coral accent |
| `src/app.css` | Tailwind CSS **v4** entry (CSS-first config): `@theme` defines cream/ink/coral palette + serif/sans/mono fonts; `@layer base` sets body styles |
| `src/routes/+layout.svelte` | Imports `app.css`; everything else renders in `+page.svelte` |
| `src/routes/+page.js` | SPA shell: `prerender = true` + `ssr = false`. Page carries no data; all roster/feed fetching happens client-side |
| `src/lib/server/blob.js` | Server-only Blob reader: `BLOB_BASE_URL` env with public-URL fallback, 300s in-memory roster cache |
| `svelte.config.js` | `adapter-vercel` (zero-config Vercel deploy; Framework Preset must be SvelteKit, not Python) |
| `vite.config.js` | SvelteKit + `@tailwindcss/vite` plugins. V4 deps: `tailwindcss`, `@tailwindcss/vite` (+ `@sveltejs/vite-plugin-svelte`) |
| `package.json` | SvelteKit app, **pnpm** (`dev`, `build`, `preview`). No Python requirements |
| `pnpm-lock.yaml` | Committed — Vercel auto-detects pnpm from it; `packageManager` pins pnpm@10.9.0 (corepack-ready) |
| `incoming/` | Downloaded xlsx (gitignored). Filenames carry week numbers (`Week 1` → Mon 14-09-2026) |
| `site/` | Build output (gitignored): `feeds/*.ics`, `roster.json`, `manifest.json` |
| `.sync_state.json` | (gitignored) `last_built_hashes` + `published_hashes` — what makes sync/publish idempotent |
| `docs/feed.ics` | DEAD. Old single-student GitHub Pages feed, fully deprecated. Do not revive |

## Vercel setup (already done, don't recreate)

- Project `circomedia-timetable` (team `yotamfrids-projects`), linked via `.vercel/`
- Blob store `circomedia-feeds` (`store_AEj7L7KeOFSPNdyy`, public, region iad1)
- Base URL: `https://aej7l7keofspndyy.public.blob.vercel-storage.com`
- Env `BLOB_BASE_URL` = base URL, set on **production + preview** (stored as Secret)
- Store connected via `vercel storage connect` (OIDC credentials — no static token)
- Local blob auth: `source .env.local` (gitignored) provides `BLOB_STORE_ID` + `VERCEL_OIDC_TOKEN`; `publish.py` does this itself
- Deploy: `vercel deploy --prod --yes` from repo root

## Local dev (SvelteKit app)

```sh
corepack enable   # once: uses the packageManager pin (or `npm i -g pnpm`)
pnpm install      # once
pnpm dev          # hot-reload dev server (API + feed routes served live)
pnpm build        # what Vercel runs; `pnpm preview` serves the static output only
```

No env setup needed: server routes read `BLOB_BASE_URL` when set and fall
back to the store's public URL otherwise, so `pnpm dev` works against live
Blob data out of the box. (`vercel env pull .env.local` only if you need
private env values locally; never commit that file.)

## The matcher (read before touching `timetable_to_ics.py`)

`extract_for_student(wb, name, ...)` — a block belongs to the student if ANY holds:

1. **Named explicitly** — with three guards:
   - only the **pre-dash** segment counts (`Tan - Jonathan` = Tan's lesson; hyphenated surnames like `Mark Parfitt-Jones` are never split);
   - pure **teacher lists** (`Lisa, Ethan, Chané`, `Nicky and Janine`) never count when the block has a subject;
   - `Name (Tag)` **owner blocks** (`Charlie (Creative)` + `Jonathan`) match only the owner — the extra name is the tutor.
   - Matching tries all spelling **aliases** + paren-stripped bases (`lewis (nicky)` → `lewis`).
2. **All-years, year-aware**: `All Yr N` / `All Nth years` matches iff student's year == N (`matched_year or start_year`). Generic `All years` needs a yellow header. (History: this used to match Year-1 blocks for EVERYONE — fixed.)
3. **Group match** for the block's subject, incl. substring fallback for non-`Group X` labels (`Major`, `Billie`, teacher-named Year-3 groups like `Nicky`).

Roster (`parse_roster`): union of raw year sheets, NO Core-Skills merge for attribution. Spelling variants merge by `roster_norm` (`farrah (minor)`→`farrah`); junk dropped (weekday names, `week`/`wk`, `?` garble). `lookup_merged` unions variant groups within the matched year. Slugs via `slugify`, collision-safe (`-2` suffix).

ICS output: deterministic UIDs (`sha1(date|start|end|subject)@circomedia`), `SEQUENCE:0` always, `REFRESH-INTERVAL:PT30M`, Europe/London VTIMEZONE, **RFC 5545 line folding** (Google rejects unfolded lines; Apple doesn't care).

## Invariants (the pipeline depends on these)

- **Change detection ignores volatile lines**: `DTSTAMP`/`LAST-MODIFIED` are excluded when comparing feeds; `updated_at` excluded for roster/manifest. This is what makes no-change syncs upload 0 files. If you add a volatile field, exclude it too or every sync republishes everything.
- **Manifest feed hashes are normalized-content hashes**, not file bytes.
- **`site/` files are never deleted by git** (gitignored); `build_feeds.py` prunes stale feeds locally and `publish.py` deletes their blobs.
- **Yotam's feed is the validated reference**: `build_feed.py` (legacy single-student script, still in repo) output must equal `site/feeds/yotam.ics` event-for-event after any matcher change. Check with the DTSTART/SUMMARY/LOCATION tuple diff.
- **CONFLICT lines on stderr are the impossibility guard** (one student, two places at once). They shout; they don't fail the build.

## Known limitations (as of 2026-09-14)

- **~15 overlaps remain in 5 feeds** (charlie, jonathan, janine, nicky + 1): Year-3 teacher-named groups, Majors/Minors axis, PAR-vs-group overlaps, `Charlie (Creative)` possibly belonging to a different Charlie. Matcher rules were validated for Year 1 only — verify with those classmates before announcing.
- Mac asleep/off = no publishes (accepted; SharePoint login can't move server-side).
- Google's URL-importer caches failed fetches for hours and can take ~12h to first populate. If it errors, retry later or use Settings → Add calendar → From URL.
- Function CDN caches feeds 300s (`s-maxage`); blob objects 300s (`cache-control-max-age`). Worst-case publish→visible lag ≈ 5 min + client's own poll (≤60 min). Fine for the 10am-change case.
- No `/api/health` route (nothing needs it; Vercel reports function health itself).
- No per-IP rate limit on `/api/student` (dropped in the SvelteKit port; re-add if abused).

## Gotchas for the next agent

- **Vercel Framework Preset must be SvelteKit**: the project was created under
  the Python/FastAPI preset. After this port, set Project Settings → General →
  Framework Preset to **SvelteKit** (or redeploy once and let auto-detect kick
  in); otherwise the build will try to serve deleted `app.py`.
- **`.env.local` OIDC token expires** — `publish.py` auto-refreshes via `vercel env pull` on auth failure. Don't commit `.env.local` (gitignored via `.env*`).
- **`vercel env add` needs `--value ... --yes`** for non-interactive use; preview envs need no branch flag when passed this way.
- **`vercel blob put` needs `--allow-overwrite true`** for stable-pathname updates, plus sourced OIDC env when run outside `publish.py`.
- **`+layout.svelte` uses legacy `<slot />`**, not Svelte 5 `{@render children()}`.
  Works (compat mode, build is green) — don't "fix" it piecemeal; convert only
  if touching the layout anyway.
- **Don't touch `/opt/circomedia` dependencies**: the Hetzner box project copy is scrapped, but `bot@*.service` units there are someone else's — never touch.
- No commits unless the user asks.
