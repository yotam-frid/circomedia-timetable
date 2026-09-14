"""Circomedia timetable web app (FastAPI, runs on Vercel).

Routes:
  GET /api/health          deploy check, touches no data
  GET /api/meta            {updated_at} freshness for the page footer
  GET /api/student?name=   htmx fragment: match card, picker, or not-found
  GET /feeds/{slug}.ics    per-student calendar feed, proxied from Blob store

Feed + roster data live in a public Vercel Blob store (written by publish.py
on the Mac); this function only reads. No secrets needed here.
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

LONDON = ZoneInfo("Europe/London")
BLOB_BASE = os.environ.get("BLOB_BASE_URL", "").rstrip("/")
ROSTER_TTL = 300  # seconds; roster changes rarely, feeds must stay fresh

_cache = {}  # key -> (expires_epoch, value)

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# /api/student: 30 requests / 60s per IP. Per-instance memory; enough to
# deter casual abuse on a classmates-only tool.
RATE_LIMIT = 30
RATE_WINDOW = 60
_hits = {}  # ip -> [epochs]


def blob_get(path, extra_headers=None):
    """Fetch a blob path; returns (status, headers, body)."""
    if not BLOB_BASE:
        raise RuntimeError("BLOB_BASE_URL is not configured")
    req = urllib.request.Request(
        BLOB_BASE + path, headers=extra_headers or {},
        method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status or 200, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def header_ci(headers, name):
    """Case-insensitive header lookup (blob CDN uses lowercase names)."""
    want = name.lower()
    for k, v in headers.items():
        if k.lower() == want:
            return v
    return None


def cached(key, ttl, loader):
    now = time.time()
    if key in _cache and _cache[key][0] > now:
        return _cache[key][1]
    value = loader()
    _cache[key] = (now + ttl, value)
    return value


def get_roster():
    def load():
        status, _, body = blob_get("/roster.json")
        if status != 200:
            raise RuntimeError(f"roster.json -> HTTP {status}")
        return json.loads(body.decode("utf-8"))
    return cached("roster", ROSTER_TTL, load)


def get_manifest():
    def load():
        status, _, body = blob_get("/manifest.json")
        if status != 200:
            raise RuntimeError(f"manifest.json -> HTTP {status}")
        return json.loads(body.decode("utf-8"))
    return cached("manifest", ROSTER_TTL, load)


def rate_limited(ip):
    now = time.time()
    stamps = [t for t in _hits.get(ip, []) if now - t < RATE_WINDOW]
    stamps.append(now)
    _hits[ip] = stamps[-RATE_LIMIT:]
    return len(stamps) > RATE_LIMIT


def pretty_subject(key):
    if key == "context1":
        return "Context 1"
    return key.replace("_", " ").title()


def feed_urls(request, slug):
    base = str(request.base_url).rstrip("/")
    https_url = f"{base}/feeds/{slug}.ics"
    webcal_url = https_url.replace("https://", "webcal://").replace(
        "http://", "webcal://")
    google_url = ("https://calendar.google.com/calendar/r?cid="
                  + urllib.parse.quote_plus(https_url))
    return https_url, webcal_url, google_url


def student_card(request, student):
    https_url, webcal_url, google_url = feed_urls(request, student["slug"])
    name = html.escape(student["name"])
    chips = "".join(
        f'<span class="inline-block rounded-full bg-indigo-100 text-indigo-800 '
        f'text-xs px-2.5 py-1 mr-1.5 mb-1.5">{html.escape(pretty_subject(k))} '
        f'&middot; {html.escape(v)}</span>'
        for k, v in student.get("groups", {}).items())
    return f"""
<div class="rounded-xl border border-indigo-200 bg-white p-5 shadow-sm">
  <div class="text-lg font-semibold text-slate-900">{name}
    <span class="ml-2 text-sm font-normal text-slate-500">Year {student["year"]}</span></div>
  <div class="mt-2">{chips or '<span class="text-sm text-slate-400">No group info this week</span>'}</div>
  <label class="mt-4 block text-xs font-medium text-slate-500" for="feed-url">Your calendar feed (keep it private)</label>
  <div class="mt-1 flex gap-2">
    <input id="feed-url" readonly value="{html.escape(https_url)}"
      class="w-full rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700" />
    <button onclick="copyFeed(this)" data-url="{html.escape(https_url)}"
      class="shrink-0 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700">Copy</button>
  </div>
  <div class="mt-3 flex flex-wrap gap-2">
    <a href="{html.escape(webcal_url)}"
      class="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700">Subscribe (Apple / iPhone)</a>
    <a href="{html.escape(google_url)}" target="_blank" rel="noopener"
      class="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100">Add to Google Calendar</a>
  </div>
  <p class="mt-3 text-xs text-slate-500">Android / other: copy the link, then in your calendar app choose
  &ldquo;Subscribe from URL&rdquo; / &ldquo;New calendar subscription&rdquo; and paste it. Updates arrive automatically.</p>
</div>"""


def picker(matches):
    items = "".join(
        f'<li><button hx-get="/api/student?name={urllib.parse.quote_plus(m["name"])}&amp;exact=1" '
        f'hx-target="#result" hx-swap="innerHTML" '
        f'class="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-sm hover:bg-indigo-50">'
        f'{html.escape(m["name"])} <span class="text-slate-400">Year {m["year"]}</span></button></li>'
        for m in matches)
    return f"""
<div class="rounded-xl border border-amber-200 bg-amber-50 p-5">
  <p class="text-sm font-medium text-amber-900">A few people match &mdash; who are you?</p>
  <ul class="mt-3 space-y-2">{items}</ul>
</div>"""


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta():
    """Plain-text freshness stamp (htmx swaps it straight into the footer)."""
    try:
        manifest = get_manifest()
        updated = manifest.get("updated_at", "")
        try:
            pretty = datetime.fromisoformat(updated).astimezone(LONDON).strftime(
                "%a %-d %b, %H:%M")
        except Exception:
            pretty = updated
        return PlainTextResponse(pretty or "recently")
    except Exception:
        return PlainTextResponse("recently", status_code=503)


@app.get("/api/student", response_class=HTMLResponse)
def student(request: Request, name: str = "", exact: str = ""):
    ip = request.client.host if request.client else "?"
    if rate_limited(ip):
        return HTMLResponse(
            '<p class="text-sm text-red-600">Too many searches &mdash; wait a minute and try again.</p>',
            status_code=429)
    q = name.strip()[:60]
    if not q:
        return HTMLResponse("")
    try:
        students = get_roster()["students"]
    except Exception:
        return HTMLResponse(
            '<p class="text-sm text-red-600">Timetable is updating &mdash; try again in a minute.</p>',
            status_code=502)
    if exact:
        for s in students:
            if s["name"].lower() == q.lower():
                return HTMLResponse(student_card(request, s))
    matches = [s for s in students if q.lower() in s["name"].lower()]
    if len(matches) == 1:
        return HTMLResponse(student_card(request, matches[0]))
    if not matches:
        return HTMLResponse(
            f'<div class="rounded-xl border border-slate-200 bg-white p-5">'
            f'<p class="text-sm text-slate-700">No one called '
            f'&ldquo;{html.escape(q)}&rdquo; in this term&rsquo;s timetable.</p>'
            f'<p class="mt-1 text-xs text-slate-500">Check the spelling, or ask Yotam &mdash; '
            f'new names appear when the next weekly sheet is published.</p></div>')
    if len(matches) > 12:
        return HTMLResponse(
            f'<p class="text-sm text-slate-600">{len(matches)} people match '
            f'&ldquo;{html.escape(q)}&rdquo; &mdash; type a bit more of the name.</p>')
    return HTMLResponse(picker(matches[:12]))


@app.get("/feeds/{slug}.ics")
@app.head("/feeds/{slug}.ics")
def feed(request: Request, slug: str):
    if not SLUG_RE.match(slug):
        return PlainTextResponse("unknown feed", status_code=404)
    try:
        roster_slugs = {s["slug"] for s in get_roster()["students"]}
    except Exception:
        return PlainTextResponse("timetable updating, try again soon",
                                 status_code=502)
    if slug not in roster_slugs:
        return PlainTextResponse("unknown feed", status_code=404)
    if request.method == "HEAD":
        return Response(status_code=200, headers={
            "Content-Type": "text/calendar; charset=utf-8",
            "Cache-Control": "public, max-age=300",
        })
    fwd = {}
    if_none = request.headers.get("if-none-match")
    if if_none:
        fwd["If-None-Match"] = if_none
    try:
        status, headers, body = blob_get(f"/feeds/{slug}.ics", fwd)
    except Exception:
        return PlainTextResponse("timetable updating, try again soon",
                                 status_code=502)
    if status == 304:
        return Response(status_code=304,
                        headers={"ETag": header_ci(headers, "ETag") or ""})
    if status != 200:
        return PlainTextResponse("unknown feed", status_code=404)
    out_headers = {
        "Content-Type": "text/calendar; charset=utf-8",
        "Content-Disposition": f'inline; filename="{slug}.ics"',
        "Cache-Control": "public, max-age=300",
        "CDN-Cache-Control": "public, s-maxage=300, stale-while-revalidate=60",
        "Vercel-CDN-Cache-Control": "public, s-maxage=300, stale-while-revalidate=60",
    }
    etag = header_ci(headers, "ETag")
    if etag:
        out_headers["ETag"] = etag
    return Response(content=body, status_code=200, headers=out_headers)


@app.get("/", response_class=HTMLResponse)
def index_fallback():
    """Fallback if the CDN does not serve public/index.html first."""
    try:
        with open("public/index.html", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except FileNotFoundError:
        return HTMLResponse("<h1>Circomedia timetable</h1>", status_code=503)
