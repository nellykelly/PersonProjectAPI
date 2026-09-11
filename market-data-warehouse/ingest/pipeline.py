"""Glue: pull every feed, land every feed."""

from __future__ import annotations

import logging

from ingest.config import FEEDS, Settings, get_settings, load_tickers
from ingest.load import make_loader
from ingest.pull_yfinance import pull_all

log = logging.getLogger(__name__)

# feed name -> RAW table name
RAW_TABLES = {
    "price_history": "price_history",
    "dividends": "dividends",
    "splits": "splits",
    "security_info": "security_info",
}


def ingest(
    tickers: list[str] | None = None, settings: Settings | None = None
) -> dict[str, int]:
    settings = settings or get_settings()
    tickers = tickers or load_tickers()
    log.info("ingesting %d tickers into %s", len(tickers), settings.target)

    frames = pull_all(tickers)
    written: dict[str, int] = {}
    with make_loader(settings) as loader:
        for feed in FEEDS:
            n = loader.load(RAW_TABLES[feed], frames.get(feed))
            written[feed] = n
            log.info("landed %s: %d rows", feed, n)
    return written
