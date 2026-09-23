"""Versioned per-sheet result cache for the v2 pipeline.

Sheets already processed in an xlsx file ("Monday 21", "Year 3 Groups")
are stored under v2/.cache/ keyed by the xlsx file name + sheet name.
An entry is reused only when CACHE_VERSION below matches the version
stored in the entry (the pipeline-processing version) and the caller's
sheet-content fingerprint still matches.
"""

# Bump this version whenever pipeline processing changes, in order to
# invalidate the cache: entries written under another version stop
# matching and are recomputed on the next run.
CACHE_VERSION = 3

# Set to True to completely skip cache (for development/debugging)
SKIP_CACHE = False

import hashlib
import json
import re
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / ".cache"

_enabled = True


def set_enabled(on: bool) -> None:
    """Master switch (--nocache turns caching off for the whole run)."""
    global _enabled
    _enabled = bool(on)


def enabled() -> bool:
    return _enabled


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def _path(xlsx_name: str, sheet_name: str) -> Path:
    stem = Path(xlsx_name).stem
    return CACHE_DIR / f"{_slug(stem)}__{_slug(sheet_name)}.json"


def load(xlsx_name, sheet_name, fingerprint=None):
    """Cached data for a sheet, or None (disabled / missing / stale)."""
    if SKIP_CACHE:
        return None
    if not _enabled or not xlsx_name:
        return None
    path = _path(xlsx_name, sheet_name)
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if entry.get("version") != CACHE_VERSION:
        return None
    if fingerprint is not None and entry.get("fingerprint") != fingerprint:
        return None
    return entry.get("data")


def save(xlsx_name, sheet_name, data, fingerprint=None):
    """Store a sheet result (no-op when caching is disabled)."""
    if SKIP_CACHE:
        return
    if not _enabled or not xlsx_name:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entry = {"version": CACHE_VERSION, "fingerprint": fingerprint, "data": data}
    _path(xlsx_name, sheet_name).write_text(json.dumps(entry))


def fingerprint(*parts) -> str:
    """Stable hash of the JSON-able inputs a cached result depends on."""
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()