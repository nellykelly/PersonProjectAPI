"""RemoteOK (https://remoteok.com/api) -- free, keyless, remote-only job feed.

Legitimate, documented public API, not scraping: one GET returns the
board's current listings as JSON, no auth needed beyond an identifying
User-Agent (RemoteOK blocks the default python-requests one).

No server-side keyword search -- the API just hands back everything on
the board, so `job_sources.matches_any_keyword()` does the filtering
here, client-side, against whatever titles are configured. Remote-only
by definition, so this is skipped entirely unless include_remote is set;
there's no separate on/off setting for it beyond that.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests
from flask import current_app

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://remoteok.com/api"
_HEADERS = {"User-Agent": "PersonProjectAPI-portfolio-site job-discovery (koskela.nelson@gmail.com)"}


class RemoteOKError(Exception):
    """Raised only if the feed request itself fails outright."""


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value)).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _salary_range(raw: dict) -> str | None:
    lo, hi = raw.get("salary_min"), raw.get("salary_max")
    if not lo and not hi:
        return None
    try:
        lo, hi = float(lo or 0), float(hi or 0)
    except (TypeError, ValueError):
        return None
    if lo and hi and round(lo) != round(hi):
        return f"${lo:,.0f} - ${hi:,.0f}"
    return f"${(lo or hi):,.0f}"


def search(*, keywords: list[str], include_remote: bool) -> list[dict]:
    """Fetch RemoteOK's current feed and keep only listings that match a
    configured search title and aren't an internship/co-op. Returns []
    (never raises) on a fetch failure -- a best-effort extra source
    shouldn't fail the whole run the way Adzuna being unreachable does."""
    if not include_remote:
        return []

    start = time.time()
    status = 200
    try:
        resp = requests.get(_URL, headers=_HEADERS, timeout=10)
        status = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - best-effort source, see docstring
        status = status if isinstance(status, int) and status != 200 else 599
        return []
    finally:
        log_outbound("remoteok", "GET", _URL, status, (time.time() - start) * 1000)

    listings = []
    for raw in payload:
        # RemoteOK's first array element is its own legal/attribution
        # notice, not a job -- it has no "position" key, unlike every
        # real listing.
        if not isinstance(raw, dict) or "position" not in raw:
            continue
        title = (raw.get("position") or "").strip()
        url = raw.get("url")
        company = (raw.get("company") or "").strip()
        if not title or not url or not company or is_excluded_title(title):
            continue
        matched_keyword = matches_any_keyword(title, keywords)
        if not matched_keyword:
            continue
        listings.append(
            {
                "company_name": company,
                "role_title": title,
                "location": "Remote",
                "job_posting_url": url,
                "date_posted": _parse_date(raw.get("date")),
                "salary_range": _salary_range(raw),
                "description": (raw.get("description") or "").strip(),
                "source": "RemoteOK",
                "_keyword": matched_keyword,
            }
        )
    return listings
