import datetime as dt

import pytest

from app import create_app
from app.extensions import db as _db


@pytest.fixture
def app():
    application = create_app("testing")
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield _db


@pytest.fixture
def make_user(app):
    """Factory: make_user("name") -> commits a User and returns its username.

    Uses its own short-lived app context on purpose (not the `db` fixture):
    a `db` context held open across the test's HTTP requests would make
    Flask-Login cache the first request's user in `g` and reuse it for
    every later request, which breaks any test that logs in as more than
    one account.
    """
    from app.models import User

    def _make(username="tester", password="password123"):
        with app.app_context():
            user = User()
            user.set_username(username)
            user.set_password(password)
            _db.session.add(user)
            _db.session.commit()
            return user.username

    return _make


@pytest.fixture
def login(app):
    """Factory: login("name") -> a fresh test client logged in as that user."""

    def _login(username, password="password123"):
        c = app.test_client()
        resp = c.post(
            "/auth/login", data={"username": username, "password": password}
        )
        assert resp.status_code == 302, resp.data
        return c

    return _login


@pytest.fixture
def auth_client(make_user, login):
    """A test client logged in as a fresh user named 'tester'."""
    return login(make_user())


@pytest.fixture
def warehouse_db(tmp_path):
    """A minimal but real DuckDB file with two securities, a few days of
    prices, and one dividend -- enough to exercise every
    app.services.market_warehouse query, including a small
    fct_security_price_projection fixture for AAPL across three
    method/lookback combinations. Shared by tests/test_market_warehouse.py
    and tests/test_market_warehouse_analyze.py; skips (not fails) if
    duckdb isn't installed."""
    duckdb = pytest.importorskip("duckdb")

    path = str(tmp_path / "test_market.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA marts")
    con.execute(
        """
        CREATE TABLE marts.dim_exchange (
            exchange_key VARCHAR, exchange_code VARCHAR, exchange_name VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO marts.dim_exchange VALUES ('ex1', 'NMS', 'Nasdaq Stock Market')"
    )
    con.execute(
        """
        CREATE TABLE marts.dim_security (
            security_key VARCHAR, ticker VARCHAR, security_name VARCHAR,
            sector VARCHAR, exchange_key VARCHAR
        )
        """
    )
    con.execute(
        "INSERT INTO marts.dim_security VALUES "
        "('sec1', 'AAPL', 'Apple Inc.', 'Technology', 'ex1'), "
        "('sec2', 'KO', 'The Coca-Cola Company', 'Consumer Defensive', 'ex1')"
    )
    con.execute(
        """
        CREATE TABLE marts.fct_security_price (
            price_key VARCHAR, security_key VARCHAR, date_key INTEGER,
            exchange_key VARCHAR, ticker VARCHAR, trade_date DATE,
            close DOUBLE, adj_close DOUBLE, volume BIGINT, daily_return DOUBLE
        )
        """
    )
    # date_key mirrors the real yyyymmdd convention -- distinct per day, so
    # the technical/risk left joins below (keyed on security_key+date_key)
    # match only the intended row, not every day for that security.
    rows = [
        ("sec1", "AAPL", dt.date(2026, 1, 2), 20260102, 100.0, 100.0, 1000, None),
        ("sec1", "AAPL", dt.date(2026, 1, 5), 20260105, 110.0, 110.0, 1100, 0.10),
        ("sec2", "KO", dt.date(2026, 1, 2), 20260102, 60.0, 58.0, 2000, None),
        ("sec2", "KO", dt.date(2026, 1, 5), 20260105, 61.2, 59.16, 2100, 0.02),
    ]
    con.executemany(
        "INSERT INTO marts.fct_security_price "
        "(price_key, security_key, date_key, exchange_key, ticker, trade_date, "
        " close, adj_close, volume, daily_return) VALUES "
        "(gen_random_uuid()::VARCHAR, ?, ?, 'ex1', ?, ?, ?, ?, ?, ?)",
        [
            (r[0], r[3], r[1], r[2], r[4], r[5], r[6], r[7])
            for r in rows
        ],
    )
    con.execute(
        """
        CREATE TABLE marts.fct_corporate_action (
            corporate_action_key VARCHAR, security_key VARCHAR,
            action_type VARCHAR, dividend_amount DOUBLE
        )
        """
    )
    con.execute(
        "INSERT INTO marts.fct_corporate_action VALUES "
        "('ca1', 'sec2', 'dividend', 0.5), ('ca2', 'sec1', 'split', NULL)"
    )
    con.execute(
        """
        CREATE TABLE marts.fct_security_technical_daily (
            security_key VARCHAR, date_key INTEGER, ticker VARCHAR, trade_date DATE,
            sma_20 DOUBLE, sma_50 DOUBLE, sma_200 DOUBLE,
            bollinger_upper_20 DOUBLE, bollinger_lower_20 DOUBLE,
            high_52w DOUBLE, low_52w DOUBLE, pct_off_52w_high DOUBLE,
            rsi_14_simplified DOUBLE
        )
        """
    )
    con.execute(
        "INSERT INTO marts.fct_security_technical_daily VALUES "
        "('sec1', 20260105, 'AAPL', DATE '2026-01-05', 105.0, 102.0, 101.0, 115.0, 95.0, 110.0, 100.0, 0.0, 71.4)"
    )
    con.execute(
        """
        CREATE TABLE marts.fct_security_risk_daily (
            security_key VARCHAR, date_key INTEGER, ticker VARCHAR, trade_date DATE,
            volatility_20d DOUBLE, volatility_60d DOUBLE, volatility_252d DOUBLE,
            max_drawdown_252d DOUBLE, sharpe_ratio_252d DOUBLE, sortino_ratio_252d DOUBLE,
            beta_60d DOUBLE, beta_252d DOUBLE
        )
        """
    )
    con.execute(
        "INSERT INTO marts.fct_security_risk_daily VALUES "
        "('sec1', 20260105, 'AAPL', DATE '2026-01-05', 0.18, 0.20, 0.22, -0.08, 1.5, 2.1, 1.1, 1.2)"
    )
    con.execute(
        """
        CREATE TABLE marts.fct_security_price_projection (
            projection_key VARCHAR, security_key VARCHAR, ticker VARCHAR, date_key INTEGER,
            projection_date DATE, method VARCHAR, lookback_days INTEGER, trading_day_offset INTEGER,
            projected_close DOUBLE, lower_bound_95 DOUBLE, upper_bound_95 DOUBLE,
            fit_quality DOUBLE
        )
        """
    )
    con.execute(
        "INSERT INTO marts.fct_security_price_projection VALUES "
        # default combination: linear_trend, 90d lookback
        "('pk1', 'sec1', 'AAPL', 20260106, DATE '2026-01-06', 'linear_trend', 90, 1, 111.0, 105.0, 117.0, 0.42), "
        "('pk2', 'sec1', 'AAPL', 20260107, DATE '2026-01-07', 'linear_trend', 90, 2, 112.0, 103.0, 121.0, 0.42), "
        # a second combination, used to test filtering: mean_reversion, 30d
        "('pk3', 'sec1', 'AAPL', 20260106, DATE '2026-01-06', 'mean_reversion', 30, 1, 108.0, 104.0, 112.0, 0.55), "
        # random_walk_drift has no fit statistic -- null, same as the real model
        "('pk4', 'sec1', 'AAPL', 20260106, DATE '2026-01-06', 'random_walk_drift', 90, 1, 109.5, 104.5, 114.5, NULL)"
    )
    con.close()
    return path
