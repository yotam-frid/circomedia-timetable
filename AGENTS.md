# AGENTS.md — circomedia-timetable

Micro-app for Circomedia classmates: type your first name → see your groups + get a personal calendar feed URL. Live at **https://circomedia-timetable.vercel.app**.

## Architecture (data flows Mac → Vercel, never the reverse)

```
Mac (launchd, every 15 min; SharePoint login ONLY works here)
  fetch_sharepoint_timetable.py --all --headless → incoming/Term*.xlsx
  build_feeds_v2.py                              → site/{feeds/*.ics, feeds/spaces/*.ics, roster.json, spaces.json, manifest.json}
  publish.py                                     → Vercel Blob (public store, only-changed uploads)
Vercel (project: circomedia-timetable, SvelteKit + adapter-vercel)
  src/routes/+page.svelte   → search page (Svelte 5, no UI framework, no preamble)
  src/lib/*                 → lego pieces: api.js helpers + StudentSearch/Card/Picker
  src/routes/api/student    → JSON search over Blob `roster.json` + `spaces.json` (status: match|picker|none|too-many|empty; entries carry kind: student|space)
  src/routes/api/meta       → JSON freshness {updated_at}
  src/routes/feeds/[slug]   → per-student .ics, proxied from Blob store
  src/routes/feeds/spaces/[slug] → per-space .ics, proxied from Blob `feeds/spaces/`
  Blob store                → circomedia-feeds, feeds + spaces feeds + roster/spaces/manifest JSON
```

The API returns JSON only — never HTML. All rendering happens in Svelte components. Feed + roster data live in the public Blob store (written by `publish.py` on the Mac); server routes only read it.

No redeploy is ever needed for timetable changes. `vercel deploy --prod` runs ONLY when `src/` / `svelte.config.js` / `package.json` change.

## Repo map

| File | Role |
|---|---|
| `timetable_to_ics.py` | **LEGACY** v1 xlsx→ics core. Kept for parity comparison only. |
| `build_feeds.py` | **LEGACY** v1 all-student builder. Not used in production. |
| `build_feeds_v2.py` | **PRODUCTION** incremental v2 builder. Reads `incoming/`, writes `site/` via v2 pipeline. Only changed xlsx files are reclassified; per-file events cached in `site/.events/`. |
| `publish.py` | Blob uploader (shells out to `vercel blob put`, `--allow-overwrite`, stable pathnames). Loads OIDC env from `.env.local`, refreshes token on auth failure. Mirrors the `site/` tree. |
| `sync.py` | Orchestrator: fetch → hash-check → build (v2) → publish. Enforces 07:00–22:00 London window (launchd fires 24/7). |
| `fetch_sharepoint_timetable.py` | Playwright scraper, persistent profile in `.sharepoint-browser-profile/`. |
| `src/routes/api/student/+server.js` | JSON search over Blob `roster.json` + `spaces.json`. Statuses: match/picker/none/too-many/empty; entries carry kind: student|space (`kind` param disambiguates exact picks). Space matching ignores case+spacing (`southwing`→South Wing). Student carries `feed: {https, webcal, google}` built from request origin; spaces point at `feeds/spaces/`. |
| `src/routes/api/meta/+server.js` | JSON freshness `{updated_at}` (newest of roster/manifest stamps); frontend formats via `formatUpdated()`. |
| `src/routes/feeds/[slug]/+server.js` | .ics proxy: strips `.ics` suffix, checks slug against roster, ETag passthrough, 300s cache headers. |
| `src/routes/feeds/spaces/[slug]/+server.js` | Same for spaces: checks slug against `spaces.json`, proxies Blob `feeds/spaces/<slug>.ics`. |
| `src/lib/api.js` | Client lego: `searchStudent`, `pickStudent`, `fetchMeta`, `formatUpdated`, `prettySubject`, `feedUrls`, `copyText`. |
| `src/lib/StudentSearch.svelte` etc. | Styled components (search box / card / picker) with a Claude-website-inspired look: warm cream bg, serif headings, coral accent. Space cards (`kind: "space"`): static header with a `Space` chip (not expandable, no groups section), gaps labelled `free for X` instead of `X break`. |
| `src/app.css` | Tailwind CSS **v4** entry (CSS-first config): `@theme` defines cream/ink/coral palette + serif/sans/mono fonts; `@layer base` sets body styles. |
| `src/routes/+layout.svelte` | Imports `app.css`; everything else renders in `+page.svelte`. |
| `src/routes/+page.js` | SPA shell: `prerender = true` + `ssr = false`. Page carries no data; all roster/feed fetching happens client-side. |
| `src/lib/server/blob.js` | Server-only Blob reader: `BLOB_BASE_URL` env with public-URL fallback, 300s in-memory roster cache. Also serves `spaces.json` (`getSpaces`, empty when unpublished) and `spaceFeedUrls` (`/feeds/spaces/…`). |
| `svelte.config.js` | `adapter-vercel` (zero-config Vercel deploy; Framework Preset must be SvelteKit, not Python). |
| `vite.config.js` | SvelteKit + `@tailwindcss/vite` plugins. V4 deps: `tailwindcss`, `@tailwindcss/vite` (+ `@sveltejs/vite-plugin-svelte`). |
| `package.json` | SvelteKit app, **pnpm** (`dev`, `build`, `preview`). No Python requirements. |
| `pnpm-lock.yaml` | Committed — Vercel auto-detects pnpm from it; `packageManager` pins pnpm@10.9.0 (corepack-ready). |
| `incoming/` | Downloaded xlsx (gitignored). Filenames carry week numbers (`Week 1` → Mon 14-09-2026). |
| `site/` | Build output (gitignored): `feeds/*.ics`, `feeds/spaces/*.ics`, `roster.json`, `spaces.json`, `manifest.json`, `.events/*.json` (per-file event caches). |
| `.sync_state.json` | (gitignored) `last_built_hashes` + `published_hashes` — what makes sync/publish idempotent. |
| `docs/feed.ics` | DEAD. Old single-student GitHub Pages feed, fully deprecated. Do not revive. |
| `v2/` | **PRODUCTION** Jev-based matcher pipeline (see **v2 pipeline** below). Wired into `build_feeds_v2.py`. |

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

No env setup needed: server routes read `BLOB_BASE_URL` when set and fall back to the store's public URL otherwise, so `pnpm dev` works against live Blob data out of the box. (`vercel env pull .env.local` only if you need private env values locally; never commit that file.)

To develop against fresh local data instead of the published store (and skip the 300s server roster cache, which no browser hard-reload can clear):

```sh
python3 build_feeds_v2.py               # refresh site/ from incoming/ (no publish)
LOCAL_SITE_DIR=$PWD/site pnpm dev       # API + feeds read site/ from disk, caches off
```

Without `LOCAL_SITE_DIR`, dev reads Blob with the same 300s caches as prod. Code-only matcher changes never trigger a publish on their own (`sync.py` skips the build when no xlsx changed) — run `python3 sync.py --force --no-fetch` to rebuild + publish from `incoming/`.

**A build is not required after every UI change.** `pnpm dev` hot-reloads components, so iterate there; only run `pnpm build` when you need to verify the Vercel production build (or before deploying).

## v2 pipeline (Jev-based matcher — now the production path)

`v2/` rebuilds the xlsx → per-student events path from scratch. The architectural rule: **parse deterministically, classify with Jev, allocate deterministically.**

**Jev** is an LLM-based decision API on OpenRouter (`~typesafe/jev-latest`). It is called **only** for classification — never for allocation. Two passes:
1. **Group sheets** (`jev_classify.classify_sheet`): classify each non-empty cell as discipline / group / student (noul questions), then resolve student candidates against the full roster list (choice questions).
2. **Day sheets** (`day_classify.classify_day_sheet`): extract time blocks deterministically (`extract_blocks`), then classify each block for subject (choice from actual subjects + "Not a class"), target audience (choice from real groups/years + `student_match`), and per-week applicability (one noul per week in the file).

Results are cached **per sheet** in `v2/.cache/` — keyed by xlsx filename + sheet name (`Term 1a Week 1 2026__Monday 14th.json`), with a content-fingerprint and a pipeline version (`CACHE_VERSION` in `v2/cache.py`, currently `2`). A cache entry is reused only when both the filename+sheet key matches **and** the version is current.

**When iterating on the pipeline, run `python3 -m v2.v2_cli --nocache` (or `--force`) so you never read a stale cached result; bump `CACHE_VERSION` in `v2/cache.py` when the work is finished** so the next regular run invalidates old entries for good.

Deterministic stages (pure Python, no model):
- `day_classify.extract_blocks` walks each day sheet into blocks `{time, location, texts, colour, weekday}` — `_parse_time` keeps time ranges in `_block_texts` (shared slots like `12.45 - 1.30 Joanna- Nicky 12.45 - 1.30 Kitty - Jonathan` must split per attendee; ranges are stripped only for the Jev prompt), dashless sub-headers (`2.15 3.30`) roll forward from the morning, legend colours resolve to years via `(theme, idx, tint)` tuples.
- `group_parse.assemble` builds the roster + per-student groups (reuses v1 `parse_roster` + `MERGE_MAP`).
- `event_creator.build_events` assigns every event — membership, years, titles, 1-to-1 seeds: all deterministic. Mirrors v1's per-student OR semantics.
- `feed_gen` / `spaces` write ICS.

**1-to-1 resolver (`_resolve_student_match_batch`) does NOT call Jev**: attendees are deterministic name hits (aliases + `MERGE_MAP`); blocks with no recoverable name yield no event (v1 parity — `11.45-12.00 - Joanna` is dropped by `_leading_dash_cell`, never guessed).

**Parity**: `OPENROUTER_API_KEY=$(grep -m1 '^OPENROUTER_API_KEY=' .env | cut -d= -f2-) python3 -m v2.compare_v1v2` runs v2 on weeks 1+2, parses the v1 feeds at `/tmp/{name}_v1.ics` (regenerate stale ones with `build_feed.py --name X --year N -o /tmp/{x}_v1.ics`), and diffs `(date, start.time(), normalize_subject(name))` after stripping ` (Group X)` suffixes and applying `_NAME_ALIASES`. **`exit=1` means discrepancies found, not a crash.** Reference: 2026-09-22 run → `270 v1 / 270 v2 / 0 discrepancies` across all 9 compared students (Yotam, Buddy, Hazel, Mia, Holly, Bee, Charlie, Joanna, Kitty). The Phase-2 print line should say `seeded deterministically`.

Gotchas: never add an LLM call to event allocation (it was deliberately removed); `named_keys` must keep a seeded student out of the group loop or they get two events with different names; an all-year named block whose named student is in the same cohort is a known (rare, accepted) double-event edge case.

## Incremental build (`build_feeds_v2.py`)

Only xlsx files whose SHA256 changed since the last build go through the v2 classifier (their per-sheet classification cache + the per-file event cache make repeats free). Each file's events are stored under `site/.events/<stem>.json`. Feeds are the union of per-file events across ALL week files, deduped by deterministic UIDs (`make_uid` / `make_space_uid`); a feed is rewritten only when its normalized content changed (volatile `DTSTAMP`/`LAST-MODIFIED` lines excluded), so an update to one week touches only that week's events in each feed, leaving the rest byte-identical (and `publish.py` uploads nothing for unchanged feeds).

## Invariants (the pipeline depends on these)

- **Change detection ignores volatile lines**: `DTSTAMP`/`LAST-MODIFIED` are excluded when comparing feeds; `updated_at` excluded for roster/manifest. This is what makes no-change syncs upload 0 files. If you add a volatile field, exclude it too or every sync republishes everything.
- **Manifest feed hashes are normalized-content hashes**, not file bytes.
- **`site/` files are never deleted by git** (gitignored); `build_feeds_v2.py` prunes stale feeds locally and `publish.py` deletes their blobs.
- **Yotam's feed is the validated reference**: `build_feed.py` (legacy single-student script, still in repo) output must equal `site/feeds/yotam.ics` event-for-event after any matcher change. Check with the DTSTART/SUMMARY/LOCATION tuple diff.
- **CONFLICT lines on stderr are the impossibility guard** (one student, two places at once). They shout; they don't fail the build.

## Known limitations (as of 2026-09-22, after v2 production cutover)

- Remaining CONFLICTs are genuine 1-to-1-vs-group overlaps (Charlie's straps/creative vs Conditioning/PT; Joanna/Nem/Oakley Joe-1-to-1s vs Friday PAR practice). The guard shouts; humans resolve.
- Week 2 has no Year-2 conditioning session (Week 1's `Conditioning | All 2nd Year` vanished) and no Movement / Context 2 blocks exist in either week — those feeds are correctly sparse, not broken. Flag to whoever makes the spreadsheet.
- `Charlie (Creative)` possibly belonging to a different Charlie; pink 1-to-1 colour treated as session colour (named matching is colour-blind, so they land regardless).
- Mac asleep/off = no publishes (accepted; SharePoint login can't move server-side).
- Google's URL-importer caches failed fetches for hours and can take ~12h to first populate. If it errors, retry later or use Settings → Add calendar → From URL.
- Function CDN caches feeds 300s (`s-maxage`); blob objects 300s (`cache-control-max-age`). Worst-case publish→visible lag ≈ 5 min + client's own poll (≤60 min). Fine for the 10am-change case.
- No `/api/health` route (nothing needs it; Vercel reports function health itself).
- No per-IP rate limit on `/api/student` (dropped in the SvelteKit port; re-add if abused).

## Gotchas for the next agent

- **Vercel Framework Preset must be SvelteKit**: the project was created under the Python/FastAPI preset. After this port, set Project Settings → General → Framework Preset to **SvelteKit** (or redeploy once and let auto-detect kick in); otherwise the build will try to serve deleted `app.py`.
- **`.env.local` OIDC token expires** — `publish.py` auto-refreshes via `vercel env pull` on auth failure. Don't commit `.env.local` (gitignored via `.env*`).
- **`vercel blob put` needs `--allow-overwrite true`** for stable-pathname updates, plus sourced OIDC env when run outside `publish.py`.
- **`+layout.svelte` uses legacy `<slot />`**, not Svelte 5 `{@render children()}`. Works (compat mode, build is green) — don't "fix" it piecemeal; convert only if touching the layout anyway.
- **Don't touch `/opt/circomedia` dependencies**: the Hetzner box project copy is scrapped, but `bot@*.service` units there are someone else's — never touch.
- No commits unless the user asks.

## Lessons learned (2026-09-22 v2 production — read before re-deriving any of this)

- **Colour is load-bearing; read the legend, not just yellow.** Years are yellow/blue/orange per the Monday `Key` swatches (`parse_legend`, per file). Year-2 blue is a *theme* tint — compare `(theme, idx, tint)` tuples, never raw RGB (a naive reader reports it as unfilled). BTEC orange, Diploma pink, hire-blue and first-aider pink are cohort colours that must never match by year/group; pink doubles as the 1-to-1 colour (named matching stays colour-blind). A session's colour answers "which year?" better than its text.
- **Row-5 formatting carries semantics.** Bold row5 = group label (`Group 1`, `Major`); unbolded row5 person-name = the column's *first member* (the sheets use a peer-group model: Billie/Pipper/DeeDee/James/Bee head their own columns). Staff-only names and dash-form apparatus bookings in row5 grant nothing; dash-less annotated/duet row5s resolve to verified members (see booking-cells lesson below). This was confirmed from formatted screenshots, not guessed — when the parse is ambiguous, ask for one.
- **Helper text is not a session.** `PT Minors | research & materials | On teams` sits uncoloured inside the orange PAR slot: annotation, no events. Rule of thumb: slot identity comes from header colour + primary subject structure; stray body lines grant no attendance (subject-scoped matching enforces this for free).
- **Booking cells mint phantom students.** Dash-form `X - Hoop/Rod` bookings, `?` garble, `need X,` roster notes and teacher names in apparatus columns all look like members to a naive parser and grant nothing. But dash-less `Name + apparatus` annotations (`Charlie straps`, `Charlie rope`, `Billie (minor) dance trap`) and `&` duets (`Oakley & Joanna`, `Lucy & Nem`) ARE real students — they resolve to roster-verified members via `_member_names` (apparatus/parens stripped, all-or-nothing for duets, teachers excluded), and an annotated row5 makes its day-column a peer column instead of killing it for everyone (this exact bug once dropped the whole Tue/Wed/Thu Year-3 Aerial grid). Filters live in `_cell_is_junk` + `_row5_kind` + `_member_names`; the `THIN:` stderr report in `build_feeds_v2.py` (< 8 events) is the tripwire — advisory, never fatal. Teacher-names-that-are-only-teachers (Janine/Nicky/Joe/Lewis, Jonathan-as-student) die here too: anyone absent from every all-hands column (Context3/PT-Wed/PAR/Core) is not a Year-3 student.
- **Nicknames need explicit merges.** Core-Skills shorthand (`Pip`, `Fin`), apparatus-tied spellings and splits (`Maddie`/`Madeline`, `Meg`/`Megan`, `JJ`/`JJ Angel`) go in `MERGE_MAP` + `DISPLAY_OVERRIDES`; merged-away keys must never win display (`_merged_away`). Single-subject roster entries are guilty until proven innocent.
- **Time handling has three traps.** Dashless sub-headers (`2.15 3.30`, Thu Acro minors) must split blocks; names embedded in time-header cells (`... Kitty - Jonathan`) must be recovered, not discarded with the header; and NEVER guess pm from row position — a dense morning column sits low on the sheet, and 11.45am misread as 23:45 silently drops the session (`end <= start` → `continue` with no warning). `block_times` rolls hours < 8 forward by construction; only reinterpret as pm when the straight parse is impossible.
- **Shared slots need per-attendee titles.** `Lucy - Joe | Tan - Jonathan` in one slot produced `Lucy - Joe` in Tan's feed; title from the attendee's own segment when a block holds several 1-to-1s.
- **Week files legitimately differ.** Valkyrie does Clown in Week 1 but not Week 2 (cast churn, plus `need X,` editing notes) — week-faithful output, not a bug to "fix". The `Martha` sheet (a real Year-2 personal timetable) is useful ground truth; Movement/Context-2 sessions appear there but were never put on the day sheets, so those feeds are correctly sparse.
- **Validate like this:** Yotam tuple-diff (DTSTART/SUMMARY/LOCATION, must be empty), THIN report empty, CONFLICTs all human-plausible (1-to-1 vs group), rebuild idempotent (0 files rewritten), `pnpm build` green.