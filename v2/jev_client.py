"""Content-addressed Jev requests, bounded concurrency and coordinated retries."""

import copy
import hashlib
import json
import os
import random
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from functools import lru_cache

from . import cache

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "~typesafe/jev-latest"
REQUEST_CACHE_VERSION = 1
MAX_WORKERS = max(1, int(os.environ.get("JEV_CONCURRENCY", "4")))
RETRIABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
OVERLOAD_STATUS = {429, 503, 529}
MAX_ATTEMPTS = 6
BACKOFF_SECONDS = (5, 15, 30, 60, 120)

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="jev")
_lock = threading.Lock()
_print_lock = threading.Lock()
_flights = {}
_metrics = {}


def _log(message):
    with _print_lock:
        print(message, flush=True)


def _record(**values):
    with _lock:
        for key, value in values.items():
            _metrics[key] = _metrics.get(key, 0) + value


def metrics():
    with _lock:
        return dict(_metrics)


def reset_metrics():
    with _lock:
        _metrics.clear()


def record_block_cache_hit():
    _record(block_cache_hits=1)


def report_metrics(wall_seconds):
    m = metrics()
    mean_success = m.get("success_api_seconds", 0) / max(1, m.get("successes", 0))
    print(f"Jev: {m.get('http_requests', 0)} HTTP attempts, "
          f"{m.get('successes', 0)} successful requests, "
          f"{m.get('cache_hits', 0)} raw-cache hits, "
          f"{m.get('block_cache_hits', 0)} block-cache hits, "
          f"{m.get('coalesced', 0)} shared requests, "
          f"{m.get('retries', 0)} retries, {m.get('failures', 0)} failures; "
          f"API {m.get('api_seconds', 0):.2f}s, "
          f"successful-attempt mean {mean_success:.2f}s, "
          f"queue {m.get('queue_seconds', 0):.2f}s, "
          f"backoff {m.get('backoff_seconds', 0):.2f}s "
          f"(summed across workers); build wall {wall_seconds:.2f}s")


def parallel_map(function, items):
    """Run independent jobs on one shared pool; return results in input order."""
    def run(item, submitted):
        _record(queue_seconds=time.monotonic() - submitted)
        return function(item)

    futures = [_executor.submit(run, item, time.monotonic()) for item in items]
    try:
        return [future.result() for future in futures]
    finally:
        # Running jobs finish and checkpoint their success even if a sibling
        # fails. Cancel queued jobs and drain before a caller can start a retry.
        for future in futures:
            future.cancel()
        for future in futures:
            if not future.cancelled():
                try:
                    future.result()
                except Exception:
                    pass


class _RequestGate:
    """Shared in-flight limit and provider-wide overload cooldown."""

    def __init__(self, limit):
        self.maximum = limit
        self.limit = limit
        self.active = 0
        self.cooldown_until = 0
        self.healthy = 0
        self.condition = threading.Condition()

    def acquire(self):
        queue = backoff = 0
        with self.condition:
            while True:
                delay = self.cooldown_until - time.monotonic()
                if delay <= 0 and self.active < self.limit:
                    self.active += 1
                    return queue, backoff
                start = time.monotonic()
                self.condition.wait(timeout=delay if delay > 0 else None)
                elapsed = time.monotonic() - start
                if delay > 0:
                    backoff += elapsed
                else:
                    queue += elapsed

    def release(self):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()

    def overload(self, wait):
        with self.condition:
            self.limit = max(1, self.limit // 2)
            self.healthy = 0
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + wait)
            self.condition.notify_all()

    def success(self):
        with self.condition:
            self.healthy += 1
            if self.healthy >= self.maximum and self.limit < self.maximum:
                self.limit += 1
                self.healthy = 0
                self.condition.notify_all()


_gate = _RequestGate(MAX_WORKERS)


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


@lru_cache(maxsize=1)
def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def request_key(state, questions):
    """Fingerprint exactly what the model sees, plus endpoint/schema revision."""
    data = {"endpoint": API_URL, "revision": REQUEST_CACHE_VERSION,
            "model": MODEL, "state": state, "questions": questions}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _retry_after(headers):
    value = headers.get("Retry-After") if headers else None
    if not value:
        return 0
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 0


def _request(state, questions, key):
    data = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(API_URL, data=data, headers={
        "Authorization": f"Bearer {_get_key()}", "Content-Type": "application/json",
    }, method="POST")
    for attempt in range(MAX_ATTEMPTS):
        queue, backoff = _gate.acquire()
        _record(queue_seconds=queue, backoff_seconds=backoff, http_requests=1)
        started = time.monotonic()
        retry_wait = 0
        overload = False
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ssl_context()) as resp:
                body = json.loads(resp.read())
            # Never checkpoint a missing/truncated answer set as a success.
            if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
                raise ValueError("Jev response has no answers object")
            answers = body["answers"]
            if any(not isinstance(answers.get(q), dict) for q in questions):
                raise ValueError("Jev response is missing requested answers")
            _gate.success()
            success_seconds = time.monotonic() - started
            _record(successes=1, success_api_seconds=success_seconds)
            if os.environ.get("JEV_TRACE") == "1":
                _log(f"  Jev {key[:8]} OK: {len(questions)} questions, "
                     f"{success_seconds:.2f}s (attempt {attempt + 1})")
            return answers
        except urllib.error.HTTPError as e:
            message = f"Jev API {e.code}: {e.read().decode(errors='replace')}"
            if e.code not in RETRIABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(message) from e
            retry_wait = _retry_after(e.headers)
            overload = e.code in OVERLOAD_STATUS
            label = str(e.code)
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            if attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError(f"Jev request failed: {e}") from e
            label = type(e).__name__
        finally:
            elapsed = time.monotonic() - started
            _record(api_seconds=elapsed)
            # Publish the cooldown before releasing the slot, so another
            # worker cannot launch in the gap after an overload response.
            if overload:
                retry_wait = max(retry_wait, BACKOFF_SECONDS[attempt]) + random.uniform(0, 1)
                _gate.overload(retry_wait)
            _gate.release()
        if not overload:
            retry_wait = max(retry_wait, BACKOFF_SECONDS[attempt]) + random.uniform(0, 1)
        _record(retries=1)
        _log(f"  Jev {key[:8]} {label} after {elapsed:.2f}s "
             f"(attempt {attempt + 1}/{MAX_ATTEMPTS}), "
             f"{'shared cooldown' if overload else 'retry'} {retry_wait:.2f}s")
        if not overload:
            started = time.monotonic()
            time.sleep(retry_wait)
            _record(backoff_seconds=time.monotonic() - started)


def jev_call(state, questions: dict) -> dict:
    """Reuse exact successful requests; checkpoint successes before returning.

    Cache-disabled runs bypass both reads and writes. Concurrent identical
    payloads share one request, including its retries; errors are never cached.
    """
    key = request_key(state, questions)
    with _lock:
        future = _flights.get(key)
        leader = future is None
        if leader:
            future = _flights[key] = Future()
    if not leader:
        _record(coalesced=1)
        return copy.deepcopy(future.result())
    try:
        answers = cache.load_artifact("requests", key)
        if isinstance(answers, dict) and all(isinstance(answers.get(q), dict) for q in questions):
            _record(cache_hits=1)
        else:
            answers = _request(state, questions, key)
            cache.save_artifact("requests", key, answers)
        future.set_result(answers)
        return copy.deepcopy(answers)
    except Exception as e:
        _record(failures=1)
        future.set_exception(e)
        raise
    finally:
        with _lock:
            del _flights[key]
