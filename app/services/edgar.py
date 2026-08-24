"""SEC EDGAR client for the Company Scorer project.

Uses only `data.sec.gov`'s official public XBRL "company facts" API and
the SEC's published ticker->CIK mapping -- both are SEC's own APIs meant
for programmatic use (not scraping disallowed HTML), and require nothing
but an identifying User-Agent header per SEC's fair-access policy:
https://www.sec.gov/os/webmaster-faq#developers

XBRL concept tag names vary between filers (e.g. some use "Revenues",
others "RevenueFromContractWithCustomerExcludingAssessedTax"), so every
concept is looked up through a small alias list and the first tag that
resolves wins -- a standard, pragmatic way to handle non-uniform
taxonomies without hand-mapping every ticker individually.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import requests
from flask import current_app

from app.services.net_monitor import log_outbound

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# logical concept -> ordered list of us-gaap XBRL tags to try
CONCEPT_ALIASES: dict[str, list[str]] = {
    "Revenues": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "NetIncomeLoss": ["NetIncomeLoss", "ProfitLoss"],
    "Assets": ["Assets"],
    "Liabilities": ["Liabilities"],
    "StockholdersEquity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "AssetsCurrent": ["AssetsCurrent"],
    "LiabilitiesCurrent": ["LiabilitiesCurrent"],
    "OperatingIncomeLoss": ["OperatingIncomeLoss"],
    "InterestExpense": ["InterestExpense", "InterestExpenseDebt", "InterestAndDebtExpense"],
    "DepreciationDepletionAndAmortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
    ],
    "CostOfRevenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"],
}

_ticker_cik_cache: dict[str, int] | None = None
_company_facts_cache: dict[int, dict] = {}


class EdgarError(Exception):
    """Raised when a ticker can't be resolved or facts can't be fetched."""


def _headers() -> dict:
    ua = current_app.config["SEC_EDGAR_USER_AGENT"]
    return {"User-Agent": ua, "Accept": "application/json"}


def _get_json(url: str) -> dict:
    start = time.time()
    status = 200
    try:
        resp = requests.get(url, headers=_headers(), timeout=10)
        status = resp.status_code
        resp.raise_for_status()
        return resp.json()
    except EdgarError:
        raise
    except Exception as exc:  # noqa: BLE001 - network/SSL/JSON errors all become a clean EdgarError
        status = status if isinstance(status, int) and status != 200 else 599
        raise EdgarError(f"SEC EDGAR request failed (data temporarily unavailable): {exc}") from exc
    finally:
        log_outbound("edgar", "GET", url, status, (time.time() - start) * 1000)


def _load_ticker_cik_map() -> dict[str, int]:
    global _ticker_cik_cache
    if _ticker_cik_cache is None:
        data = _get_json(TICKERS_URL)
        _ticker_cik_cache = {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}
    return _ticker_cik_cache


def get_cik_for_ticker(ticker: str) -> int:
    mapping = _load_ticker_cik_map()
    cik = mapping.get(ticker.upper())
    if cik is None:
        raise EdgarError(f"No SEC CIK found for ticker '{ticker}'")
    return cik


def get_company_facts(cik: int, use_cache: bool = True) -> dict:
    if use_cache and cik in _company_facts_cache:
        return _company_facts_cache[cik]
    facts = _get_json(COMPANY_FACTS_URL.format(cik=cik))
    _company_facts_cache[cik] = facts
    return facts


def _annual_facts(facts_json: dict, concept_key: str) -> list[dict]:
    """Sorted (oldest->newest), deduped-by-fiscal-year annual (10-K) USD
    facts for a concept, trying each alias tag until one has data."""
    us_gaap = facts_json.get("facts", {}).get("us-gaap", {})
    for tag in CONCEPT_ALIASES[concept_key]:
        node = us_gaap.get(tag)
        if not node:
            continue
        units = node.get("units", {}).get("USD", [])
        annual = [u for u in units if str(u.get("form", "")).startswith("10-K") and u.get("fp") == "FY"]
        if not annual:
            annual = [u for u in units if str(u.get("form", "")).startswith("10-K")]
        if not annual:
            continue

        by_fy: dict[int, dict] = {}
        for u in annual:
            fy = u.get("fy")
            if fy not in by_fy or u.get("filed", "") > by_fy[fy].get("filed", ""):
                by_fy[fy] = u
        return sorted(by_fy.values(), key=lambda u: u["end"])
    return []


def get_fundamentals(ticker: str, as_of_date: date | None = None) -> dict:
    """For each tracked concept, returns the most recent annual figure
    filed on/before `as_of_date` (defaults to today) plus the prior
    year's figure (for YoY growth). Point-in-time filtering by *filed*
    date (not period-end) avoids look-ahead bias -- this is what makes
    the backtest module's "score as of 1 year ago" honest."""
    as_of_date = as_of_date or date.today()
    cik = get_cik_for_ticker(ticker)
    facts = get_company_facts(cik)

    result: dict[str, dict] = {}
    for key in CONCEPT_ALIASES:
        annual = [
            f
            for f in _annual_facts(facts, key)
            if datetime.strptime(f["filed"], "%Y-%m-%d").date() <= as_of_date
        ]
        if not annual:
            result[key] = {"current": None, "prior": None, "period_end": None}
            continue
        current = annual[-1]
        prior = annual[-2] if len(annual) >= 2 else None
        result[key] = {
            "current": current["val"],
            "prior": prior["val"] if prior else None,
            "period_end": current["end"],
        }
    return result
