"""Tests for /projects/market-warehouse and app.services.market_warehouse.

A tiny hand-built DuckDB file stands in for a real market-data-warehouse
build -- fast, hermetic, and matches exactly the columns the service
queries against (marts.dim_security / dim_exchange / fct_security_price /
fct_corporate_action), rather than depending on the sibling project's
own pipeline having been run.
"""

from __future__ import annotations

import datetime as dt

import pytest

duckdb = pytest.importorskip("duckdb")


# --------------------------------------------------------------------------
# app.services.market_warehouse.get_analytics
# --------------------------------------------------------------------------
def test_get_analytics_missing_file_is_graceful(tmp_path):
    from app.services import market_warehouse

    result = market_warehouse.get_analytics(str(tmp_path / "nope.duckdb"))
    assert result["available"] is False
    assert "run.py all" in result["reason"]


def test_get_analytics_motherduck_without_token_fails_fast_not_graceful_hang(monkeypatch):
    """Without MOTHERDUCK_TOKEN, a bare duckdb.connect("md:...") falls back
    to an interactive browser login -- fatal in a web request. Must be
    caught before ever calling duckdb.connect, not after."""
    from app.services import market_warehouse

    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)
    import duckdb as duckdb_module

    def _must_not_be_called(*_a, **_kw):
        raise AssertionError("get_analytics must not call duckdb.connect without a token")

    monkeypatch.setattr(duckdb_module, "connect", _must_not_be_called)

    result = market_warehouse.get_analytics("md:market")
    assert result["available"] is False
    assert "MOTHERDUCK_TOKEN" in result["reason"]


def test_get_analytics_motherduck_path_skips_local_file_check(monkeypatch):
    """A "md:" path must never be treated as a missing local file just
    because os.path.exists("md:market") is (correctly) False."""
    from app.services import market_warehouse

    monkeypatch.setenv("MOTHERDUCK_TOKEN", "fake-token-for-test")
    import duckdb as duckdb_module

    def fake_connect(path, read_only=False):
        assert path == "md:market"
        assert read_only is True
        raise RuntimeError("simulated: never actually reaches the network in this test")

    monkeypatch.setattr(duckdb_module, "connect", fake_connect)

    result = market_warehouse.get_analytics("md:market")
    assert result["available"] is False
    assert "MotherDuck" in result["reason"]  # the connection-failure message, not "hasn't been built yet"


def test_get_analytics_happy_path(warehouse_db):
    from app.services import market_warehouse

    result = market_warehouse.get_analytics(warehouse_db)
    assert result["available"] is True

    stats = result["stats"]
    assert stats["security_count"] == 2
    assert stats["total_price_rows"] == 4
    assert stats["total_dividends"] == 1
    assert stats["total_splits"] == 1

    by_ticker = {row["ticker"]: row for row in result["securities"]}
    assert by_ticker.keys() == {"AAPL", "KO"}
    assert by_ticker["AAPL"]["trade_date"] == dt.date(2026, 1, 5)
    assert by_ticker["AAPL"]["close"] == 110.0
    assert by_ticker["AAPL"]["daily_return_pct"] == 10.0
    assert by_ticker["AAPL"]["exchange_name"] == "Nasdaq Stock Market"
    # technical + risk, joined at the same (security_key, date_key) grain
    assert by_ticker["AAPL"]["sma_20"] == 105.0
    assert by_ticker["AAPL"]["rsi_14_simplified"] == 71.4
    assert by_ticker["AAPL"]["sharpe_ratio_252d"] == 1.5
    assert by_ticker["AAPL"]["beta_252d"] == 1.2
    # KO has no technical_daily/risk_daily row in the fixture -- the left
    # join must leave those columns null, not drop the security entirely
    assert by_ticker["KO"]["sma_20"] is None
    assert "KO" in by_ticker

    chart = result["chart"]
    assert chart["labels"] == ["2026-01-02", "2026-01-05"]
    series_by_ticker = {s["ticker"]: s["data"] for s in chart["series"]}
    # indexed to 100 at each series' first point
    assert series_by_ticker["AAPL"] == [100.0, 110.0]
    assert series_by_ticker["KO"][0] == 100.0

    # get_analytics fetches the *default* combination (linear_trend, 90d) --
    # pk3 (mean_reversion) and pk4 (random_walk_drift) must not leak in
    projection = result["projection"]
    assert projection["tickers"] == ["AAPL"]
    assert projection["series"]["AAPL"]["dates"] == ["2026-01-06", "2026-01-07"]
    assert projection["series"]["AAPL"]["projected"] == [111.0, 112.0]
    assert projection["series"]["AAPL"]["lower"] == [105.0, 103.0]
    assert projection["series"]["AAPL"]["upper"] == [117.0, 121.0]
    assert projection["series"]["AAPL"]["r_squared"] == 0.42

    options = result["projection_options"]
    assert options["default_method"] == "linear_trend"
    assert options["default_lookback"] == 90
    assert set(options["methods"]) == {"linear_trend", "random_walk_drift", "mean_reversion"}


# --------------------------------------------------------------------------
# app.services.market_warehouse.get_projection
# --------------------------------------------------------------------------
def test_get_projection_filters_to_the_requested_combination(warehouse_db):
    from app.services import market_warehouse

    result = market_warehouse.get_projection(warehouse_db, method="mean_reversion", lookback_days=30)
    assert result["tickers"] == ["AAPL"]
    assert result["series"]["AAPL"]["projected"] == [108.0]
    assert result["series"]["AAPL"]["r_squared"] == 0.55


def test_get_projection_random_walk_drift_has_no_r_squared(warehouse_db):
    from app.services import market_warehouse

    result = market_warehouse.get_projection(warehouse_db, method="random_walk_drift", lookback_days=90)
    assert result["series"]["AAPL"]["r_squared"] is None


def test_get_projection_rejects_unknown_method_or_lookback(warehouse_db):
    from app.services import market_warehouse

    assert market_warehouse.get_projection(warehouse_db, method="astrology", lookback_days=90) == {
        "tickers": [], "series": {}
    }
    assert market_warehouse.get_projection(warehouse_db, method="linear_trend", lookback_days=999) == {
        "tickers": [], "series": {}
    }


def test_get_projection_chart_data_bundles_projection_and_recent_actual(warehouse_db):
    from app.services import market_warehouse

    result = market_warehouse.get_projection_chart_data(warehouse_db, method="mean_reversion", lookback_days=30)
    assert result["projection"]["tickers"] == ["AAPL"]
    assert result["projection"]["series"]["AAPL"]["projected"] == [108.0]
    assert "AAPL" in result["recent_actual"]
    assert result["recent_actual"]["AAPL"]["dates"]
    assert result["recent_actual"]["AAPL"]["close"]


def test_get_projection_chart_data_rejects_unknown_method_or_lookback(warehouse_db):
    from app.services import market_warehouse

    empty = {"projection": {"tickers": [], "series": {}}, "recent_actual": {}}
    assert market_warehouse.get_projection_chart_data(warehouse_db, method="astrology", lookback_days=90) == empty
    assert market_warehouse.get_projection_chart_data(warehouse_db, method="linear_trend", lookback_days=999) == empty


def test_get_projection_chart_data_graceful_on_bad_path(tmp_path):
    from app.services import market_warehouse

    result = market_warehouse.get_projection_chart_data(
        str(tmp_path / "nope.duckdb"), method="linear_trend", lookback_days=90
    )
    assert result == {"projection": {"tickers": [], "series": {}}, "recent_actual": {}}


def test_get_chart_window_respects_start_and_end(warehouse_db):
    from app.services import market_warehouse

    # only the 2026-01-05 day should be in range
    chart = market_warehouse.get_chart_window(warehouse_db, start="2026-01-04", end="2026-01-06")
    assert chart["labels"] == ["2026-01-05"]


def test_get_chart_window_graceful_on_bad_path(tmp_path):
    from app.services import market_warehouse

    chart = market_warehouse.get_chart_window(str(tmp_path / "nope.duckdb"))
    assert chart == {"labels": [], "series": []}


def test_get_analytics_never_raises_on_a_corrupt_or_partial_db(tmp_path):
    from app.services import market_warehouse

    # a file that exists but has no marts schema at all -- e.g. RAW
    # loaded but dbt never ran
    path = str(tmp_path / "partial.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA raw")
    con.close()

    result = market_warehouse.get_analytics(path)
    assert result["available"] is False
    assert result["reason"]


# --------------------------------------------------------------------------
# /projects/market-warehouse
# --------------------------------------------------------------------------
def test_page_loads_gracefully_when_warehouse_missing(client, app):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = "/does/not/exist.duckdb"
    resp = client.get("/projects/market-warehouse")
    assert resp.status_code == 200
    assert b"Market Data Warehouse" in resp.data
    assert b"run.py all" in resp.data
    assert b"stat-grid" not in resp.data


def test_page_renders_real_data(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse")
    assert resp.status_code == 200
    assert b"AAPL" in resp.data
    assert b"Apple Inc." in resp.data
    assert b"stat-grid" in resp.data
    assert b"warehouse-chart" in resp.data


def test_page_listed_on_projects_index(client):
    resp = client.get("/projects")
    assert b"Market Data Warehouse" in resp.data
    assert b"/projects/market-warehouse" in resp.data


def test_page_shows_not_financial_advice_disclaimer(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse")
    assert b"not financial advice" in resp.data.lower()


# --------------------------------------------------------------------------
# /projects/market-warehouse/api/chart
# --------------------------------------------------------------------------
def test_api_chart_returns_windowed_series(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse/api/chart?start=2026-01-04&end=2026-01-06")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["chart"]["labels"] == ["2026-01-05"]


def test_api_chart_rejects_malformed_date(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse/api/chart?start=not-a-date")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


# --------------------------------------------------------------------------
# /projects/market-warehouse/api/projection
# --------------------------------------------------------------------------
def test_api_projection_returns_the_requested_combination(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse/api/projection?method=mean_reversion&lookback=30")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["projection"]["series"]["AAPL"]["projected"] == [108.0]


def test_api_projection_rejects_unknown_method(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse/api/projection?method=astrology&lookback=90")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_projection_rejects_unknown_lookback(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse/api/projection?method=linear_trend&lookback=999")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_projection_rejects_non_integer_lookback(client, app, warehouse_db):
    app.config["MARKET_WAREHOUSE_DB_PATH"] = warehouse_db
    resp = client.get("/projects/market-warehouse/api/projection?method=linear_trend&lookback=ninety")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
