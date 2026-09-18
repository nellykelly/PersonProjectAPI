"""Ashby public job board API (api.ashbyhq.com) -- per-company, no key
needed. Same shape and same reasoning as greenhouse.py/lever.py: meant to
be embedded in a company's own careers page, so there's no ToS issue
reading it, and there is no cross-company search at all -- only "list
this one company's open postings" -- so this only ever covers companies
on the watchlist (JobSearchProfile.ashby_boards, one Ashby job-board slug
per line, e.g. "notion" for jobs.ashbyhq.com/notion). Increasingly common
at newer/YC-backed companies.

Direct-to-company by construction: `jobUrl` is the company's own
jobs.ashbyhq.com posting page, never a third-party aggregator that might
gate the application behind a signup -- see job_discovery's module
docstring for why that matters.

Same client-side keyword filtering as Greenhouse/Lever/RemoteOK, since
Ashby's API has no search of its own either -- it just lists everything
open at that one company.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://api.ashbyhq.com/posting-api/job-board/{company}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _company_label(slug: str) -> str:
    """Ashby's per-company posting-api endpoint doesn't return a display
    name -- the board itself IS the company -- so this derives a
    reasonable one from the slug, same approach as Greenhouse/Lever's.
    Cosmetic only; won't always be exact."""
    return slug.replace("-", " ").replace("_", " ").strip().title()


def search(*, company_slugs: list[str], keywords: list[str]) -> list[dict]:
    """Fetch every watched company's open postings and keep only listings
    that match a configured search title, aren't an internship/co-op, and
    are actually publicly listed. One bad/renamed slug is skipped, not
    fatal to the others."""
    listings = []
    for slug in company_slugs:
        slug = slug.strip()
        if not slug:
            continue
        url = _URL.format(company=slug)
        start = time.time()
        status = 200
        try:
            resp = requests.get(url, timeout=10)
            status = resp.status_code
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa: BLE001 - one bad slug shouldn't kill the others
            status = status if isinstance(status, int) and status != 200 else 599
            log_outbound("ashby", "GET", url, status, (time.time() - start) * 1000)
            continue
        else:
            log_outbound("ashby", "GET", url, status, (time.time() - start) * 1000)

        company_label = _company_label(slug)
        for raw in payload.get("jobs") or []:
            if raw.get("isListed") is False:
                continue
            title = (raw.get("title") or "").strip()
            job_url = raw.get("jobUrl")
            if not title or not job_url or is_excluded_title(title):
                continue
            matched_keyword = matches_any_keyword(title, keywords)
            if not matched_keyword:
                continue
            listings.append(
                {
                    "company_name": company_label,
                    "role_title": title,
                    "location": (raw.get("location") or "").strip() or None,
                    "job_posting_url": job_url,
                    "date_posted": _parse_date(raw.get("publishedAt")),
                    "salary_range": None,  # not exposed by this endpoint
                    "description": (raw.get("descriptionPlain") or "").strip(),
                    "source": "Ashby",
                    "_keyword": matched_keyword,
                }
            )
    return listings
