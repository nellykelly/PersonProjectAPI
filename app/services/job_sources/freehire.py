"""freehire.me aggregator API (freehire.me/api/v1/agent/jobs/search) --
free, keyless JSON, ported from MadsLorentzen/ai-job-search's
freehire-search skill.

freehire normalizes postings from ~50 ATS platforms (Greenhouse, Lever,
Ashby, Workable, Manatal, ...) into one schema, so it reaches companies
that aren't on any of the Greenhouse/Lever/Ashby/Workable watchlists
without having to name them up front. Its "agent" search endpoint returns
each hit's *full* description (not the index's truncated preview), so one
call per keyword is enough for the scorer -- no per-hit detail fetch.

Like Adzuna it has real server-side keyword search, so `_keyword` is the
phrase actually searched for. Unlike Adzuna, geography is a structured
facet (country code), not free text: each call asks for
FREEHIRE_COUNTRIES and the configured locations are applied client-side
against each hit's location string (see _matches_location). The service
is a best-effort personal project with no SLA, so -- same as every
extra source here -- this never raises and a failed call just returns
nothing for that keyword.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests
from flask import current_app

from app.services.job_sources import is_excluded_title, matches_any_keyword
from app.services.net_monitor import log_outbound

_SEARCH_PATH = "/api/v1/agent/jobs/search"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _salary_range(enrichment: dict) -> str | None:
    lo, hi = enrichment.get("salary_min"), enrichment.get("salary_max")
    if not lo and not hi:
        return None
    currency = (enrichment.get("salary_currency") or "USD").upper()
    prefix = "$" if currency == "USD" else f"{currency} "
    if lo and hi and round(lo) != round(hi):
        return f"{prefix}{lo:,.0f} - {prefix}{hi:,.0f}"
    return f"{prefix}{(lo or hi):,.0f}"


def _city_tokens(locations: list[str]) -> list[str]:
    """"Houston, TX" -> "houston"; "San Francisco Bay Area, CA" ->
    "san francisco". freehire's location strings read like "Houston,
    Texas, United States", so the city name alone is the reliable part."""
    tokens = []
    for loc in locations:
        city = loc.split(",")[0].strip().lower()
        city = city.removesuffix(" bay area").strip()
        if city:
            tokens.append(city)
    return tokens


def _matches_location(raw: dict, city_tokens: list[str], include_remote: bool) -> bool:
    if include_remote and (raw.get("work_mode") or "").lower() == "remote":
        return True
    if not city_tokens:
        return True  # no location filter configured -- keep everything in-country
    location = (raw.get("location") or "").lower()
    return any(token in location for token in city_tokens)


def _normalize(raw: dict, phrase: str) -> dict | None:
    title = (raw.get("title") or "").strip()
    url = raw.get("url")
    if not title or not url or is_excluded_title(title):
        return None
    # freehire's full-text search also matches on description/skills, which
    # pulls in off-target titles (a Sales Engineer mentioning "AI engineer"
    # in its body); hold it to the same title match as the no-search sources.
    if not matches_any_keyword(title, [phrase]):
        return None
    location = (raw.get("location") or "").strip() or None
    if (raw.get("work_mode") or "").lower() == "remote" and "remote" not in (location or "").lower():
        location = f"Remote ({location})" if location else "Remote"
    return {
        "company_name": (raw.get("company") or "").strip() or "Unknown",
        "role_title": title,
        "location": location,
        "job_posting_url": url,
        "date_posted": _parse_date(raw.get("posted_at") or raw.get("created_at")),
        "salary_range": _salary_range(raw.get("enrichment") or {}),
        "description": (raw.get("description") or "").strip(),
        "source": "freehire",
        "_keyword": phrase,
    }


def _search_one(base_url: str, params: list[tuple[str, str]]) -> list[dict]:
    url = f"{base_url}{_SEARCH_PATH}"
    start = time.time()
    status = 200
    try:
        resp = requests.get(url, params=params, timeout=15)
        status = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception:  # noqa: BLE001 - a flaky extra source shouldn't fail the run
        status = status if isinstance(status, int) and status != 200 else 599
        log_outbound("freehire", "GET", url, status, (time.time() - start) * 1000)
        return []
    log_outbound("freehire", "GET", url, status, (time.time() - start) * 1000)
    return [r for r in (payload.get("data") or []) if isinstance(r, dict)]


def search(*, keywords: list[str], locations: list[str], include_remote: bool) -> list[dict]:
    """One HTTP call per keyword. Never raises: a failed call contributes
    nothing and the other keywords still run."""
    config = current_app.config
    base_url = (config.get("FREEHIRE_API_URL") or "https://freehire.me").rstrip("/")
    countries = [c.strip().upper() for c in (config.get("FREEHIRE_COUNTRIES") or "US").split(",") if c.strip()]
    city_tokens = _city_tokens(locations)

    seen_urls: set[str] = set()
    listings: list[dict] = []
    for phrase in keywords:
        phrase = phrase.strip()
        if not phrase:
            continue
        params: list[tuple[str, str]] = [
            ("q", phrase),
            ("limit", str(config.get("JOB_DISCOVERY_RESULTS_PER_PAGE", 20))),
            ("offset", "0"),
            ("semantic_ratio", "0"),
            ("include_description", "true"),
            ("description_format", "text"),
            ("posted_within_days", str(config.get("FREEHIRE_POSTED_WITHIN_DAYS", 30))),
        ]
        params += [("countries", c) for c in countries]
        for raw in _search_one(base_url, params):
            if not _matches_location(raw, city_tokens, include_remote):
                continue
            normalized = _normalize(raw, phrase)
            if normalized and normalized["job_posting_url"] not in seen_urls:
                seen_urls.add(normalized["job_posting_url"])
                listings.append(normalized)
    return listings
