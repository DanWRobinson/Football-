"""Polite HTTP client with on-disk caching, retries and per-host rate limiting."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from .. import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_last_hit: dict[str, float] = {}
MIN_INTERVAL = float(os.environ.get("FOOTY_MIN_INTERVAL", "1.5"))  # seconds between hits per host


class FetchError(RuntimeError):
    pass


def _cache_path(url: str, params: dict | None) -> Path:
    key = url + "?" + json.dumps(params or {}, sort_keys=True)
    h = hashlib.sha1(key.encode()).hexdigest()
    return config.CACHE_DIR / urlparse(url).netloc / h


def get(url: str, *, params: dict | None = None, headers: dict | None = None,
        ttl: float = 6 * 3600, as_json: bool = False, retries: int = 3, timeout: float = 30):
    """GET with cache. ttl in seconds (0 disables cache read, -1 = forever)."""
    cp = _cache_path(url, params)
    if ttl != 0 and cp.exists():
        age = time.time() - cp.stat().st_mtime
        if ttl < 0 or age < ttl:
            text = cp.read_text(encoding="utf-8")
            return json.loads(text) if as_json else text

    host = urlparse(url).netloc
    hdrs = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
    hdrs.update(headers or {})
    err: Exception | None = None
    for attempt in range(retries):
        wait = MIN_INTERVAL - (time.time() - _last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_hit[host] = time.time()
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise FetchError(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code} for {r.url}")
            try:
                text = r.content.decode("utf-8-sig")
            except UnicodeDecodeError:  # some football-data CSVs are latin-1
                text = r.content.decode("latin-1")
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(text, encoding="utf-8")
            return json.loads(text) if as_json else text
        except (requests.RequestException, FetchError, UnicodeDecodeError) as e:
            err = e
            if isinstance(e, FetchError) and "for" in str(e):  # 4xx: don't retry
                break
            time.sleep(2 ** attempt)
    raise FetchError(f"{url}: {err}")
