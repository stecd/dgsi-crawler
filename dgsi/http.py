"""Polite, cached HTTP for DGSI.

DGSI is an old Lotus Domino server that rate-limits bursts (503s). This module
wraps requests with: a shared session, a minimum delay between calls, retry with
backoff on 503/timeout, and an on-disk cache keyed by URL so re-runs never re-hit
the server. Ruling documents are latin-1; search-results pages are utf-8.
"""
from __future__ import annotations
import hashlib
import time
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "Mozilla/5.0 (dgsi-crawler; academic/journalistic research)"})

_MIN_INTERVAL = 1.2          # seconds between live requests (be a good citizen)
_last_call = [0.0]


def _throttle() -> None:
    wait = _MIN_INTERVAL - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.time()


def _cache_path(url: str) -> Path:
    return CACHE / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")


def get(url: str, *, encoding: str = "utf-8", use_cache: bool = True,
        retries: int = 4) -> str:
    """Fetch url as text. Caches raw bytes on disk; decodes with `encoding`."""
    cp = _cache_path(url)
    if use_cache and cp.exists():
        return cp.read_bytes().decode(encoding, "replace")

    last_err = None
    for attempt in range(retries):
        try:
            _throttle()
            resp = _SESSION.get(url, timeout=60)
            if resp.status_code == 503:
                raise requests.HTTPError("503 rate-limited")
            resp.raise_for_status()
            cp.write_bytes(resp.content)
            return resp.content.decode(encoding, "replace")
        except (requests.RequestException, requests.HTTPError) as e:
            last_err = e
            time.sleep(2 ** attempt * 1.5)   # 1.5, 3, 6, 12s backoff
    raise RuntimeError(f"failed to fetch {url}: {last_err}")
