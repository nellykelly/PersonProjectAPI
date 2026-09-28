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
    def __init__(self, fail_on: str | None = None):
        self.calls: list[tuple[str, object]] = []
        self._fail_on = fail_on

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self._fail_on and sql.startswith(self._fail_on):
            raise RuntimeError(f"simulated failure on {self._fail_on}")
        return self

    def fetchone(self):
        return (0,)


class _FakeSnowConnection:
    def __init__(self, fail_on: str | None = None):
        self.cursor_obj = _FakeSnowCursor(fail_on)

    def cursor(self):
        return self.cursor_obj

    def close(self):
        pass


def _set_snowflake_env(monkeypatch, *, key_path="S:/keys/rsa_key.p8", password=""):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "acct")
    monkeypatch.setenv("SNOWFLAKE_USER", "user")
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", key_path)
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", "")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", password)
    monkeypatch.setenv("SNOWFLAKE_WAREHOUSE", "wh")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "MARKET")
    monkeypatch.setenv("RAW_SCHEMA", "RAW")


def _fake_snowflake(monkeypatch, fail_on=None):
    """Fake connect + write_pandas; returns (connection, connect kwargs, write calls)."""
    fake_con = _FakeSnowConnection(fail_on)
    connect_kwargs: dict = {}

    def _connect(**kw):
        connect_kwargs.update(kw)
        return fake_con

    monkeypatch.setattr(snowflake_connector, "connect", _connect)
    write_calls: list[dict] = []
    monkeypatch.setattr(
        "snowflake.connector.pandas_tools.write_pandas",
        lambda _con, df, table_name, **kw: write_calls.append({"table_name": table_name, "rows": len(df), **kw})
        or (True, 1, len(df), None),
    )
    return fake_con, connect_kwargs, write_calls


def test_snowflake_loader_uses_key_pair_auth_not_a_password(monkeypatch):
    from ingest.config import SnowflakeConfig
    from ingest.load import SnowflakeLoader

    _set_snowflake_env(monkeypatch, password="also-set")
    _, connect_kwargs, _ = _fake_snowflake(monkeypatch)
    SnowflakeLoader(SnowflakeConfig())
    assert connect_kwargs["private_key_file"] == "S:/keys/rsa_key.p8"
    assert "password" not in connect_kwargs, "the key must win when both are configured"


def test_snowflake_config_requires_a_key_or_password(monkeypatch):
    from ingest.config import SnowflakeConfig

    _set_snowflake_env(monkeypatch, key_path="", password="")
    assert "private_key_path" in SnowflakeConfig().missing()


def test_snowflake_loader_creates_and_uses_schema_before_writing(monkeypatch):
    from ingest.config import SnowflakeConfig
    from ingest.load import SnowflakeLoader

    _set_snowflake_env(monkeypatch)
    fake_con, _, _ = _fake_snowflake(monkeypatch)
    SnowflakeLoader(SnowflakeConfig())
    setup_sql = [sql for sql, _ in fake_con.cursor_obj.calls]
    assert setup_sql[0] == "CREATE SCHEMA IF NOT EXISTS MARKET.RAW"
    assert setup_sql[1] == "USE SCHEMA MARKET.RAW"


def test_snowflake_loader_stages_then_swaps_in_one_transaction(monkeypatch):
    from ingest.config import SnowflakeConfig
    from ingest.load import SnowflakeLoader

    _set_snowflake_env(monkeypatch)
    fake_con, _, write_calls = _fake_snowflake(monkeypatch)
    loader = SnowflakeLoader(SnowflakeConfig())
    fake_con.cursor_obj.calls.clear()
    loader.load("price_history", _frame("AAPL", 2))

    # Bulk load goes to a session-scoped temp stage, fully qualified, typed.
    (write,) = write_calls
    assert write["table_name"] == "PRICE_HISTORY__INCOMING"
    assert write["database"] == "MARKET" and write["schema"] == "RAW"
    assert write["table_type"] == "temporary"
    assert write["overwrite"] is True
    assert write["use_logical_type"] is True

    sql = [s for s, _ in fake_con.cursor_obj.calls]
    stage = "MARKET.RAW.PRICE_HISTORY__INCOMING"
    assert sql[0] == f"CREATE TABLE IF NOT EXISTS MARKET.RAW.PRICE_HISTORY AS SELECT * FROM {stage} WHERE 1 = 0"
    assert sql[1] == "BEGIN"
    assert sql[2] == f"DELETE FROM MARKET.RAW.PRICE_HISTORY WHERE TICKER IN (SELECT DISTINCT TICKER FROM {stage})"
    assert sql[3].startswith("INSERT INTO MARKET.RAW.PRICE_HISTORY (TICKER, ")
    assert sql[3].endswith(f"FROM {stage}")
    assert sql[4] == "COMMIT"
    assert sql[5] == f"DROP TABLE IF EXISTS {stage}"


def test_snowflake_loader_rolls_back_the_delete_when_the_insert_fails(monkeypatch):
    from ingest.config import SnowflakeConfig
    from ingest.load import SnowflakeLoader

    _set_snowflake_env(monkeypatch)
    fake_con, _, _ = _fake_snowflake(monkeypatch, fail_on="INSERT INTO")
    loader = SnowflakeLoader(SnowflakeConfig())
    with pytest.raises(RuntimeError, match="simulated failure"):
        loader.load("price_history", _frame("AAPL", 2))

    sql = [s for s, _ in fake_con.cursor_obj.calls]
    assert "ROLLBACK" in sql, "a failed insert must undo the delete"
    assert "COMMIT" not in sql
    assert sql[-1] == "DROP TABLE IF EXISTS MARKET.RAW.PRICE_HISTORY__INCOMING", "stage cleaned up even on failure"


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
