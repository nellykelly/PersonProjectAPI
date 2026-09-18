"""Lever public job board API (api.lever.co) -- per-company, no key needed.
Same shape and same reasoning as greenhouse.py: these endpoints are meant
to be embedded in a company's own careers page, so there's no ToS issue
reading them, and there is no cross-company search at all -- only "list
this one company's open postings" -- so this only ever covers companies
on the watchlist (JobSearchProfile.lever_boards, one Lever company slug
per line, e.g. "palantir" for jobs.lever.co/palantir).

Direct-to-company by construction: `hostedUrl` is the company's own
jobs.lever.co application page, never a third-party aggregator that
might gate the actual application behind a signup -- see job_discovery's
module docstring for why that matters.

Same client-side keyword filtering as Greenhouse/RemoteOK, since Lever's
API has no search of its own either -- it just lists everything open at
that one company.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone

import requests

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://api.lever.co/v0/postings/{company}"


def _parse_date(epoch_millis) -> date | None:
    if not epoch_millis:
        return None
    try:
        return datetime.fromtimestamp(int(epoch_millis) / 1000, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError):
        return None


def _company_label(slug: str) -> str:
    """Lever's per-company postings endpoint doesn't return a display
    name -- the board itself IS the company -- so this derives a
    reasonable one from the slug, same approach as Greenhouse's. Cosmetic
    only; won't always be exact."""
    return slug.replace("-", " ").replace("_", " ").strip().title()


def search(*, company_slugs: list[str], keywords: list[str]) -> list[dict]:
    """Fetch every watched company's open postings and keep only listings
    that match a configured search title and aren't an internship/co-op.
    One bad/renamed slug is skipped, not fatal to the others."""
    listings = []
    for slug in company_slugs:
        slug = slug.strip()
        if not slug:
            continue
        url = _URL.format(company=slug)
        start = time.time()
        status = 200
        try:
            resp = requests.get(url, params={"mode": "json"}, timeout=10)
            status = resp.status_code
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa: BLE001 - one bad slug shouldn't kill the others
            status = status if isinstance(status, int) and status != 200 else 599
            log_outbound("lever", "GET", url, status, (time.time() - start) * 1000)
            continue
        else:
            log_outbound("lever", "GET", url, status, (time.time() - start) * 1000)

        company_label = _company_label(slug)
        for raw in payload if isinstance(payload, list) else []:
            title = (raw.get("text") or "").strip()
            job_url = raw.get("hostedUrl")
            if not title or not job_url or is_excluded_title(title):
                continue
            matched_keyword = matches_any_keyword(title, keywords)
            if not matched_keyword:
                continue
            location = ((raw.get("categories") or {}).get("location") or "").strip() or None
            listings.append(
                {
                    "company_name": company_label,
                    "role_title": title,
                    "location": location,
                    "job_posting_url": job_url,
                    "date_posted": _parse_date(raw.get("createdAt")),
                    "salary_range": None,  # not exposed by this endpoint
                    "description": (raw.get("descriptionPlain") or "").strip(),
                    "source": "Lever",
                    "_keyword": matched_keyword,
                }
            )
    return listings
