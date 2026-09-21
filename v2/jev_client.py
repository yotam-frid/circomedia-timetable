"""Jev API wrapper — thin POST to OpenRouter Decisions endpoint."""

import json
import os
import ssl
import urllib.request

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "~typesafe/jev-latest"


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

    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()
        except Exception:
            pass
        raise RuntimeError(f"Jev API {e.code}: {err_body}") from e

    return body.get("answers", {})
