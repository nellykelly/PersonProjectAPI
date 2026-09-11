"""Land tidy frames into the warehouse RAW schema.

Idempotent by construction: each load deletes the rows for the tickers in
the batch and re-inserts them, so re-running the same universe replaces
rather than duplicates. Two backends, chosen by `WAREHOUSE_TARGET`:
`duckdb` (local file, used for dev + tests) and `snowflake`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import pandas as pd

from ingest.config import Settings, raw_schema

log = logging.getLogger(__name__)


class Loader(ABC):
    schema: str

    @abstractmethod
    def load(self, table: str, df: pd.DataFrame) -> int:
        """Replace this batch's tickers in `RAW.<table>` with `df`.
        Returns rows written."""

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> "Loader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class DuckDBLoader(Loader):
    def __init__(self, path: str) -> None:
        import duckdb

        self.schema = raw_schema()
        self._con = duckdb.connect(path)
        self._con.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
        log.info("duckdb: %s", path)

    def load(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        fq = f"{self.schema}.{table}"
        con = self._con
        con.register("_incoming", df)
        try:
            con.execute(f"CREATE TABLE IF NOT EXISTS {fq} AS SELECT * FROM _incoming LIMIT 0")
            tickers = sorted(df["ticker"].unique().tolist())
            placeholders = ",".join("?" for _ in tickers)
            # delete + insert as one transaction: a crash between the two
            # must not leave a ticker's rows deleted with nothing re-inserted
            con.execute("BEGIN TRANSACTION")
            try:
                con.execute(f"DELETE FROM {fq} WHERE ticker IN ({placeholders})", tickers)
                con.execute(f"INSERT INTO {fq} SELECT * FROM _incoming")
            except Exception:
                con.execute("ROLLBACK")
                raise
            else:
                con.execute("COMMIT")
        finally:
            con.unregister("_incoming")
        return len(df)

    def close(self) -> None:
        self._con.close()


class SnowflakeLoader(Loader):
    def __init__(self, cfg) -> None:  # cfg: SnowflakeConfig
        import snowflake.connector

        missing = cfg.missing()
        if missing:
            raise RuntimeError(
                "Snowflake config incomplete; set: "
                + ", ".join(f"SNOWFLAKE_{m.upper()}" for m in missing)
            )
        self.database = cfg.database
        self.schema = cfg.schema
        self._con = snowflake.connector.connect(
            account=cfg.account,
            user=cfg.user,
            password=cfg.password,
            role=cfg.role or None,
            warehouse=cfg.warehouse,
            database=cfg.database,
            schema=cfg.schema,
        )
        cur = self._con.cursor()
        # `connect(schema=...)` against a schema that doesn't exist yet
        # (first run) can leave the session with no usable schema context.
        # Create it, then explicitly USE it rather than relying on connect
        # having set the context -- everything after this is qualified
        # with self.database/self.schema too, so this is belt-and-braces,
        # not the only thing standing between a fresh account and a
        # misdirected write.
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {cfg.database}.{cfg.schema}")
        cur.execute(f"USE SCHEMA {cfg.database}.{cfg.schema}")
        log.info("snowflake: %s.%s", cfg.database, cfg.schema)

    def load(self, table: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        from snowflake.connector.pandas_tools import write_pandas

        name = table.upper()
        fq = f"{self.database}.{self.schema}.{name}"
        frame = df.copy()
        frame.columns = [c.upper() for c in frame.columns]
        cur = self._con.cursor()
        exists = cur.execute(
            "SELECT COUNT(*) FROM {}.INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s".format(self.database),
            (self.schema.upper(), name),
        ).fetchone()[0]
        if exists:
            tickers = sorted(frame["TICKER"].unique().tolist())
            binds = ",".join(["%s"] * len(tickers))
            # NOTE: this DELETE is transactional, but write_pandas's COPY
            # INTO is its own operation and is not known to participate in
            # an open Snowflake transaction the same way DML does -- so a
            # crash between the DELETE and the write below can still leave
            # a ticker's rows gone with nothing re-inserted. Documented as
            # a residual risk in HARDENING.md rather than claimed as fixed
            # (an unverifiable claim without a live account to test it on).
            cur.execute("BEGIN")
            try:
                cur.execute(f"DELETE FROM {fq} WHERE TICKER IN ({binds})", tickers)
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        write_pandas(
            self._con,
            frame,
            name,
            database=self.database,
            schema=self.schema,
            auto_create_table=not exists,
            quote_identifiers=False,
        )
        return len(frame)

    def close(self) -> None:
        self._con.close()


def make_loader(settings: Settings) -> Loader:
    if settings.target == "snowflake":
        return SnowflakeLoader(settings.snowflake)
    if settings.target == "motherduck":
        missing = settings.motherduck.missing()
        if missing:
            raise RuntimeError(
                "MotherDuck config incomplete; set: "
                + ", ".join(f"MOTHERDUCK_{m.upper()}" for m in missing)
                + " (get a token from https://app.motherduck.com/token after signing up "
                "for the free Lite tier -- never a live connection attempt without one)"
            )
        # Discovered on the first real run against a fresh account:
        # `duckdb.connect("md:<name>")` does NOT create the database --
        # it only attaches an existing one, and raises if <name> isn't
        # there yet ("no database/share named 'market' found"). Bootstrap
        # it via the account-level "md:" connection (no name = your
        # default catalog) before the real connection.
        _ensure_motherduck_database(settings.motherduck.database)
        # Same DuckDBLoader, same SQL -- MotherDuck is wire-compatible
        # DuckDB, so the only difference is the path duckdb.connect() gets.
        return DuckDBLoader(f"md:{settings.motherduck.database}")
    return DuckDBLoader(settings.duckdb.path)


def _ensure_motherduck_database(database: str) -> None:
    import duckdb

    bootstrap = duckdb.connect("md:")
    try:
        bootstrap.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    finally:
        bootstrap.close()
