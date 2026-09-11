"""Runtime configuration, all from the environment.

Nothing here has a secret default. `WAREHOUSE_TARGET` picks the loader;
everything else is read only by the loader that needs it.

Every value is read at call time via `default_factory`/functions, not
bound once when this module is first imported. `run.py`'s CLI flow
happens to load `.env` before its first `import ingest.config`, so the
class-defaults version worked there by accident of import order -- but
`get_settings()` claiming to return live settings while actually
returning values frozen at import time is a latent bug for any other
caller (tests, a notebook, a long-lived process). Fixed here so
`get_settings()` genuinely reflects the environment at the moment it's
called.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TICKERS_FILE = PROJECT_ROOT / "ingest" / "tickers.txt"
DEFAULT_DUCKDB_PATH = PROJECT_ROOT / "warehouse" / "market.duckdb"

# yfinance feeds this project ingests. Fundamentals are intentionally absent.
FEEDS = ("price_history", "dividends", "splits", "security_info")


def raw_schema() -> str:
    """The RAW landing schema name, read live -- shared by the loader and
    (via dbt's own `env_var`) the dbt sources."""
    return os.environ.get("RAW_SCHEMA", "RAW")


def load_tickers(path: str | os.PathLike[str] | None = None) -> list[str]:
    """One ticker per line; `#` comments and blank lines ignored."""
    p = Path(path) if path else DEFAULT_TICKERS_FILE
    out: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().upper()
        if line:
            out.append(line)
    # de-dupe, keep order
    seen: set[str] = set()
    return [t for t in out if not (t in seen or seen.add(t))]


@dataclass(frozen=True)
class DuckDBConfig:
    path: str = field(default_factory=lambda: os.environ.get("DUCKDB_PATH", str(DEFAULT_DUCKDB_PATH)))


@dataclass(frozen=True)
class SnowflakeConfig:
    account: str = field(default_factory=lambda: os.environ.get("SNOWFLAKE_ACCOUNT", ""))
    user: str = field(default_factory=lambda: os.environ.get("SNOWFLAKE_USER", ""))
    password: str = field(default_factory=lambda: os.environ.get("SNOWFLAKE_PASSWORD", ""))
    role: str = field(default_factory=lambda: os.environ.get("SNOWFLAKE_ROLE", ""))
    warehouse: str = field(default_factory=lambda: os.environ.get("SNOWFLAKE_WAREHOUSE", ""))
    database: str = field(default_factory=lambda: os.environ.get("SNOWFLAKE_DATABASE", "MARKET"))
    schema: str = field(default_factory=raw_schema)

    def missing(self) -> list[str]:
        required = ("account", "user", "password", "warehouse", "database")
        return [name for name in required if not getattr(self, name)]


@dataclass(frozen=True)
class MotherDuckConfig:
    """MotherDuck is wire-compatible DuckDB -- the free "Lite" tier (10 GB
    storage, 10 Pulse compute-hours/month, no card) is a permanent
    offering, not a trial, and is the account this project targets.
    `DuckDBLoader` connects to it exactly as it would a local file: the
    only difference is the path being `md:<database>` instead of a
    filesystem path (see `ingest/load.py::make_loader`)."""

    database: str = field(default_factory=lambda: os.environ.get("MOTHERDUCK_DATABASE", "market"))
    token: str = field(default_factory=lambda: os.environ.get("MOTHERDUCK_TOKEN", ""))

    def missing(self) -> list[str]:
        # Without a token, duckdb's `md:` connect falls back to an
        # interactive browser OAuth prompt -- fine by hand, but it hangs
        # a script. Required here so that path fails fast instead.
        return [] if self.token else ["token"]


@dataclass(frozen=True)
class Settings:
    target: str = field(default_factory=lambda: os.environ.get("WAREHOUSE_TARGET", "duckdb").lower())
    duckdb: DuckDBConfig = field(default_factory=DuckDBConfig)
    snowflake: SnowflakeConfig = field(default_factory=SnowflakeConfig)
    motherduck: MotherDuckConfig = field(default_factory=MotherDuckConfig)

    def __post_init__(self) -> None:
        if self.target not in ("duckdb", "snowflake", "motherduck"):
            raise ValueError(
                f"WAREHOUSE_TARGET must be 'duckdb', 'snowflake' or 'motherduck', got {self.target!r}"
            )


def get_settings() -> Settings:
    return Settings()
