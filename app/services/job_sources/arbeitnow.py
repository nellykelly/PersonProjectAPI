"""Arbeitnow job board API (arbeitnow.com/api/job-board-api) -- a free,
keyless aggregator, same idea as RemoteOK: one GET returns a page of
current listings, filtered client-side since the endpoint has no search
of its own. EU/remote tech-leaning, and mixes real onsite/hybrid European
postings in with remote ones (unlike RemoteOK, which is remote-only by
definition) -- this module only keeps the `remote: true` ones, since this
app's own location targeting (JOB_DISCOVERY's default Houston/SF Bay
Area) has no use for an onsite Munich listing, and Arbeitnow's API gives
no location-radius filtering to narrow that any other way.

Their own API response embeds a usage note ("please do not abuse...
appreciate linking back to the site") -- honored here by fetching one
page per run (250 results, already broad enough for client-side keyword
filtering) rather than paginating through everything, and by storing
Arbeitnow's own listing URL as job_posting_url, same as every other
source, so a promoted application always links back to the real source.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import requests

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://www.arbeitnow.com/api/job-board-api"


def _parse_date(epoch_seconds) -> date | None:
    if not epoch_seconds:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def search(*, keywords: list[str], include_remote: bool) -> list[dict]:
    """Remote-only by policy (see module docstring), so this returns
    nothing at all when include_remote is False -- same trigger RemoteOK
    already uses. Never raises: any request failure is swallowed and
    returns an empty list, since this is a best-effort extra source, not
    Adzuna's broadest-coverage primary one."""
    if not include_remote:
        return []

    start = time.time()
    status = 200
    try:
        resp = requests.get(_URL, timeout=10)
        status = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - a flaky extra source shouldn't fail the run
        status = status if isinstance(status, int) and status != 200 else 599
        log_outbound("arbeitnow", "GET", _URL, status, (time.time() - start) * 1000)
        return []
    else:
        log_outbound("arbeitnow", "GET", _URL, status, (time.time() - start) * 1000)

    listings = []
    for raw in payload.get("data") or []:
        if not raw.get("remote"):
            continue
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
                "location": "Remote",
                "job_posting_url": job_url,
                "date_posted": _parse_date(raw.get("created_at")),
                "salary_range": None,  # not exposed by this endpoint
                "description": (raw.get("description") or "").strip(),
                "source": "Arbeitnow",
                "_keyword": matched_keyword,
            }
        )
    return listings
