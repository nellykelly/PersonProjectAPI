"""Unit tests for the ingestion layer. No network: yfinance is faked."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ingest import pull_yfinance
from ingest.config import Settings, load_tickers


# --------------------------------------------------------------------------
# load_tickers
# --------------------------------------------------------------------------
def test_load_tickers_parses_comments_blanks_and_dedupes(tmp_path: Path):
    f = tmp_path / "t.txt"
    f.write_text(
        "\n".join(["AAPL", "  msft  # note", "", "# just a comment", "AAPL", "aapl"]),
        encoding="utf-8",
    )
    assert load_tickers(f) == ["AAPL", "MSFT"]


# --------------------------------------------------------------------------
# H6: Settings must read the environment at construction time, not at
# first import of ingest.config.
# --------------------------------------------------------------------------
def test_settings_reflect_env_at_construction_not_import_time(monkeypatch):
    from ingest.config import DuckDBConfig, SnowflakeConfig, raw_schema

    monkeypatch.setenv("WAREHOUSE_TARGET", "duckdb")
    monkeypatch.setenv("DUCKDB_PATH", "/tmp/first.duckdb")
    monkeypatch.setenv("RAW_SCHEMA", "RAW")
    assert Settings().target == "duckdb"
    assert DuckDBConfig().path == "/tmp/first.duckdb"
    assert raw_schema() == "RAW"

    # change the environment *after* the module is long since imported --
    # a class-level `= os.environ.get(...)` default would still return the
    # first values above.
    monkeypatch.setenv("WAREHOUSE_TARGET", "snowflake")
    monkeypatch.setenv("DUCKDB_PATH", "/tmp/second.duckdb")
    monkeypatch.setenv("RAW_SCHEMA", "LANDING")
    assert Settings().target == "snowflake"
    assert DuckDBConfig().path == "/tmp/second.duckdb"
    assert raw_schema() == "LANDING"
    assert SnowflakeConfig().schema == "LANDING"


def test_settings_rejects_unknown_target(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_TARGET", "bigquery")
    with pytest.raises(ValueError):
        Settings()


# --------------------------------------------------------------------------
# H5: transient yfinance failures are retried, not fatal on the first error.
# --------------------------------------------------------------------------
def test_with_retry_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr(pull_yfinance.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("simulated throttle")
        return "ok"

    assert pull_yfinance._with_retry(flaky, what="test") == "ok"
    assert calls["n"] == 3


def test_with_retry_gives_up_after_configured_attempts(monkeypatch):
    monkeypatch.setattr(pull_yfinance.time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def always_fails():
        calls["n"] += 1
        raise ConnectionError("still throttled")

    with pytest.raises(ConnectionError):
        pull_yfinance._with_retry(always_fails, what="test")
    assert calls["n"] == pull_yfinance.RETRY_ATTEMPTS


# --------------------------------------------------------------------------
# pull_price_history normalisation
# --------------------------------------------------------------------------
class _FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol

    def history(self, **_kw):
        idx = pd.to_datetime(["2020-01-02", "2020-01-03"])
        return pd.DataFrame(
            {
                "Open": [10.0, 11.0],
                "High": [10.5, 11.5],
                "Low": [9.5, 10.5],
                "Close": [10.2, 11.2],
                "Adj Close": [9.0, 9.9],
                "Volume": [1000, 1100],
                "Dividends": [0.0, 0.25],
                "Stock Splits": [0.0, 0.0],
            },
            index=pd.Index(idx, name="Date"),
        )

    @property
    def dividends(self):
        return pd.Series([0.25], index=pd.to_datetime(["2020-01-03"]))

    @property
    def splits(self):
        return pd.Series([4.0], index=pd.to_datetime(["2020-08-31"]))

    def get_info(self):
        return {"shortName": "Test Co", "exchange": "NMS", "currency": "USD", "quoteType": "EQUITY"}


@pytest.fixture(autouse=True)
def _patch_yf(monkeypatch):
    monkeypatch.setattr(pull_yfinance.yf, "Ticker", _FakeTicker)


def test_pull_price_history_shape_and_audit_cols():
    df = pull_yfinance.pull_price_history("aapl")
    assert list(df.columns[:3]) == ["ticker", "trade_date", "open"]
    assert set(["source", "ingested_at"]).issubset(df.columns)
    assert (df["ticker"] == "aapl").all()
    assert df["source"].unique().tolist() == ["yfinance"]
    assert len(df) == 2


def test_pull_all_returns_every_feed_nonempty():
    frames = pull_yfinance.pull_all(["AAPL"])
    assert set(frames) == {"price_history", "dividends", "splits", "security_info"}
    for feed, df in frames.items():
        assert not df.empty, feed
    assert frames["security_info"].iloc[0]["exchange_code"] == "NMS"


# --------------------------------------------------------------------------
# H3: an all-null security_info row must still land as string-typed
# columns, not an inferred int/float column a downstream `trim()` chokes on.
# --------------------------------------------------------------------------
class _FakeTickerNoInfo(_FakeTicker):
    def get_info(self):
        return {}

    @property
    def fast_info(self):
        raise RuntimeError("no fast_info for this thin ticker either")


def test_pull_security_info_all_null_is_still_string_typed(monkeypatch):
    monkeypatch.setattr(pull_yfinance.yf, "Ticker", _FakeTickerNoInfo)
    df = pull_yfinance.pull_security_info("THIN")
    for col in pull_yfinance.INFO_FIELDS.values():
        assert df[col].isna().all()
        assert pd.api.types.is_string_dtype(df[col]), f"{col} is {df[col].dtype}, not string"


# --------------------------------------------------------------------------
# DuckDBLoader idempotency
# --------------------------------------------------------------------------
duckdb = pytest.importorskip("duckdb")


def _frame(ticker: str, n: int) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": [ticker] * n,
            "trade_date": pd.date_range("2020-01-01", periods=n).date,
            "close": range(n),
            "source": "yfinance",
            "ingested_at": pd.Timestamp.now("UTC"),
        }
    )


def test_duckdb_loader_is_idempotent_and_appends_new_tickers(tmp_path: Path):
    from ingest.load import DuckDBLoader

    path = str(tmp_path / "w.duckdb")
    with DuckDBLoader(path) as ldr:
        ldr.load("price_history", _frame("AAPL", 3))
        ldr.load("price_history", _frame("AAPL", 3))  # same batch again

    con = duckdb.connect(path)
    n_aapl = con.execute("select count(*) from RAW.price_history").fetchone()[0]
    assert n_aapl == 3, "re-loading the same ticker must not duplicate"

    with DuckDBLoader(path) as ldr:
        ldr.load("price_history", _frame("MSFT", 2))
    total = con.execute("select count(*) from RAW.price_history").fetchone()[0]
    assert total == 5, "a new ticker must append, not replace"
    con.close()


# --------------------------------------------------------------------------
# H4: delete + insert is one transaction -- a failed insert must not leave
# the ticker's prior rows deleted with nothing to replace them.
# --------------------------------------------------------------------------
def test_duckdb_loader_rolls_back_on_insert_failure(tmp_path: Path):
    from ingest.load import DuckDBLoader

    path = str(tmp_path / "rollback.duckdb")
    with DuckDBLoader(path) as ldr:
        ldr.load("price_history", _frame("AAPL", 3))

    con = duckdb.connect(path)
    assert con.execute("select count(*) from RAW.price_history").fetchone()[0] == 3
    con.close()

    # a column-count mismatch against the already-created table makes the
    # INSERT fail after the DELETE has already run
    bad = _frame("AAPL", 2)
    bad["extra_column"] = "this column doesn't exist in the table yet"
    with pytest.raises(Exception):
        with DuckDBLoader(path) as ldr:
            ldr.load("price_history", bad)

    con = duckdb.connect(path)
    after = con.execute("select count(*) from RAW.price_history").fetchone()[0]
    con.close()
    assert after == 3, "a failed insert must roll back the delete, not empty the table"


# --------------------------------------------------------------------------
# H2: SnowflakeLoader must target database+schema explicitly, never rely on
# session context left over from CREATE SCHEMA / connect(). No live
# connection -- snowflake.connector is faked end to end.
# --------------------------------------------------------------------------
snowflake_connector = pytest.importorskip("snowflake.connector")


class _FakeSnowCursor:
    def __init__(self, existing=frozenset()):
        self.calls: list[tuple[str, object]] = []
        self._existing = existing

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        sql, params = self.calls[-1]
        if "INFORMATION_SCHEMA.TABLES" in sql:
            schema, name = params
            return (1 if (schema, name) in self._existing else 0,)
        return (0,)


class _FakeSnowConnection:
    def __init__(self, existing=frozenset()):
        self.cursor_obj = _FakeSnowCursor(existing)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


def _set_snowflake_env(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "user")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pw")
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "wh")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "MARKET")
    monkeypatch.setenv("RAW_SCHEMA", "RAW")


def test_snowflake_loader_first_run_creates_and_uses_schema_then_writes_qualified(monkeypatch):
    from ingest.config import SnowflakeConfig
    from ingest.load import SnowflakeLoader

    _set_snowflake_env(monkeypatch)
    fake_con = _FakeSnowConnection()  # table does not exist yet
    monkeypatch.setattr(snowflake_connector, "connect", lambda **_kw: fake_con)
    write_calls = []
    monkeypatch.setattr(
        "snowflake.connector.pandas_tools.write_pandas",
        lambda _con, df, table_name, **kw: write_calls.append(
            {"table_name": table_name, **kw}
        )
        or (True, 1, len(df), None),
    )

    loader = SnowflakeLoader(SnowflakeConfig())
    loader.load("price_history", _frame("AAPL", 2))

    setup_sql = [sql for sql, _ in fake_con.cursor_obj.calls]
    assert any(s.startswith("CREATE SCHEMA IF NOT EXISTS MARKET.RAW") for s in setup_sql)
    assert any(s == "USE SCHEMA MARKET.RAW" for s in setup_sql), setup_sql
    assert not any(s.startswith("DELETE") for s in setup_sql), "nothing to delete on first run"

    assert write_calls[0]["database"] == "MARKET"
    assert write_calls[0]["schema"] == "RAW"
    assert write_calls[0]["table_name"] == "PRICE_HISTORY"
    assert write_calls[0]["auto_create_table"] is True


def test_snowflake_loader_existing_table_deletes_qualified_and_commits(monkeypatch):
    from ingest.config import SnowflakeConfig
    from ingest.load import SnowflakeLoader

    _set_snowflake_env(monkeypatch)
    fake_con = _FakeSnowConnection(existing={("RAW", "PRICE_HISTORY")})
    monkeypatch.setattr(snowflake_connector, "connect", lambda **_kw: fake_con)
    monkeypatch.setattr(
        "snowflake.connector.pandas_tools.write_pandas",
        lambda *_a, **_kw: (True, 1, 1, None),
    )

    loader = SnowflakeLoader(SnowflakeConfig())
    loader.load("price_history", _frame("AAPL", 2))

    delete_calls = [c for c in fake_con.cursor_obj.calls if c[0].startswith("DELETE")]
    assert len(delete_calls) == 1
    sql, params = delete_calls[0]
    assert sql.startswith("DELETE FROM MARKET.RAW.PRICE_HISTORY"), sql
    assert params == ["AAPL"]
    assert any(sql == "COMMIT" for sql, _ in fake_con.cursor_obj.calls)


# --------------------------------------------------------------------------
# MotherDuck target: wire-compatible DuckDB, so it reuses DuckDBLoader --
# these tests confirm it's routed to "md:<database>" and fails fast
# without a token, rather than hanging on an interactive browser login.
# No account or network needed: duckdb.connect is faked.
# --------------------------------------------------------------------------
def test_motherduck_target_routes_duckdb_loader_to_md_path(monkeypatch):
    import duckdb as duckdb_module

    from ingest.config import Settings
    from ingest.load import make_loader

    monkeypatch.setenv("WAREHOUSE_TARGET", "motherduck")
    monkeypatch.setenv("MOTHERDUCK_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("MOTHERDUCK_DATABASE", "market_test")

    connect_calls = []
    executed = []

    class _FakeConnection:
        def execute(self, sql, *_a, **_kw):
            executed.append(sql)
            return self

        def close(self):
            pass

    def fake_connect(path):
        connect_calls.append(path)
        return _FakeConnection()

    monkeypatch.setattr(duckdb_module, "connect", fake_connect)

    loader = make_loader(Settings())
    # bootstrap connects account-wide first ("md:", no name -- a named
    # database can't be attached until it exists) and creates the
    # database, *then* the real loader connects to it by name
    assert connect_calls == ["md:", "md:market_test"]
    assert any("CREATE DATABASE IF NOT EXISTS market_test" in sql for sql in executed)
    loader.close()


def test_motherduck_requires_token_to_avoid_hanging_on_browser_auth(monkeypatch):
    from ingest.config import Settings
    from ingest.load import make_loader

    monkeypatch.setenv("WAREHOUSE_TARGET", "motherduck")
    monkeypatch.delenv("MOTHERDUCK_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="MOTHERDUCK_TOKEN"):
        make_loader(Settings())
