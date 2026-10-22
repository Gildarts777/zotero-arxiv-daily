"""Minimal Semantic Scholar client with retry/backoff and an on-disk cache.

Without an API key the shared pool often answers 429, so every call retries
with exponential backoff and successful responses are cached for `ttl_days`.
"""

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE = "https://api.semanticscholar.org"
PAPER_FIELDS = (
    "paperId,title,abstract,year,publicationDate,venue,publicationVenue,journal,"
    "externalIds,citationCount,influentialCitationCount,tldr,authors"
)
# recommendations and bulk search reject `tldr`
LIST_FIELDS = PAPER_FIELDS.replace(",tldr", "")


class S2Client:
    def __init__(self, api_key=None, cache_dir="cache/s2", ttl_days=7, max_retries=10):
        self.session = requests.Session()
        if api_key:
            self.session.headers["x-api-key"] = api_key
        self.min_interval = 1.05 if api_key else 1.5
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_days * 86400
        self.max_retries = max_retries
        self._last_call = 0.0

    # -- low level ---------------------------------------------------------
    def _cache_path(self, method, url, params, body):
        key = json.dumps([method, url, params, body], sort_keys=True)
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _request(self, method, path, params=None, body=None):
        url = BASE + path
        cp = self._cache_path(method, url, params, body)
        if cp.exists() and time.time() - cp.stat().st_mtime < self.ttl:
            return json.loads(cp.read_text(encoding="utf-8"))

        for attempt in range(self.max_retries):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                r = self.session.request(method, url, params=params, json=body, timeout=90)
            except requests.RequestException as e:
                log.warning("S2 network error (%s), retrying", type(e).__name__)
                time.sleep(min(60, 2 ** attempt))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                delay = min(60, 2 * (attempt + 1))
                # no paper ids in the message: GitHub logs are public
                log.info("S2 %s on %s, retry in %ss", r.status_code, re.sub(r"[0-9a-f]{40}", "<id>", path), delay)
                time.sleep(delay)
                continue
            if r.status_code == 404:
                return None
            if not r.ok:
                raise RuntimeError(f"S2 {r.status_code} on {re.sub(r'[0-9a-f]{40}', '<id>', path)}")
            data = r.json()
            cp.write_text(json.dumps(data), encoding="utf-8")
            return data
        raise RuntimeError(f"Semantic Scholar unavailable after {self.max_retries} retries")

    # -- endpoints ---------------------------------------------------------
    def batch(self, ids, fields=PAPER_FIELDS, chunk=400):
        """Look up papers by S2 id / 'arXiv:..' / 'DOI:..'. Returns list aligned with ids (None if missing)."""
        out = []
        for i in range(0, len(ids), chunk):
            part = ids[i:i + chunk]
            res = self._request("POST", "/graph/v1/paper/batch", {"fields": fields}, {"ids": part})
            out.extend(res or [None] * len(part))
        return out

    def match_title(self, title, fields=PAPER_FIELDS):
        res = self._request("GET", "/graph/v1/paper/search/match", {"query": title, "fields": fields})
        if res and res.get("data"):
            return res["data"][0]
        return None

    def citations(self, paper_id, limit=500, fields=LIST_FIELDS):
        """Papers citing `paper_id` (first `limit`, as returned by S2)."""
        out, offset = [], 0
        while offset < limit:
            page = min(1000, limit - offset)
            res = self._request("GET", f"/graph/v1/paper/{paper_id}/citations",
                                {"fields": fields, "limit": page, "offset": offset}) or {}
            out.extend(d["citingPaper"] for d in res.get("data", []) if d.get("citingPaper"))
            if res.get("next") is None:
                break
            offset = res["next"]
        return out

    def recommendations(self, positive_ids, negative_ids=(), limit=500, fields=LIST_FIELDS):
        body = {"positivePaperIds": list(positive_ids), "negativePaperIds": list(negative_ids)}
        res = self._request("POST", "/recommendations/v1/papers", {"fields": fields, "limit": limit}, body)
        return (res or {}).get("recommendedPapers", [])

    def bulk_search(self, query, year_from=None, min_citations=None, max_results=1000, fields=LIST_FIELDS):
        params = {"query": query, "fields": fields, "sort": "citationCount:desc",
                  "fieldsOfStudy": "Computer Science"}
        if year_from:
            params["year"] = f"{year_from}-"
        if min_citations:
            params["minCitationCount"] = str(min_citations)
        papers, token = [], None
        while len(papers) < max_results:
            if token:
                params["token"] = token
            res = self._request("GET", "/graph/v1/paper/search/bulk", dict(params)) or {}
            papers.extend(res.get("data", []))
            token = res.get("token")
            if not token:
                break
        return papers[:max_results]


def api_key_from_env():
    return os.environ.get("S2_API_KEY") or None
