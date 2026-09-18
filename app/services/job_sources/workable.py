"""Workable public job board widget API (apply.workable.com) --
per-company, no key needed. Same shape and same reasoning as
greenhouse.py/lever.py/ashby.py: meant to be embedded in a company's own
careers page, so there's no ToS issue reading it, and there is no
cross-company search at all -- only "list this one company's open
postings" -- so this only ever covers companies on the watchlist
(JobSearchProfile.workable_boards, one Workable account slug per line,
e.g. "acme" for apply.workable.com/acme).

Direct-to-company by construction: `url`/`shortlink` is the company's own
apply.workable.com posting page, never a third-party aggregator -- see
job_discovery's module docstring for why that matters.

`?details=true` is required to get each job's actual description text;
without it the endpoint returns title/location/department only. Field
names per Workable's own documented widget schema (title, shortlink, url,
department, location.location_str, full_description) -- verified the
endpoint itself is live and correctly shaped against several real
accounts, though none had open postings at verification time to confirm
every field name against real data, so this is a slightly lower-confidence
integration than Greenhouse/Lever/Ashby; a genuinely wrong field name here
just means an empty/partial result for that source, never a crash (same
never-raises contract as the sibling modules).

Same client-side keyword filtering as the sibling per-company sources,
since this endpoint has no search of its own either.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://apply.workable.com/api/v1/widget/accounts/{account}"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def search(*, account_slugs: list[str], keywords: list[str]) -> list[dict]:
    """Fetch every watched account's open postings and keep only listings
    that match a configured search title and aren't an internship/co-op.
    One bad/renamed slug is skipped, not fatal to the others."""
    listings = []
    for slug in account_slugs:
        slug = slug.strip()
        if not slug:
            continue
        url = _URL.format(account=slug)
        start = time.time()
        status = 200
        try:
            resp = requests.get(url, params={"details": "true"}, timeout=10)
            status = resp.status_code
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa: BLE001 - one bad slug shouldn't kill the others
            status = status if isinstance(status, int) and status != 200 else 599
            log_outbound("workable", "GET", url, status, (time.time() - start) * 1000)
            continue
        else:
            log_outbound("workable", "GET", url, status, (time.time() - start) * 1000)

        company_label = (payload.get("name") or slug.replace("-", " ").title()).strip()
        for raw in payload.get("jobs") or []:
            title = (raw.get("title") or "").strip()
            job_url = raw.get("shortlink") or raw.get("url")
            if not title or not job_url or is_excluded_title(title):
                continue
            matched_keyword = matches_any_keyword(title, keywords)
            if not matched_keyword:
                continue
            location = ((raw.get("location") or {}).get("location_str") or "").strip() or None
            description = (raw.get("full_description") or raw.get("description") or "").strip()
            listings.append(
                {
                    "company_name": company_label,
                    "role_title": title,
                    "location": location,
                    "job_posting_url": job_url,
                    "date_posted": _parse_date(raw.get("published_on") or raw.get("created_at")),
                    "salary_range": None,  # not exposed by this endpoint
                    "description": description,
                    "source": "Workable",
                    "_keyword": matched_keyword,
                }
            )
    return listings
