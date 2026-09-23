"""Jev API wrapper — thin POST to OpenRouter Decisions endpoint."""

import json
import os
import ssl
import time
import urllib.error
import urllib.request

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "~typesafe/jev-latest"

# OpenRouter answers 529 ("system_overloaded") whenever Jev is under load,
# which is routine — a 15-sheet build makes ~15 batched calls and will hit
# it. Retry those with backoff instead of failing the whole build (or the
# 6-hourly sync) on a transient condition. 4xx (bad request, auth) is not
# retried: it will never succeed.
RETRIABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = (5, 15, 30, 60, 120)


def _get_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        from pathlib import Path
        env = Path(__file__).resolve().parent.parent / ".env"
        for line in env.read_text().splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY env var or put it in .env")
    return key


def jev_call(state, questions: dict) -> dict:
    """Send a Jev Decisions request and return the answers dict.

    Parameters
    ----------
    state : str or dict
        The state context (cell dump + rules).
    questions : dict
        Question-id -> question spec dict. Each spec must have:
          - "type": "noul" | "choice" | "score"
          - "instructions": str (the question text)
          - "criteria": dict or list (answer options/labels)
        For noul: criteria = {"true": ..., "false": ...}
        For choice: criteria = {"opt1": ..., "opt2": ...}

    Returns
    -------
    dict
        Mapping of question-id -> answer dict (with "type", value field,
        and optionally "confidence", "probabilities").
    """
    payload = {
        "model": MODEL,
        "state": state,
        "questions": questions,
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {_get_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()

    last_error = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
                body = json.loads(resp.read())
            return body.get("answers", {})
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode()
            except Exception:
                pass
            last_error = f"Jev API {e.code}: {err_body}"
            if e.code not in RETRIABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(last_error) from e
            wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            print(f"  Jev {e.code} (attempt {attempt + 1}/{MAX_ATTEMPTS}), "
                  f"retrying in {wait}s")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = f"Jev request failed: {e}"
            if attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(last_error) from e
            wait = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
            print(f"  Jev {type(e).__name__} (attempt {attempt + 1}/"
                  f"{MAX_ATTEMPTS}), retrying in {wait}s")
        time.sleep(wait)

    raise RuntimeError(last_error or "Jev request failed")
