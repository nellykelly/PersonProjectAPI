"""Remotive remote-jobs API (remotive.com/api/remote-jobs) -- free,
keyless, same "fetch a page, filter client-side" shape as RemoteOK.

Unlike every other source here, Remotive's own API response embeds an
explicit usage limit, not just a "please be nice" note: "we advise max.
4 times a day... excessive requests will be blocked." That's a real,
enforced-by-them ceiling, not a suggestion -- job_discovery.py tracks and
pre-flight-checks today's Remotive call count against
REMOTIVE_DAILY_CALL_LIMIT (default 4) before ever calling search() here,
the same way it already pre-flight-checks Adzuna's daily quota. This
module itself makes exactly one HTTP call per invocation and never
retries, so the caller's count is always accurate.

Remote-only by definition (it's a remote-jobs board), so -- same trigger
as RemoteOK/Arbeitnow -- this only ever gets called when include_remote
is set.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://remotive.com/api/remote-jobs"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def search(*, keywords: list[str]) -> list[dict]:
    """Makes exactly one HTTP call. Callers are responsible for checking
    today's call budget BEFORE calling this (see job_discovery.py) --
    this function has no state of its own to enforce that with. Never
    raises: any request failure is swallowed and returns an empty list."""
    start = time.time()
    status = 200
    try:
        resp = requests.get(_URL, timeout=10)
        status = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - a flaky extra source shouldn't fail the run
        status = status if isinstance(status, int) and status != 200 else 599
        log_outbound("remotive", "GET", _URL, status, (time.time() - start) * 1000)
        return []
    else:
        log_outbound("remotive", "GET", _URL, status, (time.time() - start) * 1000)

    listings = []
    for raw in payload.get("jobs") or []:
        title = (raw.get("title") or "").strip()
        job_url = raw.get("url")
        if not title or not job_url or is_excluded_title(title):
            continue
        matched_keyword = matches_any_keyword(title, keywords)
        if not matched_keyword:
            continue
        listings.append(
            {
                "company_name": (raw.get("company_name") or "").strip() or "Unknown",
                "role_title": title,
                "location": (raw.get("candidate_required_location") or "Remote").strip() or "Remote",
                "job_posting_url": job_url,
                "date_posted": _parse_date(raw.get("publication_date")),
                "salary_range": (raw.get("salary") or "").strip() or None,
                "description": (raw.get("description") or "").strip(),
                "source": "Remotive",
                "_keyword": matched_keyword,
            }
        )
    return listings
