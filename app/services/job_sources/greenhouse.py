"""Greenhouse public job board API (boards-api.greenhouse.io) -- per-company,
no key needed. These endpoints are meant to be embedded in a company's own
careers page, so there's no ToS issue reading them; unlike Adzuna/RemoteOK
there is no cross-company search at all, only "list this one company's
open jobs" -- so this only ever covers companies Nelson explicitly adds
to his watchlist (JobSearchProfile.company_boards, one Greenhouse board
slug per line, e.g. "stripe" for boards.greenhouse.io/stripe).

Same client-side keyword filtering as RemoteOK, since Greenhouse's API
has no search of its own either -- it just lists everything open at that
one company.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime

import requests

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_URL = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _strip_html(html: str) -> str:
    """Good-enough plain text for the LLM scorer's context -- not a real
    HTML parser, just enough to keep tags out of the description."""
    return _TAG_RE.sub(" ", html or "").strip()


def _company_label(slug: str) -> str:
    """Greenhouse's per-board /jobs endpoint doesn't return a display
    name -- the board itself IS the company -- so this derives a
    reasonable one from the slug. Cosmetic only; won't always be exact
    (e.g. "gitlab-inc" -> "Gitlab Inc")."""
    return slug.replace("-", " ").replace("_", " ").strip().title()


def search(*, board_slugs: list[str], keywords: list[str]) -> list[dict]:
    """Fetch every watched company's open jobs and keep only listings
    that match a configured search title and aren't an internship/co-op.
    One bad/renamed slug is skipped, not fatal to the others."""
    listings = []
    for slug in board_slugs:
        slug = slug.strip()
        if not slug:
            continue
        url = _URL.format(board=slug)
        start = time.time()
        status = 200
        try:
            resp = requests.get(url, params={"content": "true"}, timeout=10)
            status = resp.status_code
            resp.raise_for_status()
            payload = resp.json()
        except Exception:  # noqa: BLE001 - one bad slug shouldn't kill the others
            status = status if isinstance(status, int) and status != 200 else 599
            log_outbound("greenhouse", "GET", url, status, (time.time() - start) * 1000)
            continue
        else:
            log_outbound("greenhouse", "GET", url, status, (time.time() - start) * 1000)

        company_label = _company_label(slug)
        for raw in payload.get("jobs") or []:
            title = (raw.get("title") or "").strip()
            job_url = raw.get("absolute_url")
            if not title or not job_url or is_excluded_title(title):
                continue
            matched_keyword = matches_any_keyword(title, keywords)
            if not matched_keyword:
                continue
            location = ((raw.get("location") or {}).get("name") or "").strip() or None
            listings.append(
                {
                    "company_name": company_label,
                    "role_title": title,
                    "location": location,
                    "job_posting_url": job_url,
                    "date_posted": _parse_date(raw.get("updated_at")),
                    "salary_range": None,  # not exposed by this endpoint
                    "description": _strip_html(raw.get("content") or ""),
                    "source": "Greenhouse",
                    "_keyword": matched_keyword,
                }
            )
    return listings
