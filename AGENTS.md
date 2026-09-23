# AGENTS.md — circomedia-timetable

Micro-app for Circomedia classmates: search by student or bookable-space name, see groups, and get a personal calendar feed URL.

## Architecture

Data flows **Mac → Hetzner box → Vercel**. Vercel never fetches timetable data from the Mac.

```text
Mac (launchd every 6h; SharePoint login only works here)
  fetch_sharepoint_timetable.py --all --headless → incoming/Term*week*.xlsx
  build_feeds_v2.py                             → site/feeds/, site/roster.json,
                                                   site/spaces.json, site/manifest.json,
                                                   site/.events/
  publish.py                                    → rsync site/ to the Hetzner box

Hetzner box (nginx + adapter-node)
  /srv/timetable       static data
  /srv/timetable-app   UI + /api/*, reads LOCAL_SITE_DIR=/srv/timetable
  https://circomedia.yotamfrid.dev

Vercel (SvelteKit + adapter-vercel)
  https://circomedia-timetable.vercel.app
  /feeds/* and /feeds/spaces/* proxy BLOB_BASE_URL to the box
```

The launchd plist, nginx/systemd units, TLS, Vercel environment, and other host configuration live outside this repository. Verify them on the host/dashboard. The API returns JSON on success; `/api/meta` has a plain-text 503 fallback, and feed routes return ICS. `BLOB_BASE_URL` defaults to the box.

Feed paths are a manual compatibility invariant: never rename a known path or slug. Slugs are recomputed from display names on each build; there is no persisted alias map, so review slug changes before publishing.

## Key files and commands

- `build_feeds_v2.py`: production incremental builder; `publish.py`: rsync publisher; `sync.py`: fetch → build → optional publish.
- `v2/`: Jev classifier, deterministic roster parser, event allocator, feed writers, and parity tools.
- `timetable_to_ics.py`: legacy v1 single-student CLI/parity reference. Production imports only compatibility slug/display helpers.
- `src/routes/api/student/+server.js`, `src/routes/feeds/*`, and `src/lib/server/blob.js`: search, feed proxying, and box/local-data access.
- `deploy.sh`: builds adapter-node for the box and adapter-vercel for Vercel, then deploys both.

```sh
corepack enable
pnpm install
pnpm dev
pnpm build                 # default adapter-vercel build
python3 build_feeds_v2.py  # refresh site/ from incoming/
LOCAL_SITE_DIR=$PWD/site pnpm dev
```

`sync.py` always runs `build_feeds_v2.py`; the builder reuses each workbook’s final event cache when possible. Publishing is opt-in:

```sh
python3 sync.py                 # build only
python3 sync.py --publish       # build and publish
python3 sync.py --force         # force final event reprocessing
python3 sync.py --no-fetch --publish
```

`build_feeds_v2.py --force` does **not** disable the Jev sheet cache; use `python3 -m v2.v2_cli --nocache` (or its `--force` alias) to force fresh Jev classification. The Python pipeline has no committed dependency manifest, but building requires `openpyxl` and fetching requires `playwright`.

## v2 pipeline

The rule is:

> **Parse deterministically, classify with Jev, allocate deterministically.**

Python owns worksheet parsing, times, colours, roster assembly, membership, event allocation, deduplication, and ICS generation. Jev supplies labels/classifications only; it never decides which students receive an event.

Jev uses the OpenRouter Decisions model `~typesafe/jev-latest`. A call sends structured `state` plus structured questions: `noul` means yes/no with a numeric answer; `choice` means one supplied option. There is no free-form calendar-building prompt. Returned confidence and answer fields are not validated.

### What Jev is asked

- **Sheet metadata:** What type of sheet is this? If it is a day sheet, which day? If it is a group sheet, which year or Core Skills cohort? (`v2/v2_cli.py:89-175`)
- **Group-sheet cells, pass 1:** For every non-empty cell, ask whether it is a discipline, group label, student, teacher, apparatus booking, or junk, and which specific group label it represents. Cells are batched 40 per request. (`v2/jev_classify.py:18-101`)
- **Group-sheet cells, pass 2:** For cells passing the student threshold, ask “Which student does this text refer to?” Options are unique candidate strings from the current worksheet, not a cross-sheet roster. (`v2/jev_classify.py:113-164`)
- **Day blocks:** For each block, ask subject, target audience, whether it is a real class, whether it has an owner, whether it is a 1-to-1 private lesson, canonical subject, and one `week N` applicability question per week in the file. Subject/target/owner options come from the parsed roster. (`v2/day_classify.py:297-443`)
- **CLI-only filename weeks:** `v2.v2_cli` may ask which weeks a filename covers when `--weeks` is omitted. Production uses deterministic `v2.weeks.weeks_from_filename` instead. (`v2/v2_cli.py:32-86`)

Jev fallbacks are deliberately small: a literal `Group D` overrides a misleading target; explicit audience/year text and the colour legend override vague answers; named blocks seed attendees from roster names/aliases; a false/missing real-class answer becomes `Not a class`; empty week answers mean all covered weeks. An unnamed `student_match` is allocated by the v1-parity day-session rule only when its own text contains a recognizable subject; booking/staff text without one stays silent. Network failures do not fall back to stale results. HTTP 408/409/425/429/500/502/503/504/529, URL errors, timeouts, and malformed JSON are retried up to six times with `5/15/30/60/120`-second waits.

## Caches and builds

Jev results live in `v2/.cache/`, keyed by workbook stem plus sheet name. `CACHE_VERSION` is `3`. A hit requires the current version, matching fingerprint, and readable JSON. Group caches store assembled memberships; day caches store final classifications, not raw API answers. Prompt text and model name are not fingerprinted, so bump `CACHE_VERSION` when changing Jev prompts/options/model. The day fingerprint includes blocks, group options, file weeks, and roster-derived student names.

Production considers only `incoming/*.xlsx` names beginning with `term` and containing `week`. It reprocesses a workbook when its SHA-256 changes, its final event cache is missing/stale, or `EVENTS_VERSION` differs. `EVENTS_VERSION` is `3`; that cache stores final allocated events, so bump it for any change that should alter extracted data, classification, allocation, dates, or event payloads. Feeds combine all week files, deduplicate by deterministic UIDs, and rewrite only when normalized content changes. `DTSTAMP`, `LAST-MODIFIED`, and `updated_at` are excluded from change detection; local/remote stale feeds are pruned by the builder/rsync `--delete`.

## Allocation and parser gotchas

- Read the workbook’s colour legend, not raw RGB. Year-2 blue is a theme tint; BTEC, Diploma, hire-blue, and other “other” fills are not ordinary year colours.
- `color_year=None` does not automatically mean “no audience.” An unnamed `other` block is skipped only when it also lacks explicit audience text such as `Group X`, `PAR Group X`, `Major`, `Minors`, `All`, or a year marker. Named blocks remain colour-blind where intended.
- Literal group labels come from the sheet; Jev’s group choice is only a suggestion. Time parsing rolls hours below 8 forward; if an allocated event still has `end <= start`, v2 adds 12 hours to both values. Shared time-header cells retain embedded names for per-attendee titles.
- The 1-to-1 resolver does not call Jev: it returns deterministic roster-name seeds. A `student_match` with no seed produces no private event.
- Space feeds are occupancy feeds, not Jev-classified student feeds: `v2/spaces.py` emits extracted non-closed blocks for every week covered by the workbook and ignores `(wkN)` markers.
- v2 has no `CONFLICT` detector. `CONFLICT` output belongs to the legacy v1 reference CLI, not the production builder.
- `v2/day_classify.py` defines `_fix_classification` twice; the later definition shadows the earlier one. `CANONICAL_SUBJECT_MAP` is currently unused; returned choice keys are consumed directly.
- Row-5, booking-cell, `_member_names`, `_merged_away`, and `block_times` are v1-parser history. The current v2 equivalents are the Jev cell questions, `group_parse`, and deterministic event rules.

## Validation

```sh
python3 -m v2.compare_v1v2
```

The command uses `OPENROUTER_API_KEY` from the environment or `.env`, runs v2 for weeks 1+2, reads v1 references from `/tmp/{name}_v1.ics`, and compares date, start time, and normalized subject. Exit 1 means discrepancies, not a crash; location is not part of its comparison key. Generate a corresponding v1 slice with:

```sh
python3 timetable_to_ics.py "incoming/<workbook>.xlsx" --name <name> --year <n> -o /tmp/<name>_<week>_v1.ics
```

A dated 2026-09-23 run reported `262 v1 / 262 v2 / 0 discrepancies` across nine students; treat that as historical, not a guarantee. For matcher changes, manually diff Yotam’s DTSTART/SUMMARY/LOCATION tuples, inspect advisory `THIN:` output, rebuild twice to confirm no normalized rewrites, and finish with `pnpm build`. `THIN` only covers student feeds rewritten in that run.

## Deployment and limitations

- Vercel project: `circomedia-timetable`; `BLOB_BASE_URL` must point to the real box origin and is set for production and preview.
- The box serves data statically and runs `timetable.service` from `/srv/timetable-app`; the service uses `LOCAL_SITE_DIR=/srv/timetable` and `ORIGIN=https://circomedia.yotamfrid.dev`.
- UI deploys use `./deploy.sh`, not a data publish. Feed responses use 300-second cache headers; generated feeds advertise 30-minute refresh/TTL, but client polling is client-dependent.
- The calendar is term-2026-specific: `v2/event_creator.py` hard-codes Week 1 as 2026-09-14.
- The scraper currently matches only single-week filename forms; range-form workbooks must be added manually or the regex extended.
- Google’s URL importer can cache failed fetches for hours; retry later or use Settings → Add calendar → From URL.
- Do not touch `/srv/bottega` or `/opt/circomedia`; they belong to other services. Do not add an LLM call to event allocation. Do not commit unless explicitly asked.
