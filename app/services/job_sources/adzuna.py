"""Adzuna job-search API client (https://developer.adzuna.com/).

Adzuna aggregates real postings across many boards and offers a free tier
(250 calls/day) with no scraping involved -- a legitimate API, unlike
LinkedIn/Indeed which either have no public search API or actively block
third-party scraping. See ADZUNA_APP_ID/ADZUNA_APP_KEY in config.py.

Adzuna's `what` parameter is one AND-ed search phrase, not a list, so a
multi-title search ("Software Engineer, Data Engineer") costs one request
per title -- results are merged and de-duplicated by URL here so
job_discovery.py always sees one flat, already-deduped list.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests
from flask import current_app

from app.services.job_sources import is_excluded_title
from app.services.net_monitor import log_outbound

_BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaError(Exception):
    """Raised when Adzuna isn't configured or a request fails."""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        # Adzuna's `created` is ISO-8601 with a trailing "Z".
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _salary_range(result: dict) -> str | None:
    lo, hi = result.get("salary_min"), result.get("salary_max")
    if not lo and not hi:
        return None
    if lo and hi and round(lo) != round(hi):
        return f"${lo:,.0f} - ${hi:,.0f}"
    return f"${(lo or hi):,.0f}"


def _normalize(result: dict) -> dict | None:
    url = result.get("redirect_url")
    title = result.get("title")
    company = (result.get("company") or {}).get("display_name")
    if not url or not title or not company:
        return None
    if is_excluded_title(title):
        return None  # internship/co-op/apprentice -- not what this search is for
    return {
        "company_name": company.strip(),
        "role_title": title.strip(),
        "location": (result.get("location") or {}).get("display_name"),
        "job_posting_url": url,
        "date_posted": _parse_date(result.get("created")),
        "salary_range": _salary_range(result),
        "description": (result.get("description") or "").strip(),
        "source": "Adzuna",
    }


def _search_one_page(
    *,
    app_id: str,
    app_key: str,
    country: str,
    page: int,
    what: str,
    where: str | None,
    results_per_page: int,
) -> list[dict]:
    url = _BASE_URL.format(country=country, page=page)
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": results_per_page,
        "what": what,
        "full_time": 1,  # full-time only -- Nelson isn't looking for part-time/contract work
        "content-type": "application/json",
    }
    if where:
        params["where"] = where

    start = time.time()
    status = 200
    try:
        resp = requests.get(url, params=params, timeout=10)
        status = resp.status_code
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - network/SSL/JSON errors all become a clean AdzunaError
        status = status if isinstance(status, int) and status != 200 else 599
        raise AdzunaError(f"Adzuna request failed (data temporarily unavailable): {exc}") from exc
    finally:
        log_outbound("adzuna", "GET", url, status, (time.time() - start) * 1000)

    return [r for r in (payload.get("results") or []) if isinstance(r, dict)]


def search(
    *,
    keywords: list[str],
    locations: list[str],
    include_remote: bool,
    max_pages: int | None = None,
    results_per_page: int | None = None,
    max_calls: int | None = None,
) -> tuple[list[dict], int, bool]:
    """Search Adzuna for every (keyword phrase x location) combination --
    each location in `locations` searched in parallel, plus one more
    nationwide "remote" sweep per keyword when `include_remote` is set --
    and return one flat, de-duplicated (by job_posting_url) list of
    normalized listings.

    Returns (listings, calls_made, truncated). `max_calls` hard-caps the
    number of Adzuna requests this call will make -- keywords x locations
    x pages grows fast, and Adzuna's free tier is 250 calls/day -- so once
    the cap is hit, the search stops early and `truncated=True` tells the
    caller to say so rather than silently returning a partial result.

    Raises AdzunaError if ADZUNA_APP_ID/ADZUNA_APP_KEY aren't configured or
    every request fails; a partial failure (one combination's request
    errors) is swallowed so the other combinations' results still come back.
    """
    config = current_app.config
    app_id = config.get("ADZUNA_APP_ID")
    app_key = config.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise AdzunaError("ADZUNA_APP_ID/ADZUNA_APP_KEY are not configured")

    country = config.get("ADZUNA_COUNTRY", "us")
    max_pages = max_pages or config.get("JOB_DISCOVERY_MAX_PAGES", 1)
    results_per_page = results_per_page or config.get("JOB_DISCOVERY_RESULTS_PER_PAGE", 20)
    max_calls = max_calls or config.get("JOB_DISCOVERY_MAX_ADZUNA_CALLS_PER_RUN", 45)

    wheres = [loc.strip() for loc in locations if loc.strip()]
    if include_remote:
        wheres.append("remote")
    if not wheres:
        wheres = [None]  # no location filter at all -- search everywhere

    seen_urls: set[str] = set()
    listings: list[dict] = []
    last_error: AdzunaError | None = None
    any_succeeded = False
    calls_made = 0
    truncated = False

    for phrase in keywords:
        phrase = phrase.strip()
        if not phrase:
            continue
        for where in wheres:
            for page in range(1, max_pages + 1):
                if calls_made >= max_calls:
                    truncated = True
                    return listings, calls_made, truncated

                try:
                    raw_results = _search_one_page(
                        app_id=app_id,
                        app_key=app_key,
                        country=country,
                        page=page,
                        what=phrase,
                        where=where,
                        results_per_page=results_per_page,
                    )
                    calls_made += 1
                    any_succeeded = True
                except AdzunaError as exc:
                    calls_made += 1
                    last_error = exc
                    break  # this combination's later pages would just fail the same way

                if not raw_results:
                    break  # short page = no more results for this combination

                for raw in raw_results:
                    normalized = _normalize(raw)
                    if normalized and normalized["job_posting_url"] not in seen_urls:
                        seen_urls.add(normalized["job_posting_url"])
                        # Internal only -- lets job_discovery.py round-robin
                        # the per-run cap across keywords instead of just
                        # taking the first N, which would always be
                        # whichever keyword happens to be searched first
                        # (see run_discovery's _round_robin_by_keyword).
                        # Popped before a listing is ever stored.
                        normalized["_keyword"] = phrase
                        # Adzuna's own location.display_name is often a
                        # hyper-local place a human wouldn't recognize
                        # ("Trammells, Harris County" for a Houston
                        # search) -- show the city/region actually
                        # searched for instead, which is what Nelson
                        # configured and will actually recognize.
                        normalized["location"] = "Remote" if where == "remote" else (where or normalized["location"])
                        listings.append(normalized)

    if not any_succeeded and last_error:
        raise last_error
    return listings, calls_made, truncated
