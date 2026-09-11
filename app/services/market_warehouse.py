"""Read-only view over the market-data-warehouse project's star schema.

market-data-warehouse/ is a sibling project, not part of this Flask app
(see its own README) -- a yfinance -> dbt -> DuckDB/MotherDuck/Snowflake
dimensional warehouse. This module opens the warehouse **read-only** and
best-effort: a local file may not exist yet (warehouse never built) or be
mid-refresh (a concurrent `dbt run` holds a write lock DuckDB won't
share); `duckdb` may not be installed; a MotherDuck path needs a token.
None of those are errors worth a 500 -- the page just says so and shows
nothing instead.

`MARKET_WAREHOUSE_DB_PATH` (app/config.py) is either a local filesystem
path (the default -- zero-config for a fresh checkout) or a MotherDuck
path (`md:<database>`), detected by the `md:` prefix -- same convention
`ingest/load.py` in the sibling project uses. Same DuckDB client either
way; only the connection target differs.

`duckdb` is imported lazily here, the same lazy-optional pattern as
app/services/assistant's groq/fastembed: the app still boots and every
other page still works without it installed.

NOT FINANCIAL ADVICE: `fct_security_price_projection` (surfaced here as
"projection") offers three real forecasting methods (linear trend,
random-walk-with-drift, mean reversion), each with a volatility-derived
uncertainty band -- none has any demonstrated predictive power. See that
model's docstring in market-data-warehouse/warehouse/models/ for what
each method actually assumes.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

log = logging.getLogger(__name__)

DEFAULT_CHART_LOOKBACK_DAYS = 730  # ~2 years -- the page's initial view
RECENT_ACTUAL_LOOKBACK_DAYS = 120  # visual lead-in before the projection chart's dashed segment

# Must match warehouse/dbt_project.yml's vars -- these are display-layer
# copies of what's actually precomputed, not a second source of truth
# for the math (the SQL is the only place the methods are computed).
PROJECTION_METHODS = ("linear_trend", "random_walk_drift", "mean_reversion")
PROJECTION_LOOKBACK_GRID = (30, 60, 90, 180)
DEFAULT_PROJECTION_METHOD = "linear_trend"
DEFAULT_PROJECTION_LOOKBACK = 90


def _empty(reason: str) -> dict:
    return {
        "available": False,
        "reason": reason,
        "stats": None,
        "securities": [],
        "chart": {"labels": [], "series": []},
        "projection": {"tickers": [], "series": {}},
        "recent_actual": {},
        "projection_options": {
            "methods": list(PROJECTION_METHODS),
            "lookbacks": list(PROJECTION_LOOKBACK_GRID),
            "default_method": DEFAULT_PROJECTION_METHOD,
            "default_lookback": DEFAULT_PROJECTION_LOOKBACK,
        },
    }


def _open(db_path: str):
    """Returns (connection, None) or (None, reason). Never raises."""
    try:
        import duckdb
    except ImportError:
        return None, "The duckdb package isn't installed."

    is_motherduck = db_path.startswith("md:")

    if is_motherduck:
        # Without a token, duckdb's `md:` connect falls back to an
        # interactive browser OAuth prompt -- fine by hand, fatal in a
        # web request. Required explicitly so this fails fast instead.
        if not os.environ.get("MOTHERDUCK_TOKEN"):
            return None, "MARKET_WAREHOUSE_DB_PATH points at MotherDuck but MOTHERDUCK_TOKEN isn't set."
    elif not os.path.exists(db_path):
        return None, "The warehouse hasn't been built yet -- run `python run.py all` in market-data-warehouse/."

    try:
        con = duckdb.connect(db_path, read_only=True)
    except Exception as exc:  # noqa: BLE001 - lock contention, bad token, corruption, etc.
        log.warning("market_warehouse: could not open %s: %s", db_path, exc)
        reason = (
            "Could not reach MotherDuck -- check MOTHERDUCK_TOKEN."
            if is_motherduck
            else "The warehouse is being refreshed right now -- try again shortly."
        )
        return None, reason
    return con, None


def get_analytics(db_path: str) -> dict:
    """Everything the dashboard page needs, in one dict. Never raises --
    any failure (missing file, missing token, lock contention, a schema
    that isn't built yet) comes back as `{"available": False, "reason": ...}`."""
    con, reason = _open(db_path)
    if con is None:
        return _empty(reason)

    try:
        return _query_all(con)
    except Exception as exc:  # noqa: BLE001 - e.g. marts schema not built yet
        log.warning("market_warehouse: query failed against %s: %s", db_path, exc)
        return _empty("The warehouse exists but isn't fully built yet.")
    finally:
        con.close()


def get_chart_window(db_path: str, *, start: str | None = None, end: str | None = None) -> dict:
    """The relative-performance chart for an explicit [start, end] window
    (ISO date strings), used by the timeframe picker's fetch() calls. Same
    never-raises contract as get_analytics -- returns an empty-but-valid
    shape on any failure, since this backs a background chart refresh, not
    the initial page render."""
    con, _reason = _open(db_path)
    if con is None:
        return {"labels": [], "series": []}
    try:
        start_date = datetime.fromisoformat(start).date() if start else None
        end_date = datetime.fromisoformat(end).date() if end else None
        return _fetch_relative_performance(con, start=start_date, end=end_date)
    except Exception as exc:  # noqa: BLE001
        log.warning("market_warehouse: chart window query failed: %s", exc)
        return {"labels": [], "series": []}
    finally:
        con.close()


def get_projection(db_path: str, *, method: str, lookback_days: int) -> dict:
    """The projection chart for an explicit (method, lookback_days) pair,
    used by the method/timeframe pickers' fetch() calls. NOT FINANCIAL
    ADVICE -- see module docstring. Same never-raises contract as
    get_chart_window: empty-but-valid shape on any failure, since this
    backs a background refresh, not the initial page render. Rejects an
    unrecognized method/lookback rather than silently returning nothing
    from a typo'd combination that was never precomputed."""
    empty = {"tickers": [], "series": {}}
    if method not in PROJECTION_METHODS or lookback_days not in PROJECTION_LOOKBACK_GRID:
        return empty
    con, _reason = _open(db_path)
    if con is None:
        return empty
    try:
        return _fetch_projection(con, method=method, lookback_days=lookback_days)
    except Exception as exc:  # noqa: BLE001
        log.warning("market_warehouse: projection query failed: %s", exc)
        return empty
    finally:
        con.close()


def _query_all(con) -> dict:
    stats_row = con.execute(
        """
        select
            (select count(*) from marts.dim_security)                                as security_count,
            (select count(*) from marts.dim_exchange where exchange_code != 'UNKNOWN') as exchange_count,
            (select min(trade_date) from marts.fct_security_price)                    as min_date,
            (select max(trade_date) from marts.fct_security_price)                    as max_date,
            (select count(*) from marts.fct_security_price)                           as total_price_rows,
            (select count(*) from marts.fct_corporate_action
                where action_type = 'dividend')                                       as total_dividends,
            (select count(*) from marts.fct_corporate_action
                where action_type = 'split')                                          as total_splits
        """
    ).fetchone()
    columns = [d[0] for d in con.description]
    stats = dict(zip(columns, stats_row))

    if not stats["security_count"]:
        return _empty("The warehouse is built but has no securities loaded yet.")

    max_date = stats["max_date"]
    start = max_date - timedelta(days=DEFAULT_CHART_LOOKBACK_DAYS) if isinstance(max_date, date) else None

    securities = _fetch_latest_snapshot(con)
    chart = _fetch_relative_performance(con, start=start, end=max_date)
    projection = _fetch_projection(
        con, method=DEFAULT_PROJECTION_METHOD, lookback_days=DEFAULT_PROJECTION_LOOKBACK
    )
    recent_actual = _fetch_recent_actual_prices(con, max_date)

    return {
        "available": True,
        "reason": None,
        "stats": stats,
        "securities": securities,
        "chart": chart,
        "projection": projection,
        "recent_actual": recent_actual,
        "projection_options": {
            "methods": list(PROJECTION_METHODS),
            "lookbacks": list(PROJECTION_LOOKBACK_GRID),
            "default_method": DEFAULT_PROJECTION_METHOD,
            "default_lookback": DEFAULT_PROJECTION_LOOKBACK,
        },
    }


def _fetch_latest_snapshot(con) -> list[dict]:
    """One row per security: the latest price plus that same day's
    technical and risk metrics (fct_security_technical_daily /
    fct_security_risk_daily share fct_security_price's grain, so this is
    a same-grain left join, not a fan-out)."""
    rows = con.execute(
        """
        with latest as (
            select security_key, max(trade_date) as trade_date
            from marts.fct_security_price
            group by security_key
        )
        select
            s.ticker,
            s.security_name,
            s.sector,
            x.exchange_name,
            p.trade_date,
            p.close,
            round(p.daily_return * 100, 2) as daily_return_pct,
            p.volume,
            t.sma_20,
            t.sma_50,
            t.sma_200,
            t.rsi_14_simplified,
            t.high_52w,
            t.low_52w,
            t.pct_off_52w_high,
            round(r.volatility_252d * 100, 2) as volatility_252d_pct,
            r.sharpe_ratio_252d,
            r.sortino_ratio_252d,
            round(r.max_drawdown_252d * 100, 2) as max_drawdown_252d_pct,
            r.beta_252d
        from latest l
        join marts.fct_security_price p
            on p.security_key = l.security_key and p.trade_date = l.trade_date
        join marts.dim_security s on s.security_key = l.security_key
        join marts.dim_exchange x on x.exchange_key = p.exchange_key
        left join marts.fct_security_technical_daily t
            on t.security_key = l.security_key and t.date_key = p.date_key
        left join marts.fct_security_risk_daily r
            on r.security_key = l.security_key and r.date_key = p.date_key
        order by s.ticker
        """
    ).fetchall()
    cols = [d[0] for d in con.description]
    return [dict(zip(cols, row)) for row in rows]


def _fetch_relative_performance(con, *, start=None, end=None) -> dict:
    """Adjusted close for every security over [start, end], indexed to 100
    at each series' first available day in the window so tickers at very
    different price levels are comparable on one chart."""
    if start is None and end is None:
        return {"labels": [], "series": []}

    rows = con.execute(
        """
        select s.ticker, p.trade_date, p.adj_close
        from marts.fct_security_price p
        join marts.dim_security s on s.security_key = p.security_key
        where (? is null or p.trade_date >= ?)
          and (? is null or p.trade_date <= ?)
        order by s.ticker, p.trade_date
        """,
        [start, start, end, end],
    ).fetchall()

    by_ticker: dict[str, list[tuple]] = {}
    all_dates: set = set()
    for ticker, trade_date, adj_close in rows:
        by_ticker.setdefault(ticker, []).append((trade_date, adj_close))
        all_dates.add(trade_date)

    labels = sorted(all_dates)
    label_index = {d: i for i, d in enumerate(labels)}

    series = []
    for ticker, points in sorted(by_ticker.items()):
        values: list[float | None] = [None] * len(labels)
        for trade_date, adj_close in points:
            if adj_close is not None:
                values[label_index[trade_date]] = float(adj_close)
        base = next((v for v in values if v is not None), None)
        if base:
            values = [round(v / base * 100, 2) if v is not None else None for v in values]
        series.append({"ticker": ticker, "data": values})

    return {"labels": [d.isoformat() for d in labels], "series": series}


def _fetch_recent_actual_prices(con, max_date) -> dict:
    """Raw (not indexed-to-100) adjusted close per ticker over a short
    recent window -- the visual lead-in the projection chart draws as a
    solid line before its dashed, banded future segment. Deliberately
    raw price units, matching fct_security_price_projection's units;
    the relative-performance chart's indexed values would be meaningless
    plotted next to an actual price level."""
    if not isinstance(max_date, date):
        return {}
    start = max_date - timedelta(days=RECENT_ACTUAL_LOOKBACK_DAYS)
    rows = con.execute(
        """
        select ticker, trade_date, adj_close
        from marts.fct_security_price
        where trade_date >= ? and adj_close is not null
        order by ticker, trade_date
        """,
        [start],
    ).fetchall()

    by_ticker: dict[str, dict] = {}
    for ticker, trade_date, adj_close in rows:
        entry = by_ticker.setdefault(ticker, {"dates": [], "close": []})
        entry["dates"].append(trade_date.isoformat())
        entry["close"].append(float(adj_close))
    return by_ticker


def _fetch_projection(con, *, method: str, lookback_days: int) -> dict:
    """NOT FINANCIAL ADVICE -- see module docstring. All tickers' rows for
    one (method, lookback_days) combination -- small enough to embed all
    tickers for that combination at once; switching ticker is a
    client-side toggle, switching method/lookback is a fresh fetch (see
    get_projection), since that's a different set of precomputed rows,
    not a filter over ones already on the page."""
    rows = con.execute(
        """
        select ticker, projection_date, projected_close, lower_bound_95,
               upper_bound_95, fit_quality
        from marts.fct_security_price_projection
        where method = ? and lookback_days = ?
        order by ticker, projection_date
        """,
        [method, lookback_days],
    ).fetchall()

    by_ticker: dict[str, dict] = {}
    for ticker, proj_date, close, lower, upper, r2 in rows:
        entry = by_ticker.setdefault(
            ticker, {"dates": [], "projected": [], "lower": [], "upper": [], "r_squared": r2}
        )
        entry["dates"].append(proj_date.isoformat())
        entry["projected"].append(close)
        entry["lower"].append(lower)
        entry["upper"].append(upper)

    return {"tickers": sorted(by_ticker), "series": by_ticker}
